import pytest

from querymind.errors import ValidationError
from querymind.safety import mask_value, validate

TABLES = {"customers", "orders"}


def v(sql, **kw):
    return validate(sql, TABLES, "shop", max_rows=kw.pop("max_rows", 200), **kw)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM customers",
    "WITH a AS (SELECT * FROM orders) SELECT * FROM a",
    "SELECT id FROM customers UNION SELECT id FROM orders",
    "SELECT c.name, COUNT(*) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.name",
    "SELECT * FROM shop.customers",
    "SELECT name, ROW_NUMBER() OVER (ORDER BY id) FROM customers",
])
def test_allows_read_queries(sql):
    assert v(sql).sql.upper().startswith(("SELECT", "WITH", "("))


@pytest.mark.parametrize("sql,fragment", [
    ("DROP TABLE customers", "DROP"),
    ("UPDATE customers SET name='x'", "UPDATE"),
    ("DELETE FROM orders", "DELETE"),
    ("INSERT INTO customers VALUES (1)", "INSERT"),
    ("SELECT 1; DROP TABLE customers", "one SQL statement"),
    ("WITH x AS (DELETE FROM customers) SELECT 1", "DELETE"),
    ("SELECT SLEEP(10)", "SLEEP"),
    ("SELECT LOAD_FILE('/etc/passwd')", "LOAD_FILE"),
    ("SELECT BENCHMARK(100000000, MD5('a'))", "BENCHMARK"),
    ("SELECT * FROM customers INTO OUTFILE '/tmp/x'", "parsed"),
    ("SELECT * FROM customers FOR UPDATE", "LOCK"),
    ("SHOW TABLES", "SHOW"),
    ("CALL do_thing()", "COMMAND"),
    ("SELECT * FROM nonexistent", "Unknown table"),
    ("SELECT * FROM other_db.customers", "Cross-database"),
    ("SELECT * FROM information_schema.tables", "Cross-database"),
    ("", "Empty"),
    ("   ", "Empty"),
])
def test_blocks_dangerous_or_invalid(sql, fragment):
    with pytest.raises(ValidationError) as exc:
        v(sql)
    assert fragment.lower() in str(exc.value).lower()


def test_executable_comments_are_stripped_from_output():
    out = v("/*!50000 DROP TABLE customers */ SELECT id FROM customers -- trailing").sql
    assert "DROP" not in out.upper() and "--" not in out and "/*" not in out


def test_limit_injected_when_missing():
    res = v("SELECT * FROM customers")
    assert res.limit_applied and res.sql.endswith("LIMIT 200")


def test_limit_injected_on_union_and_cte():
    assert v("SELECT id FROM customers UNION SELECT id FROM orders").sql.endswith("LIMIT 200")
    assert v("WITH a AS (SELECT * FROM orders) SELECT * FROM a").sql.endswith("LIMIT 200")


def test_small_limit_preserved_large_clamped():
    assert not v("SELECT * FROM customers LIMIT 5").limit_applied
    assert v("SELECT * FROM customers LIMIT 5").sql.endswith("LIMIT 5")
    clamped = v("SELECT * FROM customers LIMIT 99999")
    assert clamped.limit_applied and clamped.sql.endswith("LIMIT 200")


def test_cte_names_are_not_treated_as_unknown_tables():
    res = v("WITH recent AS (SELECT * FROM orders) SELECT * FROM recent")
    assert res.tables == ["orders"]


def test_table_check_is_case_insensitive():
    assert v("SELECT * FROM CUSTOMERS").tables == ["CUSTOMERS"]


def test_masking():
    assert mask_value("jane.doe@example.com") == "***@example.com"
    assert mask_value("+1 (555) 123-4567").endswith("67") and "555" not in mask_value("+1 (555) 123-4567")
    assert mask_value("Germany") == "Germany"
    assert len(mask_value("x" * 200)) <= 60
    assert mask_value(42) == 42
