"""Database-agnostic types and the adapter interface.

The agent only ever talks to DatabaseAdapter, so supporting PostgreSQL later means
writing one new file here, with no changes to the agent, safety layer, or CLI.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Column:
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    comment: str = ""


@dataclass
class ForeignKey:
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


@dataclass
class Table:
    name: str
    columns: list[Column]
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    comment: str = ""
    row_estimate: int | None = None
    indexes: list[str] = field(default_factory=list)  # e.g. "idx_orders_date(order_date)"
    is_view: bool = False

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


@dataclass
class Schema:
    database: str
    tables: dict[str, Table]

    @property
    def table_names(self) -> list[str]:
        return list(self.tables)

    def get(self, name: str) -> Table | None:
        if name in self.tables:
            return self.tables[name]
        lowered = {n.lower(): t for n, t in self.tables.items()}
        return lowered.get(name.lower())

    def fingerprint(self) -> str:
        return hashlib.sha1(self.describe(self.table_names).encode()).hexdigest()[:12]

    # -- text renderings sent to the model (kept compact on purpose) --------
    def _table_line(self, t: Table) -> str:
        fk_by_col = {}
        for fk in t.foreign_keys:
            for c, rc in zip(fk.columns, fk.ref_columns, strict=False):
                fk_by_col[c] = f"{fk.ref_table}.{rc}"
        parts = []
        for c in t.columns:
            bits = [f"{c.name} {c.type}"]
            if c.primary_key:
                bits.append("PK")
            if c.name in fk_by_col:
                bits.append(f"-> {fk_by_col[c.name]}")
            parts.append(" ".join(bits))
        head = f"{t.name}{' [view]' if t.is_view else ''}"
        # row_estimate is intentionally NOT shown: InnoDB's TABLE_ROWS is a stale guess
        # (often wildly off after bulk loads) and would mislead both users and the model.
        return f"{head}: " + ", ".join(parts)

    def describe(self, names: list[str]) -> str:
        lines = []
        for name in names:
            t = self.get(name)
            if t is None:
                lines.append(f"{name}: (no such table)")
                continue
            lines.append(self._table_line(t))
            if t.comment:
                lines.append(f"  # table: {t.comment}")
            for c in t.columns:
                if c.comment:
                    lines.append(f"  # {c.name}: {c.comment}")
            if t.indexes:
                lines.append(f"  # indexes: {'; '.join(t.indexes)}")
        return "\n".join(lines)

    def overview(self) -> str:
        """Names plus a few columns each: used when the full schema won't fit the budget."""
        lines = []
        for t in self.tables.values():
            cols = t.column_names()
            shown = ", ".join(cols[:6]) + (f", +{len(cols) - 6} more" if len(cols) > 6 else "")
            lines.append(f"{t.name}({shown})")
        return "\n".join(lines)

    def for_prompt(self, budget_chars: int) -> tuple[str, bool]:
        """Return (text, is_complete). Falls back to the overview for large schemas."""
        full = self.describe(self.table_names)
        if len(full) <= budget_chars:
            return full, True
        return self.overview(), False

    def find_relationships(self, table_name: str | None = None) -> list[str]:
        """Return explicit foreign keys and inferred relationships from column naming."""
        rels: list[str] = []
        known_tables = {name.lower(): name for name in self.tables}

        # 1. Explicit foreign keys
        for tname, t in self.tables.items():
            if table_name and tname.lower() != table_name.lower():
                continue
            for fk in t.foreign_keys:
                for c, rc in zip(fk.columns, fk.ref_columns, strict=False):
                    rels.append(f"{tname}.{c} -> {fk.ref_table}.{rc} [FK]")

        # 2. Inferred joins from column naming (e.g. customer_id -> customers.id)
        for tname, t in self.tables.items():
            if table_name and tname.lower() != table_name.lower():
                continue
            explicit_cols = {c for fk in t.foreign_keys for c in fk.columns}
            for col in t.columns:
                if col.name in explicit_cols or col.primary_key:
                    continue
                cname = col.name.lower()
                if cname.endswith("_id") and len(cname) > 3:
                    prefix = cname[:-3]
                    candidates = [prefix, prefix + "s", prefix + "es", prefix.rstrip("y") + "ies"]
                    target_table_name = None
                    for cand in candidates:
                        if cand in known_tables and cand != tname.lower():
                            target_table_name = known_tables[cand]
                            break
                    if target_table_name:
                        target_tab = self.tables[target_table_name]
                        target_cols = [c.name for c in target_tab.columns]
                        target_pk = next((c.name for c in target_tab.columns if c.primary_key), None)
                        target_col = target_pk or ("id" if "id" in target_cols else (col.name if col.name in target_cols else None))
                        if target_col:
                            rels.append(
                                f"{tname}.{col.name} -> {target_table_name}.{target_col} "
                                f"[inferred join: ON {tname}.{col.name} = {target_table_name}.{target_col}]"
                            )

        return rels



@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False
    elapsed_s: float = 0.0
    sql_run: str = ""


@dataclass
class PlanInfo:
    ok: bool
    warnings: list[str] = field(default_factory=list)
    index_suggestions: list[str] = field(default_factory=list)
    error: str | None = None



class DatabaseAdapter(ABC):
    dialect: str = "mysql"  # sqlglot dialect name

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def server_info(self) -> str: ...

    @abstractmethod
    def introspect(self) -> Schema: ...

    @abstractmethod
    def explain(self, sql: str) -> PlanInfo:
        """Ask the database to plan (not run) the query. Authoritative for unknown columns."""

    @abstractmethod
    def execute_readonly(self, sql: str, max_rows: int) -> QueryResult: ...

    @abstractmethod
    def sample_rows(self, table: str, n: int) -> QueryResult: ...

    @abstractmethod
    def distinct_values(self, table: str, column: str, limit: int) -> list[Any]: ...

    @abstractmethod
    def dialect_hints(self) -> str:
        """Short, version-aware rules for the model (syntax gotchas for this engine)."""

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()
