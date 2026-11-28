"""Файловое SQLite-хранилище Job для прототипа."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from sweetsoft.core.job import Job, JobState, Transition
from sweetsoft.core.store import Job as StoredJob
from sweetsoft.core.tool import SkippedItem, ToolResult


def database_path() -> Path:
    return Path(os.environ.get("SWEETSOFT_DB_PATH", "sweetsoft.sqlite3"))


class SQLiteJobStore:
    def __init__(self, path: str | Path | None = None) -> None:
        db_path = database_path() if path is None else Path(path)
        self._uri = f"{db_path.resolve().as_uri()}?mode=rw"

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._uri, uri=True)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            # Блокировку записи берем до чтения состояния Job. Обычный BEGIN
            # дает согласованный снимок Job и его истории при чтении.
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection

    def check_health(self) -> None:
        """Проверить доступ к схеме. При проблеме sqlite3 поднимает исключение."""
        with self._transaction() as connection:
            connection.execute("SELECT 1 FROM jobs LIMIT 1").fetchone()
            connection.execute("SELECT 1 FROM job_transitions LIMIT 1").fetchone()

    def add(self, job: StoredJob) -> None:
        history = job.history
        created_at = history[0].at if history else datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        with self._transaction(write=True) as connection:
            inserted = connection.execute(
                """INSERT INTO jobs
                   (id, tool, params, state, correlation_id, created_at,
                    result, error_code, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO NOTHING""",
                (
                    job.id,
                    job.tool,
                    json.dumps(job.params),
                    job.state,
                    job.correlation_id,
                    created_at.astimezone(timezone.utc).isoformat(timespec="microseconds"),
                    _encode_result(job.result),
                    job.error_code,
                    job.error_message,
                ),
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
            job = self._get(connection, job_id)
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
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY created_at, sequence LIMIT 1",
                (JobState.QUEUED,),
            ).fetchone()
            if row is None:
                return None
            job = self._load(connection, row)
            job.start(job.correlation_id)
            self._save_transition(connection, job)
            return job

    def list_by_state(self, state: str) -> list[Job]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY created_at, sequence", (state,)
            )
            return [self._load(connection, row) for row in rows]

    def _get(self, connection: sqlite3.Connection, job_id: str) -> Job:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._load(connection, row)

    @staticmethod
    def _load(connection: sqlite3.Connection, row: sqlite3.Row) -> Job:
        history = connection.execute(
            """SELECT from_state, to_state, at, correlation_id, reason
               FROM job_transitions WHERE job_id = ? ORDER BY position""",
            (row["id"],),
        )
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
    def _save_transition(connection: sqlite3.Connection, job: Job) -> None:
        connection.execute(
            """UPDATE jobs SET state = ?, result = ?, error_code = ?, error_message = ?
               WHERE id = ?""",
            (job.state, _encode_result(job.result), job.error_code, job.error_message, job.id),
        )
        _append_history(connection, job.id, len(job.history) - 1, job.history[-1:])


def _append_history(
    connection: sqlite3.Connection, job_id: str, start: int, history: Sequence[Transition]
) -> None:
    connection.executemany(
        """INSERT INTO job_transitions
           (job_id, position, from_state, to_state, at, correlation_id, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                job_id,
                position,
                event.from_state,
                event.to_state,
                event.at.isoformat(),
                event.correlation_id,
                event.reason,
            )
            for position, event in enumerate(history, start)
        ),
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
