"""The knowledge base in PostgreSQL: loading a corpus version, and hybrid search over it.

Search runs two retrievers over the same chunks and merges them with reciprocal rank fusion:
- vector similarity (pgvector, cosine, HNSW index) catches paraphrases;
- full-text search (PostgreSQL tsvector, OR over the query's terms) catches exact product names,
  fees and numbers.
Each retriever only sees chunks for the caller's audience, so a customer is never shown a staff
procedure. If the query embedding is unavailable, keyword search answers alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import asyncpg

from kaskazi.retrieval.corpus import Chunk, Document, read_embeddings

RRF_K = 60
CANDIDATES = 24
# Chosen by evals/retrieval.py on 98 golden questions (29 October): at k = 8, hybrid search with
# keyword results at half weight matched vector search on fact recall (0.970) and ranked the
# right passage higher (MRR 0.770 against 0.706). Length normalisation made ranking worse.
KEYWORD_WEIGHT = 0.5
RANK_NORMALIZATION = 0
DEFAULT_K = 8


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    document_id: str
    title: str
    heading_path: str
    text: str
    score: float
    vector_rank: int | None
    keyword_rank: int | None
    similarity: float | None


@dataclass
class SearchResult:
    hits: list[Hit]
    keyword_only: bool
    candidates: dict[str, int] = field(default_factory=dict)

    @property
    def top_similarity(self) -> float:
        sims = [h.similarity for h in self.hits if h.similarity is not None]
        return max(sims, default=0.0)


async def load_corpus(
    conn: asyncpg.Connection,
    version: str,
    documents: list[Document],
    chunks: list[Chunk],
) -> bool:
    """Loads a corpus version if it isn't there already. Returns True if it was loaded now."""

    if await conn.fetchval("SELECT 1 FROM kb.corpus_versions WHERE version = $1", version):
        return False
    vectors = read_embeddings()
    missing = [c.id for c in chunks if c.content_hash not in vectors]
    if missing:
        raise ValueError(
            f"{len(missing)} chunks have no embedding, e.g. {missing[0]}; run the embed step"
        )
    async with conn.transaction():
        await conn.execute("INSERT INTO kb.corpus_versions (version) VALUES ($1)", version)
        await conn.executemany(
            """INSERT INTO kb.documents
                   (corpus_version, id, title, audience, topics, version, effective, owner,
                    content_hash, body)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
            [
                (
                    version,
                    d.id,
                    d.title,
                    d.audience,
                    d.topics,
                    d.version,
                    d.effective,
                    d.owner,
                    d.content_hash,
                    d.body,
                )
                for d in documents
            ],
        )
        await conn.executemany(
            """INSERT INTO kb.chunks
                   (corpus_version, id, document_id, ord, heading_path, text, audience, tokens,
                    content_hash, embedding)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::text::vector)""",
            [
                (
                    version,
                    c.id,
                    c.document_id,
                    c.ord,
                    c.heading_path,
                    c.text,
                    c.audience,
                    c.tokens,
                    c.content_hash,
                    vector_literal(vectors[c.content_hash]),
                )
                for c in chunks
            ],
        )
    return True


async def drop_other_versions(conn: asyncpg.Connection, keep: list[str]) -> int:
    result: str = await conn.execute(
        "DELETE FROM kb.corpus_versions WHERE NOT (version = ANY($1::text[]))", keep
    )
    return int(result.split()[-1])


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


_VECTOR_SQL = f"""
    SELECT c.id, 1 - (c.embedding <=> $1::text::vector) AS similarity
    FROM kb.chunks c
    WHERE c.corpus_version = $2 AND c.audience = ANY($3::text[])
    ORDER BY c.embedding <=> $1::text::vector
    LIMIT {CANDIDATES}
"""  # noqa: S608 - a module constant

# OR over the query's own lexemes, so a long question still matches chunks that share some of
# its words; ts_rank_cd rewards chunks where they appear close together.
_KEYWORD_SQL_TEMPLATE = """
    WITH q AS (
        SELECT to_tsquery('english', string_agg(lexeme, ' | ')) AS query
        FROM unnest(to_tsvector('english', $1)) AS t(lexeme, positions, weights)
    )
    SELECT c.id, ts_rank_cd(c.tsv, q.query, {normalization}) AS rank
    FROM kb.chunks c, q
    WHERE q.query IS NOT NULL AND c.corpus_version = $2 AND c.audience = ANY($3::text[])
      AND c.tsv @@ q.query
    ORDER BY rank DESC
    LIMIT {candidates}
"""


async def search(
    conn: asyncpg.Connection,
    *,
    version: str,
    query: str,
    query_vector: list[float] | None,
    audiences: list[str],
    k: int = DEFAULT_K,
    keyword_weight: float = KEYWORD_WEIGHT,
    normalization: int = RANK_NORMALIZATION,
) -> SearchResult:
    vector_ranks: dict[str, tuple[int, float]] = {}
    if query_vector is not None:
        await conn.execute("SET LOCAL hnsw.ef_search = 100")
        await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        rows = await conn.fetch(_VECTOR_SQL, vector_literal(query_vector), version, audiences)
        vector_ranks = {r["id"]: (i, float(r["similarity"])) for i, r in enumerate(rows, start=1)}
    # Both values are integers chosen in code, never input.
    keyword_sql = _KEYWORD_SQL_TEMPLATE.format(
        normalization=int(normalization), candidates=CANDIDATES
    )
    rows = await conn.fetch(keyword_sql, query, version, audiences) if query else []
    keyword_ranks = {r["id"]: i for i, r in enumerate(rows, start=1)}

    scores: dict[str, float] = {}
    for chunk_id, (rank, _) in vector_ranks.items():
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    for chunk_id, rank in keyword_ranks.items():
        scores[chunk_id] = scores.get(chunk_id, 0.0) + keyword_weight / (RRF_K + rank)
    top = sorted(scores, key=lambda cid: -scores[cid])[:k]
    if not top:
        return SearchResult(hits=[], keyword_only=query_vector is None)

    details = await conn.fetch(
        """SELECT c.id, c.document_id, d.title, c.heading_path, c.text
           FROM kb.chunks c JOIN kb.documents d
             ON d.corpus_version = c.corpus_version AND d.id = c.document_id
           WHERE c.corpus_version = $1 AND c.id = ANY($2::text[])""",
        version,
        top,
    )
    by_id = {r["id"]: r for r in details}
    hits = [
        Hit(
            chunk_id=cid,
            document_id=by_id[cid]["document_id"],
            title=by_id[cid]["title"],
            heading_path=by_id[cid]["heading_path"],
            text=by_id[cid]["text"],
            score=round(scores[cid], 6),
            vector_rank=vector_ranks[cid][0] if cid in vector_ranks else None,
            keyword_rank=keyword_ranks.get(cid),
            similarity=round(vector_ranks[cid][1], 4) if cid in vector_ranks else None,
        )
        for cid in top
    ]
    return SearchResult(hits=hits, keyword_only=query_vector is None)
