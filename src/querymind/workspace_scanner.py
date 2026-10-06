"""Workspace scanning for ORM models, migrations, schema files, and SQL files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelMetadata:
    name: str
    source_file: str
    orm_type: str  # "prisma" | "sqlalchemy" | "django" | "sql" | "general"
    docstring: str = ""
    fields: dict[str, str] = field(default_factory=dict)  # field_name -> type/info
    comments: dict[str, str] = field(default_factory=dict)  # field_name -> comment/help_text
    relations: list[str] = field(default_factory=list)


def parse_prisma(path: Path) -> list[ModelMetadata]:
    """Parse Prisma schema.prisma for models, fields, relations, and doc comments."""
    if not path.is_file():
        return []
    content = path.read_text(encoding="utf-8", errors="replace")
    models: list[ModelMetadata] = []
    current_model: ModelMetadata | None = None
    accumulated_comment: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("///"):
            accumulated_comment.append(line.lstrip("/").strip())
            continue

        model_match = re.match(r"^model\s+([A-Za-z0-9_]+)\s*\{", line)
        if model_match:
            doc = " ".join(accumulated_comment)
            accumulated_comment = []
            current_model = ModelMetadata(
                name=model_match.group(1),
                source_file=str(path.name),
                orm_type="prisma",
                docstring=doc,
            )
            continue

        if line == "}":
            if current_model:
                models.append(current_model)
                current_model = None
            accumulated_comment = []
            continue

        if current_model and line and not line.startswith("//"):
            parts = line.split()
            if len(parts) >= 2:
                field_name = parts[0]
                field_type = parts[1]
                current_model.fields[field_name] = field_type
                if accumulated_comment:
                    current_model.comments[field_name] = " ".join(accumulated_comment)
                    accumulated_comment = []
                if "@relation" in line:
                    current_model.relations.append(line)

    return models


def parse_sqlalchemy(path: Path) -> list[ModelMetadata]:
    """Parse SQLAlchemy models from python files."""
    if not path.is_file() or not path.name.endswith(".py"):
        return []
    content = path.read_text(encoding="utf-8", errors="replace")
    if "Column" not in content and "mapped_column" not in content and "Base" not in content:
        return []

    models: list[ModelMetadata] = []
    # Match class definitions
    class_pattern = re.compile(r"^\s*class\s+([A-Za-z0-9_]+)\s*\([^)]*\):", re.MULTILINE)
    matches = list(class_pattern.finditer(content))

    for i, match in enumerate(matches):
        class_name = match.group(1)
        start_idx = match.end()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start_idx:end_idx]

        # Extract __tablename__ if present
        table_match = re.search(r'__tablename__\s*=\s*["\']([A-Za-z0-9_]+)["\']', body)
        table_name = table_match.group(1) if table_match else class_name

        # Extract class docstring
        doc = ""
        doc_match = re.search(r'^\s*["\']{3}([\s\S]*?)["\']{3}', body)
        if doc_match:
            doc = " ".join(doc_match.group(1).split())

        model = ModelMetadata(
            name=table_name,
            source_file=str(path.name),
            orm_type="sqlalchemy",
            docstring=doc,
        )

        # Extract columns
        col_pattern = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*(?:Column|mapped_column)\s*\(((?:[^()]*|\([^()]*\))*)\)', re.MULTILINE)
        for col_match in col_pattern.finditer(body):
            col_name = col_match.group(1)
            col_args = col_match.group(2)
            model.fields[col_name] = col_args.split(",")[0].strip() if col_args else "unknown"

            # Check for comment
            comment_match = re.search(r'comment\s*=\s*["\']([^"\']+)["\']', col_args)
            if comment_match:
                model.comments[col_name] = comment_match.group(1)

            # Check for ForeignKey
            fk_match = re.search(r'ForeignKey\s*\(\s*["\']([^"\']+)["\']', col_args)
            if fk_match:
                model.relations.append(f"{col_name} -> {fk_match.group(1)}")

        if model.fields or table_match:
            models.append(model)

    return models


def parse_django(path: Path) -> list[ModelMetadata]:
    """Parse Django models from models.py files."""
    if not path.is_file() or not path.name.endswith(".py"):
        return []
    content = path.read_text(encoding="utf-8", errors="replace")
    if "models.Model" not in content and "from django.db import models" not in content:
        return []

    models: list[ModelMetadata] = []
    class_pattern = re.compile(r"^\s*class\s+([A-Za-z0-9_]+)\s*\(.*models\.Model.*\):", re.MULTILINE)
    matches = list(class_pattern.finditer(content))

    for i, match in enumerate(matches):
        class_name = match.group(1)
        start_idx = match.end()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start_idx:end_idx]

        doc = ""
        doc_match = re.search(r'^\s*["\']{3}([\s\S]*?)["\']{3}', body)
        if doc_match:
            doc = " ".join(doc_match.group(1).split())

        model = ModelMetadata(
            name=class_name,
            source_file=str(path.name),
            orm_type="django",
            docstring=doc,
        )

        field_pattern = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*models\.([A-Za-z0-9_]+)\s*\(([^)]*)\)', re.MULTILINE)
        for f_match in field_pattern.finditer(body):
            f_name, f_type, f_args = f_match.group(1), f_match.group(2), f_match.group(3)
            model.fields[f_name] = f_type
            help_match = re.search(r'help_text\s*=\s*["\']([^"\']+)["\']', f_args)
            if help_match:
                model.comments[f_name] = help_match.group(1)
            if f_type in ("ForeignKey", "OneToOneField"):
                rel_target = f_args.split(",")[0].strip().strip('"\'')
                model.relations.append(f"{f_name} -> {rel_target}")

        models.append(model)

    return models


def scan_workspace_models(root: Path) -> list[ModelMetadata]:
    """Scan project root for ORM models (Prisma, SQLAlchemy, Django)."""
    results: list[ModelMetadata] = []
    # Avoid scanning node_modules, .git, venv, __pycache__
    ignored_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__", ".pytest_cache", "build", "dist"}

    for path in root.rglob("*"):
        if any(part in ignored_dirs for part in path.parts):
            continue
        if path.name == "schema.prisma":
            results.extend(parse_prisma(path))
        elif path.suffix == ".py":
            if "model" in path.name.lower() or "schema" in path.name.lower():
                results.extend(parse_sqlalchemy(path))
                results.extend(parse_django(path))

    return results


def search_workspace(root: Path, query: str, max_results: int = 5) -> str:
    """Grep migrations, ORM models, docs, and .sql files in the workspace for terms."""
    query_lower = query.lower()
    matches: list[str] = []
    ignored_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__", ".pytest_cache", "build", "dist"}
    allowed_exts = {".sql", ".py", ".prisma", ".ts", ".js", ".md", ".txt"}

    for path in root.rglob("*"):
        if any(part in ignored_dirs for part in path.parts) or not path.is_file():
            continue
        if path.suffix not in allowed_exts:
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if query_lower in content.lower():
            lines = content.splitlines()
            matching_lines: list[str] = []
            for idx, line in enumerate(lines):
                if query_lower in line.lower():
                    # context snippet: preceding line, matching line, following line
                    start = max(0, idx - 1)
                    end = min(len(lines), idx + 2)
                    snippet = "\n".join(f"  {lines[j]}" for j in range(start, end))
                    matching_lines.append(snippet)
                    if len(matching_lines) >= 2:
                        break

            rel_path = path.relative_to(root)
            matches.append(f"File {rel_path}:\n" + "\n---\n".join(matching_lines))
            if len(matches) >= max_results:
                break

    if not matches:
        return f"No matches found for '{query}' in workspace files."
    return "\n\n".join(matches)


def find_existing_sql_queries(root: Path, table_name: str = "", max_results: int = 3) -> list[tuple[str, str]]:
    """Search project for existing .sql files matching a table or keyword to reuse styles."""
    found: list[tuple[str, str]] = []
    ignored_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist"}

    for path in root.rglob("*.sql"):
        if any(part in ignored_dirs for part in path.parts) or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if not table_name or table_name.lower() in content.lower():
                rel_path = str(path.relative_to(root))
                # Only take up to first 500 chars of sql
                found.append((rel_path, content[:500]))
                if len(found) >= max_results:
                    break
        except OSError:
            continue

    return found


def propose_memory_addition(root: Path, rule: str) -> Path:
    """Propose and append a new rule/definition to QUERYMIND.md."""
    notes_path = root / "QUERYMIND.md"
    rule_line = f"- {rule.strip()}"
    if not notes_path.exists():
        notes_path.write_text(f"# QueryMind project notes\n\n## Definitions\n{rule_line}\n", encoding="utf-8")
    else:
        text = notes_path.read_text(encoding="utf-8", errors="replace")
        if rule_line not in text:
            if "## Definitions" in text:
                text = text.replace("## Definitions", f"## Definitions\n{rule_line}")
            else:
                text += f"\n\n## Custom Definitions\n{rule_line}\n"
            notes_path.write_text(text, encoding="utf-8")
    return notes_path
