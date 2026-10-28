"""List prices, in US dollars per million tokens, and the cost of one call.

Sources, read on 29 October 2026:
- Anthropic: the Claude API price table (standard rates; 5-minute cache writes cost 1.25x the
  input rate, cache reads as listed). Claude Haiku 5.5 has a second rate card for prompts over
  100,000 tokens.
- OpenAI: developers.openai.com/api/docs/pricing, standard tier.
"""

from __future__ import annotations

from dataclasses import dataclass

from kaskazi.llm.contract import Usage


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float  # 5-minute writes; OpenAI has no separate write price
    long_prompt_over: int | None = None  # prompt size above which `long` applies
    long: Price | None = None


def _anthropic(input_: float, output: float, cache_read: float) -> Price:
    return Price(input_, output, cache_read, input_ * 1.25)


PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(
        0.10,
        0.50,
        0.01,
        0.125,
        long_prompt_over=100_000,
        long=Price(0.50, 2.50, 0.05, 0.625),
    ),
    "claude-sonnet-5-5": _anthropic(2.00, 10.00, 0.20),
    "claude-opus-5-5": _anthropic(4.00, 20.00, 0.20),
    "gpt-5.4-nano": Price(0.20, 1.25, 0.02, 0.20),
    "gpt-5.4-mini": Price(0.75, 4.50, 0.075, 0.75),
    "gpt-5.4": Price(2.50, 15.00, 0.25, 2.50),
    "text-embedding-3-small": Price(0.02, 0.0, 0.0, 0.0),
    "fake": Price(0.0, 0.0, 0.0, 0.0),
}


def cost_usd(model: str, usage: Usage) -> float:
    price = PRICES[model]
    prompt = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    if (
        price.long is not None
        and price.long_prompt_over is not None
        and prompt > price.long_prompt_over
    ):
        price = price.long
    total = (
        usage.input_tokens * price.input
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
        + usage.output_tokens * price.output
    )
    return round(total / 1_000_000, 8)


def worst_case_usd(model: str, prompt_tokens: int, max_tokens: int) -> float:
    """The most a call can cost: the whole prompt uncached, written to the cache, and every
    output token used. The gateway reserves this before calling and settles the real cost after."""

    usage = Usage(cache_write_tokens=prompt_tokens, output_tokens=max_tokens)
    return cost_usd(model, usage)
