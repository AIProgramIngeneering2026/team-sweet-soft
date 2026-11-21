"""Ядро не зависит от веб-фреймворка и драйвера БД."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE_DIR = Path(__file__).resolve().parents[1] / "src" / "sweetsoft" / "core"

FORBIDDEN = {
    "fastapi",
    "starlette",
    "flask",
    "django",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "sqlite3",
    "httpx",
    "requests",
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


CORE_FILES = sorted(CORE_DIR.rglob("*.py"))


def test_core_files_found():
    assert CORE_FILES


@pytest.mark.parametrize("path", CORE_FILES, ids=lambda p: p.name)
def test_core_has_no_framework_or_db_imports(path):
    bad = imported_modules(path) & FORBIDDEN
    assert not bad, f"{path.name} импортирует {sorted(bad)}"
