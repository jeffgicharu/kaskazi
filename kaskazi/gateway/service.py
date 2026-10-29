"""The model gateway: the only process that talks to model providers.

It holds the provider keys, applies the spending caps, refuses to send unmasked personal data,
chooses the provider for each call (with fallback), and records every attempt's metadata. It
knows nothing about banking: requests arrive in the neutral contract from the API.
"""

from __future__ import annotations

import hmac
import json
import os
import time
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from kaskazi.controls.pii import find_unmasked
from kaskazi.db.pool import create_pool
from kaskazi.gateway.ledger import Caps, Ledger
from kaskazi.llm.contract import (
    Allowance,
    Attempt,
    BudgetContext,
    DocumentPart,
    EmbedRequest,
    EmbedResult,
    GatewayError,
    GatewayRefusal,
    GenerateRequest,
    GenerateResult,
    ImagePart,
    Purpose,
    TextPart,
    ToolResultPart,
)
from kaskazi.llm.prices import PRICES, cost_usd, worst_case_usd
from kaskazi.llm.providers.base import Provider, ProviderFailure, ProviderUnreachable
from kaskazi.llm.routing import AllProvidersFailed, EmbeddingRoute, Generator, Route, load_routes
from kaskazi.release import release_id

IMAGE_TOKENS = 1_600  # a conservative allowance for one downscaled document image


@dataclass(frozen=True)
class GatewaySettings:
    token: str
    database_url: str
    caps: Caps
    ai_enabled: bool
    anthropic_key: str | None
    anthropic_workspace: str | None
    openai_key: str | None
    use_fake: bool

    @classmethod
    def from_env(cls) -> GatewaySettings:
        env = os.environ
        return cls(
            token=env["KASKAZI_GATEWAY_TOKEN"],
            database_url=env["KASKAZI_GATEWAY_DATABASE_URL"],
            caps=Caps(
                site_day_usd=float(env.get("KASKAZI_CAP_DAY_USD", "1.30")),
                site_month_usd=float(env.get("KASKAZI_CAP_MONTH_USD", "40")),
                visitor_day_usd=float(env.get("KASKAZI_CAP_VISITOR_DAY_USD", "0.04")),
                address_day_usd=float(env.get("KASKAZI_CAP_ADDRESS_DAY_USD", "0.12")),
            ),
            ai_enabled=env.get("KASKAZI_AI_ENABLED", "1") == "1",
            anthropic_key=env.get("ANTHROPIC_KEY"),
            anthropic_workspace=env.get("ANTHROPIC_WORKSPACE_ID"),
            openai_key=env.get("OPENAI_API_KEY"),
            # Only for tests and the browser tests in CI. A release refuses to go live with it.
            use_fake=env.get("KASKAZI_GATEWAY_FAKE_PROVIDERS") == "1",
        )


class Gateway:
    def __init__(
        self,
        pool: asyncpg.Pool,
        providers: dict[str, Provider],
        routes: dict[str, Route],
        embedding_route: EmbeddingRoute,
        caps: Caps,
        *,
        ai_enabled: bool = True,
    ) -> None:
        self.pool = pool
        self.providers = providers
        self.routes = routes
        self.embedding_route = embedding_route
        self.ledger = Ledger(pool, caps, ai_enabled=ai_enabled)
        self.generator = Generator(providers, routes)

    async def generate(self, request: GenerateRequest) -> GenerateResult | GatewayError:
        leaked = find_unmasked(_outgoing_texts(request))
        if leaked:
            await self._refused(
                request.request_id,
                request.budget,
                request.purpose,
                GatewayRefusal.UNMASKED_PERSONAL_DATA,
            )
            return GatewayError(
                refusal=GatewayRefusal.UNMASKED_PERSONAL_DATA,
                detail="the request still holds: " + ", ".join(k.value for k in leaked),
            )
        if request.route not in self.routes:
            return GatewayError(
                refusal=GatewayRefusal.BAD_REQUEST, detail=f"unknown route {request.route}"
            )
        if (
            any(isinstance(p, ImagePart) for m in request.messages for p in m.parts)
            and request.purpose is not Purpose.EXTRACT_IMAGE
        ):
            return GatewayError(
                refusal=GatewayRefusal.BAD_REQUEST,
                detail="images are only sent for document extraction",
            )

        reservation = await self.ledger.reserve(
            request.budget,
            purpose=request.purpose.value,
            route=request.route,
            request_id=request.request_id,
            amount_usd=self._worst_case(request),
        )
        if isinstance(reservation, GatewayRefusal):
            await self._refused(request.request_id, request.budget, request.purpose, reservation)
            return GatewayError(refusal=reservation, detail="a spending cap has been reached")

        try:
            result = await self.generator.generate(request)
        except AllProvidersFailed as failed:
            await self.ledger.settle(reservation, sum(a.cost_usd for a in failed.attempts))
            await self._log_attempts(request, failed.attempts)
            refusal = (
                GatewayRefusal.MODEL_REFUSED
                if failed.refusal
                else GatewayRefusal.BAD_REQUEST
                if failed.bad_request
                else GatewayRefusal.ALL_PROVIDERS_FAILED
            )
            return GatewayError(
                refusal=refusal, detail="no provider produced a reply", attempts=failed.attempts
            )
        await self.ledger.settle(reservation, result.cost_usd)
        await self._log_attempts(request, result.attempts)
        return result

    async def embed(self, request: EmbedRequest) -> EmbedResult | GatewayError:
        if find_unmasked(request.texts):
            return GatewayError(
                refusal=GatewayRefusal.UNMASKED_PERSONAL_DATA, detail="unmasked text"
            )
        route = self.embedding_route
        tokens_estimate = sum(len(t) for t in request.texts) // 3 + 1
        reservation = await self.ledger.reserve(
            request.budget,
            purpose="embed",
            route="embeddings",
            request_id=request.request_id,
            amount_usd=cost_usd(route.model, _usage(input_tokens=tokens_estimate)),
        )
        if isinstance(reservation, GatewayRefusal):
            return GatewayError(refusal=reservation, detail="a spending cap has been reached")
        provider = self.providers["openai"]
        started = time.monotonic()
        try:
            vectors, tokens = await provider.embed(  # type: ignore[attr-defined]
                request.texts, route.model, route.dimensions, route.timeout_s
            )
        except ProviderFailure as exc:
            await self.ledger.settle(reservation, 0)
            return GatewayError(refusal=GatewayRefusal.ALL_PROVIDERS_FAILED, detail=exc.error_kind)
        cost = cost_usd(route.model, _usage(input_tokens=tokens))
        await self.ledger.settle(reservation, cost)
        latency = int((time.monotonic() - started) * 1000)
        await self._log(
            request.request_id,
            request.budget.sandbox_id,
            "embed",
            "embeddings",
            "-",
            Attempt(
                provider="openai",
                model=route.model,
                outcome="ok",
                latency_ms=latency,
                usage=_usage(input_tokens=tokens),
                cost_usd=cost,
            ),
        )
        return EmbedResult(
            model=route.model, vectors=vectors, tokens=tokens, cost_usd=cost, latency_ms=latency
        )

    async def pinned_models(self) -> dict[str, bool]:
        models = (
            {r.primary for r in self.routes.values()}
            | {r.fallback for r in self.routes.values() if r.fallback}
            | {self.embedding_route.model}
        )
        found: dict[str, bool] = {}
        for model in sorted(models):
            provider = "anthropic" if model.startswith("claude-") else "openai"
            found[model] = await self.providers[provider].model_exists(model)
        return found

    def _worst_case(self, request: GenerateRequest) -> float:
        prompt = _prompt_tokens(request)
        steps = self.generator.plan(request)
        return max(
            worst_case_usd(s.model, prompt, s.max_tokens) for s in steps if s.model in PRICES
        )

    async def _log_attempts(self, request: GenerateRequest, attempts: Iterable[Attempt]) -> None:
        for attempt in attempts:
            await self._log(
                request.request_id,
                request.budget.sandbox_id,
                request.purpose.value,
                request.route,
                request.prompt_ref,
                attempt,
            )

    async def _log(
        self,
        request_id: str,
        sandbox: str | None,
        purpose: str,
        route: str,
        prompt_ref: str,
        a: Attempt,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO gateway.calls
                       (request_id, sandbox_id, purpose, route, prompt_ref, provider, model,
                        outcome, error_kind, latency_ms, input_tokens, cache_read_tokens,
                        cache_write_tokens, output_tokens, cost_usd, provider_request_id)
                   VALUES ($1, $2::text::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                           $15, $16)""",
                request_id,
                sandbox,
                purpose,
                route,
                prompt_ref,
                a.provider,
                a.model,
                a.outcome,
                a.error_kind,
                a.latency_ms,
                a.usage.input_tokens,
                a.usage.cache_read_tokens,
                a.usage.cache_write_tokens,
                a.usage.output_tokens,
                Decimal(str(a.cost_usd)),
                a.provider_request_id,
            )

    async def _refused(
        self, request_id: str, budget: BudgetContext, purpose: Purpose | str, reason: GatewayRefusal
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO gateway.refusals (request_id, sandbox_id, purpose, reason) "
                "VALUES ($1, $2::text::uuid, $3, $4)",
                request_id,
                budget.sandbox_id,
                str(purpose),
                reason.value,
            )


def _outgoing_texts(request: GenerateRequest) -> list[str]:
    """Everything in the request that came from a customer, a document or a tool. The system
    prompt and tool descriptions are the bank's own text, kept in the repo."""

    texts: list[str] = []
    for message in request.messages:
        for part in message.parts:
            if isinstance(part, TextPart | DocumentPart):
                texts.append(part.text)
            elif isinstance(part, ToolResultPart):
                texts.append(part.content)
            elif hasattr(part, "arguments"):
                texts.append(json.dumps(part.arguments))
    return texts


def _prompt_tokens(request: GenerateRequest) -> int:
    chars = sum(len(s) for s in request.system)
    chars += sum(len(json.dumps(t.input_schema)) + len(t.description) for t in request.tools)
    images = 0
    for message in request.messages:
        for part in message.parts:
            if isinstance(part, ImagePart):
                images += 1
            else:
                chars += len(part.model_dump_json())
    return chars // 3 + images * IMAGE_TOKENS + 50


def _usage(**kwargs: int) -> Any:
    from kaskazi.llm.contract import Usage

    return Usage(**kwargs)


# --- HTTP ----------------------------------------------------------------------------------------


def build_providers(settings: GatewaySettings) -> dict[str, Provider]:
    if settings.use_fake:
        from kaskazi.llm.providers.scripted import default_fake_providers

        return default_fake_providers()
    from kaskazi.llm.providers.anthropic import AnthropicProvider
    from kaskazi.llm.providers.openai import OpenAIProvider

    if not (settings.anthropic_key and settings.anthropic_workspace and settings.openai_key):
        raise SystemExit(
            "the gateway needs ANTHROPIC_KEY, ANTHROPIC_WORKSPACE_ID and OPENAI_API_KEY"
        )
    return {
        "anthropic": AnthropicProvider(settings.anthropic_key, settings.anthropic_workspace),
        "openai": OpenAIProvider(settings.openai_key),
    }


def create_app(settings: GatewaySettings, gateway: Gateway | None = None) -> FastAPI:
    """The gateway's HTTP app. Tests pass a ready `gateway`; a server builds its own."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.fake = settings.use_fake
        if gateway is not None:
            app.state.gateway = gateway
            yield
            return
        pool = await create_pool(settings.database_url, max_size=2)
        # Another routes file is for measuring a variant locally (evals/answers.py --routes). A
        # gateway with one reports it on /healthz, and the release check refuses it.
        override = os.environ.get("KASKAZI_GATEWAY_ROUTES")
        app.state.routes_override = override
        routes, embedding = load_routes(Path(override)) if override else load_routes()
        providers = build_providers(settings)
        app.state.gateway = Gateway(
            pool, providers, routes, embedding, settings.caps, ai_enabled=settings.ai_enabled
        )
        try:
            yield
        finally:
            for provider in providers.values():
                await provider.aclose()
            await pool.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    expected = f"Bearer {settings.token}".encode()

    def authorised(authorization: str = Header(default="")) -> None:
        if not hmac.compare_digest(authorization.encode(), expected):
            raise HTTPException(status_code=401)

    @app.middleware("http")
    async def release_header(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Release"] = release_id()
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "release": release_id(),
            "fake_providers": bool(getattr(app.state, "fake", False)),
            "routes_override": bool(getattr(app.state, "routes_override", None)),
        }

    @app.post("/v1/generate", dependencies=[Depends(authorised)])
    async def generate(request: GenerateRequest) -> Response:
        outcome = await app.state.gateway.generate(request)
        status = 200 if isinstance(outcome, GenerateResult) else _status(outcome.refusal)
        return JSONResponse(outcome.model_dump(mode="json"), status_code=status)

    @app.post("/v1/embed", dependencies=[Depends(authorised)])
    async def embed(request: EmbedRequest) -> Response:
        outcome = await app.state.gateway.embed(request)
        status = 200 if isinstance(outcome, EmbedResult) else _status(outcome.refusal)
        return JSONResponse(outcome.model_dump(mode="json"), status_code=status)

    @app.post("/v1/allowance", dependencies=[Depends(authorised)])
    async def allowance(budget: BudgetContext) -> Allowance:
        result: Allowance = await app.state.gateway.ledger.allowance(budget)
        return result

    @app.get("/v1/status", dependencies=[Depends(authorised)])
    async def status() -> dict[str, Any]:
        """For the staff console: the caps, what has been spent against them, the breakers."""
        gw: Gateway = app.state.gateway
        caps = gw.ledger.caps
        return {
            "ai_enabled": gw.ledger.ai_enabled,
            "caps": {
                "day_usd": caps.site_day_usd,
                "month_usd": caps.site_month_usd,
                "visitor_day_usd": caps.visitor_day_usd,
                "address_day_usd": caps.address_day_usd,
            },
            "spent": await gw.ledger.totals(),
            "breakers": {name: b.state() for name, b in gw.generator.breakers.items()},
        }

    @app.get("/v1/models", dependencies=[Depends(authorised)])
    async def models() -> Response:
        try:
            found: dict[str, bool] = await app.state.gateway.pinned_models()
        except ProviderUnreachable as error:
            return JSONResponse(
                {"unreachable": error.provider, "reason": error.reason}, status_code=502
            )
        return JSONResponse(found)

    return app


def _status(refusal: GatewayRefusal) -> int:
    if refusal in (
        GatewayRefusal.SITE_DAY_CAP,
        GatewayRefusal.SITE_MONTH_CAP,
        GatewayRefusal.VISITOR_CAP,
        GatewayRefusal.ADDRESS_CAP,
    ):
        return 429
    if refusal in (GatewayRefusal.UNMASKED_PERSONAL_DATA, GatewayRefusal.BAD_REQUEST):
        return 422
    if refusal is GatewayRefusal.AI_DISABLED:
        return 503
    return 502
