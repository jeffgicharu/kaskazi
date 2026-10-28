"""Deterministic stand-ins for both providers, for CI's browser tests and local development.

They answer each route plausibly from the request itself: the router classifies by keywords,
the assistant cites the first passage it was given or calls the tool the message asks for.
Nothing here is used to measure quality; the evaluation suite always runs the real models.
"""

from __future__ import annotations

import re
import uuid

from kaskazi.llm.contract import (
    AnswerText,
    Citation,
    DocumentPart,
    GenerateRequest,
    ProviderTurn,
    StopReason,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from kaskazi.llm.providers.base import CallSettings, Provider, ProviderReply
from kaskazi.llm.providers.fake import FakeProvider

_TOOL_WORDS = [
    (re.compile(r"(?i)\bbalance|how much (do i|have i) (have|got)"), "get_accounts"),
    (re.compile(r"(?i)statement|transactions"), "get_mini_statement"),
    (re.compile(r"(?i)\bblock\b.*\bcard|card.*\b(lost|stolen)"), "block_card"),
    (re.compile(r"(?i)\bcomplain"), "raise_complaint"),
    (re.compile(r"(?i)eligib|qualify"), "check_loan_eligibility"),
]
_OFF_TOPIC = re.compile(r"(?i)\b(poem|recipe|football|weather|joke|homework|election)\b")
_INJECTION = re.compile(r"(?i)ignore (all|previous|your) instructions|system prompt|you are now")
_PERSON = re.compile(r"(?i)\b(person|human|agent|someone real|customer care)\b")


def _last_user_text(request: GenerateRequest) -> str:
    for message in reversed(request.messages):
        if message.role == "user":
            texts = [p.text for p in message.parts if isinstance(p, TextPart)]
            if texts:
                # The router sees earlier turns for context; it judges only the new message.
                return texts[-1].rsplit("New message:\n", 1)[-1]
    return ""


def _reply(request: GenerateRequest, settings: CallSettings) -> ProviderReply:
    usage = Usage(input_tokens=800, cache_read_tokens=3000, output_tokens=120)
    text = _last_user_text(request)

    if request.output_schema is not None and request.route == "assistant.router":
        intent = "question"
        if _OFF_TOPIC.search(text):
            intent = "off_topic"
        elif _PERSON.search(text):
            intent = "wants_person"
        elif any(p.search(text) for p, _ in _TOOL_WORDS):
            intent = "task"
        data = {
            "intent": intent,
            "topics": [],
            "injection_risk": "high" if _INJECTION.search(text) else "none",
            "sensitive_situation": "none",
            "search_query": text[:200],
            "confidence": 0.9,
        }
        return _structured(data, settings, usage)

    if request.output_schema is not None:
        return _structured({"summary": "Sample summary.", "next_steps": []}, settings, usage)

    last = request.messages[-1]
    if not any(isinstance(p, ToolResultPart) for p in last.parts):
        offered = {t.name for t in request.tools}
        for pattern, tool in _TOOL_WORDS:
            if pattern.search(text) and tool in offered:
                call = ToolCallPart(
                    call_id=f"toolu_{uuid.uuid4().hex[:12]}", name=tool, arguments={}
                )
                return ProviderReply(
                    stop=StopReason.TOOL_USE,
                    texts=[],
                    tool_calls=[call],
                    structured=None,
                    turn=ProviderTurn(
                        provider="anthropic",
                        model=settings.model,
                        blocks=[
                            {"type": "tool_use", "id": call.call_id, "name": tool, "input": {}}
                        ],
                    ),
                    usage=usage,
                    request_id="fake",
                )

    documents = [p for m in request.messages for p in m.parts if isinstance(p, DocumentPart)]
    if documents:
        doc = documents[0]
        sentence = doc.text.split(". ")[0].strip()
        answer = AnswerText(
            text=f"{sentence}.", citations=[Citation(doc_id=doc.doc_id, cited_text=sentence)]
        )
    else:
        answer = AnswerText(text="Done. Is there anything else I can help with?")
    return ProviderReply(
        stop=StopReason.END,
        texts=[answer],
        tool_calls=[],
        structured=None,
        turn=ProviderTurn(
            provider="anthropic",
            model=settings.model,
            blocks=[{"type": "text", "text": answer.text}],
        ),
        usage=usage,
        request_id="fake",
    )


def _structured(data: dict[str, object], settings: CallSettings, usage: Usage) -> ProviderReply:
    import json

    text = json.dumps(data)
    return ProviderReply(
        stop=StopReason.END,
        texts=[AnswerText(text=text)],
        tool_calls=[],
        structured=data,
        turn=ProviderTurn(
            provider="anthropic", model=settings.model, blocks=[{"type": "text", "text": text}]
        ),
        usage=usage,
        request_id="fake",
    )


class _FakeOpenAI(FakeProvider):
    async def embed(
        self, texts: list[str], model: str, dimensions: int, timeout_s: float
    ) -> tuple[list[list[float]], int]:
        from kaskazi.retrieval.hashing import hashed_vector

        return [hashed_vector(t, dimensions) for t in texts], sum(len(t) // 4 for t in texts)


def default_fake_providers() -> dict[str, Provider]:
    return {"anthropic": FakeProvider(_reply), "openai": _FakeOpenAI(_reply)}
