"""How far the judge can be trusted: its verdicts against a careful reader's, on real answers.

evals/judge/calibration.jsonl holds answers from golden-set runs, each with its criteria and the
verdict a person gave every criterion after reading the answer and the passages it cited. This
runs one judge set-up over them and counts agreement, and above all how often the judge says
"contradicted" when a reader wouldn't: a false contradiction fails a release for nothing.

    uv run python -m evals.judge_calibration build --report evals/reports/answers-....json
    uv run python -m evals.judge_calibration measure [--model claude-haiku-5-5] [--effort medium]
                                                     [--prompt 1.1.0]

`build` adds unlabelled cases from a report (labels are then written by hand); `measure` calls
the judge and spends a little (a few US cents for Haiku).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import asyncpg

from evals.answers import judge, load_cases, tool_results
from kaskazi.llm.providers.anthropic import AnthropicProvider
from kaskazi.llm.providers.base import CallSettings
from kaskazi.prompts.registry import all_versions

HERE = Path(__file__).parent
CALIBRATION = HERE / "judge" / "calibration.jsonl"
DB = "postgresql://postgres:local-dev-only@127.0.0.1:55432/kaskazi_dev"


async def sources_for(db: asyncpg.Connection, result: dict[str, Any]) -> list[dict[str, Any]]:
    """The passages the answer cited, from the stack's own record of the conversation."""
    if result.get("sources") is not None:
        return list(result["sources"])
    conversation = result.get("conversation_id")
    if conversation is None:
        conversation = await db.fetchval(
            "SELECT conversation_id FROM app.messages WHERE author = 'customer' "
            "AND display->>'text' = $1 ORDER BY id DESC LIMIT 1",
            result["question"],
        )
    reply = await db.fetchval(
        "SELECT display FROM app.messages WHERE conversation_id = $1 AND author = 'assistant' "
        "ORDER BY seq DESC LIMIT 1",
        uuid.UUID(str(conversation)),
    )
    reply = json.loads(reply) if isinstance(reply, str) else reply
    return [
        {"document": s["document_id"], "section": s["section"], "quote": s["quote"]}
        for s in (reply or {}).get("sources", [])
    ]


async def build(
    report: dict[str, Any], name: str, ids: list[str], existing: set[str]
) -> list[dict[str, Any]]:
    cases = {c["id"]: c for c in load_cases()}
    db = await asyncpg.connect(DB)
    added = []
    try:
        for result in report["results"]:
            if result["id"] not in ids or result["id"] in existing or not result.get("criteria"):
                continue
            added.append(
                {
                    "id": result["id"],
                    "from_report": name,
                    "question": result["question"],
                    "before": cases[result["id"]].get("before", []),
                    "answer": result["answer"],
                    "sources": await sources_for(db, result),
                    "records": result.get("records")
                    or (
                        await tool_results(db, result["conversation_id"])
                        if result.get("conversation_id")
                        else []
                    ),
                    "criteria": [c["text"] for c in result["criteria"]],
                    "labels": [None for _ in result["criteria"]],
                }
            )
    finally:
        await db.close()
    return added


async def measure(
    rows: list[dict[str, Any]], model: str, effort: str, version: str
) -> dict[str, Any]:
    rows = [r for r in rows if all(label is not None for label in r["labels"])]
    judge_prompt = next(p for p in all_versions() if p.id == "evals.judge" and p.version == version)
    settings = CallSettings(model, effort, 2048, 60)
    provider = AnthropicProvider(os.environ["ANTHROPIC_KEY"], os.environ["ANTHROPIC_WORKSPACE_ID"])
    pairs: list[tuple[str, str]] = []
    cost = 0.0
    disagreements = []
    try:
        for row in rows:
            case = {
                "id": row["id"],
                "question": row["question"],
                "before": row["before"],
                "expected_facts": row["criteria"],
            }
            graded, spent = await judge(
                provider,
                case,
                row["answer"],
                row["sources"],
                row.get("records", []),
                settings,
                judge_prompt,
            )
            cost += spent
            for criterion, label, verdict in zip(
                row["criteria"], row["labels"], graded, strict=True
            ):
                pairs.append((label, verdict["verdict"]))
                if label != verdict["verdict"]:
                    disagreements.append(
                        {
                            "id": row["id"],
                            "criterion": criterion,
                            "reader": label,
                            "judge": verdict["verdict"],
                            "why": verdict["why"],
                        }
                    )
    finally:
        await provider.aclose()
    agree = sum(a == b for a, b in pairs)
    said_contradicted = [a for a, b in pairs if b == "contradicted"]
    return {
        "judge": {"model": model, "effort": effort, "prompt": f"evals.judge@{version}"},
        "cases": len(rows),
        "criteria": len(pairs),
        "agreement": round(agree / len(pairs), 3),
        "confusion": {f"{a} -> {b}": n for (a, b), n in sorted(Counter(pairs).items())},
        "false_contradictions": sum(1 for a in said_contradicted if a != "contradicted"),
        "missed_contradictions": sum(
            1 for a, b in pairs if a == "contradicted" and b != "contradicted"
        ),
        "cost_usd": round(cost, 4),
        "disagreements": disagreements,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--report", required=True, type=Path)
    b.add_argument("--ids", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--model", default="claude-haiku-5-5")
    m.add_argument("--effort", default="medium")
    m.add_argument("--prompt", default="1.1.0")
    args = parser.parse_args()
    if args.command == "build":
        existing = (
            {
                json.loads(line)["id"]
                for line in CALIBRATION.read_text().splitlines()
                if line.strip()
            }
            if CALIBRATION.exists()
            else set()
        )
        report = json.loads(args.report.read_text())
        added = asyncio.run(build(report, args.report.name, args.ids.split(","), existing))
        CALIBRATION.parent.mkdir(exist_ok=True)
        with CALIBRATION.open("a") as out:
            for row in added:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"added {len(added)} cases to {CALIBRATION}")
    else:
        rows = [json.loads(line) for line in CALIBRATION.read_text().splitlines() if line.strip()]
        result = asyncio.run(measure(rows, args.model, args.effort, args.prompt))
        print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
