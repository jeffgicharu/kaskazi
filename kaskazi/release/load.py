"""Loading a release's shared data: the documents, the prompts and the release's record.

Runs at activation with the owner's credentials, before the new slot starts. Each corpus version
is keyed by a hash of its chunks, so loading is idempotent and the slot still serving the
previous release keeps reading its own version. The three most recent versions are kept, like
the releases on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import asyncpg

from kaskazi.llm.routing import load_routes
from kaskazi.prompts.registry import load_prompts
from kaskazi.retrieval.corpus import chunk_corpus, corpus_version, read_corpus
from kaskazi.retrieval.store import load_corpus

KEEP_VERSIONS = 3
REPORTS = Path(__file__).resolve().parents[2] / "evals" / "reports"


async def load_release(dsn: str, release: str) -> str:
    documents = read_corpus()
    chunks = chunk_corpus(documents)
    _, embedding = load_routes()
    version = corpus_version(chunks, embedding.model)
    conn = await asyncpg.connect(dsn)
    try:
        loaded = await load_corpus(conn, version, documents, chunks)
        dropped = await conn.execute(
            """DELETE FROM kb.corpus_versions WHERE version NOT IN (
                   SELECT version FROM kb.corpus_versions ORDER BY loaded_at DESC LIMIT $1)
               AND version <> $2""",
            KEEP_VERSIONS,
            version,
        )
        prompts = load_prompts()
        new_prompts = 0
        for p in prompts.values():
            result = await conn.execute(
                """INSERT INTO app.prompt_versions
                       (prompt_id, version, owner, route, content_hash, change_note, changed_on, body, first_release)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                   ON CONFLICT (prompt_id, version) DO NOTHING""",
                p.id,
                p.version,
                p.owner,
                p.route,
                p.content_hash,
                p.change_note,
                p.changed_on,
                p.body,
                release,
            )
            new_prompts += int(result.split()[-1])
        report = _latest_report()
        await conn.execute(
            """INSERT INTO app.releases (sha, eval_report) VALUES ($1, $2::jsonb)
               ON CONFLICT (sha) DO UPDATE SET activated_at = now(), eval_report = EXCLUDED.eval_report""",
            release,
            json.dumps(report) if report is not None else None,
        )
    finally:
        await conn.close()
    return (
        f"corpus {version} ({len(documents)} documents, {len(chunks)} chunks, "
        f"{'loaded' if loaded else 'already there'}); {dropped.split()[-1]} old versions dropped; "
        f"{len(prompts)} prompts, {new_prompts} new versions"
    )


def _latest_report() -> dict[str, object] | None:
    path = REPORTS / "latest.json"
    if not path.exists():
        return None
    data: dict[str, object] = json.loads(path.read_text())
    return data
