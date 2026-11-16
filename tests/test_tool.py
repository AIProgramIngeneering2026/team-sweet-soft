"""Тесты контракта результата Tool."""

from __future__ import annotations

import pytest

from sweetsoft.core import ToolResult


def test_done_result_is_valid():
    result = ToolResult(status="done", data={"total": 10})

    assert result.skipped == []
    assert result.warnings == []


def test_partial_result_requires_skipped_list():
    with pytest.raises(ValueError):
        ToolResult(status="partial", data={})


def test_done_result_cannot_have_skipped():
    with pytest.raises(ValueError):
        ToolResult(status="done", data={}, skipped=[{"item": "x", "reason": "y"}])


def test_unknown_status_is_rejected():
    with pytest.raises(ValueError):
        ToolResult(status="ok", data={})
