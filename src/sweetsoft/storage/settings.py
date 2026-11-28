"""Настройки подключения, общие для приложения и миграций."""

import os
from pathlib import Path

from sqlalchemy.engine import URL, make_url


def sqlite_url(path: str | Path | None = None) -> URL:
    if path is None:
        path = os.environ.get("SWEETSOFT_DB_PATH", "sweetsoft.sqlite3")
    return URL.create("sqlite", database=str(Path(path).resolve()))


def postgresql_url(value: str | URL) -> URL:
    url = make_url(value)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("Для SWEETSOFT_DATABASE_URL ожидается URL PostgreSQL")
    return url.set(drivername="postgresql+psycopg")


def database_url() -> URL:
    value = os.environ.get("SWEETSOFT_DATABASE_URL")
    return postgresql_url(value) if value else sqlite_url()
