"""Masking for the opt-in 'samples' privacy mode: values shown to the LLM are scrubbed of
obvious personal data. In the default 'schema_only' mode no row values are ever sent."""

from __future__ import annotations

import re
from typing import Any

_EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)")
_LONG_DIGITS = re.compile(r"\d[\d\s().-]{6,}\d")


def mask_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = _EMAIL.sub(r"***@\1", value)
    value = _LONG_DIGITS.sub(lambda m: "*" * (len(m.group()) - 2) + m.group()[-2:], value)
    return value if len(value) <= 60 else value[:57] + "..."


def mask_rows(rows: list[list[Any]], limit: int = 5) -> list[list[Any]]:
    return [[mask_value(v) for v in row] for row in rows[:limit]]
