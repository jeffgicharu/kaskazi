"""Document desk evaluation: how accurately fields are read, how far the confidence can be
trusted, and whether faults and planted instructions are caught.

The evaluation set is 30 generated documents of invented people (kaskazi.documents.samples):
10 payslips (2 as photos), 10 specimen IDs, 10 statements, 8 of them with faults and 4 with
planted instructions. Each is read the way the desk reads a visitor's document, then compared
with the truth it was generated from.

    uv run python -m evals.documents [--image-model claude-sonnet-5-5]

Spends real money: about $0.03 a run with Haiku.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from kaskazi.controls.pii import Vault
from kaskazi.documents import checks
from kaskazi.documents.extract import CONFIDENT, FIELDS, Extraction, amount, extract
from kaskazi.documents.samples import generate
from kaskazi.documents.text import read_pdf
from kaskazi.llm.contract import BudgetContext, GenerateRequest, GenerateResult
from kaskazi.llm.prices import cost_usd
from kaskazi.llm.providers.anthropic import AnthropicProvider
from kaskazi.llm.routing import load_routes

REPORTS = Path(__file__).parent / "reports"
TODAY = dt.date(2026, 10, 29)  # the samples' pay periods are measured against this day

# Which failed check (or flag) finds each planted fault.
FINDS = {
    "net_mismatch": {"net_adds_up"},
    "paye_mismatch": {"statutory_paye"},
    "future_period": {"period_not_future", "paid_not_future"},
    "running_balance_mismatch": {"running_balance", "balances_add_up"},
    "under_18": {"adult"},
    "planted_instruction": {"no_hidden_text", "no_instructions", "injection_screen"},
}


def same(fmt: str, got: str | None, want: str | None) -> bool:
    if not want:
        return not got
    if got is None:
        return False
    if fmt == "money":
        a, b = amount(got), amount(want)
        return a is not None and b is not None and abs(a - b) < Decimal("0.01")
    norm = lambda s: re.sub(r"\s+", " ", s).strip().casefold()  # noqa: E731
    return norm(got) == norm(want)


async def read(
    provider: AnthropicProvider, sample: dict[str, Any], folder: Path, image_model: str | None
) -> dict[str, Any]:
    routes, _ = load_routes()
    data = (folder / sample["file"]).read_bytes()
    pdf = read_pdf(data) if sample["file"].endswith(".pdf") else None

    async def generate_(request: GenerateRequest) -> GenerateResult:
        settings = routes[request.route].primary_settings()
        if image_model and request.route == "documents.extract_image":
            settings = type(settings)(
                image_model, settings.effort, settings.max_tokens, settings.timeout_s
            )
        started = time.perf_counter()
        reply = await provider.generate(request, settings)
        return GenerateResult(
            provider="anthropic",
            model=settings.model,
            stop=reply.stop,
            texts=reply.texts,
            tool_calls=[],
            structured=reply.structured,
            usage=reply.usage,
            cost_usd=cost_usd(settings.model, reply.usage),
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=[],
        )

    started = time.perf_counter()
    x: Extraction = await extract(
        generate_,
        sample["kind"],
        pdf_text=pdf.visible if pdf else None,
        image=None if pdf else data,
        vault=Vault(),
        known_names=[],
        budget=BudgetContext(),
        request_id=f"eval-{sample['key']}",
    )
    seconds = round(time.perf_counter() - started, 2)
    found = checks.CHECKS[sample["kind"]](x, TODAY) + checks.integrity(
        x, pdf.hidden if pdf else [], pdf.visible if pdf else None
    )
    checks.apply(x, found)
    failed = {c.name for c in found if c.ok is False}
    fields = []
    for name, fmt in FIELDS[sample["kind"]].items():
        f = x.fields.get(name)
        fields.append(
            {
                "field": name,
                "want": sample["truth"].get(name),
                "got": f.value if f else None,
                "right": same(fmt, f.value if f else None, sample["truth"].get(name)),
                "confidence": f.confidence if f else 0.0,
                "model_confidence": f.model_confidence if f else None,
            }
        )
    caught = {d: bool(FINDS[d] & failed) for d in sample["defects"]}
    return {
        "document": sample["key"],
        "kind": sample["kind"],
        "path": x.path,
        "seconds": seconds,
        "cost_usd": x.cost_usd,
        "defects": sample["defects"],
        "caught": caught,
        "failed_checks": sorted(failed),
        "fields": fields,
        "review": checks.review_reasons(x, found),
    }


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    fields = [f for r in results for f in r["fields"] if f["want"] not in (None, "")]

    def bucket(lo: float, hi: float) -> dict[str, Any]:
        inside = [f for f in fields if lo <= f["confidence"] < hi]
        return {
            "fields": len(inside),
            "right": round(sum(f["right"] for f in inside) / len(inside), 3) if inside else None,
        }

    planted = [r for r in results if r["defects"]]
    clean = [r for r in results if not r["defects"]]
    by_path = {}
    for path in ("text", "image"):
        on = [
            f
            for r in results
            if r["path"] == path
            for f in r["fields"]
            if f["want"] not in (None, "")
        ]
        by_path[path] = round(sum(f["right"] for f in on) / len(on), 3) if on else None
    return {
        "documents": len(results),
        "field_accuracy": round(sum(f["right"] for f in fields) / len(fields), 3),
        "field_accuracy_by_path": by_path,
        "calibration": {
            "confident (0.85 and over)": bucket(CONFIDENT, 1.01),
            "unsure (0.5 to 0.85)": bucket(0.5, CONFIDENT),
            "doubtful (under 0.5)": bucket(0.0, 0.5),
        },
        "faults_caught": f"{sum(all(r['caught'].values()) for r in planted)} of {len(planted)}",
        "clean_documents_flagged_by_a_check": f"{sum(bool(r['failed_checks']) for r in clean)} of {len(clean)}",
        "clean_documents_sent_to_review": f"{sum(bool(r['review']) for r in clean)} of {len(clean)}",
        "seconds_p50": sorted(r["seconds"] for r in results)[len(results) // 2],
        "cost_usd": round(sum(r["cost_usd"] for r in results), 4),
    }


async def run(image_model: str | None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        generate(folder)
        samples = json.loads((folder / "evaluation" / "index.json").read_text())
        provider = AnthropicProvider(
            os.environ["ANTHROPIC_KEY"], os.environ["ANTHROPIC_WORKSPACE_ID"]
        )
        gate = asyncio.Semaphore(4)

        async def one(s: dict[str, Any]) -> dict[str, Any]:
            async with gate:
                r = await read(provider, s, folder / "evaluation", image_model)
                print(
                    f"{s['key']:<20} {r['path']:<5} {sum(f['right'] for f in r['fields'])}/{len(r['fields'])} "
                    f"failed={r['failed_checks']} caught={r['caught']}",
                    flush=True,
                )
                return r

        try:
            return {"results": list(await asyncio.gather(*(one(s) for s in samples)))}
        finally:
            await provider.aclose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-model", default=None, help="another model for the image path")
    args = parser.parse_args()
    results = asyncio.run(run(args.image_model))["results"]
    routes, _ = load_routes()
    from kaskazi.release.evalgate import document_gate, document_metrics
    from kaskazi.release.fingerprint import fingerprint

    gated = document_metrics(results)
    report = {
        "run_on": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        # A run with another image model measures a variant and covers no release.
        "fingerprint": fingerprint() if not args.image_model else {"total": "variant", "parts": {}},
        "gate": {"metrics": gated, "reasons": document_gate(gated)},
        "models": {
            "text": routes["documents.extract_text"].primary,
            "image": args.image_model or routes["documents.extract_image"].primary,
        },
        "metrics": summarise(results),
        "results": results,
    }
    print(json.dumps(report["metrics"], indent=1))
    REPORTS.mkdir(exist_ok=True)
    total = str(report["fingerprint"]["total"])  # type: ignore[index]
    suffix = f"-variant-image-{args.image_model}" if args.image_model else f"-{total[:12]}"
    out = REPORTS / f"documents-{dt.date.today().isoformat()}{suffix}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
