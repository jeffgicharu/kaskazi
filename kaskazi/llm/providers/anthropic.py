"""The Anthropic adapter: the neutral request as a Claude Messages API call, and back.

- Tools and the system prompt form one prefix that is identical for every visitor, cached with a
  breakpoint on the last system block. Automatic caching then caches the conversation as it grows.
- Retrieved passages are `document` blocks with citations on, so every cited sentence comes back
  with the passage it rests on.
- Claude's own turns are replayed exactly as returned (thinking blocks, citations and all), so
  the history stays append-only and its cache stays valid.
- Customer context and handover notes go in as mid-conversation system messages, never into the
  shared system prompt.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import anthropic

from kaskazi.llm.contract import (
    AnswerText,
    Citation,
    DocumentPart,
    GenerateRequest,
    ImagePart,
    Message,
    ProviderTurn,
    StopReason,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from kaskazi.llm.providers.base import (
    CallSettings,
    ProviderFailure,
    ProviderReply,
    ProviderUnreachable,
    pinned_http_client,
)

BASE_URL = "https://api.anthropic.com"
HOST = "api.anthropic.com"

_STOP = {
    "end_turn": StopReason.END,
    "stop_sequence": StopReason.END,
    "pause_turn": StopReason.END,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSAL,
}


class AnthropicProvider:
    name: Literal["anthropic"] = "anthropic"

    def __init__(self, api_key: str, workspace_id: str) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=BASE_URL,
            default_headers={"anthropic-workspace-id": workspace_id},
            max_retries=1,
            http_client=pinned_http_client(anthropic.DefaultAsyncHttpxClient, HOST),
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def model_exists(self, model: str) -> bool:
        try:
            await self._client.models.retrieve(model)
        except anthropic.NotFoundError:
            return False
        except anthropic.APIConnectionError as error:
            raise ProviderUnreachable(self.name, error) from error
        return True

    async def generate(self, request: GenerateRequest, settings: CallSettings) -> ProviderReply:
        documents: list[str] = []
        params: dict[str, Any] = {
            "model": settings.model,
            "max_tokens": settings.max_tokens,
            "system": _system(request.system),
            "messages": [_message(m, documents) for m in request.messages],
            "output_config": {"effort": settings.effort},
            "thinking": {"type": "adaptive" if settings.thinking else "disabled"},
            "cache_control": {"type": "ephemeral"},
        }
        if request.tools:
            params["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "strict": True,
                }
                for t in request.tools
            ]
        if request.output_schema is not None:
            params["output_config"]["format"] = {
                "type": "json_schema",
                "schema": request.output_schema,
            }

        client = self._client.with_options(timeout=settings.timeout_s)
        try:
            response = await client.messages.create(**params)
        except anthropic.APITimeoutError as exc:
            raise ProviderFailure("timeout", "timeout") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderFailure("unavailable", "connection") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderFailure("rate_limited", "429", request_id=exc.request_id) from exc
        except anthropic.BadRequestError as exc:
            # Our request is wrong. Another provider would not fix it, and switching would hide it.
            raise ProviderFailure(
                "error", "bad_request", fallback_allowed=False, request_id=exc.request_id
            ) from exc
        except anthropic.APIStatusError as exc:
            outcome: Literal["unavailable", "error"] = (
                "unavailable" if exc.status_code >= 500 else "error"
            )
            raise ProviderFailure(outcome, str(exc.status_code), request_id=exc.request_id) from exc

        usage = Usage(
            input_tokens=response.usage.input_tokens,
            cache_read_tokens=response.usage.cache_read_input_tokens or 0,
            cache_write_tokens=response.usage.cache_creation_input_tokens or 0,
            output_tokens=response.usage.output_tokens,
        )
        stop = _STOP.get(response.stop_reason or "end_turn", StopReason.END)
        if stop is StopReason.REFUSAL:
            # A safety decline. Claude Haiku 5.5 has no server-side fallback, and a decline is not
            # an outage, so the caller gets a safe answer rather than another provider's attempt.
            raise ProviderFailure(
                "refusal",
                getattr(response.stop_details, "category", None) or "refusal",
                fallback_allowed=False,
                request_id=response._request_id,
                usage=usage,
            )

        texts: list[AnswerText] = []
        tool_calls: list[ToolCallPart] = []
        for block in response.content:
            if block.type == "text":
                citations = [
                    Citation(doc_id=_doc_id(c, documents), cited_text=c.cited_text)
                    for c in (block.citations or [])
                    if c.type == "char_location"
                ]
                texts.append(AnswerText(text=block.text, citations=citations))
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCallPart(call_id=block.id, name=block.name, arguments=dict(block.input))
                )

        structured = None
        if request.output_schema is not None and stop is StopReason.END:
            structured = json.loads("".join(t.text for t in texts))

        turn = ProviderTurn(
            provider="anthropic",
            model=settings.model,
            blocks=[b.model_dump(mode="json", exclude_none=True) for b in response.content],
        )
        return ProviderReply(
            stop=stop,
            texts=texts,
            tool_calls=tool_calls,
            structured=structured,
            turn=turn,
            usage=usage,
            request_id=response._request_id,
        )


def _system(blocks: list[str]) -> list[dict[str, Any]]:
    system: list[dict[str, Any]] = [{"type": "text", "text": text} for text in blocks]
    if system:
        system[-1]["cache_control"] = {"type": "ephemeral"}
    return system


def _message(message: Message, documents: list[str]) -> dict[str, Any]:
    if message.role == "system":
        return {"role": "system", "content": "\n\n".join(_texts(message))}
    if message.role == "assistant":
        turn = message.provider_turn
        if turn is not None and turn.provider == "anthropic":
            return {"role": "assistant", "content": turn.blocks}
        return {"role": "assistant", "content": [_neutral_block(p) for p in message.parts]}
    content: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, DocumentPart):
            documents.append(part.doc_id)
            content.append(
                {
                    "type": "document",
                    "source": {"type": "text", "media_type": "text/plain", "data": part.text},
                    "title": part.title,
                    "citations": {"enabled": True},
                }
            )
        else:
            content.append(_neutral_block(part))
    return {"role": "user", "content": content}


def _neutral_block(part: Any) -> dict[str, Any]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImagePart):
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": part.media_type, "data": part.data_base64},
        }
    if isinstance(part, ToolCallPart):
        return {"type": "tool_use", "id": part.call_id, "name": part.name, "input": part.arguments}
    if isinstance(part, ToolResultPart):
        return {
            "type": "tool_result",
            "tool_use_id": part.call_id,
            "content": part.content,
            "is_error": part.is_error,
        }
    raise TypeError(f"cannot send {type(part).__name__} in this position")


def _texts(message: Message) -> list[str]:
    return [p.text for p in message.parts if isinstance(p, TextPart)]


def _doc_id(citation: Any, documents: list[str]) -> str:
    index = int(citation.document_index)
    if 0 <= index < len(documents):
        return documents[index]
    return str(citation.document_title or "")
