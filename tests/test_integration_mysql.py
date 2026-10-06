"""Live-database tests. Skipped unless QUERYMIND_TEST_DB_URL points at the demo database:

    docker compose up -d
    QUERYMIND_TEST_DB_URL=mysql://qm_readonly:qm_readonly_pw@127.0.0.1:3307/shop pytest -m integration
"""
import os
from urllib.parse import unquote, urlparse

import pytest

from querymind.adapters.mysql import MySQLAdapter
from querymind.config import DBConfig
from querymind.errors import DatabaseError

URL = os.environ.get("QUERYMIND_TEST_DB_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason="QUERYMIND_TEST_DB_URL not set")]


@pytest.fixture(scope="module")
def adapter():
    u = urlparse(URL)
    cfg = DBConfig(u.hostname, u.port or 3306, unquote(u.username), unquote(u.password or ""), u.path.lstrip("/"))
    with MySQLAdapter(cfg, timeout_s=3) as a:
        yield a


def test_introspection_captures_keys_comments_and_indexes(adapter):
    schema = adapter.introspect()
    assert {"customers", "orders", "order_items", "products", "categories", "reviews"} <= set(schema.table_names)
    text = schema.describe(["orders", "order_items"])
    assert "customer_id int" in text and "-> customers.id" in text
    assert "cancelled and refunded orders do not count as revenue" in text
    assert "idx_orders_date(order_date)" in text


def test_explain_reports_exact_unknown_column_error(adapter):
    plan = adapter.explain("SELECT nope FROM orders")
    assert not plan.ok and "Unknown column 'nope'" in plan.error


def test_execute_and_row_cap(adapter):
    res = adapter.execute_readonly("SELECT * FROM orders", 5)
    assert len(res.rows) == 5 and res.truncated


def test_window_functions_and_ctes_work(adapter):
    res = adapter.execute_readonly(
        "WITH t AS (SELECT status, COUNT(*) n FROM orders GROUP BY status) "
        "SELECT status, RANK() OVER (ORDER BY n DESC) r FROM t", 50)
    assert res.rows and res.columns == ["status", "r"]


@pytest.mark.parametrize("sql", ["INSERT INTO categories VALUES (999,'x',NULL)", "DELETE FROM orders",
                                 "UPDATE orders SET status='paid'", "DROP TABLE orders"])
def test_database_layer_denies_writes_even_if_validator_were_bypassed(adapter, sql):
    with pytest.raises(DatabaseError):
        adapter.execute_readonly(sql, 5)


def test_server_side_timeout(adapter):
    with pytest.raises(DatabaseError):
        adapter.execute_readonly(
            "SELECT COUNT(*) FROM order_items a, order_items b, order_items c, order_items d", 5)
