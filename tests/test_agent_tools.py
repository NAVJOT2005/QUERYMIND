import json
from pathlib import Path

from querymind.agent.agent import Agent
from tests.fakes import FakeAdapter, final, scripted_router, shop_schema

CFG = {"max_steps": 8, "max_repairs": 2, "privacy": "schema_only", "schema_budget_chars": 8000}


def make(replies, adapter=None, root=None, **kw):
    adapter = adapter or FakeAdapter()
    router, llm = scripted_router(replies)
    agent = Agent(adapter, router, CFG, 200, project_root=root, **kw)
    return agent, adapter, llm


def test_agent_action_list_tables():
    list_action = json.dumps({"action": "list_tables"})
    agent, _, llm = make([list_action, final("SELECT * FROM customers")])

    res = agent.ask("what tables do we have?")
    assert res.status == "ok"
    assert res.model_calls == 2
    # Verify the observation contained tables
    obs = llm.calls[1][-1]["content"]
    assert "Tables in database" in obs
    assert "customers" in obs


def test_agent_action_find_relationships():
    rel_action = json.dumps({"action": "find_relationships", "table": "orders"})
    agent, _, llm = make([rel_action, final("SELECT * FROM orders")])

    res = agent.ask("how do orders join to customers?")
    assert res.status == "ok"
    assert res.model_calls == 2
    obs = llm.calls[1][-1]["content"]
    assert "orders.customer_id -> customers.id" in obs


def test_agent_action_search_workspace(tmp_path: Path):
    doc = tmp_path / "RULES.md"
    doc.write_text("Active customer definition: purchased within 90 days", encoding="utf-8")

    search_action = json.dumps({"action": "search_workspace", "query": "Active customer"})
    agent, _, llm = make([search_action, final("SELECT * FROM customers")], root=tmp_path)

    res = agent.ask("who are active customers?")
    assert res.status == "ok"
    assert res.model_calls == 2
    obs = llm.calls[1][-1]["content"]
    assert "Active customer" in obs
    assert "purchased within 90 days" in obs


def test_agent_action_explain_query():
    explain_action = json.dumps({"action": "explain_query", "sql": "SELECT * FROM orders"})
    agent, _, llm = make([explain_action, final("SELECT id FROM orders")])

    res = agent.ask("check performance of orders query")
    assert res.status == "ok"
    assert res.model_calls == 2
    obs = llm.calls[1][-1]["content"]
    assert "Plan OK" in obs


def test_agent_action_run_query():
    run_action = json.dumps({"action": "run_query", "sql": "SELECT COUNT(*) FROM customers"})
    agent, _, llm = make([run_action, final("SELECT COUNT(*) FROM customers")])

    res = agent.ask("preview customer count")
    assert res.status == "ok"
    assert res.model_calls == 2
    obs = llm.calls[1][-1]["content"]
    assert "Executed in" in obs
