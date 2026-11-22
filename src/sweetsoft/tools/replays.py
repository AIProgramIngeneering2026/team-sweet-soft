"""Интерфейс разбора реплея для series_summary.

Tool не знает, чем разбирается файл. Настоящий разборщик подключается адаптером,
в тестах используется подделка. Адаптер сам переводит ошибки своей библиотеки
в ReplayParseError, любое другое исключение считается внутренней ошибкой.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from sweetsoft.core import SkipReason

Outcome = Literal["win", "loss", "draw"]


@dataclass(frozen=True)
class PlayerOutcome:
    name: str
    outcome: Outcome  # draw также для матча по лимиту времени без победителя


@dataclass(frozen=True)
class ParsedReplay:
    map_name: str
    players: tuple[PlayerOutcome, ...]


class ReplayParseError(Exception):
    """Реплей не разобрать. reason попадает в SkippedItem."""

    reason: SkipReason = "corrupted_replay"


class CorruptedReplay(ReplayParseError):
    reason: SkipReason = "corrupted_replay"


class NotSc2Replay(ReplayParseError):
    reason: SkipReason = "not_sc2_replay"


class ReplayParser(Protocol):
    def parse(self, content: bytes) -> ParsedReplay:
        """Разобрать файл реплея или бросить ReplayParseError."""
        ...
