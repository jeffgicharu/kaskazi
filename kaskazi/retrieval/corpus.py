"""Reading the bank's documents and cutting them into retrievable chunks.

A chunk is one section of a document, carrying its heading path ("Debit card terms > Fees >
Using the card abroad") so it makes sense on its own. Long sections are split on paragraph
boundaries; tables are never split. The corpus version is a hash of every chunk, so a release
that changes any document loads a new version beside the old one, and the slot still serving
the previous release keeps reading its own.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "corpus"
DOCUMENTS = ROOT / "documents"
EMBEDDINGS = ROOT / "embeddings" / "vectors.jsonl"

MAX_CHUNK_TOKENS = 450
TARGET_SPLIT_TOKENS = 320
MIN_CHUNK_TOKENS = 40

_FRONT_MATTER = re.compile(r"\A\+\+\+\n(.*?)\n\+\+\+\n", re.S)
_HEADING = re.compile(r"^(#{1,3}) (.+)$", re.M)


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text.split()) * 1.33))


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    audience: str
    topics: list[str]
    version: str
    effective: date
    owner: str
    body: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body.encode()).hexdigest()


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: str
    ord: int
    heading_path: str
    text: str
    audience: str

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.heading_path + " " + self.text)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.heading_path}\n{self.text}".encode()).hexdigest()

    @property
    def embedding_input(self) -> str:
        return f"{self.heading_path}\n\n{self.text}"


def read_document(path: Path) -> Document:
    raw = path.read_text()
    match = _FRONT_MATTER.match(raw)
    if match is None:
        raise ValueError(f"{path.name} has no front matter")
    meta = tomllib.loads(match.group(1))
    if meta["id"] != path.stem:
        raise ValueError(f"{path.name} has id {meta['id']}")
    if meta["audience"] not in ("customer", "staff"):
        raise ValueError(f"{path.name} has audience {meta['audience']}")
    return Document(
        id=meta["id"],
        title=meta["title"],
        audience=meta["audience"],
        topics=list(meta["topics"]),
        version=meta["version"],
        effective=meta["effective"],
        owner=meta["owner"],
        body=raw[match.end() :].strip(),
    )


def read_corpus(directory: Path = DOCUMENTS) -> list[Document]:
    return [read_document(p) for p in sorted(directory.glob("*.md"))]


def chunk_document(doc: Document) -> list[Chunk]:
    sections: list[tuple[list[str], str]] = []
    path: list[str] = [doc.title]
    cursor = 0
    current_path = list(path)
    for m in _HEADING.finditer(doc.body):
        text = doc.body[cursor : m.start()].strip()
        if text:
            sections.append((current_path, text))
        level = len(m.group(1))
        heading = m.group(2).strip()
        path = [heading] if level == 1 else [*path[: level - 1], heading]
        current_path = list(path)
        cursor = m.end()
    tail = doc.body[cursor:].strip()
    if tail:
        sections.append((current_path, tail))

    pieces: list[tuple[str, str]] = []
    for headings, text in sections:
        heading_path = " > ".join(headings)
        for part in _split(text):
            pieces.append((heading_path, part))

    # A very short section joins the next one in the same part of the document, so a chunk is
    # never just a sentence of preamble.
    merged: list[tuple[str, str]] = []
    for heading_path, text in pieces:
        if merged and estimate_tokens(merged[-1][1]) < MIN_CHUNK_TOKENS:
            prev_path, prev_text = merged.pop()
            merged.append(
                (
                    prev_path,
                    f"{prev_text}\n\n{text}"
                    if prev_path == heading_path
                    else f"{prev_text}\n\n{_last(heading_path)}\n{text}",
                )
            )
        else:
            merged.append((heading_path, text))

    return [
        Chunk(
            id=f"{doc.id}#{i}",
            document_id=doc.id,
            ord=i,
            heading_path=heading_path,
            text=text,
            audience=doc.audience,
        )
        for i, (heading_path, text) in enumerate(merged, start=1)
    ]


def _last(heading_path: str) -> str:
    return heading_path.split(" > ")[-1]


def _split(text: str) -> list[str]:
    if estimate_tokens(text) <= MAX_CHUNK_TOKENS:
        return [text]
    blocks = re.split(r"\n\s*\n", text)
    parts: list[str] = []
    current: list[str] = []
    for block in blocks:
        candidate = "\n\n".join([*current, block])
        if current and estimate_tokens(candidate) > TARGET_SPLIT_TOKENS:
            parts.append("\n\n".join(current))
            current = [block]
        else:
            current.append(block)
    if current:
        parts.append("\n\n".join(current))
    return parts


def chunk_corpus(documents: list[Document]) -> list[Chunk]:
    return [c for d in documents for c in chunk_document(d)]


def corpus_version(chunks: list[Chunk], embedding_model: str) -> str:
    digest = hashlib.sha256(embedding_model.encode())
    for chunk in chunks:
        digest.update(chunk.id.encode())
        digest.update(chunk.content_hash.encode())
    return digest.hexdigest()[:16]


# --- Committed embeddings --------------------------------------------------------------------


def pack(vector: list[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode()


def unpack(data: str) -> list[float]:
    raw = base64.b64decode(data)
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def read_embeddings(path: Path = EMBEDDINGS) -> dict[str, list[float]]:
    """Vectors by chunk content hash."""

    if not path.exists():
        return {}
    vectors: dict[str, list[float]] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            vectors[row["hash"]] = unpack(row["vector"])
    return vectors


def write_embeddings(vectors: dict[str, list[float]], model: str, path: Path = EMBEDDINGS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"hash": h, "model": model, "vector": pack(v)})
        for h, v in sorted(vectors.items())
    ]
    path.write_text("\n".join(lines) + "\n")
