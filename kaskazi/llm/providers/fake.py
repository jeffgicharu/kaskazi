"""A scripted provider for tests and for the browser tests in CI, which must not spend money.

A script is a function from the request to a reply. Tests pass their own; the default script
gives deterministic, plausible behaviour for the assistant's routes.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Literal

from kaskazi.llm.contract import GenerateRequest, ProviderTurn, Usage
from kaskazi.llm.providers.base import CallSettings, ProviderFailure, ProviderReply

Script = Callable[[GenerateRequest, CallSettings], Awaitable[ProviderReply] | ProviderReply]


class FakeProvider:
    name: Literal["fake"] = "fake"

    def __init__(self, script: Script, *, models: tuple[str, ...] = ()) -> None:
        self.script = script
        self.models = models
        self.calls: list[tuple[GenerateRequest, CallSettings]] = []
        self.fail_next: list[ProviderFailure] = []

    async def generate(self, request: GenerateRequest, settings: CallSettings) -> ProviderReply:
        self.calls.append((request, settings))
        if self.fail_next:
            raise self.fail_next.pop(0)
        reply = self.script(request, settings)
        if not isinstance(reply, ProviderReply):
            reply = await reply
        return reply

    async def model_exists(self, model: str) -> bool:
        return not self.models or model in self.models

    async def aclose(self) -> None:
        return None


def structured_reply(data: dict[str, object], usage: Usage | None = None) -> ProviderReply:
    from kaskazi.llm.contract import AnswerText, StopReason

    text = json.dumps(data)
    return ProviderReply(
        stop=StopReason.END,
        texts=[AnswerText(text=text)],
        tool_calls=[],
        structured=data,
        turn=ProviderTurn(
            provider="anthropic", model="fake", blocks=[{"type": "text", "text": text}]
        ),
        usage=usage or Usage(input_tokens=100, output_tokens=20),
        request_id="fake",
    )
