"""Контракт Tool. Описание словами: docs/tool-contract.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, get_args, runtime_checkable


@dataclass(frozen=True)
class ParamError:
    """Ошибка в одном параметре."""

    field: str  # какое поле не так, например "bot_name" или "replays[3]"
    message: str  # что именно не так


SkipReason = Literal["corrupted_replay", "not_sc2_replay", "bot_not_in_replay"]
SKIP_REASONS: tuple[str, ...] = get_args(SkipReason)


@dataclass(frozen=True)
class SkippedItem:
    """Что пропустили при partial и почему. В openapi.yaml это SkippedItem."""

    item: str  # что пропущено, путь как в FieldError.field, например "params.replays[3]"
    reason: SkipReason  # почему пропущено

    def __post_init__(self) -> None:
        if self.reason not in SKIP_REASONS:
            raise ValueError(f"неизвестная причина пропуска: {self.reason!r}")


@dataclass(frozen=True)
class ToolResult:
    """Что вернул Tool. По status Dispatcher переводит Job в done или partial."""

    status: Literal["done", "partial"]
    data: dict[str, Any]  # сам результат, у каждого Tool свой формат
    skipped: list[SkippedItem] = field(default_factory=list)  # что пропустили и почему, только для partial
    warnings: list[str] = field(default_factory=list)  # предупреждения, которые не меняют статус

    def __post_init__(self) -> None:
        if self.status not in ("done", "partial"):
            raise ValueError(f"неизвестный status результата: {self.status!r}")
        for entry in self.skipped:
            if not isinstance(entry, SkippedItem):
                raise TypeError(f"элемент skipped должен быть SkippedItem, а не {type(entry).__name__}")
        if self.status == "partial" and not self.skipped:
            raise ValueError("partial без списка пропущенного")
        if self.status == "done" and self.skipped:
            raise ValueError("done не может содержать пропущенное, это partial")


class ToolError(Exception):
    """Tool не смог получить результат. Job переходит в failed с этим кодом."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@runtime_checkable
class RunContext(Protocol):
    correlation_id: str

    def is_cancelled(self) -> bool:
        """Отменили ли Job. Tool проверяет это в безопасных точках."""
        ...


@runtime_checkable
class Tool(Protocol):
    name: str  # имя в Registry и в POST /runs
    description: str  # описание для GET /tools

    def validate(self, params: dict[str, Any]) -> list[ParamError]:
        """Проверка параметров по смыслу. Пустой список значит, что все в порядке."""
        ...

    def run(self, params: dict[str, Any], context: RunContext) -> ToolResult:
        """Выполнить работу. Вызывается только с параметрами, которые прошли validate."""
        ...
