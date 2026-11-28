"""Хранилище Job в PostgreSQL."""

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from sweetsoft.storage.settings import postgresql_url
from sweetsoft.storage.sql import SQLJobStore


class PostgreSQLJobStore(SQLJobStore):
    def __init__(self, url: str | URL) -> None:
        engine = create_engine(
            postgresql_url(url),
            isolation_level="READ COMMITTED",
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3},
            hide_parameters=True,
        )
        super().__init__(engine)
