"""Replies the bank writes itself, for moments when a model shouldn't.

These go out without a model call: a polite refusal for off-topic messages, a firm one for
attempts to take over the assistant, the friendly message when a spending cap is reached, the
safe answer when no provider can help, and the handover notice. Figures in them come from
bank.toml.
"""

from __future__ import annotations

from kaskazi.corebank.rules import bank
from kaskazi.llm.contract import GatewayRefusal

_service = bank()["service"]

OFF_TOPIC = (
    "I can only help with your Kaskazi banking: accounts, cards, loans, payments, fees and the app. "
    "Is there something about your account I can help with?"
)

INJECTION = (
    "I can't do that. I can help you with your Kaskazi accounts, cards, loans and payments, "
    "and I'll always ask you to confirm before anything changes."
)

DONT_SHARE_WARNING = (
    "I've hidden what looked like a PIN, password, code or card number. Please never share these "
    "with anyone, including Kaskazi staff and this assistant."
)

SAFE_ANSWER = (
    "I'm not sure I have that right, so I'd rather not guess. Would you like me to pass this to a colleague? "
    'You can also tap "Talk to a person" at any time.'
)

ASSISTANT_UNAVAILABLE = (
    "I can't answer right now. You can still check your balance, see your statement or block a card "
    "with the quick actions, or talk to a person."
)

HANDOVER = (
    "I'm passing you to a member of our team, with this conversation so you won't need to repeat yourself. "
    f"Someone will reply here, usually within {_service['assistant_handover_target_minutes']} minutes "
    f"during staffed hours ({_service['staffed_hours']})."
)

HANDOVER_FRAUD = (
    'If someone else may have your card or details, block your card now: tap "Block a card" below, '
    "or ask me to do it. " + HANDOVER
)


def cap_message(refusal: GatewayRefusal) -> str:
    if refusal is GatewayRefusal.AI_DISABLED:
        return "The assistant is switched off for now. " + _QUICK
    if refusal is GatewayRefusal.SITE_MONTH_CAP:
        return "This demonstration has used its AI allowance for the month. " + _QUICK
    if refusal is GatewayRefusal.SITE_DAY_CAP:
        return (
            "This demonstration has used today's AI allowance. It resets at midnight, Nairobi time. "
            + _QUICK
        )
    return (
        "You've used today's allowance of questions for this demonstration. It resets at midnight, "
        "Nairobi time. " + _QUICK
    )


_QUICK = "The quick actions still work, because they don't use AI, and you can talk to a person."
