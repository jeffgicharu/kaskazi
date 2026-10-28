"""Database connections, and transactions that act for exactly one visitor."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg


async def _init(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str, *, max_size: int) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=max_size,
        init=_init,
        command_timeout=15,
        max_inactive_connection_lifetime=300,
    )
    assert pool is not None
    return pool


@asynccontextmanager
async def visitor_tx(pool: asyncpg.Pool, visitor: UUID) -> AsyncIterator[asyncpg.Connection]:
    """A transaction in which row security shows only `visitor`'s copy.

    The visitor is set with `set_config(..., true)`, which lasts until the end of this
    transaction only. A connection handed back to the pool carries nobody, so the next request
    on it sees nothing until it names its own visitor.
    """

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.visitor', $1, true)", str(visitor))
        yield conn


@asynccontextmanager
async def plain_tx(pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Connection]:
    """A transaction for shared data (the knowledge base, the job queue, monitoring)."""

    async with pool.acquire() as conn, conn.transaction():
        yield conn
