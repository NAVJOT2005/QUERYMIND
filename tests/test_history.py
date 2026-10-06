import json
from pathlib import Path

from querymind.history import (
    delete_saved_query,
    get_saved_query,
    list_saved_queries,
    load_history,
    record_history,
    save_query,
)


def test_history_and_saved_queries(tmp_path: Path, monkeypatch):
    history_file = tmp_path / "history.jsonl"
    saved_file = tmp_path / "saved_queries.json"

    monkeypatch.setattr("querymind.history._history_file", lambda project=True: history_file)
    monkeypatch.setattr("querymind.history._saved_file", lambda project=True: saved_file)

    # History
    assert load_history() == []
    record_history("how many orders?", "SELECT COUNT(*) FROM orders", "ok", "shop", 0.05)
    record_history("active customers", "SELECT * FROM customers", "ok", "shop", 0.08)

    entries = load_history(limit=5)
    assert len(entries) == 2
    assert entries[0]["question"] == "active customers"
    assert entries[1]["question"] == "how many orders?"

    # Saved queries
    assert list_saved_queries() == {}
    save_query("top_customers", "SELECT customer_id, sum(amount) FROM orders GROUP BY customer_id", "Ranks top buyers")
    queries = list_saved_queries()
    assert "top_customers" in queries
    assert queries["top_customers"]["description"] == "Ranks top buyers"

    q = get_saved_query("top_customers")
    assert q is not None
    assert "orders" in q["sql"]

    assert delete_saved_query("top_customers") is True
    assert list_saved_queries() == {}
