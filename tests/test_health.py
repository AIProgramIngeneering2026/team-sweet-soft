from uuid import UUID

from alembic import command
from alembic.config import Config
from health_client import request_health


def test_health_checks_migrated_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("SWEETSOFT_DATABASE_URL", raising=False)
    monkeypatch.setenv("SWEETSOFT_DB_PATH", str(tmp_path / "health.sqlite3"))
    command.upgrade(Config("alembic.ini"), "head")

    response = request_health({"X-Correlation-ID": "health-check.1"})

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "health-check.1"
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
