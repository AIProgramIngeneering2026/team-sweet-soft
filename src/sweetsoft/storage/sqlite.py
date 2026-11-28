"""Файловое SQLite-хранилище Job."""

from pathlib import Path

from sqlalchemy import create_engine, pool

from sweetsoft.storage.settings import sqlite_url
from sweetsoft.storage.sql import SQLJobStore


class SQLiteJobStore(SQLJobStore):
    def __init__(self, path: str | Path | None = None) -> None:
        url = sqlite_url(path)
        # Файл создаёт Alembic. mode=rw запрещает создавать пустую БД при открытии.
        url = url.set(database=Path(url.database).as_uri(), query={"mode": "rw", "uri": "true"})
        super().__init__(create_engine(url, poolclass=pool.NullPool, hide_parameters=True))
