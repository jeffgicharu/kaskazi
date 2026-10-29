"""The assistant's tools, their risk levels, and how each one runs.

The model never sees an internal id or a raw identifier. Accounts, cards and saved recipients are
offered as short references (A1, C1, R1) built from the signed-in customer's own records, and
personal details in tool results are masked with the conversation's vault before the model reads
them. Arguments are checked against strict schemas by the API and again here.

Risk levels:
- read: runs at once (balances, statements, quotes, fees).
- confirm: creates a pending action and a confirmation card built by the server from real data.
  Only the customer's tap runs it, never the model.
- confirm_with_code: as confirm, and the customer also enters a one-time code.
- handover: passes the conversation to a person.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from kaskazi.corebank import rules
from kaskazi.llm.contract import ToolSpec

Risk = Literal["read", "confirm", "confirm_with_code", "handover"]


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    # Every property is required and optional ones are nullable: this is the shape both providers'
    # strict modes accept.
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


ACCOUNT_REF = {
    "type": "string",
    "description": "An account reference from get_accounts, such as A1.",
}
CARD_REF = {"type": "string", "description": "A card reference from get_cards, such as C1."}


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    risk: Risk

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name, description=self.description, input_schema=self.input_schema
        )


TOOLS: list[ToolDef] = [
    ToolDef(
        "get_accounts",
        "The signed-in customer's accounts with their references (A1, A2), products, masked numbers, "
        "balances and status. Use it for balance questions and before any transfer.",
        _schema({}),
        "read",
    ),
    ToolDef(
        "get_mini_statement",
        "The latest transactions on one of the customer's accounts, newest first. The customer also sees "
        "them as a statement on screen, so summarise rather than list them all.",
        _schema(
            {
                "account": ACCOUNT_REF,
                "count": {
                    "type": ["integer", "null"],
                    "description": "How many transactions, 1 to 20. Default 10.",
                },
            }
        ),
        "read",
    ),
    ToolDef(
        "get_cards",
        "The customer's debit and credit cards with references (C1, C2), the last four digits, status "
        "(active, blocked, cancelled) and expiry.",
        _schema({}),
        "read",
    ),
    ToolDef(
        "get_loans",
        "The customer's current loans: product, amount borrowed, amount outstanding, rate, monthly "
        "instalment and next due date.",
        _schema({}),
        "read",
    ),
    ToolDef(
        "check_loan_eligibility",
        "Whether the customer can get a Kaskazi Personal Loan, worked out by the bank's rules from their "
        "own pay and existing loans: the affordability limit, the room for a new instalment, the largest "
        "loan for each term, and reasons if they don't qualify. Give amount and term to check a specific loan. "
        "If the customer asks a what-if with figures of their own (their net pay, other loan repayments), "
        "pass those figures: the result then uses them instead of the bank's records and says so. "
        "This is not a decision: a person decides every loan.",
        _schema(
            {
                "amount": {
                    "type": ["integer", "null"],
                    "description": "Loan amount in KES, if the customer named one.",
                },
                "term_months": {
                    "type": ["integer", "null"],
                    "description": "Term in months, if named.",
                },
                "net_monthly_pay": {
                    "type": ["integer", "null"],
                    "description": "Net monthly pay in KES, only if the customer gave their own figure.",
                },
                "other_monthly_repayments": {
                    "type": ["integer", "null"],
                    "description": "Monthly repayments on other loans in KES, only if the customer gave a figure.",
                },
            }
        ),
        "read",
    ),
    ToolDef(
        "get_loan_quote",
        "The exact cost of a Kaskazi Personal Loan: monthly instalment, fees and insurance deducted, amount "
        "received, total repaid and total cost of credit.",
        _schema(
            {
                "amount": {
                    "type": "integer",
                    "description": "Loan amount in KES, 50,000 to 3,000,000.",
                },
                "term_months": {"type": "integer", "description": "Term in months, 6 to 60."},
            }
        ),
        "read",
    ),
    ToolDef(
        "suggest_products",
        "Kaskazi savings products that would suit the customer, worked out by the bank's rules from their "
        "own accounts and transactions. Each comes with the rule that fired and its figures, which the "
        "customer is shown as 'why am I seeing this'. It never suggests credit. Use it when the customer "
        "asks how to earn more, what would suit them, or what to do with money they aren't using.",
        _schema({}),
        "read",
    ),
    ToolDef(
        "get_application_status",
        "The customer's latest Kaskazi Personal Loan application: its amount and term, where it stands "
        "(waiting for documents, with a reviewer, approved, declined), the documents given and their "
        "state, and the reviewer's reason if one was given.",
        _schema({}),
        "read",
    ),
    ToolDef(
        "get_salary_advance_limit",
        "The most the customer can take as a Kaskazi Salary Advance and why (half of net pay, the cap, or "
        "the one-third rule), with the fee on that amount.",
        _schema({}),
        "read",
    ),
    ToolDef(
        "look_up_fee",
        "The fee for a service from the bank's tariff. For tiered services give the amount.",
        _schema(
            {
                "service": {"type": "string", "enum": list(rules.FEE_SERVICES)},
                "amount": {
                    "type": ["number", "null"],
                    "description": "The amount in KES, for tiered fees.",
                },
            }
        ),
        "read",
    ),
    ToolDef(
        "get_recipients",
        "The customer's saved recipients with references (R1, R2): name, kind (mobile money, Kaskazi "
        "account, another bank) and masked phone or account number.",
        _schema({}),
        "read",
    ),
    ToolDef(
        "get_complaints",
        "The customer's complaints with their references (KZC-...), category, status and when a reply is due.",
        _schema({}),
        "read",
    ),
    ToolDef(
        "block_card",
        "Prepare a card block for the customer to confirm on screen. 'temporary' can be undone by the "
        "customer; 'lost_or_stolen' cancels the card for good and needs a replacement. Ask which if unclear.",
        _schema(
            {"card": CARD_REF, "mode": {"type": "string", "enum": ["temporary", "lost_or_stolen"]}}
        ),
        "confirm",
    ),
    ToolDef(
        "unblock_card",
        "Prepare unblocking a temporarily blocked card. The customer confirms on screen and enters a "
        "one-time code. A card blocked as lost or stolen can't be unblocked.",
        _schema({"card": CARD_REF}),
        "confirm_with_code",
    ),
    ToolDef(
        "order_replacement_card",
        "Prepare a replacement for a card blocked as lost or stolen, to collect at a branch, for the "
        "customer to confirm. Branch codes: "
        + ", ".join(f"{b['code']} {b['name']}" for b in rules.bank()["branches"])
        + ".",
        _schema(
            {
                "card": CARD_REF,
                "branch_code": {"type": "string", "description": "A 3-digit branch code."},
            }
        ),
        "confirm",
    ),
    ToolDef(
        "transfer_between_own_accounts",
        "Prepare moving money between two of the customer's own accounts, for them to confirm on screen.",
        _schema(
            {
                "from_account": ACCOUNT_REF,
                "to_account": ACCOUNT_REF,
                "amount": {"type": "number", "description": "Amount in KES."},
            }
        ),
        "confirm",
    ),
    ToolDef(
        "send_to_mobile_money",
        "Prepare sending money from one of the customer's accounts to a mobile money wallet, for them to "
        "confirm on screen. Give a saved recipient (R1) or a phone placeholder the customer typed (such as "
        "[PHONE_1]), not both.",
        _schema(
            {
                "from_account": ACCOUNT_REF,
                "recipient": {
                    "type": ["string", "null"],
                    "description": "A saved recipient reference, such as R1.",
                },
                "phone": {
                    "type": ["string", "null"],
                    "description": "A phone placeholder from the conversation, such as [PHONE_1].",
                },
                "amount": {"type": "number", "description": "Amount in KES."},
            }
        ),
        "confirm",
    ),
    ToolDef(
        "raise_complaint",
        "Prepare a complaint for the customer to confirm on screen. Summarise what happened in the "
        "customer's own terms, with dates and amounts they gave.",
        _schema(
            {
                "category": {
                    "type": "string",
                    "enum": ["card", "transfer", "loan", "fees", "service", "app", "other"],
                },
                "summary": {"type": "string", "description": "What happened, 1 to 4 sentences."},
            }
        ),
        "confirm",
    ),
    ToolDef(
        "hand_over",
        "Pass the conversation to a member of staff, with the reason. Use it when the customer asks for a "
        "person, when they describe fraud, a bereavement, financial hardship or distress, or when you "
        "can't help from the documents and tools you have.",
        _schema(
            {
                "reason": {
                    "type": "string",
                    "description": "One sentence for the colleague taking over.",
                },
                "urgent": {
                    "type": "boolean",
                    "description": "True for fraud in progress or a customer in distress.",
                },
            }
        ),
        "handover",
    ),
]

BY_NAME = {t.name: t for t in TOOLS}
READ_TOOLS = [t for t in TOOLS if t.risk == "read"]


def tools_for(intent: str) -> list[ToolDef]:
    """Least privilege: a question gets read-only tools; only a task gets tools that change things."""

    if intent in ("task", "complaint"):
        return list(TOOLS)
    return [t for t in TOOLS if t.risk in ("read", "handover")]


def toolset_hash(tools: list[ToolDef]) -> str:
    import hashlib

    blob = json.dumps([[t.name, t.description, t.input_schema] for t in tools], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class Preview:
    """What the confirmation card shows. Built by the server from the bank's own records."""

    title: str
    rows: list[tuple[str, str]]
    note: str | None = None
    needs_code: bool = False


@dataclass
class ToolOutcome:
    content: dict[str, Any]  # what the model reads, before masking
    is_error: bool = False
    display: dict[str, Any] | None = None  # a card for the customer's screen (statement, quote)
    preview: Preview | None = None  # a write waiting for the customer
    handover: dict[str, Any] | None = None
    resolved_arguments: dict[str, Any] = field(
        default_factory=dict
    )  # internal ids, never shown to the model


Runner = Callable[..., Awaitable[ToolOutcome]]
