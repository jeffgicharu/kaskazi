"""A deterministic stand-in for embeddings, for tests and CI's browser tests.

Words are hashed into a fixed number of buckets, so texts that share words point in similar
directions. It is not a semantic model and is never used to measure retrieval quality.
"""

from __future__ import annotations

import hashlib
import math
import re

_WORD = re.compile(r"[a-z0-9]+")


def hashed_vector(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    for word in _WORD.findall(text.lower()):
        digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]
