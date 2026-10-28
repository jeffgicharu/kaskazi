"""Prompts as code: versions are locked, owned, and point at real routes."""

from __future__ import annotations

from kaskazi.llm.routing import load_routes
from kaskazi.prompts.registry import all_versions, load_prompts, lock_problems


def test_no_locked_prompt_has_changed_and_every_version_is_locked() -> None:
    assert lock_problems() == []


def test_every_prompt_has_an_owner_a_change_note_and_a_real_route() -> None:
    routes, _ = load_routes()
    for p in all_versions():
        assert p.owner and p.change_note
        assert p.route in routes, p.ref


def test_the_newest_version_is_the_one_in_use() -> None:
    current = load_prompts()
    for p in all_versions():
        newest = current[p.id]
        assert tuple(map(int, newest.version.split("."))) >= tuple(map(int, p.version.split(".")))
