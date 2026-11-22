"""Tool series_summary: процент побед бота по серии реплеев.

Контракт: docs/tool-contract.md, правила подсчета: сценарий 1 в docs/requirements.md.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sweetsoft.core import ParamError, RunContext, SkippedItem, ToolError, ToolResult
from sweetsoft.tools.replays import Outcome, ReplayParseError, ReplayParser

MAX_REPLAYS = 200
MAX_REPLAY_BYTES = 5 * 1024 * 1024


class SeriesSummary:
    name = "series_summary"
    description = "Процент побед бота по серии реплеев: общий, по картам и по соперникам"

    def __init__(self, parser: ReplayParser) -> None:
        self._parser = parser

    def validate(self, params: dict[str, Any]) -> list[ParamError]:
        errors: list[ParamError] = []

        bot_name = params.get("bot_name")
        if "bot_name" not in params:
            errors.append(ParamError("bot_name", "обязательное поле"))
        elif not isinstance(bot_name, str) or not bot_name.strip():
            errors.append(ParamError("bot_name", "ожидается непустая строка"))
        elif bot_name != bot_name.strip():
            # run сравнивает имя с реплеями как есть, " X" там не найдется
            errors.append(ParamError("bot_name", "без пробелов в начале и в конце"))

        replays = params.get("replays")
        if "replays" not in params:
            errors.append(ParamError("replays", "обязательное поле"))
        elif not isinstance(replays, list):
            errors.append(ParamError("replays", "ожидается список реплеев"))
        elif not replays:
            errors.append(ParamError("replays", "нужен хотя бы один реплей"))
        elif len(replays) > MAX_REPLAYS:
            errors.append(ParamError("replays", f"не больше {MAX_REPLAYS} реплеев, передано {len(replays)}"))
        else:
            for index, item in enumerate(replays):
                errors.extend(_validate_replay(index, item))

        return errors

    def run(self, params: dict[str, Any], context: RunContext) -> ToolResult:
        bot_name: str = params["bot_name"]
        games: list[tuple[str, str, Outcome]] = []  # карта, соперник, исход для бота
        skipped: list[SkippedItem] = []
        seen: set[str] = set()

        for index, item in enumerate(params["replays"]):
            if context.is_cancelled():
                break  # Dispatcher все равно отбросит итог отмененного Job

            content = base64.b64decode(item["content_base64"])
            digest = hashlib.sha256(content).hexdigest()
            if digest in seen:
                continue  # дубль отсеиваем молча, матч уже посчитан
            seen.add(digest)

            path = f"params.replays[{index}]"
            try:
                replay = self._parser.parse(content)
            except ReplayParseError as exc:
                skipped.append(SkippedItem(path, exc.reason))
                continue

            # Считаем только матчи 1 на 1, как на AI Arena. В командной игре первый
            # игрок не бот может оказаться союзником, поэтому такие реплеи пропускаем явно
            if len(replay.players) != 2:
                skipped.append(SkippedItem(path, "not_1v1"))
                continue

            bot = next((p for p in replay.players if p.name == bot_name), None)
            if bot is None:
                skipped.append(SkippedItem(path, "bot_not_in_replay"))
                continue
            opponent = next((p.name for p in replay.players if p.name != bot_name), None)
            if opponent is None:
                # Оба игрока с именем bot_name, соперника не определить
                skipped.append(SkippedItem(path, "corrupted_replay"))
                continue

            games.append((replay.map_name, opponent, bot.outcome))

        if not games:
            raise ToolError(
                "no_valid_replays",
                f"Нет реплеев, по которым можно посчитать бота {bot_name!r}: пропущено {len(skipped)}",
            )

        by_map: dict[str, list[Outcome]] = defaultdict(list)
        by_opponent: dict[str, list[Outcome]] = defaultdict(list)
        for map_name, opponent, outcome in games:
            by_map[map_name].append(outcome)
            by_opponent[opponent].append(outcome)

        data: dict[str, Any] = {
            **win_stats([outcome for _, _, outcome in games]),
            "by_map": {key: win_stats(by_map[key]) for key in sorted(by_map)},
            "by_opponent": {key: win_stats(by_opponent[key]) for key in sorted(by_opponent)},
        }
        return ToolResult(status="partial" if skipped else "done", data=data, skipped=skipped)


def win_stats(outcomes: list[Outcome]) -> dict[str, Any]:
    """WinStats из openapi.yaml: total, wins, losses, draws, win_rate."""
    total = len(outcomes)
    wins = outcomes.count("win")
    losses = outcomes.count("loss")
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "draws": total - wins - losses,
        "win_rate": win_rate(wins, total),
    }


def win_rate(wins: int, total: int) -> float:
    """wins / total × 100, округление до 0,1 по правилам арифметики (0,05 вверх)."""
    if total == 0:
        return 0.0
    percent = Decimal(wins * 100) / Decimal(total)
    return float(percent.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _validate_replay(index: int, item: Any) -> list[ParamError]:
    field = f"replays[{index}]"
    if not isinstance(item, dict):
        return [ParamError(field, "ожидается объект с name и content_base64")]

    errors: list[ParamError] = []
    if not isinstance(item.get("name"), str):
        errors.append(ParamError(f"{field}.name", "ожидается строка с именем файла"))

    content = item.get("content_base64")
    if not isinstance(content, str):
        errors.append(ParamError(f"{field}.content_base64", "ожидается строка base64"))
        return errors
    try:
        size = len(base64.b64decode(content, validate=True))
    except (binascii.Error, ValueError):
        errors.append(ParamError(f"{field}.content_base64", "содержимое не в base64"))
        return errors
    if size > MAX_REPLAY_BYTES:
        errors.append(ParamError(field, f"реплей больше 5 МБ: {size} байт"))
    return errors
