"""Доменное ядро: Job, контракт Tool, Registry, Dispatcher.

Здесь нет импортов веб-фреймворка и драйвера БД. Хранение и внешние сервисы
ядро получает через интерфейсы.
"""

from sweetsoft.core.errors import CoreError, IdempotencyKeyConflict, InvalidTransition
from sweetsoft.core.job import ALLOWED_TRANSITIONS, TERMINAL_STATES, Job, JobState, Transition
from sweetsoft.core.tool import (
    ParamError,
    RunContext,
    SkippedItem,
    SkipReason,
    Tool,
    ToolError,
    ToolResult,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATES",
    "CoreError",
    "IdempotencyKeyConflict",
    "InvalidTransition",
    "Job",
    "JobState",
    "ParamError",
    "RunContext",
    "SkippedItem",
    "SkipReason",
    "Tool",
    "ToolError",
    "ToolResult",
    "Transition",
]
