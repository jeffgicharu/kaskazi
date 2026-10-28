"""Router evaluation: how well, how fast and at what cost each set-up labels a message.

Every case in router_cases.jsonl is a message (with a little conversation before it, for
follow-ups) and the labels a careful person would give it. Each set-up sees each case once, in
an order shuffled per case so that neither set-up always goes first. The request is the one the
assistant sends (`router_request`), masked the same way.

    uv run python -m evals.router [--variants haiku-adaptive,haiku-off,gpt-mini] [--limit N]

Needs ANTHROPIC_KEY, ANTHROPIC_WORKSPACE_ID and OPENAI_API_KEY. Spends real money: about
$0.0003 a case for Haiku, $0.0012 for gpt-5.4-mini. Writes evals/reports/router-<date>.json and
prints a table.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import random
import statistics
import time
from pathlib import Path
from typing import Any

from kaskazi.assistant.turn import router_request
from kaskazi.controls.pii import Vault, mask
from kaskazi.llm.contract import BudgetContext
from kaskazi.llm.prices import cost_usd
from kaskazi.llm.providers.anthropic import AnthropicProvider
from kaskazi.llm.providers.base import CallSettings, Provider, ProviderFailure
from kaskazi.llm.providers.openai import OpenAIProvider

HERE = Path(__file__).parent
CASES = HERE / "router_cases.jsonl"
REPORTS = HERE / "reports"

VARIANTS: dict[str, tuple[str, CallSettings]] = {
    "haiku-adaptive": ("anthropic", CallSettings("claude-haiku-5-5", "low", 1024, 20)),
    "haiku-off": ("anthropic", CallSettings("claude-haiku-5-5", "low", 1024, 20, thinking=False)),
    "gpt-mini": ("openai", CallSettings("gpt-5.4-mini", "low", 1024, 20)),
}


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]


def score(case: dict[str, Any], label: dict[str, Any]) -> dict[str, bool]:
    marks: dict[str, bool] = {}
    if case["intent"] is not None:
        marks["intent"] = label["intent"] == case["intent"]
    marks["sensitive"] = label["sensitive_situation"] == case["sensitive"]
    if case["injection"] == "high":
        marks["injection_caught"] = label["injection_risk"] == "high"
    else:
        marks["no_false_injection"] = label["injection_risk"] != "high"
    if "search_mentions" in case:
        query = label["search_query"].lower()
        marks["follow_up_spelled_out"] = all(w in query for w in case["search_mentions"])
    return marks


async def run_one(
    provider: Provider, settings: CallSettings, case: dict[str, Any]
) -> dict[str, Any]:
    masked = mask(case["message"], Vault())
    request = router_request(
        case.get("context", []), masked.text, BudgetContext(), f"eval-{case['id']}"
    )
    started = time.perf_counter()
    try:
        reply = await provider.generate(request, settings)
    except ProviderFailure as failure:
        return {"error": str(failure), "ms": round((time.perf_counter() - started) * 1000)}
    ms = round((time.perf_counter() - started) * 1000)
    label = reply.structured or {}
    return {
        "ms": ms,
        "label": label,
        "marks": score(case, label) if label else {},
        "output_tokens": reply.usage.output_tokens,
        "cost_usd": cost_usd(settings.model, reply.usage),
    }


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in results if "error" not in r]
    marks: dict[str, list[bool]] = {}
    for r in ok:
        for name, passed in r["marks"].items():
            marks.setdefault(name, []).append(passed)
    times = sorted(r["ms"] for r in ok)
    return {
        "calls": len(results),
        "errors": len(results) - len(ok),
        "accuracy": {name: round(sum(v) / len(v), 3) for name, v in sorted(marks.items())},
        "misses": {
            name: [r["id"] for r in ok if r["marks"].get(name) is False] for name in sorted(marks)
        },
        "ms_p50": statistics.median(times) if times else None,
        "ms_p90": times[int(0.9 * (len(times) - 1))] if times else None,
        "ms_mean": round(statistics.fmean(times)) if times else None,
        "output_tokens_mean": round(statistics.fmean(r["output_tokens"] for r in ok))
        if ok
        else None,
        "cost_usd": round(sum(r["cost_usd"] for r in ok), 6),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    chosen = args.variants.split(",")

    providers: dict[str, Provider] = {
        "anthropic": AnthropicProvider(
            os.environ["ANTHROPIC_KEY"], os.environ["ANTHROPIC_WORKSPACE_ID"]
        ),
        "openai": OpenAIProvider(os.environ["OPENAI_API_KEY"]),
    }
    cases = load_cases()[: args.limit or None]
    rng = random.Random(29)
    results: dict[str, list[dict[str, Any]]] = {v: [] for v in chosen}

    # One call each first, so the cached system prompt is written before anything is timed.
    for v in chosen:
        provider_name, settings = VARIANTS[v]
        await run_one(providers[provider_name], settings, cases[0])

    for case in cases:
        order = chosen[:]
        rng.shuffle(order)
        for v in order:
            provider_name, settings = VARIANTS[v]
            outcome = await run_one(providers[provider_name], settings, case)
            results[v].append({"id": case["id"], **outcome})
        print(case["id"], " ".join(f"{v}={results[v][-1].get('ms')}" for v in chosen), flush=True)

    for p in providers.values():
        await p.aclose()

    report = {
        "run_on": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "cases": len(cases),
        "variants": {
            v: {
                "model": VARIANTS[v][1].model,
                "effort": VARIANTS[v][1].effort,
                "thinking": VARIANTS[v][1].thinking,
                **summarise(results[v]),
            }
            for v in chosen
        },
    }
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"router-{dt.date.today().isoformat()}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    (runs / f"router-{int(time.time())}.json").write_text(json.dumps(results, indent=2))
    for v, s in report["variants"].items():
        print(
            f"\n{v}: p50 {s['ms_p50']} ms, p90 {s['ms_p90']} ms, cost ${s['cost_usd']}, "
            f"errors {s['errors']}, out tokens {s['output_tokens_mean']}"
        )
        for name, acc in s["accuracy"].items():
            print(f"  {name}: {acc}  misses {s['misses'][name]}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
