"""Dispatcher: берет Job из очереди, запускает Tool и переводит Job по итогу.

Конкретных Tool Dispatcher не знает, он получает их из Registry по имени.
Как Job выполняется в фоне и что происходит при перезапуске: ADR-0001.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sweetsoft.core.errors import InvalidTransition
from sweetsoft.core.registry import Registry
from sweetsoft.core.store import Job, JobStore
from sweetsoft.core.tool import ToolError, ToolResult

log = logging.getLogger(__name__)

RESTART_REASON = "выполнение прервано перезапуском сервиса"


@dataclass
class StoreRunContext:
    """RunContext для Tool: correlation_id и проверка отмены по хранилищу."""

    job_id: str
    store: JobStore
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def is_cancelled(self) -> bool:
        return self.store.get(self.job_id).state == "cancelled"


class Dispatcher:
    def __init__(self, registry: Registry, store: JobStore) -> None:
        self._registry = registry
        self._store = store

    def run_next(self) -> Job | None:
        """Выполнить один Job из очереди. None, если очередь пуста."""
        job = self._store.claim_next()
        if job is None:
            return None
        context = StoreRunContext(job.id, self._store)
        try:
            tool = self._registry.get(job.tool)
            result = tool.run(job.params, context)
        except ToolError as exc:
            self._finish(job, "failed", reason=exc.message, error_code=exc.code)
        except Exception:
            log.exception("Tool упал, Job %s", job.id, extra={"correlation_id": context.correlation_id})
            self._finish(job, "failed", reason="внутренняя ошибка Tool", error_code="internal_error")
        else:
            self._finish(job, result.status, result=result)
        return self._store.get(job.id)

    def _finish(
        self,
        job: Job,
        to_state: str,
        *,
        reason: str | None = None,
        result: ToolResult | None = None,
        error_code: str | None = None,
    ) -> None:
        try:
            self._store.transition(job.id, to_state, reason=reason, result=result, error_code=error_code)
        except InvalidTransition:
            # Пока Tool работал, Job отменили. Итог Tool отбрасывается, Job остается cancelled.
            log.warning("Итог Tool для Job %s отброшен: Job уже %s", job.id, self._store.get(job.id).state)


def recover_after_restart(store: JobStore) -> list[Job]:
    """Вызвать при старте до запуска Dispatcher.

    Tool, которые работали до перезапуска, пропали, ответа от них не будет. Поэтому все Job
    в running уходят в failed с internal_error. Job в queued остаются и выполняются после старта.
    """
    recovered = []
    for job in store.list_by_state("running"):
        recovered.append(store.transition(job.id, "failed", reason=RESTART_REASON, error_code="internal_error"))
    return recovered
