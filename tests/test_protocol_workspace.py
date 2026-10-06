import pytest

from querymind import workspace
from querymind.agent.protocol import ProtocolError, parse_action


def test_parse_plain_json():
    assert parse_action('{"action":"final","sql":"SELECT 1"}')["sql"] == "SELECT 1"


def test_parse_strips_fences_and_prose():
    text = 'Sure!\n```json\n{"action":"describe_tables","tables":["a"]}\n```\nhope that helps'
    assert parse_action(text)["tables"] == ["a"]


def test_parse_handles_braces_inside_strings_and_raw_newlines():
    text = '{"action":"final","sql":"SELECT \'}\' AS x\nFROM t","explanation":"a {b} c"}'
    assert "FROM t" in parse_action(text)["sql"]


@pytest.mark.parametrize("bad", ["no json here", '{"action":"hack"}', '{"action":"final"}',
                                 '{"action": "final", "sql": 5}', '{"action":"final","sql":'])
def test_parse_rejects(bad):
    with pytest.raises(ProtocolError):
        parse_action(bad)


def test_discover_db_from_env_url(tmp_path):
    (tmp_path / ".env").write_text("DATABASE_URL=mysql://app:pw@10.0.0.5:3311/store\n")
    h = workspace.discover_db(tmp_path)[0]
    assert (h.user, h.host, h.port, h.name, h.has_password) == ("app", "10.0.0.5", 3311, "store", True)


def test_discover_db_ignores_postgres_urls(tmp_path):
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://a:b@h/db\n")
    assert workspace.discover_db(tmp_path) == []


def test_discover_db_from_split_env_vars(tmp_path):
    (tmp_path / ".env").write_text("DB_HOST=localhost\nDB_USER=u\nDB_NAME=n\nDB_PORT=3309\nDB_PASSWORD=x\n")
    h = workspace.discover_db(tmp_path)[0]
    assert (h.user, h.name, h.port) == ("u", "n", 3309)


def test_discover_db_from_compose(tmp_path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  db:\n    image: mysql:8\n    environment:\n      MYSQL_DATABASE: shop\n"
        "      MYSQL_USER: shopper\n      MYSQL_PASSWORD: pw\n    ports:\n      - '3308:3306'\n"
        "  web:\n    image: nginx\n")
    h = workspace.discover_db(tmp_path)[0]
    assert (h.user, h.name, h.port) == ("shopper", "shop", 3308)


def test_notes_loading(tmp_path):
    assert workspace.load_notes(tmp_path) == ""
    (tmp_path / "QUERYMIND.md").write_text("active = 90 days")
    assert "90 days" in workspace.load_notes(tmp_path)


def test_gitignore_check(tmp_path):
    assert workspace.gitignore_covers_env(tmp_path) is None
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text("X=1")
    assert workspace.gitignore_covers_env(tmp_path) is False
    (tmp_path / ".gitignore").write_text("node_modules\n.env\n")
    assert workspace.gitignore_covers_env(tmp_path) is True
