"""Подделка разбора реплеев для тестов series_summary.

Вместо .SC2Replay в content лежит JSON с картой и игроками.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from sweetsoft.tools.replays import CorruptedReplay, NotSc2Replay, ParsedReplay, PlayerOutcome

CORRUPTED = b"\x00\x01 not a replay"
NOT_SC2 = json.dumps({"not_sc2": True}).encode()


class FakeReplayParser:
    def __init__(self) -> None:
        self.calls = 0

    def parse(self, content: bytes) -> ParsedReplay:
        self.calls += 1
        try:
            raw = json.loads(content)
        except ValueError as exc:
            raise CorruptedReplay("не JSON") from exc
        if raw.get("not_sc2"):
            raise NotSc2Replay("не SC2")
        players = tuple(PlayerOutcome(name, outcome) for name, outcome in raw["players"])
        return ParsedReplay(map_name=raw["map"], players=players)


_counter = 0


def game(map_name: str, opponent: str, outcome: str, bot: str = "X") -> bytes:
    """Содержимое реплея: бот сыграл с opponent на map_name с исходом outcome."""
    global _counter
    _counter += 1  # у каждого матча свой хеш, иначе одинаковые матчи считались бы дублями
    other = {"win": "loss", "loss": "win", "draw": "draw"}[outcome]
    return json.dumps({"map": map_name, "players": [[bot, outcome], [opponent, other]], "n": _counter}).encode()


def upload(content: bytes, name: str = "game.SC2Replay") -> dict[str, Any]:
    return {"name": name, "content_base64": base64.b64encode(content).decode()}


def scenario_1_set() -> list[bytes]:
    """Тестовый набор из сценария 1: 10 матчей бота X."""
    return (
        [game("A", "Alpha", "win") for _ in range(3)]
        + [game("A", "Alpha", "loss")]
        + [game("B", "Alpha", "win")]
        + [game("B", "Beta", "win") for _ in range(3)]
        + [game("B", "Beta", "loss"), game("B", "Beta", "draw")]
    )
