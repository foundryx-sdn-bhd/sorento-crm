"""ACCESS-MODEL owner additions (2 Oct): multi-role (AC-AM-22), tier on the role (AC-AM-23),
per-contact stamps (AC-AM-24), region scope (AC-AM-25).

Red-first. Seams chosen:
  * tier -> staff: `turn/state.py::is_staff_profile` (state.py:~211) reads `Profile.tiers`
    (tuple, sorted) filled by `turn_runtime.load_profile` (turn_runtime.py:~471) from
    `EffectiveAccess.tiers`; `Profile.tier` stays None.
  * promotion default: `turn/narrow.py::decide` (narrow.py:~411, 426) with `Profile(tiers=...)`;
    `NarrowOutcome.filter_value` is the LIST of tiers when several are held.
  * incoming stamp switch: `lanes/business/pickers.py::annotate_incoming(..., show_stamp=True)`
    (pickers.py:~82); the caller that has `ctx.access` is `turn_runtime.resolve_kinds`
    (turn_runtime.py:~2099-2113), which passes `show_stamp = "stamp.incoming" in attributes`.
  * attachment stamp switch: `lanes/business/answer.py::build_suggest_offer(..., show_stamp=True)`
    (answer.py:4274; the has/no suffixes are rendered at :4697-4701, :4764-4767, :4884-4903
    behind `dym_ok`); False renders as if `dym_annotate` were None.
"""
from __future__ import annotations

import dataclasses
import inspect

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.chatbot._access_seed import (
    SPACE_ID,
    give_role,
    make_contact,
    make_workspace,
    override,
    rid,
)
from tests.chatbot.test_chatbot_access_migration import (
    DEALER_HAS_STAMPS,
    K_STAMP_ATTACHMENT,
    K_STAMP_INCOMING,
    SALES_OFFICE_DOMAINS,
    _registry,
    _role,
    _role_fields,
)


def _seeded(session_factory):
    from app.services.chatbot.access_seed import seed_default_roles

    db = session_factory()
    wid = make_workspace(db)
    _registry(db)
    seed_default_roles(db)
    db.commit()
    return db, wid


def _ea(db, rio):
    from app.services.chatbot.access_tree import effective_access

    return effective_access(db, contact_respond_id=rio, space_id=SPACE_ID)


def _holder(db, wid, *codes):
    pk, rio = make_contact(db, workspace_id=wid)
    for code in codes:
        give_role(db, pk, _role(db, code).id)
    return pk, rio


class TestMultiRole:
    """AC-AM-22."""

    def test_dealer_plus_sales_office_is_the_union(self, session_factory):
        db, wid = _seeded(session_factory)
        _p1, r1 = _holder(db, wid, "dealer")
        _p2, r2 = _holder(db, wid, "sales_office")
        _p3, both = _holder(db, wid, "dealer", "sales_office")
        a, b, c = _ea(db, r1), _ea(db, r2), _ea(db, both)
        assert c.domains == a.domains | b.domains == frozenset(SALES_OFFICE_DOMAINS)
        assert set(c.attributes) == set(a.attributes) | set(b.attributes)
        assert c.roles == ("dealer", "sales_office")

    def test_sees_all_customers_if_any_role_has_it(self, session_factory):
        db, wid = _seeded(session_factory)
        _p, rio = _holder(db, wid, "dealer")
        assert _ea(db, rio).sees_all_customers is False
        _p, both = _holder(db, wid, "dealer", "sales_office")
        assert _ea(db, both).sees_all_customers is True

    def test_a_contact_remove_override_beats_both_roles(self, session_factory):
        db, wid = _seeded(session_factory)
        pk, rio = _holder(db, wid, "dealer", "sales_office")
        override(db, pk, "order", granted=False)
        override(db, pk, "inventory", field_key="inventory.sellable", granted=False)
        access = _ea(db, rio)
        assert "order" not in access.domains
        assert "inventory.sellable" not in access.attributes
        assert "inventory" in access.domains


class TestTierOnTheRole:
    """AC-AM-23."""

    def test_presets_carry_the_audience_tier(self, session_factory):
        db, _wid = _seeded(session_factory)
        tiers = {r: _role(db, r).audience_tier for r in ("dealer", "sales_office", "purchasing", "warehouse", "management")}
        assert tiers == {
            "dealer": "dealer", "sales_office": "office", "purchasing": "office",
            "warehouse": "office", "management": "office",
        }

    def test_effective_tiers_are_the_set_over_held_roles(self, session_factory):
        db, wid = _seeded(session_factory)
        _p, only = _holder(db, wid, "dealer")
        _p, both = _holder(db, wid, "dealer", "sales_office")
        _p, none = _holder(db, wid)
        assert _ea(db, only).tiers == frozenset({"dealer"})
        assert _ea(db, both).tiers == frozenset({"dealer", "office"})
        assert _ea(db, none).tiers == frozenset()

    def test_the_old_chatbot_profile_tier_is_not_read(self, session_factory):
        """A contact whose profile JSON says office but holds only Dealer is not staff."""
        import json

        from app.services.chatbot import turn_runtime
        from app.services.chatbot.turn.state import is_staff_profile

        db, wid = _seeded(session_factory)
        pk, rio = _holder(db, wid, "dealer")
        db.execute(
            text("UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) WHERE id = :i"),
            {"p": json.dumps({"tier": "office"}), "i": pk},
        )
        db.commit()
        profile, _ = turn_runtime.load_profile(db, rio, space_id=SPACE_ID)
        assert profile.tier is None
        assert profile.tiers == ("dealer",)
        assert is_staff_profile(profile) is False

    def test_dealer_plus_office_roles_make_staff(self, session_factory):
        from app.services.chatbot import turn_runtime
        from app.services.chatbot.turn.state import is_staff_profile

        db, wid = _seeded(session_factory)
        _pk, rio = _holder(db, wid, "dealer", "sales_office")
        profile, _ = turn_runtime.load_profile(db, rio, space_id=SPACE_ID)
        assert profile.tiers == ("dealer", "office")
        assert is_staff_profile(profile) is True

    def test_promotion_default_receives_every_held_tier(self):
        from app.services.chatbot.turn.narrow import decide
        from app.services.chatbot.turn.state import Focus, Profile

        out = decide(
            kind="tier", policy_value="narrow_by_tier", focus=Focus(),
            profile=Profile(tiers=("dealer", "office")),
        )
        assert out.ask_kind is None
        assert out.filter_value == ["dealer", "office"]
        single = decide(
            kind="tier", policy_value="narrow_by_tier", focus=Focus(), profile=Profile(tiers=("dealer",))
        )
        assert single.filter_value in ("dealer", ["dealer"])
        none = decide(kind="tier", policy_value="narrow_by_tier", focus=Focus(), profile=Profile())
        assert none.ask_kind == "tier_pick", "no tier held: the customer is still asked"


class TestStampFieldsInTheTree:
    """AC-AM-24: the two stamp fields exist and are ticked on every preset."""

    def test_stamp_fields_are_registry_rows_of_kind_field(self, session_factory):
        from app.models.chatbot_access import ChatbotDomainField

        db, _wid = _seeded(session_factory)
        rows = {
            r.key: (r.domain_name, r.kind)
            for r in db.query(ChatbotDomainField).filter(ChatbotDomainField.key.like("stamp.%"))
        }
        assert rows == {
            K_STAMP_INCOMING: ("incoming", "field"),
            K_STAMP_ATTACHMENT: ("product_attachment", "field"),
        }

    @pytest.mark.parametrize("role", ["sales_office", "purchasing", "warehouse", "management"])
    def test_every_staff_preset_ticks_both_stamps(self, session_factory, role):
        db, _wid = _seeded(session_factory)
        assert {K_STAMP_INCOMING, K_STAMP_ATTACHMENT} <= _role_fields(db, role)

    def test_dealer_ticks_the_stamps_per_the_one_named_constant(self, session_factory):
        db, _wid = _seeded(session_factory)
        ticked = {K_STAMP_INCOMING, K_STAMP_ATTACHMENT} <= _role_fields(db, "dealer")
        none_ticked = not ({K_STAMP_INCOMING, K_STAMP_ATTACHMENT} & _role_fields(db, "dealer"))
        assert ticked if DEALER_HAS_STAMPS else none_ticked

    def test_a_field_override_remove_takes_the_stamp_off_one_contact(self, session_factory):
        db, wid = _seeded(session_factory)
        pk, rio = _holder(db, wid, "sales_office")
        assert K_STAMP_INCOMING in _ea(db, rio).attributes
        override(db, pk, "incoming", field_key=K_STAMP_INCOMING, granted=False)
        assert K_STAMP_INCOMING not in _ea(db, rio).attributes


_GATE = {"gate_clarification": "Which product?\n1. AAA-1\n2. BBB-2", "compatible_entities": [{"code": "AAA-1"}, {"code": "BBB-2"}]}
_PROBE = {"answers": [{"title": "AAA-1"}]}


class TestIncomingStampSwitch:
    def test_show_stamp_defaults_true_and_keeps_todays_output(self):
        from app.services.chatbot.lanes.business import pickers

        assert inspect.signature(pickers.annotate_incoming).parameters["show_stamp"].default is True
        out = pickers.annotate_incoming(dict(_GATE), probe=_PROBE)
        assert "1. AAA-1 - has incoming" in out["escalate_message"]
        assert "2. BBB-2 - no incoming" in out["escalate_message"]

    def test_show_stamp_false_prints_bare_lines_and_drops_the_none_sentence(self):
        from app.services.chatbot.lanes.business import pickers

        out = pickers.annotate_incoming(dict(_GATE), probe=_PROBE, show_stamp=False)
        assert out["escalate_message"] == "Which product?\n1. AAA-1\n2. BBB-2"
        empty = pickers.annotate_incoming(dict(_GATE), probe={"answers": []}, show_stamp=False)
        assert "None of these have incoming stock" not in empty["escalate_message"]
        assert " - no incoming" not in empty["escalate_message"]

    def test_incoming_by_code_data_is_unchanged_when_hidden(self):
        from app.services.chatbot.lanes.business import pickers

        shown = pickers.annotate_incoming(dict(_GATE), probe=_PROBE)
        hidden = pickers.annotate_incoming(dict(_GATE), probe=_PROBE, show_stamp=False)
        assert hidden["incoming_by_code"] == shown["incoming_by_code"] == {"AAA-1": True, "BBB-2": False}

    @pytest.mark.parametrize(
        "attributes,expected",
        [([], False), (["stamp.incoming"], True)],
    )
    def test_resolve_kinds_passes_show_stamp_from_ctx_access(self, monkeypatch, attributes, expected):
        """The caller with `ctx.access`: a contact lacking `stamp.incoming` gets show_stamp False."""
        from app.services.chatbot import turn_runtime
        from app.services.chatbot.lanes.business import pickers, resolve_gate

        seen: list[dict] = []

        def fake_run(ctx, entry, item, **kwargs):
            return {
                "resolved": {"resolutions": []},
                "gate": {"compatible_entities": [{"code": "AAA-1", "entity_type": "product", "raw": "AAA-1"}]},
                "aggregate": None,
                "tier_gate": None,
            }

        def fake_annotate(gate, *, probe, **kwargs):
            seen.append(kwargs)
            return gate

        monkeypatch.setattr(resolve_gate, "run", fake_run)
        monkeypatch.setattr(resolve_gate, "probe_incoming", lambda *a, **k: {"answers": []})
        monkeypatch.setattr(pickers, "annotate_incoming", fake_annotate)
        ctx = {
            "parse": {"output": {"entities": [{"raw": "AAA-1", "hint": "product", "canonical_code": "AAA-1"}]}},
            "access": {"attributes": attributes},
        }
        turn_runtime.resolve_kinds(
            object(), ctx=ctx, branch_kind="check_stock", space_id=None, dry_run=True, stamp_incoming=True
        )
        assert seen, "annotate_incoming must have been reached"
        assert seen[-1].get("show_stamp") is expected


class TestAttachmentStampSwitch:
    _ITEM = {"escalate_message": "Please choose:\n1. SRTWC286-SH\n2. SRTWC286-SH-200", "is_clarification": False}
    _ANN = {
        "dym_probe_meta": {"ok": True, "probed": ["srtwc286-sh", "srtwc286-sh-200"], "noun": "Product Photos"},
        "dym_available_codes": ["srtwc286-sh"],
    }

    def _build(self, **kw):
        from app.services.chatbot.lanes.business.answer import build_suggest_offer

        return build_suggest_offer(
            dict(self._ITEM), parser={}, resolved={}, gate={"require_specific": True},
            dym_annotate=self._ANN, **kw,
        )

    def test_show_stamp_defaults_true_and_keeps_todays_output(self):
        from app.services.chatbot.lanes.business.answer import build_suggest_offer

        assert inspect.signature(build_suggest_offer).parameters["show_stamp"].default is True
        out = self._build()
        assert out["escalate_message"] == (
            "Please choose:\n1. SRTWC286-SH - has Product Photos\n2. SRTWC286-SH-200 - no Product Photos"
        )

    def test_show_stamp_false_prints_the_picker_lines_bare(self):
        out = self._build(show_stamp=False)
        assert out["escalate_message"] == "Please choose:\n1. SRTWC286-SH\n2. SRTWC286-SH-200"
