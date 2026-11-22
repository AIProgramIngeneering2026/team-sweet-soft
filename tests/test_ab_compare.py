"""Тесты ab_compare: сценарий 2 и контракт из docs/tool-contract.md."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from fake_replays import FakeReplayParser, game, upload
from sweetsoft.core import SkippedItem, Tool, ToolError, ToolResult
from sweetsoft.tools.ab_compare import AbCompare
from sweetsoft.tools.series_summary import SeriesSummary, win_stats

BASE, CAND = "base-1", "cand-1"


@dataclass
class Context:
    correlation_id: str = "test"

    def is_cancelled(self) -> bool:
        return False


@dataclass
class FakeRun:
    tool: str
    state: str
    result: ToolResult | None = None


class FakeRunReader:
    """Подделка хранилища: get как у JobStore, KeyError для неизвестного id."""

    def __init__(self) -> None:
        self.runs: dict[str, FakeRun] = {}

    def get(self, job_id: str) -> FakeRun:
        return self.runs[job_id]


def series(games: dict[tuple[str, str], tuple[int, int]], state: str = "done") -> FakeRun:
    """Готовый Job series_summary. games: (карта, соперник) -> (победы, поражения)."""
    outcomes = {key: ["win"] * w + ["loss"] * l for key, (w, l) in games.items()}
    by_map: dict[str, list] = {}
    by_opponent: dict[str, list] = {}
    for (map_name, opponent), items in outcomes.items():
        by_map.setdefault(map_name, []).extend(items)
        by_opponent.setdefault(opponent, []).extend(items)
    data = {
        **win_stats([o for items in outcomes.values() for o in items]),
        "by_map": {k: win_stats(v) for k, v in by_map.items()},
        "by_opponent": {k: win_stats(v) for k, v in by_opponent.items()},
    }
    skipped = [SkippedItem("params.replays[0]", "corrupted_replay")] if state == "partial" else []
    status = "partial" if state == "partial" else "done"
    return FakeRun("series_summary", state, ToolResult(status=status, data=data, skipped=skipped))


@pytest.fixture
def store() -> FakeRunReader:
    return FakeRunReader()


@pytest.fixture
def tool(store) -> AbCompare:
    return AbCompare(store)


PARAMS = {"baseline_run_id": BASE, "candidate_run_id": CAND}


def test_satisfies_tool_contract(tool):
    assert isinstance(tool, Tool)
    assert tool.name == "ab_compare"


# Критерии сценария 2


def test_delta_plus_10_without_warnings(tool, store):
    store.runs[BASE] = series({("A", "Alpha"): (18, 12)})  # 60,0
    store.runs[CAND] = series({("A", "Alpha"): (21, 9)})  # 70,0

    result = tool.run(PARAMS, Context())

    assert result.status == "done"
    assert result.warnings == []
    overall = result.data["overall"]
    assert overall["delta"] == 10.0
    assert overall["only_in_one"] is False
    assert overall["baseline"] == {"total": 30, "wins": 18, "losses": 12, "draws": 0, "win_rate": 60.0}
    assert overall["candidate"]["win_rate"] == 70.0


def test_low_sample_when_series_has_10_matches(tool, store):
    store.runs[BASE] = series({("A", "Alpha"): (5, 5)})  # 10 матчей, 50,0
    store.runs[CAND] = series({("A", "Alpha"): (21, 9)})  # 70,0

    result = tool.run(PARAMS, Context())

    assert result.status == "done"
    assert result.data["overall"]["delta"] == 20.0
    assert result.warnings == ["low_sample"]


def test_exactly_20_matches_is_not_low_sample(tool, store):
    store.runs[BASE] = series({("A", "Alpha"): (10, 10)})
    store.runs[CAND] = series({("A", "Alpha"): (12, 8)})

    assert tool.run(PARAMS, Context()).warnings == []


def test_map_only_in_one_series(tool, store):
    store.runs[BASE] = series({("A", "Alpha"): (15, 15)})
    store.runs[CAND] = series({("A", "Alpha"): (10, 10), ("B", "Alpha"): (8, 2)})

    by_map = tool.run(PARAMS, Context()).data["by_map"]

    assert by_map["B"]["only_in_one"] is True
    assert by_map["B"]["delta"] is None
    assert by_map["B"]["baseline"] is None
    assert by_map["B"]["candidate"]["total"] == 10
    assert by_map["A"]["only_in_one"] is False
    assert by_map["A"]["delta"] == 0.0


def test_delta_by_opponent(tool, store):
    store.runs[BASE] = series({("A", "Alpha"): (10, 10), ("A", "Beta"): (5, 15)})
    store.runs[CAND] = series({("A", "Alpha"): (12, 8), ("A", "Gamma"): (4, 16)})

    by_opponent = tool.run(PARAMS, Context()).data["by_opponent"]

    assert by_opponent["Alpha"]["delta"] == 10.0
    assert by_opponent["Beta"]["only_in_one"] is True
    assert by_opponent["Gamma"]["only_in_one"] is True
    assert by_opponent["Gamma"]["baseline"] is None


def test_delta_has_no_float_noise(tool, store):
    store.runs[BASE] = series({("A", "Alpha"): (2, 1)})  # 66,7
    store.runs[CAND] = series({("A", "Alpha"): (7, 3)})  # 70,0

    assert tool.run(PARAMS, Context()).data["overall"]["delta"] == 3.3


def test_source_partial_warning(tool, store):
    store.runs[BASE] = series({("A", "Alpha"): (18, 12)}, state="partial")
    store.runs[CAND] = series({("A", "Alpha"): (21, 9)})

    result = tool.run(PARAMS, Context())

    assert result.status == "done"
    assert result.warnings == ["source_partial"]


def test_both_warnings(tool, store):
    store.runs[BASE] = series({("A", "Alpha"): (3, 2)}, state="partial")
    store.runs[CAND] = series({("A", "Alpha"): (21, 9)})

    assert tool.run(PARAMS, Context()).warnings == ["low_sample", "source_partial"]


# invalid_source_run


@pytest.mark.parametrize("state", ["created", "queued", "running", "failed", "cancelled"])
def test_baseline_in_wrong_state(tool, store, state):
    store.runs[BASE] = FakeRun("series_summary", state)
    store.runs[CAND] = series({("A", "Alpha"): (21, 9)})

    with pytest.raises(ToolError) as exc:
        tool.run(PARAMS, Context())

    assert exc.value.code == "invalid_source_run"
    assert BASE in exc.value.message
    assert state in exc.value.message


def test_baseline_not_found(tool, store):
    store.runs[CAND] = series({("A", "Alpha"): (21, 9)})

    with pytest.raises(ToolError) as exc:
        tool.run(PARAMS, Context())

    assert exc.value.code == "invalid_source_run"
    assert BASE in exc.value.message
    assert "не найден" in exc.value.message


def test_source_from_other_tool(tool, store):
    store.runs[BASE] = series({("A", "Alpha"): (18, 12)})
    store.runs[CAND] = FakeRun("arena_sync", "done", ToolResult(status="done", data={"fetched": 1, "new_matches": 1}))

    with pytest.raises(ToolError) as exc:
        tool.run(PARAMS, Context())

    assert exc.value.code == "invalid_source_run"
    assert CAND in exc.value.message
    assert "arena_sync" in exc.value.message


def test_both_sources_bad_are_reported_together(tool, store):
    store.runs[BASE] = FakeRun("series_summary", "failed")

    with pytest.raises(ToolError) as exc:
        tool.run(PARAMS, Context())

    assert BASE in exc.value.message
    assert CAND in exc.value.message


# validate: неверные параметры с указанием поля


def test_valid_params_pass(tool):
    assert tool.validate(PARAMS) == []


def test_missing_ids(tool):
    assert [e.field for e in tool.validate({})] == ["baseline_run_id", "candidate_run_id"]


@pytest.mark.parametrize("value", ["", "  ", 42, None])
def test_bad_id(tool, value):
    errors = tool.validate({"baseline_run_id": BASE, "candidate_run_id": value})

    assert [e.field for e in errors] == ["candidate_run_id"]


def test_same_ids(tool):
    errors = tool.validate({"baseline_run_id": BASE, "candidate_run_id": BASE})

    assert [e.field for e in errors] == ["candidate_run_id"]


def test_does_not_check_source_existence_in_validate(tool, store):
    # Источника еще нет, но validate проходит: проверка в run
    assert tool.validate({"baseline_run_id": "nope-1", "candidate_run_id": "nope-2"}) == []


# Вместе с series_summary


def test_compares_real_series_summary_results(tool, store):
    summary = SeriesSummary(FakeReplayParser())

    def summarize(wins: int, losses: int) -> FakeRun:
        contents = [game("A", "Alpha", "win") for _ in range(wins)] + [game("A", "Alpha", "loss") for _ in range(losses)]
        result = summary.run({"bot_name": "X", "replays": [upload(c) for c in contents]}, Context())
        return FakeRun("series_summary", result.status, result)

    store.runs[BASE] = summarize(18, 12)
    store.runs[CAND] = summarize(21, 9)

    result = tool.run(PARAMS, Context())

    assert result.data["overall"]["delta"] == 10.0
    assert result.data["by_map"]["A"]["delta"] == 10.0
    assert result.warnings == []
