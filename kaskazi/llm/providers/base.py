"""What every provider adapter implements, and the HTTP client they all use."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx2

from kaskazi.llm.contract import (
    AnswerText,
    GenerateRequest,
    ProviderTurn,
    StopReason,
    ToolCallPart,
    Usage,
)

Outcome = Literal["timeout", "unavailable", "rate_limited", "error", "refusal"]


class ProviderFailure(Exception):
    """A provider call that produced no usable reply."""

    def __init__(
        self,
        outcome: Outcome,
        error_kind: str,
        *,
        fallback_allowed: bool = True,
        request_id: str | None = None,
        usage: Usage | None = None,
    ) -> None:
        super().__init__(f"{outcome}: {error_kind}")
        self.outcome = outcome
        self.error_kind = error_kind
        self.fallback_allowed = fallback_allowed
        self.request_id = request_id
        self.usage = usage or Usage()


@dataclass(frozen=True)
class CallSettings:
    model: str
    effort: str
    max_tokens: int
    timeout_s: float
    # Claude only: adaptive thinking (the model's default) or none. A classifier answering to a
    # schema gains little from thinking and waits for it on every call.
    thinking: bool = True


@dataclass
class ProviderReply:
    stop: StopReason
    texts: list[AnswerText]
    tool_calls: list[ToolCallPart]
    structured: dict[str, Any] | None
    turn: ProviderTurn | None
    usage: Usage
    request_id: str | None


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    async def generate(self, request: GenerateRequest, settings: CallSettings) -> ProviderReply: ...

    async def model_exists(self, model: str) -> bool: ...

    async def aclose(self) -> None: ...


class UnexpectedHost(Exception):
    pass


class ProviderUnreachable(Exception):
    """The provider could not be reached at all (no name resolution, no connection, a timeout)."""

    def __init__(self, provider: str, error: BaseException) -> None:
        cause = error.__cause__ or error
        self.provider = provider
        self.reason = f"{type(cause).__name__}: {cause}"
        super().__init__(f"{provider} unreachable: {self.reason}")


def pinned_http_client(sdk_client_class: type[Any], host: str) -> Any:
    """An HTTP client that can only talk to `host`.

    Both SDKs follow redirects by default; this one never does, ignores proxy settings from the
    environment, and refuses any request to another host or any redirect response. The base URL
    is set in code by each adapter and never comes from input or data.
    """

    async def only_pinned_host(request: httpx2.Request) -> None:
        if request.url.scheme != "https" or request.url.host != host:
            raise UnexpectedHost(f"refused a request to {request.url.host}")

    async def no_redirects(response: httpx2.Response) -> None:
        if response.is_redirect:
            raise UnexpectedHost(f"refused a redirect from {host}")

    return sdk_client_class(
        follow_redirects=False,
        trust_env=False,
        event_hooks={"request": [only_pinned_host], "response": [no_redirects]},
    )
