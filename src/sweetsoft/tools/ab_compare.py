"""Tool ab_compare: сравнение двух серий series_summary, старой версии бота и новой.

Контракт: docs/tool-contract.md, правила: сценарий 2 в docs/requirements.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from sweetsoft.core import ParamError, RunContext, ToolError, ToolResult

LOW_SAMPLE = 20
SOURCE_STATES = ("done", "partial")
STAT_KEYS = ("total", "wins", "losses", "draws", "win_rate")


class SourceRun(Protocol):
    """Что ab_compare нужно от Job источника."""

    tool: str
    state: str
    result: ToolResult | None


class RunReader(Protocol):
    """Чтение Job из хранилища. JobStore ядра подходит под этот интерфейс."""

    def get(self, job_id: str) -> SourceRun:
        """Job по id. Если такого нет, KeyError."""
        ...


@dataclass(frozen=True)
class SourceResult:
    state: str  # done или partial
    data: dict[str, Any]  # data из результата series_summary


class AbCompare:
    name = "ab_compare"
    description = "Сравнение двух серий series_summary: разница процента побед в п. п."

    def __init__(self, runs: RunReader) -> None:
        self._runs = runs

    def validate(self, params: dict[str, Any]) -> list[ParamError]:
        errors: list[ParamError] = []
        for key in ("baseline_run_id", "candidate_run_id"):
            value = params.get(key)
            if key not in params:
                errors.append(ParamError(key, "обязательное поле"))
            elif not isinstance(value, str) or not value.strip():
                errors.append(ParamError(key, "ожидается непустая строка с id Job"))
        if not errors and params["baseline_run_id"] == params["candidate_run_id"]:
            errors.append(ParamError("candidate_run_id", "должен отличаться от baseline_run_id"))
        return errors

    def run(self, params: dict[str, Any], context: RunContext) -> ToolResult:
        # Источник проверяем здесь, а не в validate: он мог завершиться или появиться
        # между созданием Job и запуском (docs/tool-contract.md)
        sources: dict[str, SourceResult] = {}
        problems: list[str] = []
        for role in ("baseline", "candidate"):
            source = self._source(role, params[f"{role}_run_id"])
            if isinstance(source, str):
                problems.append(source)
            else:
                sources[role] = source
        if problems:
            raise ToolError("invalid_source_run", "; ".join(problems))

        baseline, candidate = sources["baseline"], sources["candidate"]
        warnings: list[str] = []
        if min(baseline.data["total"], candidate.data["total"]) < LOW_SAMPLE:
            warnings.append("low_sample")
        if "partial" in (baseline.state, candidate.state):
            warnings.append("source_partial")

        data = {
            "overall": _row(_stats(baseline.data), _stats(candidate.data)),
            "by_map": _rows(baseline.data["by_map"], candidate.data["by_map"]),
            "by_opponent": _rows(baseline.data["by_opponent"], candidate.data["by_opponent"]),
        }
        return ToolResult(status="done", data=data, warnings=warnings)

    def _source(self, role: str, run_id: str) -> SourceResult | str:
        """Результат источника или описание, почему он не подошел."""
        prefix = f"{role}_run_id {run_id!r}"
        try:
            run = self._runs.get(run_id)
        except KeyError:
            return f"{prefix}: Job не найден"
        if run.tool != "series_summary":
            return f"{prefix}: Job {run.tool}, а нужен series_summary"
        if run.state not in SOURCE_STATES or run.result is None:
            return f"{prefix}: Job в состоянии {run.state}, а нужен done или partial"
        return SourceResult(run.state, run.result.data)


def _stats(data: dict[str, Any]) -> dict[str, Any]:
    return {key: data[key] for key in STAT_KEYS}


def _row(baseline: dict[str, Any] | None, candidate: dict[str, Any] | None) -> dict[str, Any]:
    """CompareRow из openapi.yaml."""
    only_in_one = baseline is None or candidate is None
    delta = None
    if not only_in_one:
        diff = Decimal(str(candidate["win_rate"])) - Decimal(str(baseline["win_rate"]))
        delta = float(diff)
    return {"baseline": baseline, "candidate": candidate, "delta": delta, "only_in_one": only_in_one}


def _rows(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(baseline.keys() | candidate.keys())
    return {key: _row(baseline.get(key), candidate.get(key)) for key in keys}
