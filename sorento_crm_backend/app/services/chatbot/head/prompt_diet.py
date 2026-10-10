"""PROMPT-DIET: the parser prompt as a stable core plus the cards a turn needs.

Replaces the one static v50 prompt (documentation/plans/chatbot/PLAN-prompt-diet-11oct.md)
when `CHATBOT_PROMPT_DIET` is on. Which cards a turn gets is decided from STATE (what the
conversation has open) and from VECTORS (the nearest worked examples), never from the
customer's words: no pattern or keyword rule lives here. The parser's own output says when
a card was missing (`missing`), and the engine re-calls once with it.

Data files (the registry fallback and the migration seed) are produced by
`scripts/prompt_diet_cut.py`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.config import settings
from app.services.chatbot.head.embedding_access import EmbeddingAccess, resolve_embedding_access  # noqa: F401


CARD_MARKER_OPEN = "<<card:"
CARD_MARKER_CLOSE = ">>"

#: Cards that carry conversation state: pinned by what is open, never retrieved.
STATE_CARDS = frozenset({"open_question", "escalation"})

#: An area's card needs these loaded beside it.
COMPANIONS: dict[str, tuple[str, ...]] = {"order": ("account",), "sales": ("account", "order")}

#: Fixed order, so the same card set always builds the same prompt prefix: state cards
#: first, then the areas in `chatbot_domains` order (companions ahead of their users).
CARD_ORDER: tuple[str, ...] = (
    "open_question",
    "escalation",
    "account",
    "master_products",
    "product_attachment",
    "promotion",
    "inventory",
    "order",
    "sales",
    "resource_attachment",
    "spo_allocation",
    "ideate",
    "purchase_order",
    "purchase_cost",
)

#: The pending kinds that are an escalation offer (`turn/pending.py`), not a business pick.
ESCALATION_PENDING_KINDS = frozenset({"team_pick", "member_offer", "company_pick"})

#: `order_status` values that mean a sales ask even when `domain_hint` says otherwise.
SALES_ORDER_STATUSES = frozenset({"sales_report", "sales_analysis", "top_selling", "sales_ranking"})

#: The embedding call sits on a customer's turn: a slow provider is a fallback, not a wait.
EMBED_TIMEOUT_SECONDS = 5.0

EXAMPLES_HEADING = "== WORKED EXAMPLES =="


@dataclass(frozen=True)
class Example:
    area: str
    message: str
    context: str | None
    expected_output: dict[str, Any]
    id: str | None = None


def enabled() -> bool:
    return bool(settings.chatbot_prompt_diet)


def parse_cards(text: str) -> dict[str, str]:
    """Split the cards document on `<<card:NAME>>` lines; text before the first is ignored."""
    cards: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        if line.startswith(CARD_MARKER_OPEN) and line.endswith(CARD_MARKER_CLOSE):
            current = line[len(CARD_MARKER_OPEN) : -len(CARD_MARKER_CLOSE)]
            if current in cards:
                raise ValueError(f"duplicate card {current!r}")
            cards[current] = []
        elif current is not None:
            cards[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in cards.items()}


def pinned_areas(
    *,
    focus_domains: list[str] | tuple[str, ...],
    pending_kind: str | None,
    open_question: Any,
    tasks: Any,
) -> set[str]:
    """Cards the conversation's own state requires, whatever retrieval finds."""
    pinned = set(focus_domains or ())
    if open_question or pending_kind or tasks:
        pinned.add("open_question")
    if pending_kind in ESCALATION_PENDING_KINDS:
        pinned.add("escalation")
    return pinned


def select_cards(*, retrieved_areas: set[str], pinned: set[str], available: set[str]) -> list[str]:
    wanted = set(retrieved_areas) | set(pinned)
    for name in list(wanted):
        wanted.update(COMPANIONS.get(name, ()))
    wanted &= set(available)
    ordered = [name for name in CARD_ORDER if name in wanted]
    return ordered + sorted(wanted - set(CARD_ORDER))


def compact(value: Any) -> Any:
    """`value` without the keys whose value is null, false, "", [] or {} (nested too)."""
    if isinstance(value, dict):
        kept = {key: compact(item) for key, item in value.items()}
        return {key: item for key, item in kept.items() if not _empty(item)}
    if isinstance(value, list):
        return [compact(item) for item in value]
    return value


def _empty(value: Any) -> bool:
    return value is None or value is False or value == "" or value == [] or value == {}


def build_system_prompt(
    core: str, cards: dict[str, str], selected: list[str], examples: list[Example]
) -> str:
    parts = [core.rstrip("\n")]
    parts.extend(cards[name].strip() for name in selected)
    if examples:
        shots = []
        for example in examples:
            lines = [f"Message: {example.message}"]
            if example.context:
                lines.append(f"Context: {example.context}")
            lines.append("Output: " + json.dumps(compact(example.expected_output), sort_keys=True, ensure_ascii=False))
            shots.append("\n".join(lines))
        parts.append(EXAMPLES_HEADING + "\n" + "\n\n".join(shots))
    return "\n\n".join(parts) + "\n"


def needed_areas(parsed: dict[str, Any]) -> set[str]:
    """The areas the parser's own emission says it needed."""
    needed: set[str] = set()
    hint = parsed.get("domain_hint")
    if isinstance(hint, str) and hint:
        needed.add(hint)
    for ask in parsed.get("asks") or []:
        domain = ask.get("domain") if isinstance(ask, dict) else None
        if isinstance(domain, str) and domain:
            needed.add(domain)
    if parsed.get("order_status") in SALES_ORDER_STATUSES:
        needed.add("sales")
    return needed


def missing(parsed: dict[str, Any], loaded: set[str], available: set[str] | None = None) -> set[str]:
    """Needed areas that have a card in `available` (default: every known card) and are not loaded."""
    return needed_areas(parsed) & set(CARD_ORDER if available is None else available) - set(loaded)


class NoEmbeddingKey(Exception):
    """The assistant configuration holds no OpenAI key to embed with."""


def embed_text(access: EmbeddingAccess, text: str) -> list[float]:
    """The text's embedding through the provider class and the configured key.

    Cassette-aware (`OpenAIProvider.embed`). Called with no session open. Note the provider
    sends its own embedding model name, which equals `embedding_model_name`'s default.
    """
    from app.services.llm_provider import OpenAIProvider

    if not access.api_key:
        raise NoEmbeddingKey("no embedding key")
    vector = OpenAIProvider(access.api_key, access.model).embed(
        text, timeout=EMBED_TIMEOUT_SECONDS, max_retries=0
    )
    if not vector:
        raise ValueError("embedding provider returned an empty vector")
    return list(vector)


def nearest_examples(db: Session, vector: list[float], k: int = 5) -> list[Example]:
    """The `k` enabled examples nearest the vector by cosine distance, nearest first."""
    literal = "[" + ",".join(str(float(v)) for v in vector) + "]"
    rows = db.execute(
        sql_text(
            "SELECT id, area, message, context, expected_output FROM chatbot_examples "
            "WHERE enabled AND embedding IS NOT NULL "
            "ORDER BY embedding <=> CAST(:vec AS vector), created_at, id LIMIT :k"
        ),
        {"vec": literal, "k": k},
    ).all()
    return [
        Example(
            area=row.area,
            message=row.message,
            context=row.context,
            expected_output=row.expected_output,
            id=str(row.id),
        )
        for row in rows
    ]
