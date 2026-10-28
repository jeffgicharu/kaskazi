"""One turn of the customer assistant: a customer's message in, a checked answer out.

    normalise -> mask -> screen -> allowance -> route -> (refuse | hand over | answer)
    answer: retrieve -> generate with tools (up to 4 rounds) -> check -> restore -> show

Nothing reaches the customer until it has passed its checks, and every step writes an audit
event. The conversation the models see is append-only: each turn adds messages and never
changes earlier ones, which keeps prompt caches and replayed thinking valid.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from kaskazi.assistant import templates
from kaskazi.assistant.actions import create_pending
from kaskazi.audit.log import record
from kaskazi.controls import figures as figures_check
from kaskazi.controls.injection import screen
from kaskazi.controls.normalize import normalize
from kaskazi.controls.pii import Vault, mask, restore
from kaskazi.controls.vault_store import VaultKey, load_vault, save_new_entries
from kaskazi.corebank.client import CoreBank
from kaskazi.db.pool import plain_tx, visitor_tx
from kaskazi.llm.client import GatewayClient
from kaskazi.llm.contract import (
    BudgetContext,
    DocumentPart,
    EmbedRequest,
    EmbedResult,
    GatewayError,
    GatewayRefusal,
    GenerateRequest,
    GenerateResult,
    Message,
    Purpose,
    StopReason,
    TextPart,
    ToolResultPart,
    Usage,
)
from kaskazi.prompts.registry import prompt
from kaskazi.retrieval.store import Hit, search
from kaskazi.tools.executor import Executor
from kaskazi.tools.registry import BY_NAME, tools_for, toolset_hash

MAX_TOOL_ROUNDS = 4
CANARY = re.compile(r"(?i)kz[\W_]*canary[\W_]*6c41e8")
CONFIDENCE_FLOOR = 0.45

ROUTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["question", "task", "complaint", "wants_person", "greeting", "off_topic"],
        },
        "topics": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "accounts",
                    "savings",
                    "cards",
                    "loans",
                    "payments",
                    "digital",
                    "agents",
                    "fees",
                    "complaints",
                    "security",
                    "privacy",
                    "other",
                ],
            },
        },
        "injection_risk": {"type": "string", "enum": ["none", "low", "high"]},
        "sensitive_situation": {
            "type": "string",
            "enum": ["none", "fraud", "bereavement", "hardship", "distress"],
        },
        "search_query": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "intent",
        "topics",
        "injection_risk",
        "sensitive_situation",
        "search_query",
        "confidence",
    ],
    "additionalProperties": False,
}

TOOL_LABELS = {
    "get_accounts": "Checking your accounts",
    "get_mini_statement": "Getting your statement",
    "get_cards": "Checking your cards",
    "get_loans": "Checking your loans",
    "check_loan_eligibility": "Working out what you could borrow",
    "get_loan_quote": "Working out a quote",
    "get_salary_advance_limit": "Working out your salary advance limit",
    "look_up_fee": "Looking up the tariff",
    "get_recipients": "Checking your saved recipients",
    "get_complaints": "Checking your complaints",
}

Progress = Callable[[str, str], Awaitable[None]]


@dataclass
class TurnDeps:
    pool: asyncpg.Pool
    gateway: GatewayClient
    bank: CoreBank
    vault_key: VaultKey
    secret: bytes
    corpus_version: str


@dataclass
class TurnInput:
    sandbox_id: uuid.UUID
    customer_id: uuid.UUID
    conversation_id: uuid.UUID
    ip_hash: str | None
    text: str
    exempt_from_visitor_caps: bool = False
    simulate_primary_outage: bool = False


@dataclass
class TurnState:
    turn_id: str
    started: float
    vault: Vault
    history: list[Message]
    first_turn: bool
    model_calls: int = 0
    fallback: bool = False
    cost: float = 0.0
    usage: Usage = field(default_factory=Usage)
    cards: list[dict[str, Any]] = field(default_factory=list)
    new_placeholders: list[str] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)
    handover: dict[str, Any] | None = None
    tool_sources: list[str] = field(default_factory=list)


async def run_turn(deps: TurnDeps, inp: TurnInput, progress: Progress) -> dict[str, Any]:
    turn_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    text, invisible = normalize(inp.text)
    executor = Executor(deps.bank, inp.sandbox_id, inp.customer_id, Vault())

    async with visitor_tx(deps.pool, inp.sandbox_id) as conn:
        status = await conn.fetchval(
            "SELECT status FROM app.conversations WHERE id = $1", inp.conversation_id
        )
        vault = await load_vault(conn, deps.vault_key, inp.conversation_id)
        rows = await conn.fetch(
            "SELECT model_message FROM app.messages WHERE conversation_id = $1 AND model_message IS NOT NULL ORDER BY seq",
            inp.conversation_id,
        )
        notes = await _unnoted_outcomes(conn, inp.conversation_id)
        agent_said = await conn.fetch(
            """SELECT display FROM app.messages WHERE conversation_id = $1 AND author = 'agent' AND seq > (
                   SELECT coalesce(max(seq), 0) FROM app.messages WHERE conversation_id = $1 AND model_message IS NOT NULL)
               ORDER BY seq""",
            inp.conversation_id,
        )
    executor.vault = vault
    state = TurnState(
        turn_id,
        started,
        vault,
        [Message.model_validate(r["model_message"]) for r in rows],
        not rows,
    )

    known_names = await executor.known_names()
    if agent_said:
        # What a colleague told the customer while the conversation was with staff.
        said = " | ".join(r["display"].get("text", "") for r in agent_said)
        agent_note = mask(
            f"Bank system: while this conversation was with staff, a colleague wrote to the customer: {said}",
            vault,
            known_names,
        )
        notes.append(Message(role="system", parts=[TextPart(text=agent_note.text)]))
    masked = mask(text, vault, known_names)
    state.new_placeholders += masked.new_placeholders
    heuristic = screen(masked.text)
    budget = BudgetContext(
        sandbox_id=str(inp.sandbox_id),
        ip_hash=inp.ip_hash,
        exempt_from_visitor_caps=inp.exempt_from_visitor_caps,
    )
    user_message = Message(role="user", parts=[TextPart(text=masked.text)])

    async with visitor_tx(deps.pool, inp.sandbox_id) as conn:
        await record(
            conn,
            "input",
            {
                "masked_text": masked.text,
                "invisible_characters_removed": invisible,
                "personal_data_found": sorted({k.value for k in masked.found}),
                "secret_shared": masked.secret_shared,
                "card_number_shared": masked.card_number_shared,
                "screen": {"risk": heuristic.risk, "matches": heuristic.matches},
            },
            conversation_id=inp.conversation_id,
            turn_id=turn_id,
        )

    if status == "with_agent":
        return await _to_agent(deps, inp, state, user_message, masked.text)

    notice = (
        templates.DONT_SHARE_WARNING
        if (masked.secret_shared or masked.card_number_shared)
        else None
    )

    allowance = await deps.gateway.allowance(budget)
    if allowance is not None and allowance.blocking is not None:
        return await _template(
            deps,
            inp,
            state,
            user_message,
            templates.cap_message(allowance.blocking),
            "cap",
            {"refusal": allowance.blocking.value},
        )

    await progress("reading", "Reading your message")
    router, router_failed = await _route(deps, inp, state, budget, masked.text)
    if isinstance(router, GatewayRefusal):
        return await _template(
            deps,
            inp,
            state,
            user_message,
            templates.cap_message(router),
            "cap",
            {"refusal": router.value},
        )
    risk = (
        "high"
        if "high" in (heuristic.risk, router["injection_risk"])
        else max(heuristic.risk, router["injection_risk"], key=["none", "low", "high"].index)
    )
    decision = _decide(router, risk)
    async with visitor_tx(deps.pool, inp.sandbox_id) as conn:
        await record(
            conn,
            "screen",
            {
                "heuristic": {"risk": heuristic.risk, "matches": heuristic.matches},
                "router": router,
                "router_failed": router_failed,
                "risk": risk,
                "decision": decision,
            },
            conversation_id=inp.conversation_id,
            turn_id=turn_id,
        )

    if decision == "refuse_off_topic":
        return await _template(
            deps, inp, state, user_message, templates.OFF_TOPIC, "off_topic", {}, notice
        )
    if decision == "refuse_injection":
        return await _template(
            deps, inp, state, user_message, templates.INJECTION, "injection_refused", {}, notice
        )
    if decision == "hand_over":
        state.handover = {
            "reason": f"Router: {router['intent']}, {router['sensitive_situation']}",
            "urgent": router["sensitive_situation"] in ("fraud", "distress"),
            "trigger": router["sensitive_situation"]
            if router["sensitive_situation"] != "none"
            else "customer_asked",
        }
        reply = (
            templates.HANDOVER_FRAUD
            if router["sensitive_situation"] == "fraud"
            else templates.HANDOVER
        )
        return await _template(deps, inp, state, user_message, reply, "handover", {}, notice)

    # --- Answer: retrieve, generate, check ---------------------------------------------------
    await progress("searching", "Searching the bank's documents")
    query = router["search_query"] or masked.text
    vector: list[float] | None = None
    embedded = await deps.gateway.embed(
        EmbedRequest(texts=[query], budget=budget, request_id=f"{turn_id}-embed")
    )
    if isinstance(embedded, EmbedResult):
        vector = embedded.vectors[0]
        state.cost += embedded.cost_usd
    async with plain_tx(deps.pool) as conn:
        found = await search(
            conn,
            version=deps.corpus_version,
            query=query,
            query_vector=vector,
            audiences=["customer"],
        )
    hits = found.hits
    async with visitor_tx(deps.pool, inp.sandbox_id) as conn:
        await record(
            conn,
            "retrieval",
            {
                "query": query,
                "keyword_only": found.keyword_only,
                "hits": [
                    {
                        "chunk": h.chunk_id,
                        "score": h.score,
                        "similarity": h.similarity,
                        "vector_rank": h.vector_rank,
                        "keyword_rank": h.keyword_rank,
                    }
                    for h in hits
                ],
            },
            conversation_id=inp.conversation_id,
            turn_id=turn_id,
        )

    offered = tools_for("question" if router_failed else router["intent"])
    user_message = Message(
        role="user",
        parts=[
            *[
                DocumentPart(
                    doc_id=h.chunk_id,
                    title=f"{h.title}: {h.heading_path.split(' > ')[-1]}",
                    text=h.text,
                )
                for h in hits
            ],
            TextPart(text=masked.text),
        ],
    )
    new: list[Message] = [user_message]
    if state.first_turn:
        new.append(await _context_note(executor, vault, known_names, state))
    new += notes

    system_prompt = prompt("assistant.system")
    final: GenerateResult | None = None
    failure: str | None = None
    for _ in range(MAX_TOOL_ROUNDS + 1):
        result = await _generate(
            deps,
            inp,
            state,
            budget,
            system_prompt.body,
            system_prompt.ref,
            state.history + new,
            offered,
        )
        if isinstance(result, GatewayError):
            failure = result.refusal.value
            break
        if result.stop is StopReason.TOOL_USE and result.tool_calls:
            new.append(
                Message(
                    role="assistant",
                    parts=[
                        *[TextPart(text=t.text) for t in result.texts if t.text],
                        *result.tool_calls,
                    ],
                    provider_turn=result.turn,
                )
            )
            parts: list[TextPart | ToolResultPart] = []
            for call in result.tool_calls:
                parts.append(
                    await _run_tool(
                        deps,
                        inp,
                        state,
                        executor,
                        call.name,
                        call.arguments,
                        call.call_id,
                        {t.name for t in offered},
                        known_names,
                        progress,
                    )
                )
            new.append(Message(role="user", parts=parts))  # type: ignore[arg-type]
            continue
        final = result
        break
    else:
        failure = "too_many_tool_rounds"

    reply_text: str
    outcome: str
    checks: dict[str, Any] = {}
    sources = [h.text for h in hits] + state.tool_sources + [masked.text, system_prompt.body]
    if final is not None:
        await progress("checking", "Checking the answer")
        answer, checks = _check(final, sources)
        if not checks["passed"] and checks.get("unsupported_figures"):
            note = Message(
                role="system",
                parts=[
                    TextPart(
                        text=(
                            "Bank system check: your answer gave figures that are not in the passages or tool results: "
                            + ", ".join(checks["unsupported_figures"])
                            + ". Answer again using only figures from the passages or tool results, or say you don't have that figure."
                        )
                    )
                ],
            )
            retry = await _generate(
                deps,
                inp,
                state,
                budget,
                system_prompt.body,
                system_prompt.ref,
                state.history + new + [note],
                offered,
            )
            if isinstance(retry, GenerateResult) and retry.stop is not StopReason.TOOL_USE:
                answer2, checks2 = _check(retry, sources)
                checks = {**checks2, "retried": True, "first_attempt": checks}
                if checks2["passed"]:
                    new.append(note)
                    final, answer = retry, answer2
        if checks["passed"]:
            reply_text, outcome = answer, "answered"
            new.append(
                Message(
                    role="assistant",
                    parts=[TextPart(text=t.text) for t in final.texts if t.text],
                    provider_turn=final.turn,
                )
            )
        else:
            reply_text, outcome, final = templates.SAFE_ANSWER, "safe_answer", None
    elif failure in ("site_day_cap", "site_month_cap", "visitor_cap", "address_cap", "ai_disabled"):
        reply_text, outcome = templates.cap_message(GatewayRefusal(failure)), "cap"
    elif failure == "model_refused":
        reply_text, outcome = templates.SAFE_ANSWER, "model_refused"
    else:
        reply_text, outcome = templates.ASSISTANT_UNAVAILABLE, "unavailable"

    if final is None:
        # Keep the history truthful: the customer's message, and the bank's own reply.
        new = [
            user_message,
            *([n for n in new[1:] if n.role == "system"] if state.first_turn else []),
            Message(role="assistant", parts=[TextPart(text=reply_text)]),
        ]

    confidence = (
        _confidence(router, router_failed, hits, final, checks, used_tools=bool(state.tool_sources))
        if final is not None
        else 0.0
    )
    display = _display(
        reply_text, final, hits, state, notice, confidence if final is not None else None
    )
    if state.handover is not None:
        outcome = "handover"
    return await _finish(
        deps, inp, state, new, display, outcome, router.get("intent"), confidence, checks
    )


# --- Steps ---------------------------------------------------------------------------------------


def _decide(router: dict[str, Any], risk: str) -> str:
    if risk == "high":
        return "refuse_injection"
    if router["intent"] == "off_topic" and router["confidence"] >= 0.6:
        return "refuse_off_topic"
    if router["intent"] == "wants_person" or router["sensitive_situation"] != "none":
        return "hand_over"
    return "answer"


async def _route(
    deps: TurnDeps, inp: TurnInput, state: TurnState, budget: BudgetContext, masked_text: str
) -> tuple[dict[str, Any] | GatewayRefusal, bool]:
    recent = [m for m in state.history if m.role in ("user", "assistant")][-4:]
    lines = []
    for m in recent:
        said = " ".join(p.text for p in m.parts if isinstance(p, TextPart))
        if said:
            lines.append(f"{'Customer' if m.role == 'user' else 'Assistant'}: {said[:400]}")
    context = "\n".join(lines) or "(this is the first message)"
    router_prompt = prompt("assistant.router")
    request = GenerateRequest(
        purpose=Purpose.ROUTER,
        route="assistant.router",
        prompt_ref=router_prompt.ref,
        system=[router_prompt.body],
        messages=[
            Message(
                role="user",
                parts=[
                    TextPart(text=f"Conversation so far:\n{context}\n\nNew message:\n{masked_text}")
                ],
            )
        ],
        output_schema=ROUTER_SCHEMA,
        budget=budget,
        request_id=f"{state.turn_id}-route",
        simulate_primary_outage=inp.simulate_primary_outage,
    )
    result = await deps.gateway.generate(request)
    await _log_call(deps, inp, state, request, result)
    if isinstance(result, GenerateResult) and result.structured is not None:
        return result.structured, False
    if isinstance(result, GatewayError) and result.refusal.value.endswith("_cap"):
        return result.refusal, True
    # Without the router: carry on as a plain question with read-only tools, on the heuristic screen.
    return {
        "intent": "question",
        "topics": [],
        "injection_risk": "none",
        "sensitive_situation": "none",
        "search_query": masked_text,
        "confidence": 0.5,
    }, True


async def _generate(
    deps: TurnDeps,
    inp: TurnInput,
    state: TurnState,
    budget: BudgetContext,
    system: str,
    prompt_ref: str,
    messages: list[Message],
    offered: list[Any],
) -> GenerateResult | GatewayError:
    request = GenerateRequest(
        purpose=Purpose.ASSISTANT,
        route="assistant.answer",
        prompt_ref=prompt_ref,
        system=[system],
        messages=messages,
        tools=[t.spec() for t in offered],
        budget=budget,
        request_id=f"{state.turn_id}-{state.model_calls + 1}",
        simulate_primary_outage=inp.simulate_primary_outage,
    )
    result = await deps.gateway.generate(request)
    await _log_call(deps, inp, state, request, result, toolset=toolset_hash(offered))
    return result


async def _log_call(
    deps: TurnDeps,
    inp: TurnInput,
    state: TurnState,
    request: GenerateRequest,
    result: GenerateResult | GatewayError,
    toolset: str | None = None,
) -> None:
    state.model_calls += 1
    p = prompt(request.prompt_ref.split("@")[0])
    payload: dict[str, Any] = {
        "route": request.route,
        "prompt": request.prompt_ref,
        "prompt_hash": p.content_hash[:16],
        "toolset": toolset,
        "request_id": request.request_id,
    }
    if isinstance(result, GenerateResult):
        state.cost += result.cost_usd
        state.fallback = state.fallback or result.fallback_used
        u = result.usage
        state.usage = Usage(
            input_tokens=state.usage.input_tokens + u.input_tokens,
            cache_read_tokens=state.usage.cache_read_tokens + u.cache_read_tokens,
            cache_write_tokens=state.usage.cache_write_tokens + u.cache_write_tokens,
            output_tokens=state.usage.output_tokens + u.output_tokens,
        )
        payload |= {
            "provider": result.provider,
            "model": result.model,
            "stop": result.stop.value,
            "usage": result.usage.model_dump(),
            "cost_usd": result.cost_usd,
            "latency_ms": result.latency_ms,
            "attempts": [
                a.model_dump(include={"provider", "model", "outcome", "error_kind", "latency_ms"})
                for a in result.attempts
            ],
        }
    else:
        state.cost += sum(a.cost_usd for a in result.attempts)
        state.fallback = state.fallback or bool(result.attempts)
        payload |= {
            "refused": result.refusal.value,
            "detail": result.detail,
            "attempts": [
                a.model_dump(include={"provider", "model", "outcome", "error_kind", "latency_ms"})
                for a in result.attempts
            ],
        }
    async with visitor_tx(deps.pool, inp.sandbox_id) as conn:
        await record(
            conn, "model_call", payload, conversation_id=inp.conversation_id, turn_id=state.turn_id
        )


async def _run_tool(
    deps: TurnDeps,
    inp: TurnInput,
    state: TurnState,
    executor: Executor,
    name: str,
    arguments: dict[str, Any],
    call_id: str,
    offered: set[str],
    known_names: list[str],
    progress: Progress,
) -> ToolResultPart:
    tool = BY_NAME.get(name)
    if tool is None or name not in offered:
        outcome_content: dict[str, Any] = {"error": "That tool isn't available for this request."}
        is_error, outcome = True, None
    else:
        await progress("tool", TOOL_LABELS.get(name, "Preparing that for you"))
        outcome = await executor.run(name, arguments)
        outcome_content, is_error = outcome.content, outcome.is_error
    content = mask(json.dumps(outcome_content, default=str), state.vault, known_names)
    state.new_placeholders += content.new_placeholders
    state.tool_sources.append(content.text)
    action_id: uuid.UUID | None = None
    async with visitor_tx(deps.pool, inp.sandbox_id) as conn:
        if outcome is not None and outcome.preview is not None and not is_error:
            card = await create_pending(
                conn,
                secret=deps.secret,
                sandbox_id=inp.sandbox_id,
                conversation_id=inp.conversation_id,
                tool=name,
                model_arguments=arguments,
                resolved=outcome.resolved_arguments,
                preview=outcome.preview,
            )
            action_id = uuid.UUID(card["action_id"])
            state.cards.append(card)
        if outcome is not None and outcome.display is not None:
            state.cards.append(outcome.display)
        if outcome is not None and outcome.handover is not None:
            state.handover = {**outcome.handover, "trigger": "assistant"}
        await record(
            conn,
            "tool_call",
            {
                "tool": name,
                "risk": tool.risk if tool else None,
                "arguments": arguments,
                "result": json.loads(content.text)
                if content.text.startswith("{")
                else content.text,
                "is_error": is_error,
                "pending_action": str(action_id) if action_id else None,
            },
            conversation_id=inp.conversation_id,
            turn_id=state.turn_id,
        )
    return ToolResultPart(call_id=call_id, content=content.text, is_error=is_error)


def _check(result: GenerateResult, sources: list[str]) -> tuple[str, dict[str, Any]]:
    answer = result.text.strip()
    figures = figures_check.check(answer, sources)
    canary = bool(CANARY.search(answer))
    checks: dict[str, Any] = {
        "figures_checked": figures.checked,
        "unsupported_figures": figures.unsupported,
        "canary": canary,
        "citations": sum(len(t.citations) for t in result.texts),
        "stop": result.stop.value,
    }
    checks["passed"] = (
        figures.ok
        and not canary
        and bool(answer)
        and result.stop in (StopReason.END, StopReason.TOOL_USE)
    )
    return answer, checks


def _confidence(
    router: dict[str, Any],
    router_failed: bool,
    hits: list[Hit],
    final: GenerateResult,
    checks: dict[str, Any],
    *,
    used_tools: bool,
) -> float:
    """How far to trust an answer, from 0 to 1. Calibrated on the golden set; below the floor,
    the customer is offered a person."""

    routed = 0.5 if router_failed else float(router.get("confidence", 0.5))
    top = max((h.similarity or 0.0 for h in hits), default=0.0)
    retrieval = min(1.0, max(0.0, (top - 0.25) / 0.45))
    sentences = [t for t in final.texts if t.text.strip()]
    cited = sum(1 for t in sentences if t.citations)
    coverage = cited / len(sentences) if sentences else 0.0
    if used_tools and coverage == 0:
        coverage = (
            0.6  # an answer from the bank's systems (a balance, a quote) rather than its documents
        )
    score = 0.35 * routed + 0.35 * retrieval + 0.30 * coverage
    if checks.get("retried"):
        score -= 0.1
    return round(max(0.0, min(1.0, score)), 3)


def _display(
    reply_text: str,
    final: GenerateResult | None,
    hits: list[Hit],
    state: TurnState,
    notice: str | None,
    confidence: float | None,
) -> dict[str, Any]:
    by_chunk = {h.chunk_id: h for h in hits}
    sources: list[dict[str, Any]] = []
    numbers: dict[str, int] = {}
    segments: list[dict[str, Any]] = []
    if final is not None:
        for t in final.texts:
            cites = []
            for c in t.citations:
                h = by_chunk.get(c.doc_id)
                if h is None:
                    continue
                if c.doc_id not in numbers:
                    numbers[c.doc_id] = len(numbers) + 1
                    sources.append(
                        {
                            "n": numbers[c.doc_id],
                            "chunk_id": h.chunk_id,
                            "document_id": h.document_id,
                            "title": h.title,
                            "section": h.heading_path.split(" > ")[-1],
                            "quote": restore(c.cited_text, state.vault).text[:600],
                        }
                    )
                if numbers[c.doc_id] not in cites:
                    cites.append(numbers[c.doc_id])
            segments.append({"text": restore(t.text, state.vault).text, "cites": cites})
    else:
        segments = [{"text": reply_text, "cites": []}]
    shown = restore(reply_text, state.vault).text
    offer = confidence is not None and confidence < CONFIDENCE_FLOOR
    if offer:
        segments.append(
            {"text": "\n\nIf that doesn't answer it, I can pass you to a colleague.", "cites": []}
        )
    return {
        "text": shown,
        "segments": segments,
        "sources": sources,
        "cards": state.cards,
        "notice": notice,
        "offer_handover": offer or final is None,
        "handover": state.handover is not None,
    }


async def _context_note(
    executor: Executor, vault: Vault, known_names: list[str], state: TurnState
) -> Message:
    customer = await executor.bank.call(
        "GET", "/v1/customer", sandbox_id=executor.sandbox_id, customer_id=executor.customer_id
    )
    note = (
        f"The signed-in customer is {customer['full_name']}, who goes by {customer['first_name']}. "
        f"Segment: {customer['segment']}. Home branch: {customer['home_branch']}. Their accounts, cards, "
        "loans and saved recipients are available through the tools."
    )
    masked = mask(note, vault, known_names)
    state.new_placeholders += masked.new_placeholders
    return Message(role="system", parts=[TextPart(text=masked.text)])


async def _unnoted_outcomes(conn: asyncpg.Connection, conversation_id: uuid.UUID) -> list[Message]:
    await conn.execute(
        "UPDATE app.pending_actions SET status = 'expired', decided_at = now() "
        "WHERE conversation_id = $1 AND status = 'pending' AND expires_at < now()",
        conversation_id,
    )
    rows = await conn.fetch(
        """UPDATE app.pending_actions SET noted = true
           WHERE conversation_id = $1 AND decided_at IS NOT NULL AND NOT noted
           RETURNING tool, status, preview, result, decided_at""",
        conversation_id,
    )
    if not rows:
        return []
    lines = []
    for r in sorted(rows, key=lambda r: r["decided_at"]):
        what = r["preview"]["title"]
        if r["status"] == "done":
            lines.append(f"The customer confirmed '{what}', and it is done.")
        elif r["status"] == "cancelled":
            lines.append(f"The customer cancelled '{what}'. Nothing was changed.")
        elif r["status"] == "expired":
            lines.append(f"'{what}' expired before the customer confirmed it. Nothing was changed.")
        else:
            reason = (r["result"] or {}).get("error", "the bank's system refused it")
            lines.append(f"'{what}' failed: {reason}. Nothing was changed.")
    return [Message(role="system", parts=[TextPart(text="Bank system: " + " ".join(lines))])]


async def _template(
    deps: TurnDeps,
    inp: TurnInput,
    state: TurnState,
    user_message: Message,
    text: str,
    outcome: str,
    detail: dict[str, Any],
    notice: str | None = None,
) -> dict[str, Any]:
    new = [user_message, Message(role="assistant", parts=[TextPart(text=text)])]
    display = {
        "text": text,
        "segments": [{"text": text, "cites": []}],
        "sources": [],
        "cards": [],
        "notice": notice,
        "offer_handover": outcome in ("cap", "unavailable"),
        "handover": state.handover is not None,
    }
    return await _finish(deps, inp, state, new, display, outcome, None, None, detail)


async def _to_agent(
    deps: TurnDeps, inp: TurnInput, state: TurnState, user_message: Message, masked_text: str
) -> dict[str, Any]:
    async with visitor_tx(deps.pool, inp.sandbox_id) as conn:
        seq = await conn.fetchval(
            "SELECT coalesce(max(seq), 0) + 1 FROM app.messages WHERE conversation_id = $1",
            inp.conversation_id,
        )
        message_id = await conn.fetchval(
            """INSERT INTO app.messages (conversation_id, sandbox_id, seq, author, model_message, display)
               VALUES ($1, $2, $3, 'customer', $4, $5) RETURNING id""",
            inp.conversation_id,
            inp.sandbox_id,
            seq,
            user_message.model_dump(mode="json"),
            {"text": restore(masked_text, state.vault).text},
        )
        await save_new_entries(
            conn,
            deps.vault_key,
            inp.sandbox_id,
            inp.conversation_id,
            state.vault,
            state.new_placeholders,
        )
        await conn.execute(
            "UPDATE app.conversations SET updated_at = now() WHERE id = $1", inp.conversation_id
        )
        await record(
            conn,
            "routed_to_agent",
            {"message": masked_text},
            conversation_id=inp.conversation_id,
            turn_id=state.turn_id,
        )
    return {"outcome": "with_agent", "customer_message_id": message_id, "reply": None}


async def _finish(
    deps: TurnDeps,
    inp: TurnInput,
    state: TurnState,
    new: list[Message],
    display: dict[str, Any],
    outcome: str,
    intent: str | None,
    confidence: float | None,
    checks: dict[str, Any],
) -> dict[str, Any]:
    latency = int((time.monotonic() - state.started) * 1000)
    customer_text = " ".join(p.text for p in new[0].parts if isinstance(p, TextPart))
    async with visitor_tx(deps.pool, inp.sandbox_id) as conn:
        await save_new_entries(
            conn,
            deps.vault_key,
            inp.sandbox_id,
            inp.conversation_id,
            state.vault,
            state.new_placeholders,
        )
        seq = await conn.fetchval(
            "SELECT coalesce(max(seq), 0) FROM app.messages WHERE conversation_id = $1",
            inp.conversation_id,
        )
        customer_id = assistant_id = None
        for i, message in enumerate(new):
            seq += 1
            shown: dict[str, Any]
            if i == 0:
                author, shown = "customer", {"text": restore(customer_text, state.vault).text}
            elif i == len(new) - 1 and message.role == "assistant":
                author, shown = "assistant", display
            else:
                author, shown = ("bank" if message.role == "system" else "tool"), {"hidden": True}
            message_id = await conn.fetchval(
                """INSERT INTO app.messages (conversation_id, sandbox_id, seq, author, model_message, display)
                   VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
                inp.conversation_id,
                inp.sandbox_id,
                seq,
                author,
                message.model_dump(mode="json"),
                shown,
            )
            if i == 0:
                customer_id = message_id
            if author == "assistant":
                assistant_id = message_id
        handover_id = None
        if state.handover is not None:
            handover_id = uuid.uuid4()
            await conn.execute(
                """INSERT INTO app.handovers (id, sandbox_id, conversation_id, reason, trigger, priority)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                handover_id,
                inp.sandbox_id,
                inp.conversation_id,
                state.handover["reason"],
                state.handover.get("trigger", "assistant"),
                "urgent" if state.handover.get("urgent") else "normal",
            )
            await conn.execute(
                "UPDATE app.conversations SET status = 'with_agent' WHERE id = $1",
                inp.conversation_id,
            )
            await conn.execute(
                "INSERT INTO app.jobs (sandbox_id, kind, payload) VALUES ($1, 'handover_summary', $2)",
                inp.sandbox_id,
                {"handover_id": str(handover_id), "conversation_id": str(inp.conversation_id)},
            )
        await conn.execute(
            "UPDATE app.conversations SET updated_at = now() WHERE id = $1", inp.conversation_id
        )
        await conn.execute(
            """INSERT INTO app.turns (sandbox_id, conversation_id, outcome, intent, confidence, latency_ms,
                                      model_calls, fallback, cost_usd, cache_read_tokens, input_tokens)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
            inp.sandbox_id,
            inp.conversation_id,
            outcome,
            intent,
            confidence,
            latency,
            state.model_calls,
            state.fallback,
            round(state.cost, 6),
            state.usage.cache_read_tokens,
            state.usage.input_tokens
            + state.usage.cache_write_tokens
            + state.usage.cache_read_tokens,
        )
        await record(
            conn,
            "output",
            {
                "outcome": outcome,
                "text": next((p.text for p in new[-1].parts if isinstance(p, TextPart)), "")
                if new[-1].role == "assistant"
                else "",
                "sources": [s["chunk_id"] for s in display["sources"]],
                "checks": checks,
                "confidence": confidence,
                "latency_ms": latency,
                "model_calls": state.model_calls,
                "cost_usd": round(state.cost, 6),
                "fallback": state.fallback,
                "handover": str(handover_id) if handover_id else None,
            },
            conversation_id=inp.conversation_id,
            turn_id=state.turn_id,
        )
    return {
        "outcome": outcome,
        "turn_id": state.turn_id,
        "customer_message_id": customer_id,
        "message_id": assistant_id,
        "reply": display,
        "latency_ms": latency,
    }
