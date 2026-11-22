"""Job и его жизненный цикл.

Правило переходов живет здесь, а не в вызывающем коде. Любой запрещенный переход
дает InvalidTransition, состояние и история при этом не меняются.
Диаграмма: docs/architecture.md, раздел «Диаграмма состояний Job».
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sweetsoft.core.errors import InvalidTransition
from sweetsoft.core.tool import ToolResult


class JobState(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[JobState] = frozenset(
    {JobState.DONE, JobState.PARTIAL, JobState.FAILED, JobState.CANCELLED}
)

# Разрешенные переходы. Все, чего здесь нет, запрещено.
ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.CREATED: frozenset({JobState.QUEUED, JobState.CANCELLED}),
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.CANCELLED}),
    JobState.RUNNING: frozenset(
        {JobState.DONE, JobState.PARTIAL, JobState.FAILED, JobState.CANCELLED}
    ),
    JobState.DONE: frozenset(),
    JobState.PARTIAL: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Transition:
    """Запись в истории Job."""

    from_state: JobState | None  # None только у первой записи, создания Job
    to_state: JobState
    at: datetime
    correlation_id: str
    reason: str | None = None  # для failed и cancelled


@dataclass
class Job:
    id: str
    tool: str
    params: dict[str, Any]
    state: JobState
    correlation_id: str  # correlation_id запроса, который создал Job
    history: list[Transition] = field(default_factory=list)
    result: ToolResult | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def create(
        cls,
        tool: str,
        params: dict[str, Any],
        correlation_id: str,
        job_id: str | None = None,
        at: datetime | None = None,
    ) -> Job:
        """Новый Job в состоянии created. Создание это первая запись в истории."""
        job = cls(
            id=job_id or str(uuid.uuid4()),
            tool=tool,
            params=params,
            state=JobState.CREATED,
            correlation_id=correlation_id,
        )
        job.history.append(
            Transition(None, JobState.CREATED, at or _now(), correlation_id)
        )
        return job

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def can_move_to(self, to_state: JobState) -> bool:
        return to_state in ALLOWED_TRANSITIONS[self.state]

    def transition(
        self,
        to_state: JobState | str,
        *,
        reason: str | None = None,
        correlation_id: str | None = None,
        at: datetime | None = None,
    ) -> None:
        """Общий переход по таблице.

        Строку приводим к JobState: хранилище передает состояние строкой, как в
        контракте (ADR-0001). Неизвестное имя это тоже запрещенный переход.
        """
        self._move(to_state, correlation_id or self.correlation_id, at, reason)

    def enqueue(self, correlation_id: str, at: datetime | None = None) -> None:
        """created -> queued: Job сохранен и поставлен в очередь."""
        self._move(JobState.QUEUED, correlation_id, at)

    def start(self, correlation_id: str, at: datetime | None = None) -> None:
        """queued -> running: Dispatcher взял Job."""
        self._move(JobState.RUNNING, correlation_id, at)

    def complete(
        self, result: ToolResult, correlation_id: str, at: datetime | None = None
    ) -> None:
        """running -> done или partial, смотря что вернул Tool."""
        to_state = JobState.DONE if result.status == "done" else JobState.PARTIAL
        self._move(to_state, correlation_id, at)
        self.result = result

    def fail(
        self,
        error_code: str,
        error_message: str,
        correlation_id: str,
        at: datetime | None = None,
    ) -> None:
        """running -> failed: ошибка Tool, AI Arena или перезапуск сервиса.

        В историю пишем код причины, текст для человека живет в error_message.
        """
        self._move(JobState.FAILED, correlation_id, at, reason=error_code)
        self.error_code = error_code
        self.error_message = error_message

    def cancel(
        self,
        correlation_id: str,
        reason: str = "отменен пользователем",
        at: datetime | None = None,
    ) -> None:
        """created, queued или running -> cancelled. У cancelled нет результата."""
        self._move(JobState.CANCELLED, correlation_id, at, reason=reason)
        self.result = None

    def _move(
        self,
        to_state: JobState | str,
        correlation_id: str,
        at: datetime | None,
        reason: str | None = None,
    ) -> None:
        if not isinstance(to_state, JobState):
            try:
                to_state = JobState(to_state)
            except ValueError:
                raise InvalidTransition(self.id, self.state.value, str(to_state)) from None
        if not self.can_move_to(to_state):
            raise InvalidTransition(self.id, self.state.value, to_state.value)
        self.history.append(
            Transition(self.state, to_state, at or _now(), correlation_id, reason)
        )
        self.state = to_state
