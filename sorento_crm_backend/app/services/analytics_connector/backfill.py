"""One-time backfills for the connector's cost datasets (CHAT-TURN-COST, migration
`ctc_0001_turn_costs`). Both read the turn trace once, so the views never have to.
Idempotent: each touches only rows still NULL.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text


def _understood_fact(key: str) -> str:
    return f"(jsonb_path_query_first(t.trace, '$[*] ? (@.stage == \"understood\").facts.{key}') #>> '{{}}')::int"


def backfill_cached_tokens(conn: Any) -> None:
    """Fill `cached_tokens` on chatbot parser usage rows written before the column existed.

    The value is the turn's `understood` stage fact (the trace has carried the parser's cache
    hit since LLM-TEST-COST), or 0 when the trace has none. Other features' NULLs stay NULL:
    no other writer ever reported a cache hit, and the views read NULL as 0.
    """
    conn.execute(
        text(
            "UPDATE ai_assistant_usage_logs u "
            f"   SET cached_tokens = COALESCE({_understood_fact('cached_tokens')}, 0) "
            "  FROM chatbot.turns t "
            " WHERE u.cached_tokens IS NULL AND u.feature = 'chatbot_parser' "
            "   AND t.id::text = u.chatbot_turn_id"
        )
    )


def backfill_prompt_version(conn: Any) -> None:
    """Fill `chat_ask_facts.prompt_version` for facts recorded before the column existed."""
    conn.execute(
        text(
            "UPDATE chatbot.chat_ask_facts f "
            f"   SET prompt_version = {_understood_fact('prompt_version')} "
            "  FROM chatbot.turns t "
            " WHERE f.prompt_version IS NULL AND f.turn_id = t.id"
        )
    )
