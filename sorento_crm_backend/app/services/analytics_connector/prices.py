"""Model prices for the connector's cost datasets (CHAT-TURN-COST, `documentation/plans/analytics/PLAN-chat-turn-cost-13oct.md`).

`llm_model_prices` is the one place a price lives; the views join it, so a price edit is a row
update and no query changes. Only prices the owner confirmed are seeded. Every other model stays
unpriced (cost NULL) until the owner adds its row: a guessed price would read as a fact. That
includes `gpt-4o-transcribe`, which is billed per audio minute and does not fit a per-token row.

Adding or changing a price is one statement (USD per 1M tokens, cached NULL when unknown):

    INSERT INTO llm_model_prices (model, input_usd_per_1m, cached_input_usd_per_1m, output_usd_per_1m, note)
    VALUES ('<exact model id from the usage log>', <input>, <cached input or NULL>, <output>, 'owner, <date>')
    ON CONFLICT (model) DO UPDATE SET input_usd_per_1m = EXCLUDED.input_usd_per_1m,
        cached_input_usd_per_1m = EXCLUDED.cached_input_usd_per_1m,
        output_usd_per_1m = EXCLUDED.output_usd_per_1m, note = EXCLUDED.note, updated_at = now();
"""
from __future__ import annotations

from typing import Any, NamedTuple

from sqlalchemy import text

# USD per 1M tokens: (input, cached input, output, source).
SEED_PRICES: dict[str, tuple[str, str | None, str, str]] = {
    # The chatbot parser's model on dev and prod (crew, 13 Oct 2026). The OpenAI pricing page
    # figures `scripts/chatbot_eval/budget.py` cites, confirmed by the owner 13 Oct 2026.
    "gpt-5.4-mini": ("0.75", "0.075", "4.50", "owner-confirmed 13 Oct 2026 (OpenAI pricing page)"),
}


def seed_prices(conn: Any) -> None:
    """Insert the seed rows that are missing. Never overwrites a price someone edited."""
    for model, (inp, cached, out, note) in SEED_PRICES.items():
        conn.execute(
            text(
                "INSERT INTO llm_model_prices (model, input_usd_per_1m, cached_input_usd_per_1m, output_usd_per_1m, note) "
                "VALUES (:m, CAST(:i AS numeric), CAST(:c AS numeric), CAST(:o AS numeric), :n) "
                "ON CONFLICT (model) DO NOTHING"
            ),
            {"m": model, "i": inp, "c": cached, "o": out, "n": note},
        )



class CostSql(NamedTuple):
    """Three SQL boolean/numeric expressions over a usage-log alias and a price-table alias."""

    cost_usd: str
    priced: str
    cached_price_missing: str


def cost_sql(u: str, p: str) -> CostSql:
    """The one per-call cost formula (MODEL-PRICES UC-1), shared by the `llm_calls` view and the usage endpoints.

    ``u`` is the `ai_assistant_usage_logs` alias, ``p`` the LEFT JOINed `llm_model_prices` alias.
    Anthropic reports cache reads OUTSIDE `prompt_tokens`, OpenAI and Gemini inside it. Unpriced
    or cached-without-cached-price is NULL, never 0.
    """
    cached = f"COALESCE({u}.cached_tokens, 0)"
    return CostSql(
        cost_usd=(
            f"(CASE WHEN {p}.model IS NULL OR ({cached} > 0 AND {p}.cached_input_usd_per_1m IS NULL) THEN NULL\n"
            f"       ELSE ((\n"
            f"         (CASE WHEN lower(btrim({u}.provider)) = 'anthropic' THEN {u}.prompt_tokens\n"
            f"               ELSE GREATEST({u}.prompt_tokens - {cached}, 0) END) * {p}.input_usd_per_1m\n"
            f"       + {cached} * COALESCE({p}.cached_input_usd_per_1m, 0)\n"
            f"       + {u}.completion_tokens * {p}.output_usd_per_1m\n"
            f"  ) / 1000000) END)"
        ),
        priced=f"({p}.model IS NOT NULL AND ({cached} = 0 OR {p}.cached_input_usd_per_1m IS NOT NULL))",
        cached_price_missing=f"({p}.model IS NOT NULL AND {p}.cached_input_usd_per_1m IS NULL AND {cached} > 0)",
    )
