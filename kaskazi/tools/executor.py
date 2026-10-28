"""Running the assistant's tools against the core-banking service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from kaskazi.controls.pii import Kind, Vault, display_form
from kaskazi.corebank import rules
from kaskazi.corebank.client import CoreBank, CoreBankError
from kaskazi.tools.registry import BY_NAME, Preview, ToolOutcome


def kes(amount: float) -> str:
    return f"KES {amount:,.0f}" if float(amount).is_integer() else f"KES {amount:,.2f}"


@dataclass
class Refs:
    accounts: dict[str, dict[str, Any]]
    cards: dict[str, dict[str, Any]]
    recipients: dict[str, dict[str, Any]]


class Executor:
    def __init__(
        self, bank: CoreBank, sandbox_id: uuid.UUID, customer_id: uuid.UUID, vault: Vault
    ) -> None:
        self.bank = bank
        self.sandbox_id = sandbox_id
        self.customer_id = customer_id
        self.vault = vault
        self._refs: Refs | None = None

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self.bank.call(
            method, path, sandbox_id=self.sandbox_id, customer_id=self.customer_id, **kwargs
        )

    async def refs(self, refresh: bool = False) -> Refs:
        if self._refs is None or refresh:
            accounts = await self._call("GET", "/v1/accounts")
            cards = await self._call("GET", "/v1/cards")
            recipients = await self._call("GET", "/v1/recipients")
            self._refs = Refs(
                accounts={f"A{i}": a for i, a in enumerate(accounts, start=1)},
                cards={f"C{i}": c for i, c in enumerate(cards, start=1)},
                recipients={f"R{i}": r for i, r in enumerate(recipients, start=1)},
            )
        return self._refs

    async def known_names(self) -> list[str]:
        customer = await self._call("GET", "/v1/customer")
        refs = await self.refs()
        names = [customer["full_name"], *customer["full_name"].split()]
        for r in refs.recipients.values():
            names.append(r["name"])
            names.extend(part for part in r["name"].split() if len(part) >= 4)
        return names

    async def run(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        tool = BY_NAME.get(name)
        if tool is None:
            return ToolOutcome({"error": f"There is no tool called {name}."}, is_error=True)
        try:
            handler = getattr(self, f"_{name}")
            outcome: ToolOutcome = await handler(**args)
            return outcome
        except CoreBankError as exc:
            return ToolOutcome({"error": exc.message, "code": exc.code}, is_error=True)
        except (TypeError, ValueError, KeyError) as exc:
            return ToolOutcome({"error": f"The arguments weren't usable: {exc}"}, is_error=True)

    # --- Read tools -----------------------------------------------------------------------------

    async def _get_accounts(self) -> ToolOutcome:
        refs = await self.refs()
        accounts = [
            {
                "ref": ref,
                "product": a["product"],
                "number": a["number"],
                "currency": a["currency"],
                "balance": a["balance"],
                "status": a["status"],
            }
            for ref, a in refs.accounts.items()
        ]
        display = {
            "type": "accounts",
            "accounts": [
                {
                    "product": a["product"],
                    "number": display_form(Kind.ACCOUNT, a["number"]),
                    "balance": a["balance"],
                    "currency": a["currency"],
                }
                for a in refs.accounts.values()
            ],
        }
        return ToolOutcome({"accounts": accounts}, display=display)

    async def _get_mini_statement(self, account: str, count: int | None = None) -> ToolOutcome:
        refs = await self.refs()
        a = _ref(refs.accounts, account, "account")
        n = max(1, min(int(count or 10), 20))
        data = await self._call("GET", f"/v1/accounts/{a['id']}/transactions", params={"limit": n})
        rows = [
            {k: t[k] for k in ("at", "description", "amount", "balance_after")}
            for t in data["transactions"]
        ]
        display = {
            "type": "statement",
            "product": a["product"],
            "number": display_form(Kind.ACCOUNT, a["number"]),
            "transactions": rows,
        }
        return ToolOutcome(
            {"account": account, "product": a["product"], "transactions": rows}, display=display
        )

    async def _get_cards(self) -> ToolOutcome:
        refs = await self.refs()
        cards = [
            {
                "ref": ref,
                "kind": c["kind"],
                "last4": c["last4"],
                "status": c["status"],
                "expires": c["expires"],
                "account": c["account_product"],
                "credit_limit": c["credit_limit"],
                "replacement_ordered": c["replacement_ordered"],
            }
            for ref, c in refs.cards.items()
        ]
        return ToolOutcome({"cards": cards})

    async def _get_loans(self) -> ToolOutcome:
        return ToolOutcome({"loans": await self._call("GET", "/v1/loans")})

    async def _check_loan_eligibility(
        self, amount: int | None = None, term_months: int | None = None
    ) -> ToolOutcome:
        result = await self._call(
            "POST",
            "/v1/loans/personal/eligibility",
            json={"amount": amount, "term_months": term_months},
        )
        return ToolOutcome(
            {"eligibility": result, "note": "Indicative only: a person decides every loan."},
            display={"type": "eligibility", **result},
        )

    async def _get_loan_quote(self, amount: int, term_months: int) -> ToolOutcome:
        quote = await self._call(
            "POST", "/v1/loans/personal/quote", json={"amount": amount, "term_months": term_months}
        )
        return ToolOutcome({"quote": quote}, display={"type": "quote", **quote})

    async def _get_salary_advance_limit(self) -> ToolOutcome:
        return ToolOutcome(
            {"salary_advance": await self._call("POST", "/v1/loans/salary-advance/limit")}
        )

    async def _look_up_fee(self, service: str, amount: float | None = None) -> ToolOutcome:
        params = {"amount": amount} if amount is not None else None
        return ToolOutcome({"fee": await self._call("GET", f"/v1/fees/{service}", params=params)})

    async def _get_recipients(self) -> ToolOutcome:
        refs = await self.refs()
        return ToolOutcome(
            {
                "recipients": [
                    {
                        "ref": ref,
                        "name": r["name"],
                        "kind": r["kind"],
                        "phone": r["phone"],
                        "account_number": r["account_number"],
                    }
                    for ref, r in refs.recipients.items()
                ]
            }
        )

    async def _get_complaints(self) -> ToolOutcome:
        return ToolOutcome({"complaints": await self._call("GET", "/v1/complaints")})

    # --- Tools that change things: each prepares a confirmation card --------------------------

    async def _block_card(self, card: str, mode: str) -> ToolOutcome:
        refs = await self.refs()
        c = _ref(refs.cards, card, "card")
        if c["status"] == "cancelled":
            return ToolOutcome({"error": "That card is already cancelled."}, is_error=True)
        if mode == "temporary" and c["status"] == "blocked":
            return ToolOutcome({"error": "That card is already blocked."}, is_error=True)
        label = f"{c['kind'].title()} card ending {c['last4']}"
        if mode == "temporary":
            preview = Preview(
                f"Block your {label}",
                [
                    ("Card", label),
                    ("Block", "Temporary"),
                    ("Undo", "You can unblock it yourself with a one-time code"),
                ],
            )
        else:
            fee = rules.bank()["cards"]["debit"]["replacement_fee"]
            preview = Preview(
                f"Cancel your {label} as lost or stolen",
                [
                    ("Card", label),
                    ("Block", "Lost or stolen"),
                    ("Undo", "Not possible: the card is cancelled for good"),
                    ("Replacement", f"{kes(fee)}, ready at a branch"),
                ],
                note="If you might just have misplaced it, a temporary block is safer.",
            )
        return ToolOutcome(
            {"status": "waiting_for_customer", "summary": preview.title},
            preview=preview,
            resolved_arguments={"card_id": c["id"], "mode": mode, "last4": c["last4"]},
        )

    async def _unblock_card(self, card: str) -> ToolOutcome:
        refs = await self.refs()
        c = _ref(refs.cards, card, "card")
        if c["status"] == "cancelled":
            return ToolOutcome(
                {
                    "error": "A card blocked as lost or stolen can't be unblocked. Offer a replacement."
                },
                is_error=True,
            )
        if c["status"] != "blocked":
            return ToolOutcome({"error": "That card isn't blocked."}, is_error=True)
        label = f"{c['kind'].title()} card ending {c['last4']}"
        preview = Preview(
            f"Unblock your {label}",
            [("Card", label)],
            needs_code=True,
            note="We've sent a one-time code to your registered phone. Enter it to confirm.",
        )
        return ToolOutcome(
            {"status": "waiting_for_customer", "summary": preview.title},
            preview=preview,
            resolved_arguments={"card_id": c["id"], "last4": c["last4"]},
        )

    async def _order_replacement_card(self, card: str, branch_code: str) -> ToolOutcome:
        refs = await self.refs()
        c = _ref(refs.cards, card, "card")
        branches = {b["code"]: b["name"] for b in rules.bank()["branches"]}
        if branch_code not in branches:
            return ToolOutcome(
                {"error": "Unknown branch code. Ask which branch they'd like."}, is_error=True
            )
        if c["status"] != "cancelled":
            return ToolOutcome(
                {"error": "Only a card blocked as lost or stolen is replaced."}, is_error=True
            )
        b = rules.bank()["cards"]
        fee, days = (
            b["debit"]["replacement_fee"],
            b["blocking"]["replacement_delivery_working_days"],
        )
        preview = Preview(
            "Order a replacement card",
            [
                ("Card", f"{c['kind'].title()} card ending {c['last4']}"),
                ("Collect at", branches[branch_code]),
                ("Ready within", f"{days} working days"),
                ("Fee", kes(fee)),
                ("Taken from", c["account_product"]),
            ],
        )
        return ToolOutcome(
            {"status": "waiting_for_customer", "summary": preview.title},
            preview=preview,
            resolved_arguments={"card_id": c["id"], "branch_code": branch_code},
        )

    async def _transfer_between_own_accounts(
        self, from_account: str, to_account: str, amount: float
    ) -> ToolOutcome:
        refs = await self.refs()
        src, dst = (
            _ref(refs.accounts, from_account, "account"),
            _ref(refs.accounts, to_account, "account"),
        )
        if src["id"] == dst["id"]:
            return ToolOutcome({"error": "Choose two different accounts."}, is_error=True)
        if amount <= 0 or amount > src["balance"]:
            return ToolOutcome({"error": f"That account has {kes(src['balance'])}."}, is_error=True)
        preview = Preview(
            f"Move {kes(amount)} between your accounts",
            [
                ("From", f"{src['product']} {display_form(Kind.ACCOUNT, src['number'])}"),
                ("To", f"{dst['product']} {display_form(Kind.ACCOUNT, dst['number'])}"),
                ("Amount", kes(amount)),
                ("Fee", kes(0)),
            ],
        )
        return ToolOutcome(
            {"status": "waiting_for_customer", "summary": preview.title},
            preview=preview,
            resolved_arguments={
                "from_account_id": src["id"],
                "to_account_id": dst["id"],
                "amount": str(amount),
            },
        )

    async def _send_to_mobile_money(
        self,
        from_account: str,
        amount: float,
        recipient: str | None = None,
        phone: str | None = None,
    ) -> ToolOutcome:
        refs = await self.refs()
        src = _ref(refs.accounts, from_account, "account")
        t = rules.bank()["transfers"]
        if bool(recipient) == bool(phone):
            return ToolOutcome(
                {"error": "Give a saved recipient or a phone number, not both."}, is_error=True
            )
        resolved: dict[str, Any] = {"from_account_id": src["id"], "amount": str(amount)}
        if recipient:
            r = _ref(refs.recipients, recipient, "recipient")
            if r["kind"] != "mobile_money":
                return ToolOutcome(
                    {"error": "That recipient isn't a mobile money wallet."}, is_error=True
                )
            to_label, resolved["recipient_id"] = f"{r['name']}, {_spaced(r['phone'])}", r["id"]
        else:
            entry = self.vault.value(phone or "")
            if entry is None or entry[0] is not Kind.PHONE:
                return ToolOutcome(
                    {"error": "Use a phone placeholder from the conversation, such as [PHONE_1]."},
                    is_error=True,
                )
            number = "0" + "".join(ch for ch in entry[1] if ch.isdigit())[-9:]
            to_label, resolved["phone"] = f"{_spaced(number)} (a new recipient)", number
        if amount > t["mobile_money_maximum_per_transfer"]:
            return ToolOutcome(
                {
                    "error": f"The most per transfer is {kes(t['mobile_money_maximum_per_transfer'])}."
                },
                is_error=True,
            )
        fee = rules.tiered_fee(t["mobile_money"], amount) or 0
        if amount + fee > src["balance"]:
            return ToolOutcome(
                {"error": f"That account has {kes(src['balance'])}, and the fee is {kes(fee)}."},
                is_error=True,
            )
        preview = Preview(
            f"Send {kes(amount)} by mobile money",
            [
                ("From", f"{src['product']} {display_form(Kind.ACCOUNT, src['number'])}"),
                ("To", to_label),
                ("Amount", kes(amount)),
                ("Fee", kes(fee)),
                ("Total", kes(amount + fee)),
            ],
            note="Check the number carefully: money sent to the wrong wallet is hard to get back.",
        )
        return ToolOutcome(
            {"status": "waiting_for_customer", "summary": preview.title, "fee": fee},
            preview=preview,
            resolved_arguments=resolved,
        )

    async def _raise_complaint(self, category: str, summary: str) -> ToolOutcome:
        c = rules.bank()["complaints"]
        from kaskazi.controls.pii import restore

        shown = restore(summary, self.vault).text
        preview = Preview(
            "Raise a complaint",
            [("About", category.title()), ("What happened", shown)],
            note=f"We'll acknowledge it within {c['acknowledge_within_hours']} hours and aim to "
            f"resolve it within {c['resolve_within_working_days']} working days.",
        )
        return ToolOutcome(
            {"status": "waiting_for_customer", "summary": preview.title},
            preview=preview,
            resolved_arguments={"category": category, "summary": summary},
        )

    async def _hand_over(self, reason: str, urgent: bool = False) -> ToolOutcome:
        return ToolOutcome(
            {"status": "handed_over"}, handover={"reason": reason, "urgent": bool(urgent)}
        )


async def execute_confirmed(
    bank: CoreBank,
    sandbox_id: uuid.UUID,
    customer_id: uuid.UUID,
    tool: str,
    resolved: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """Runs a confirmed action against the core-banking service. Raises CoreBankError."""

    def call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        return bank.call(
            method,
            path,
            sandbox_id=sandbox_id,
            customer_id=customer_id,
            json=body,
            idempotency_key=idempotency_key,
        )

    if tool == "block_card":
        return dict(
            await call("POST", f"/v1/cards/{resolved['card_id']}/block", {"mode": resolved["mode"]})
        )
    if tool == "unblock_card":
        return dict(await call("POST", f"/v1/cards/{resolved['card_id']}/unblock"))
    if tool == "order_replacement_card":
        return dict(
            await call(
                "POST",
                f"/v1/cards/{resolved['card_id']}/replacement",
                {"branch_code": resolved["branch_code"]},
            )
        )
    if tool == "transfer_between_own_accounts":
        return dict(
            await call(
                "POST",
                "/v1/transfers/own",
                {k: resolved[k] for k in ("from_account_id", "to_account_id", "amount")},
            )
        )
    if tool == "send_to_mobile_money":
        body = {k: resolved.get(k) for k in ("from_account_id", "recipient_id", "phone", "amount")}
        return dict(await call("POST", "/v1/transfers/mobile-money", body))
    if tool == "raise_complaint":
        return dict(
            await call(
                "POST",
                "/v1/complaints",
                {"category": resolved["category"], "summary": resolved["summary"]},
            )
        )
    raise ValueError(f"{tool} doesn't change anything")


def _ref(table: dict[str, dict[str, Any]], ref: str, what: str) -> dict[str, Any]:
    if ref not in table:
        raise ValueError(f"no {what} {ref}; use a reference from the list")
    return table[ref]


def _spaced(phone: str) -> str:
    return f"{phone[:4]} {phone[4:7]} {phone[7:]}"
