from pathlib import Path

from querymind.adapters.sqlite import SQLiteAdapter


def test_sqlite_adapter_lifecycle_and_introspection(tmp_path: Path):
    db_file = tmp_path / "test.db"
    adapter = SQLiteAdapter(db_file)
    with adapter:
        # Create test tables
        adapter.conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT);")
        adapter.conn.execute(
            "CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, "
            "FOREIGN KEY(user_id) REFERENCES users(id));"
        )
        adapter.conn.execute("INSERT INTO users VALUES (1, 'Alice', 'alice@example.com'), (2, 'Bob', 'bob@example.com');")
        adapter.conn.execute("INSERT INTO posts VALUES (10, 1, 'Hello World'), (20, 1, 'Second Post');")

        # Introspect
        schema = adapter.introspect()
        assert "users" in schema.tables
        assert "posts" in schema.tables
        assert schema.get("users").column_names() == ["id", "name", "email"]
        assert len(schema.get("posts").foreign_keys) == 1
        assert schema.get("posts").foreign_keys[0].ref_table == "users"

        # Readonly execute
        res = adapter.execute_readonly("SELECT * FROM users ORDER BY id", 10)
        assert len(res.rows) == 2
        assert res.columns == ["id", "name", "email"]

        # Explain query
        plan = adapter.explain("SELECT * FROM users WHERE name = 'Alice'")
        assert plan.ok is True

        # Sample rows & distinct
        samples = adapter.sample_rows("users", 2)
        assert len(samples.rows) == 2
        dist = adapter.distinct_values("users", "name", 5)
        assert sorted(dist) == ["Alice", "Bob"]
