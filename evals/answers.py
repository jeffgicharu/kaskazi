"""The golden-set evaluation: the whole assistant, end to end, on 135 questions.

Every case in evals/golden/questions.jsonl is asked through the API of a running stack (the
local one: `KASKAZI_DEV_REAL=1 scripts/dev.sh`), as one of the sample customers, in a fresh
conversation, after any earlier questions a follow-up needs. Each answer is then graded:

- in code: the outcome (answered, refused, handed over), whether it cited the document the
  question was written from, whether a change waited for confirmation, whether a warning showed;
- by a judge (Claude Haiku with the evals.judge prompt): each criterion written before the
  answer existed is met, missed or contradicted.

    uv run python -m evals.answers [--only acc-001,tool-02] [--routes alternative.toml --label x]

With no --routes the report covers the committed settings: it is written as
evals/reports/answers-<date>-<fingerprint>.json, compared with the newest report before it and
gated (kaskazi.release.evalgate). Commit it with the release. With --routes (the gateway must
have been started with KASKAZI_GATEWAY_ROUTES pointing at the same file) it measures a variant;
its fingerprint covers that file, so it matches a release only if the release adopts it.

Needs ANTHROPIC_KEY and ANTHROPIC_WORKSPACE_ID for the judge, and the stack's database for each
turn's cost. Spends real money: about $0.25 a run.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import httpx

from kaskazi.llm.contract import BudgetContext, GenerateRequest, Message, Purpose, TextPart
from kaskazi.llm.prices import cost_usd
from kaskazi.llm.providers.anthropic import AnthropicProvider
from kaskazi.llm.providers.base import CallSettings
from kaskazi.llm.routing import load_routes
from kaskazi.prompts.registry import Prompt, prompt
from kaskazi.release.evalgate import REPORTS, gate, latest_passing, summarise
from kaskazi.release.fingerprint import fingerprint

HERE = Path(__file__).parent
CASES = HERE / "golden" / "questions.jsonl"
WRITE = {"X-Kaskazi": "1"}

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["met", "missed", "contradicted"]},
                    "why": {"type": "string"},
                },
                "required": ["n", "verdict", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["criteria"],
    "additionalProperties": False,
}


def load_cases(only: set[str] | None = None) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    return [c for c in cases if only is None or c["id"] in only]


async def ask(api: str, case: dict[str, Any]) -> dict[str, Any]:
    """Asks a case's question as its sample customer; returns the turn as the client saw it."""
    async with httpx.AsyncClient(base_url=api, timeout=90) as client:
        r = await client.post("/api/session", json={"persona": case["persona"]}, headers=WRITE)
        r.raise_for_status()
        conversation = r.json()["conversation_id"]
        for earlier in case.get("before", []):
            r = await client.post(
                "/api/chat", json={"conversation_id": conversation, "text": earlier}, headers=WRITE
            )
            r.raise_for_status()
        started = time.perf_counter()
        r = await client.post(
            "/api/chat",
            json={"conversation_id": conversation, "text": case["question"]},
            headers=WRITE,
        )
        r.raise_for_status()
        turn: dict[str, Any] = r.json()
        turn["wall_ms"] = round((time.perf_counter() - started) * 1000)
        turn["conversation_id"] = conversation
        return turn


async def timed_out(db: asyncpg.Pool, conversation: str) -> bool:
    """Whether a provider timed out during this conversation's turns."""
    found = await db.fetchval(
        "SELECT 1 FROM gateway.calls c JOIN app.conversations v ON v.sandbox_id = c.sandbox_id "
        "WHERE v.id = $1 AND c.error_kind = 'timeout' LIMIT 1",
        uuid.UUID(conversation),
    )
    return found is not None


async def tool_results(db: asyncpg.Pool, conversation: str) -> list[str]:
    """What the bank's systems returned to the assistant in the case's last turn, as it saw it."""
    rows = await db.fetch(
        "SELECT model_message FROM app.messages WHERE conversation_id = $1 AND author = 'tool' "
        "AND seq > coalesce((SELECT max(seq) FROM app.messages WHERE conversation_id = $1 "
        "AND author = 'customer'), 0) ORDER BY seq",
        uuid.UUID(conversation),
    )
    found = []
    for row in rows:
        message = row["model_message"]
        message = json.loads(message) if isinstance(message, str) else message
        found += [p["content"] for p in message["parts"] if p.get("type") == "tool_result"]
    return found


async def judge(
    provider: AnthropicProvider,
    case: dict[str, Any],
    answer: str,
    sources: list[dict[str, Any]],
    records: list[str] | None = None,
    settings: CallSettings | None = None,
    judge_prompt: Prompt | None = None,
) -> tuple[list[dict[str, Any]], float]:
    judge_prompt = judge_prompt or prompt("evals.judge")
    settings = settings or load_routes()[0]["evals.judge"].primary_settings()
    asked = "".join(f"Earlier, the customer asked: {q}\n" for q in case.get("before", []))
    criteria = "\n".join(f"{n}. {text}" for n, text in enumerate(case["expected_facts"], 1))
    cited = "".join(
        f'<passage document="{s["document"]}" section="{s["section"]}">\n{s["quote"]}\n</passage>\n'
        for s in sources
    )
    passages = (
        f"The passages from the bank's documents that the answer cited:\n{cited}\n"
        if cited
        else "The answer cited no passages from the bank's documents.\n\n"
    )
    if records:
        passages += (
            "What the bank's systems returned to the assistant (the customer's own records):\n"
            + "".join(f"<record>\n{r}\n</record>\n" for r in records)
            + "\n"
        )
    request = GenerateRequest(
        purpose=Purpose.JUDGE,
        route="evals.judge",
        prompt_ref=judge_prompt.ref,
        system=[judge_prompt.body],
        messages=[
            Message(
                role="user",
                parts=[
                    TextPart(
                        text=f"{asked}The customer asked: {case['question']}\n\n"
                        f"The assistant answered:\n<answer>\n{answer}\n</answer>\n\n"
                        f"{passages}Criteria:\n{criteria}"
                    )
                ],
            )
        ],
        output_schema=JUDGE_SCHEMA,
        budget=BudgetContext(),
        request_id=f"judge-{case['id']}",
    )
    reply = await provider.generate(request, settings)
    verdicts = {v["n"]: v for v in (reply.structured or {}).get("criteria", [])}
    graded = [
        {
            "text": text,
            "verdict": verdicts.get(n, {}).get("verdict", "missed"),
            "why": verdicts.get(n, {}).get("why", "The judge gave no verdict."),
        }
        for n, text in enumerate(case["expected_facts"], 1)
    ]
    return graded, cost_usd(settings.model, reply.usage)


async def run_case(
    api: str, db: asyncpg.Pool, provider: AnthropicProvider, case: dict[str, Any]
) -> dict[str, Any]:
    base = {k: case.get(k) for k in ("id", "kind", "persona", "question", "source_doc")}
    base["expect_card"] = bool(case.get("expect_card"))
    asked_again = False
    try:
        turn = await ask(api, case)
        if turn["outcome"] == "unavailable" or await timed_out(db, turn["conversation_id"]):
            # A provider timed out while answering: at this runner's end that is usually the
            # network, and it says nothing about the answers. Ask once more, and count it.
            # (Provider reliability is watched on the console's monitoring view.)
            await asyncio.sleep(20)
            turn = await ask(api, case)
            asked_again = True
    except httpx.HTTPError as error:
        return {**base, "error": f"{type(error).__name__}: {error}"}
    if turn["outcome"] == "unavailable":
        return {**base, "error": "no model provider could be reached, twice"}
    reply = turn["reply"]
    sources = [
        {"document": s["document_id"], "section": s["section"], "quote": s["quote"]}
        for s in reply.get("sources", [])
    ]
    cited = sorted({s["document_id"] for s in reply.get("sources", [])})
    spent = await db.fetchrow(
        "SELECT coalesce(sum(cost_usd), 0) AS cost, bool_or(fallback) AS fallback "
        "FROM app.turns WHERE conversation_id = $1",
        uuid.UUID(turn["conversation_id"]),
    )
    records = await tool_results(db, turn["conversation_id"])
    checks: dict[str, Any] = {}
    if case["kind"] == "behaviour":
        outcome_ok = case.get("expect_outcome") is None or turn["outcome"] == case["expect_outcome"]
        notice_ok = not case.get("expect_notice") or bool(reply.get("notice"))
        checks["behaviour_ok"] = outcome_ok and notice_ok
    if case.get("expect_card"):
        checks["card_offered"] = bool(reply.get("cards"))
    criteria: list[dict[str, Any]] = []
    judge_cost = 0.0
    if case.get("expected_facts"):
        criteria, judge_cost = await judge(provider, case, reply.get("text", ""), sources, records)
    return {
        **base,
        "outcome": turn["outcome"],
        "asked_again": asked_again,
        "conversation_id": turn["conversation_id"],
        "answer": reply.get("text", ""),
        "sources": sources,
        "records": records,
        "cited_documents": cited,
        "notice": reply.get("notice"),
        "checks": checks,
        "criteria": criteria,
        "latency_ms": turn["latency_ms"],
        "wall_ms": turn["wall_ms"],
        "cost_usd": round(float(spent["cost"]), 6),
        "fallback": bool(spent["fallback"]),
        "judge_cost_usd": round(judge_cost, 6),
    }


async def evaluate(
    cases: list[dict[str, Any]], api: str, dsn: str, concurrency: int
) -> list[dict[str, Any]]:
    provider = AnthropicProvider(os.environ["ANTHROPIC_KEY"], os.environ["ANTHROPIC_WORKSPACE_ID"])
    db = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    gate_ = asyncio.Semaphore(concurrency)

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        async with gate_:
            result = await run_case(api, db, provider, case)
            marks = [c["verdict"][0] for c in result.get("criteria", [])]
            print(
                f"{case['id']:<12} {result.get('outcome', 'ERROR'):<18} "
                f"{''.join(marks):<6} {result.get('latency_ms', '')} ms",
                flush=True,
            )
            return result

    try:
        return list(await asyncio.gather(*(one(c) for c in cases)))
    finally:
        await provider.aclose()
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8800")
    parser.add_argument(
        "--db", default="postgresql://postgres:local-dev-only@127.0.0.1:55432/kaskazi_dev"
    )
    parser.add_argument("--only", default="")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--routes", help="the routes file the gateway was started with")
    parser.add_argument("--label", default="", help="a name for a variant run")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None
    if args.routes and not args.label:
        parser.error("a variant run (--routes) needs a --label")

    routes_text = Path(args.routes).read_text() if args.routes else None
    overrides = {"kaskazi/llm/routes.toml": routes_text.encode()} if routes_text else {}
    covered = fingerprint(overrides=overrides)
    cases = load_cases(only)
    started = time.perf_counter()
    results = asyncio.run(evaluate(cases, args.api, args.db, args.concurrency))

    metrics = summarise(results)
    full_run = only is None
    judge_settings = load_routes()[0]["evals.judge"]
    judge_used = {
        "model": judge_settings.primary,
        "effort": judge_settings.effort,
        "prompt": prompt("evals.judge").ref,
    }
    passing = latest_passing(judge=judge_used) if full_run else None
    baseline = passing.name if passing else None
    baseline_metrics = (
        summarise(json.loads((REPORTS / baseline).read_text())["results"]) if baseline else None
    )
    reasons = gate(metrics, baseline_metrics)
    report = {
        "kind": "answers",
        "run_on": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "label": args.label or None,
        "fingerprint": covered,
        "judge": judge_used,
        "asked_again": sum(1 for r in results if r.get("asked_again")),
        "routes_override": routes_text,
        "minutes": round((time.perf_counter() - started) / 60, 1),
        "judge_cost_usd": round(sum(r.get("judge_cost_usd", 0) for r in results), 4),
        "baseline": baseline,
        "metrics": metrics,
        "gate": {"passed": not reasons, "reasons": reasons},
        "results": results,
    }
    print(json.dumps({"metrics": metrics, "gate": report["gate"]}, indent=2))
    if not full_run:
        print("(a partial run: not written as a report)")
        return
    REPORTS.mkdir(exist_ok=True)
    stamp = dt.date.today().isoformat()
    name = (
        f"answers-{stamp}-variant-{args.label}.json"
        if args.label
        else f"answers-{stamp}-{str(covered['total'])[:12]}.json"
    )
    (REPORTS / name).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote evals/reports/{name}")


if __name__ == "__main__":
    main()
