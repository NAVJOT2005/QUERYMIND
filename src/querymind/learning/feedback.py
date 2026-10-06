"""Feedback store: records verified or user-edited query pairs and retrieves similar ones as few-shot examples."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from querymind.learning.similarity import rank_similar_examples
from querymind.paths import find_project_root


@dataclass
class QueryExample:
    question: str
    sql: str
    database: str = ""
    explanation: str = ""
    timestamp: float = field(default_factory=time.time)
    source: str = "feedback"  # "user_edit" | "feedback" | "saved"
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueryExample:
        return cls(
            question=str(data.get("question", "")),
            sql=str(data.get("sql", "")),
            database=str(data.get("database", "")),
            explanation=str(data.get("explanation", "")),
            timestamp=float(data.get("timestamp", 0.0)),
            source=str(data.get("source", "feedback")),
            tags=list(data.get("tags", [])),
        )


def get_feedback_file(root: Path | None = None) -> Path:
    base = root or find_project_root()
    return base / ".querymind" / "feedback.jsonl"


class FeedbackStore:
    def __init__(self, path: Path | None = None):
        self.path = path or get_feedback_file()

    def record(
        self,
        question: str,
        sql: str,
        database: str = "",
        explanation: str = "",
        source: str = "feedback",
        tags: list[str] | None = None,
    ) -> QueryExample:
        example = QueryExample(
            question=question.strip(),
            sql=sql.strip(),
            database=database.strip(),
            explanation=explanation.strip(),
            source=source,
            tags=tags or [],
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(example.to_dict()) + "\n")
        return example

    def load_all(self, database: str | None = None) -> list[QueryExample]:
        if not self.path.is_file():
            return []
        examples: list[QueryExample] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    ex = QueryExample.from_dict(data)
                    if not database or not ex.database or ex.database.lower() == database.lower():
                        examples.append(ex)
                except (json.JSONDecodeError, KeyError):
                    continue
        return examples

    def find_similar(
        self,
        question: str,
        database: str | None = None,
        top_k: int = 3,
    ) -> list[QueryExample]:
        all_examples = self.load_all(database=database)
        if not all_examples:
            return []
        ranked_dicts = rank_similar_examples(
            question, [ex.to_dict() for ex in all_examples], top_k=top_k
        )
        return [QueryExample.from_dict(d) for d in ranked_dicts]


def format_few_shot_examples(examples: list[QueryExample]) -> str:
    """Format similar examples for prompt injection."""
    if not examples:
        return ""
    lines = ["Similar verified queries previously approved for this project:"]
    for i, ex in enumerate(examples, 1):
        lines.append(f"Example {i}:")
        lines.append(f"User: {ex.question}")
        lines.append(f"SQL: {ex.sql}")
        if ex.explanation:
            lines.append(f"Note: {ex.explanation}")
    return "\n".join(lines)
