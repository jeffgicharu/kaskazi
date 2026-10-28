"""The provider-neutral contract between the application and the model gateway.

The application describes what it needs (a route, the conversation, the documents it may cite,
the tools it offers) without naming a provider. The gateway turns that into an Anthropic or
OpenAI request, and turns the reply back into these types. Nothing here imports a provider SDK.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Purpose(StrEnum):
    """What a call is for. Spending is reported by purpose."""

    ASSISTANT = "assistant"
    ROUTER = "router"
    EXTRACT_TEXT = "extract_text"
    EXTRACT_IMAGE = "extract_image"
    SUMMARY = "summary"
    JUDGE = "judge"
    CHECK = "check"


# --- Message parts -----------------------------------------------------------------------------


class TextPart(_Model):
    type: Literal["text"] = "text"
    text: str


class DocumentPart(_Model):
    """A passage the model may cite. `doc_id` is the chunk's id; it comes back on citations."""

    type: Literal["document"] = "document"
    doc_id: str
    title: str
    text: str


class ImagePart(_Model):
    """An image the model must read. Only allowed on the extract_image purpose."""

    type: Literal["image"] = "image"
    media_type: Literal["image/jpeg", "image/png", "image/webp"]
    data_base64: str


class ToolCallPart(_Model):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    arguments: dict[str, Any]


class ToolResultPart(_Model):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    content: str
    is_error: bool = False


Part = Annotated[
    TextPart | DocumentPart | ImagePart | ToolCallPart | ToolResultPart,
    Field(discriminator="type"),
]


class ProviderTurn(_Model):
    """An assistant turn exactly as one provider returned it.

    Replaying a turn unchanged to the provider that wrote it keeps its prompt cache and its
    thinking blocks valid. Any other provider reads the neutral `parts` instead.
    """

    provider: Literal["anthropic", "openai"]
    model: str
    blocks: list[dict[str, Any]]


class Message(_Model):
    """One entry in the conversation.

    `system` messages are operator notes added mid-conversation (the customer's context, a
    handover note). They never come from the customer.
    """

    role: Literal["user", "assistant", "system"]
    parts: list[Part]
    provider_turn: ProviderTurn | None = None


class ToolSpec(_Model):
    name: str
    description: str
    input_schema: dict[str, Any]


class BudgetContext(_Model):
    """Who a call is spent for, so the gateway can apply the caps."""

    sandbox_id: str | None = None
    ip_hash: str | None = None
    exempt_from_visitor_caps: bool = False  # the release check's own copy; site caps still apply


class GenerateRequest(_Model):
    purpose: Purpose
    route: str
    prompt_ref: str  # "<prompt id>@<version>", recorded with the call
    system: list[str]  # stable text blocks; cached as one prefix together with the tools
    messages: list[Message]
    tools: list[ToolSpec] = []
    output_schema: dict[str, Any] | None = None  # structured output; not combined with documents
    budget: BudgetContext
    request_id: str
    simulate_primary_outage: bool = False


# --- Results -------------------------------------------------------------------------------------


class Citation(_Model):
    doc_id: str
    cited_text: str


class AnswerText(_Model):
    text: str
    citations: list[Citation] = []


class Usage(_Model):
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0


class Attempt(_Model):
    provider: Literal["anthropic", "openai", "fake"]
    model: str
    outcome: Literal["ok", "timeout", "unavailable", "rate_limited", "error", "refusal", "skipped"]
    error_kind: str | None = None
    latency_ms: int
    usage: Usage = Usage()
    cost_usd: float = 0.0
    provider_request_id: str | None = None


class StopReason(StrEnum):
    END = "end"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"


class GenerateResult(_Model):
    provider: Literal["anthropic", "openai", "fake"]
    model: str
    stop: StopReason
    texts: list[AnswerText]
    tool_calls: list[ToolCallPart]
    structured: dict[str, Any] | None = None
    turn: ProviderTurn | None = None
    usage: Usage
    cost_usd: float
    latency_ms: int
    attempts: list[Attempt]

    @property
    def fallback_used(self) -> bool:
        return any(a.outcome != "ok" for a in self.attempts)

    @property
    def text(self) -> str:
        return "".join(t.text for t in self.texts)


class EmbedRequest(_Model):
    texts: list[str]
    budget: BudgetContext
    request_id: str


class EmbedResult(_Model):
    model: str
    vectors: list[list[float]]
    tokens: int
    cost_usd: float
    latency_ms: int


# --- Refusals by the gateway itself --------------------------------------------------------------


class GatewayRefusal(StrEnum):
    """Why the gateway refused to call any provider."""

    SITE_DAY_CAP = "site_day_cap"
    SITE_MONTH_CAP = "site_month_cap"
    VISITOR_CAP = "visitor_cap"
    ADDRESS_CAP = "address_cap"
    UNMASKED_PERSONAL_DATA = "unmasked_personal_data"
    AI_DISABLED = "ai_disabled"
    ALL_PROVIDERS_FAILED = "all_providers_failed"
    MODEL_REFUSED = "model_refused"
    BAD_REQUEST = "bad_request"


class GatewayError(_Model):
    refusal: GatewayRefusal
    detail: str
    attempts: list[Attempt] = []


class Allowance(_Model):
    """What is left today, for the friendly message and the "questions left" hint."""

    ai_enabled: bool
    site_day_left_usd: float
    site_month_left_usd: float
    visitor_left_usd: float | None
    address_left_usd: float | None

    @property
    def blocking(self) -> GatewayRefusal | None:
        if not self.ai_enabled:
            return GatewayRefusal.AI_DISABLED
        if self.site_month_left_usd <= 0:
            return GatewayRefusal.SITE_MONTH_CAP
        if self.site_day_left_usd <= 0:
            return GatewayRefusal.SITE_DAY_CAP
        if self.visitor_left_usd is not None and self.visitor_left_usd <= 0:
            return GatewayRefusal.VISITOR_CAP
        if self.address_left_usd is not None and self.address_left_usd <= 0:
            return GatewayRefusal.ADDRESS_CAP
        return None
