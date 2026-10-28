"""Retrieval evaluation: does search find the passage a question needs?

For every answerable question in the golden set, it checks whether the expected document (and
the expected section) is among the top k chunks, for three set-ups: vectors only, keywords only,
and the hybrid the assistant uses. Query vectors are cached in evals/cache, keyed by the
question's text, so the run is free once they exist and repeatable in CI.

    uv run python -m evals.retrieval --dsn postgresql://... [--embed-missing]
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


async def run(dsn: str, k: int, embed: bool) -> dict[str, object]:
    documents = read_corpus()
    chunks = chunk_corpus(documents)
    version = corpus_version(chunks, "text-embedding-3-small")
    pool = await create_pool(dsn, max_size=1)
    async with pool.acquire() as conn:
        await load_corpus(conn, version, documents, chunks)

    questions = load_questions()
    cache = read_cache()
    tokens = await embed_missing([str(q["question"]) for q in questions], cache) if embed else 0

    setups = {"vector": (True, False), "keyword": (False, True), "hybrid": (True, True)}
    results: dict[str, dict[str, float]] = {}
    misses: list[dict[str, object]] = []
    for name, (use_vector, use_keyword) in setups.items():
        doc_hits = section_hits = reciprocal = 0.0
        for q in questions:
            audience = ["customer", "staff"] if q.get("audience") == "staff" else ["customer"]
            vector = cache.get(str(q["question"])) if use_vector else None
            async with pool.acquire() as conn, conn.transaction():
                found = await search(
                    conn,
                    version=version,
                    query=str(q["question"]) if use_keyword else "",
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
    args = parser.parse_args()
    report = asyncio.run(run(args.dsn, args.k, args.embed_missing))
    misses = report.pop("hybrid_section_misses")
    print(json.dumps(report, indent=2))
    if args.show_misses:
        for m in misses:  # type: ignore[union-attr]
            print(json.dumps(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
