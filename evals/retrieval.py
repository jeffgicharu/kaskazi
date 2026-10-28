"""Retrieval evaluation: does search find the passage a question needs?

For every answerable question in the golden set, it checks whether the expected document (and
the expected section) is among the top k chunks, for three set-ups: vectors only, keywords only,
and the hybrid the assistant uses. Query vectors are cached in evals/cache, keyed by the
question's text, so the run is free once they exist and repeatable in CI.

With --router-queries it also searches with the router's rewrite of each question (its
`search_query`), for the vector part, the keyword part or both, so the choice of what a turn
searches with is measured too. Rewrites are cached in evals/cache/router_queries.jsonl; making
the missing ones calls the real router (about $0.0002 a question).

    uv run python -m evals.retrieval --dsn postgresql://... [--embed-missing] [--router-queries]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from kaskazi.db.pool import create_pool
from kaskazi.retrieval.corpus import chunk_corpus, corpus_version, pack, read_corpus, unpack
from kaskazi.retrieval.store import load_corpus, search

HERE = Path(__file__).parent
QUERY_CACHE = HERE / "cache" / "query_vectors.jsonl"
ROUTER_CACHE = HERE / "cache" / "router_queries.jsonl"


def load_questions() -> list[dict[str, object]]:
    path = HERE / "golden" / "questions.jsonl"
    if not path.exists():
        path = HERE / "drafts"
        files = sorted(path.glob("*.jsonl"))
    else:
        files = [path]
    rows: list[dict[str, object]] = []
    for f in files:
        rows += [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    return [r for r in rows if r.get("kind") == "answerable"]


def read_cache() -> dict[str, list[float]]:
    if not QUERY_CACHE.exists():
        return {}
    out = {}
    for line in QUERY_CACHE.read_text().splitlines():
        row = json.loads(line)
        out[row["text"]] = unpack(row["vector"])
    return out


async def embed_missing(texts: list[str], cache: dict[str, list[float]]) -> int:
    from kaskazi.llm.providers.openai import OpenAIProvider
    from kaskazi.llm.routing import load_routes

    missing = [t for t in texts if t not in cache]
    if not missing:
        return 0
    _, route = load_routes()
    provider = OpenAIProvider(os.environ["OPENAI_API_KEY"])
    tokens = 0
    try:
        for start in range(0, len(missing), 100):
            batch = missing[start : start + 100]
            vectors, used = await provider.embed(batch, route.model, route.dimensions, 60)
            tokens += used
            cache.update(zip(batch, vectors, strict=True))
    finally:
        await provider.aclose()
    QUERY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    QUERY_CACHE.write_text(
        "".join(json.dumps({"text": t, "vector": pack(v)}) + "\n" for t, v in sorted(cache.items()))
    )
    return tokens


async def router_queries(questions: list[str]) -> dict[str, str]:
    """The router's search query for each question, as a first message of a conversation."""
    from kaskazi.assistant.turn import router_request
    from kaskazi.controls.pii import Vault, mask
    from kaskazi.llm.contract import BudgetContext
    from kaskazi.llm.providers.anthropic import AnthropicProvider
    from kaskazi.llm.routing import load_routes

    cache: dict[str, str] = {}
    if ROUTER_CACHE.exists():
        for line in ROUTER_CACHE.read_text().splitlines():
            row = json.loads(line)
            cache[row["question"]] = row["search_query"]
    missing = [q for q in questions if q not in cache]
    if missing:
        routes, _ = load_routes()
        settings = routes["assistant.router"].primary_settings()
        provider = AnthropicProvider(
            os.environ["ANTHROPIC_KEY"], os.environ["ANTHROPIC_WORKSPACE_ID"]
        )
        gate = asyncio.Semaphore(6)

        async def one(question: str) -> None:
            async with gate:
                request = router_request(
                    [], mask(question, Vault()).text, BudgetContext(), "eval-retrieval"
                )
                reply = await provider.generate(request, settings)
                cache[question] = str((reply.structured or {}).get("search_query") or question)

        try:
            await asyncio.gather(*(one(q) for q in missing))
        finally:
            await provider.aclose()
        ROUTER_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ROUTER_CACHE.write_text(
            "".join(
                json.dumps({"question": q, "search_query": cache[q]}) + "\n" for q in sorted(cache)
            )
        )
    return cache


async def run(dsn: str, k: int, embed: bool, rewrites: bool = False) -> dict[str, object]:
    documents = read_corpus()
    chunks = chunk_corpus(documents)
    version = corpus_version(chunks, "text-embedding-3-small")
    pool = await create_pool(dsn, max_size=1)
    async with pool.acquire() as conn:
        await load_corpus(conn, version, documents, chunks)

    questions = load_questions()
    texts = [str(q["question"]) for q in questions]
    rewritten = await router_queries(texts) if rewrites else {}
    cache = read_cache()
    tokens = await embed_missing(texts + list(rewritten.values()), cache) if embed else 0

    # (what the vector part searches with, what the keyword part searches with)
    setups: dict[str, tuple[str | None, str | None]] = {
        "vector": ("question", None),
        "keyword": (None, "question"),
        "hybrid": ("question", "question"),
    }
    if rewrites:
        setups["hybrid, router keywords"] = ("question", "rewrite")
        setups["hybrid, router rewrite"] = ("rewrite", "rewrite")
    results: dict[str, dict[str, float]] = {}
    misses: list[dict[str, object]] = []
    for name, (vector_from, keywords_from) in setups.items():
        doc_hits = section_hits = reciprocal = 0.0
        for q in questions:
            audience = ["customer", "staff"] if q.get("audience") == "staff" else ["customer"]
            text = str(q["question"])
            source = {"question": text, "rewrite": rewritten.get(text, text)}
            vector = cache.get(source[vector_from]) if vector_from else None
            async with pool.acquire() as conn, conn.transaction():
                found = await search(
                    conn,
                    version=version,
                    query=source[keywords_from] if keywords_from else "",
                    query_vector=vector,
                    audiences=audience,
                    k=k,
                )
            docs = [h.document_id for h in found.hits]
            sections = [h.heading_path.split(" > ")[-1] for h in found.hits]
            if q["source_doc"] in docs:
                doc_hits += 1
                reciprocal += 1 / (docs.index(str(q["source_doc"])) + 1)
            if q.get("source_section") in sections and q["source_doc"] in docs:
                section_hits += 1
            elif name == "hybrid":
                misses.append(
                    {
                        "id": q["id"],
                        "question": q["question"],
                        "want": q["source_doc"],
                        "section": q.get("source_section"),
                        "got": docs[:3],
                    }
                )
        n = len(questions)
        results[name] = {
            "document_recall": round(doc_hits / n, 3),
            "section_recall": round(section_hits / n, 3),
            "mrr": round(reciprocal / n, 3),
        }
    await pool.close()
    return {
        "questions": len(questions),
        "k": k,
        "corpus_version": version,
        "chunks": len(chunks),
        "results": results,
        "embedding_tokens": tokens,
        "hybrid_section_misses": misses,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--embed-missing", action="store_true")
    parser.add_argument("--show-misses", action="store_true")
    parser.add_argument("--router-queries", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(run(args.dsn, args.k, args.embed_missing, args.router_queries))
    misses = report.pop("hybrid_section_misses")
    print(json.dumps(report, indent=2))
    if args.show_misses:
        for m in misses:  # type: ignore[union-attr]
            print(json.dumps(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
