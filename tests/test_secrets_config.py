import os
import stat

import pytest

from querymind import secrets
from querymind.config import load_settings, usable_chain
from querymind.errors import ConfigError
from querymind.paths import credentials_path, project_config_path


def test_mask():
    assert secrets.mask(None) == "(not set)"
    assert secrets.mask("abcdefghijkl") == "...ijkl"
    assert secrets.mask("short") == "****"


def test_lookup_order_env_beats_dotenv_beats_credentials(project, monkeypatch):
    secrets.set_secret("GEMINI_API_KEY", "from-credentials-1111")
    assert secrets.lookup("GEMINI_API_KEY", project) == ("from-credentials-1111", "credentials")
    (project / ".env").write_text("GEMINI_API_KEY=from-dotenv-2222\n")
    assert secrets.lookup("GEMINI_API_KEY", project) == ("from-dotenv-2222", ".env")
    monkeypatch.setenv("GEMINI_API_KEY", "from-env-3333")
    assert secrets.lookup("GEMINI_API_KEY", project) == ("from-env-3333", "env")


def test_lookup_missing(project):
    assert secrets.lookup("NOPE_KEY", project) == (None, None)


def test_set_secret_is_private_and_replaces():
    secrets.set_secret("A_KEY", "one-1111111")
    secrets.set_secret("B_KEY", "two-2222222")
    secrets.set_secret("A_KEY", "uno-3333333")
    text = credentials_path().read_text()
    assert text.count("A_KEY=") == 1 and "uno-3333333" in text and "one-1111111" not in text
    if os.name == "posix":
        assert stat.S_IMODE(credentials_path().stat().st_mode) == 0o600


def test_remove_secret():
    secrets.set_secret("A_KEY", "one-1111111")
    assert secrets.remove_secret("A_KEY") is True
    assert secrets.remove_secret("A_KEY") is False


@pytest.mark.parametrize("bad", ["", "  ", "line1\nline2"])
def test_set_secret_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        secrets.set_secret("A_KEY", bad)


def test_default_chain_skips_providers_without_keys(project):
    s = load_settings()
    assert [p.name for p in usable_chain(s)] == ["ollama"]  # only keyless local provider usable


def test_adding_a_key_activates_provider_in_order(project):
    secrets.set_secret("GROQ_API_KEY", "gsk_test_1234567")
    secrets.set_secret("GEMINI_API_KEY", "AIza_test_1234567")
    assert [p.name for p in usable_chain(load_settings())] == ["gemini", "groq", "ollama"]


def test_cli_flag_forces_single_provider_and_model(project):
    s = load_settings(provider="groq", model="my-model")
    assert s.chain == ["groq"] and s.providers["groq"].model == "my-model"


def test_env_provider_override(project, monkeypatch):
    monkeypatch.setenv("QUERYMIND_PROVIDER", "mistral")
    assert load_settings().chain == ["mistral"]


def test_project_config_overrides_user_config(project):
    from querymind.config import write_toml
    from querymind.paths import user_config_path
    write_toml(user_config_path(), {"llm": {"chain": ["groq"]}, "agent": {"max_repairs": 5}})
    write_toml(project_config_path(project), {"agent": {"max_repairs": 1}})
    s = load_settings()
    assert s.chain == ["groq"] and s.agent["max_repairs"] == 1


def test_unknown_chain_provider_errors(project):
    from querymind.config import write_toml
    write_toml(project_config_path(project), {"llm": {"chain": ["ghost"]}})
    with pytest.raises(ConfigError):
        usable_chain(load_settings())


def test_invalid_privacy_rejected(project):
    with pytest.raises(ConfigError):
        load_settings(privacy="everything")


def test_db_url_parsing(project, monkeypatch):
    monkeypatch.setenv("QUERYMIND_DB_URL", "mysql+pymysql://bob:p%40ss@db.example:3310/app")
    cfg = load_settings().db_config()
    assert (cfg.host, cfg.port, cfg.user, cfg.password, cfg.database) == ("db.example", 3310, "bob", "p@ss", "app")


def test_db_fields_with_password_from_credentials(project):
    from querymind.config import write_toml
    write_toml(project_config_path(project), {"database": {"user": "ro", "name": "shop", "port": 3307}})
    secrets.set_secret("QUERYMIND_DB_PASSWORD", "s3cret")
    cfg = load_settings().db_config()
    assert (cfg.user, cfg.password, cfg.database, cfg.port) == ("ro", "s3cret", "shop", 3307)


def test_missing_db_config_is_actionable(project):
    with pytest.raises(ConfigError, match="querymind init"):
        load_settings().db_config()


def test_non_mysql_url_rejected(project, monkeypatch):
    monkeypatch.setenv("QUERYMIND_DB_URL", "postgresql://u:p@h/db")
    with pytest.raises(ConfigError, match="MySQL only"):
        load_settings().db_config()
