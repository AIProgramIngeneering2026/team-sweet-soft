"""Тесты контракта результата Tool."""

from __future__ import annotations

import pytest

from sweetsoft.core import SkippedItem, ToolResult


def test_done_result_is_valid():
    result = ToolResult(status="done", data={"total": 10})

    assert result.skipped == []
    assert result.warnings == []


def test_partial_result_requires_skipped_list():
    with pytest.raises(ValueError):
        ToolResult(status="partial", data={})


def test_done_result_cannot_have_skipped():
    with pytest.raises(ValueError):
        ToolResult(status="done", data={}, skipped=[SkippedItem(item="x", reason="corrupted_replay")])


def test_unknown_status_is_rejected():
    with pytest.raises(ValueError):
        ToolResult(status="ok", data={})


def test_partial_result_with_valid_skipped():
    result = ToolResult(
        status="partial",
        data={},
        skipped=[SkippedItem(item="params.replays[3]", reason="corrupted_replay")]
    )
    assert len(result.skipped) == 1
    assert result.skipped[0].item == "params.replays[3]"
    assert result.skipped[0].reason == "corrupted_replay"


def test_skipped_item_with_invalid_reason():
    with pytest.raises(ValueError, match="неизвестная причина пропуска"):
        SkippedItem(item="x", reason="unknown_reason")  # type: ignore

