"""Consistency: does the assistant give the same answer to the same question, and to the same
question asked differently?

- Repeats: 20 golden cases, each asked three times in fresh conversations.
- Rephrasings: 15 golden questions, each asked as written and in two other wordings
  (evals/golden/paraphrases.jsonl).

For each group of three it compares the outcome (answered, refused, handed over), the tools the
assistant called, and the judge's verdict on the case's key fact. The judge is the golden set's
(evals.answers), so the numbers sit beside the golden set's.

    uv run python -m evals.consistency

Needs the local stack with the real models, like evals.answers. About $0.50 a run.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import asyncpg

from evals.answers import CASES, ask, judge, timed_out, tool_results
from kaskazi.llm.providers.anthropic import AnthropicProvider
from kaskazi.release.fingerprint import fingerprint

HERE = Path(__file__).parent
REPORTS = HERE / "reports"
PARAPHRASES = HERE / "golden" / "paraphrases.jsonl"
DB = "postgresql://postgres:local-dev-only@127.0.0.1:55432/kaskazi_dev"
API = "http://127.0.0.1:8800"
REPEATED = [
    "acc-001",
    "acc-004",
    "acc-012",
    "crd-001",
    "crd-007",
    "lon-002",
    "lon-005",
    "pay-001",
    "pay-010",
    "pay-028",
    "tool-01",
    "tool-05",
    "tool-07",
    "tool-08",
    "behave-01",
    "behave-03",
    "behave-07",
    "behave-08",
    "acc-028",
    "crd-022",
]


async def tools_used(db: asyncpg.Pool, conversation: str) -> list[str]:
    rows = await db.fetch(
        "SELECT model_message FROM app.messages WHERE conversation_id = $1 AND author = 'tool' "
        "AND seq > coalesce((SELECT max(seq) FROM app.messages WHERE conversation_id = $1 "
        "AND author = 'customer'), 0)",
        uuid.UUID(conversation),
    )
    names = set()
    for row in rows:
        message = (
            json.loads(row["model_message"])
            if isinstance(row["model_message"], str)
            else row["model_message"]
        )
        names |= {p["name"] for p in message["parts"] if p.get("type") == "tool_call"}
    return sorted(names)


async def one(
    db: asyncpg.Pool, provider: AnthropicProvider, case: dict[str, Any], question: str
) -> dict[str, Any]:
    asked = {**case, "question": question}
    turn = await ask(API, asked)
    if turn["outcome"] == "unavailable" or await timed_out(db, turn["conversation_id"]):
        await asyncio.sleep(70)  # a network blip at the runner, as in evals.answers
        turn = await ask(API, asked)
    reply = turn["reply"]
    verdict, judge_cost = None, 0.0
    if case.get("expected_facts"):
        sources = [
            {"document": s["document_id"], "section": s["section"], "quote": s["quote"]}
            for s in reply.get("sources", [])
        ]
        records = await tool_results(db, turn["conversation_id"])
        graded, judge_cost = await judge(
            provider,
            {**case, "expected_facts": case["expected_facts"][:1]},
            reply.get("text", ""),
            sources,
            records,
        )
        verdict = graded[0]["verdict"]
    spent = await db.fetchval(
        "SELECT coalesce(sum(cost_usd), 0) FROM app.turns WHERE conversation_id = $1",
        uuid.UUID(turn["conversation_id"]),
    )
    return {
        "question": question,
        "outcome": turn["outcome"],
        "tools": await tools_used(db, turn["conversation_id"]),
        "key_fact": verdict,
        "answer": reply.get("text", "")[:400],
        "latency_ms": turn["latency_ms"],
        "cost_usd": round(float(spent), 6),
        "judge_cost_usd": round(judge_cost, 6),
    }


def agreement(groups: list[dict[str, Any]]) -> dict[str, Any]:
    def same(key: str, of: list[dict[str, Any]]) -> float | None:
        usable = [g for g in of if all(a[key] is not None for a in g["asks"])]
        if not usable:
            return None
        return round(
            sum(len({json.dumps(a[key]) for a in g["asks"]}) == 1 for g in usable) / len(usable), 3
        )

    with_fact = [g for g in groups if all(a["key_fact"] for a in g["asks"])]
    return {
        "groups": len(groups),
        "same_outcome": same("outcome", groups),
        "same_tools": same("tools", groups),
        "same_key_fact_verdict": same("key_fact", groups),
        "key_fact_met_every_time": round(
            sum(all(a["key_fact"] == "met" for a in g["asks"]) for g in with_fact) / len(with_fact),
            3,
        )
        if with_fact
        else None,
        "differing": [
            g["id"]
            for g in groups
            if len({(a["outcome"], json.dumps(a["tools"]), a["key_fact"]) for a in g["asks"]}) > 1
        ],
    }


async def run() -> dict[str, Any]:
    cases = {
        c["id"]: c
        for c in (json.loads(line) for line in CASES.read_text().splitlines() if line.strip())
    }
    paraphrases = [
        json.loads(line) for line in PARAPHRASES.read_text().splitlines() if line.strip()
    ]
    provider = AnthropicProvider(os.environ["ANTHROPIC_KEY"], os.environ["ANTHROPIC_WORKSPACE_ID"])
    db = await asyncpg.create_pool(DB, min_size=1, max_size=2)
    gate = asyncio.Semaphore(4)

    async def group(case_id: str, questions: list[str]) -> dict[str, Any]:
        async def asked(q: str) -> dict[str, Any]:
            async with gate:
                return await one(db, provider, cases[case_id], q)

        asks = list(await asyncio.gather(*(asked(q) for q in questions)))
        print(case_id, [(a["outcome"], a["key_fact"]) for a in asks], flush=True)
        return {"id": case_id, "asks": asks}

    try:
        repeats = list(
            await asyncio.gather(*(group(i, [cases[i]["question"]] * 3) for i in REPEATED))
        )
        reworded = list(
            await asyncio.gather(
                *(
                    group(p["id"], [cases[p["id"]]["question"], *p["phrasings"]])
                    for p in paraphrases
                )
            )
        )
    finally:
        await provider.aclose()
        await db.close()
    return {"repeats": repeats, "rephrasings": reworded}


def main() -> None:
    started = time.perf_counter()
    covered = fingerprint()
    results = asyncio.run(run())
    report = {
        "run_on": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "fingerprint": covered,
        "minutes": round((time.perf_counter() - started) / 60, 1),
        "metrics": {
            "repeats": agreement(results["repeats"]),
            "rephrasings": agreement(results["rephrasings"]),
        },
        "results": results,
    }
    asks = [a for part in results.values() for g in part for a in g["asks"]]
    report["cost_usd"] = round(sum(a["cost_usd"] + a["judge_cost_usd"] for a in asks), 4)
    print(json.dumps(report["metrics"], indent=1), f"\ncost ${report['cost_usd']}")
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"consistency-{dt.date.today().isoformat()}-{str(covered['total'])[:12]}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
