import pytest

PROVIDER_ENV = ["GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY",
                "OLLAMA_API_KEY", "QUERYMIND_DB_URL", "QUERYMIND_DB_PASSWORD",
                "QUERYMIND_PROVIDER", "QUERYMIND_PRIVACY"]


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets its own config dir and a clean environment: no real keys can leak in."""
    cfg = tmp_path / "userconfig"
    monkeypatch.setenv("QUERYMIND_CONFIG_DIR", str(cfg))
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    return cfg


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root
