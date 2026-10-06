"""The agent loop.

    understand -> (explore schema) -> draft -> validate -> EXPLAIN -> execute -> present
                                         ^                                |
                                         +------- self-repair on failure -+

For small and medium schemas the whole compact schema is in the first prompt, so most
questions need only one or two model calls (important on rate-limited free tiers).
Large schemas get an overview plus a `describe_tables` action instead.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from querymind.adapters.base import DatabaseAdapter, QueryResult, Schema
from querymind.agent import prompts
from querymind.agent.protocol import ProtocolError, parse_action
from querymind.errors import DatabaseError, ValidationError
from querymind.learning import FeedbackStore, format_few_shot_examples
from querymind.llm.router import LLMRouter
from querymind.paths import find_project_root
from querymind.safety import mask_rows, mask_value, validate
from querymind.workspace import search_workspace

OBSERVATION_CAP = 6000


@dataclass
class AgentResult:
    status: str                              # "ok" | "clarify" | "failed"
    sql: str = ""
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)
    result: QueryResult | None = None
    plan_warnings: list[str] = field(default_factory=list)
    index_suggestions: list[str] = field(default_factory=list)
    limit_applied: bool = False
    provider: str = ""
    model: str = ""
    model_calls: int = 0
    repairs: int = 0
    clarification: tuple[str, list[str]] | None = None
    error: str | None = None


class Agent:
    def __init__(
        self,
        adapter: DatabaseAdapter,
        router: LLMRouter,
        agent_cfg: dict,
        max_rows: int,
        notes: str = "",
        project_root: Path | None = None,
        feedback_store: FeedbackStore | None = None,
        on_event: Callable[[str], None] | None = None,
        on_clarify: Callable[[str, list[str]], str | None] | None = None,
    ):
        self.adapter = adapter
        self.router = router
        self.max_steps = int(agent_cfg["max_steps"])
        self.max_repairs = int(agent_cfg["max_repairs"])
        self.allow_samples = agent_cfg["privacy"] == "samples"
        self.budget = int(agent_cfg["schema_budget_chars"])
        self.max_rows = max_rows
        self.notes = notes[:4000]
        self.project_root = project_root or find_project_root()
        self.feedback_store = feedback_store or FeedbackStore(self.project_root / ".querymind" / "feedback.jsonl")
        self.on_event = on_event or (lambda _m: None)
        self.on_clarify = on_clarify
        self._schema: Schema | None = None


    @property
    def schema(self) -> Schema:
        if self._schema is None:
            self._schema = self.adapter.introspect()
        return self._schema

    def refresh_schema(self) -> None:
        self._schema = None

    # ------------------------------------------------------------------
    def ask(self, question: str, history: list[tuple[str, str]] | None = None) -> AgentResult:
        schema = self.schema
        if not schema.tables:
            return AgentResult(status="failed", error="The database has no tables to query.")
        schema_text, complete = schema.for_prompt(self.budget)
        similar_examples = self.feedback_store.find_similar(question, schema.database, top_k=2) if self.feedback_store else []
        few_shot_text = format_few_shot_examples(similar_examples)
        messages = [
            {"role": "system", "content": prompts.build_system(
                self.adapter.dialect_hints(), self.allow_samples, complete)},
            {"role": "user", "content": prompts.build_user(
                question, schema.database, schema_text, complete, self.notes, history or [], few_shot_text)},
        ]

        out = AgentResult(status="failed")
        last_ok: AgentResult | None = None
        nudged_empty = False

        while out.model_calls < self.max_steps:
            completion = self.router.complete(messages)
            out.model_calls += 1
            out.provider, out.model = completion.provider, completion.model
            messages.append({"role": "assistant", "content": completion.text})

            try:
                action = parse_action(completion.text)
            except ProtocolError as exc:
                messages.append({"role": "user", "content": (
                    f"{exc} Reply with exactly one JSON object using one of the listed actions.")})
                continue

            kind = action["action"]

            if kind == "list_tables":
                names = schema.table_names
                self.on_event("Listing tables")
                obs = f"Tables in database ({len(names)}): " + ", ".join(names)
                messages.append({"role": "user", "content": f"Observation:\n{obs[:OBSERVATION_CAP]}"})
                continue

            if kind in ("describe_table", "describe_tables"):
                names = [str(t) for t in (action.get("tables") or ([action["table"]] if "table" in action else []))][:8]
                self.on_event(f"Exploring: {', '.join(names) or '(none)'}")
                obs = schema.describe(names) if names else "No tables requested."
                messages.append({"role": "user", "content": f"Observation:\n{obs[:OBSERVATION_CAP]}"})
                continue

            if kind == "find_relationships":
                tbl = action.get("table")
                self.on_event(f"Finding relationships for {tbl or 'all tables'}")
                rels = schema.find_relationships(str(tbl) if tbl else None)
                obs = "\n".join(rels) if rels else "No explicit or inferred foreign key relationships found."
                messages.append({"role": "user", "content": f"Observation (Relationships):\n{obs[:OBSERVATION_CAP]}"})
                continue

            if kind == "search_workspace":
                query_term = str(action.get("query") or "")
                self.on_event(f"Searching workspace for '{query_term}'")
                obs = search_workspace(self.project_root, query_term)
                messages.append({"role": "user", "content": f"Observation (Workspace search for '{query_term}'):\n{obs[:OBSERVATION_CAP]}"})
                continue

            if kind == "explain_query":
                sql = str(action.get("sql") or "")
                self.on_event("Checking query execution plan")
                obs = self._explain_action(sql)
                messages.append({"role": "user", "content": f"Observation (Query plan):\n{obs[:OBSERVATION_CAP]}"})
                continue

            if kind == "run_query":
                sql = str(action.get("sql") or "")
                self.on_event("Running tentative exploratory query")
                obs = self._run_action(sql)
                messages.append({"role": "user", "content": f"Observation (Query execution):\n{obs[:OBSERVATION_CAP]}"})
                continue

            if kind in ("sample_rows", "distinct_values"):
                messages.append({"role": "user", "content": self._data_action(kind, action)})
                continue

            if kind == "clarify":
                question_text = str(action.get("question") or "Could you clarify?")
                options = [str(o) for o in (action.get("options") or [])][:4]
                answer = self.on_clarify(question_text, options) if self.on_clarify else None
                if answer is None:
                    out.status = "clarify"
                    out.clarification = (question_text, options)
                    return out
                messages.append({"role": "user", "content": f"The user answered: {answer}"})
                continue


            # ---- final -------------------------------------------------------------
            self.on_event("Drafted query")
            failure = self._try_final(action, out)
            if failure is None:
                if self._should_nudge_empty(out) and not nudged_empty:
                    nudged_empty = True
                    last_ok = _snapshot(out)
                    self.on_event("Query returned 0 rows; double-checking filters")
                    messages.append({"role": "user", "content": self._empty_nudge()})
                    continue
                return out

            out.repairs += 1
            if out.repairs > self.max_repairs:
                out.status, out.error = "failed", failure
                return last_ok or out
            self.on_event(f"Repair {out.repairs}/{self.max_repairs}: {failure[:110]}")
            messages.append({"role": "user", "content": f"{failure}\nFix it and reply with a new final JSON."})

        if last_ok is not None:
            return last_ok
        out.error = out.error or "The model did not produce a valid query within the step limit."
        return out

    # ------------------------------------------------------------------
    def _try_final(self, action: dict, out: AgentResult) -> str | None:
        """Validate -> EXPLAIN -> execute. Returns a failure message, or None on success."""
        try:
            checked = validate(
                action["sql"], known_tables=set(self.schema.table_names),
                database=self.schema.database, dialect=self.adapter.dialect,
                max_rows=self.max_rows)
        except ValidationError as exc:
            return f"Your SQL was rejected by the safety validator: {exc}"

        plan = self.adapter.explain(checked.sql)
        if not plan.ok:
            return f"The database rejected the query at planning time: {plan.error}"

        try:
            result = self.adapter.execute_readonly(checked.sql, self.max_rows)
        except DatabaseError as exc:
            return f"The query failed when executed: {exc}"

        out.status = "ok"
        out.sql = checked.sql
        out.explanation = str(action.get("explanation") or "").strip()
        out.assumptions = [str(a) for a in (action.get("assumptions") or [])][:5]
        out.result = result
        out.plan_warnings = plan.warnings
        out.index_suggestions = getattr(plan, "index_suggestions", [])
        out.limit_applied = checked.limit_applied
        out.error = None
        return None

    def _explain_action(self, sql: str) -> str:
        try:
            checked = validate(
                sql, known_tables=set(self.schema.table_names),
                database=self.schema.database, dialect=self.adapter.dialect,
                max_rows=self.max_rows)
            plan = self.adapter.explain(checked.sql)
            if not plan.ok:
                return f"Plan failed: {plan.error}"
            lines = ["Plan OK."]
            if plan.warnings:
                lines.append(f"Warnings: {'; '.join(plan.warnings)}")
            if getattr(plan, "index_suggestions", None):
                lines.append(f"Index suggestions: {'; '.join(plan.index_suggestions)}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Could not explain query: {exc}"

    def _run_action(self, sql: str) -> str:
        try:
            checked = validate(
                sql, known_tables=set(self.schema.table_names),
                database=self.schema.database, dialect=self.adapter.dialect,
                max_rows=min(10, self.max_rows))
            plan = self.adapter.explain(checked.sql)
            if not plan.ok:
                return f"Exploratory execution blocked - plan failed: {plan.error}"
            res = self.adapter.execute_readonly(checked.sql, min(5, self.max_rows))
            rows = mask_rows(res.rows, 5) if self.allow_samples else [["<masked>" for _ in r] for r in res.rows]
            return f"Executed in {res.elapsed_s:.3f}s, {len(res.rows)} rows:\ncolumns={res.columns}\nrows={rows}"
        except Exception as exc:
            return f"Exploratory query failed: {exc}"

    def _data_action(self, kind: str, action: dict) -> str:
        if not self.allow_samples:
            return ("Observation: row values are disabled (privacy mode 'schema_only'). "
                    "Rely on the schema, enum values, and column comments.")
        table, column = str(action.get("table", "")), str(action.get("column", ""))
        if self.schema.get(table) is None:
            return f"Observation: no such table '{table}'."
        try:
            if kind == "sample_rows":
                self.on_event(f"Sampling rows from {table}")
                res = self.adapter.sample_rows(table, 5)
                rows = mask_rows(res.rows, 5)
                return f"Observation (masked sample of {table}):\ncolumns={res.columns}\nrows={rows}"
            self.on_event(f"Checking distinct values of {table}.{column}")
            values = [mask_value(v) for v in self.adapter.distinct_values(table, column, 25)]
            return f"Observation (distinct {table}.{column}): {values}"
        except DatabaseError as exc:
            return f"Observation: could not read that ({exc})."


    @staticmethod
    def _should_nudge_empty(out: AgentResult) -> bool:
        if out.result is None or out.result.rows:
            return False
        return bool(re.search(r"\b(where|join|having)\b", out.sql, re.IGNORECASE))

    def _empty_nudge(self) -> str:
        msg = ("The query is valid but returned 0 rows. If that is unexpected, re-check filter "
               "values (enum values / status codes / formats), date ranges, and join conditions, "
               "then send a corrected final JSON. If 0 rows is genuinely correct, resend the "
               "same final JSON.")
        if self.allow_samples:
            msg += " You may use distinct_values to inspect real values first."
        return msg


def _snapshot(out: AgentResult) -> AgentResult:
    return AgentResult(**{k: getattr(out, k) for k in out.__dataclass_fields__})
