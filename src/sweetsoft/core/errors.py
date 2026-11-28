"""Ошибки ядра.

У каждой ошибки есть `code` из нашего контракта (requirements.md, openapi.yaml).
HTTP слой на практике 4 переводит их в единый формат ошибки и HTTP код.
"""


class CoreError(Exception):
    """Базовая ошибка ядра с кодом из контракта."""

    code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidTransition(CoreError):
    """Переход Job запрещен жизненным циклом. В API это 409."""

    code = "invalid_transition"

    def __init__(self, job_id: str, from_state: str, to_state: str) -> None:
        super().__init__(f"Job {job_id}: переход {from_state} -> {to_state} запрещен")
        self.job_id = job_id
        self.from_state = from_state
        self.to_state = to_state


class IdempotencyKeyConflict(CoreError):
    """Ключ уже привязан к запросу с другим хешем. В API это 409."""

    code = "idempotency_key_conflict"

    def __init__(self, job_id: str) -> None:
        super().__init__(
            f"Ключ уже использован для другого запроса, Job '{job_id}'. "
            "Для нового задания нужен новый ключ"
        )
        self.job_id = job_id


class UnknownTool(CoreError):
    """Tool с таким именем нет в Registry. В API это 422."""

    code = "unknown_tool"

    def __init__(self, name: str) -> None:
        super().__init__(f"Tool '{name}' не найден, список доступных: GET /tools")
        self.name = name
