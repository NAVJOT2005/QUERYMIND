"""SQLite adapter using Python's built-in sqlite3 standard library."""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any

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

_IDENT = re.compile(r"^[A-Za-z0-9_$]+$")


def _quote(ident: str) -> str:
    if not _IDENT.match(ident):
        raise DatabaseError(f"Refusing unusual identifier: {ident!r}")
    return f'"{ident}"'


class SQLiteAdapter(DatabaseAdapter):
    dialect = "sqlite"

    def __init__(self, db_path: str | Path = ":memory:", timeout_s: int = 10):
        self.db_path = str(db_path)
        self.timeout_s = timeout_s
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        try:
            self._conn = sqlite3.connect(
                self.db_path,
                timeout=self.timeout_s,
                check_same_thread=False,
            )
            # Enforce foreign keys
            self._conn.execute("PRAGMA foreign_keys = ON;")
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not connect to SQLite database '{self.db_path}': {exc}") from exc

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise DatabaseError("Not connected.")
        return self._conn

    def server_info(self) -> str:
        return f"SQLite {sqlite3.sqlite_version}"

    def introspect(self) -> Schema:
        tables: dict[str, Table] = {}
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%';")
            for name, obj_type in cur.fetchall():
                tables[name] = Table(
                    name=name,
                    columns=[],
                    is_view=(obj_type == "view"),
                )

            for tname, tab in tables.items():
                # Get column details: cid, name, type, notnull, dflt_value, pk
                cur.execute(f"PRAGMA table_info({_quote(tname)});")
                for cid, cname, ctype, notnull, dflt_val, pk in cur.fetchall():
                    tab.columns.append(
                        Column(
                            name=cname,
                            type=str(ctype or "TEXT").upper(),
                            nullable=not bool(notnull),
                            primary_key=bool(pk),
                        )
                    )

                # Get foreign keys: id, seq, table, from, to, on_update, on_delete, match
                cur.execute(f"PRAGMA foreign_key_list({_quote(tname)});")
                for _, _, rtable, from_col, to_col, _, _, _ in cur.fetchall():
                    if rtable and from_col and to_col:
                        tab.foreign_keys.append(
                            ForeignKey(columns=[from_col], ref_table=rtable, ref_columns=[to_col])
                        )

                # Get indexes
                cur.execute(f"PRAGMA index_list({_quote(tname)});")
                for _, iname, unique, origin, partial in cur.fetchall():
                    cur.execute(f"PRAGMA index_info({_quote(iname)});")
                    cols = [row[2] for row in cur.fetchall()]
                    tab.indexes.append(f"{iname}({', '.join(cols)})")
        finally:
            cur.close()

        db_name = Path(self.db_path).stem if self.db_path != ":memory:" else "main"
        return Schema(database=db_name, tables=tables)

    def explain(self, sql: str) -> PlanInfo:
        warnings = []
        try:
            cur = self.conn.cursor()
            cur.execute(f"EXPLAIN QUERY PLAN {sql}")
            plan_rows = cur.fetchall()
            cur.close()
        except sqlite3.Error as exc:
            return PlanInfo(ok=False, error=str(exc))

        for row in plan_rows:
            detail = str(row[-1] if len(row) >= 4 else row)
            if "SCAN TABLE" in detail:
                match = re.search(r"SCAN TABLE\s+([A-Za-z0-9_]+)", detail)
                tbl = match.group(1) if match else "table"
                warnings.append(f"Full table scan on `{tbl}`; consider adding an index.")
        return PlanInfo(ok=True, warnings=warnings)

    def execute_readonly(self, sql: str, max_rows: int) -> QueryResult:
        started = time.perf_counter()
        try:
            cur = self.conn.cursor()
            cur.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            fetched = cur.fetchmany(max_rows + 1)
            cur.close()
        except sqlite3.Error as exc:
            raise DatabaseError(str(exc)) from exc

        truncated = len(fetched) > max_rows
        rows = [list(r) for r in fetched[:max_rows]]
        return QueryResult(columns, rows, truncated, time.perf_counter() - started, sql)

    def sample_rows(self, table: str, n: int) -> QueryResult:
        n = max(1, min(int(n), 5))
        return self.execute_readonly(f"SELECT * FROM {_quote(table)} LIMIT {n}", n)

    def distinct_values(self, table: str, column: str, limit: int) -> list[Any]:
        limit = max(1, min(int(limit), 25))
        res = self.execute_readonly(
            f"SELECT DISTINCT {_quote(column)} FROM {_quote(table)} "
            f"WHERE {_quote(column)} IS NOT NULL LIMIT {limit}",
            limit,
        )
        return [r[0] for r in res.rows]

    def dialect_hints(self) -> str:
        return (
            f"Engine: {self.server_info()}. Dialect: SQLite.\n"
            "Use LIMIT n for row caps. String concatenation uses ||.\n"
            "Date functions: date(), time(), datetime(), strftime('%Y-%m', col).\n"
            "Supports CTEs (WITH) and window functions.\n"
            "Case-insensitive LIKE for ASCII characters."
        )
