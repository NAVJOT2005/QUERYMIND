"""Model Context Protocol (MCP) server for QueryMind CLI.

Allows Claude Code, Cursor, Windsurf, or Antigravity to interact with QueryMind via stdio.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from querymind import __version__
from querymind.adapters.mysql import MySQLAdapter
from querymind.agent.agent import Agent
from querymind.config import load_settings
from querymind.llm import build_router
from querymind.safety import validate
from querymind.workspace import load_notes


def _build_agent():
    settings = load_settings()
    adapter = MySQLAdapter(settings.db_config(), timeout_s=int(settings.db["timeout_s"]))
    adapter.connect()
    router = build_router(settings)
    agent = Agent(
        adapter,
        router,
        settings.agent,
        int(settings.db["max_rows"]),
        notes=load_notes(settings.root),
    )
    return agent, adapter


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "querymind_ask",
            "description": "Translate a natural language question into verified, executed MySQL query with explanations and results.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "What you want to know from the database in plain English.",
                    },
                },
                "required": ["question"],
            },
        },
        {
            "name": "querymind_run",
            "description": "Run a read-only SELECT query safely through QueryMind's validation, planning, and row limits.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "The SELECT query to execute.",
                    },
                },
                "required": ["sql"],
            },
        },
        {
            "name": "querymind_schema",
            "description": "Inspect the live database schema (tables, columns, types, foreign keys, comments).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "Optional specific table name to inspect. If omitted, returns all tables overview.",
                    },
                },
            },
        },
        {
            "name": "querymind_explain",
            "description": "Explain a SQL query: inspect execution plan, check for table scans, and get index suggestions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "The SQL query to explain.",
                    },
                },
                "required": ["sql"],
            },
        },
    ]


def handle_tool_call(name: str, args: dict[str, Any]) -> str:
    if name == "querymind_ask":
        question = args.get("question", "")
        agent, adapter = _build_agent()
        try:
            res = agent.ask(question)
            if res.status != "ok":
                return f"Agent failed: {res.error}"
            output = [
                f"SQL: {res.sql}",
                f"Explanation: {res.explanation}",
            ]
            if res.assumptions:
                output.append(f"Assumptions: {'; '.join(res.assumptions)}")
            if res.result:
                output.append(f"Columns: {', '.join(res.result.columns)}")
                output.append(f"Rows ({len(res.result.rows)}):")
                for r in res.result.rows[:20]:
                    output.append(f"  {r}")
                if len(res.result.rows) > 20:
                    output.append(f"  ... +{len(res.result.rows) - 20} more rows")
            if res.plan_warnings:
                output.append(f"Warnings: {'; '.join(res.plan_warnings)}")
            if res.index_suggestions:
                output.append(f"Suggested indexes:\n" + "\n".join(f"  {idx}" for idx in res.index_suggestions))
            return "\n".join(output)
        finally:
            adapter.close()

    elif name == "querymind_run":
        sql = args.get("sql", "")
        settings = load_settings()
        adapter = MySQLAdapter(settings.db_config(), timeout_s=int(settings.db["timeout_s"]))
        with adapter:
            schema = adapter.introspect()
            checked = validate(
                sql, set(schema.table_names), schema.database, adapter.dialect, int(settings.db["max_rows"])
            )
            plan = adapter.explain(checked.sql)
            if not plan.ok:
                return f"Planning error: {plan.error}"
            res = adapter.execute_readonly(checked.sql, int(settings.db["max_rows"]))
            lines = [
                f"Validated SQL: {checked.sql}",
                f"Columns: {', '.join(res.columns)}",
                f"Rows ({len(res.rows)}, took {res.elapsed_s:.3f}s):",
            ]
            for r in res.rows[:20]:
                lines.append(f"  {r}")
            return "\n".join(lines)

    elif name == "querymind_schema":
        table = args.get("table")
        settings = load_settings()
        adapter = MySQLAdapter(settings.db_config())
        with adapter:
            schema = adapter.introspect()
            if table:
                t = schema.get(table)
                if not t:
                    return f"Table '{table}' not found in database '{schema.database}'."
                return schema.describe([t.name])
            return schema.describe(schema.table_names)

    elif name == "querymind_explain":
        sql = args.get("sql", "")
        settings = load_settings()
        adapter = MySQLAdapter(settings.db_config())
        with adapter:
            schema = adapter.introspect()
            checked = validate(
                sql, set(schema.table_names), schema.database, adapter.dialect, int(settings.db["max_rows"])
            )
            plan = adapter.explain(checked.sql)
            lines = [
                f"Query: {checked.sql}",
                f"Plan valid: {plan.ok}",
            ]
            if plan.error:
                lines.append(f"Error: {plan.error}")
            if plan.warnings:
                lines.append(f"Warnings: {'; '.join(plan.warnings)}")
            if plan.index_suggestions:
                lines.append("Suggested indexes:\n" + "\n".join(f"  {s}" for s in plan.index_suggestions))
            return "\n".join(lines)

    raise ValueError(f"Unknown tool: {name}")


def run_mcp_server():
    """Run standard stdio MCP JSON-RPC server loop."""
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "querymind",
                        "version": __version__,
                    },
                },
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "notifications/initialized":
            pass

        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": _tool_definitions()},
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            try:
                output_text = handle_tool_call(tool_name, tool_args)
                res = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": output_text}],
                        "isError": False,
                    },
                }
            except Exception as exc:
                res = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {exc}"}],
                        "isError": True,
                    },
                }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif msg_id is not None:
            # Method not found
            res = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_mcp_server()
