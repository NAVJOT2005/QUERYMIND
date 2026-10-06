"""Test doubles: a fake database adapter and a scripted LLM."""
from querymind.adapters.base import (
    Column,
    DatabaseAdapter,
    ForeignKey,
    PlanInfo,
    QueryResult,
    Schema,
    Table,
)
from querymind.errors import DatabaseError
from querymind.llm.router import LLMRouter, ProviderHandle


def shop_schema(extra_tables: int = 0) -> Schema:
    tables = {
        "customers": Table("customers", [Column("id", "int", False, True), Column("name", "varchar(100)"),
                                         Column("country", "char(2)", comment="ISO code")]),
        "orders": Table("orders", [Column("id", "int", False, True), Column("customer_id", "int"),
                                   Column("status", "enum('paid','cancelled')")],
                        foreign_keys=[ForeignKey(["customer_id"], "customers", ["id"])], row_estimate=800),
    }
    for i in range(extra_tables):
        tables[f"t{i}"] = Table(f"t{i}", [Column("id", "int", False, True)] +
                                [Column(f"col_{j}", "varchar(50)") for j in range(10)])
    return Schema("shop", tables)


class FakeAdapter(DatabaseAdapter):
    dialect = "mysql"

    def __init__(self, schema=None):
        self._schema = schema or shop_schema()
        self.plan_errors: dict[str, str] = {}      # substring -> EXPLAIN error
        self.exec_errors: dict[str, str] = {}      # substring -> execution error
        self.rows_for: dict[str, list] = {}        # substring -> rows
        self.executed: list[str] = []
        self.sampled: list[tuple] = []

    def connect(self): ...
    def close(self): ...
    def server_info(self): return "FakeSQL 1.0"
    def introspect(self): return self._schema
    def dialect_hints(self): return "Engine: FakeSQL."

    def explain(self, sql):
        for frag, msg in self.plan_errors.items():
            if frag in sql:
                return PlanInfo(ok=False, error=msg)
        return PlanInfo(ok=True)

    def execute_readonly(self, sql, max_rows):
        self.executed.append(sql)
        for frag, msg in self.exec_errors.items():
            if frag in sql:
                raise DatabaseError(msg)
        rows = [[1]]
        for frag, r in self.rows_for.items():
            if frag in sql:
                rows = r
        return QueryResult(["x"], rows, False, 0.001, sql)

    def sample_rows(self, table, n):
        self.sampled.append((table, n))
        return QueryResult(["id", "email"], [[1, "jane@example.com"]], False, 0, "")

    def distinct_values(self, table, column, limit):
        self.sampled.append((table, column))
        return ["paid", "cancelled"]


class Scripted:
    """Callable LLM stub that replays canned replies and records every prompt it received."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, messages):
        self.calls.append([dict(m) for m in messages])
        return self.replies.pop(0)


def scripted_router(replies):
    llm = Scripted(replies)
    return LLMRouter([ProviderHandle("fake", "fake-model", llm)]), llm


def final(sql, explanation="does a thing", assumptions=None):
    import json
    return json.dumps({"action": "final", "sql": sql, "explanation": explanation,
                       "assumptions": assumptions or []})
