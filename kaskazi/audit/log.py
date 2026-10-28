"""Writing to the audit trail.

Every event goes through the database's audit.append, which chains it to the visitor's previous
event under a lock. Payloads hold masked text only: placeholders, never the values behind them.
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from kaskazi.controls.pii import find_unmasked


class UnmaskedAuditPayload(Exception):
    pass


async def record(
    conn: asyncpg.Connection,
    kind: str,
    payload: dict[str, Any],
    *,
    conversation_id: uuid.UUID | None = None,
    turn_id: str | None = None,
) -> int:
    import json

    blob = json.dumps(payload, default=str, sort_keys=True)
    leaked = find_unmasked([blob])
    if leaked:
        # The trail must never hold raw identifiers. Refuse loudly rather than store one.
        raise UnmaskedAuditPayload(f"{kind} payload holds {', '.join(k.value for k in leaked)}")
    seq: int = await conn.fetchval(
        "SELECT audit.append($1, $2, $3, $4)", conversation_id, turn_id, kind, json.loads(blob)
    )
    return seq
