"""Row security between visitor copies, and the audit trail's guarantees."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from kaskazi.db.pool import visitor_tx


async def make_copy(pool: asyncpg.Pool, persona: str = "teacher") -> uuid.UUID:
    sandbox = uuid.uuid4()
    async with visitor_tx(pool, sandbox) as conn:
        await conn.execute(
            "INSERT INTO app.sandboxes (id, kind, persona, customer_id) VALUES ($1, 'visitor', $2, $3)",
            sandbox,
            persona,
            uuid.uuid4(),
        )
        conv = uuid.uuid4()
        await conn.execute(
            "INSERT INTO app.conversations (id, sandbox_id) VALUES ($1, $2)", conv, sandbox
        )
        await conn.execute(
            "INSERT INTO app.messages (conversation_id, sandbox_id, seq, author, display) "
            "VALUES ($1, $2, 1, 'customer', $3)",
            conv,
            sandbox,
            {"text": f"hello from {sandbox}"},
        )
        await conn.execute("SELECT audit.append($1, 't1', 'input', $2)", conv, {"masked": "hello"})
    return sandbox


async def test_a_visitor_sees_only_their_own_copy(app_pool: asyncpg.Pool) -> None:
    a, b = await make_copy(app_pool), await make_copy(app_pool)
    async with visitor_tx(app_pool, a) as conn:
        texts = [r["display"]["text"] for r in await conn.fetch("SELECT display FROM app.messages")]
        events = await conn.fetchval("SELECT count(*) FROM audit.events")
    assert texts == [f"hello from {a}"]
    assert events == 1
    async with visitor_tx(app_pool, b) as conn:
        assert await conn.fetchval("SELECT count(*) FROM app.sandboxes WHERE id = $1", a) == 0


async def test_a_reused_connection_carries_no_visitor(app_pool: asyncpg.Pool) -> None:
    a, b = await make_copy(app_pool), await make_copy(app_pool)
    # One connection, used for a then b then nobody, the way the pool hands it out.
    async with app_pool.acquire() as conn:
        for visitor, expected in ((a, a), (b, b)):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.visitor', $1, true)", str(visitor))
                ids = [
                    r["sandbox_id"] for r in await conn.fetch("SELECT sandbox_id FROM app.messages")
                ]
                assert ids == [expected]
        async with conn.transaction():
            assert await conn.fetchval("SELECT count(*) FROM app.messages") == 0
            assert await conn.fetchval("SELECT current_setting('app.visitor', true)") in ("", None)


async def test_concurrent_visitors_never_see_each_other(app_pool: asyncpg.Pool) -> None:
    copies = [await make_copy(app_pool) for _ in range(6)]

    async def look(visitor: uuid.UUID) -> None:
        for _ in range(15):
            async with visitor_tx(app_pool, visitor) as conn:
                ids = {
                    r["sandbox_id"] for r in await conn.fetch("SELECT sandbox_id FROM app.messages")
                }
                assert ids == {visitor}
                await asyncio.sleep(0)

    # The pool has 2 connections, so every connection is reused across visitors many times.
    await asyncio.gather(*(look(v) for v in copies))


async def test_writing_into_another_copy_is_refused(app_pool: asyncpg.Pool) -> None:
    a, b = await make_copy(app_pool), await make_copy(app_pool)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with visitor_tx(app_pool, a) as conn:
            await conn.execute(
                "INSERT INTO app.conversations (id, sandbox_id) VALUES ($1, $2)", uuid.uuid4(), b
            )


async def test_the_app_cannot_change_or_remove_audit_events(app_pool: asyncpg.Pool) -> None:
    a = await make_copy(app_pool)
    for statement in (
        "UPDATE audit.events SET payload = '{}'",
        "DELETE FROM audit.events",
        "TRUNCATE audit.events",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with visitor_tx(app_pool, a) as conn:
                await conn.execute(statement)


async def test_the_owner_cannot_rewrite_events_either(
    owner_dsn: str, app_pool: asyncpg.Pool
) -> None:
    a = await make_copy(app_pool)
    conn = await asyncpg.connect(owner_dsn)
    try:
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await conn.execute("UPDATE audit.events SET kind = 'edited' WHERE sandbox_id = $1", a)
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await conn.execute("DELETE FROM audit.events WHERE sandbox_id = $1", a)
    finally:
        await conn.close()


async def test_the_chain_verifies_and_detects_tampering(
    superuser_dsn: str, app_pool: asyncpg.Pool
) -> None:
    a = await make_copy(app_pool)
    async with visitor_tx(app_pool, a) as conn:
        for i in range(5):
            await conn.execute("SELECT audit.append(NULL, $1, 'output', $2)", f"t{i}", {"n": i})
        row = await conn.fetchrow("SELECT * FROM audit.verify()")
    assert row["events"] == 6 and row["first_broken"] is None

    # Tamper the way only a superuser could: with the trigger off.
    su = await asyncpg.connect(superuser_dsn)
    try:
        await su.execute("ALTER TABLE audit.events DISABLE TRIGGER events_append_only")
        await su.execute(
            "UPDATE audit.events SET payload = '{\"n\": 99}' WHERE sandbox_id = $1 AND seq = 4", a
        )
        await su.execute("ALTER TABLE audit.events ENABLE TRIGGER events_append_only")
    finally:
        await su.close()
    async with visitor_tx(app_pool, a) as conn:
        row = await conn.fetchrow("SELECT * FROM audit.verify()")
    assert row["first_broken"] == 4


async def test_purge_removes_only_expired_copies_and_their_chains(app_pool: asyncpg.Pool) -> None:
    old, fresh = await make_copy(app_pool), await make_copy(app_pool)
    async with visitor_tx(app_pool, old) as conn:
        await conn.execute(
            "UPDATE app.sandboxes SET last_used_at = now() - interval '25 hours' WHERE id = $1", old
        )
    async with app_pool.acquire() as conn:
        purged = await conn.fetchrow("SELECT * FROM app.purge_expired_copies()")
    assert purged["copies"] >= 1
    async with visitor_tx(app_pool, old) as conn:
        assert await conn.fetchval("SELECT count(*) FROM audit.events") == 0
        assert await conn.fetchval("SELECT count(*) FROM app.messages") == 0
    async with visitor_tx(app_pool, fresh) as conn:
        assert await conn.fetchval("SELECT count(*) FROM audit.events") == 1
