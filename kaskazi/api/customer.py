"""The customer app's endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from kaskazi.api.app import Services, services
from kaskazi.api.sessions import (
    COPY_LIFETIME_S,
    VISITOR_COOKIE,
    client_ip,
    ip_hash,
    read_visitor,
    require_write_header,
    visitor_cookie,
)
from kaskazi.assistant.actions import ActionRefused, create_pending, decide
from kaskazi.assistant.turn import TurnInput, run_turn
from kaskazi.audit.log import record
from kaskazi.corebank.seed import PERSONAS, seed_copy
from kaskazi.db.pool import plain_tx, visitor_tx
from kaskazi.llm.contract import BudgetContext
from kaskazi.tools.executor import Executor

router = APIRouter(prefix="/api")
Svc = Annotated[Services, Depends(services)]
COPIES_PER_HOUR = 3
COPIES_PER_DAY = 10
# A rough cost of one answered question, for the "about N questions left" hint (measured later).
TYPICAL_TURN_USD = 0.0012


class Visitor(BaseModel):
    sandbox_id: uuid.UUID
    customer_id: uuid.UUID
    persona: str
    kind: str
    ip_hash: str


async def visitor(request: Request, svc: Svc) -> Visitor:
    sandbox = read_visitor(svc.settings.secret, request)
    if sandbox is None:
        raise HTTPException(status_code=401, detail="Choose a sample customer to start.")
    async with visitor_tx(svc.pool, sandbox) as conn:
        row = await conn.fetchrow(
            "UPDATE app.sandboxes SET last_used_at = now() WHERE id = $1 RETURNING customer_id, persona, kind",
            sandbox,
        )
    if row is None:
        raise HTTPException(
            status_code=401,
            detail="Your sample copy has expired. Choose a sample customer to start again.",
        )
    return Visitor(
        sandbox_id=sandbox,
        customer_id=row["customer_id"],
        persona=row["persona"],
        kind=row["kind"],
        ip_hash=ip_hash(svc.settings.secret, client_ip(request)),
    )


Me = Annotated[Visitor, Depends(visitor)]
Write = Depends(require_write_header)


@router.get("/personas")
async def personas() -> list[dict[str, str]]:
    return [
        {"key": p.key, "label": p.label, "summary": p.summary, "first_name": p.first_name}
        for p in PERSONAS.values()
    ]


class NewSession(BaseModel):
    persona: Literal["teacher", "trader", "student"]


@router.post("/session", dependencies=[Write])
async def start(body: NewSession, request: Request, response: Response, svc: Svc) -> dict[str, Any]:
    address = ip_hash(svc.settings.secret, client_ip(request))
    async with plain_tx(svc.pool) as conn:
        hour = await conn.fetchval(
            "SELECT app.copies_from_address($1, now() - interval '1 hour')", address
        )
        day = await conn.fetchval(
            "SELECT app.copies_from_address($1, now() - interval '1 day')", address
        )
    if hour >= COPIES_PER_HOUR or day >= COPIES_PER_DAY:
        raise HTTPException(
            status_code=429,
            detail="You've started a lot of sample copies. Please carry on with the one you have, or try again later.",
        )
    sandbox = uuid.uuid4()
    async with visitor_tx(svc.pool, sandbox) as conn:
        customer = await seed_copy(conn, sandbox, body.persona)
        await conn.execute("UPDATE app.sandboxes SET ip_hash = $2 WHERE id = $1", sandbox, address)
        conversation = uuid.uuid4()
        await conn.execute(
            "INSERT INTO app.conversations (id, sandbox_id) VALUES ($1, $2)", conversation, sandbox
        )
        await record(conn, "copy_created", {"persona": body.persona})
    response.set_cookie(
        VISITOR_COOKIE,
        visitor_cookie(svc.settings.secret, sandbox),
        max_age=COPY_LIFETIME_S,
        httponly=True,
        secure=svc.settings.secure_cookies,
        samesite="strict",
        path="/api",
    )
    _ = customer
    return {"persona": body.persona, "conversation_id": str(conversation)}


@router.delete("/session", dependencies=[Write])
async def leave(response: Response) -> dict[str, bool]:
    response.delete_cookie(VISITOR_COOKIE, path="/api")
    return {"signed_out": True}


@router.get("/session")
async def session(request: Request, svc: Svc) -> dict[str, Any]:
    try:
        me = await visitor(request, svc)
    except HTTPException:
        return {"signed_in": False}
    persona = PERSONAS[me.persona]
    allowance = await svc.gateway.allowance(
        BudgetContext(
            sandbox_id=str(me.sandbox_id),
            ip_hash=me.ip_hash,
            exempt_from_visitor_caps=me.kind == "check",
        )
    )
    left = None
    if allowance is not None:
        rooms = [
            r
            for r in (
                allowance.site_day_left_usd,
                allowance.visitor_left_usd,
                allowance.address_left_usd,
            )
            if r is not None
        ]
        left = max(0, int(min(rooms) / TYPICAL_TURN_USD)) if rooms else None
    async with visitor_tx(svc.pool, me.sandbox_id) as conn:
        conversation = await conn.fetchval(
            "SELECT id FROM app.conversations ORDER BY created_at DESC LIMIT 1"
        )
    return {
        "signed_in": True,
        "persona": me.persona,
        "label": persona.label,
        "first_name": persona.first_name,
        "conversation_id": str(conversation),
        "questions_left_today": left,
        "ai_available": allowance is None or allowance.blocking is None,
    }


@router.get("/conversation")
async def conversation(me: Me, svc: Svc, id: uuid.UUID) -> dict[str, Any]:
    async with visitor_tx(svc.pool, me.sandbox_id) as conn:
        convo = await conn.fetchrow("SELECT id, status FROM app.conversations WHERE id = $1", id)
        if convo is None:
            raise HTTPException(status_code=404, detail="No such conversation.")
        rows = await conn.fetch(
            """SELECT m.id, m.author, m.display, m.created_at, f.rating
               FROM app.messages m LEFT JOIN app.feedback f ON f.message_id = m.id
               WHERE m.conversation_id = $1 AND m.author IN ('customer', 'assistant', 'agent', 'bank', 'sms')
               ORDER BY m.seq""",
            id,
        )
        actions = await conn.fetch(
            "SELECT id, status, expires_at FROM app.pending_actions WHERE conversation_id = $1", id
        )
    status = {
        str(a["id"]): (
            "expired"
            if a["status"] == "pending" and a["expires_at"] < datetime.now(UTC)
            else a["status"]
        )
        for a in actions
    }
    messages = []
    for r in rows:
        display = dict(r["display"])
        if display.get("hidden"):
            continue
        for card in display.get("cards", []):
            if card.get("type") == "confirm":
                card["status"] = status.get(card["action_id"], card.get("status"))
        messages.append(
            {
                "id": r["id"],
                "author": r["author"],
                "display": display,
                "at": r["created_at"].isoformat(),
                "rating": r["rating"],
            }
        )
    return {"id": str(convo["id"]), "status": convo["status"], "messages": messages}


class ChatMessage(BaseModel):
    conversation_id: uuid.UUID
    text: str = Field(min_length=1, max_length=2000)


@router.post("/chat", dependencies=[Write])
async def chat(body: ChatMessage, me: Me, svc: Svc) -> dict[str, Any]:
    async with visitor_tx(svc.pool, me.sandbox_id) as conn:
        owned = await conn.fetchval(
            "SELECT 1 FROM app.conversations WHERE id = $1", body.conversation_id
        )
        outage = await conn.fetchval(
            "SELECT simulate_primary_outage FROM app.sandboxes WHERE id = $1", me.sandbox_id
        )
    if not owned:
        raise HTTPException(status_code=404, detail="No such conversation.")
    channel = f"conversation:{body.conversation_id}"

    async def progress(stage: str, label: str) -> None:
        svc.hub.publish(channel, {"type": "progress", "stage": stage, "label": label})

    result = await run_turn(
        svc.deps,
        TurnInput(
            sandbox_id=me.sandbox_id,
            customer_id=me.customer_id,
            conversation_id=body.conversation_id,
            ip_hash=me.ip_hash,
            text=body.text,
            exempt_from_visitor_caps=me.kind == "check",
            simulate_primary_outage=bool(outage),
        ),
        progress,
    )
    svc.hub.publish(channel, {"type": "done"})
    if result["outcome"] in ("handover", "with_agent"):
        svc.hub.publish(f"queue:{me.sandbox_id}", {"type": "queue_changed"})
    return result


@router.get("/stream")
async def stream(
    request: Request, svc: Svc, conversation: uuid.UUID | None = None
) -> StreamingResponse:
    channels: list[str] = []
    sandbox = read_visitor(svc.settings.secret, request)
    if sandbox is not None and conversation is not None:
        async with visitor_tx(svc.pool, sandbox) as conn:
            if await conn.fetchval("SELECT 1 FROM app.conversations WHERE id = $1", conversation):
                channels = [f"conversation:{conversation}"]
    # Without a session the stream still opens with its retry line, then ends.
    return StreamingResponse(
        svc.hub.stream(channels),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )


class Decision(BaseModel):
    action_id: uuid.UUID
    token: str = Field(min_length=10, max_length=128)
    code: str | None = Field(default=None, max_length=12)


@router.post("/actions/confirm", dependencies=[Write])
async def confirm(body: Decision, me: Me, svc: Svc) -> dict[str, Any]:
    return await _decide(body, me, svc, confirm=True)


@router.post("/actions/cancel", dependencies=[Write])
async def cancel(body: Decision, me: Me, svc: Svc) -> dict[str, Any]:
    return await _decide(body, me, svc, confirm=False)


async def _decide(body: Decision, me: Visitor, svc: Services, *, confirm: bool) -> dict[str, Any]:
    try:
        return await decide(
            svc.pool,
            svc.bank,
            svc.settings.secret,
            sandbox_id=me.sandbox_id,
            customer_id=me.customer_id,
            action_id=body.action_id,
            token=body.token,
            confirm=confirm,
            code=body.code,
        )
    except ActionRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


class Feedback(BaseModel):
    message_id: int
    rating: Literal[-1, 1]
    reason: Literal["wrong", "unclear", "not_helpful", "unsafe", "other", "helpful"] | None = None
    comment: str | None = Field(default=None, max_length=500)


@router.post("/feedback", dependencies=[Write])
async def feedback(body: Feedback, me: Me, svc: Svc) -> dict[str, bool]:
    async with visitor_tx(svc.pool, me.sandbox_id) as conn:
        author = await conn.fetchval(
            "SELECT author FROM app.messages WHERE id = $1", body.message_id
        )
        if author != "assistant":
            raise HTTPException(
                status_code=404, detail="Only the assistant's answers can be rated."
            )
        await conn.execute(
            """INSERT INTO app.feedback (sandbox_id, message_id, rating, reason, comment) VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (message_id) DO UPDATE SET rating = EXCLUDED.rating, reason = EXCLUDED.reason,
                   comment = EXCLUDED.comment, created_at = now()""",
            me.sandbox_id,
            body.message_id,
            body.rating,
            body.reason,
            body.comment,
        )
        await record(
            conn,
            "feedback",
            {"message": body.message_id, "rating": body.rating, "reason": body.reason},
        )
    return {"saved": True}


class AskForPerson(BaseModel):
    conversation_id: uuid.UUID


@router.post("/handover", dependencies=[Write])
async def ask_for_person(body: AskForPerson, me: Me, svc: Svc) -> dict[str, Any]:
    async with visitor_tx(svc.pool, me.sandbox_id) as conn:
        status = await conn.fetchval(
            "SELECT status FROM app.conversations WHERE id = $1", body.conversation_id
        )
        if status is None:
            raise HTTPException(status_code=404, detail="No such conversation.")
        if status == "with_agent":
            return {"already_with_agent": True}
        handover = uuid.uuid4()
        await conn.execute(
            "INSERT INTO app.handovers (id, sandbox_id, conversation_id, reason, trigger) VALUES ($1, $2, $3, $4, 'button')",
            handover,
            me.sandbox_id,
            body.conversation_id,
            "The customer tapped Talk to a person.",
        )
        await conn.execute(
            "UPDATE app.conversations SET status = 'with_agent' WHERE id = $1", body.conversation_id
        )
        await conn.execute(
            "INSERT INTO app.jobs (sandbox_id, kind, payload) VALUES ($1, 'handover_summary', $2)",
            me.sandbox_id,
            {"handover_id": str(handover), "conversation_id": str(body.conversation_id)},
        )
        from kaskazi.assistant.templates import HANDOVER

        seq = await conn.fetchval(
            "SELECT coalesce(max(seq), 0) + 1 FROM app.messages WHERE conversation_id = $1",
            body.conversation_id,
        )
        await conn.execute(
            "INSERT INTO app.messages (conversation_id, sandbox_id, seq, author, display) VALUES ($1, $2, $3, 'bank', $4)",
            body.conversation_id,
            me.sandbox_id,
            seq,
            {"type": "bank_notice", "text": HANDOVER, "ok": True},
        )
        await record(
            conn,
            "handover",
            {"handover": str(handover), "trigger": "button"},
            conversation_id=body.conversation_id,
        )
    svc.hub.publish(f"queue:{me.sandbox_id}", {"type": "queue_changed"})
    return {"handover_id": str(handover)}


@router.get("/sources/{document_id}")
async def source(
    document_id: str, svc: Svc, chunk: str | None = Query(default=None, max_length=120)
) -> dict[str, Any]:
    async with plain_tx(svc.pool) as conn:
        doc = await conn.fetchrow(
            "SELECT id, title, owner, version, effective, body FROM kb.documents "
            "WHERE corpus_version = $1 AND id = $2 AND audience = 'customer'",
            svc.deps.corpus_version,
            document_id,
        )
        highlight = None
        if doc is not None and chunk:
            highlight = await conn.fetchval(
                "SELECT text FROM kb.chunks WHERE corpus_version = $1 AND id = $2 AND document_id = $3",
                svc.deps.corpus_version,
                chunk,
                document_id,
            )
    if doc is None:
        raise HTTPException(status_code=404, detail="No such document.")
    return {
        "id": doc["id"],
        "title": doc["title"],
        "owner": doc["owner"],
        "version": doc["version"],
        "effective": doc["effective"].isoformat(),
        "body": doc["body"],
        "highlight": highlight,
    }


# --- Quick actions: work without AI --------------------------------------------------------------


@router.get("/quick/overview")
async def overview(me: Me, svc: Svc) -> dict[str, Any]:
    executor = Executor(svc.bank, me.sandbox_id, me.customer_id, vault=_empty_vault())
    accounts = await executor.run("get_accounts", {})
    refs = await executor.refs()
    cards = [
        {
            "ref": ref,
            "kind": c["kind"],
            "last4": c["last4"],
            "status": c["status"],
            "expires": c["expires"],
        }
        for ref, c in refs.cards.items()
    ]
    return {
        "accounts": accounts.display["accounts"] if accounts.display else [],
        "account_refs": list(refs.accounts),
        "cards": cards,
    }


class StatementRequest(BaseModel):
    account: str = Field(pattern=r"^A\d{1,2}$")


@router.post("/quick/statement", dependencies=[Write])
async def statement(body: StatementRequest, me: Me, svc: Svc) -> dict[str, Any]:
    executor = Executor(svc.bank, me.sandbox_id, me.customer_id, vault=_empty_vault())
    outcome = await executor.run("get_mini_statement", {"account": body.account, "count": 10})
    if outcome.is_error or outcome.display is None:
        raise HTTPException(
            status_code=422, detail=outcome.content.get("error", "Couldn't get that statement.")
        )
    return outcome.display


class QuickBlock(BaseModel):
    conversation_id: uuid.UUID
    card: str = Field(pattern=r"^C\d{1,2}$")
    mode: Literal["temporary", "lost_or_stolen"]


@router.post("/quick/block-card", dependencies=[Write])
async def quick_block(body: QuickBlock, me: Me, svc: Svc) -> dict[str, Any]:
    executor = Executor(svc.bank, me.sandbox_id, me.customer_id, vault=_empty_vault())
    outcome = await executor.run("block_card", {"card": body.card, "mode": body.mode})
    if outcome.is_error or outcome.preview is None:
        raise HTTPException(
            status_code=409, detail=outcome.content.get("error", "That card can't be blocked.")
        )
    async with visitor_tx(svc.pool, me.sandbox_id) as conn:
        if not await conn.fetchval(
            "SELECT 1 FROM app.conversations WHERE id = $1", body.conversation_id
        ):
            raise HTTPException(status_code=404, detail="No such conversation.")
        card = await create_pending(
            conn,
            secret=svc.settings.secret,
            sandbox_id=me.sandbox_id,
            conversation_id=body.conversation_id,
            tool="block_card",
            model_arguments={"card": body.card, "mode": body.mode, "via": "quick_action"},
            resolved=outcome.resolved_arguments,
            preview=outcome.preview,
        )
        seq = await conn.fetchval(
            "SELECT coalesce(max(seq), 0) + 1 FROM app.messages WHERE conversation_id = $1",
            body.conversation_id,
        )
        await conn.execute(
            "INSERT INTO app.messages (conversation_id, sandbox_id, seq, author, display) VALUES ($1, $2, $3, 'bank', $4)",
            body.conversation_id,
            me.sandbox_id,
            seq,
            {"type": "quick_action", "text": "Check the details and confirm.", "cards": [card]},
        )
        await record(
            conn,
            "quick_action",
            {"action": card["action_id"], "tool": "block_card"},
            conversation_id=body.conversation_id,
        )
    return card


def _empty_vault() -> Any:
    from kaskazi.controls.pii import Vault

    return Vault()


def _expires(minutes: int) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)
