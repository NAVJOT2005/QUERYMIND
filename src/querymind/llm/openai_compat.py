"""One client for every provider: Gemini, Groq, OpenRouter, Mistral, Ollama, or any
OpenAI-compatible endpoint. Switching providers only changes base_url / key / model."""

from __future__ import annotations

import openai

from querymind.errors import (
    AuthError,
    BadRequest,
    ModelNotFound,
    ProviderUnavailable,
    RateLimited,
)


def make_client(base_url: str, api_key: str | None, timeout: float) -> openai.OpenAI:
    # Local servers (Ollama) ignore the key but the SDK requires a non-empty string.
    return openai.OpenAI(base_url=base_url, api_key=api_key or "not-needed", timeout=timeout, max_retries=0)


def _translate(exc: Exception) -> Exception:
    """Map SDK exceptions onto our own so the router never depends on SDK internals."""
    if isinstance(exc, openai.RateLimitError):
        return RateLimited("rate limit or quota reached")
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return AuthError("key rejected (invalid, revoked, or lacks access)")
    if isinstance(exc, openai.NotFoundError):
        return ModelNotFound("model not found (run `querymind provider models <name>`)")
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return ProviderUnavailable("could not connect (network down, or server not running)")
    if isinstance(exc, openai.InternalServerError):
        return ProviderUnavailable("provider-side error (5xx)")
    if isinstance(exc, openai.BadRequestError):
        return BadRequest(f"bad request: {str(exc)[:160]}")
    if isinstance(exc, openai.APIStatusError):
        return ProviderUnavailable(f"HTTP {exc.status_code}")
    return exc


def complete(client: openai.OpenAI, model: str, messages: list[dict],
             temperature: float, max_tokens: int) -> str:
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
        )
    except openai.OpenAIError as exc:
        raise _translate(exc) from exc
    text = resp.choices[0].message.content if resp.choices else None
    if not text:
        raise ProviderUnavailable("empty response from model")
    return text


def list_models(client: openai.OpenAI) -> list[str]:
    try:
        return sorted(m.id for m in client.models.list())
    except openai.OpenAIError as exc:
        raise _translate(exc) from exc
