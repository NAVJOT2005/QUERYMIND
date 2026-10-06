"""Where QueryMind keeps things. Everything secret lives OUTSIDE the project repo."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def user_config_dir() -> Path:
    override = os.environ.get("QUERYMIND_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "querymind"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "querymind"


def user_config_path() -> Path:
    return user_config_dir() / "config.toml"


def credentials_path() -> Path:
    return user_config_dir() / "credentials.env"


def history_path() -> Path:
    return user_config_dir() / "history"


def find_project_root(start: Path | None = None) -> Path:
    """Nearest parent containing .querymind/ or .git; falls back to the start dir."""
    start = (start or Path.cwd()).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / ".querymind").is_dir() or (candidate / ".git").exists():
            return candidate
    return start


def project_config_path(root: Path) -> Path:
    return root / ".querymind" / "config.toml"
