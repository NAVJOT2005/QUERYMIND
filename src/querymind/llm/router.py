"""Fallback router. Tries providers in chain order; on rate limits, bad keys, or outages
it moves to the next one so a free-tier limit never surfaces as a user-facing failure."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from querymind.config import Settings, usable_chain
from querymind.errors import AllProvidersFailed, LLMError, NoProvidersConfigured
from querymind.llm import openai_compat

Messages = list[dict]


@dataclass
class ProviderHandle:
    name: str
    model: str
    call: Callable[[Messages], str]


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    latency_s: float


class LLMRouter:
    def __init__(self, handles: list[ProviderHandle],
                 on_event: Callable[[str], None] | None = None):
        if not handles:
            raise NoProvidersConfigured(
                "No usable LLM provider. Add a key with `querymind keys set gemini` "
                "(free: aistudio.google.com) or run `querymind provider list` to see status."
            )
        self.handles = handles
        self.on_event = on_event or (lambda _msg: None)
        self._preferred = 0  # sticky: stay on a working provider between calls

    def complete(self, messages: Messages) -> Completion:
        failures: list[tuple[str, str]] = []
        order = self.handles[self._preferred:] + self.handles[:self._preferred]
        for handle in order:
            started = time.perf_counter()
            try:
                text = handle.call(messages)
            except LLMError as exc:
                failures.append((handle.name, str(exc)))
                self.on_event(f"{handle.name} failed ({exc}); trying next provider")
                continue
            self._preferred = self.handles.index(handle)
            return Completion(text, handle.name, handle.model, time.perf_counter() - started)
        raise AllProvidersFailed(failures)


def build_router(settings: Settings, on_event: Callable[[str], None] | None = None) -> LLMRouter:
    llm = settings.llm
    handles = []
    for prov in usable_chain(settings):
        client = openai_compat.make_client(prov.base_url, prov.api_key, llm["timeout"])

        def call(messages, _c=client, _m=prov.model):
            return openai_compat.complete(_c, _m, messages, llm["temperature"], llm["max_tokens"])

        handles.append(ProviderHandle(prov.name, prov.model, call))
    return LLMRouter(handles, on_event)
