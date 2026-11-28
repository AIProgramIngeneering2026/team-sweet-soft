import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError

from sweetsoft.core.dispatcher import INTERNAL_ERROR, RESTART_MESSAGE, recover_after_restart
from sweetsoft.core.errors import InvalidTransition
from sweetsoft.core.job import Job, JobState
from sweetsoft.core.tool import SkippedItem, ToolResult
from sweetsoft.storage import SQLiteJobStore


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "тестовая база.sqlite3"
    monkeypatch.delenv("SWEETSOFT_DATABASE_URL", raising=False)
    monkeypatch.setenv("SWEETSOFT_DB_PATH", str(path))
    return path


@pytest.fixture
def migration_config():
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


@pytest.fixture
def migrated_db(db_path, migration_config):
    command.upgrade(migration_config, "head")
    return db_path


def queued_job(store, *, at=None):
    job = Job.create("series_summary", {"bot_name": "Игрок"}, "create-id", at=at)
    store.add(job)
    return store.transition(job.id, JobState.QUEUED, correlation_id="queue-id")


def test_upgrade_empty_database(db_path, migration_config):
    store = SQLiteJobStore()
    with pytest.raises(OperationalError):
        store.check_health()
    assert not db_path.exists()

    command.upgrade(migration_config, "head")
    command.upgrade(migration_config, "head")
    store.check_health()
    assert store.claim_next() is None


def test_job_result_and_history_survive_reopening(migrated_db):
    store = SQLiteJobStore(migrated_db)
    created_at = datetime(2026, 11, 28, 10, tzinfo=timezone(timedelta(hours=5)))
    queued = queued_job(store, at=created_at)
    assert store.claim_next().id == queued.id
    result = ToolResult(
        "partial",
        {"total": 3, "by_map": {"Карта": {"win_rate": 66.7}}},
        skipped=[SkippedItem("params.replays[3]", "corrupted_replay")],
        warnings=["low_sample"],
    )
    finished = store.transition(queued.id, JobState.PARTIAL, result=result, correlation_id="finish-id")

    restored = SQLiteJobStore(migrated_db).get(queued.id)
    assert restored == finished
    assert restored.result == result
    assert restored.params == {"bot_name": "Игрок"}
    assert restored.history[0].at.isoformat() == created_at.isoformat()
    assert [event.to_state for event in restored.history] == [
        JobState.CREATED, JobState.QUEUED, JobState.RUNNING, JobState.PARTIAL,
    ]
    assert [event.correlation_id for event in restored.history] == [
        "create-id", "queue-id", "create-id", "finish-id",
    ]


def test_restart_recovers_running_job_and_keeps_queue(migrated_db):
    store = SQLiteJobStore(migrated_db)
    first = queued_job(store)
    assert store.claim_next().id == first.id
    second = queued_job(store)

    restarted = SQLiteJobStore(migrated_db)
    assert [job.id for job in recover_after_restart(restarted)] == [first.id]
    failed = SQLiteJobStore(migrated_db).get(first.id)
    assert failed.state == JobState.FAILED
    assert failed.error_code == INTERNAL_ERROR
    assert failed.error_message == RESTART_MESSAGE
    assert failed.history[-1].reason == INTERNAL_ERROR
    assert restarted.claim_next().id == second.id


def test_history_write_failure_rolls_back_state_and_result(migrated_db):
    store = SQLiteJobStore(migrated_db)
    queued = queued_job(store)
    running = store.claim_next()
    with closing(sqlite3.connect(migrated_db)) as connection, connection:
        connection.execute(
            """CREATE TRIGGER reject_done_history BEFORE INSERT ON job_transitions
               WHEN NEW.to_state = 'done'
               BEGIN
                   SELECT RAISE(ABORT, 'history write failed');
               END"""
        )

    result = ToolResult("done", {"total": 1})
    with pytest.raises(IntegrityError, match="history write failed"):
        store.transition(queued.id, JobState.DONE, result=result)
    assert SQLiteJobStore(migrated_db).get(queued.id) == running

    with closing(sqlite3.connect(migrated_db)) as connection, connection:
        connection.execute("DROP TRIGGER reject_done_history")
    finished = store.transition(queued.id, JobState.DONE, result=result)
    assert finished.result == result
    assert len(finished.history) == len(running.history) + 1


def test_claim_next_uses_creation_order_and_cannot_claim_twice(migrated_db):
    store = SQLiteJobStore(migrated_db)
    now = datetime.now(timezone.utc)
    newer = queued_job(store, at=now)
    older = queued_job(store, at=now - timedelta(seconds=10))
    assert store.claim_next().id == older.id

    other_store = SQLiteJobStore(migrated_db)
    before_select = Event()
    other_begin = Event()
    resume = Event()

    @event.listens_for(store._engine, "before_cursor_execute")
    def pause_before_select(_connection, _cursor, statement, *_):
        if statement.startswith("SELECT * FROM jobs WHERE state"):
            before_select.set()
            assert resume.wait(timeout=5)

    @event.listens_for(other_store._engine, "before_cursor_execute")
    def record_other_begin(_connection, _cursor, statement, *_):
        if statement == "BEGIN IMMEDIATE":
            other_begin.set()

    with ThreadPoolExecutor(max_workers=2) as workers:
        try:
            first = workers.submit(store.claim_next)
            assert before_select.wait(timeout=5)
            # Первый воркер ещё не выполнил SELECT, но SQLite уже держит блокировку записи.
            with closing(sqlite3.connect(migrated_db, timeout=0)) as probe:
                with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                    probe.execute("BEGIN IMMEDIATE")
            second = workers.submit(other_store.claim_next)
            assert other_begin.wait(timeout=5)
        finally:
            resume.set()

        assert first.result().id == newer.id
        assert second.result() is None

    history = store.get(newer.id).history
    assert [item.to_state for item in history] == [JobState.CREATED, JobState.QUEUED, JobState.RUNNING]


def test_rejected_transition_preserves_cancelled_job(migrated_db):
    store = SQLiteJobStore(migrated_db)
    queued = queued_job(store)
    store.claim_next()
    cancelled = store.transition(queued.id, JobState.CANCELLED, reason="cancelled_by_user")

    with pytest.raises(InvalidTransition):
        store.transition(queued.id, JobState.DONE, result=ToolResult("done", {"total": 1}))

    assert store.get(queued.id) == cancelled
    assert cancelled.result is None
