"""PostgreSQL adapter with support for psycopg, psycopg2, or pg8000."""

from __future__ import annotations

import re
import time
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


def _get_pg_driver():
    for mod in ("psycopg", "psycopg2", "pg8000"):
        try:
            return __import__(mod)
        except ImportError:
            pass
    return None


class PostgreSQLAdapter(DatabaseAdapter):
    dialect = "postgres"

    def __init__(self, cfg: DBConfig, timeout_s: int = 10):
        self.cfg = cfg
        self.timeout_s = timeout_s
        self._conn: Any = None
        self._version = ""

    def connect(self) -> None:
        driver = _get_pg_driver()
        if driver is None:
            raise DatabaseError(
                "No PostgreSQL driver installed. Please run: pip install psycopg[binary] (or psycopg2-binary)."
            )

        try:
            # psycopg 3 or psycopg2 connection
            if hasattr(driver, "connect"):
                self._conn = driver.connect(
                    host=self.cfg.host,
                    port=self.cfg.port or 5432,
                    user=self.cfg.user,
                    password=self.cfg.password,
                    dbname=self.cfg.database,
                    connect_timeout=5,
                )
            else:
                raise DatabaseError("Unsupported PostgreSQL driver interface.")
        except Exception as exc:
            raise DatabaseError(
                f"Could not connect to PostgreSQL {self.cfg.host}:{self.cfg.port}/{self.cfg.database}: {exc}"
            ) from exc

        # Set read-only transaction and statement timeout
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT version();")
                self._version = str(cur.fetchone()[0])
                cur.execute(f"SET statement_timeout = {int(self.timeout_s) * 1000};")
                cur.execute("SET default_transaction_read_only = ON;")
        except Exception as exc:
            self.close()
            raise DatabaseError(f"Failed initializing PostgreSQL session: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    @property
    def conn(self) -> Any:
        if self._conn is None:
            raise DatabaseError("Not connected.")
        return self._conn

    def server_info(self) -> str:
        return f"PostgreSQL {self._version.split()[1] if len(self._version.split()) > 1 else ''}".strip()

    def introspect(self) -> Schema:
        db = self.cfg.database
        tables: dict[str, Table] = {}
        with self.conn.cursor() as cur:
            # Get tables & views in public schema
            cur.execute(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name;"
            )
            for name, ttype in cur.fetchall():
                tables[name] = Table(
                    name=name,
                    columns=[],
                    is_view=(ttype == "VIEW"),
                )

            # Columns
            cur.execute(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns WHERE table_schema = 'public' "
                "ORDER BY table_name, ordinal_position;"
            )
            for tname, cname, dtype, nullable in cur.fetchall():
                if tname in tables:
                    tables[tname].columns.append(
                        Column(
                            name=cname,
                            type=str(dtype).upper(),
                            nullable=(nullable == "YES"),
                        )
                    )

            # Foreign keys
            cur.execute(
                """
                SELECT
                    tc.table_name, kcu.column_name,
                    ccu.table_name AS foreign_table_name,
                    ccu.column_name AS foreign_column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                  AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public';
                """
            )
            for tname, col, rtable, rcol in cur.fetchall():
                if tname in tables:
                    tables[tname].foreign_keys.append(
                        ForeignKey(columns=[col], ref_table=rtable, ref_columns=[rcol])
                    )
        return Schema(database=db, tables=tables)

    def explain(self, sql: str) -> PlanInfo:
        try:
            with self.conn.cursor() as cur:
                cur.execute(f"EXPLAIN {sql}")
                plan = [r[0] for r in cur.fetchall()]
        except Exception as exc:
            return PlanInfo(ok=False, error=str(exc))

        warnings = []
        for line in plan:
            if "Seq Scan on" in line:
                match = re.search(r"Seq Scan on\s+([A-Za-z0-9_]+)", line)
                tbl = match.group(1) if match else "table"
                warnings.append(f"Sequential scan on `{tbl}`; consider adding an index.")
        return PlanInfo(ok=True, warnings=warnings)

    def execute_readonly(self, sql: str, max_rows: int) -> QueryResult:
        started = time.perf_counter()
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql)
                columns = [d[0] for d in cur.description] if cur.description else []
                fetched = cur.fetchmany(max_rows + 1)
        except Exception as exc:
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
            f"Engine: {self.server_info()}. Dialect: PostgreSQL.\n"
            "Use LIMIT n for row caps. String concatenation uses ||.\n"
            "Case-insensitive matching uses ILIKE. Identifiers are case-sensitive if double-quoted.\n"
            "Date arithmetic: CURRENT_DATE - INTERVAL '30 days', DATE_TRUNC('month', col).\n"
            "CTEs (WITH), window functions, and JSON operators are fully supported."
        )
