"""Workspace awareness: what the project itself can tell us.

  * QUERYMIND.md          business rules the user writes once ("active customer = ...")
  * .env / docker-compose connection hints, offered during `querymind init`

Discovered passwords are never written to config; `init` only records host/port/user/db.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml
from dotenv import dotenv_values

NOTES_TEMPLATE = """# QueryMind project notes

Write business rules here. QueryMind reads this file before every question, so it
stops guessing at definitions that are not visible in the schema.

## Definitions
- (example) "active customer" = placed an order in the last 90 days
- (example) "revenue" = SUM(order_items.quantity * order_items.unit_price), excluding cancelled orders

## Conventions
- (example) Money columns are stored in USD
"""


def load_notes(root: Path) -> str:
    for candidate in (root / "QUERYMIND.md", root / ".querymind" / "notes.md"):
        if candidate.is_file():
            return candidate.read_text(errors="replace")
    return ""


@dataclass
class DBHint:
    source: str
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = ""
    name: str = ""
    has_password: bool = False


def _from_url(url: str, source: str) -> DBHint | None:
    parsed = urlparse(url)
    if parsed.scheme.split("+")[0] not in ("mysql", "mariadb"):
        return None
    return DBHint(source, parsed.hostname or "127.0.0.1", parsed.port or 3306,
                  unquote(parsed.username or ""), (parsed.path or "").lstrip("/"),
                  bool(parsed.password))


def _from_env_file(path: Path) -> DBHint | None:
    env = dotenv_values(path)
    for key in ("QUERYMIND_DB_URL", "DATABASE_URL", "DB_URL", "MYSQL_URL"):
        if env.get(key):
            hint = _from_url(env[key], f"{path.name}:{key}")
            if hint:
                return hint
    pick = lambda *keys: next((env[k] for k in keys if env.get(k)), "")
    user = pick("DB_USER", "DB_USERNAME", "MYSQL_USER")
    name = pick("DB_NAME", "DB_DATABASE", "MYSQL_DATABASE")
    if user and name:
        port = pick("DB_PORT", "MYSQL_PORT")
        return DBHint(f"{path.name}", pick("DB_HOST", "MYSQL_HOST") or "127.0.0.1",
                      int(port) if port.isdigit() else 3306, user, name,
                      bool(pick("DB_PASSWORD", "MYSQL_PASSWORD")))
    return None


def _from_compose(path: Path) -> DBHint | None:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (yaml.YAMLError, OSError):
        return None
    for svc_name, svc in (data.get("services") or {}).items():
        image = str(svc.get("image", "")).lower()
        if not any(x in image for x in ("mysql", "mariadb")):
            continue
        env = svc.get("environment") or {}
        if isinstance(env, list):
            env = dict(item.split("=", 1) for item in env if "=" in item)
        port = 3306
        for mapping in svc.get("ports") or []:
            host_port, _, container = str(mapping).rpartition(":")
            if container.split("/")[0] == "3306" and host_port.split(":")[-1].isdigit():
                port = int(host_port.split(":")[-1])
        user = str(env.get("MYSQL_USER") or "root")
        return DBHint(f"{path.name}:{svc_name}", "127.0.0.1", port, user,
                      str(env.get("MYSQL_DATABASE") or ""),
                      bool(env.get("MYSQL_PASSWORD") or env.get("MYSQL_ROOT_PASSWORD")))
    return None


def discover_db(root: Path) -> list[DBHint]:
    hints: list[DBHint] = []
    for env_name in (".env", ".env.local", ".env.development"):
        if (root / env_name).is_file():
            hint = _from_env_file(root / env_name)
            if hint:
                hints.append(hint)
    for compose in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        if (root / compose).is_file():
            hint = _from_compose(root / compose)
            if hint:
                hints.append(hint)
    return hints


def gitignore_covers_env(root: Path) -> bool | None:
    """True/False if we are in a git repo with a .env file; None when not applicable."""
    if not (root / ".env").is_file() or not (root / ".git").exists():
        return None
    gi = root / ".gitignore"
    if not gi.is_file():
        return False
    patterns = {ln.strip() for ln in gi.read_text().splitlines()}
    return bool(patterns & {".env", ".env*", "*.env", ".env.*", "/.env"})


# Re-export scanner and search functions from workspace_scanner
from querymind.workspace_scanner import (
    ModelMetadata,
    find_existing_sql_queries,
    parse_django,
    parse_prisma,
    parse_sqlalchemy,
    propose_memory_addition,
    scan_workspace_models,
    search_workspace,
)

__all__ = [
    "DBHint",
    "ModelMetadata",
    "NOTES_TEMPLATE",
    "discover_db",
    "find_existing_sql_queries",
    "gitignore_covers_env",
    "load_notes",
    "parse_django",
    "parse_prisma",
    "parse_sqlalchemy",
    "propose_memory_addition",
    "scan_workspace_models",
    "search_workspace",
]

