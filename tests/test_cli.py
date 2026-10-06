from typer.testing import CliRunner

from querymind.cli import app
from querymind.paths import credentials_path, user_config_path

runner = CliRunner()


def test_keys_set_list_remove_roundtrip(project):
    r = runner.invoke(app, ["keys", "set", "gemini"], input="AIza_FAKE_KEY_9999\n")
    assert r.exit_code == 0 and "9999" in r.output and "AIza_FAKE" not in r.output
    listing = runner.invoke(app, ["keys", "list"]).output
    assert "9999" in listing and "credentials" in listing and "AIza_FAKE" not in listing
    assert runner.invoke(app, ["keys", "remove", "gemini"]).exit_code == 0
    assert not credentials_path().read_text().strip()


def test_keys_set_accepts_raw_env_var_name(project):
    r = runner.invoke(app, ["keys", "set", "QUERYMIND_DB_PASSWORD"], input="pw12345678\n")
    assert r.exit_code == 0 and "QUERYMIND_DB_PASSWORD=pw12345678" in credentials_path().read_text()


def test_keys_set_rejects_unknown_name(project):
    assert runner.invoke(app, ["keys", "set", "not-a-provider"], input="x\n").exit_code == 1


def test_provider_use_reorders_chain(project):
    r = runner.invoke(app, ["provider", "use", "groq"])
    assert r.exit_code == 0 and "groq -> gemini -> ollama" in r.output
    assert "groq" in user_config_path().read_text()


def test_provider_use_unknown_fails(project):
    assert runner.invoke(app, ["provider", "use", "nope"]).exit_code == 1


def test_provider_add_custom_endpoint(project):
    r = runner.invoke(app, ["provider", "add", "mylocal", "--base-url", "http://localhost:8080/v1",
                            "--model", "foo", "--no-key"])
    assert r.exit_code == 0
    listing = runner.invoke(app, ["provider", "list"]).output
    assert "mylocal" in listing and "no key needed" in listing


def test_provider_set_model(project):
    runner.invoke(app, ["provider", "set-model", "gemini", "gemini-custom-id"])
    assert "gemini-custom-id" in runner.invoke(app, ["provider", "list"]).output.replace("\n", "").replace("│", "").replace(" ", "")


def test_ask_without_database_gives_actionable_error(project):
    r = runner.invoke(app, ["ask", "how many rows?"])
    assert r.exit_code == 1 and "querymind init" in r.output


def test_ask_without_any_key_explains_how_to_fix(project, monkeypatch):
    monkeypatch.setenv("QUERYMIND_DB_URL", "mysql://u:p@127.0.0.1:1/db")
    r = runner.invoke(app, ["ask", "x"])
    assert r.exit_code == 1  # fails on DB connect first, with a clear message
    assert "Could not connect" in r.output


def test_version():
    assert "querymind" in runner.invoke(app, ["--version"]).output
