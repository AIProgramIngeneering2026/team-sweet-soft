import asyncio

from httpx import ASGITransport, AsyncClient, Response

from sweetsoft.api import app


def request_health(headers: dict[str, str] | None = None) -> Response:
    async def request() -> Response:
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                return await client.get("/health", headers=headers)

    return asyncio.run(request())
