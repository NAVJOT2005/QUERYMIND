"""SQL safety validator.

Model output is untrusted input. This module is the first of several layers:

  1. Parse with sqlglot. Anything that does not parse is rejected (this alone blocks
     SELECT ... INTO OUTFILE and LOAD DATA).
  2. Exactly one statement, and the root must be a query (SELECT / WITH...SELECT / UNION).
  3. No DML/DDL/locking nodes anywhere in the tree (e.g. DELETE hidden inside a CTE).
  4. No dangerous functions (LOAD_FILE, SLEEP, BENCHMARK, GET_LOCK, ...).
  5. Every referenced table must exist in the introspected schema, and cross-database
     access is refused.
  6. The text we EXECUTE is regenerated from the AST with comments stripped, never the
     model's raw string, so tricks such as /*!50000 ... */ executable comments never
     reach the server.
  7. A row LIMIT is injected, or clamped if the model asked for more than allowed.

Column existence is deliberately left to the database: `EXPLAIN` is authoritative and
produces exact error messages that feed the self-repair loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from querymind.errors import ValidationError

logging.getLogger("sqlglot").setLevel(logging.ERROR)

FORBIDDEN_FUNCTIONS = {
    "load_file", "sleep", "benchmark", "get_lock", "release_lock", "release_all_locks",
    "is_free_lock", "is_used_lock", "master_pos_wait", "sys_exec", "sys_eval", "pg_sleep",
}

FORBIDDEN_NODES: tuple[type, ...] = tuple(
    getattr(exp, name) for name in (
        "Insert", "Update", "Delete", "Merge", "Drop", "Create", "Alter", "Command",
        "Set", "Use", "Show", "Lock", "Into", "Grant", "TruncateTable", "Transaction",
        "Commit", "Rollback", "LoadData", "Copy", "Kill",
    ) if hasattr(exp, name)
)


@dataclass
class Validated:
    sql: str                 # safe, executable SQL (regenerated, limited)
    tables: list[str]        # base tables referenced
    limit_applied: bool      # True if we added/clamped a LIMIT


def validate(sql: str, known_tables: set[str] | None = None, database: str | None = None,
             dialect: str = "mysql", max_rows: int = 200) -> Validated:
    if not sql or not sql.strip():
        raise ValidationError("Empty SQL.")

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except SqlglotError as exc:
        raise ValidationError(f"SQL could not be parsed: {str(exc).splitlines()[0]}") from exc

    if len(statements) != 1:
        raise ValidationError("Exactly one SQL statement is allowed.")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise ValidationError(
            f"Only read-only SELECT queries are allowed (got {type(tree).__name__.upper()}).")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise ValidationError(f"Forbidden construct: {type(node).__name__.upper()}.")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in FORBIDDEN_FUNCTIONS:
                raise ValidationError(f"Forbidden function: {name.upper()}().")

    cte_names = {cte.alias.lower() for cte in tree.find_all(exp.CTE) if cte.alias}
    tables: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name
        if not name:
            continue
        if table.db and database and table.db.lower() != database.lower():
            raise ValidationError(
                f"Cross-database access is not allowed (`{table.db}`.`{name}`).")
        if table.db and not database:
            raise ValidationError("Cross-database access is not allowed.")
        if not table.db and name.lower() in cte_names:
            continue
        tables.append(name)

    if known_tables is not None:
        known = {t.lower() for t in known_tables}
        missing = sorted({t for t in tables if t.lower() not in known})
        if missing:
            raise ValidationError(
                f"Unknown table(s): {', '.join(missing)}. Existing tables: "
                f"{', '.join(sorted(known_tables))}.")

    limited, applied = _apply_limit(tree, max_rows)
    return Validated(sql=limited.sql(dialect=dialect, comments=False, pretty=True),
                     tables=sorted(set(tables)), limit_applied=applied)


def _apply_limit(tree: exp.Query, max_rows: int) -> tuple[exp.Query, bool]:
    limit = tree.args.get("limit")
    if limit is None:
        return tree.limit(max_rows), True
    expr = getattr(limit, "expression", None)
    if isinstance(expr, exp.Literal) and expr.is_int and int(expr.name) > max_rows:
        return tree.limit(max_rows), True
    return tree, False
