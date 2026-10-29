"""The staff console's endpoints. Staff work inside the visitor's own copy, in one of three roles.

- agent: the handover queue; replying to customers; resolving and handing back to the assistant.
- reviewer: the document review queue (the document desk).
- ai_lead: the audit trail, prompts, evaluations, monitoring and spending, and the outage switch.

Staff see the masked transcript: what the models saw, with placeholders, plus the sources and
tool calls. They work from the summary and the transcript, not from raw identifiers.
"""

from __future__ import annotations

import json
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
from kaskazi.db.pool import plain_tx, visitor_tx

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


# --- Monitoring, evaluations and prompts -------------------------------------------------------
#
# Monitoring is the one view across every visitor, and it is numbers only: the turn and rating
# figures come from two functions that can return nothing but aggregates (migration 0003), the
# calls and spending from the gateway's own ledger, which holds no content.


def _num(value: Any) -> float | None:
    return None if value is None else round(float(value), 6)


@router.get("/monitoring")
async def monitoring(s: Lead, svc: Svc, days: int = 7) -> dict[str, Any]:
    days = max(1, min(days, 30))
    async with plain_tx(svc.pool) as conn:
        since = await conn.fetchval("SELECT now() - make_interval(days => $1)", days)
        turns = await conn.fetch("SELECT * FROM app.visitor_turn_stats($1)", since)
        feedback = await conn.fetch("SELECT * FROM app.site_feedback_stats($1)", since)
        calls = await conn.fetch(
            """SELECT route, provider, model, count(*) AS calls,
                      count(*) FILTER (WHERE outcome <> 'ok'
                                       AND error_kind IS DISTINCT FROM 'simulated_outage')
                          AS failures,
                      count(*) FILTER (WHERE error_kind = 'simulated_outage') AS drills,
                      percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
                      percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
                      sum(cost_usd) AS cost_usd,
                      sum(cache_read_tokens) AS cache_read_tokens,
                      sum(input_tokens + cache_read_tokens + cache_write_tokens) AS prompt_tokens
               FROM gateway.calls WHERE at > now() - interval '24 hours'
               GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""
        )
        # Cost per feature and per day: every call, the release checks' included.
        features = await conn.fetch(
            "SELECT (at AT TIME ZONE 'Africa/Nairobi')::date AS day, purpose, count(*) AS calls, "
            "sum(cost_usd) AS cost_usd FROM gateway.calls WHERE at >= $1 GROUP BY 1, 2 ORDER BY 1, 2",
            since,
        )
        refusals = await conn.fetch(
            "SELECT reason, count(*) AS refusals FROM gateway.refusals "
            "WHERE at > now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC"
        )
    return {
        "days": days,
        "gateway": await svc.gateway.status(),
        "turns": [
            {
                "day": r["day"].isoformat(),
                "outcome": r["outcome"],
                "turns": r["turns"],
                "p50_ms": r["p50_ms"],
                "p95_ms": r["p95_ms"],
                "cost_usd": _num(r["cost_usd"]),
                "fallbacks": r["fallbacks"],
            }
            for r in turns
        ],
        "feedback": [
            {"rating": r["rating"], "reason": r["reason"], "ratings": r["ratings"]}
            for r in feedback
        ],
        "calls_24h": [
            {
                "route": r["route"],
                "provider": r["provider"],
                "model": r["model"],
                "calls": r["calls"],
                "failures": r["failures"],
                "drills": r["drills"],
                "p50_ms": r["p50_ms"],
                "p95_ms": r["p95_ms"],
                "cost_usd": _num(r["cost_usd"]),
                # Embeddings have no prompt to cache.
                "cache_share": round(r["cache_read_tokens"] / r["prompt_tokens"], 3)
                if r["prompt_tokens"] and r["route"] != "embeddings"
                else None,
            }
            for r in calls
        ],
        "refusals_24h": [{"reason": r["reason"], "refusals": r["refusals"]} for r in refusals],
        "features": [
            {
                "day": r["day"].isoformat(),
                "purpose": r["purpose"],
                "calls": r["calls"],
                "cost_usd": _num(r["cost_usd"]),
            }
            for r in features
        ],
    }


@router.get("/evaluations")
async def evaluations(s: Lead) -> dict[str, Any]:
    from kaskazi.release.evalgate import REPORTS, gate, reports, summarise

    this_release = _release_fingerprint()
    answers = []
    for path in reversed(reports()):
        report = json.loads(path.read_text())
        # Recomputed from the cases with today's gate, exactly as CI does, not as stored.
        metrics = summarise(report["results"])
        baseline_path = REPORTS / report["baseline"] if report.get("baseline") else None
        baseline = (
            summarise(json.loads(baseline_path.read_text())["results"])
            if baseline_path and baseline_path.exists()
            else None
        )
        reasons = gate(metrics, baseline)
        answers.append(
            {
                "name": path.name,
                "run_on": report["run_on"],
                "label": report.get("label"),
                "fingerprint": report["fingerprint"]["total"][:12],
                "covers_this_release": report["fingerprint"]["total"] == this_release,
                "baseline": report.get("baseline"),
                "metrics": metrics,
                "gate": {"passed": not reasons, "reasons": reasons},
                "judge_cost_usd": report.get("judge_cost_usd"),
                # The cases that missed or contradicted something, to read rather than trust.
                "misses": [
                    {
                        "id": r["id"],
                        "question": r["question"],
                        "outcome": r.get("outcome"),
                        "answer": r.get("answer", "")[:600],
                        "criteria": [c for c in r.get("criteria", []) if c["verdict"] != "met"],
                    }
                    for r in report["results"]
                    if r.get("error") or any(c["verdict"] != "met" for c in r.get("criteria", []))
                ][:40],
            }
        )
    other = []
    for path in sorted(REPORTS.glob("*.json")):
        if path.name.startswith("answers-"):
            continue
        report = json.loads(path.read_text())
        other.append({"name": path.name, "run_on": report.get("run_on"), "report": report})
    return {
        "this_release": this_release[:12] if this_release else None,
        "answers": answers,
        "other": other,
    }


def _release_fingerprint() -> str | None:
    """The AI fingerprint this release was built with (written by scripts/build-release.sh)."""
    from kaskazi.release.fingerprint import ROOT

    path = ROOT / "fingerprint.json"
    if not path.exists():
        return None
    total: str = json.loads(path.read_text())["total"]
    return total


@router.get("/prompts")
async def prompts(s: Lead, svc: Svc) -> dict[str, Any]:
    """Every version of every prompt in this release's files, newest first, with the release that
    first used it where one has."""
    from kaskazi.prompts.registry import all_versions, load_prompts, semver

    live = {p.id: p.version for p in load_prompts().values()}
    async with plain_tx(svc.pool) as conn:
        first = {
            (r["prompt_id"], r["version"]): r["first_release"]
            for r in await conn.fetch(
                "SELECT prompt_id, version, first_release FROM app.prompt_versions"
            )
        }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for p in sorted(all_versions(), key=lambda p: (p.id, semver(p.version)), reverse=True):
        released = first.get((p.id, p.version))
        grouped.setdefault(p.id, []).append(
            {
                "version": p.version,
                "owner": p.owner,
                "route": p.route,
                "hash": p.content_hash[:12],
                "change": p.change_note,
                "changed_on": p.changed_on.isoformat(),
                "first_release": released[:7] if released else None,
                "live": live.get(p.id) == p.version,
                "body": p.body,
            }
        )
    return {"prompts": [{"id": k, "versions": v} for k, v in sorted(grouped.items())]}


# --- Document review and loan decisions ----------------------------------------------------------

Reviewer = Annotated[Staff, needs("reviewer", "ai_lead")]


def _desk_failure(error: Exception) -> HTTPException:
    from kaskazi.documents.desk import DeskError

    assert isinstance(error, DeskError)
    return HTTPException(status_code=error.status, detail=str(error))


@router.get("/reviews")
async def reviews(s: Reviewer, svc: Svc) -> dict[str, Any]:
    from kaskazi.documents import desk

    return {"applications": await desk.queue(svc.pool, s.sandbox_id)}


@router.get("/documents/{doc_id}/file")
async def review_file(doc_id: uuid.UUID, s: Reviewer, svc: Svc) -> Response:
    from kaskazi.documents import desk

    try:
        media_type, data, _ = await desk.file(svc.pool, s.sandbox_id, doc_id)
    except desk.DeskError as error:
        raise _desk_failure(error) from error
    return Response(
        data,
        media_type=media_type,
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "no-store",
        },
    )


@router.get("/documents/{doc_id}/text")
async def review_text(doc_id: uuid.UUID, s: Reviewer, svc: Svc) -> dict[str, Any]:
    """A PDF's text as the desk read it, and the text hidden on its pages, for the reviewer to
    see each field's evidence in place."""
    from kaskazi.documents import desk

    try:
        media_type, data, _ = await desk.file(svc.pool, s.sandbox_id, doc_id)
        if media_type != "application/pdf":
            return {"visible": None, "hidden": []}
        text = await desk.read_text(data)
    except desk.DeskError as error:
        raise _desk_failure(error) from error
    return {"visible": text.visible, "hidden": text.hidden}


class DocumentReview(BaseModel):
    decision: Literal["approved", "corrected", "rejected"]
    corrections: dict[str, str] = Field(default_factory=dict, max_length=20)
    note: str | None = Field(default=None, max_length=500)


@router.post("/documents/{doc_id}/review", dependencies=[Write])
async def review_document(
    doc_id: uuid.UUID, body: DocumentReview, s: Reviewer, svc: Svc
) -> dict[str, Any]:
    from kaskazi.documents import desk

    try:
        return await desk.review_document(
            svc.pool,
            s.sandbox_id,
            doc_id,
            decision=body.decision,
            corrections=body.corrections,
            note=body.note,
        )
    except desk.DeskError as error:
        raise _desk_failure(error) from error


class LoanDecision(BaseModel):
    decision: Literal["approve", "decline", "more_documents"]
    reason: str = Field(default="", max_length=500)


@router.post("/applications/{application_id}/decide", dependencies=[Write])
async def decide_application(
    application_id: uuid.UUID, body: LoanDecision, s: Reviewer, svc: Svc
) -> dict[str, Any]:
    from kaskazi.documents import desk

    try:
        return await desk.decide(
            svc.pool,
            svc.bank,
            s.sandbox_id,
            application_id,
            decision=body.decision,
            reason=body.reason,
        )
    except desk.DeskError as error:
        raise _desk_failure(error) from error


@router.get("/feedback")
async def feedback(s: Lead, svc: Svc) -> dict[str, Any]:
    """The customer's ratings in this copy, each with the question and the answer it rates.
    (Ratings across every visitor are on the monitoring view, as counts.)"""
    async with visitor_tx(svc.pool, s.sandbox_id) as conn:
        rows = await conn.fetch(
            """SELECT f.rating, f.reason, f.comment, f.created_at, m.display AS answer,
                      (SELECT c.display FROM app.messages c
                        WHERE c.conversation_id = m.conversation_id AND c.author = 'customer'
                          AND c.seq < m.seq ORDER BY c.seq DESC LIMIT 1) AS question
               FROM app.feedback f JOIN app.messages m ON m.id = f.message_id
               ORDER BY f.created_at DESC LIMIT 100"""
        )

    def text(display: Any) -> str:
        d = json.loads(display) if isinstance(display, str) else (display or {})
        return str(d.get("text", ""))

    return {
        "ratings": [
            {
                "rating": r["rating"],
                "reason": r["reason"],
                "comment": r["comment"],
                "at": r["created_at"].isoformat(),
                "question": text(r["question"]),
                "answer": text(r["answer"])[:800],
            }
            for r in rows
        ]
    }
