"""Shared builders for the S2 `app/services/chatbot/turn/` red tests (AC-1520 to
AC-1528, PLAN-chatbot-turn-rearch.md).

Not itself a test file (no `test_` prefix - pytest never collects it).

`verdict(**overrides)` is a PLAIN DICT, not a pydantic class (captain ruling, 16 Sep
2026, item 6: Verdict is a plain dict). Every key defaults to the parser schema's own
"said nothing" value.

`PENDING_KINDS` (captain ruling, item 1, 16 Sep 2026): the LANE's eight names, not
main's - `product_pick`, `customer_pick`, `tier_pick`, `team_pick`, `company_pick`,
`member_offer`, `outstanding_scope`, `outstanding_detail`, plus `kind_pick` as the ninth
(reconciliation-only) kind, not counted in the eight. `ROSTER_KINDS` (stay alive after
their own pick, with `answered_positions`) = `product_pick`, `customer_pick`, `tier_pick`
(+ `kind_pick`, outside the eight); the other five clear when answered.

SUPERSEDED for `tier_pick` (hand pass 9, D3, owner ruling 20 Sep 2026 - the same ruling
`app/services/chatbot/turn/pending.py::ROSTER_KINDS` already carries): the 16 Sep ruling
above clocked `tier_pick` as an OFFER kind. Main's fresh-typed picker gate
(`tail/compile_state.py`) draws no kind-based distinction at all - a `require_specific`
roster stays answerable across as many picks as the customer makes, and the tier ask is
the identical shape (a numbered list, one axis), so treating it as a one-off offer that
clears on its own pick was the gap (live turn 25dcef1d-decc-407b-a6eb-08d8112ee814).
`ROSTER_KINDS` below now matches the real code.
"""
from __future__ import annotations

from datetime import date
from typing import Any

PENDING_KINDS: tuple[str, ...] = (
    "product_pick",
    "customer_pick",
    "tier_pick",
    "team_pick",
    "company_pick",
    "member_offer",
    "outstanding_scope",
    "outstanding_detail",
)

# Within PENDING_KINDS, which stay alive (with `answered_positions`) after their own
# pick vs. clear once answered (captain ruling, item 1, 16 Sep 2026; `tier_pick` added
# by the hand pass 9 D3 supersession, see the module docstring).
ROSTER_KINDS: frozenset[str] = frozenset({"product_pick", "customer_pick", "tier_pick"})
OFFER_KINDS: frozenset[str] = frozenset(PENDING_KINDS) - ROSTER_KINDS

# `reset_on_topic`'s survivors (PLAN AC-1525 / the ported `test_focus_rules.py` on
# `feat/chatbot-focus`, `TestResetOnTopic.test_topic_reset_clears_the_question_and_
# keeps_the_asker`): a topic reset clears every focus axis EXCEPT these two.
RESET_KEEPS: frozenset[str] = frozenset({"tier", "brands"})


def entity(
    raw: str,
    hint: str | None = "product",
    *,
    canonical_code: str | None = None,
    current_message: bool = True,
    confident: bool = True,
    hint_confident: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "raw": raw,
        "hint": hint,
        "canonical_code": canonical_code if canonical_code is not None else raw,
        "current_message": current_message,
        "confident": confident,
        "hint_confident": hint_confident,
        **extra,
    }


def verdict(**overrides: Any) -> dict[str, Any]:
    """The parser's parsed output, every key defaulted to its "said nothing" value."""
    base: dict[str, Any] = {
        "message_type": "business_query",
        "intent_hint": None,
        "domain_hint": None,
        "scope_intent": None,
        "is_affirmative": None,
        "user_goal": None,
        "access_levels": [],
        "broaden_axis": None,
        "date_mode": None,
        "date_filter_start": None,
        "date_filter_end": None,
        "match_mode": None,
        "demand_qty": None,
        "entities": [],
        "entity_op": None,
        "scope_exclusive": None,
        "requested_attributes": [],
        "contains_flyer": None,
        "reference_positions": [],
        "reference_target": None,
        "person_mention": None,
        "is_active": None,
        "document": [],
        "status": None,
        "group_by": None,
        "top_n": None,
        "correction": None,
        "routing": {"suggested_team": None, "suggested_agent": None},
        "escalation": {
            "is_escalation_confirmation": None,
            "escalation_declined": None,
            "company_pick": None,
        },
        "anaphora": {"backward_reference": False},
        "answers_open_question": {"resolved": False, "picks": [], "answer": None},
        # v3 emits this (captain ruling, item 2, 16 Sep 2026).
        "topic_reset": False,
        # Two-domain messages (captain ruling, item 3, 16 Sep 2026): message-order list
        # of {domain, intent}; `focus.domains` = [a["domain"] for a in asks] when
        # non-empty, else the single `domain_hint`.
        "asks": [],
        # The parser's OWN "this message continues the previous set" signal (16 Sep
        # 2026 ruling): a "more"/"next"/"lagi" paging turn is the parser saying so,
        # never the engine pattern-matching `user_goal`'s free text for it - that
        # word-match rule is being deleted. Defaults to False; a paging test sets it
        # True explicitly (`test_rearch_s3_attribute_first.py::TestPagingByFive`).
        "continuation": False,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Policy fixture (AC-1526): the six (domain, kind) triples the UAC names by name,
# not a DB read - "Policy built from rows: tests seed rows, never patch constants"
# (PLAN "Testing seams"), and the rows here are the in-memory stand-in.
# --------------------------------------------------------------------------- #

TIER_ORDER_FIXTURE: list[str] = ["dealer", "office", "end_user"]


def _domain_row(
    name: str,
    *,
    narrowing: dict[str, str],
    tools: tuple[str, ...] = (),
    reveal_key: str | None = None,
    intents: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "name": name,
        "label": name,
        "intents": list(intents),
        "tools": list(tools),
        "primary_tool": tools[0] if tools else None,
        "escalation_team_code": None,
        "switch_words": [],
        "takes_date_filter": False,
        "reveal_key": reveal_key,
        "supported": True,
        "narrowing": narrowing,
        "ladder": [],
        "sort_order": 0,
    }


POLICY_DOMAIN_ROWS: list[dict[str, Any]] = [
    _domain_row("inventory", narrowing={"product": "list_all"}),
    _domain_row("incoming", narrowing={"product": "narrow_to_code"}),
    _domain_row("purchase_cost", narrowing={"product": "narrow_to_code"}),
    _domain_row("order", narrowing={"customer": "must_narrow_one"}),
    _domain_row("product_attachment", narrowing={"attachment_type": "narrow_by_type"}),
    _domain_row("promotion", narrowing={"tier": "narrow_by_tier"}),
    # AC-1861 (PLAN-chatbot-roster-label-vs-ask-24sep.md): the real `master_products`
    # domain row (`turn/policy_rows.py`) narrows on nothing at all.
    _domain_row("master_products", narrowing={}),
    # S2 (reviewer pass 1 on PR #1185): the real `ideate` row (`policy_rows.py:273`)
    # carries `intents=["submit_idea"]`. Without it here, `policy.domain("ideate")` was
    # `None` in every pure test, so `_continues_open_draft`'s "ideate's own intents
    # pass" branch (`turn/apply.py`) never ran against a real intents set.
    _domain_row("ideate", narrowing={}, intents=("submit_idea",)),
]


def _kind_row(kind: str, *, default_narrowing: str, family_grouping: str | None = None) -> dict[str, Any]:
    row = {
        "kind": kind,
        "resolver_source": kind,
        "did_you_mean": True,
        "default_narrowing": default_narrowing,
        "family_grouping": family_grouping,
        "base_property_words": {} if kind != "product" else {"price": "list_price"},
    }
    return row


POLICY_KIND_ROWS: list[dict[str, Any]] = [
    _kind_row("product", default_narrowing="list_all", family_grouping="base_code"),
    _kind_row("customer", default_narrowing="must_narrow_one", family_grouping="ledger_family"),
    _kind_row("attachment_type", default_narrowing="narrow_by_type"),
    _kind_row("promotion", default_narrowing="optional_filter"),
]


def build_policy():
    """`Policy.from_rows(...)` over the fixture rows above. Imports lazily so importing
    this helper module never itself raises `ModuleNotFoundError` before a test gets to
    assert on it."""
    from app.services.chatbot.turn.policy import Policy

    return Policy.from_rows(
        domains=POLICY_DOMAIN_ROWS, kinds=POLICY_KIND_ROWS, tier_order=TIER_ORDER_FIXTURE
    )


#: A delivery date inside `do_window()`, for a seed whose orders must match a dated DO ask.
DO_WINDOW_DATE = date(2026, 9, 15)


def do_window() -> dict[str, str]:
    """A 30-day window (Sep 2026) for a DO list ask the test is not about.

    Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
    A seed that needs its orders listed stamps `actual_delivery_date=DO_WINDOW_DATE`.
    """
    return {"date_filter_start": "2026-09-01", "date_filter_end": "2026-09-30"}
