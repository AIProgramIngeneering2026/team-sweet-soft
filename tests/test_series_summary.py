"""Тесты series_summary: сценарий 1 и контракт из docs/tool-contract.md."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from fake_replays import CORRUPTED, NOT_SC2, FakeReplayParser, game, scenario_1_set, upload
from sweetsoft.core import ParamError, SkippedItem, Tool, ToolError
from sweetsoft.tools.series_summary import MAX_REPLAY_BYTES, SeriesSummary, win_rate


@dataclass
class Context:
    correlation_id: str = "test"
    cancel_after: int | None = None  # после скольких проверок Job считается отмененным
    checks: int = 0

    def is_cancelled(self) -> bool:
        self.checks += 1
        return self.cancel_after is not None and self.checks > self.cancel_after


def params(*contents: bytes, bot_name: str = "X") -> dict:
    return {"bot_name": bot_name, "replays": [upload(c) for c in contents]}


@pytest.fixture
def parser() -> FakeReplayParser:
    return FakeReplayParser()


@pytest.fixture
def tool(parser) -> SeriesSummary:
    return SeriesSummary(parser)


def fields(errors: list[ParamError]) -> list[str]:
    return [e.field for e in errors]


def test_satisfies_tool_contract(tool):
    assert isinstance(tool, Tool)
    assert tool.name == "series_summary"


# Подсчет по сценарию 1


def test_scenario_1_totals(tool):
    result = tool.run(params(*scenario_1_set()), Context())

    assert result.status == "done"
    assert result.skipped == []
    assert result.warnings == []
    data = result.data
    assert (data["total"], data["wins"], data["losses"], data["draws"]) == (10, 7, 2, 1)
    assert data["win_rate"] == 70.0


def test_scenario_1_by_map_and_opponent(tool):
    data = tool.run(params(*scenario_1_set()), Context()).data

    assert data["by_map"] == {
        "A": {"total": 4, "wins": 3, "losses": 1, "draws": 0, "win_rate": 75.0},
        "B": {"total": 6, "wins": 4, "losses": 1, "draws": 1, "win_rate": 66.7},
    }
    assert data["by_opponent"] == {
        "Alpha": {"total": 5, "wins": 4, "losses": 1, "draws": 0, "win_rate": 80.0},
        "Beta": {"total": 5, "wins": 3, "losses": 1, "draws": 1, "win_rate": 60.0},
    }


def test_time_limit_draw_counts_as_draw(tool):
    # Разборщик отдает матч по лимиту времени без победителя как draw
    data = tool.run(params(*scenario_1_set(), game("A", "Alpha", "draw")), Context()).data

    assert (data["total"], data["wins"], data["draws"]) == (11, 7, 2)


@pytest.mark.parametrize(
    ("wins", "total", "expected"),
    [(7, 10, 70.0), (4, 6, 66.7), (1, 3, 33.3), (1, 16, 6.3), (0, 5, 0.0), (5, 5, 100.0)],
)
def test_win_rate_rounding(wins, total, expected):
    assert win_rate(wins, total) == expected


# partial, дубли, no_valid_replays


def test_corrupted_replay_gives_partial(tool):
    result = tool.run(params(game("A", "Alpha", "win"), CORRUPTED), Context())

    assert result.status == "partial"
    assert result.skipped == [SkippedItem("params.replays[1]", "corrupted_replay")]
    assert (result.data["total"], result.data["wins"], result.data["win_rate"]) == (1, 1, 100.0)


def test_skip_reasons(tool):
    result = tool.run(
        params(game("A", "Alpha", "win"), NOT_SC2, game("A", "Alpha", "win", bot="Other"), CORRUPTED),
        Context(),
    )

    assert result.status == "partial"
    assert result.skipped == [
        SkippedItem("params.replays[1]", "not_sc2_replay"),
        SkippedItem("params.replays[2]", "bot_not_in_replay"),
        SkippedItem("params.replays[3]", "corrupted_replay"),
    ]
    assert result.data["total"] == 1


def test_duplicates_are_counted_once_silently(tool, parser):
    same = game("A", "Alpha", "win")
    result = tool.run(params(same, same, game("B", "Beta", "loss"), same), Context())

    assert result.status == "done"
    assert result.skipped == []
    assert result.data["total"] == 2
    assert parser.calls == 2


def test_all_replays_skipped_is_no_valid_replays(tool):
    with pytest.raises(ToolError) as exc:
        tool.run(params(CORRUPTED, game("A", "Alpha", "win", bot="Other")), Context())

    assert exc.value.code == "no_valid_replays"


def test_checks_cancel_between_replays(tool, parser):
    tool.run(params(*scenario_1_set()), Context(cancel_after=3))

    assert parser.calls == 3


# validate: неверные параметры с указанием поля


def test_valid_params_pass(tool):
    assert tool.validate(params(*scenario_1_set())) == []


def test_missing_bot_name_and_empty_replays(tool):
    # Критерий сценария 1: нет bot_name или список реплеев пуст
    assert fields(tool.validate({"replays": []})) == ["bot_name", "replays"]


@pytest.mark.parametrize("bot_name", ["", "   ", 42, None, " X", "X ", "\tX"])
def test_bad_bot_name(tool, bot_name):
    p = params(game("A", "Alpha", "win"))
    p["bot_name"] = bot_name

    assert fields(tool.validate(p)) == ["bot_name"]


@pytest.mark.parametrize("replays", ["not a list", {"0": "x"}, None])
def test_replays_not_a_list(tool, replays):
    assert fields(tool.validate({"bot_name": "X", "replays": replays})) == ["replays"]


def test_missing_replays(tool):
    assert fields(tool.validate({"bot_name": "X"})) == ["replays"]


def test_more_than_200_replays(tool):
    p = {"bot_name": "X", "replays": [upload(b"x")] * 201}

    assert fields(tool.validate(p)) == ["replays"]


def test_exactly_200_replays_pass(tool):
    assert tool.validate({"bot_name": "X", "replays": [upload(b"x")] * 200}) == []


def test_replay_over_5_mb_points_to_its_index(tool):
    contents = [game("A", "Alpha", "win") for _ in range(3)] + [b"\0" * (MAX_REPLAY_BYTES + 1)]

    assert fields(tool.validate(params(*contents))) == ["replays[3]"]


def test_replay_of_exactly_5_mb_passes(tool):
    assert tool.validate(params(b"\0" * MAX_REPLAY_BYTES)) == []


def test_bad_replay_items(tool):
    p = {
        "bot_name": "X",
        "replays": [
            upload(b"ok"),
            {"name": "a.SC2Replay", "content_base64": "@@@ не base64"},
            "not an object",
            {"content_base64": "b2s="},
            {"name": "b.SC2Replay"},
        ],
    }

    assert fields(tool.validate(p)) == [
        "replays[1].content_base64",
        "replays[2]",
        "replays[3].name",
        "replays[4].content_base64",
    ]
