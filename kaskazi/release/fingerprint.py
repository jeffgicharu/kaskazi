"""The AI fingerprint: one hash over everything that shapes what the assistant says.

An evaluation report covers exactly one fingerprint. CI computes the fingerprint of the commit
it is releasing and refuses the release unless a passing report covers it, so a change to a
prompt, a model setting, a tool, retrieval, a control, the bank's documents or the golden set
can't reach visitors without an evaluation run on it. A change to the web app or the server
files leaves the fingerprint as it was and needs no new run.

Each part is hashed on its own as well, so a report shows what changed since the one before.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PARTS: dict[str, tuple[str, ...]] = {
    "prompts": ("prompts",),
    "models": ("kaskazi/llm",),  # pinned routes and settings, the adapters, the request contract
    "tools": ("kaskazi/tools", "kaskazi/corebank"),  # tool schemas, the core bank and its rules
    "retrieval": ("kaskazi/retrieval",),
    "controls": ("kaskazi/controls",),
    "assistant": ("kaskazi/assistant",),  # the turn itself and the bank's own templates
    "documents": ("kaskazi/documents",),  # reading documents, the statutory rates, the checks
    "corpus": ("corpus/documents", "corpus/embeddings"),
    "golden_set": ("evals/golden", "evals/answers.py"),
}


def _files(root: Path, entry: str) -> list[Path]:
    path = root / entry
    if path.is_file():
        return [path]
    return sorted(
        p
        for p in path.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )


def fingerprint(
    root: Path = ROOT, overrides: Mapping[str, bytes] | None = None
) -> dict[str, object]:
    """The fingerprint of the tree at `root`.

    `overrides` replaces a file's content by its path relative to `root`, for an evaluation run
    with settings that aren't committed (a variant of the routes, say): its report then covers
    those settings, and matches a release only if the release has them too.
    """
    overrides = overrides or {}
    parts: dict[str, str] = {}
    for name, entries in PARTS.items():
        digest = hashlib.sha256()
        for entry in entries:
            for file in _files(root, entry):
                relative = file.relative_to(root).as_posix()
                content = overrides.get(relative, file.read_bytes())
                digest.update(relative.encode() + b"\0" + hashlib.sha256(content).digest())
        parts[name] = digest.hexdigest()
    total = hashlib.sha256(
        "".join(f"{name}={parts[name]}\n" for name in sorted(parts)).encode()
    ).hexdigest()
    return {"total": total, "parts": parts}
