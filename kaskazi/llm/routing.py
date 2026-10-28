"""Choosing a provider for each call: the pinned routes, the fallback and the circuit breaker."""

from __future__ import annotations

import asyncio
import time
import tomllib
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from kaskazi.llm.contract import Attempt, GenerateRequest, GenerateResult
from kaskazi.llm.prices import cost_usd
from kaskazi.llm.providers.base import CallSettings, Provider, ProviderFailure

ROUTES_FILE = Path(__file__).with_name("routes.toml")


@dataclass(frozen=True)
class Route:
    name: str
    primary: str
    effort: str
    max_tokens: int
    timeout_s: float
    fallback: str | None
    fallback_effort: str | None
    fallback_timeout_s: float | None
    thinking: bool = True

    def primary_settings(self) -> CallSettings:
        return CallSettings(
            self.primary, self.effort, self.max_tokens, self.timeout_s, thinking=self.thinking
        )

    def fallback_settings(self) -> CallSettings | None:
        if self.fallback is None:
            return None
        return CallSettings(
            self.fallback,
            self.fallback_effort or "low",
            self.max_tokens,
            self.fallback_timeout_s or self.timeout_s,
        )


@dataclass(frozen=True)
class EmbeddingRoute:
    model: str
    dimensions: int
    timeout_s: float


def load_routes(path: Path = ROUTES_FILE) -> tuple[dict[str, Route], EmbeddingRoute]:
    data = tomllib.loads(path.read_text())
    routes = {
        name: Route(
            name=name,
            primary=r["primary"],
            effort=r["effort"],
            max_tokens=r["max_tokens"],
            timeout_s=r["timeout_s"],
            fallback=r.get("fallback"),
            fallback_effort=r.get("fallback_effort"),
            fallback_timeout_s=r.get("fallback_timeout_s"),
            thinking=r.get("thinking", True),
        )
        for name, r in data["routes"].items()
    }
    e = data["embeddings"]
    return routes, EmbeddingRoute(e["model"], e["dimensions"], e["timeout_s"])


def provider_for(model: str) -> str:
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith(("gpt-", "text-embedding-")):
        return "openai"
    if model == "fake":
        return "fake"
    raise ValueError(f"no provider for {model}")


class CircuitBreaker:
    """Opens after `threshold` failures within `window_s`, stays open for `cooldown_s`, then lets
    one probe call through. A success closes it again."""

    def __init__(self, threshold: int = 3, window_s: float = 60, cooldown_s: float = 60) -> None:
        self.threshold = threshold
        self.window_s = window_s
        self.cooldown_s = cooldown_s
        self._failures: deque[float] = deque()
        self._open_until = 0.0
        self._probing = False

    def allow(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if now < self._open_until:
            return False
        if self._open_until and not self._probing:
            self._probing = True  # half-open: this call is the probe
        elif self._open_until and self._probing:
            return False
        return True

    def record(self, ok: bool, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if ok:
            self._failures.clear()
            self._open_until = 0.0
            self._probing = False
            return
        if self._probing:
            self._open_until = now + self.cooldown_s
            self._probing = False
            return
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self.window_s:
            self._failures.popleft()
        if len(self._failures) >= self.threshold:
            self._open_until = now + self.cooldown_s
            self._failures.clear()

    @property
    def is_open(self) -> bool:
        return time.monotonic() < self._open_until


class AllProvidersFailed(Exception):
    def __init__(
        self, attempts: list[Attempt], *, refusal: bool = False, bad_request: bool = False
    ) -> None:
        super().__init__("no provider produced a reply")
        self.attempts = attempts
        self.refusal = refusal
        self.bad_request = bad_request


class Generator:
    """Runs a request on its route's primary model, falling back when the primary fails."""

    def __init__(self, providers: Mapping[str, Provider], routes: Mapping[str, Route]) -> None:
        self.providers = providers
        self.routes = routes
        self.breakers = {name: CircuitBreaker() for name in providers}

    def plan(self, request: GenerateRequest) -> list[CallSettings]:
        route = self.routes[request.route]
        steps = [route.primary_settings()]
        fallback = route.fallback_settings()
        if fallback is not None:
            steps.append(fallback)
        return steps

    async def generate(self, request: GenerateRequest) -> GenerateResult:
        attempts: list[Attempt] = []
        steps = self.plan(request)
        for index, settings in enumerate(steps):
            provider_name = provider_for(settings.model)
            provider = self.providers[provider_name]
            breaker = self.breakers[provider_name]
            last = index == len(steps) - 1

            if index == 0 and request.simulate_primary_outage:
                attempts.append(_skipped(provider_name, settings.model, "simulated_outage"))
                continue
            if not last and not breaker.allow():
                attempts.append(_skipped(provider_name, settings.model, "circuit_open"))
                continue

            started = time.monotonic()
            try:
                async with asyncio.timeout(settings.timeout_s + 2):
                    reply = await provider.generate(request, settings)
            except TimeoutError:
                failure = ProviderFailure("timeout", "timeout")
            except ProviderFailure as exc:
                failure = exc
            else:
                latency = _ms(started)
                breaker.record(ok=True)
                cost = cost_usd(settings.model, reply.usage)
                attempts.append(
                    Attempt(
                        provider=provider_name,  # type: ignore[arg-type]
                        model=settings.model,
                        outcome="ok",
                        latency_ms=latency,
                        usage=reply.usage,
                        cost_usd=cost,
                        provider_request_id=reply.request_id,
                    )
                )
                return GenerateResult(
                    provider=provider_name,  # type: ignore[arg-type]
                    model=settings.model,
                    stop=reply.stop,
                    texts=reply.texts,
                    tool_calls=reply.tool_calls,
                    structured=reply.structured,
                    turn=reply.turn,
                    usage=reply.usage,
                    cost_usd=round(sum(a.cost_usd for a in attempts), 8),
                    latency_ms=sum(a.latency_ms for a in attempts),
                    attempts=attempts,
                )

            # A refusal or a malformed request is not an outage: don't count it against the
            # provider, and don't hand it to another provider.
            if failure.outcome not in ("refusal",) and failure.error_kind != "bad_request":
                breaker.record(ok=False)
            attempts.append(
                Attempt(
                    provider=provider_name,  # type: ignore[arg-type]
                    model=settings.model,
                    outcome=failure.outcome,
                    error_kind=failure.error_kind,
                    latency_ms=_ms(started),
                    usage=failure.usage,
                    cost_usd=cost_usd(settings.model, failure.usage),
                    provider_request_id=failure.request_id,
                )
            )
            if not failure.fallback_allowed:
                raise AllProvidersFailed(
                    attempts,
                    refusal=failure.outcome == "refusal",
                    bad_request=failure.error_kind == "bad_request",
                )
        raise AllProvidersFailed(attempts)


def _skipped(provider: str, model: str, kind: str) -> Attempt:
    return Attempt(
        provider=provider,  # type: ignore[arg-type]
        model=model,
        outcome="skipped",
        error_kind=kind,
        latency_ms=0,
    )


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
