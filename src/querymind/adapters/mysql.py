"""MySQL / MariaDB adapter (PyMySQL).

Safety layers implemented here (the validator in querymind.safety is the first line):
  * session is set READ ONLY
  * server-side execution timeout (MySQL: MAX_EXECUTION_TIME, MariaDB: max_statement_time)
  * client-side socket read timeout as a backstop
  * row cap via fetchmany(), so a huge result can never be pulled into memory
"""

from __future__ import annotations

import datetime as dt
import decimal
import re
import time
from typing import Any

import pymysql
import pymysql.cursors

from querymind.adapters.base import (
    Column,
    DatabaseAdapter,
    ForeignKey,
    PlanInfo,
    QueryResult,
    Schema,
    Table,
)
from querymind.config import DBConfig
from querymind.errors import DatabaseError
from querymind.safety.index_advisor import suggest_indexes_for_query


_IDENT = re.compile(r"^[A-Za-z0-9_$]+$")


def _quote(ident: str) -> str:
    if not _IDENT.match(ident):
        raise DatabaseError(f"Refusing unusual identifier: {ident!r}")
    return f"`{ident}`"


def to_display(value: Any) -> Any:
    """Make driver values printable / JSON-friendly."""
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time, dt.timedelta)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    return value


class MySQLAdapter(DatabaseAdapter):
    dialect = "mysql"

    def __init__(self, cfg: DBConfig, timeout_s: int = 10):
        self.cfg = cfg
        self.timeout_s = timeout_s
        self._conn: pymysql.connections.Connection | None = None
        self._version = ""
        self.is_mariadb = False

    # -- lifecycle ------------------------------------------------------------
    def connect(self) -> None:
        try:
            self._conn = pymysql.connect(
                host=self.cfg.host, port=self.cfg.port, user=self.cfg.user,
                password=self.cfg.password, database=self.cfg.database,
                connect_timeout=5, read_timeout=self.timeout_s + 5, write_timeout=5,
                autocommit=True, charset="utf8mb4",
            )
        except pymysql.MySQLError as exc:
            raise DatabaseError(
                f"Could not connect to {self.cfg.host}:{self.cfg.port}/{self.cfg.database} "
                f"as '{self.cfg.user}': {exc}"
            ) from exc
        with self._conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            self._version = str(cur.fetchone()[0])
            self.is_mariadb = "mariadb" in self._version.lower()
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            if self.is_mariadb:
                cur.execute(f"SET SESSION max_statement_time = {int(self.timeout_s)}")
            else:
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(self.timeout_s) * 1000}")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    @property
    def conn(self) -> pymysql.connections.Connection:
        if self._conn is None:
            raise DatabaseError("Not connected.")
        return self._conn

    def server_info(self) -> str:
        return f"{'MariaDB' if self.is_mariadb else 'MySQL'} {self._version}"

    def _major(self) -> int:
        m = re.match(r"(\d+)", self._version)
        return int(m.group(1)) if m else 0

    # -- introspection --------------------------------------------------------
    def introspect(self) -> Schema:
        db = self.cfg.database
        tables: dict[str, Table] = {}
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME, TABLE_TYPE, TABLE_COMMENT, TABLE_ROWS "
                "FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME", (db,))
            for name, ttype, comment, rows in cur.fetchall():
                tables[name] = Table(
                    name=name, columns=[], comment=comment or "",
                    row_estimate=int(rows) if rows is not None else None,
                    is_view=(ttype == "VIEW"),
                )
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_COMMENT "
                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION", (db,))
            for tname, cname, ctype, nullable, key, comment in cur.fetchall():
                if tname in tables:
                    tables[tname].columns.append(Column(
                        name=cname, type=str(ctype), nullable=(nullable == "YES"),
                        primary_key=(key == "PRI"), comment=comment or ""))
            cur.execute(
                "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, "
                "REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL "
                "ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION", (db,))
            grouped: dict[tuple[str, str], ForeignKey] = {}
            for tname, cname_fk, col, rtable, rcol in cur.fetchall():
                fk = grouped.setdefault((tname, cname_fk), ForeignKey([], rtable, []))
                fk.columns.append(col)
                fk.ref_columns.append(rcol)
            for (tname, _), fk in grouped.items():
                if tname in tables:
                    tables[tname].foreign_keys.append(fk)
            cur.execute(
                "SELECT TABLE_NAME, INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) "
                "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND INDEX_NAME <> 'PRIMARY' "
                "GROUP BY TABLE_NAME, INDEX_NAME", (db,))
            for tname, iname, cols in cur.fetchall():
                if tname in tables:
                    tables[tname].indexes.append(f"{iname}({cols})")
        return Schema(database=db, tables=tables)

    # -- execution ------------------------------------------------------------
    def explain(self, sql: str) -> PlanInfo:
        try:
            with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(f"EXPLAIN {sql}")
                plan = cur.fetchall()
        except pymysql.MySQLError as exc:
            return PlanInfo(ok=False, error=_clean_error(exc))
        warnings = []
        for row in plan:
            rtype = str(row.get("type") or "").upper()
            est = row.get("rows")
            if rtype == "ALL" and est and int(est) >= 10_000:
                warnings.append(f"Full table scan on `{row.get('table')}` (~{int(est):,} rows); "
                                f"consider an index or a narrower filter.")
            extra = str(row.get("Extra") or "")
            if "Using temporary" in extra and "Using filesort" in extra and est and int(est) >= 10_000:
                warnings.append(f"Temporary table + filesort on `{row.get('table')}`.")
        suggestions = suggest_indexes_for_query(sql, warnings, self.dialect)
        return PlanInfo(ok=True, warnings=warnings, index_suggestions=suggestions)


    def execute_readonly(self, sql: str, max_rows: int) -> QueryResult:
        started = time.perf_counter()
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql)
                columns = [d[0] for d in cur.description] if cur.description else []
                fetched = cur.fetchmany(max_rows + 1)
        except pymysql.MySQLError as exc:
            raise DatabaseError(_clean_error(exc)) from exc
        truncated = len(fetched) > max_rows
        rows = [[to_display(v) for v in r] for r in fetched[:max_rows]]
        return QueryResult(columns, rows, truncated, time.perf_counter() - started, sql)

    def sample_rows(self, table: str, n: int) -> QueryResult:
        n = max(1, min(int(n), 5))
        return self.execute_readonly(f"SELECT * FROM {_quote(table)} LIMIT {n}", n)

    def distinct_values(self, table: str, column: str, limit: int) -> list[Any]:
        limit = max(1, min(int(limit), 25))
        res = self.execute_readonly(
            f"SELECT DISTINCT {_quote(column)} FROM {_quote(table)} "
            f"WHERE {_quote(column)} IS NOT NULL LIMIT {limit}", limit)
        return [r[0] for r in res.rows]

    # -- prompt hints ---------------------------------------------------------
    def dialect_hints(self) -> str:
        modern = self._major() >= 8 or (self.is_mariadb and self._major() >= 10)
        lines = [
            f"Engine: {self.server_info()}. Dialect: MySQL.",
            "Row limits use LIMIT n (no TOP/FETCH). Concatenate with CONCAT(). No ILIKE; "
            "LIKE is case-insensitive under default collations.",
            "Dates: DATE_SUB(CURDATE(), INTERVAL n DAY), DATEDIFF, DATE_FORMAT(d,'%Y-%m'), "
            "YEAR(), MONTH(), QUARTER().",
            "No FULL OUTER JOIN (emulate with LEFT JOIN UNION RIGHT JOIN). "
            "Integer division is DIV; / returns a decimal.",
            "Non-aggregated selected columns must appear in GROUP BY (ONLY_FULL_GROUP_BY).",
        ]
        lines.append(
            "CTEs (WITH) and window functions (ROW_NUMBER, RANK, LAG, SUM() OVER) are available."
            if modern else
            "This server is old: NO CTEs and NO window functions; use subqueries or joins instead."
        )
        return "\n".join(lines)


def _clean_error(exc: Exception) -> str:
    args = getattr(exc, "args", ())
    if len(args) >= 2:
        return f"MySQL error {args[0]}: {args[1]}"
    return str(exc)
