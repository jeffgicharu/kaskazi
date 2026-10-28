"""The Kaskazi API: the customer app's endpoints, the staff console's, and the release's health."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from kaskazi.api.events import Hub
from kaskazi.api.settings import Settings
from kaskazi.assistant.turn import TurnDeps
from kaskazi.controls.vault_store import VaultKey
from kaskazi.corebank.client import CoreBank
from kaskazi.corebank.service import create_app as create_corebank
from kaskazi.db.pool import create_pool
from kaskazi.llm.client import GatewayClient
from kaskazi.llm.routing import load_routes
from kaskazi.release import release_id
from kaskazi.retrieval.corpus import chunk_corpus, corpus_version, read_corpus

API_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cross-Origin-Resource-Policy": "same-origin",
}


@dataclass
class Services:
    settings: Settings
    pool: asyncpg.Pool
    gateway: GatewayClient
    bank: CoreBank
    hub: Hub
    deps: TurnDeps


def current_corpus_version() -> str:
    _, embedding = load_routes()
    return corpus_version(chunk_corpus(read_corpus()), embedding.model)


def create_app(
    settings: Settings,
    *,
    gateway_transport: httpx.AsyncBaseTransport | None = None,
    pool: asyncpg.Pool | None = None,
    run_worker: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        own_pool = pool is None
        db = pool or await create_pool(settings.database_url, max_size=4)
        gateway = GatewayClient(
            settings.gateway_url, settings.gateway_token, transport=gateway_transport
        )
        bank = CoreBank(httpx.ASGITransport(app=create_corebank(db)))
        hub = Hub()
        deps = TurnDeps(
            db,
            gateway,
            bank,
            VaultKey(settings.vault_key),
            settings.secret,
            current_corpus_version(),
        )
        app.state.services = Services(settings, db, gateway, bank, hub, deps)
        worker = None
        if run_worker:
            from kaskazi.api.worker import Worker

            worker = Worker(app.state.services)
            worker.start()
        try:
            yield
        finally:
            if worker is not None:
                await worker.stop()
            await bank.aclose()
            await gateway.aclose()
            if own_pool:
                await db.close()

    app = FastAPI(
        title="Kaskazi API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    @app.middleware("http")
    async def headers_and_log(request: Request, call_next: Any) -> Response:
        started = time.monotonic()
        response: Response = await call_next(request)
        for name, value in API_HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers["X-Release"] = release_id()
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        services: Services | None = getattr(request.app.state, "services", None)
        if services is not None and path not in (
            "/api/healthz",
            "/api/stream",
            "/api/console/stream",
        ):
            try:
                async with services.pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO app.requests (route, status, latency_ms) VALUES ($1, $2, $3)",
                        f"{request.method} {path}",
                        response.status_code,
                        int((time.monotonic() - started) * 1000),
                    )
            except (asyncpg.PostgresError, OSError):
                pass  # monitoring must never break a request
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = {".".join(str(p) for p in e["loc"][1:]): e["msg"] for e in exc.errors()}
        return JSONResponse(
            {
                "type": "invalid_request",
                "title": "Some fields aren't right.",
                "status": 422,
                "fields": fields,
            },
            status_code=422,
            media_type="application/problem+json",
        )

    @app.get("/api/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "release": release_id()}

    from kaskazi.api import console, customer

    app.include_router(customer.router)
    app.include_router(console.router)
    return app


def services(request: Request) -> Services:
    s: Services = request.app.state.services
    return s


def new_id() -> uuid.UUID:
    return uuid.uuid4()
