"""Background work inside the API: queued jobs and the hourly purge of expired copies.

Jobs are claimed with FOR UPDATE SKIP LOCKED, so the two slots can run side by side during a
release without doing a job twice. The purge takes an advisory lock, so only one slot runs it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from kaskazi.audit.log import record
from kaskazi.db.pool import plain_tx, visitor_tx
from kaskazi.llm.contract import (
    BudgetContext,
    GenerateRequest,
    GenerateResult,
    Message,
    Purpose,
    TextPart,
)
from kaskazi.prompts.registry import prompt

if TYPE_CHECKING:
    from kaskazi.api.app import Services

log = logging.getLogger("kaskazi.worker")
PURGE_LOCK = 0x70757267  # "purg"
PURGE_EVERY_S = 3600

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "reason": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "suggested_first_reply": {"type": "string"},
        "urgency": {"type": "string", "enum": ["urgent", "normal"]},
    },
    "required": [
        "summary",
        "reason",
        "facts",
        "open_questions",
        "suggested_first_reply",
        "urgency",
    ],
    "additionalProperties": False,
}


class Worker:
    def __init__(self, services: Services) -> None:
        self.s = services
        self._task: asyncio.Task[None] | None = None
        self._last_purge = 0.0

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="kaskazi-worker")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            try:
                while await self.run_one():
                    pass
                if time.monotonic() - self._last_purge > PURGE_EVERY_S:
                    await self.purge()
                    self._last_purge = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("worker step failed")
            await asyncio.sleep(2)

    async def run_one(self) -> bool:
        async with plain_tx(self.s.pool) as conn:
            job = await conn.fetchrow(
                """UPDATE app.jobs SET status = 'running', locked_at = now(), attempts = attempts + 1
                   WHERE id = (SELECT id FROM app.jobs WHERE status = 'queued' AND run_after <= now()
                               ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
                   RETURNING id, sandbox_id, kind, payload, attempts"""
            )
        if job is None:
            return False
        try:
            if job["kind"] == "handover_summary":
                await self.handover_summary(job["sandbox_id"], job["payload"])
            status, error = "done", None
        except Exception as exc:  # a job's failure is recorded, and retried a few times
            log.exception("job %s failed", job["id"])
            status, error = ("queued" if job["attempts"] < 3 else "failed"), type(exc).__name__
        async with plain_tx(self.s.pool) as conn:
            await conn.execute(
                "UPDATE app.jobs SET status = $2, error = $3, run_after = now() + interval '20 seconds' WHERE id = $1",
                job["id"],
                status,
                error,
            )
        return True

    async def handover_summary(self, sandbox_id: uuid.UUID, payload: dict[str, Any]) -> None:
        handover = uuid.UUID(payload["handover_id"])
        conversation = uuid.UUID(payload["conversation_id"])
        async with visitor_tx(self.s.pool, sandbox_id) as conn:
            rows = await conn.fetch(
                "SELECT author, model_message FROM app.messages WHERE conversation_id = $1 AND model_message IS NOT NULL ORDER BY seq",
                conversation,
            )
            ip = await conn.fetchval("SELECT ip_hash FROM app.sandboxes WHERE id = $1", sandbox_id)
        lines = []
        for r in rows:
            parts = r["model_message"]["parts"]
            text = " ".join(p["text"] for p in parts if p.get("type") == "text")
            for p in parts:
                if p.get("type") == "tool_call":
                    text += f" [tool {p['name']} {p['arguments']}]"
                if p.get("type") == "tool_result":
                    text += f" [result {p['content'][:300]}]"
            who = {"user": "Customer", "assistant": "Assistant", "system": "Bank system"}[
                r["model_message"]["role"]
            ]
            if text.strip():
                lines.append(f"{who}: {text.strip()}")
        transcript = "\n".join(lines[-30:]) or "(no messages yet)"
        p = prompt("handover.summary")
        request = GenerateRequest(
            purpose=Purpose.SUMMARY,
            route="handover.summary",
            prompt_ref=p.ref,
            system=[p.body],
            messages=[Message(role="user", parts=[TextPart(text=f"Transcript:\n{transcript}")])],
            output_schema=SUMMARY_SCHEMA,
            budget=BudgetContext(sandbox_id=str(sandbox_id), ip_hash=ip),
            request_id=f"handover-{handover.hex[:12]}",
        )
        result = await self.s.gateway.generate(request)
        summary = result.structured if isinstance(result, GenerateResult) else None
        async with visitor_tx(self.s.pool, sandbox_id) as conn:
            await conn.execute(
                "UPDATE app.handovers SET summary = $2 WHERE id = $1",
                handover,
                summary
                or {
                    "summary": "A summary isn't available; read the transcript.",
                    "facts": [],
                    "open_questions": [],
                    "suggested_first_reply": "",
                    "urgency": "normal",
                    "reason": "summary unavailable",
                },
            )
            await record(
                conn,
                "handover_summary",
                {
                    "handover": str(handover),
                    "prompt": p.ref,
                    "ok": summary is not None,
                    "model": result.model if isinstance(result, GenerateResult) else None,
                    "cost_usd": result.cost_usd if isinstance(result, GenerateResult) else 0,
                },
                conversation_id=conversation,
            )
        self.s.hub.publish(f"queue:{sandbox_id}", {"type": "queue_changed"})

    async def purge(self) -> None:
        async with self.s.pool.acquire() as conn:
            if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", PURGE_LOCK):
                return
            try:
                row = await conn.fetchrow("SELECT * FROM app.purge_expired_copies()")
                if row["copies"]:
                    log.info(
                        "purged %s expired copies and %s audit events", row["copies"], row["events"]
                    )
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", PURGE_LOCK)
