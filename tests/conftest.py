"""Test fixtures: a fresh database per session, with the three roles the server has.

Needs PostgreSQL 16 with pgvector. Locally that is a container (see CONTRIBUTING.md); in CI it
is a service container. Set KASKAZI_TEST_SUPERUSER_DSN to point elsewhere.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from kaskazi.db.migrate import migrate
from kaskazi.db.pool import create_pool

SUPERUSER_DSN = os.environ.get(
    "KASKAZI_TEST_SUPERUSER_DSN", "postgresql://postgres:local-dev-only@127.0.0.1:55432/postgres"
)
ROLES = {"kaskazi_owner": 3, "kaskazi_app": 12, "kaskazi_gateway": 6}
PASSWORD = "test-only-password"


def _dsn(user: str, database: str) -> str:
    base = SUPERUSER_DSN.rsplit("/", 1)[0].split("@", 1)[1]
    return f"postgresql://{user}:{PASSWORD}@{base}/{database}"


@pytest.fixture(scope="session")
async def database() -> AsyncIterator[str]:
    name = f"kaskazi_test_{uuid.uuid4().hex[:8]}"
    su = await asyncpg.connect(SUPERUSER_DSN)
    try:
        for role, limit in ROLES.items():
            exists = await su.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
            if not exists:
                await su.execute(
                    f"CREATE ROLE {role} LOGIN NOINHERIT CONNECTION LIMIT {limit} PASSWORD '{PASSWORD}'"
                )
        await su.execute(f"CREATE DATABASE {name} OWNER kaskazi_owner")
        db = await asyncpg.connect(SUPERUSER_DSN.rsplit("/", 1)[0] + f"/{name}")
        await db.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await db.execute(f"REVOKE ALL ON DATABASE {name} FROM PUBLIC")
        await db.execute(f"GRANT CONNECT ON DATABASE {name} TO kaskazi_app, kaskazi_gateway")
        await db.close()
        await migrate(_dsn("kaskazi_owner", name))
        yield name
    finally:
        await su.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", name
        )
        await su.execute(f"DROP DATABASE IF EXISTS {name}")
        await su.close()


@pytest.fixture(scope="session")
async def app_pool(database: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(_dsn("kaskazi_app", database), max_size=2)
    yield pool
    await pool.close()


@pytest.fixture(scope="session")
async def gateway_pool(database: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(_dsn("kaskazi_gateway", database), max_size=2)
    yield pool
    await pool.close()


@pytest.fixture(scope="session")
async def owner_dsn(database: str) -> str:
    return _dsn("kaskazi_owner", database)
