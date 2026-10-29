"""Ten golden questions, asked live at every activation.

The full golden set is run before a release, with a judge, and its report gates the release in
CI (kaskazi.release.evalgate). On the server, against the idle slot and the real models, the
release check asks ten of its questions again and checks in code what can be checked without a
judge: the outcome, that a documents answer cites a passage, that a change waits for the
customer, that a refusal or a handover happens. It costs about one US cent.

Each question gets its own check copy, so none of them sees another's conversation.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx

GOLDEN = Path(__file__).resolve().parents[2] / "evals" / "golden" / "questions.jsonl"

# The check copy is always the teacher, so these are all her questions: documents (fees, cards,
# loans, payments), one the documents can't answer, the bank's records, a change, and the three
# things the assistant must refuse or pass on.
LIVE = (
    "pay-001",
    "crd-001",
    "lon-002",
    "acc-002",
    "acc-028",
    "tool-01",
    "tool-08",
    "behave-01",
    "behave-03",
    "behave-08",
)


def live_cases() -> list[dict[str, Any]]:
    cases = {
        c["id"]: c
        for c in (json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip())
    }
    return [cases[i] for i in LIVE]


def problem(case: dict[str, Any], turn: dict[str, Any]) -> str | None:
    """What is wrong with a turn's answer to a case, or None."""
    outcome, reply = turn["outcome"], turn["reply"]
    if case["kind"] == "behaviour":
        want = case.get("expect_outcome")
        return None if want is None or outcome == want else f"{outcome}, not {want}"
    if case["kind"] == "unanswerable":
        return None if outcome in ("answered", "safe_answer") else f"outcome {outcome}"
    if outcome != "answered":
        return f"outcome {outcome}"
    if case["kind"] == "answerable" and not reply["sources"]:
        return "the answer cites nothing"
    if case.get("expect_card") and not reply["cards"]:
        return "no confirmation card"
    return None


async def ask(api: httpx.AsyncClient, release: str, case: dict[str, Any]) -> dict[str, Any]:
    from kaskazi.api.check import check_token
    from kaskazi.release.check import SessionCookies

    cookies = SessionCookies()
    # A client of its own, connections and all: closing one that shared the check's transport
    # would close the pool under the other questions still being asked.
    async with httpx.AsyncClient(
        base_url=api.base_url,
        timeout=60,
        event_hooks={"request": [cookies.on_request], "response": [cookies.on_response]},
    ) as client:
        token = check_token(bytes.fromhex(os.environ["KASKAZI_SECRET"]), release)
        r = await client.post("/api/check/session", headers={"X-Kaskazi-Check": token})
        r.raise_for_status()
        conversation = r.json()["conversation_id"]
        r = await client.post(
            "/api/chat",
            json={"conversation_id": conversation, "text": case["question"]},
            headers={"X-Kaskazi": "1"},
        )
        r.raise_for_status()
        turn: dict[str, Any] = r.json()
        return turn


async def ten_golden_questions(
    api: httpx.AsyncClient, gateway: httpx.AsyncClient, release: str
) -> str:
    from kaskazi.release.journeys import JourneyFailed

    cases = live_cases()
    gate = asyncio.Semaphore(5)

    async def one(case: dict[str, Any]) -> tuple[str, str | None, int]:
        async with gate:
            turn = await ask(api, release, case)
            return case["id"], problem(case, turn), turn["latency_ms"]

    outcomes = await asyncio.gather(*(one(c) for c in cases))
    wrong = [f"{i}: {why}" for i, why, _ in outcomes if why]
    if wrong:
        raise JourneyFailed("golden questions answered wrongly: " + "; ".join(wrong))
    slowest = max(ms for _, _, ms in outcomes)
    return (
        f"{len(cases)} golden questions answered as the golden set expects (slowest {slowest} ms)"
    )
