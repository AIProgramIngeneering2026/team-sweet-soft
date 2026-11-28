"""Реализации хранилищ за интерфейсами ядра."""

from sweetsoft.storage.postgresql import PostgreSQLJobStore
from sweetsoft.storage.settings import database_url
from sweetsoft.storage.sql import SQLJobStore
from sweetsoft.storage.sqlite import SQLiteJobStore


def create_job_store() -> SQLJobStore:
    url = database_url()
    if url.get_backend_name() == "postgresql":
        return PostgreSQLJobStore(url)
    return SQLiteJobStore(url.database)


__all__ = ["PostgreSQLJobStore", "SQLiteJobStore", "create_job_store"]
