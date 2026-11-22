"""Registry: по имени выдает Tool.

Dispatcher и сервис Job знают только Registry, а не конкретные Tool. Новый Tool
добавляется регистрацией, управляющий код не меняется.
"""

from __future__ import annotations

from sweetsoft.core.errors import UnknownTool
from sweetsoft.core.tool import Tool


class Registry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        """Добавить Tool. Два Tool с одним именем это ошибка в коде, а не в запросе."""
        if not isinstance(tool, Tool):
            raise TypeError(f"{tool!r} не реализует контракт Tool")
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' уже зарегистрирован")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """Tool по имени. Неизвестное имя дает UnknownTool с кодом unknown_tool."""
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownTool(name) from None

    def list(self) -> list[Tool]:
        """Все Tool по имени, для GET /tools."""
        return [self._tools[name] for name in sorted(self._tools)]
