"""Тесты Registry: Tool по имени и ошибка «неизвестная операция»."""

from __future__ import annotations

from typing import Any

import pytest

from sweetsoft.core import ParamError, RunContext, ToolResult
from sweetsoft.core.errors import UnknownTool
from sweetsoft.core.registry import Registry


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"тестовый Tool {name}"

    def validate(self, params: dict[str, Any]) -> list[ParamError]:
        return []

    def run(self, params: dict[str, Any], context: RunContext) -> ToolResult:
        return ToolResult(status="done", data={})


def test_get_returns_registered_tool():
    tool = FakeTool("series_summary")
    registry = Registry([tool])

    assert registry.get("series_summary") is tool


def test_unknown_tool_is_rejected_with_code_and_name():
    registry = Registry([FakeTool("series_summary")])

    with pytest.raises(UnknownTool) as exc:
        registry.get("replay_stat")

    assert exc.value.code == "unknown_tool"
    assert "replay_stat" in exc.value.message


def test_duplicate_name_is_rejected():
    registry = Registry([FakeTool("series_summary")])

    with pytest.raises(ValueError):
        registry.register(FakeTool("series_summary"))


def test_object_without_tool_contract_is_rejected():
    with pytest.raises(TypeError):
        Registry().register(object())


def test_list_returns_tools_sorted_by_name():
    registry = Registry([FakeTool("series_summary"), FakeTool("ab_compare")])

    assert [tool.name for tool in registry.list()] == ["ab_compare", "series_summary"]
