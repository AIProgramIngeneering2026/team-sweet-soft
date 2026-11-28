"""HTTP-приложение сервиса."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from sweetsoft.storage import create_job_store
from sweetsoft.storage.sql import SchemaVersionMismatch

CORRELATION_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = create_job_store()
    app.state.job_store = store
    try:
        yield
    finally:
        store.close()


app = FastAPI(title="Sweet Soft", lifespan=lifespan)


@app.get(
    "/health",
    operation_id="getHealth",
    tags=["service"],
    summary="Состояние сервиса",
    responses={503: {"description": "БД недоступна"}},
)
def get_health(request: Request) -> JSONResponse:
    correlation_id = request.headers.get("X-Correlation-ID", "")
    # По OpenAPI (CorrelationId) при отсутствующем или неверном заголовке сервис создаёт свой ID.
    if not CORRELATION_ID.fullmatch(correlation_id):
        correlation_id = str(uuid4())
    headers = {"X-Correlation-ID": correlation_id}

    try:
        request.app.state.job_store.check_health()
    except (SQLAlchemyError, SchemaVersionMismatch):
        return JSONResponse(
            status_code=503,
            headers=headers,
            content={
                "error": {
                    "code": "service_unavailable",
                    "message": "БД недоступна",
                    "correlation_id": correlation_id,
                }
            },
        )

    return JSONResponse({"status": "ok", "checks": {"db": "ok"}}, headers=headers)
