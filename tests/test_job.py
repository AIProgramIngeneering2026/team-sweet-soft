"""Тесты жизненного цикла Job: обычный путь, запрещенные переходы, злодейства З3-З6."""

from __future__ import annotations

import itertools

import pytest

from sweetsoft.core import (
    ALLOWED_TRANSITIONS,
    InvalidTransition,
    Job,
    JobState,
    ToolResult,
)

CID = "test-cid"

DONE_RESULT = ToolResult(status="done", data={"total": 10, "wins": 7})
PARTIAL_RESULT = ToolResult(
    status="partial",
    data={"total": 9, "wins": 6},
    skipped=[{"item": "replays[3]", "reason": "corrupted_replay"}],
)


def make_job(state: JobState) -> Job:
    """Job, честно доведенный до нужного состояния разрешенными переходами."""
    job = Job.create("series_summary", {"bot_name": "X", "replays": []}, CID)
    if state is JobState.CREATED:
        return job
    if state is JobState.CANCELLED:
        job.cancel(CID)
        return job
    job.enqueue(CID)
    if state is JobState.QUEUED:
        return job
    job.start(CID)
    if state is JobState.RUNNING:
        return job
    if state is JobState.DONE:
        job.complete(DONE_RESULT, CID)
    elif state is JobState.PARTIAL:
        job.complete(PARTIAL_RESULT, CID)
    elif state is JobState.FAILED:
        job.fail("external_unavailable", "AI Arena не отвечает", CID)
    return job


# Как вызвать переход в каждое состояние. Для done и partial нужен результат Tool.
MOVES = {
    JobState.QUEUED: lambda job: job.enqueue(CID),
    JobState.RUNNING: lambda job: job.start(CID),
    JobState.DONE: lambda job: job.complete(DONE_RESULT, CID),
    JobState.PARTIAL: lambda job: job.complete(PARTIAL_RESULT, CID),
    JobState.FAILED: lambda job: job.fail("internal_error", "упал", CID),
    JobState.CANCELLED: lambda job: job.cancel(CID),
}


def assert_rejected(job: Job, move) -> None:
    """Переход отклонен явной ошибкой, а состояние, история и результат не изменились."""
    state, history, result = job.state, list(job.history), job.result
    with pytest.raises(InvalidTransition) as exc:
        move(job)
    assert exc.value.code == "invalid_transition"
    assert job.state is state
    assert job.history == history
    assert job.result is result


# Обычный путь


def test_create_job_is_created_with_first_history_record():
    job = Job.create("series_summary", {"bot_name": "X"}, CID)

    assert job.state is JobState.CREATED
    assert len(job.history) == 1
    assert job.history[0].from_state is None
    assert job.history[0].to_state is JobState.CREATED
    assert job.history[0].correlation_id == CID


def test_happy_path_created_to_done():
    job = Job.create("series_summary", {"bot_name": "X"}, CID)
    job.enqueue(CID)
    job.start(CID)
    job.complete(DONE_RESULT, CID)

    assert job.state is JobState.DONE
    assert job.result == DONE_RESULT
    assert [t.to_state for t in job.history] == [
        JobState.CREATED,
        JobState.QUEUED,
        JobState.RUNNING,
        JobState.DONE,
    ]
    assert all(t.correlation_id == CID for t in job.history)


def test_partial_result_moves_job_to_partial():
    job = make_job(JobState.RUNNING)
    job.complete(PARTIAL_RESULT, CID)

    assert job.state is JobState.PARTIAL
    assert job.result.skipped == PARTIAL_RESULT.skipped


def test_fail_keeps_error_code_and_reason():
    job = make_job(JobState.RUNNING)
    job.fail("external_unavailable", "AI Arena не отвечает", CID)

    assert job.state is JobState.FAILED
    assert job.error_code == "external_unavailable"
    assert job.history[-1].reason == "AI Arena не отвечает"
    assert job.result is None


@pytest.mark.parametrize("state", [JobState.CREATED, JobState.QUEUED, JobState.RUNNING])
def test_cancel_allowed_from_not_finished_states(state):
    job = make_job(state)
    job.cancel(CID)

    assert job.state is JobState.CANCELLED
    assert job.result is None
    assert job.history[-1].from_state is state
    assert job.history[-1].reason


# Таблица переходов целиком


ALL_PAIRS = list(itertools.product(JobState, MOVES))
FORBIDDEN = [
    (src, dst) for src, dst in ALL_PAIRS if dst not in ALLOWED_TRANSITIONS[src]
]
ALLOWED = [(src, dst) for src, dst in ALL_PAIRS if dst in ALLOWED_TRANSITIONS[src]]


@pytest.mark.parametrize(("src", "dst"), ALLOWED, ids=lambda s: s.value)
def test_allowed_transition_works(src, dst):
    job = make_job(src)
    MOVES[dst](job)

    assert job.state is dst
    assert job.history[-1].from_state is src


@pytest.mark.parametrize(("src", "dst"), FORBIDDEN, ids=lambda s: s.value)
def test_forbidden_transition_is_rejected(src, dst):
    assert_rejected(make_job(src), MOVES[dst])


def test_terminal_states_have_no_way_out():
    for state in (JobState.DONE, JobState.PARTIAL, JobState.FAILED, JobState.CANCELLED):
        job = make_job(state)
        assert job.is_terminal
        for move in MOVES.values():
            assert_rejected(job, move)


# Злодейства с пары (tasks/p3_job/README.md)


def test_villain_3_cancelled_job_cannot_become_done():
    """З3: Job отменили, потом Tool дорабатывает и пытается записать done."""
    job = make_job(JobState.RUNNING)
    job.cancel(CID)

    assert_rejected(job, lambda j: j.complete(DONE_RESULT, CID))
    assert job.state is JobState.CANCELLED
    assert job.result is None


def test_villain_4_failed_job_cannot_become_done():
    """З4: Job уже в failed, потом приходит результат и пытается сделать done."""
    job = make_job(JobState.FAILED)

    assert_rejected(job, lambda j: j.complete(DONE_RESULT, CID))
    assert job.error_code == "external_unavailable"


@pytest.mark.parametrize("state", [JobState.CREATED, JobState.QUEUED])
def test_villain_5_done_without_running(state):
    """З5: Job становится done, не побывав в running."""
    job = make_job(state)

    assert_rejected(job, lambda j: j.complete(DONE_RESULT, CID))
    assert JobState.RUNNING not in [t.to_state for t in job.history]


@pytest.mark.parametrize("state", [JobState.DONE, JobState.PARTIAL])
def test_villain_6_finished_job_cannot_run_again(state):
    """З6: готовый Job снова идет в работу (повторный start или enqueue)."""
    job = make_job(state)

    assert_rejected(job, lambda j: j.start(CID))
    assert_rejected(job, lambda j: j.enqueue(CID))

