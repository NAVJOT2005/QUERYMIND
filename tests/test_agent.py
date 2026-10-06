import json

from querymind.agent.agent import Agent
from tests.fakes import FakeAdapter, final, scripted_router, shop_schema

CFG = {"max_steps": 8, "max_repairs": 2, "privacy": "schema_only", "schema_budget_chars": 8000}


def make(replies, adapter=None, cfg=None, **kw):
    adapter = adapter or FakeAdapter()
    router, llm = scripted_router(replies)
    agent = Agent(adapter, router, {**CFG, **(cfg or {})}, 200, **kw)
    return agent, adapter, llm


def test_happy_path_single_call():
    agent, adapter, llm = make([final("SELECT COUNT(*) FROM customers", "counts customers", ["all time"])])
    res = agent.ask("how many customers?")
    assert res.status == "ok" and res.model_calls == 1 and res.repairs == 0
    assert " ".join(res.sql.split()) == "SELECT COUNT(*) FROM customers LIMIT 200"
    assert res.assumptions == ["all time"] and adapter.executed == [res.sql]
    # whole schema was in the first prompt, so no exploration needed
    prompt = llm.calls[0][1]["content"]
    assert "customers" in prompt and "-> customers.id" in prompt and "ISO code" in prompt


def test_describe_then_final_for_large_schemas():
    schema = shop_schema(extra_tables=40)
    describe = json.dumps({"action": "describe_tables", "tables": ["orders", "customers"]})
    agent, adapter, llm = make([describe, final("SELECT * FROM orders")], FakeAdapter(schema),
                               {"schema_budget_chars": 500})
    res = agent.ask("list orders")
    assert res.status == "ok" and res.model_calls == 2
    assert "overview only" in llm.calls[0][1]["content"]
    assert "enum('paid','cancelled')" in llm.calls[1][-1]["content"]


def test_validator_rejection_triggers_repair():
    agent, adapter, llm = make([final("SELECT * FROM ghosts"), final("SELECT * FROM orders")])
    res = agent.ask("show orders")
    assert res.status == "ok" and res.repairs == 1 and res.model_calls == 2
    assert "Unknown table" in llm.calls[1][-1]["content"]
    assert len(adapter.executed) == 1  # the rejected query never reached the database


def test_explain_error_feeds_back_exact_db_message():
    adapter = FakeAdapter()
    adapter.plan_errors["bad_col"] = "MySQL error 1054: Unknown column 'bad_col' in 'SELECT'"
    agent, _, llm = make([final("SELECT bad_col FROM orders"), final("SELECT id FROM orders")], adapter)
    res = agent.ask("ids")
    assert res.status == "ok" and res.repairs == 1
    assert "Unknown column 'bad_col'" in llm.calls[1][-1]["content"]


def test_execution_error_is_repaired():
    adapter = FakeAdapter()
    adapter.exec_errors["boom"] = "MySQL error 1064: syntax"
    agent, _, _ = make([final("SELECT 'boom' FROM orders"), final("SELECT 1 FROM orders")], adapter)
    assert agent.ask("x").repairs == 1


def test_gives_up_after_max_repairs_with_last_error():
    agent, adapter, _ = make([final("SELECT * FROM a1"), final("SELECT * FROM a2"), final("SELECT * FROM a3")])
    res = agent.ask("x")
    assert res.status == "failed" and "Unknown table" in res.error and adapter.executed == []


def test_destructive_sql_never_reaches_database():
    agent, adapter, _ = make([final("DROP TABLE orders"), final("DELETE FROM orders"),
                              final("UPDATE orders SET status='paid'")])
    res = agent.ask("clean up")
    assert res.status == "failed" and adapter.executed == []


def test_recovers_from_invalid_json():
    agent, _, llm = make(["I think the answer is SELECT 1", final("SELECT 1 FROM orders")])
    res = agent.ask("x")
    assert res.status == "ok" and res.model_calls == 2
    assert "JSON" in llm.calls[1][-1]["content"]


def test_clarify_without_callback_returns_question():
    clarify = json.dumps({"action": "clarify", "question": "Top by what?", "options": ["revenue", "orders"]})
    agent, _, _ = make([clarify])
    res = agent.ask("top customers")
    assert res.status == "clarify" and res.clarification == ("Top by what?", ["revenue", "orders"])


def test_clarify_with_callback_continues():
    clarify = json.dumps({"action": "clarify", "question": "Top by what?", "options": ["revenue"]})
    agent, _, llm = make([clarify, final("SELECT 1 FROM orders")], on_clarify=lambda q, o: o[0])
    res = agent.ask("top customers")
    assert res.status == "ok" and "The user answered: revenue" in llm.calls[1][-1]["content"]


def test_empty_result_nudges_once_then_accepts_fix():
    adapter = FakeAdapter()
    adapter.rows_for["'canceled'"] = []
    replies = [final("SELECT * FROM orders WHERE status = 'canceled'"),
               final("SELECT * FROM orders WHERE status = 'cancelled'")]
    agent, _, llm = make(replies, adapter)
    res = agent.ask("cancelled orders")
    assert res.status == "ok" and res.result.rows == [[1]] and res.model_calls == 2
    assert "0 rows" in llm.calls[1][-1]["content"]


def test_empty_result_accepted_if_model_confirms():
    adapter = FakeAdapter()
    adapter.rows_for["WHERE"] = []
    same = final("SELECT * FROM orders WHERE status = 'paid'")
    agent, _, _ = make([same, same], adapter)
    res = agent.ask("x")
    assert res.status == "ok" and res.result.rows == [] and res.model_calls == 2


def test_empty_result_without_filters_is_not_nudged():
    adapter = FakeAdapter()
    adapter.rows_for["orders"] = []
    agent, _, _ = make([final("SELECT * FROM orders")], adapter)
    assert agent.ask("x").model_calls == 1


def test_schema_only_mode_refuses_value_lookups():
    ask_values = json.dumps({"action": "distinct_values", "table": "orders", "column": "status"})
    agent, adapter, llm = make([ask_values, final("SELECT 1 FROM orders")])
    agent.ask("x")
    assert adapter.sampled == []
    assert "disabled" in llm.calls[1][-1]["content"]
    assert "distinct_values" not in llm.calls[0][0]["content"]  # not even offered


def test_samples_mode_allows_lookup_and_masks_pii():
    sample = json.dumps({"action": "sample_rows", "table": "customers"})
    agent, adapter, llm = make([sample, final("SELECT 1 FROM orders")], cfg={"privacy": "samples"})
    agent.ask("x")
    assert adapter.sampled == [("customers", 5)]
    observation = llm.calls[1][-1]["content"]
    assert "jane@example.com" not in observation and "***@example.com" in observation


def test_history_is_included_for_followups():
    agent, _, llm = make([final("SELECT 1 FROM orders")])
    agent.ask("now only 2024", history=[("orders per month", "SELECT 1 FROM orders")])
    prompt = llm.calls[0][1]["content"]
    assert "orders per month" in prompt and "now only 2024" in prompt


def test_project_notes_reach_the_prompt_and_are_capped():
    agent, _, llm = make([final("SELECT 1 FROM orders")], notes="active customer = ordered in 90 days" + "x" * 9000)
    agent.ask("active customers")
    prompt = llm.calls[0][1]["content"]
    assert "active customer = ordered in 90 days" in prompt and len(prompt) < 7000


def test_step_limit_prevents_runaway_loops():
    describe = json.dumps({"action": "describe_tables", "tables": ["orders"]})
    agent, _, _ = make([describe] * 20, cfg={"max_steps": 4})
    res = agent.ask("x")
    assert res.status == "failed" and res.model_calls == 4
