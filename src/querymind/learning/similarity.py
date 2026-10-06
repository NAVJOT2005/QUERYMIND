"""Similarity search for retrieving few-shot query examples from previous user queries and feedback."""

from __future__ import annotations

import re
from typing import Any

# Standard English stop words to filter out for keyword matching
_STOP_WORDS = {
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do",
    "does", "did", "and", "or", "but", "if", "then", "else", "when", "where", "why",
    "how", "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "can", "will", "just", "should", "now", "me", "my", "our", "we", "us", "show",
    "get", "find", "list", "give", "what", "which", "who", "whom", "this", "that",
}


def tokenize(text: str) -> set[str]:
    """Tokenize text into lowercase alphanumeric words, filtering stopwords."""
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return {w for w in words if len(w) > 1 and w not in _STOP_WORDS}


def jaccard_similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    """Compute Jaccard similarity between two sets of tokens."""
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union > 0 else 0.0


def rank_similar_examples(
    query: str,
    examples: list[dict[str, Any]],
    top_k: int = 3,
    min_score: float = 0.15,
) -> list[dict[str, Any]]:
    """Rank feedback examples by textual similarity to the user's query."""
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for ex in examples:
        target_tokens = tokenize(ex.get("question", ""))
        # Also include any table or column tags if available
        tags = set(ex.get("tags", []))
        combined_target = target_tokens | {t.lower() for t in tags}

        score = jaccard_similarity(query_tokens, combined_target)
        if score >= min_score:
            scored.append((score, ex))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [ex for _, ex in scored[:top_k]]
