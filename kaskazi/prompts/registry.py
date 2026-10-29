"""Prompts as code: every prompt is a versioned file with an owner and a change history.

    prompts/<prompt id>/<version>.md     TOML front matter between +++ lines, then the text

The newest version of each prompt is the one in use. prompts/prompts.lock records the hash of
every version, and a test fails if a version's text changes after it was locked: a change to a
prompt is always a new version with a note saying what changed and why. Every model call
records the prompt id, version and hash it used, and the console shows the history.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "prompts"
LOCK = ROOT / "prompts.lock"
_FRONT = re.compile(r"\A\+\+\+\n(.*?)\n\+\+\+\n", re.S)
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    owner: str
    route: str
    changed_on: date
    change_note: str
    body: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body.encode()).hexdigest()

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


def semver(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(x) for x in version.split("."))
    return major, minor, patch


def read_prompt(path: Path) -> Prompt:
    raw = path.read_text()
    match = _FRONT.match(raw)
    if match is None:
        raise ValueError(f"{path} has no front matter")
    meta = tomllib.loads(match.group(1))
    if meta["id"] != path.parent.name or meta["version"] != path.stem:
        raise ValueError(f"{path} names {meta['id']}@{meta['version']}")
    if not _VERSION.match(meta["version"]):
        raise ValueError(f"{path}: versions are major.minor.patch")
    return Prompt(
        id=meta["id"],
        version=meta["version"],
        owner=meta["owner"],
        route=meta["route"],
        changed_on=meta["changed_on"],
        change_note=meta["change"],
        body=raw[match.end() :].strip() + "\n",
    )


def all_versions(root: Path = ROOT) -> list[Prompt]:
    return [read_prompt(p) for p in sorted(root.glob("*/*.md"))]


@cache
def load_prompts(root: Path = ROOT) -> dict[str, Prompt]:
    """The version in use (the newest) of every prompt, by id."""

    current: dict[str, Prompt] = {}
    for p in all_versions(root):
        if p.id not in current or semver(p.version) > semver(current[p.id].version):
            current[p.id] = p
    return current


def prompt(prompt_id: str) -> Prompt:
    return load_prompts()[prompt_id]


def lock_problems(root: Path = ROOT, lock: Path = LOCK) -> list[str]:
    """Versions whose text changed after they were locked, and versions not locked yet."""

    locked: dict[str, dict[str, str]] = json.loads(lock.read_text()) if lock.exists() else {}
    problems = []
    for p in all_versions(root):
        known = locked.get(p.id, {}).get(p.version)
        if known is None:
            problems.append(f"{p.ref} is not in prompts.lock; run python -m kaskazi.prompts.lock")
        elif known != p.content_hash:
            problems.append(f"{p.ref} changed after it was locked; make the change a new version")
    return problems


def write_lock(root: Path = ROOT, lock: Path = LOCK) -> None:
    locked: dict[str, dict[str, str]] = json.loads(lock.read_text()) if lock.exists() else {}
    for p in all_versions(root):
        locked.setdefault(p.id, {}).setdefault(p.version, p.content_hash)
    lock.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n")
