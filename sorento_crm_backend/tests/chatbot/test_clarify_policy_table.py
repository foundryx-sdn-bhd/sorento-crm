"""CLARIFY-POLICY AC-110..AC-116: the policy is DATA. Table `chatbot_clarify_policy`
(model `ChatbotClarifyPolicy`, `app/models/chatbot_policy.py`), seed list `CLARIFY_POLICY_ROWS` and
`load_clarify_policy(db) -> dict[str, rule]` in `app/services/chatbot/clarify_policy.py`
(`rule.mode`, `.allowed_modes`, `.repeat_limit`), migration `clp_0001_clarify_policy`.

Blank-schema fixtures build the table with `create_all` and leave it EMPTY, so the seed fallback
is what most tests read. Engine tests write rows to the table first.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError

from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_do_ask_range_engine import _ask, dealer  # noqa: F401 - fixture
from tests.chatbot.test_engine import stub_access  # noqa: F401 - fixture

KINDS = [
    "promo_tier", "tier_pick", "promo_subject", "attachment_type", "attachment_kind", "cost_product",
    "do_period", "so_period", "ranking_fields", "ranking_drill", "top_selling", "low_stock", "idea_text",
    "outstanding_scope", "outstanding_customer", "product_pick", "customer_pick", "roster_pick",
    "kind_pick", "account_customer", "stock_qty", "stock_family", "qty_scope", "demand_qty",
    "did_you_mean", "generic_menu", "llm_clarify", "escalation_pick", "entities_only",
    "carry_combine", "carry_unclear",
]


def _cp():
    from app.services.chatbot import clarify_policy

    return clarify_policy


def _model():
    from app.models.chatbot_policy import ChatbotClarifyPolicy

    return ChatbotClarifyPolicy


def _write_all_rows(db, **overrides) -> None:
    """Every row of the table from the seed, with `overrides[kind] = {field: value}` applied."""
    model = _model()
    for kind, rule in _cp().load_clarify_policy(db).items():
        fields = {"mode": rule.mode, "repeat_limit": rule.repeat_limit, **overrides.get(kind, {})}
        db.add(model(ask_kind=kind, label=kind, allowed_modes=list(rule.allowed_modes), default_note="", **fields))
    db.commit()


# --- the seed (AC-110, AC-112) -------------------------------------------------------------- #


def test_ac110_the_seed_list_has_31_rows():
    assert len(_cp().CLARIFY_POLICY_ROWS) == 31


def test_ac110_an_empty_table_loads_the_31_seeded_kinds(session_factory):
    rules = _cp().load_clarify_policy(session_factory())
    assert sorted(rules) == sorted(KINDS)


def test_ac110_seed_modes_are_todays_except_the_ruled_ones(session_factory):
    rules = _cp().load_clarify_policy(session_factory())
    assert rules["generic_menu"].mode == "default" and rules["entities_only"].mode == "default"
    assert rules["carry_combine"].mode == "ask"
    # Round 3: carry_unclear seeds `carry` until the new parser version is promoted (the release
    # step flips it to `ask`).
    assert rules["carry_unclear"].mode == "carry"
    assert rules["product_pick"].mode == "collapse"
    for kind in KINDS:
        if kind not in ("generic_menu", "entities_only", "product_pick", "carry_unclear"):
            assert rules[kind].mode == "ask", kind
        assert rules[kind].repeat_limit == 2, kind  # AC-116 seed


def test_ac112_allowed_modes(session_factory):
    rules = _cp().load_clarify_policy(session_factory())
    assert set(rules["do_period"].allowed_modes) == {"ask", "default"}
    assert set(rules["so_period"].allowed_modes) == {"ask", "default"}
    assert set(rules["outstanding_scope"].allowed_modes) == {"ask", "default"}
    assert set(rules["carry_combine"].allowed_modes) == {"ask", "carry", "drop"}
    assert set(rules["carry_unclear"].allowed_modes) == {"ask", "carry", "drop"}
    assert set(rules["generic_menu"].allowed_modes) == {"ask", "default"}
    assert set(rules["entities_only"].allowed_modes) == {"ask", "default"}
    for kind in KINDS:
        if kind not in ("do_period", "so_period", "outstanding_scope", "carry_combine", "carry_unclear",
                        "generic_menu", "entities_only"):
            assert list(rules[kind].allowed_modes) == [rules[kind].mode], kind


# --- the table (AC-110, AC-111, AC-113) ----------------------------------------------------- #


def test_ac111_an_update_to_a_mode_outside_allowed_modes_fails(session_factory):
    db = session_factory()
    _write_all_rows(db)
    row = db.query(_model()).filter_by(ask_kind="do_period").one()
    row.mode = "carry"  # not in {ask, default}
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()


def test_ac111_an_update_to_an_allowed_mode_is_accepted(session_factory):
    db = session_factory()
    _write_all_rows(db)
    db.query(_model()).filter_by(ask_kind="do_period").one().mode = "default"
    db.commit()
    assert _cp().load_clarify_policy(session_factory())["do_period"].mode == "default"


def test_ac113_the_table_is_read_over_the_seed(session_factory):
    db = session_factory()
    _write_all_rows(db, do_period={"mode": "default"}, so_period={"repeat_limit": 5})
    rules = _cp().load_clarify_policy(session_factory())
    assert rules["do_period"].mode == "default" and rules["so_period"].repeat_limit == 5


def test_ac113_a_read_error_falls_back_to_the_seed_never_to_no_ask(session_factory):
    class Broken:
        def query(self, *a, **k):
            raise RuntimeError("table unreadable")

        execute = scalars = query

    rules = _cp().load_clarify_policy(Broken())
    assert rules["do_period"].mode == "ask" and len(rules) == 31


def test_ac110_the_migration_exists_and_is_short_enough():
    from pathlib import Path

    versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    text = "\n".join(p.read_text() for p in versions.glob("clp_0001*.py"))
    assert 'revision = "clp_0001_clarify_policy"' in text or "revision: str = \"clp_0001_clarify_policy\"" in text, text[:200]
    assert len("clp_0001_clarify_policy") <= 32


# --- engine level (AC-114, AC-115, AC-116) -------------------------------------------------- #


def test_ac114_do_period_default_behaves_like_the_skip_switch(dealer, session_factory):  # noqa: F811
    _write_all_rows(session_factory(), do_period={"mode": "default"})
    reply = dealer.say("delivery to zzt dealer", _ask())
    assert "Which period" not in reply, reply
    orders = [a for n, a in dealer.calls if n == "crm_order_management_orders_list"]
    assert orders, dealer.calls
    assert not orders[0].get("actual_delivery_date_from") and not orders[0].get("actual_delivery_date_to"), orders[0]
    assert orders[0].get("limit") == 20 or orders[0].get("limit") is None, orders[0]


def test_ac114_do_period_seed_ask_is_today(dealer):  # noqa: F811
    assert "Which period" in dealer.say("delivery to zzt dealer", _ask())


def test_ac115_outstanding_scope_default_asks_nothing_and_scopes_to_both(session_factory, monkeypatch):
    from tests.chatbot.test_outstanding_lane import (
        PRODUCT_CODE, PRODUCT_UUID, REPORT_HIT, _qf, _run_turn, _seed_contact,
    )

    _seed_contact(session_factory, variables={})
    _write_all_rows(session_factory(), outstanding_scope={"mode": "default"})
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=_qf(order_status="outstanding"), text_body="SRTWT7445 outstanding",
        msg_id="ZZT-clp-table-outstanding", attributes=["sales_orders.outstanding"],
        matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
        mcp_response=REPORT_HIT,
    )
    reply = (result.reply or {}).get("text") or ""
    assert "Outstanding for which document?" not in reply, reply
    reports = [a for n, a in captured if n == "crm_outstanding_report"]
    assert reports and reports[0].get("scope") in (None, "both"), captured


def test_ac116_repeat_limit_1_breaks_on_the_second_identical_ask(dealer, session_factory):  # noqa: F811
    _write_all_rows(session_factory(), do_period={"repeat_limit": 1})
    bare = verdict(domain_hint="order", intent_hint="check_order", entities=[])
    first = dealer.say("my deliveries", bare)
    second = dealer.say("my deliveries", bare)
    assert first.startswith("Which period"), first
    assert not second.startswith("Which period"), second
