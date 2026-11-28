import sqlite3
from contextlib import closing
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from health_client import request_health


@pytest.mark.parametrize("incoming_id", [None, "health-check.1", "invalid id"])
def test_health_checks_migrated_sqlite_and_returns_correlation_id(tmp_path, monkeypatch, incoming_id):
    monkeypatch.delenv("SWEETSOFT_DATABASE_URL", raising=False)
    monkeypatch.setenv("SWEETSOFT_DB_PATH", str(tmp_path / "health.sqlite3"))
    command.upgrade(Config("alembic.ini"), "head")

    headers = {"X-Correlation-ID": incoming_id} if incoming_id is not None else None
    response = request_health(headers)

    assert response.status_code == 200
    correlation_id = response.headers["X-Correlation-ID"]
    if incoming_id == "health-check.1":
        assert correlation_id == incoming_id
    else:
        assert UUID(correlation_id).version == 4
    assert response.json() == {"status": "ok", "checks": {"db": "ok"}}


def test_health_returns_contract_error_when_database_is_unavailable(tmp_path, monkeypatch):
    db_path = tmp_path / "absent.sqlite3"
    monkeypatch.delenv("SWEETSOFT_DATABASE_URL", raising=False)
    monkeypatch.setenv("SWEETSOFT_DB_PATH", str(db_path))

    response = request_health({"X-Correlation-ID": "invalid id"})

    assert response.status_code == 503
    correlation_id = response.headers["X-Correlation-ID"]
    assert UUID(correlation_id).version == 4
    assert response.json() == {
        "error": {
            "code": "service_unavailable",
            "message": "БД недоступна",
            "correlation_id": correlation_id,
        }
    }
    assert not db_path.exists()


@pytest.mark.parametrize(
    "version_change",
    ["UPDATE alembic_version SET version_num = 'previous_revision'", "DROP TABLE alembic_version"],
    ids=["stale-revision", "missing-version-table"],
)
def test_health_rejects_schema_without_current_revision(tmp_path, monkeypatch, version_change):
    db_path = tmp_path / "stale.sqlite3"
    monkeypatch.delenv("SWEETSOFT_DATABASE_URL", raising=False)
    monkeypatch.setenv("SWEETSOFT_DB_PATH", str(db_path))
    command.upgrade(Config("alembic.ini"), "head")
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute(version_change)

    response = request_health({"X-Correlation-ID": "health-check.1"})

    assert response.status_code == 503
    assert response.headers["X-Correlation-ID"] == "health-check.1"
    assert response.json() == {
        "error": {
            "code": "service_unavailable",
            "message": "БД недоступна",
            "correlation_id": "health-check.1",
        }
    }
