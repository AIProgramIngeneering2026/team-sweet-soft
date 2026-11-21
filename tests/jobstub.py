"""Временная заглушка Job для тестов Dispatcher.

Таблица переходов из docs/requirements.md. Заменить на настоящий Job из
sweetsoft.core.job, когда он появится.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sweetsoft.core.errors import InvalidTransition
from sweetsoft.core.tool import ToolResult

ALLOWED = {
    ("created", "queued"),
    ("queued", "running"),
    ("running", "done"),
    ("running", "partial"),
    ("running", "failed"),
    ("created", "cancelled"),
    ("queued", "cancelled"),
    ("running", "cancelled"),
}


@dataclass(frozen=True)
class Transition:
    from_state: str
    to_state: str
    reason: str | None
    correlation_id: str


@dataclass
class StubJob:
    id: str
    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    state: str = "created"
    correlation_id: str = "corr-create"
    result: ToolResult | None = None
    error_code: str | None = None
    error_message: str | None = None
    history: list[Transition] = field(default_factory=list)

    def transition(self, to_state: str, *, reason: str | None = None, correlation_id: str | None = None) -> None:
        if (self.state, to_state) not in ALLOWED:
            raise InvalidTransition(self.id, self.state, to_state)
        self.history.append(Transition(self.state, to_state, reason, correlation_id or self.correlation_id))
        self.state = to_state
