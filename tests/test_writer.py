import pytest

from querymind.errors import ValidationError
from querymind.safety.writer import (
    execute_write_transaction,
    validate_write,
)


def test_validate_write_inserts():
    val = validate_write(
        "INSERT INTO customers (id, name, country) VALUES (1, 'Alice', 'US')",
        known_tables={"customers", "orders"},
        database="shop",
    )
    assert val.statement_type == "INSERT"
    assert val.table == "customers"
    assert "INSERT" in val.sql


def test_validate_write_updates_with_where():
    val = validate_write(
        "UPDATE customers SET country = 'CA' WHERE id = 5",
        known_tables={"customers"},
        database="shop",
    )
    assert val.statement_type == "UPDATE"
    assert val.table == "customers"
    assert val.has_where is True
    assert val.preview_sql is not None
    assert "WHERE" in val.preview_sql


def test_validate_write_updates_without_where():
    val = validate_write(
        "UPDATE customers SET country = 'CA'",
        known_tables={"customers"},
        database="shop",
    )
    assert val.statement_type == "UPDATE"
    assert val.has_where is False


def test_validate_write_blocks_ddl_and_drop():
    with pytest.raises(ValidationError, match="Only INSERT, UPDATE, and DELETE"):
        validate_write("DROP TABLE customers", known_tables={"customers"})

    with pytest.raises(ValidationError, match="Only INSERT, UPDATE, and DELETE"):
        validate_write("ALTER TABLE customers ADD COLUMN age INT", known_tables={"customers"})

    with pytest.raises(ValidationError, match="Only INSERT, UPDATE, and DELETE"):
        validate_write("TRUNCATE TABLE customers", known_tables={"customers"})


def test_validate_write_blocks_unknown_table():
    with pytest.raises(ValidationError, match="Unknown table"):
        validate_write("DELETE FROM non_existent WHERE id=1", known_tables={"customers"})


class FakeWriteConnection:
    def __init__(self):
        self.log = []
        self.rowcount = 3

    def autocommit(self, val):
        self.log.append(f"autocommit({val})")

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql):
        self.log.append(sql.strip())

    @property
    def description(self):
        return [("id",), ("name",)]

    def fetchall(self):
        return [(1, "Alice"), (2, "Bob")]


def test_execute_write_transaction_commit():
    conn = FakeWriteConnection()
    val = validate_write("DELETE FROM customers WHERE id IN (1, 2)", known_tables={"customers"})

    res = execute_write_transaction(
        conn, val, confirm_callback=lambda val, affected, cols, rows: True
    )

    assert res.committed is True
    assert res.affected_rows == 3
    assert "START TRANSACTION" in conn.log
    assert "COMMIT" in conn.log
    assert "ROLLBACK" not in conn.log


def test_execute_write_transaction_rollback():
    conn = FakeWriteConnection()
    val = validate_write("UPDATE customers SET country='US' WHERE id=1", known_tables={"customers"})

    res = execute_write_transaction(
        conn, val, confirm_callback=lambda val, affected, cols, rows: False
    )

    assert res.committed is False
    assert "START TRANSACTION" in conn.log
    assert "ROLLBACK" in conn.log
    assert "COMMIT" not in conn.log
