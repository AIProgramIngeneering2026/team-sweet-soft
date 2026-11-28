"""Общее SQL-хранилище Job для SQLite и PostgreSQL."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, RowMapping

from sweetsoft.core.job import Job, JobState, Transition
from sweetsoft.core.store import Job as StoredJob
from sweetsoft.core.tool import SkippedItem, ToolResult


class SQLJobStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._sqlite = engine.dialect.name == "sqlite"

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[Connection]:
        with self._engine.connect() as connection:
            if not self._sqlite and not write:
                # Job и историю читаем из одного снимка при параллельных обновлениях.
                connection.execution_options(isolation_level="REPEATABLE READ")
            with connection.begin():
                if self._sqlite:
                    connection.exec_driver_sql("PRAGMA foreign_keys = ON")
                    # Блокировку записи берем до чтения состояния Job и очереди.
                    connection.exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection

    def close(self) -> None:
        self._engine.dispose()

    def check_health(self) -> None:
        """Проверить доступ к обеим таблицам схемы."""
        with self._transaction() as connection:
            connection.execute(text("SELECT 1 FROM jobs LIMIT 1")).first()
            connection.execute(text("SELECT 1 FROM job_transitions LIMIT 1")).first()

    def add(self, job: StoredJob) -> None:
        history = job.history
        created_at = history[0].at if history else datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        with self._transaction(write=True) as connection:
            inserted = connection.execute(
                text("""INSERT INTO jobs
                   (id, tool, params, state, correlation_id, created_at,
                    result, error_code, error_message)
                   VALUES (:id, :tool, :params, :state, :correlation_id, :created_at,
                           :result, :error_code, :error_message)
                   ON CONFLICT (id) DO NOTHING"""),
                {
                    "id": job.id,
                    "tool": job.tool,
                    "params": json.dumps(job.params),
                    "state": str(job.state),
                    "correlation_id": job.correlation_id,
                    "created_at": created_at.astimezone(timezone.utc).isoformat(timespec="microseconds"),
                    "result": _encode_result(job.result),
                    "error_code": job.error_code,
                    "error_message": job.error_message,
                },
            )
            if inserted.rowcount == 0:
                raise ValueError(f"Job {job.id} уже есть в хранилище")
            _append_history(connection, job.id, 0, history)

    def get(self, job_id: str) -> Job:
        with self._transaction() as connection:
            return self._get(connection, job_id)

    def transition(
        self,
        job_id: str,
        to_state: str,
        *,
        reason: str | None = None,
        correlation_id: str | None = None,
        result: ToolResult | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Job:
        with self._transaction(write=True) as connection:
            job = self._get(connection, job_id, lock=True)
            job.transition(
                to_state,
                reason=reason,
                correlation_id=correlation_id,
                result=result,
                error_code=error_code,
                error_message=error_message,
            )
            self._save_transition(connection, job)
            return job

    def claim_next(self) -> Job | None:
        query = "SELECT * FROM jobs WHERE state = :state ORDER BY created_at, sequence LIMIT 1"
        if not self._sqlite:
            query += " FOR UPDATE SKIP LOCKED"
        with self._transaction(write=True) as connection:
            row = connection.execute(text(query), {"state": JobState.QUEUED.value}).mappings().first()
            if row is None:
                return None
            job = self._load(connection, row)
            job.start(job.correlation_id)
            self._save_transition(connection, job)
            return job

    def list_by_state(self, state: str) -> list[Job]:
        with self._transaction() as connection:
            rows = connection.execute(
                text("SELECT * FROM jobs WHERE state = :state ORDER BY created_at, sequence"),
                {"state": str(state)},
            ).mappings()
            return [self._load(connection, row) for row in rows]

    def _get(self, connection: Connection, job_id: str, *, lock: bool = False) -> Job:
        query = "SELECT * FROM jobs WHERE id = :id"
        if lock and not self._sqlite:
            query += " FOR UPDATE"
        row = connection.execute(text(query), {"id": job_id}).mappings().first()
        if row is None:
            raise KeyError(job_id)
        return self._load(connection, row)

    @staticmethod
    def _load(connection: Connection, row: RowMapping) -> Job:
        history = connection.execute(
            text("""SELECT from_state, to_state, at, correlation_id, reason
                    FROM job_transitions WHERE job_id = :id ORDER BY position"""),
            {"id": row["id"]},
        ).mappings()
        return Job(
            id=row["id"],
            tool=row["tool"],
            params=json.loads(row["params"]),
            state=JobState(row["state"]),
            correlation_id=row["correlation_id"],
            history=[
                Transition(
                    from_state=JobState(event["from_state"]) if event["from_state"] else None,
                    to_state=JobState(event["to_state"]),
                    at=datetime.fromisoformat(event["at"]),
                    correlation_id=event["correlation_id"],
                    reason=event["reason"],
                )
                for event in history
            ],
            result=_decode_result(row["result"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
        )

    @staticmethod
    def _save_transition(connection: Connection, job: Job) -> None:
        connection.execute(
            text("""UPDATE jobs SET state = :state, result = :result,
                   error_code = :error_code, error_message = :error_message WHERE id = :id"""),
            {
                "id": job.id,
                "state": job.state.value,
                "result": _encode_result(job.result),
                "error_code": job.error_code,
                "error_message": job.error_message,
            },
        )
        _append_history(connection, job.id, len(job.history) - 1, job.history[-1:])


def _append_history(
    connection: Connection, job_id: str, start: int, history: Sequence[Transition]
) -> None:
    if not history:
        return
    connection.execute(
        text("""INSERT INTO job_transitions
                (job_id, position, from_state, to_state, at, correlation_id, reason)
                VALUES (:job_id, :position, :from_state, :to_state, :at, :correlation_id, :reason)"""),
        [
            {
                "job_id": job_id,
                "position": position,
                "from_state": event.from_state.value if event.from_state is not None else None,
                "to_state": event.to_state.value,
                "at": event.at.isoformat(),
                "correlation_id": event.correlation_id,
                "reason": event.reason,
            }
            for position, event in enumerate(history, start)
        ],
    )


def _encode_result(result: ToolResult | None) -> str | None:
    return json.dumps(asdict(result)) if result is not None else None


def _decode_result(value: str | None) -> ToolResult | None:
    if value is None:
        return None
    result = json.loads(value)
    return ToolResult(
        status=result["status"],
        data=result["data"],
        skipped=[SkippedItem(**item) for item in result["skipped"]],
        warnings=result["warnings"],
    )
