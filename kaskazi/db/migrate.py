"""Applies the numbered SQL migrations, once each, in order.

Runs at activation with the owner's credentials. A migration that was applied and has since
changed is refused: migrations are only ever added, never edited. Every migration must also be
safe for the release before it, which keeps running on the same database while the next one
starts (expand first, contract in a later release).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import asyncpg

MIGRATIONS = Path(__file__).with_name("migrations")
LOCK_ID = 0x6B61736B  # "kask"


class MigrationChanged(Exception):
    pass


def migration_files() -> list[tuple[str, str, str]]:
    files = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text()
        files.append((path.stem, hashlib.sha256(sql.encode()).hexdigest(), sql))
    return files


async def migrate(dsn: str) -> list[str]:
    conn = await asyncpg.connect(dsn)
    applied_now: list[str] = []
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", LOCK_ID)
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS public.schema_migrations (
                   version text PRIMARY KEY,
                   checksum text NOT NULL,
                   applied_at timestamptz NOT NULL DEFAULT now())"""
        )
        done = {
            r["version"]: r["checksum"]
            for r in await conn.fetch("SELECT * FROM public.schema_migrations")
        }
        for version, checksum, sql in migration_files():
            if version in done:
                if done[version] != checksum:
                    raise MigrationChanged(f"{version} was applied and has changed since")
                continue
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO public.schema_migrations (version, checksum) VALUES ($1, $2)",
                    version,
                    checksum,
                )
            applied_now.append(version)
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_ID)
        await conn.close()
    return applied_now
