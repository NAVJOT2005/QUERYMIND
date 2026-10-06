"""Approval-gated write execution with transaction dry-runs and before/after previews."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import pymysql
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from querymind.errors import DatabaseError, ValidationError
from querymind.safety.validator import FORBIDDEN_FUNCTIONS

logging.getLogger("sqlglot").setLevel(logging.ERROR)

ALLOWED_WRITE_TYPES = (exp.Insert, exp.Update, exp.Delete)


@dataclass
class ValidatedWrite:
    sql: str
    statement_type: str  # "INSERT" | "UPDATE" | "DELETE"
    table: str
    has_where: bool
    preview_sql: str | None = None


def validate_write(
    sql: str,
    known_tables: set[str] | None = None,
    database: str | None = None,
    dialect: str = "mysql",
) -> ValidatedWrite:
    """Validate that a SQL statement is a single, safe INSERT/UPDATE/DELETE statement."""
    if not sql or not sql.strip():
        raise ValidationError("Empty write SQL.")

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except SqlglotError as exc:
        raise ValidationError(f"SQL could not be parsed: {str(exc).splitlines()[0]}") from exc

    if len(statements) != 1:
        raise ValidationError("Exactly one SQL statement is allowed.")

    tree = statements[0]
    if not isinstance(tree, ALLOWED_WRITE_TYPES):
        raise ValidationError(
            f"Only INSERT, UPDATE, and DELETE statements are allowed (got {type(tree).__name__.upper()}). "
            "DDL statements like DROP, ALTER, and TRUNCATE are blocked."
        )

    stmt_type = type(tree).__name__.upper()

    # Check for forbidden functions
    for node in tree.walk():
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in FORBIDDEN_FUNCTIONS:
                raise ValidationError(f"Forbidden function: {name.upper()}().")

    # Determine targeted table
    target_table = ""
    if isinstance(tree, (exp.Insert, exp.Update, exp.Delete)):
        this_table = tree.this
        if isinstance(this_table, exp.Table):
            target_table = this_table.name
        elif isinstance(this_table, exp.Schema):
            target_table = this_table.this.name
        elif hasattr(tree, "table") and isinstance(tree.table, exp.Table):
            target_table = tree.table.name

    if not target_table:
        for t in tree.find_all(exp.Table):
            if t.name:
                target_table = t.name
                break

    if not target_table:
        raise ValidationError(f"Could not determine target table for {stmt_type}.")

    # Check cross-database access
    for t in tree.find_all(exp.Table):
        if t.db and database and t.db.lower() != database.lower():
            raise ValidationError(f"Cross-database access is not allowed (`{t.db}`.`{t.name}`).")

    if known_tables is not None:
        known = {t.lower() for t in known_tables}
        if target_table.lower() not in known:
            raise ValidationError(
                f"Unknown table '{target_table}'. Existing tables: {', '.join(sorted(known_tables))}."
            )

    has_where = tree.find(exp.Where) is not None

    # Construct preview SQL for UPDATE / DELETE
    preview_sql = None
    if isinstance(tree, (exp.Update, exp.Delete)):
        where_node = tree.find(exp.Where)
        if where_node:
            preview_query = exp.select("*").from_(target_table).where(where_node.this).limit(10)
            preview_sql = preview_query.sql(dialect=dialect)
        else:
            preview_sql = f"SELECT * FROM `{target_table}` LIMIT 10"

    cleaned_sql = tree.sql(dialect=dialect, comments=False, pretty=True)
    return ValidatedWrite(
        sql=cleaned_sql,
        statement_type=stmt_type,
        table=target_table,
        has_where=has_where,
        preview_sql=preview_sql,
    )


@dataclass
class WriteExecutionResult:
    committed: bool
    statement_type: str
    table: str
    affected_rows: int
    preview_rows: list[list[Any]] = field(default_factory=list)
    preview_columns: list[str] = field(default_factory=list)
    sql_executed: str = ""
    error: str | None = None


def execute_write_transaction(
    conn: Any,
    validated: ValidatedWrite,
    confirm_callback: Callable[[ValidatedWrite, int, list[str], list[list[Any]]], bool],
) -> WriteExecutionResult:
    """Execute a validated write inside a transaction with dry-run preview and explicit commit approval."""
    preview_cols: list[str] = []
    preview_rows: list[list[Any]] = []

    # 1. Fetch preview of affected rows if applicable
    if validated.preview_sql:
        try:
            with conn.cursor() as cur:
                cur.execute(validated.preview_sql)
                if cur.description:
                    preview_cols = [d[0] for d in cur.description]
                    preview_rows = [list(r) for r in cur.fetchall()]
        except Exception as exc:
            logging.warning(f"Could not fetch preview rows: {exc}")

    # 2. Begin transaction, execute write, check affected rows
    try:
        # Disable autocommit to ensure explicit transaction control
        if hasattr(conn, "autocommit"):
            conn.autocommit(False)
        with conn.cursor() as cur:
            cur.execute("START TRANSACTION")
            cur.execute(validated.sql)
            affected_rows = cur.rowcount if cur.rowcount is not None else 0

        # 3. Prompt user for approval
        approved = confirm_callback(validated, affected_rows, preview_cols, preview_rows)

        if approved:
            with conn.cursor() as cur:
                cur.execute("COMMIT")
            return WriteExecutionResult(
                committed=True,
                statement_type=validated.statement_type,
                table=validated.table,
                affected_rows=affected_rows,
                preview_rows=preview_rows,
                preview_columns=preview_cols,
                sql_executed=validated.sql,
            )
        else:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK")
            return WriteExecutionResult(
                committed=False,
                statement_type=validated.statement_type,
                table=validated.table,
                affected_rows=affected_rows,
                preview_rows=preview_rows,
                preview_columns=preview_cols,
                sql_executed=validated.sql,
                error="Cancelled by user: transaction was rolled back.",
            )
    except Exception as exc:
        try:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK")
        except Exception:
            pass
        raise DatabaseError(f"Write transaction failed: {exc}") from exc
    finally:
        if hasattr(conn, "autocommit"):
            conn.autocommit(True)
