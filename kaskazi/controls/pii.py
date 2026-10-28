"""Finding personal data and replacing it with placeholders before any model sees it.

`mask` turns "send 2,000 to 0712 345 678" into "send 2,000 to [PHONE_1]" and records the
original in the conversation's vault. The same value always gets the same placeholder within a
conversation, so the model can still reason about it ("the number you gave earlier"), and tools
receive placeholders that the server resolves. `restore` puts values back for the customer in a
partly hidden form, and `find_unmasked` is the gateway's last check before anything leaves.

What it finds: Kenyan phone numbers, email addresses, card numbers (Luhn-valid), Kaskazi account
numbers (13 digits with a check digit), KRA PINs, national ID and passport numbers when labelled
as such, dates of birth when labelled, PINs and one-time codes typed by the customer, and names
the bank already knows (the customer's own and their saved recipients'). Names typed freely
("send it to John") are not found: there is no name-recognition model on the server.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class Kind(StrEnum):
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    CARD = "CARD"
    ACCOUNT = "ACCOUNT"
    KRA_PIN = "KRA_PIN"
    ID = "ID"
    PASSPORT = "PASSPORT"
    DOB = "DOB"
    SECRET = "SECRET"  # noqa: S105 - a PIN, one-time code or password the customer typed
    NAME = "NAME"


# The kinds the gateway refuses to send. Names are only known through the vault, and a secret
# is always caught with its label, so neither can be checked without context.
TRIPWIRE_KINDS = frozenset(
    {Kind.PHONE, Kind.EMAIL, Kind.CARD, Kind.ACCOUNT, Kind.KRA_PIN, Kind.ID, Kind.PASSPORT}
)


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: Kind
    value: str


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# The domain must end in a letter TLD, so a version reference such as "assistant.router@1.0.0" is no email.
_EMAIL = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])"
)
_PHONE = re.compile(r"(?<![\d+])(?:\+?254[\s-]?|0)(?:7|1)\d{2}[\s-]?\d{3}[\s-]?\d{3}(?!\d)")
# Digit runs count only when they stand alone: not inside a hex id, a hash or a UUID.
_DIGIT_RUN = re.compile(r"(?<![\w-])\d(?:[ -]?\d){11,18}(?![\w-])")
_KRA = re.compile(r"(?<![A-Za-z0-9])[APap]\d{9}[A-Za-z](?![A-Za-z0-9])")
_ID_LABELLED = re.compile(
    r"(?i)\b(?:national\s+id|id\s*(?:no\.?|number|num|#)?|i\.d\.?|identity(?:\s+card)?(?:\s+number)?|kitambulisho|namba\s+ya\s+id)"
    r"\s*(?:is|ni|:|-|=)?\s*(\d{6,9})(?!\d)"
)
_PASSPORT = re.compile(
    r"(?i)\bpassport(?:\s+(?:no\.?|number))?\s*(?:is|:|-)?\s*([A-Z]{1,2}\d{6,8})\b"
)
_DOB = re.compile(
    r"(?i)\b(?:date\s+of\s+birth|dob|born(?:\s+on)?|nilizaliwa)\s*(?:is|:|-|on)?\s*"
    r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"
)
# A PIN or code is a run of digits after the word. A password needs "is" or ":" and must hold a
# digit or symbol, so "PIN payments" or "password-protected" are not mistaken for secrets.
_SECRET_CODE = re.compile(
    r"(?i)\b(?:pin|otp|one[- ]time\s+code|code|passcode)\b"
    r"(?:\s+(?:is|ni|number|no\.?))?\s*[:=-]?\s*(\d{4,8})(?!\d)"
)
_SECRET_PASSWORD = re.compile(
    r"(?i)\b(?:password|nenosiri)\s*(?:is|ni|:|=)\s*((?=\S*[\d!@#$%^&*])\S{6,32})"
)
_UNLABELLED_ID = re.compile(r"(?<![\w,.-])\d{8}(?![\w,.-])")
_MONEY_CONTEXT = re.compile(
    r"(?i)(?:kes|ksh|kshs|sh|shs|shillings?|bob|borrow|loan|pay|paid|send|amount|balance|salary|"
    r"earn|deposit|withdraw|limit|transfer|fee|pesa)\W{0,3}(?:\w+\W+){0,2}$"
)


def find(text: str, known_names: Iterable[str] = ()) -> list[Span]:
    """Every piece of personal data in `text`, without overlaps, in order."""

    spans: list[Span] = []

    def add(start: int, end: int, kind: Kind) -> None:
        spans.append(Span(start, end, kind, text[start:end]))

    for m in _EMAIL.finditer(text):
        add(m.start(), m.end(), Kind.EMAIL)
    for m in _PHONE.finditer(text):
        add(m.start(), m.end(), Kind.PHONE)
    for m in _DIGIT_RUN.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if len(digits) == 13 and digits.startswith("0") and luhn_ok(digits):
            add(m.start(), m.end(), Kind.ACCOUNT)
        elif 13 <= len(digits) <= 19 and digits[0] != "0" and luhn_ok(digits):
            add(m.start(), m.end(), Kind.CARD)
    for m in _KRA.finditer(text):
        add(m.start(), m.end(), Kind.KRA_PIN)
    for m in _ID_LABELLED.finditer(text):
        add(m.start(1), m.end(1), Kind.ID)
    for m in _PASSPORT.finditer(text):
        add(m.start(1), m.end(1), Kind.PASSPORT)
    for m in _DOB.finditer(text):
        add(m.start(1), m.end(1), Kind.DOB)
    for pattern in (_SECRET_CODE, _SECRET_PASSWORD):
        for m in pattern.finditer(text):
            add(m.start(1), m.end(1), Kind.SECRET)
    for m in _UNLABELLED_ID.finditer(text):
        # An unlabelled 8-digit number is treated as an ID number unless the words before it
        # say it's money ("borrow 15000000", "KES 10000000").
        if not _MONEY_CONTEXT.search(text[max(0, m.start() - 40) : m.start()]):
            add(m.start(), m.end(), Kind.ID)
    for name in sorted(
        {n.strip() for n in known_names if len(n.strip()) >= 3}, key=len, reverse=True
    ):
        for m in re.finditer(rf"(?i)(?<!\w){re.escape(name)}(?!\w)", text):
            add(m.start(), m.end(), Kind.NAME)

    return _without_overlaps(spans)


def _without_overlaps(spans: list[Span]) -> list[Span]:
    # Earlier start wins; at the same start the longer span wins.
    spans.sort(key=lambda s: (s.start, -(s.end - s.start)))
    kept: list[Span] = []
    for s in spans:
        if kept and s.start < kept[-1].end:
            continue
        kept.append(s)
    return kept


def normalized(kind: Kind, value: str) -> str:
    if kind in (Kind.EMAIL, Kind.NAME, Kind.PASSPORT, Kind.KRA_PIN):
        return value.strip().lower()
    if kind is Kind.PHONE:
        digits = re.sub(r"\D", "", value)
        return "0" + digits[-9:]
    if kind in (Kind.CARD, Kind.ACCOUNT, Kind.ID):
        return re.sub(r"\D", "", value)
    return value.strip()


@dataclass
class Vault:
    """The placeholders of one conversation and the values behind them."""

    entries: dict[str, tuple[Kind, str]] = field(
        default_factory=dict
    )  # placeholder -> (kind, value)
    _by_value: dict[tuple[Kind, str], str] = field(default_factory=dict)

    @classmethod
    def from_entries(cls, entries: dict[str, tuple[Kind, str]]) -> Vault:
        vault = cls()
        for placeholder, (kind, value) in entries.items():
            vault.entries[placeholder] = (kind, value)
            vault._by_value[(kind, normalized(kind, value))] = placeholder
        return vault

    def placeholder_for(self, kind: Kind, value: str) -> tuple[str, bool]:
        key = (kind, normalized(kind, value))
        existing = self._by_value.get(key)
        if existing is not None:
            return existing, False
        n = 1 + sum(1 for k, _ in self.entries.values() if k is kind)
        placeholder = f"[{kind.value}_{n}]"
        self.entries[placeholder] = (kind, value)
        self._by_value[key] = placeholder
        return placeholder, True

    def value(self, placeholder: str) -> tuple[Kind, str] | None:
        return self.entries.get(placeholder)


@dataclass(frozen=True)
class Masked:
    text: str
    found: list[Kind]
    new_placeholders: list[str]

    @property
    def secret_shared(self) -> bool:
        return Kind.SECRET in self.found

    @property
    def card_number_shared(self) -> bool:
        return Kind.CARD in self.found


def mask(text: str, vault: Vault, known_names: Iterable[str] = ()) -> Masked:
    spans = find(text, known_names)
    out: list[str] = []
    new: list[str] = []
    cursor = 0
    for s in spans:
        out.append(text[cursor : s.start])
        placeholder, created = vault.placeholder_for(s.kind, s.value)
        if created:
            new.append(placeholder)
        out.append(placeholder)
        cursor = s.end
    out.append(text[cursor:])
    return Masked("".join(out), [s.kind for s in spans], new)


PLACEHOLDER = re.compile(
    r"\[(PHONE|EMAIL|CARD|ACCOUNT|KRA_PIN|ID|PASSPORT|DOB|SECRET|NAME)_(\d+)\]"
)


def display_form(kind: Kind, value: str) -> str:
    """How a value is shown back to the customer in chat: enough to recognise, not to copy."""

    if kind is Kind.NAME:
        return value
    if kind is Kind.EMAIL:
        local, _, domain = value.partition("@")
        return f"{local[:1]}•••@{domain}"
    if kind is Kind.PHONE:
        digits = normalized(kind, value)
        return f"{digits[:2]}•• ••• {digits[-3:]}"
    if kind in (Kind.CARD, Kind.ACCOUNT, Kind.ID, Kind.PASSPORT):
        digits = re.sub(r"\s", "", value)
        return f"••••{digits[-4:]}"
    if kind is Kind.KRA_PIN:
        return f"{value[:1]}•••••••••{value[-1:]}"
    return "•••"  # dates of birth and secrets are never shown back


@dataclass(frozen=True)
class Restored:
    text: str
    unknown_placeholders: list[str]


def restore(text: str, vault: Vault) -> Restored:
    """Replaces placeholders in a model's answer with their display forms. A placeholder the
    vault doesn't hold (the model made it up) becomes "[hidden]" and is reported."""

    unknown: list[str] = []

    def sub(m: re.Match[str]) -> str:
        entry = vault.value(m.group(0))
        if entry is None:
            unknown.append(m.group(0))
            return "[hidden]"
        return display_form(*entry)

    return Restored(PLACEHOLDER.sub(sub, text), unknown)


def find_unmasked(texts: Iterable[str]) -> list[Kind]:
    """The gateway's check: the kinds of personal data still present in an outgoing payload."""

    found: set[Kind] = set()
    for text in texts:
        for span in find(text):
            if span.kind in TRIPWIRE_KINDS:
                found.add(span.kind)
    return sorted(found)
