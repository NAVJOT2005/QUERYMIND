"""Session history and saved queries management."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from querymind.paths import find_project_root, user_config_path


@dataclass
class HistoryEntry:
    timestamp: float
    question: str
    sql: str
    status: str
    database: str
    elapsed_s: float


def _history_file(project: bool = True) -> Path:
    if project:
        return find_project_root() / ".querymind" / "history.jsonl"
    return user_config_path().parent / "history.jsonl"


def _saved_file(project: bool = True) -> Path:
    if project:
        return find_project_root() / ".querymind" / "saved_queries.json"
    return user_config_path().parent / "saved_queries.json"


def record_history(
    question: str,
    sql: str,
    status: str,
    database: str = "",
    elapsed_s: float = 0.0,
    project: bool = True,
) -> None:
    """Record an executed query in history."""
    entry = HistoryEntry(
        timestamp=time.time(),
        question=question.strip(),
        sql=sql.strip(),
        status=status,
        database=database,
        elapsed_s=elapsed_s,
    )
    p = _history_file(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry)) + "\n")


def load_history(limit: int = 20, project: bool = True) -> list[dict[str, Any]]:
    """Load the most recent query history entries."""
    p = _history_file(project)
    if not p.is_file():
        # Fall back to user history if project history is empty
        p = _history_file(False)
        if not p.is_file():
            return []

    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    entries: list[dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
            if len(entries) >= limit:
                break
        except json.JSONDecodeError:
            continue
    return entries


def save_query(name: str, sql: str, description: str = "", project: bool = True) -> Path:
    """Save a query under a memorable name."""
    p = _saved_file(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    data[name] = {
        "sql": sql.strip(),
        "description": description.strip(),
        "saved_at": time.time(),
    }
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


def list_saved_queries(project: bool = True) -> dict[str, dict[str, Any]]:
    """List all saved queries."""
    p = _saved_file(project)
    if not p.is_file():
        p = _saved_file(False)
        if not p.is_file():
            return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_saved_query(name: str, project: bool = True) -> dict[str, Any] | None:
    queries = list_saved_queries(project)
    return queries.get(name)


def delete_saved_query(name: str, project: bool = True) -> bool:
    p = _saved_file(project)
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if name in data:
            del data[name]
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return True
    except Exception:
        pass
    return False
