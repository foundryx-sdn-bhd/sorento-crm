"""Model prices for the connector's cost datasets, and the one-time cached-token backfill
(CHAT-TURN-COST, `documentation/plans/analytics/PLAN-chat-turn-cost-13oct.md`).

`llm_model_prices` is the one place a price lives; the views join it, so a price edit is a row
update and no query changes. Only prices the repo already cites with a source and a date are
seeded (`scripts/chatbot_eval/budget.py`). Every other model stays unpriced (cost NULL) until
the owner adds its row: a guessed price would read as a fact.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

# USD per 1M tokens: (input, cached input, output, source).
SEED_PRICES: dict[str, tuple[str, str, str, str]] = {
    "gpt-5.4-mini": ("0.75", "0.075", "4.50", "https://developers.openai.com/api/docs/pricing, read 10 Oct 2026"),
    "gpt-4.1-mini": ("0.40", "0.10", "1.60", "https://developers.openai.com/api/docs/pricing, read 10 Oct 2026"),
    "claude-sonnet-5-5": ("2.00", "0.10", "10.00", "https://platform.claude.com/docs/en/about-claude/pricing, read 11 Oct 2026"),
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


def backfill_cached_tokens(conn: Any) -> None:
    """Fill `cached_tokens` on usage rows written before the column existed.

    A `chatbot_parser` row takes its turn's `understood` stage fact (the trace has carried the
    parser's cache hit since LLM-TEST-COST), or 0 when the trace has none. Other features'
    NULLs stay NULL: no other writer ever reported a cache hit, and the views read NULL as 0.
    """
    conn.execute(
        text(
            "UPDATE ai_assistant_usage_logs u "
            "   SET cached_tokens = COALESCE((jsonb_path_query_first(t.trace, "
            "         '$[*] ? (@.stage == \"understood\").facts.cached_tokens') #>> '{}')::int, 0) "
            "  FROM chatbot.turns t "
            " WHERE u.cached_tokens IS NULL AND u.feature = 'chatbot_parser' "
            "   AND t.id::text = u.chatbot_turn_id"
        )
    )
