"""Masking personal data: what is found, what is left alone, and that nothing leaks."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kaskazi.controls.pii import Kind, Vault, find, find_unmasked, luhn_ok, mask, restore


def account_number(branch: str, serial: int) -> str:
    body = f"0{branch}{serial:08d}"
    for check in range(10):
        if luhn_ok(body + str(check)):
            return body + str(check)
    raise AssertionError


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("call me on 0712 345 678", Kind.PHONE),
        ("my number is +254712345678", Kind.PHONE),
        ("try 0110-123-456", Kind.PHONE),
        ("email wanjiru.k@example.com please", Kind.EMAIL),
        ("card 4111 1111 1111 1111", Kind.CARD),
        ("my KRA PIN is A012345678Z", Kind.KRA_PIN),
        ("ID number 23456789", Kind.ID),
        ("kitambulisho ni 31234567", Kind.ID),
        ("passport AK1234567", Kind.PASSPORT),
        ("date of birth 14/03/1991", Kind.DOB),
        ("my pin is 4821", Kind.SECRET),
        ("the otp: 739104", Kind.SECRET),
        ("password is Kamau2024!", Kind.SECRET),
    ],
)
def test_finds_each_kind(text: str, kind: Kind) -> None:
    assert [s.kind for s in find(text)] == [kind]


def test_finds_kaskazi_account_numbers_by_their_check_digit() -> None:
    good = account_number("201", 4821)
    assert [s.kind for s in find(f"move it to {good}")] == [Kind.ACCOUNT]
    bad = good[:-1] + str((int(good[-1]) + 1) % 10)
    assert find(f"move it to {bad}") == []


@pytest.mark.parametrize(
    "text",
    [
        "I want to borrow 15000000",
        "send KES 10000000 to savings",
        "loan of 1,500,000 over 36 months",
        "the fee is KES 2,500",
        "my KRA PIN is needed for the loan",
        "card payments with a PIN are limited",
        "is the file password-protected?",
        "complaint reference KZC-123456",
        "a 24 month loan",
        "prompt assistant.router@1.0.0 was used",
        "request 83712345a0c3-route and action 12345678-90ab-4cde-8f01-23456789abcd",
        "hash 4111111111111111abcdef",
    ],
)
def test_leaves_ordinary_banking_text_alone(text: str) -> None:
    assert find(text) == []


def test_same_value_same_placeholder_and_restore_hides_digits() -> None:
    vault = Vault()
    first = mask("send 2,000 to 0712 345 678", vault)
    again = mask("did 0712345678 get it?", vault)
    assert first.text == "send 2,000 to [PHONE_1]"
    assert again.text == "did [PHONE_1] get it?"
    assert first.new_placeholders == ["[PHONE_1]"] and again.new_placeholders == []
    shown = restore("I sent it to [PHONE_1].", vault)
    assert shown.text == "I sent it to 07•• ••• 678." and shown.unknown_placeholders == []


def test_restore_hides_placeholders_the_model_made_up() -> None:
    shown = restore("Call [PHONE_9] now", Vault())
    assert shown.text == "Call [hidden] now" and shown.unknown_placeholders == ["[PHONE_9]"]


def test_known_names_are_masked() -> None:
    vault = Vault()
    masked = mask(
        "Pay Achieng Odhiambo 500 bob", vault, known_names=["Achieng Odhiambo", "Achieng"]
    )
    assert masked.text == "Pay [NAME_1] 500 bob"


def test_a_first_name_typed_and_a_saved_recipients_first_name_share_a_placeholder() -> None:
    # Found by the golden set: "send 500 to Rose" and the saved "Rose Kiprop" masked to different
    # placeholders, so the model couldn't tell they were the same person.
    import json

    vault, names = Vault(), ["Rose Kiprop", "Rose", "Kiprop"]
    typed = mask("send 500 to Rose", vault, names).text
    saved = mask(json.dumps({"name": "Rose Kiprop", "first_name": "Rose"}), vault, names).text
    assert typed == "send 500 to [NAME_1]"
    assert json.loads(saved) == {"name": "[NAME_2]", "first_name": "[NAME_1]"}


def test_secrets_and_card_numbers_raise_the_warning_flags() -> None:
    masked = mask("my card is 4111111111111111 and pin 1234", Vault())
    assert masked.card_number_shared and masked.secret_shared
    assert "4111" not in masked.text and "1234" not in masked.text


phones = st.builds(
    lambda prefix, rest, sep: f"{prefix}{rest[:3]}{sep}{rest[3:6]}{sep}{rest[6:]}",
    st.sampled_from(["07", "01", "+2547", "2541"]),
    st.text("0123456789", min_size=8, max_size=8).map(lambda d: d + "1"),
    st.sampled_from(["", " ", "-"]),
)
emails = st.builds(
    lambda user, domain: f"{user}@{domain}.example",
    st.text("abcdefghijklmnopqrstuvwxyz.", min_size=1, max_size=12).filter(lambda s: s.strip(".")),
    st.text("abcdefghijklmnop", min_size=2, max_size=10),
)
filler = st.text("abcdefghij klmnop,.?", max_size=30)


@settings(max_examples=300)
@given(st.lists(st.tuples(filler, st.one_of(phones, emails)), min_size=1, max_size=4), filler)
def test_masked_text_never_holds_the_original_values(
    pieces: list[tuple[str, str]], tail: str
) -> None:
    text = "".join(f"{f} {v} " for f, v in pieces) + tail
    masked = mask(text, Vault())
    assert find_unmasked([masked.text]) == []


def test_the_bank_documents_trip_nothing() -> None:
    documents = Path(__file__).parents[1] / "corpus" / "documents"
    for path in sorted(documents.glob("*.md")):
        assert find_unmasked([path.read_text()]) == [], path.name
