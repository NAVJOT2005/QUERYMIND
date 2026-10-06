"""Layered configuration.

Precedence (low -> high):
    built-in defaults  <  ~/.config/querymind/config.toml  <  <project>/.querymind/config.toml
    <  QUERYMIND_* env vars  <  CLI flags

Config files contain NO secrets. A provider only names the *environment variable* that
holds its key (api_key_env); the value is resolved at runtime by querymind.secrets.
That is what makes the project-level config safe to commit and keys trivial to swap.
"""

from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import tomli_w

from querymind import secrets
from querymind.errors import ConfigError
from querymind.paths import find_project_root, project_config_path, user_config_path

# ---------------------------------------------------------------------------
# Provider presets. Anything OpenAI-compatible works; add your own with
# `querymind provider add`. Model ids change often: run `querymind provider models <name>`
# to list the live ids for your key, then `querymind provider set-model <name> <id>`.
# ---------------------------------------------------------------------------
PRESETS: dict[str, dict[str, Any]] = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.5-flash",
        "api_key_env": "GEMINI_API_KEY",
        "requires_key": True,
        "note": "Google AI Studio free tier (Flash models). Free-tier data may be used by Google.",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
        "requires_key": True,
        "note": "Very fast free tier; per-model limits and token caps.",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "",
        "api_key_env": "OPENROUTER_API_KEY",
        "requires_key": True,
        "note": "Pick a ':free' model with `provider models openrouter`.",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-small-latest",
        "api_key_env": "MISTRAL_API_KEY",
        "requires_key": True,
        "note": "Free 'Experiment' tier, low request rate.",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5-coder:7b",
        "api_key_env": "OLLAMA_API_KEY",
        "requires_key": False,
        "note": "Local and private. Quality depends on your hardware.",
    },
}

DEFAULTS: dict[str, Any] = {
    "llm": {
        "chain": ["gemini", "groq", "ollama"],
        "temperature": 0.0,
        "timeout": 60,
        "max_tokens": 1500,
        "providers": {},
    },
    "database": {
        "url": "",
        "host": "127.0.0.1",
        "port": 3306,
        "user": "",
        "name": "",
        "password_env": "QUERYMIND_DB_PASSWORD",
        "max_rows": 200,
        "timeout_s": 10,
    },
    "agent": {
        "max_steps": 8,
        "max_repairs": 2,
        "privacy": "schema_only",  # or "samples"
        "schema_budget_chars": 8000,
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc


def write_toml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data))


def update_toml(path: Path, mutate) -> None:
    """Read a TOML file, apply mutate(dict), write back. Creates the file if needed."""
    data = _read_toml(path)
    mutate(data)
    write_toml(path, data)


@dataclass
class ResolvedProvider:
    name: str
    base_url: str
    model: str
    api_key_env: str
    requires_key: bool
    api_key: str | None = None
    key_source: str | None = None
    note: str = ""

    @property
    def usable(self) -> bool:
        if not self.base_url or not self.model:
            return False
        return bool(self.api_key) or not self.requires_key


@dataclass
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass
class Settings:
    raw: dict
    root: Path
    providers: dict[str, ResolvedProvider] = field(default_factory=dict)

    # -- convenience accessors ---------------------------------------------
    @property
    def chain(self) -> list[str]:
        return list(self.raw["llm"]["chain"])

    @property
    def agent(self) -> dict:
        return self.raw["agent"]

    @property
    def llm(self) -> dict:
        return self.raw["llm"]

    @property
    def db(self) -> dict:
        return self.raw["database"]

    def db_config(self) -> DBConfig:
        return resolve_db_config(self)


def _resolve_providers(raw: dict, root: Path) -> dict[str, ResolvedProvider]:
    resolved: dict[str, ResolvedProvider] = {}
    names = set(PRESETS) | set(raw["llm"].get("providers", {}))
    for name in names:
        spec = {**PRESETS.get(name, {}), **raw["llm"].get("providers", {}).get(name, {})}
        key_env = spec.get("api_key_env") or f"{name.upper().replace('-', '_')}_API_KEY"
        value, source = secrets.lookup(key_env, root)
        resolved[name] = ResolvedProvider(
            name=name,
            base_url=spec.get("base_url", ""),
            model=spec.get("model", ""),
            api_key_env=key_env,
            requires_key=spec.get("requires_key", True),
            api_key=value,
            key_source=source,
            note=spec.get("note", ""),
        )
    return resolved


def load_settings(
    cwd: Path | None = None,
    provider: str | None = None,
    model: str | None = None,
    privacy: str | None = None,
    max_rows: int | None = None,
) -> Settings:
    root = find_project_root(cwd)
    raw = deep_merge(DEFAULTS, _read_toml(user_config_path()))
    raw = deep_merge(raw, _read_toml(project_config_path(root)))

    # env overrides
    if os.environ.get("QUERYMIND_PROVIDER"):
        raw["llm"]["chain"] = [os.environ["QUERYMIND_PROVIDER"]]
    if os.environ.get("QUERYMIND_PRIVACY"):
        raw["agent"]["privacy"] = os.environ["QUERYMIND_PRIVACY"]

    # CLI flag overrides
    if provider:
        raw["llm"]["chain"] = [provider]
        if model:
            raw["llm"].setdefault("providers", {}).setdefault(provider, {})["model"] = model
    if privacy:
        raw["agent"]["privacy"] = privacy
    if max_rows:
        raw["database"]["max_rows"] = max_rows

    if raw["agent"]["privacy"] not in ("schema_only", "samples"):
        raise ConfigError("agent.privacy must be 'schema_only' or 'samples'.")

    settings = Settings(raw=raw, root=root)
    settings.providers = _resolve_providers(raw, root)
    return settings


def usable_chain(settings: Settings) -> list[ResolvedProvider]:
    """Providers in chain order that have a model and (if required) a key."""
    out = []
    for name in settings.chain:
        prov = settings.providers.get(name)
        if prov is None:
            raise ConfigError(
                f"Provider '{name}' is in your chain but not defined. "
                f"Add it with: querymind provider add {name} --base-url ... --model ..."
            )
        if prov.usable:
            out.append(prov)
    return out


def resolve_db_config(settings: Settings) -> DBConfig:
    root = settings.root
    url, _ = secrets.lookup("QUERYMIND_DB_URL", root)
    url = url or settings.db.get("url") or ""
    if url:
        parsed = urlparse(url)
        if parsed.scheme.split("+")[0] not in ("mysql", "mariadb"):
            raise ConfigError(f"Unsupported database URL scheme '{parsed.scheme}' (MySQL only for now).")
        return DBConfig(
            host=parsed.hostname or "127.0.0.1",
            port=parsed.port or 3306,
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            database=(parsed.path or "/").lstrip("/"),
        )
    db = settings.db
    if not db.get("user") or not db.get("name"):
        raise ConfigError(
            "No database configured. Run `querymind init`, or set QUERYMIND_DB_URL "
            "(e.g. mysql://user:pass@127.0.0.1:3306/mydb)."
        )
    password, _ = secrets.lookup(db.get("password_env", "QUERYMIND_DB_PASSWORD"), root)
    return DBConfig(
        host=db["host"], port=int(db["port"]), user=db["user"],
        password=password or "", database=db["name"],
    )
