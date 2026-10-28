"""Pending actions: changes waiting for the customer's tap, and what happens when they confirm.

A pending action is created by a tool (or a quick action) with a confirmation card the server
built from the bank's records. Only a confirmation from the customer's own session, with the
card's token, before it expires, and with the one-time code where one is needed, runs it. The
action's id is the idempotency key, so a repeated tap never runs it twice.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from kaskazi.audit.log import record
from kaskazi.corebank.client import CoreBank, CoreBankError, CoreBankUnavailable
from kaskazi.db.pool import visitor_tx
from kaskazi.tools.executor import execute_confirmed
from kaskazi.tools.registry import Preview

ACTION_TTL = timedelta(minutes=5)


def action_token(secret: bytes, action_id: uuid.UUID) -> str:
    return hmac.new(secret, f"action:{action_id}".encode(), hashlib.sha256).hexdigest()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def create_pending(
    conn: asyncpg.Connection,
    *,
    secret: bytes,
    sandbox_id: uuid.UUID,
    conversation_id: uuid.UUID,
    tool: str,
    model_arguments: dict[str, Any],
    resolved: dict[str, Any],
    preview: Preview,
) -> dict[str, Any]:
    """Stores a pending action and returns the card the customer sees."""

    action_id = uuid.uuid4()
    code = f"{uuid.uuid4().int % 1_000_000:06d}" if preview.needs_code else None
    token = action_token(secret, action_id)
    expires = datetime.now(UTC) + ACTION_TTL
    shown = {
        "title": preview.title,
        "rows": preview.rows,
        "note": preview.note,
        "needs_code": preview.needs_code,
    }
    await conn.execute(
        """INSERT INTO app.pending_actions
               (id, sandbox_id, conversation_id, tool, arguments, preview, needs_code, code_hash, token_hash, expires_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
        action_id,
        sandbox_id,
        conversation_id,
        tool,
        {"model": model_arguments, "resolved": resolved},
        shown,
        preview.needs_code,
        _hash(code) if code else None,
        _hash(token),
        expires,
    )
    if code is not None:
        seq = await conn.fetchval(
            "SELECT coalesce(max(seq), 0) + 1 FROM app.messages WHERE conversation_id = $1",
            conversation_id,
        )
        # The pretend phone's text message. It is never shown to a model.
        await conn.execute(
            "INSERT INTO app.messages (conversation_id, sandbox_id, seq, author, display) VALUES ($1, $2, $3, 'sms', $4)",
            conversation_id,
            sandbox_id,
            seq,
            {
                "from": "Kaskazi",
                "text": f"Kaskazi: your one-time code is {code}. It expires in 5 minutes. "
                "Never share it, not even with Kaskazi staff.",
            },
        )
    return {
        "type": "confirm",
        "action_id": str(action_id),
        "token": token,
        "tool": tool,
        **shown,
        "expires_at": expires.isoformat(),
        "status": "pending",
    }


class ActionRefused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


async def decide(
    pool: asyncpg.Pool,
    bank: CoreBank,
    secret: bytes,
    *,
    sandbox_id: uuid.UUID,
    customer_id: uuid.UUID,
    action_id: uuid.UUID,
    token: str,
    confirm: bool,
    code: str | None = None,
) -> dict[str, Any]:
    async with visitor_tx(pool, sandbox_id) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM app.pending_actions WHERE id = $1 FOR UPDATE", action_id
        )
        if row is None or not hmac.compare_digest(_hash(token), row["token_hash"]):
            raise ActionRefused(404, "There's no such action waiting for you.")
        if row["status"] != "pending":
            raise ActionRefused(409, f"This was already {row['status']}.")
        if row["expires_at"] < datetime.now(UTC):
            await conn.execute(
                "UPDATE app.pending_actions SET status = 'expired', decided_at = now() WHERE id = $1",
                action_id,
            )
            await record(
                conn,
                "action_expired",
                {"action": str(action_id), "tool": row["tool"]},
                conversation_id=row["conversation_id"],
            )
            raise ActionRefused(410, "This expired. Ask again and I'll prepare it afresh.")
        if not confirm:
            await conn.execute(
                "UPDATE app.pending_actions SET status = 'cancelled', decided_at = now() WHERE id = $1",
                action_id,
            )
            await record(
                conn,
                "action_cancelled",
                {"action": str(action_id), "tool": row["tool"]},
                conversation_id=row["conversation_id"],
            )
            return {"status": "cancelled", "title": row["preview"]["title"]}
        if row["needs_code"] and (
            not code or not hmac.compare_digest(_hash(code.strip()), row["code_hash"] or "")
        ):
            await record(
                conn,
                "action_code_wrong",
                {"action": str(action_id), "tool": row["tool"]},
                conversation_id=row["conversation_id"],
            )
            raise ActionRefused(
                422, "That code doesn't match. Check the text message and try again."
            )
        await conn.execute(
            "UPDATE app.pending_actions SET status = 'confirmed' WHERE id = $1", action_id
        )
        await record(
            conn,
            "action_confirmed",
            {"action": str(action_id), "tool": row["tool"], "arguments": row["arguments"]["model"]},
            conversation_id=row["conversation_id"],
        )
        conversation_id, tool, resolved, title = (
            row["conversation_id"],
            row["tool"],
            row["arguments"]["resolved"],
            row["preview"]["title"],
        )

    outcome: dict[str, Any]
    try:
        result = await execute_confirmed(
            bank, sandbox_id, customer_id, tool, resolved, str(action_id)
        )
        status, outcome = "done", {"ok": True, "result": result}
    except CoreBankError as exc:
        status, outcome = "failed", {"ok": False, "error": exc.message}
    except CoreBankUnavailable:
        status, outcome = (
            "failed",
            {"ok": False, "error": "The bank's system didn't answer. Nothing was changed."},
        )

    async with visitor_tx(pool, sandbox_id) as conn:
        await conn.execute(
            "UPDATE app.pending_actions SET status = $2, result = $3, decided_at = now() WHERE id = $1",
            action_id,
            status,
            outcome,
        )
        notice = (
            f"Done: {title[0].lower()}{title[1:]}."
            if status == "done"
            else f"That didn't go through: {outcome['error']}"
        )
        if status == "done" and tool == "raise_complaint":
            notice = f"Your complaint is raised. Its reference is {outcome['result']['reference']}."
        seq = await conn.fetchval(
            "SELECT coalesce(max(seq), 0) + 1 FROM app.messages WHERE conversation_id = $1",
            conversation_id,
        )
        await conn.execute(
            "INSERT INTO app.messages (conversation_id, sandbox_id, seq, author, display) VALUES ($1, $2, $3, 'bank', $4)",
            conversation_id,
            sandbox_id,
            seq,
            {"type": "bank_notice", "text": notice, "ok": status == "done"},
        )
        await record(
            conn,
            "action_result",
            {
                "action": str(action_id),
                "tool": tool,
                "status": status,
                "error": outcome.get("error"),
            },
            conversation_id=conversation_id,
        )
    return {"status": status, "title": title, "notice": notice}
