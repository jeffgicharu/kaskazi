"""The release gate: the AI fingerprint and the golden-set report it must have."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kaskazi.release.evalgate import check_release, gate, summarise
from kaskazi.release.fingerprint import PARTS, fingerprint


def tree(root: Path) -> Path:
    for entries in PARTS.values():
        for entry in entries:
            path = root / entry
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {entry}\n")
            else:
                path.mkdir(parents=True, exist_ok=True)
                (path / "a.txt").write_text(f"{entry}\n")
    (root / "web").mkdir()
    (root / "web" / "app.tsx").write_text("export {}\n")
    return root


def result(kind: str = "answerable", **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "x",
        "kind": kind,
        "outcome": "answered",
        "source_doc": "faq" if kind == "answerable" else None,
        "cited_documents": ["faq"],
        "checks": {},
        "criteria": [{"text": "t", "verdict": "met", "why": "w"}],
        "latency_ms": 4000,
        "cost_usd": 0.001,
        "fallback": False,
        "expect_card": False,
    }
    return {**base, **extra}


def test_the_fingerprint_follows_the_ai_and_nothing_else(tmp_path: Path) -> None:
    root = tree(tmp_path)
    before = fingerprint(root)
    (root / "web" / "app.tsx").write_text("export const changed = 1\n")
    assert fingerprint(root) == before  # the web app isn't part of it

    (root / "prompts" / "a.txt").write_text("a new prompt version\n")
    after = fingerprint(root)
    assert after["total"] != before["total"]
    changed = [k for k in PARTS if after["parts"][k] != before["parts"][k]]  # type: ignore[index]
    assert changed == ["prompts"]

    variant = fingerprint(root, {"kaskazi/llm/a.txt": b"other settings\n"})
    assert variant["total"] != after["total"]


def test_the_gate_holds_floors_and_catches_regressions() -> None:
    good = summarise(
        [result() for _ in range(20)] + [result("behaviour", checks={"behaviour_ok": True})]
    )
    assert gate(good, None) == []

    refused = summarise(
        [result() for _ in range(20)] + [result("behaviour", checks={"behaviour_ok": False})]
    )
    assert gate(refused, None) == ["behaviour_ok 0.0 is below the floor of 1.0"]

    worse = summarise(
        [result() for _ in range(18)]
        + [result(criteria=[{"text": "t", "verdict": "missed", "why": "w"}]) for _ in range(2)]
    )
    assert gate(worse, None) == []  # 0.9 clears the floor of 0.85
    assert gate(worse, good) == [
        "key_fact fell from 1.0 to 0.9",
        "fact_coverage fell from 1.0 to 0.9",
    ]


def test_a_release_needs_a_passing_report_that_covers_it(tmp_path: Path) -> None:
    root = tree(tmp_path / "repo")
    reports = tmp_path / "reports"
    reports.mkdir()
    ok, why = check_release(root, reports)
    assert not ok and "no evaluation report covers" in why

    results = [result() for _ in range(20)]
    report = {
        "run_on": "2026-10-29T10:00:00+00:00",
        "fingerprint": fingerprint(root),
        "baseline": None,
        "metrics": summarise(results),
        "results": results,
    }
    (reports / "answers-a.json").write_text(json.dumps(report))
    ok, why = check_release(root, reports)
    assert not ok and "no document desk report covers" in why

    read = {"want": "1", "got": "1", "right": True, "confidence": 0.95}
    documents = {
        "run_on": "2026-10-29T10:00:00+00:00",
        "fingerprint": fingerprint(root),
        "results": [
            {"path": "text", "defects": [], "caught": {}, "fields": [read] * 5},
            {
                "path": "text",
                "defects": ["net_mismatch"],
                "caught": {"net_mismatch": True},
                "fields": [read],
            },
        ],
    }
    (reports / "documents-a.json").write_text(json.dumps(documents))
    ok, why = check_release(root, reports)
    assert ok, why

    # A summary edited by hand no longer matches the results it came from.
    report["metrics"] = {**report["metrics"], "fact_coverage": 1.0, "contradicted": 0}  # type: ignore[dict-item]
    results[0]["criteria"] = [{"text": "t", "verdict": "contradicted", "why": "w"}] * 3
    (reports / "answers-a.json").write_text(json.dumps(report))
    ok, why = check_release(root, reports)
    assert not ok and "don't match its own results" in why

    # A change to the AI needs a new report.
    (root / "prompts" / "a.txt").write_text("changed\n")
    ok, why = check_release(root, reports)
    assert not ok and "no evaluation report covers" in why


def test_the_live_golden_questions_suit_the_check_copy() -> None:
    from kaskazi.release.golden import LIVE, live_cases, problem

    cases = live_cases()
    assert [c["id"] for c in cases] == list(LIVE)
    assert {c["persona"] for c in cases} == {"teacher"}  # the check copy is always the teacher
    assert {c["kind"] for c in cases} >= {"answerable", "unanswerable", "tool", "behaviour"}

    fee = next(c for c in cases if c["kind"] == "answerable")
    cited = {"outcome": "answered", "reply": {"sources": [{"n": 1}], "cards": []}}
    assert problem(fee, cited) is None
    assert (
        problem(fee, {**cited, "reply": {"sources": [], "cards": []}}) == "the answer cites nothing"
    )
    fraud = next(c for c in cases if c.get("expect_outcome") == "handover")
    assert problem(fraud, cited) == "answered, not handover"


def test_a_new_run_is_compared_with_the_last_passing_one(tmp_path: Path) -> None:
    from kaskazi.release.evalgate import latest_passing

    def write(
        name: str, run_on: str, results: list[dict[str, Any]], label: str | None = None
    ) -> None:
        body = {"run_on": run_on, "label": label, "baseline": None, "results": results}
        (tmp_path / f"answers-{name}.json").write_text(json.dumps(body))

    good = [result() for _ in range(20)]
    failing = [*good, result("behaviour", checks={"behaviour_ok": False})]
    write("a", "2026-10-29T01:00:00+00:00", good)
    write("b", "2026-10-29T02:00:00+00:00", failing)  # newer, but fails the gate
    write("c", "2026-10-29T03:00:00+00:00", good, label="variant")  # passes, but a variant
    assert latest_passing(tmp_path) == tmp_path / "answers-a.json"
