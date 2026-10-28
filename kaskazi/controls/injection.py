"""A fast, deterministic screen for prompt injection, run before any model sees a message.

It looks for the common shapes of an attempt to take over the assistant: instructions to ignore
or replace its rules, requests for its hidden instructions, role-play framing, fake system or
tool turns, and payloads hidden in encodings. It is the first of several layers, not the only
one: the router model gives its own verdict, retrieved and uploaded text is always passed as
data, tools act only on the signed-in customer, and changes need the customer's confirmation.
The same screen reads the text of uploaded documents.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field

_PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    (
        "override",
        re.compile(
            r"(?i)\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|above|prior|all|your|the|system|earlier)\b.{0,30}"
            r"\b(instructions?|rules?|prompts?|guidelines|directions|polic(y|ies))\b"
        ),
        3,
    ),
    ("override_sw", re.compile(r"(?i)\b(puuza|sahau)\b.{0,40}\b(maagizo|maelekezo|sheria)\b"), 3),
    (
        "reveal_prompt",
        re.compile(
            r"(?i)\b(reveal|show|print|repeat|output|tell me|what (is|are)|give me)\b.{0,40}"
            r"\b(system prompt|hidden (instructions|prompt)|your (instructions|prompt|rules)|initial prompt|developer message)\b"
        ),
        3,
    ),
    (
        "new_role",
        re.compile(
            r"(?i)\b(you are now|from now on,? you|act as|pretend (to be|you are)|roleplay as|simulate being|developer mode|dan mode|jailbreak)\b"
        ),
        2,
    ),
    ("fake_turn", re.compile(r"(?im)^\s*(system|assistant|developer|tool)\s*:"), 2),
    (
        "fake_tags",
        re.compile(r"(?i)</?\s*(system|instructions?|admin|assistant|tool_result|passage)\s*>"),
        2,
    ),
    (
        "authority",
        re.compile(
            r"(?i)\b(i am|this is)\b.{0,20}\b(the )?(developer|administrator|admin|bank (manager|staff)|anthropic|openai|security team)\b.{0,40}\b(authori[sz]e|allow|permit|instruct|order)"
        ),
        2,
    ),
    (
        "exfiltrate",
        re.compile(
            r"(?i)\b(other|another|all|every)\b.{0,15}\b(customers?|users?|accounts?|people)('s)?\b.{0,30}\b(balance|details|data|numbers?|transactions?|records?)\b"
        ),
        2,
    ),
    (
        "tool_forcing",
        re.compile(
            r"(?i)\b(call|run|execute|use|invoke)\b.{0,20}\b(the )?(tool|function)\b.{0,40}\b(without|skip|no)\b.{0,20}\b(confirm|confirmation|asking|check)"
        ),
        3,
    ),
    (
        "instruction_to_model",
        re.compile(
            r"(?i)\b(note|message|instruction)s? (to|for) (the )?(ai|assistant|model|llm|chatbot|reviewer|system)\b"
        ),
        2,
    ),
]

_BASE64 = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])")


@dataclass(frozen=True)
class Screen:
    score: int
    matches: list[str] = field(default_factory=list)

    @property
    def risk(self) -> str:
        if self.score >= 3:
            return "high"
        if self.score >= 2:
            return "low"
        return "none"


def screen(text: str) -> Screen:
    matches: list[str] = []
    score = 0
    for name, pattern, weight in _PATTERNS:
        if pattern.search(text):
            matches.append(name)
            score += weight
    for candidate in _BASE64.findall(text):
        decoded = _decode(candidate)
        if decoded and any(p.search(decoded) for _, p, w in _PATTERNS if w >= 2):
            matches.append("encoded_instructions")
            score += 3
            break
    return Screen(score=score, matches=matches)


def _decode(candidate: str) -> str | None:
    try:
        raw = base64.b64decode(candidate + "=" * (-len(candidate) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text if sum(ch.isprintable() for ch in text) > 0.9 * len(text) else None
