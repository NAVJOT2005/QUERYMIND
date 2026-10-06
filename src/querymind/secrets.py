"""API-key and password lookup. Keys are NEVER stored in the project's config files.

Lookup order for a variable name such as GEMINI_API_KEY:
    1. the process environment
    2. <project>/.env                       (git-ignored by convention)
    3. ~/.config/querymind/credentials.env  (chmod 600, outside any repo)

`querymind keys set <provider>` writes to (3), so keys cannot be committed by accident.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

from querymind.paths import credentials_path


def mask(value: str | None) -> str:
    if not value:
        return "(not set)"
    return "..." + value[-4:] if len(value) > 8 else "****"


def lookup(name: str, project_root: Path | None = None) -> tuple[str | None, str | None]:
    """Return (value, source) where source is 'env' | '.env' | 'credentials' | None."""
    value = os.environ.get(name)
    if value:
        return value.strip(), "env"
    if project_root is not None:
        env_file = project_root / ".env"
        if env_file.is_file():
            value = dotenv_values(env_file).get(name)
            if value:
                return value.strip(), ".env"
    creds = credentials_path()
    if creds.is_file():
        value = dotenv_values(creds).get(name)
        if value:
            return value.strip(), "credentials"
    return None, None


def _read_lines() -> list[str]:
    path = credentials_path()
    return path.read_text().splitlines() if path.is_file() else []


def _write_lines(lines: list[str]) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + ("\n" if lines else ""))
    try:
        os.chmod(path, 0o600)
    except OSError:  # e.g. Windows
        pass


def set_secret(name: str, value: str) -> None:
    value = value.strip()
    if not value or any(c in value for c in "\r\n"):
        raise ValueError("Secret must be a single non-empty line.")
    lines = [ln for ln in _read_lines() if not ln.startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    _write_lines(lines)


def remove_secret(name: str) -> bool:
    lines = _read_lines()
    kept = [ln for ln in lines if not ln.startswith(f"{name}=")]
    if len(kept) == len(lines):
        return False
    _write_lines(kept)
    return True
