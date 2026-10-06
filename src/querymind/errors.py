"""Exception hierarchy. The CLI catches QueryMindError and prints a friendly message."""


class QueryMindError(Exception):
    """Base class for expected, user-facing errors."""


class ConfigError(QueryMindError):
    pass


class DatabaseError(QueryMindError):
    pass


class ValidationError(QueryMindError):
    """SQL was rejected by the safety layer before reaching the database."""


# --- LLM layer -------------------------------------------------------------
class LLMError(QueryMindError):
    """A single provider failed. The router reacts by trying the next provider."""


class RateLimited(LLMError):
    pass


class AuthError(LLMError):
    pass


class ModelNotFound(LLMError):
    pass


class ProviderUnavailable(LLMError):
    pass


class BadRequest(LLMError):
    pass


class NoProvidersConfigured(QueryMindError):
    pass


class AllProvidersFailed(QueryMindError):
    def __init__(self, failures: list[tuple[str, str]]):
        self.failures = failures
        lines = "\n".join(f"  - {name}: {reason}" for name, reason in failures)
        super().__init__(f"Every configured LLM provider failed:\n{lines}")
