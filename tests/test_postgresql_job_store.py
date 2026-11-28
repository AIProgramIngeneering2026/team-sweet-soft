import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from health_client import request_health
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema

from sweetsoft.core import IdempotencyKeyConflict, InvalidTransition, Job, JobState, ToolResult
from sweetsoft.storage import PostgreSQLJobStore
from sweetsoft.storage.settings import postgresql_url


@pytest.fixture
def postgres_schema(monkeypatch):
    configured_url = os.getenv("SWEETSOFT_TEST_POSTGRES_URL")
    if not configured_url:
        pytest.skip("SWEETSOFT_TEST_POSTGRES_URL не задан")

    url = postgresql_url(configured_url)
    schema = f"sweetsoft_test_{uuid4().hex}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))

        schema_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
        monkeypatch.setenv("SWEETSOFT_DATABASE_URL", schema_url.render_as_string(hide_password=False))
        try:
            yield schema_url
        finally:
            with engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
    finally:
        engine.dispose()


def queued_job(store):
    job = Job.create("series_summary", {"bot_name": "Игрок"}, "create-id")
    store.add(job)
    return store.transition(job.id, JobState.QUEUED)


def test_postgresql_migration_persistence_and_health(postgres_schema):
    assert request_health().status_code == 503
    command.upgrade(Config("alembic.ini"), "head")
    response = request_health()
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"db": "ok"}}

    with closing(PostgreSQLJobStore(postgres_schema)) as store:
        queued = queued_job(store)
        assert store.claim_next().id == queued.id
        result = ToolResult("done", {"total": 10, "wins": 7})
        finished = store.transition(queued.id, JobState.DONE, result=result)

    with closing(PostgreSQLJobStore(postgres_schema)) as reopened:
        restored = reopened.get(queued.id)
        assert restored == finished
        assert restored.result == result
        assert restored.params == {"bot_name": "Игрок"}
        assert [event.to_state for event in restored.history] == [
            JobState.CREATED, JobState.QUEUED, JobState.RUNNING, JobState.DONE,
        ]
        with pytest.raises(ValueError, match="уже есть"):
            reopened.add(restored)


def test_postgresql_rolls_back_when_history_write_fails(postgres_schema):
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(postgres_schema)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE FUNCTION reject_done_history() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.to_state = 'done' AND
                       (SELECT state FROM jobs WHERE id = NEW.job_id) = 'done' THEN
                        RAISE EXCEPTION 'history write failed';
                    END IF;
                    RETURN NEW;
                END;
                $$
            """)
            connection.exec_driver_sql("""
                CREATE TRIGGER reject_done_history BEFORE INSERT ON job_transitions
                FOR EACH ROW EXECUTE FUNCTION reject_done_history()
            """)
    finally:
        engine.dispose()

    with closing(PostgreSQLJobStore(postgres_schema)) as store:
        queued = queued_job(store)
        running = store.claim_next()
        with pytest.raises(DBAPIError, match="history write failed"):
            store.transition(queued.id, JobState.DONE, result=ToolResult("done", {"total": 1}))
        assert store.get(queued.id) == running


def test_postgresql_worker_and_transition_races(postgres_schema):
    command.upgrade(Config("alembic.ini"), "head")
    with closing(PostgreSQLJobStore(postgres_schema)) as store:
        queued = queued_job(store)
        barrier = Barrier(2, timeout=5)

        def claim(_):
            barrier.wait()
            return store.claim_next()

        with ThreadPoolExecutor(max_workers=2) as workers:
            claimed = [job for job in workers.map(claim, range(2)) if job is not None]
        assert [job.id for job in claimed] == [queued.id]

        def finish(state):
            barrier.wait()
            try:
                if state == JobState.DONE:
                    return store.transition(queued.id, state, result=ToolResult("done", {"total": 1}))
                return store.transition(queued.id, state, reason="cancelled_by_user")
            except InvalidTransition:
                return None

        with ThreadPoolExecutor(max_workers=2) as workers:
            finished = [job for job in workers.map(finish, (JobState.DONE, JobState.CANCELLED)) if job]
        assert len(finished) == 1
        stored = store.get(queued.id)
        assert stored == finished[0]
        assert len(stored.history) == 4
        assert (stored.result is not None) == (stored.state == JobState.DONE)


@pytest.mark.parametrize("same_hash", [True, False], ids=["replay", "conflict"])
def test_postgresql_concurrent_create_with_key(postgres_schema, same_hash):
    command.upgrade(Config("alembic.ini"), "head")
    barrier = Barrier(2, timeout=5)
    with closing(PostgreSQLJobStore(postgres_schema)) as store:
        def create(request_hash):
            job = Job.create("arena_sync", {"bot_id": 1}, "create-id")
            job.enqueue("queue-id")
            barrier.wait()
            try:
                return store.create_with_key(job, key="request-1", request_hash=request_hash)
            except IdempotencyKeyConflict as exc:
                return exc

        hashes = ("a" * 64, ("a" if same_hash else "b") * 64)
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(create, hashes))

        stored, = store.list_by_state(JobState.QUEUED)
        assert [item.to_state for item in stored.history] == [JobState.CREATED, JobState.QUEUED]
        outcomes.remove((True, stored))
        repeated, = outcomes
        if same_hash:
            assert repeated == (False, stored)
        else:
            assert isinstance(repeated, IdempotencyKeyConflict)
            assert repeated.code == "idempotency_key_conflict"
            assert repeated.job_id == stored.id
