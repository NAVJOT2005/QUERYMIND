"""The model talks to the agent in single JSON objects. This works with every provider
(no dependence on native function-calling support, which varies across free models)."""

from __future__ import annotations

import json
import re
from typing import Any

from querymind.errors import QueryMindError

ACTIONS = {
    "list_tables",
    "describe_table",
    "describe_tables",
    "sample_rows",
    "distinct_values",
    "find_relationships",
    "search_workspace",
    "explain_query",
    "run_query",
    "clarify",
    "final",
}



class ProtocolError(QueryMindError):
    pass


def _first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_action(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    blob = _first_json_object(cleaned)
    if blob is None:
        raise ProtocolError("No JSON object found in your reply.")
    try:
        data = json.loads(blob, strict=False)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"Invalid JSON: {exc.msg}.") from exc
    if not isinstance(data, dict) or data.get("action") not in ACTIONS:
        raise ProtocolError(f"'action' must be one of {sorted(ACTIONS)}.")
    if data["action"] == "final" and not isinstance(data.get("sql"), str):
        raise ProtocolError("A 'final' action needs a string field 'sql'.")
    return data
