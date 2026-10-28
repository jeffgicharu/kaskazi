"""The staff console's endpoints. Staff work inside the visitor's own copy, in one of three roles.

- agent: the handover queue; replying to customers; resolving and handing back to the assistant.
- reviewer: the document review queue (the document desk).
- ai_lead: the audit trail, prompts, evaluations, monitoring and spending, and the outage switch.

Staff see the masked transcript: what the models saw, with placeholders, plus the sources and
tool calls. They work from the summary and the transcript, not from raw identifiers.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from kaskazi.api.app import Services, services
from kaskazi.api.sessions import (
    STAFF_COOKIE,
    STAFF_LIFETIME_S,
    Role,
    Staff,
    read_staff,
    read_visitor,
    require_write_header,
    staff_cookie,
)
from kaskazi.audit.log import record
from kaskazi.db.pool import visitor_tx

router = APIRouter(prefix="/api/console")
Svc = Annotated[Services, Depends(services)]
Write = Depends(require_write_header)


def staff(request: Request, svc: Svc) -> Staff:
    s = read_staff(svc.settings.secret, request)
    if s is None:
        raise HTTPException(
            status_code=401, detail="Open the console from the home page to sign in."
        )
    return s


def needs(*roles: Role) -> Any:
    def check(s: Annotated[Staff, Depends(staff)]) -> Staff:
        if s.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"This needs the {' or '.join(r.replace('_', ' ') for r in roles)} role.",
            )
        return s

    return Depends(check)


class Enter(BaseModel):
    role: Literal["agent", "reviewer", "ai_lead"]


@router.post("/enter", dependencies=[Write])
async def enter(body: Enter, request: Request, response: Response, svc: Svc) -> dict[str, str]:
    sandbox = read_visitor(svc.settings.secret, request)
    if sandbox is None:
        raise HTTPException(
            status_code=401,
            detail="Choose a sample customer first: the console works on your own copy.",
        )
    async with visitor_tx(svc.pool, sandbox) as conn:
        if not await conn.fetchval("SELECT 1 FROM app.sandboxes WHERE id = $1", sandbox):
            raise HTTPException(status_code=401, detail="Your sample copy has expired.")
        await record(conn, "staff_signed_in", {"role": body.role})
    response.set_cookie(
        STAFF_COOKIE,
        staff_cookie(svc.settings.secret, sandbox, body.role),
        max_age=STAFF_LIFETIME_S,
        httponly=True,
        secure=svc.settings.secure_cookies,
        samesite="strict",
        path="/api",
    )
    return {"role": body.role}


@router.get("/me")
async def me(s: Annotated[Staff, Depends(staff)]) -> dict[str, str]:
    return {"role": s.role}


# --- Handovers -----------------------------------------------------------------------------------

Agent = Annotated[Staff, needs("agent", "ai_lead")]


@router.get("/handovers")
async def handovers(
    s: Agent, svc: Svc, status: Literal["open", "resolved"] = "open"
) -> list[dict[str, Any]]:
    wanted = ["waiting", "active"] if status == "open" else ["resolved"]
    async with visitor_tx(svc.pool, s.sandbox_id) as conn:
        rows = await conn.fetch(
            """SELECT h.id, h.conversation_id, h.reason, h.trigger, h.priority, h.status, h.summary, h.created_at,
                      h.picked_at, h.resolved_at, h.outcome, c.sample
               FROM app.handovers h JOIN app.conversations c ON c.id = h.conversation_id
               WHERE h.status = ANY($1::text[])
               ORDER BY (h.priority = 'urgent') DESC, h.created_at""",
            wanted,
        )
    return [
        {
            **{
                k: (
                    str(v)
                    if isinstance(v, uuid.UUID)
                    else v.isoformat()
                    if hasattr(v, "isoformat")
                    else v
                )
                for k, v in dict(r).items()
            }
        }
        for r in rows
    ]


@router.get("/conversations/{conversation_id}")
async def transcript(
    conversation_id: uuid.UUID, s: Annotated[Staff, Depends(staff)], svc: Svc
) -> dict[str, Any]:
    async with visitor_tx(svc.pool, s.sandbox_id) as conn:
        convo = await conn.fetchrow(
            "SELECT id, status, created_at FROM app.conversations WHERE id = $1", conversation_id
        )
        if convo is None:
            raise HTTPException(status_code=404, detail="No such conversation.")
        rows = await conn.fetch(
            "SELECT id, seq, author, model_message, display, created_at FROM app.messages WHERE conversation_id = $1 ORDER BY seq",
            conversation_id,
        )
        events = await conn.fetch(
            "SELECT seq, at, turn_id, kind, payload FROM audit.events WHERE conversation_id = $1 AND kind IN ('retrieval', 'tool_call', 'screen', 'output') ORDER BY seq",
            conversation_id,
        )
    entries = []
    for r in rows:
        if r["author"] == "sms":
            continue  # one-time codes are for the customer's phone only
        text = None
        if r["model_message"] is not None:
            parts = r["model_message"]["parts"]
            text = " ".join(p["text"] for p in parts if p.get("type") == "text") or None
            if r["author"] == "tool":
                calls = [
                    f"{p['name']}({p['arguments']})" for p in parts if p.get("type") == "tool_call"
                ]
                results = [p["content"][:400] for p in parts if p.get("type") == "tool_result"]
                text = "; ".join(calls + results) or text
        elif r["author"] in ("agent", "bank"):
            text = r["display"].get("text")
        entries.append(
            {
                "seq": r["seq"],
                "author": r["author"],
                "masked_text": text,
                "at": r["created_at"].isoformat(),
            }
        )
    return {
        "id": str(convo["id"]),
        "status": convo["status"],
        "entries": entries,
        "events": [
            {
                "seq": e["seq"],
                "at": e["at"].isoformat(),
                "turn": e["turn_id"],
                "kind": e["kind"],
                "payload": e["payload"],
            }
            for e in events
        ],
    }


@router.post("/handovers/{handover_id}/pick", dependencies=[Write])
async def pick(handover_id: uuid.UUID, s: Agent, svc: Svc) -> dict[str, str]:
    async with visitor_tx(svc.pool, s.sandbox_id) as conn:
        done = await conn.fetchval(
            "UPDATE app.handovers SET status = 'active', picked_at = now() WHERE id = $1 AND status = 'waiting' RETURNING conversation_id",
            handover_id,
        )
        if done is None:
            raise HTTPException(status_code=409, detail="That handover isn't waiting.")
        await record(
            conn,
            "handover_picked",
            {"handover": str(handover_id), "role": s.role},
            conversation_id=done,
        )
    svc.hub.publish(f"queue:{s.sandbox_id}", {"type": "queue_changed"})
    return {"status": "active"}


class Reply(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


@router.post("/handovers/{handover_id}/reply", dependencies=[Write])
async def reply(handover_id: uuid.UUID, body: Reply, s: Agent, svc: Svc) -> dict[str, Any]:
    async with visitor_tx(svc.pool, s.sandbox_id) as conn:
        h = await conn.fetchrow(
            "SELECT conversation_id, status FROM app.handovers WHERE id = $1", handover_id
        )
        if h is None or h["status"] == "resolved":
            raise HTTPException(status_code=409, detail="That handover is closed.")
        if h["status"] == "waiting":
            await conn.execute(
                "UPDATE app.handovers SET status = 'active', picked_at = now() WHERE id = $1",
                handover_id,
            )
        seq = await conn.fetchval(
            "SELECT coalesce(max(seq), 0) + 1 FROM app.messages WHERE conversation_id = $1",
            h["conversation_id"],
        )
        message_id = await conn.fetchval(
            "INSERT INTO app.messages (conversation_id, sandbox_id, seq, author, display) VALUES ($1, $2, $3, 'agent', $4) RETURNING id",
            h["conversation_id"],
            s.sandbox_id,
            seq,
            {"text": body.text, "from": "Kaskazi colleague"},
        )
        await record(
            conn,
            "agent_reply",
            {"handover": str(handover_id), "length": len(body.text)},
            conversation_id=h["conversation_id"],
        )
    svc.hub.publish(
        f"conversation:{h['conversation_id']}",
        {"type": "agent_message", "id": message_id, "text": body.text},
    )
    return {"message_id": message_id}


class Resolve(BaseModel):
    outcome: str = Field(min_length=3, max_length=300)
    hand_back: bool = True


@router.post("/handovers/{handover_id}/resolve", dependencies=[Write])
async def resolve(handover_id: uuid.UUID, body: Resolve, s: Agent, svc: Svc) -> dict[str, str]:
    async with visitor_tx(svc.pool, s.sandbox_id) as conn:
        conversation = await conn.fetchval(
            "UPDATE app.handovers SET status = 'resolved', resolved_at = now(), outcome = $2 WHERE id = $1 AND status <> 'resolved' RETURNING conversation_id",
            handover_id,
            body.outcome,
        )
        if conversation is None:
            raise HTTPException(status_code=409, detail="That handover is already resolved.")
        await conn.execute(
            "UPDATE app.conversations SET status = $2 WHERE id = $1",
            conversation,
            "open" if body.hand_back else "closed",
        )
        seq = await conn.fetchval(
            "SELECT coalesce(max(seq), 0) + 1 FROM app.messages WHERE conversation_id = $1",
            conversation,
        )
        notice = (
            "Our colleague has finished. The assistant can help again from here."
            if body.hand_back
            else "Our colleague has closed this conversation."
        )
        await conn.execute(
            "INSERT INTO app.messages (conversation_id, sandbox_id, seq, author, display) VALUES ($1, $2, $3, 'bank', $4)",
            conversation,
            s.sandbox_id,
            seq,
            {"type": "bank_notice", "text": notice, "ok": True},
        )
        await record(
            conn,
            "handover_resolved",
            {"handover": str(handover_id), "hand_back": body.hand_back},
            conversation_id=conversation,
        )
    svc.hub.publish(f"conversation:{conversation}", {"type": "agent_done"})
    svc.hub.publish(f"queue:{s.sandbox_id}", {"type": "queue_changed"})
    return {"status": "resolved"}


@router.get("/stream")
async def console_stream(request: Request, svc: Svc) -> StreamingResponse:
    s = read_staff(svc.settings.secret, request)
    channels = [f"queue:{s.sandbox_id}"] if s is not None else []
    return StreamingResponse(
        svc.hub.stream(channels),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )


# --- Audit trail -----------------------------------------------------------------------------

Lead = Annotated[Staff, needs("ai_lead")]


@router.get("/audit")
async def audit(
    s: Lead, svc: Svc, conversation: uuid.UUID | None = None, after: int = 0, limit: int = 200
) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    async with visitor_tx(svc.pool, s.sandbox_id) as conn:
        if conversation is None:
            rows = await conn.fetch(
                "SELECT seq, at, conversation_id, turn_id, kind, payload, hash FROM audit.events "
                "WHERE seq > $1 ORDER BY seq LIMIT $2",
                after,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT seq, at, conversation_id, turn_id, kind, payload, hash FROM audit.events "
                "WHERE conversation_id = $1 AND seq > $2 ORDER BY seq LIMIT $3",
                conversation,
                after,
                limit,
            )
    return {
        "events": [
            {
                "seq": r["seq"],
                "at": r["at"].isoformat(),
                "conversation": str(r["conversation_id"]) if r["conversation_id"] else None,
                "turn": r["turn_id"],
                "kind": r["kind"],
                "payload": r["payload"],
                "hash": r["hash"][:16],
            }
            for r in rows
        ]
    }


@router.get("/audit/verify")
async def verify(s: Lead, svc: Svc) -> dict[str, Any]:
    async with visitor_tx(svc.pool, s.sandbox_id) as conn:
        row = await conn.fetchrow("SELECT * FROM audit.verify()")
    return {
        "events": row["events"],
        "intact": row["first_broken"] is None,
        "first_broken": row["first_broken"],
    }


class OutageSwitch(BaseModel):
    simulate_primary_outage: bool


@router.post("/controls/outage", dependencies=[Write])
async def outage(body: OutageSwitch, s: Lead, svc: Svc) -> dict[str, bool]:
    async with visitor_tx(svc.pool, s.sandbox_id) as conn:
        await conn.execute(
            "UPDATE app.sandboxes SET simulate_primary_outage = $2 WHERE id = $1",
            s.sandbox_id,
            body.simulate_primary_outage,
        )
        await record(
            conn, "control_changed", {"simulate_primary_outage": body.simulate_primary_outage}
        )
    return {"simulate_primary_outage": body.simulate_primary_outage}
