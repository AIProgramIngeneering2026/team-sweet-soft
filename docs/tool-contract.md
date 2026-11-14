# Контракт Tool

Tool (исполнитель) это модуль, который делает работу одного Job. Dispatcher находит Tool в Registry по имени и вызывает его через этот контракт. Конкретных Tools Dispatcher не знает, поэтому новый Tool добавляется без правки управляющей логики: пишем класс и регистрируем его в Registry.

## Интерфейс

```python
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ParamError:
    field: str     # какое поле не так, например "bot_name" или "replays[3]"
    message: str   # что именно не так


@dataclass(frozen=True)
class ToolResult:
    status: Literal["done", "partial"]
    data: dict[str, Any]                                       # сам результат, у каждого Tool свой формат
    skipped: list[dict[str, str]] = field(default_factory=list)  # что пропустили и почему, только для partial
    warnings: list[str] = field(default_factory=list)          # предупреждения, которые не меняют статус


class ToolError(Exception):
    """Tool не смог получить результат. Job переходит в failed с этим кодом."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message


class RunContext(Protocol):
    correlation_id: str

    def is_cancelled(self) -> bool:
        """Отменили ли Job. Tool проверяет это в безопасных точках."""


class Tool(Protocol):
    name: str          # имя в Registry и в POST /runs
    description: str   # описание для GET /tools

    def validate(self, params: dict[str, Any]) -> list[ParamError]:
        """Проверка параметров по смыслу. Пустой список значит, что все в порядке."""

    def run(self, params: dict[str, Any], context: RunContext) -> ToolResult:
        """Выполнить работу. Вызывается только с параметрами, которые прошли validate."""
```

## Что получает Tool

`validate` получает `params` из тела `POST /runs` как есть. `run` получает те же `params`, уже проверенные, и `context`: `correlation_id` для логов и способ узнать, что Job отменили. Доступ к хранилищу и к адаптеру AI Arena Tool получает при создании, через конструктор. Сам Tool не знает, PostgreSQL там или fake.

## Что возвращает Tool

`run` возвращает `ToolResult`. По нему Dispatcher переводит Job:

| Что вернул Tool | Состояние Job |
| --- | --- |
| `ToolResult(status="done")` | `done` |
| `ToolResult(status="partial")`, в `skipped` список пропущенного | `partial` |
| Исключение `ToolError(code, message)` | `failed`, в `error_code` код из ошибки |
| Любое другое исключение | `failed` с кодом `internal_error`, подробности только в логе |

Состояние Job Tool сам не меняет и в историю не пишет, это делает Dispatcher через сервис Job.

## Какими ошибками отказывает Tool

| Код | Когда | Где появляется |
| --- | --- | --- |
| `invalid_params` | `validate` вернул непустой список | Ответ 422 на `POST /runs`, в ответе список полей с ошибками. Job не создается |
| `external_unavailable` | AI Arena не ответила за 3 попытки (таймаут или 5xx) | Job в `failed` |
| `external_bad_response` | AI Arena ответила не JSON или без нужных полей | Job в `failed` |
| `invalid_source_run` | `ab_compare` получил id Job, который не подходит как источник | Job в `failed` |
| `no_valid_replays` | `series_summary`: все реплеи оказались битыми или без нужного бота | Job в `failed` |
| `internal_error` | Непредвиденное исключение внутри Tool | Job в `failed` |

## Кто проверяет параметры

Проверяем в два слоя, и оба до создания Job. Сначала HTTP API сверяет тело запроса с OpenAPI: есть ли поле `tool`, объект ли `params`, не слишком ли большое тело. Если нет, ответ 422 `invalid_params`.

Потом сервис Job ищет Tool в Registry. Если имени там нет, ответ 422 `unknown_tool`. Если есть, вызывается `validate(params)`, и Tool проверяет параметры по смыслу: обязательные поля, типы, допустимые значения, лимиты. Нашлись ошибки, значит ответ 422 `invalid_params` со списком полей, Job не создается.

Внутри `run` параметры повторно не проверяются. Если данные оказались плохими уже во время работы (битый реплей, кривой ответ AI Arena), это не `invalid_params`, а `partial` или `failed` по таблице выше.

## Tools проекта через контракт

### series_summary

Считает процент побед бота по серии реплеев: общий, по картам и по соперникам.

| Часть контракта | Значение |
| --- | --- |
| Параметры | `bot_name`: строка, обязательна, не пустая. `replays`: список реплеев `.SC2Replay`, обязателен, от 1 до 200 штук, каждый не больше 5 МБ |
| `validate` отклоняет | Нет `bot_name` или он пустой. Нет `replays`, список пустой или длиннее 200. Реплей больше 5 МБ. В каждом случае `field` указывает на поле, например `replays[3]` |
| Результат `done` | `total`, `wins`, `losses`, `draws`, `win_rate`, `by_map`, `by_opponent`. Правила подсчета в сценарии 1 [requirements.md](requirements.md) |
| Результат `partial` | Часть реплеев битые, не от SC2 или в них нет бота `bot_name`. Они перечислены в `skipped` с причиной, цифры посчитаны по остальным. Дубли (одинаковый хеш файла) отсеиваются молча и считаются один раз |
| Ошибки `run` | `no_valid_replays`, если все реплеи пропущены и считать нечего |
| Отмена | Проверяет `context.is_cancelled()` между реплеями |
| Внешние вызовы | Нет |

### arena_sync

Забирает результаты матчей бота на ладдере AI Arena и сохраняет новые.

| Часть контракта | Значение |
| --- | --- |
| Параметры | `bot_id`: целое число, обязательно, больше 0. Это id бота на AI Arena |
| `validate` отклоняет | Нет `bot_id`, не число или не больше 0 |
| Результат `done` | `fetched`: сколько матчей вернула AI Arena, `new_matches`: сколько из них новых. Пустой список от AI Arena это `done` с нулями, не ошибка |
| Результат `partial` | Не используется: матчи либо получены целиком, либо нет |
| Ошибки `run` | `external_unavailable`, если адаптер не получил ответ за 3 попытки. `external_bad_response`, если ответ не разобрать |
| Отмена | Проверяет `context.is_cancelled()` между запросами к AI Arena |
| Внешние вызовы | AI Arena через адаптер. Таймаут, повторы и токен скрыты в адаптере, Tool получает от него список матчей или ошибку |

### ab_compare

Сравнивает две готовые серии `series_summary`: старую версию бота и новую.

| Часть контракта | Значение |
| --- | --- |
| Параметры | `baseline_run_id` и `candidate_run_id`: строки, обязательны, разные |
| `validate` отклоняет | Нет одного из id или они совпадают |
| Результат `done` | Значения обеих серий и `delta` в процентных пунктах: общая, по картам, по соперникам. Предупреждения `low_sample` (меньше 20 матчей) и `source_partial` (источник в `partial`) идут в `warnings` |
| Результат `partial` | Не используется |
| Ошибки `run` | `invalid_source_run`, если Job источника нет, он не `series_summary` или не в `done` / `partial` |
| Отмена | Работает быстро, отмену не проверяет. Если Job отменили во время работы, Dispatcher отбросит результат |
| Внешние вызовы | Нет, читает результаты из хранилища |

Почему существование источника проверяется в `run`, а не в `validate`: Job источника может завершиться или появиться между созданием Job и его запуском, поэтому проверять имеет смысл в момент работы. Так же описано в сценарии 2.
