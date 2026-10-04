"""Phase 2 RED tests - "account N" narrows a LINKED contact to the ledger at that level.

`documentation/plans/chatbot/PLAN-account-ledger-2oct.md` (Design 5, 7) and
`account-ledger-acceptance-criteria.md` AC-5, AC-6, AC-7 (Q3, Q6). Written BEFORE the wiring.

Same harness as `test_customer_scope_lane.py`: one real `engine.run_turn`, parser / access /
resolver / MCP faked, so the verdict dict passed as `qf` is what the engine reads. Entities
carry the new key `account` (int or null). Levels are the SETTING (`customers.account_level`),
set here by raw SQL, never read off the names.

Pinned names the coder must match:
  * `ContactCustomerScope.levels`, `match_words(words, accounts)`, `refusal_line_for(scope, ids)`
    (`tests/test_contact_customer_scope_account.py`).
  * `business_services.customer_scope(...)` returns a `"levels"` dict (customer id -> level).
  * The refusal travels in `customer_scope["refusal"]` as today, so the turn's reply text is what
    the tests read; refusals are asserted on `reply`, never on a private function.

Data (one contact, link order): the six HANLIM TRADING SDN BHD ledgers (levels None, None, 1,
2, 3, 4) then SOON HENG HARDWARE CO.SDN.BHD. [A/C I] (1). The resolver is deliberately
wrong-target so an engine that still asks it about a linked customer's word fails visibly.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.services.chatbot.lanes.business import services as business_services
from tests.chatbot.test_customer_scope_lane import (
    ORDER_UUID,  # noqa: F401
    REPORT,
    _ask,
    _calls,
    _ent,
    _give_access_type,
    _link_customers,
    _turn,
    refusal,
)
from tests.chatbot.test_outstanding_lane import (
    CONTACT_ID,
    HANLIM_UUID_1,
    REPORT_HIT,
    _seed_contact,
)

H_BARE = "HANLIM TRADING SDN BHD"
H_CER = "HANLIM TRADING SDN BHD (CERAMIC & ELLECI)"
H_1 = "HANLIM TRADING SDN BHD [A/C I]"
H_2 = "HANLIM TRADING SDN BHD [A/C II]"
H_3 = "HANLIM TRADING SDN BHD [A/C III]"
H_4 = "HANLIM TRADING SDN BHD [A/C IV]"
S_1 = "SOON HENG HARDWARE CO.SDN.BHD. [A/C I]"
#: The group names a refusal prints (DO-ASK-SIMPLIFY, owner rule 2 Oct 2026: a customer
#: company is named by its group name only, never per ledger).
H_GROUP = "HANLIM TRADING SDN BHD"
S_GROUP = "SOON HENG HARDWARE CO.SDN.BHD."
ALL = [H_BARE, H_CER, H_1, H_2, H_3, H_4, S_1]
LEVELS = [None, None, 1, 2, 3, 4, 1]


def _set_levels(session_factory, ids: list[str], levels: list[int | None]) -> None:
    db = session_factory()
    for cid, level in zip(ids, levels):
        if level is None:
            continue  # the column default; keeps the no-account regression tests off the new column
        db.execute(text("UPDATE customers SET account_level = :n WHERE id = :i"), {"n": level, "i": cid})
    db.commit()


def _world(session_factory, names: list[str] | None = None, levels: list[int | None] | None = None) -> dict[str, str]:
    names = names or ALL
    levels = levels if levels is not None else LEVELS
    _seed_contact(session_factory, variables={})
    ids = _link_customers(session_factory, *names)
    _set_levels(session_factory, ids, levels)
    return dict(zip(names, ids))


def _acct(raw: str | None, account: int | None, **over: Any) -> dict[str, Any]:
    base = _ent(raw or "x")
    base["raw"] = raw
    base["account"] = account
    base.update(over)
    return base


def _run(session_factory, monkeypatch, entities, body, **verdict):
    return _turn(session_factory, monkeypatch, _ask(entities, **verdict), body, mcp_response=REPORT_HIT)


class TestNamedAccountNarrowsTheLinks:
    def test_hanlim_account_2_answers_the_level_two_ledger_only(self, session_factory, monkeypatch) -> None:
        """AC-6: raw "Hanlim", account 2 -> exactly the [A/C II] ledger."""
        ids = _world(session_factory)
        reply, captured = _run(session_factory, monkeypatch, [_acct("Hanlim", 2)], "hanlim account 2 outstanding")
        (args,) = _calls(captured, REPORT)
        assert args["customer_ids"] == [ids[H_2]], args
        assert "under your account" not in reply

    def test_level_comes_from_the_setting_not_the_name(self, session_factory, monkeypatch) -> None:
        """AC-3/AC-11: the office re-levelled the ledgers; the name markers no longer matter."""
        ids = _world(session_factory, levels=[None, None, 2, 1, 3, 4, 1])
        _reply, captured = _run(session_factory, monkeypatch, [_acct("Hanlim", 2)], "hanlim account 2")
        (args,) = _calls(captured, REPORT)
        assert args["customer_ids"] == [ids[H_1]], args

    def test_soon_heng_account_1_answers_its_level_one_ledger(self, session_factory, monkeypatch) -> None:
        """AC-7 (first half)."""
        ids = _world(session_factory)
        _reply, captured = _run(session_factory, monkeypatch, [_acct("Soon Heng", 1)], "soon heng account 1")
        (args,) = _calls(captured, REPORT)
        assert args["customer_ids"] == [ids[S_1]], args

    def test_account_one_picks_by_setting_when_the_family_has_several_links(self, session_factory, monkeypatch) -> None:
        """Discriminating form of AC-7: two Soon Heng links, levels swapped against their names."""
        second = "SOON HENG HARDWARE CO.SDN.BHD. [A/C II]"
        ids = _world(session_factory, names=[S_1, second], levels=[2, 1])
        _reply, captured = _run(session_factory, monkeypatch, [_acct("Soon Heng", 1)], "soon heng account 1")
        (args,) = _calls(captured, REPORT)
        assert args["customer_ids"] == [ids[second]], args

    def test_soon_heng_account_2_is_refused_naming_only_that_linked_ledger(self, session_factory, monkeypatch) -> None:
        """AC-7 (second half), Q3: no Hanlim name, nothing fetched."""
        _world(session_factory)
        reply, captured = _run(session_factory, monkeypatch, [_acct("Soon Heng", 2)], "soon heng account 2")
        assert reply.strip() == refusal(S_GROUP), reply
        assert "HANLIM" not in reply
        assert captured == []

    def test_a_level_the_family_lacks_names_that_familys_links_only(self, session_factory, monkeypatch) -> None:
        """Hanlim has no level 5 among the links: the refusal names the Hanlim group (group names only, owner rule 2 Oct 2026)."""
        _world(session_factory)
        reply, captured = _run(session_factory, monkeypatch, [_acct("Hanlim", 5)], "hanlim account 5")
        assert reply.strip() == refusal(H_GROUP), reply
        assert "SOON HENG" not in reply
        assert captured == []

    def test_two_named_customers_each_with_its_own_account(self, session_factory, monkeypatch) -> None:
        ids = _world(session_factory)
        _reply, captured = _run(
            session_factory, monkeypatch, [_acct("Hanlim", 2), _acct("Soon Heng", 1)], "hanlim a/c 2 and soon heng a/c 1",
        )
        (args,) = _calls(captured, REPORT)
        assert args["customer_ids"] == [ids[H_2], ids[S_1]], args


class TestNoAccountIsTodaysBehaviour:
    def test_a_word_without_account_matches_every_ledger_of_the_name(self, session_factory, monkeypatch) -> None:
        """AC-5: entity without the key, and with `account: null`, answer all six Hanlim links."""
        ids = _world(session_factory, levels=[None] * 7)
        hanlim = [ids[n] for n in (H_BARE, H_CER, H_1, H_2, H_3, H_4)]
        _reply, captured = _run(session_factory, monkeypatch, [_ent("Hanlim")], "outstanding for hanlim")
        (args,) = _calls(captured, REPORT)
        assert args["customer_ids"] == hanlim, args

    def test_account_null_is_the_same_as_the_key_absent(self, session_factory, monkeypatch) -> None:
        ids = _world(session_factory, levels=[None] * 7)
        hanlim = [ids[n] for n in (H_BARE, H_CER, H_1, H_2, H_3, H_4)]
        _reply, captured = _run(session_factory, monkeypatch, [_acct("Hanlim", None)], "outstanding for hanlim")
        (args,) = _calls(captured, REPORT)
        assert args["customer_ids"] == hanlim, args

    def test_foreign_word_without_account_is_still_refused_with_all_links(self, session_factory, monkeypatch) -> None:
        _world(session_factory, names=[H_1, S_1], levels=[None, None])
        reply, captured = _run(session_factory, monkeypatch, [_acct("Zzt Stranger", None)], "outstanding for stranger")
        assert reply.strip() == refusal(H_GROUP, S_GROUP), reply
        assert captured == []


class TestMyAccount:
    """Q6 (a): "my account 1" names no customer, "my" means the links, narrowed to level 1."""

    def test_my_account_1_runs_on_the_level_one_links(self, session_factory, monkeypatch) -> None:
        ids = _world(session_factory)
        _reply, captured = _run(
            session_factory, monkeypatch, [_acct(None, 1)], "my account 1 outstanding", self_reference=True,
        )
        (args,) = _calls(captured, REPORT)
        assert args["customer_ids"] == [ids[H_1], ids[S_1]], args

    def test_a_staff_contact_with_links_gets_the_same(self, session_factory, monkeypatch) -> None:
        """engine.py rule: `self_reference` means the links for staff too."""
        ids = _world(session_factory)
        _give_access_type(session_factory, "Sorento Office")
        _reply, captured = _run(
            session_factory, monkeypatch, [_acct(None, 1)], "my account 1 outstanding", self_reference=True,
        )
        (args,) = _calls(captured, REPORT)
        assert args["customer_ids"] == [ids[H_1], ids[S_1]], args

    def test_my_account_without_a_number_is_all_the_links_as_today(self, session_factory, monkeypatch) -> None:
        ids = _world(session_factory, levels=[None] * 7)
        _reply, captured = _run(session_factory, monkeypatch, [], "what's my outstanding", self_reference=True)
        (args,) = _calls(captured, REPORT)
        assert args["customer_ids"] == [ids[n] for n in ALL], args

    def test_my_account_1_for_links_only_at_level_two_is_refused_naming_its_links(
        self, session_factory, monkeypatch
    ) -> None:
        _world(session_factory, names=[H_2, "SOON HENG HARDWARE CO.SDN.BHD. [A/C II]"], levels=[2, 2])
        reply, captured = _run(
            session_factory, monkeypatch, [_acct(None, 1)], "my account 1 outstanding", self_reference=True,
        )
        assert reply.strip() == refusal(H_GROUP, S_GROUP), reply
        assert captured == []


class TestCustomerScopeCarriesLevels:
    def test_customer_scope_dict_has_levels(self, session_factory) -> None:
        ids = _world(session_factory)
        db = session_factory()
        scope = business_services.customer_scope(db, str(CONTACT_ID), "364817")
        assert scope is not None
        assert scope["levels"] == {ids[n]: lvl for n, lvl in zip(ALL, LEVELS)}
        assert [row[0] for row in scope["linked"]] == [ids[n] for n in ALL]
        assert all(len(row) == 3 for row in scope["linked"])
