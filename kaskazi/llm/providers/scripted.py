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

    if request.output_schema is not None and request.route == "handover.summary":
        return _structured(
            {
                "summary": "The customer asked for a person. (Written by the stand-in provider used in tests.)",
                "reason": "Customer asked for a person",
                "facts": [],
                "open_questions": ["What does the customer need?"],
                "suggested_first_reply": "Hello, I'm a colleague at Kaskazi. How can I help?",
                "urgency": "normal",
            },
            settings,
            usage,
        )
    if request.output_schema is not None and request.route.startswith("documents.extract_"):
        return _structured(_read_document(request), settings, usage)
    if request.output_schema is not None:
        return _structured({}, settings, usage)

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


# --- Documents -----------------------------------------------------------------------------------
#
# The stand-in reads the sample documents' text layer with patterns that fit their layout. It
# can't read images: it returns nothing for them, so an image always goes to a reviewer.

_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        1,
    )
}
_DOCUMENT_PATTERNS = {
    "employer_kra_pin": r"Employer KRA PIN:?\s+(\S+)",
    "employee_name": r"^\s*Employee\s{2,}(.+?)\s*$",
    "employee_kra_pin": r"Employee KRA PIN\s+(\S+)",
    "basic_pay": r"Basic pay\s+([\d,]+\.\d\d)",
    "gross_pay": r"Gross pay\s+([\d,]+\.\d\d)",
    "paye": r"PAYE\s+([\d,]+\.\d\d)",
    "nssf": r"NSSF\s+([\d,]+\.\d\d)",
    "shif": r"SHIF\s+([\d,]+\.\d\d)",
    "housing_levy": r"Housing Levy\s+([\d,]+\.\d\d)",
    "total_deductions": r"Total deductions\s+([\d,]+\.\d\d)",
    "net_pay": r"NET PAY\s+([\d,]+\.\d\d)",
}


def _read_document(request: GenerateRequest) -> dict[str, object]:
    properties = (request.output_schema or {}).get("properties", {})
    names = list(properties.get("fields", {}).get("properties", {}))
    text = _last_user_text(request)
    body = text.split("<document>", 1)[-1]
    found: dict[str, dict[str, str]] = {}
    for name in names:
        value, evidence = "", ""
        pattern = _DOCUMENT_PATTERNS.get(name)
        m = re.search(pattern, body, re.MULTILINE) if pattern else None
        if m:
            evidence = m.group(0).strip()
            value = m.group(1).replace(",", "") if name not in ("employee_name",) else m.group(1)
        elif name == "employer":
            first = next((line.strip() for line in body.splitlines() if line.strip()), "")
            value = evidence = first
        elif name in ("pay_period", "pay_date"):
            d = re.search(r"Pay date\s+(\d{1,2}) (\w+) (\d{4})", body)
            if d and d.group(2).lower() in _MONTHS:
                month = _MONTHS[d.group(2).lower()]
                evidence = d.group(0)
                value = (
                    f"{d.group(3)}-{month:02d}"
                    if name == "pay_period"
                    else f"{d.group(3)}-{month:02d}-{int(d.group(1)):02d}"
                )
        found[name] = {
            "value": value,
            "evidence": evidence,
            "confidence": "high" if value else "low",
        }
    data: dict[str, object] = {
        "fields": found,
        "instructions_to_reader": [],
        "legible": bool(body.strip()),
    }
    if "rows" in properties:
        data["rows"] = []
    return data
