"""Database adapters package."""

from querymind.adapters.base import (
    Column,
    DatabaseAdapter,
    ForeignKey,
    PlanInfo,
    QueryResult,
    Schema,
    Table,
)
from querymind.adapters.mysql import MySQLAdapter
from querymind.adapters.postgres import PostgreSQLAdapter
from querymind.adapters.sqlite import SQLiteAdapter

__all__ = [
    "Column",
    "DatabaseAdapter",
    "ForeignKey",
    "MySQLAdapter",
    "PlanInfo",
    "PostgreSQLAdapter",
    "QueryResult",
    "SQLiteAdapter",
    "Schema",
    "Table",
]
