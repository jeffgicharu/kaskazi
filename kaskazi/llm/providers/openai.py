"""The OpenAI adapter, used when Anthropic fails or times out, and for embeddings.

It uses the Responses API with `store: false`, so responses aren't stored for later retrieval.
(OpenAI's own retention of API traffic for abuse monitoring still applies; only a
zero-retention agreement removes it.)
OpenAI has no citations feature for supplied documents, so passages are given short ids
(`[S1]`, `[S2]`) and the model is told to put the id after each sentence that uses one. The ids
that come back are mapped to passages and checked against the passages actually sent; the
application's figures check applies to these answers exactly as to Claude's.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

import openai

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

BASE_URL = "https://api.openai.com/v1"
HOST = "api.openai.com"

CITATION_RULE = (
    "Some passages from the bank's documents are given to you inside <passage> tags, each with "
    "an id such as S1. Use only these passages for facts about the bank. After each sentence "
    "that uses a passage, write its id in square brackets, like [S1]. Never write an id you were "
    "not given."
)
_MARKER = re.compile(r"\s?\[(S\d+)\]")


class OpenAIProvider:
    name: Literal["openai"] = "openai"

    def __init__(self, api_key: str) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=BASE_URL,
            organization=None,
            project=None,
            max_retries=1,
            http_client=pinned_http_client(openai.DefaultAsyncHttpxClient, HOST),
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def model_exists(self, model: str) -> bool:
        try:
            await self._client.models.retrieve(model)
        except openai.NotFoundError:
            return False
        except openai.APIConnectionError as error:
            raise ProviderUnreachable(self.name, error) from error
        return True

    async def embed(
        self, texts: list[str], model: str, dimensions: int, timeout_s: float
    ) -> tuple[list[list[float]], int]:
        try:
            response = await self._client.with_options(timeout=timeout_s).embeddings.create(
                model=model, input=texts, dimensions=dimensions
            )
        except openai.APITimeoutError as exc:
            raise ProviderFailure("timeout", "timeout") from exc
        except openai.APIConnectionError as exc:
            raise ProviderFailure("unavailable", "connection") from exc
        except openai.APIStatusError as exc:
            raise ProviderFailure("error", str(exc.status_code)) from exc
        vectors = [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
        return vectors, response.usage.total_tokens

    async def generate(self, request: GenerateRequest, settings: CallSettings) -> ProviderReply:
        aliases: dict[str, str] = {}
        items: list[dict[str, Any]] = []
        for message in request.messages:
            items.extend(_items(message, aliases))

        instructions = "\n\n".join(request.system)
        if aliases:
            instructions = f"{instructions}\n\n{CITATION_RULE}"

        params: dict[str, Any] = {
            "model": settings.model,
            "instructions": instructions,
            "input": items,
            "max_output_tokens": settings.max_tokens,
            "reasoning": {"effort": settings.effort},
            "store": False,
        }
        if request.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                    "strict": True,
                }
                for t in request.tools
            ]
        if request.output_schema is not None:
            params["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "output",
                    "schema": request.output_schema,
                    "strict": True,
                }
            }

        try:
            response = await self._client.with_options(timeout=settings.timeout_s).responses.create(
                **params
            )
        except openai.APITimeoutError as exc:
            raise ProviderFailure("timeout", "timeout") from exc
        except openai.APIConnectionError as exc:
            raise ProviderFailure("unavailable", "connection") from exc
        except openai.RateLimitError as exc:
            raise ProviderFailure("rate_limited", "429", request_id=exc.request_id) from exc
        except openai.BadRequestError as exc:
            raise ProviderFailure(
                "error", "bad_request", fallback_allowed=False, request_id=exc.request_id
            ) from exc
        except openai.APIStatusError as exc:
            outcome: Literal["unavailable", "error"] = (
                "unavailable" if exc.status_code >= 500 else "error"
            )
            raise ProviderFailure(outcome, str(exc.status_code), request_id=exc.request_id) from exc

        details = response.usage.input_tokens_details if response.usage else None
        cached = (details.cached_tokens if details else 0) or 0
        usage = Usage(
            input_tokens=(response.usage.input_tokens if response.usage else 0) - cached,
            cache_read_tokens=cached,
            output_tokens=response.usage.output_tokens if response.usage else 0,
        )

        texts: list[AnswerText] = []
        tool_calls: list[ToolCallPart] = []
        refused = False
        for item in response.output:
            if item.type == "message":
                for content in item.content:
                    if content.type == "output_text":
                        texts.append(_cited(content.text, aliases))
                    elif content.type == "refusal":
                        refused = True
            elif item.type == "function_call":
                tool_calls.append(
                    ToolCallPart(
                        call_id=item.call_id, name=item.name, arguments=json.loads(item.arguments)
                    )
                )

        if refused:
            raise ProviderFailure(
                "refusal", "refusal", fallback_allowed=False, request_id=response.id, usage=usage
            )
        if tool_calls:
            stop = StopReason.TOOL_USE
        elif response.status == "incomplete":
            stop = StopReason.MAX_TOKENS
        else:
            stop = StopReason.END

        structured = None
        if request.output_schema is not None and stop is StopReason.END:
            structured = json.loads("".join(t.text for t in texts))

        turn = ProviderTurn(
            provider="openai",
            model=settings.model,
            blocks=[
                item.model_dump(mode="json", exclude_none=True)
                for item in response.output
                if item.type in ("message", "function_call")
            ],
        )
        return ProviderReply(
            stop=stop,
            texts=texts,
            tool_calls=tool_calls,
            structured=structured,
            turn=turn,
            usage=usage,
            request_id=response.id,
        )


def _items(message: Message, aliases: dict[str, str]) -> list[dict[str, Any]]:
    if message.role == "system":
        text = "\n\n".join(p.text for p in message.parts if isinstance(p, TextPart))
        return [{"role": "developer", "content": text}]

    if message.role == "assistant":
        turn = message.provider_turn
        if turn is not None and turn.provider == "openai":
            return list(turn.blocks)
        out: list[dict[str, Any]] = []
        text = "".join(p.text for p in message.parts if isinstance(p, TextPart))
        if text:
            out.append({"role": "assistant", "content": [{"type": "output_text", "text": text}]})
        for p in message.parts:
            if isinstance(p, ToolCallPart):
                out.append(
                    {
                        "type": "function_call",
                        "call_id": p.call_id,
                        "name": p.name,
                        "arguments": json.dumps(p.arguments),
                    }
                )
        return out

    results: list[dict[str, Any]] = []
    content: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, DocumentPart):
            alias = aliases.setdefault(part.doc_id, f"S{len(aliases) + 1}")
            content.append(
                {
                    "type": "input_text",
                    "text": f'<passage id="{alias}" title="{part.title}">\n{part.text}\n</passage>',
                }
            )
        elif isinstance(part, TextPart):
            content.append({"type": "input_text", "text": part.text})
        elif isinstance(part, ImagePart):
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{part.media_type};base64,{part.data_base64}",
                }
            )
        elif isinstance(part, ToolResultPart):
            results.append(
                {"type": "function_call_output", "call_id": part.call_id, "output": part.content}
            )
    if content:
        results.append({"role": "user", "content": content})
    return results


def _cited(text: str, aliases: dict[str, str]) -> AnswerText:
    by_alias = {alias: doc_id for doc_id, alias in aliases.items()}
    citations: list[Citation] = []
    for alias in _MARKER.findall(text):
        doc_id = by_alias.get(alias)
        if doc_id is not None and all(c.doc_id != doc_id for c in citations):
            citations.append(Citation(doc_id=doc_id, cited_text=""))
    return AnswerText(text=_MARKER.sub("", text), citations=citations)
