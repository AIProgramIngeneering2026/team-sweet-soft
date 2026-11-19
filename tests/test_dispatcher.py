"""Тесты Dispatcher и восстановления после перезапуска."""

from __future__ import annotations

from typing import Any, Callable

import pytest
from jobstub import StubJob

from sweetsoft.core import ParamError, RunContext, ToolError, ToolResult
from sweetsoft.core.dispatcher import Dispatcher, recover_after_restart
from sweetsoft.core.errors import InvalidTransition
from sweetsoft.core.registry import Registry
from sweetsoft.core.store import InMemoryJobStore


class FakeTool:
    """Tool, который делает то, что ему передали в behavior, и считает запуски."""

    def __init__(self, name: str, behavior: Callable[[RunContext], ToolResult]) -> None:
        self.name = name
        self.description = f"тестовый Tool {name}"
        self.behavior = behavior
        self.runs = 0

    def validate(self, params: dict[str, Any]) -> list[ParamError]:
        return []

    def run(self, params: dict[str, Any], context: RunContext) -> ToolResult:
        self.runs += 1
        return self.behavior(context)


def done(context: RunContext) -> ToolResult:
    return ToolResult(status="done", data={"total": 10})


def queued_job(store: InMemoryJobStore, job_id: str, tool: str) -> StubJob:
    job = StubJob(id=job_id, tool=tool)
    store.add(job)
    store.transition(job_id, "queued")
    return job


def setup(behavior: Callable[[RunContext], ToolResult] = done):
    tool = FakeTool("series_summary", behavior)
    store = InMemoryJobStore()
    return tool, store, Dispatcher(Registry([tool]), store)


def test_normal_path_from_created_to_done():
    tool, store, dispatcher = setup()
    queued_job(store, "job-1", "series_summary")

    job = dispatcher.run_next()

    assert job.state == "done"
    assert job.result.data == {"total": 10}
    assert [(a, b) for a, b, _ in job.history] == [("created", "queued"), ("queued", "running"), ("running", "done")]
    assert tool.runs == 1


def test_partial_result_gives_partial_job():
    def partial(context):
        return ToolResult(status="partial", data={}, skipped=[{"item": "params.replays[1]", "reason": "corrupted_replay"}])

    _, store, dispatcher = setup(partial)
    queued_job(store, "job-1", "series_summary")

    assert dispatcher.run_next().state == "partial"


def test_tool_error_gives_failed_with_its_code():
    def unavailable(context):
        raise ToolError("external_unavailable", "AI Arena не ответила за 3 попытки")

    _, store, dispatcher = setup(unavailable)
    queued_job(store, "job-1", "series_summary")

    job = dispatcher.run_next()

    assert job.state == "failed"
    assert job.error_code == "external_unavailable"


def test_tool_crash_gives_failed_with_internal_error():
    def crash(context):
        raise RuntimeError("неожиданное падение")

    _, store, dispatcher = setup(crash)
    queued_job(store, "job-1", "series_summary")

    job = dispatcher.run_next()

    assert job.state == "failed"
    assert job.error_code == "internal_error"
    assert job.result is None


def test_tool_missing_from_registry_gives_failed_with_internal_error():
    _, store, dispatcher = setup()
    queued_job(store, "job-1", "local_match")

    job = dispatcher.run_next()

    assert job.state == "failed"
    assert job.error_code == "internal_error"


def test_empty_queue_returns_none():
    _, _, dispatcher = setup()

    assert dispatcher.run_next() is None


def test_result_after_cancel_is_discarded():
    store_ref = {}

    def cancelled_while_running(context):
        store_ref["store"].transition(context.job_id, "cancelled")
        assert context.is_cancelled()
        return ToolResult(status="done", data={"total": 10})

    _, store, dispatcher = setup(cancelled_while_running)
    store_ref["store"] = store
    queued_job(store, "job-1", "series_summary")

    job = dispatcher.run_next()

    assert job.state == "cancelled"
    assert job.result is None


def test_villain_1_two_tools_in_one_job_are_rejected():
    """З1: в одном Job одновременно работают два Tool."""
    tool, store, dispatcher = setup()
    job = queued_job(store, "job-1", "series_summary")

    assert store.claim_next() is job  # первый Tool взял Job
    assert dispatcher.run_next() is None  # второму брать нечего
    with pytest.raises(InvalidTransition):
        store.transition("job-1", "running")  # повторный старт работающего Job

    assert job.state == "running"
    assert tool.runs == 0


def test_villain_2_job_does_not_stay_running_after_restart():
    """З2: сервер перезапустился, а Job так и остался в рабочем состоянии."""
    tool, store, dispatcher = setup()
    running = queued_job(store, "job-1", "series_summary")
    store.claim_next()  # Tool работал, потом сервер перезапустился
    waiting = queued_job(store, "job-2", "series_summary")

    recovered = recover_after_restart(store)

    assert recovered == [running]
    assert running.state == "failed"
    assert running.error_code == "internal_error"
    assert waiting.state == "queued"

    assert dispatcher.run_next() is waiting  # Job из очереди выполняется после старта
    assert waiting.state == "done"
    assert running.state == "failed"
    assert tool.runs == 1
