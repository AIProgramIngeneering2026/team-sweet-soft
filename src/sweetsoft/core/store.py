"""Хранилище Job.

Ядро работает с хранилищем только через интерфейс JobStore. Сейчас есть реализация
в памяти, на практике 4 появится реализация на БД с тем же интерфейсом.

Правила переходов живут в Job. Хранилище только делает проверку и запись атомарной:
два потока не могут одновременно перевести один Job (ADR-0001).
"""

from __future__ import annotations

import threading
from typing import Any, Protocol

from sweetsoft.core.tool import ToolResult


class Job(Protocol):
    """Что ядру нужно от Job. Сам класс Job и таблица переходов в модуле job."""

    id: str
    tool: str
    params: dict[str, Any]
    state: str
    result: ToolResult | None
    error_code: str | None

    def transition(self, to_state: str, *, reason: str | None = None) -> None:
        """Перейти в to_state или бросить InvalidTransition."""
        ...


class JobStore(Protocol):
    def add(self, job: Job) -> None: ...

    def get(self, job_id: str) -> Job: ...

    def transition(
        self,
        job_id: str,
        to_state: str,
        *,
        reason: str | None = None,
        result: ToolResult | None = None,
        error_code: str | None = None,
    ) -> Job:
        """Атомарно перевести Job и записать результат или код ошибки."""
        ...

    def claim_next(self) -> Job | None:
        """Атомарно взять самый старый Job в queued и перевести его в running."""
        ...

    def list_by_state(self, state: str) -> list[Job]: ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}  # порядок вставки это порядок создания
        self._lock = threading.Lock()

    def add(self, job: Job) -> None:
        with self._lock:
            if job.id in self._jobs:
                raise ValueError(f"Job {job.id} уже есть в хранилище")
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id]

    def transition(
        self,
        job_id: str,
        to_state: str,
        *,
        reason: str | None = None,
        result: ToolResult | None = None,
        error_code: str | None = None,
    ) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            job.transition(to_state, reason=reason)
            if result is not None:
                job.result = result
            if error_code is not None:
                job.error_code = error_code
            return job

    def claim_next(self) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.state == "queued":
                    job.transition("running")
                    return job
            return None

    def list_by_state(self, state: str) -> list[Job]:
        with self._lock:
            return [job for job in self._jobs.values() if job.state == state]
