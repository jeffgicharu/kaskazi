"""Embeds every chunk that has no committed vector yet, and writes corpus/embeddings.

Run after changing a document: `uv run python -m kaskazi.retrieval.embed_corpus`. It calls the
embeddings API directly (the corpus holds no personal data; a test checks that), and prints the
tokens used and what they cost.
"""

from __future__ import annotations

import asyncio
import os
import sys

from kaskazi.llm.contract import Usage
from kaskazi.llm.prices import cost_usd
from kaskazi.llm.providers.openai import OpenAIProvider
from kaskazi.llm.routing import load_routes
from kaskazi.retrieval.corpus import chunk_corpus, read_corpus, read_embeddings, write_embeddings

BATCH = 100


async def main() -> int:
    _, route = load_routes()
    chunks = chunk_corpus(read_corpus())
    vectors = read_embeddings()
    wanted = {c.content_hash: c for c in chunks}
    missing = [c for h, c in wanted.items() if h not in vectors]
    print(
        f"{len(chunks)} chunks, {len(missing)} to embed "
        f"with {route.model} at {route.dimensions} dimensions"
    )
    tokens = 0
    if missing:
        provider = OpenAIProvider(os.environ["OPENAI_API_KEY"])
        try:
            for start in range(0, len(missing), BATCH):
                batch = missing[start : start + BATCH]
                got, used = await provider.embed(
                    [c.embedding_input for c in batch], route.model, route.dimensions, 60
                )
                tokens += used
                for chunk, vector in zip(batch, got, strict=True):
                    vectors[chunk.content_hash] = vector
        finally:
            await provider.aclose()
    kept = {h: v for h, v in vectors.items() if h in wanted}
    write_embeddings(kept, route.model)
    cost = cost_usd(route.model, Usage(input_tokens=tokens))
    print(f"wrote {len(kept)} vectors; {tokens:,} tokens, ${cost:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
