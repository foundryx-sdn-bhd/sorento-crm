"""QUICK-REPLY-NAMES (owner rule, 6 Oct 2026): quick replies are fixed short options only.

Prod, 6 Oct 2026: the customer picker

    Which customer do you mean? Please choose:
    1. SAMPLE ONG BROTHERS (SRT)
    2. ONG BROTHERS TILING TRADING SDN BHD (SRT)
    3. ONG BROTHERS TILING TRADING SDN BHD (SAMPLE) (SRT)

went out with the three names as WhatsApp quick-reply buttons and Meta refused the
message with #131009 "Duplicate button title" (a title is cut at 20 characters; options
2 and 3 are one string after the cut). The customer received nothing.

Two layers, both pinned here:

* rule 1 at the producers - a set goes out only when EVERY title is a fixed short option
  (`turn/pending.py::quick_replies`, `lanes/business/answer.py`, `lanes/escalation.py`);
* the safety net at the seam every reply crosses (`quick_reply_guard.guard`, applied in
  `engine._reply_of`, `engine.run_tail` and `engine._seal_send`): at most 3 buttons, no
  title over 20 characters (one drops the whole set), no duplicates, one log line.
"""
from __future__ import annotations

import logging
import uuid as uuid_mod
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.chatbot import quick_reply_guard
from app.services.chatbot.lanes import business as business_lane
from app.services.chatbot.turn import pending as turn_pending
from app.services.chatbot.turn.compose import _lane_question
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_samantha_27sep_r5_do_list_carry import ORDERS_LIST, _order_ask
from tests.chatbot.test_samantha_27sep_r7_owner_replay import owner_chat  # noqa: F401 - fixture

#: The three names of the incident. Prod printed the third as "ONG BROTHERS TILING
#: TRADING SDN BHD (SAMPLE)"; the harness's ledger-family grouping (`lanes/business/
#: gate.py`, `_cust_base` drops every bracketed marker) folds that spelling into option
#: 2 and prints two lines, so the marker is a plain word here. The property the incident
#: turns on is kept: options 2 and 3 are one string once cut at 20 characters.
INCIDENT_NAMES = (
    "SAMPLE ONG BROTHERS",
    "ONG BROTHERS TILING TRADING SDN BHD",
    "ONG BROTHERS TILING TRADING SAMPLE SDN BHD",
)
INCIDENT_LABELS = tuple(f"{name} (SRT)" for name in INCIDENT_NAMES)


# --------------------------------------------------------------------------- #
# The guard: the net every outgoing set crosses
# --------------------------------------------------------------------------- #


class TestGuard:
    def test_a_set_it_need_not_change_comes_back_byte_identical(self) -> None:
        assert quick_reply_guard.guard("Mocha, Sorento") == "Mocha, Sorento"
        assert quick_reply_guard.guard("Yes escalate,No it's okay") == "Yes escalate,No it's okay"

    @pytest.mark.parametrize("empty", [None, "", "  ", ",", 42, ["a", "b"]])
    def test_nothing_in_is_null_out_never_an_empty_string_or_a_list(self, empty) -> None:
        assert quick_reply_guard.guard(empty) is None

    def test_more_than_three_titles_keep_the_first_three(self) -> None:
        """The did-you-mean numbered set used to be "1,2,3,Yes escalate,No it's okay":
        WhatsApp shows three buttons, so the numbers stay and the Yes / No pair goes
        (the text already offers the escalation in a sentence a typed yes answers)."""
        assert quick_reply_guard.guard("1,2,3,Yes escalate,No it's okay") == "1, 2, 3"

    def test_the_incident_names_send_no_buttons_at_all(self) -> None:
        """One title over 20 characters drops the WHOLE set: a partial set would leave
        the customer guessing which printed option is the tappable one."""
        assert quick_reply_guard.guard(", ".join(INCIDENT_LABELS)) is None

    def test_one_over_long_title_drops_the_set_even_beside_short_ones(self) -> None:
        assert quick_reply_guard.guard("Yes, Project Customer Service") is None
        # Exactly 20 is allowed; 21 is not.
        assert quick_reply_guard.guard("a" * 20) == "a" * 20
        assert quick_reply_guard.guard("a" * 21) is None

    def test_duplicates_are_dropped_first_occurrence_kept(self) -> None:
        """Meta's own check is what refused the message, and it compares the titles as
        the transport sends them - so a repeat, in any case, never leaves here."""
        assert quick_reply_guard.guard("Yes, No, yes") == "Yes, No"
        assert quick_reply_guard.guard("1, 1, 2, 2, 3, 3, 4") == "1, 2, 3"

    def test_titles_are_trimmed_before_they_are_measured(self) -> None:
        assert quick_reply_guard.guard("  Yes ,  No  ") == "  Yes ,  No  "
        assert quick_reply_guard.guard(f"  {'a' * 20}  ") == f"  {'a' * 20}  "
        assert quick_reply_guard.guard(f"  {'a' * 21}  ") is None

    def test_it_logs_once_when_it_drops_something_and_not_otherwise(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="app.services.chatbot.quick_reply_guard"):
            quick_reply_guard.guard("Mocha, Sorento", where="unit")
            assert caplog.records == []
            quick_reply_guard.guard(", ".join(INCIDENT_LABELS), where="unit")
            assert len(caplog.records) == 1
            assert "unit" in caplog.records[0].getMessage()
            caplog.clear()
            quick_reply_guard.guard("1,2,3,Yes escalate,No it's okay", where="unit")
            assert len(caplog.records) == 1


# --------------------------------------------------------------------------- #
# Rule 1 at the pending seam
# --------------------------------------------------------------------------- #


def _option(position: int, label: str, entity_type: str, **payload: Any) -> dict[str, Any]:
    return {"position": position, "label": label, "entity_type": entity_type, "payload": payload}


class TestPendingKinds:
    def test_the_incident_customer_picker_sends_no_buttons(self) -> None:
        labels = list(INCIDENT_LABELS)
        assert turn_pending.quick_replies("customer_pick", labels) is None
        # ... and not only because the names are long: a short customer name is a
        # roster value all the same (owner rule: fixed short options ONLY).
        assert turn_pending.quick_replies("customer_pick", ["ACME", "BETA"]) is None

    @pytest.mark.parametrize(
        "kind",
        [
            "product_pick",
            "tier_pick",
            "kind_pick",
            "company_pick",
            "attachment_type_ask",
            "member_offer",
            "top_selling_pick",
            # minted off the operator-editable `chatbot_entity_kinds` table: a roster
            # by construction, suppressed without being named anywhere.
            "brand_pick",
            "colour_ask",
        ],
    )
    def test_every_roster_kind_sends_no_buttons(self, kind: str) -> None:
        assert turn_pending.quick_replies_suppressed(kind) is True
        assert turn_pending.quick_replies(kind, ["A", "B"]) is None

    def test_team_pick_keeps_its_team_names(self) -> None:
        options = [_option(1, "Mocha", "team", team="mocha"), _option(2, "Sorento", "team", team="sorento")]
        assert turn_pending.quick_replies("team_pick", ["Mocha", "Sorento"], options=options) == "Mocha, Sorento"

    @pytest.mark.parametrize(
        ("kind", "rows"),
        [
            (
                "outstanding_scope",
                [dict(r) for r in business_lane._OUTSTANDING_SCOPE_OPTIONS],
            ),
            (
                "outstanding_detail",
                [
                    {"idx": 1, "label": "Sales order list", "value": "so"},
                    {"idx": 2, "label": "Delivery order list", "value": "do"},
                    {"idx": 3, "label": "Both lists", "value": "both"},
                ],
            ),
            (
                "sales_report_detail",
                [
                    {"idx": 1, "label": "By customer", "value": "customer", "aliases": []},
                    {"idx": 2, "label": "By product", "value": "product", "aliases": []},
                    {"idx": 3, "label": "Delivery orders", "value": "delivery_order", "aliases": ["DO"]},
                ],
            ),
        ],
    )
    def test_the_fixed_option_kinds_keep_theirs_in_the_shape_production_builds(
        self, kind: str, rows: list[dict[str, Any]]
    ) -> None:
        """`turn/compose.py::_lane_question` stamps an outstanding kind's options with
        `entity_type = kind` and both production callers pass `options=`, so the
        predicate must not read that stamp as a roster."""
        from app.services.chatbot import engine as engine_mod

        question = _lane_question([{"lane_ask": {"kind": kind, "last_result_set": rows}}])
        assert question is not None and question.kind == kind
        labels = [str(r["label"]) for r in rows]
        expected = ", ".join(labels)
        assert turn_pending.quick_replies(kind, labels, options=question.options) == expected
        reply = engine_mod._reply_of(SimpleNamespace(question=question, text="Which one?"))
        assert reply["quick_replies"] == expected

    def test_a_team_pick_that_carries_company_names_is_a_company_clarify_and_sends_none(self) -> None:
        """`answer_bridge.py` mints the company clarify AS a `team_pick` whose options
        name companies (`payload.company`). Company names are roster values."""
        options = [
            _option(1, "Mocha", "team", company="Mocha", company_id="m", brand_code="MCH"),
            _option(2, "Sorento", "team", company="Sorento", company_id="s", brand_code="SRT"),
        ]
        assert turn_pending.quick_replies("team_pick", ["Mocha", "Sorento"], options=options) is None

    def test_a_team_pick_whose_options_are_products_sends_none(self) -> None:
        """The escalation lane's own product did-you-mean (`engine._question_offered`,
        `clarify_product`) is minted as a `team_pick` whose options are products. Product
        codes are roster values, whatever the kind says."""
        options = [
            {"position": 1, "label": "SRTWC8517", "uuid": None, "uuids": [], "entity_type": "product",
             "payload": {"product_code": "SRTWC8517", "team": "warehouse"}},
            {"position": 2, "label": "SRTWC8518", "uuid": None, "uuids": [], "entity_type": "product",
             "payload": {"product_code": "SRTWC8518", "team": "warehouse"}},
        ]
        assert turn_pending.quick_replies("team_pick", ["SRTWC8517", "SRTWC8518"], options=options) is None

    def test_the_three_button_cap_still_holds_for_a_fixed_kind(self) -> None:
        labels = [f"Option {i}" for i in range(1, 5)]
        assert turn_pending.quick_replies("team_pick", labels) is None
        assert turn_pending.quick_replies("team_pick", labels[:3]) == "Option 1, Option 2, Option 3"


# --------------------------------------------------------------------------- #
# Rule 1 at the did-you-mean producers
# --------------------------------------------------------------------------- #


class TestDidYouMeanSets:
    def test_a_numbered_list_of_two_or_more_fills_the_buttons_with_its_numbers(self) -> None:
        from app.services.chatbot.lanes.business import answer as answer_mod

        assert answer_mod._numbered_quick_reply(1) == "1,Yes escalate,No it's okay"
        assert answer_mod._numbered_quick_reply(2) == "1,2"
        assert answer_mod._numbered_quick_reply(3) == "1,2,3"
        assert answer_mod._numbered_quick_reply(5) == "1,2,3"

    def test_the_engine_seam_hands_a_numbered_set_on_unchanged(self) -> None:
        assert quick_reply_guard.guard("1,2,3") == "1,2,3"
        assert quick_reply_guard.guard("1,Yes escalate,No it's okay") == "1,Yes escalate,No it's okay"

    def test_the_code_mode_offer_sends_only_the_yes_no_pair(self) -> None:
        """`build_suggest_offer`, D1 code mode: one missed token with three close codes
        reads "Did you mean SRT57-CR, SRT5713, or SRT5732? ... or would you like me to
        escalate?" - codes are free text, so the sentence is the picker and only the
        fixed Yes / No pair goes out (it used to be the bare codes plus Yes / No)."""
        from app.services.chatbot.lanes.business.answer import build_suggest_offer
        from tests.chatbot.test_multi_code_dym import _PARSER, _two_token_resolved

        out = build_suggest_offer(
            {}, parser=_PARSER, resolved=_two_token_resolved(second_alts=False), gate={"company_team": "warehouse"}
        )
        assert out["suggest_offer"] is True
        assert "Did you mean SRT57-CR, SRT5713, or SRT5732?" in out["suggest_response"], out["suggest_response"]
        assert out["suggest_quick_reply"] == "Yes escalate,No it's okay", out["suggest_quick_reply"]
        # The typed code still resolves: the roster rides with the offer.
        assert [r["label"] for r in out["suggest_last_result_set"]] == ["SRT57-CR", "SRT5713", "SRT5732"]

    def test_the_all_miss_offer_keeps_its_yes_no_pair(self) -> None:
        """`build_suggest_offer`, two missed tokens (the all-miss paragraphs): the set is
        the fixed Yes / No pair, unchanged, and it passes the guard as is."""
        from app.services.chatbot.lanes.business.answer import build_suggest_offer
        from tests.chatbot.test_multi_code_dym import _PARSER, _two_token_resolved

        out = build_suggest_offer({}, parser=_PARSER, resolved=_two_token_resolved(), gate={"company_team": "warehouse"})
        assert out["suggest_quick_reply"] == "Yes escalate,No it's okay", out["suggest_quick_reply"]
        assert quick_reply_guard.guard(out["suggest_quick_reply"]) == "Yes escalate,No it's okay"

    def test_the_numbered_offer_sends_its_first_three_numbers(self) -> None:
        """`build_suggest_offer`, D1 numbered mode (a uuid-coded promotion candidate
        switches the sentence to "Here are the closest matches: 1. ... 2. ... 3. ..."):
        the buttons are "1,2,3" (owner confirmation 6 Oct 11:30: Yes / No dropped under
        the cap). It used to be "1,2,3,Yes escalate,No it's okay"."""
        from app.services.chatbot.lanes.business.answer import build_suggest_offer

        promos = [
            ("aaaaaaaa-1111-4111-8111-111111111111", "Spring promo"),
            ("bbbbbbbb-2222-4222-8222-222222222222", "Dealer launch promo"),
            ("cccccccc-3333-4333-8333-333333333333", "Year end promo"),
        ]
        resolved = {
            "resolutions": [
                {
                    "token": "promo",
                    "resolved": False,
                    "matches": [],
                    "alternatives": [
                        {"entity_type": "promotion", "canonical_code": pid, "uuid": pid, "match_tier": "trgm",
                         "similarity": 0.5, "display": {"name": name}}
                        for pid, name in promos
                    ],
                }
            ],
            "unresolved_tokens": ["promo"],
        }
        parser = {"domain_hint": "promotion", "entities": [{"raw": "promo", "hint": "promotion"}]}
        out = build_suggest_offer({}, parser=parser, resolved=resolved, gate={"company_team": "marketing"})
        assert "Here are the closest matches:\n1. Spring promo\n2. Dealer launch promo\n3. Year end promo" in out["suggest_response"], out["suggest_response"]
        assert out["suggest_quick_reply"] == "1,2,3", out["suggest_quick_reply"]

    def test_the_alternatives_offer_sends_only_the_escalation_pair(self) -> None:
        """`build_suggest_offer`, D2 "Try: ..." arm: the offered codes are free text, so
        the set is the fixed Yes / No pair and none of the values rides as a button."""
        from app.services.chatbot.lanes.business.answer import build_suggest_offer

        values = ["SRTWC8517", "SRTWC8518", "SRTWC8519"]
        out = build_suggest_offer(
            {},
            parser={"domain_hint": "inventory", "entities": [{"raw": "srtwc8500", "hint": "product"}]},
            resolved={"resolutions": [], "unresolved_tokens": []},
            gate={"company_team": "warehouse"},
            get_results=[[{"alternatives": [{"value": v} for v in values], "relaxed_axis": "entity"}]],
        )
        assert out["suggest_offer"] is True
        assert "Try: SRTWC8517, SRTWC8518, SRTWC8519." in out["suggest_response"], out["suggest_response"]
        assert out["suggest_quick_reply"] == "Yes escalate,No it's okay", out["suggest_quick_reply"]
        assert not any(v in out["suggest_quick_reply"] for v in values)

    def test_the_customer_service_date_miss_sends_no_buttons(self) -> None:
        """`build_suggest_offer`, D2 date arm on the customer service order enquiry
        route: its sentence offers dates only, so no Yes / No pair goes out."""
        from app.services.chatbot.lanes.business.answer import build_suggest_offer

        out = build_suggest_offer(
            {},
            parser={
                "domain_hint": "order",
                "entities": [{"raw": "acme", "hint": "customer"}],
                "date_filter_start": "2026-10-01",
                "date_filter_end": "2026-10-01",
                "routing": {"suggested_team": "customer_service", "suggested_agent": "order_enquiries"},
            },
            resolved={"resolutions": [], "unresolved_tokens": []},
            gate={"company_team": "customer_service"},
            get_results=[[{"alternatives": [{"value": "2026-10-03"}, {"value": "2026-10-05"}], "relaxed_axis": "date"}]],
        )
        assert out["suggest_offer"] is True
        assert out["suggest_quick_reply"] is None, out["suggest_quick_reply"]

    def test_the_uuid_coded_alternatives_offer_sends_its_numbers(self) -> None:
        """`build_suggest_offer`, D2 numbered call site: uuid-coded alternatives print as
        a numbered list and the buttons are "1,2,3"."""
        from app.services.chatbot.lanes.business.answer import build_suggest_offer

        alts = [
            {"value": "aaaaaaaa-1111-4111-8111-111111111111", "display": "Spring promo"},
            {"value": "bbbbbbbb-2222-4222-8222-222222222222", "display": "Dealer launch promo"},
            {"value": "cccccccc-3333-4333-8333-333333333333", "display": "Year end promo"},
        ]
        out = build_suggest_offer(
            {},
            parser={"domain_hint": "promotion", "entities": [{"raw": "promo", "hint": "promotion"}]},
            resolved={"resolutions": [], "unresolved_tokens": []},
            gate={"company_team": "marketing"},
            get_results=[[{"alternatives": alts, "relaxed_axis": "entity"}]],
        )
        assert "1. Spring promo\n2. Dealer launch promo\n3. Year end promo" in out["suggest_response"], out["suggest_response"]
        assert out["suggest_quick_reply"] == "1,2,3", out["suggest_quick_reply"]


# --------------------------------------------------------------------------- #
# The engine seams
# --------------------------------------------------------------------------- #


class TestEngineSeams:
    def test_reply_of_runs_the_guard_over_a_fixed_kind_with_long_labels(self) -> None:
        """A producer that forgets rule 1 still cannot trip Meta: a `team_pick` label
        over 20 characters drops the set at `_reply_of`."""
        from app.services.chatbot import engine as engine_mod

        question = turn_pending.ask(
            "team_pick",
            [
                _option(1, "DO Customer Service", "team", team="do_cs"),
                _option(2, "Project Customer Service", "team", team="project_cs"),
            ],
        )
        reply = engine_mod._reply_of(SimpleNamespace(question=question, text="Which team?"))
        assert reply["quick_replies"] is None
        assert [o["label"] for o in reply["result_set"]] == ["DO Customer Service", "Project Customer Service"]

    def test_reply_of_keeps_a_short_team_pick(self) -> None:
        from app.services.chatbot import engine as engine_mod

        question = turn_pending.ask(
            "team_pick", [_option(1, "Mocha", "team", team="mocha"), _option(2, "Sorento", "team", team="sorento")]
        )
        reply = engine_mod._reply_of(SimpleNamespace(question=question, text="Which team?"))
        assert reply["quick_replies"] == "Mocha, Sorento"

    def test_reply_of_hands_a_company_clarifys_options_to_the_predicate(self) -> None:
        """`_quick_replies_of` passes the question's options through, so a `team_pick`
        that is really the company clarify (`payload.company`) sends no buttons."""
        from app.services.chatbot import engine as engine_mod

        question = turn_pending.ask(
            "team_pick",
            [
                _option(1, "Mocha", "team", company="Mocha", company_id="m", brand_code="MCH"),
                _option(2, "Sorento", "team", company="Sorento", company_id="s", brand_code="SRT"),
            ],
        )
        reply = engine_mod._reply_of(SimpleNamespace(question=question, text="Which company?"))
        assert reply["quick_replies"] is None

    def test_reply_of_hands_a_product_did_you_mean_options_to_the_predicate(self) -> None:
        from app.services.chatbot import engine as engine_mod

        question = turn_pending.ask(
            "team_pick",
            [
                {"position": 1, "label": "SRT1", "uuid": None, "uuids": [], "entity_type": "product",
                 "payload": {"product_code": "SRT1", "team": "warehouse"}},
                {"position": 2, "label": "SRT2", "uuid": None, "uuids": [], "entity_type": "product",
                 "payload": {"product_code": "SRT2", "team": "warehouse"}},
            ],
        )
        reply = engine_mod._reply_of(SimpleNamespace(question=question, text="Which product?"))
        assert reply["quick_replies"] is None

    def test_compose_question_hands_a_company_clarifys_options_to_the_predicate(self) -> None:
        from app.services.chatbot.turn.compose import compose_question

        company = turn_pending.ask(
            "team_pick",
            [
                _option(1, "Mocha", "team", company="Mocha", company_id="m", brand_code="MCH"),
                _option(2, "Sorento", "team", company="Sorento", company_id="s", brand_code="SRT"),
            ],
        )
        assert compose_question(company).actions[0]["quick_replies"] is None
        teams = turn_pending.ask(
            "team_pick", [_option(1, "Mocha", "team", team="mocha"), _option(2, "Sorento", "team", team="sorento")]
        )
        assert compose_question(teams).actions[0]["quick_replies"] == "Mocha, Sorento"

    @pytest.mark.parametrize(
        ("tail_set", "expected"),
        [
            ("Alpha one two three four, Beta", None),
            ("One, Two, Three, Four", "One, Two, Three"),
        ],
    )
    def test_run_tail_guards_a_set_the_tail_composed(
        self, session_factory, system_settings_row, monkeypatch, tail_set: str, expected: str | None
    ) -> None:
        """`run_tail` guards `reply_ladder.compose_reply`'s own `quick_replies`. No
        producer emits an over-long or over-three set through the ladder today (the
        producers pass the guard themselves), so the ladder is monkeypatched to return one:
        this is the net for a future producer. The clarify's own lane fallback is made
        unusable (team names over 20 characters) so only the tail set can reach the reply."""
        from app.services.chatbot.tail import reply_ladder
        from tests.chatbot.test_s5_escalation_seams import _run_nurain_team_clarify

        real = reply_ladder.compose_reply

        def composed_with_a_set(outcome: Any) -> dict[str, Any]:
            return {**real(outcome), "quick_replies": tail_set}

        monkeypatch.setattr(reply_ladder, "compose_reply", composed_with_a_set)
        result, row = _run_nurain_team_clarify(
            session_factory, system_settings_row, monkeypatch,
            teams=(
                ("do_customer_service", "DO Customer Service"),
                ("project_customer_service", "Project Customer Service"),
            ),
        )
        assert row.response["reply"]["quick_replies"] == expected, row.response["reply"]

    def test_seal_send_guards_the_lanes_own_fallback(self) -> None:
        """A lane that hand-built its action's buttons (the escalation clarifies) has
        them guarded too when the tail sealed none."""
        from app.services.chatbot import engine as engine_mod

        action = {"kind": "send_message", "text": "Which team?", "quick_replies": ", ".join(INCIDENT_LABELS)}
        sealed = engine_mod._seal_send(action, {"quick_replies": None, "result_set": []})
        assert sealed["quick_replies"] is None

        short = {"kind": "send_message", "text": "Which team?", "quick_replies": "Mocha, Sorento"}
        assert engine_mod._seal_send(short, {"quick_replies": None, "result_set": []})["quick_replies"] == (
            "Mocha, Sorento"
        )


# --------------------------------------------------------------------------- #
# The incident conversation, end to end through the real engine
# --------------------------------------------------------------------------- #


def _ong_brothers_ask(**extra: Any) -> dict[str, Any]:
    return _order_ask(
        entities=[{"raw": "ong brothers", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}],
        document=["DO"],
        status="delivered",
        routing={"suggested_team": "customer_service", "suggested_agent": "order_enquiries", "team_source": None},
        **{**do_window(), **extra},
    )


def _pick(position: int) -> dict[str, Any]:
    """"2" typed back at the open picker: the parser resolves it onto a position."""
    return _parser_output(
        message_type="casual", intent_hint=None, domain_hint=None, entities=[],
        reference_positions=[position], order_status=None, domain_in_message=False,
    )


@pytest.fixture
def ong_brothers_is_three_customers(owner_chat, monkeypatch):  # noqa: F811 - fixture
    """What prod resolved "ong brothers" to on 6 Oct 2026: three single-account
    customers under Sorento, two of them one string once cut at 20 characters."""
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.lanes.business.services import ResolveGateServices
    from tests.chatbot.conftest import validating_resolve_entity

    ids = {name: str(uuid_mod.uuid4()) for name in INCIDENT_NAMES}

    def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        tokens = list(body.get("tokens") or [])
        resolutions = []
        unresolved = []
        for t in tokens:
            if t.casefold() == "ong brothers":
                resolutions.append({"token": t, "resolved": True, "matches": [
                    {
                        "uuid": ids[name], "entity_type": "customer", "canonical_code": f"3000/O{i}",
                        "match_tier": "fuzzy", "company_code": "SRT",
                        "display": {"customer_name": name, "debtor_name": name},
                    }
                    for i, name in enumerate(INCIDENT_NAMES, 1)
                ]})
            else:
                unresolved.append(t)
        return {"tokens": tokens, "resolutions": resolutions, "unresolved_tokens": unresolved}

    services = ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(resolve_entity),
        probe=lambda **_: None,
    )
    monkeypatch.setattr(engine_mod.business_services, "production_services", lambda db, *, space_id=None: services)
    return ids


def _last_turn_row(chat):
    from app.models.chatbot_turn import ChatbotTurn

    db = chat.session_factory()
    return db.query(ChatbotTurn).filter(ChatbotTurn.message_id == f"ZZT-r3-brand-carry-{chat._turn}").first()


def _open_question(chat) -> dict[str, Any]:
    from tests.chatbot.test_samantha_26sep_s9_brand_resolve import _open_question_of

    return _open_question_of(chat.session_factory) or {}


def test_the_incident_picker_prints_its_list_and_sends_no_buttons_then_2_picks(
    owner_chat, ong_brothers_is_three_customers  # noqa: F811 - fixtures
) -> None:
    chat = owner_chat
    ids = ong_brothers_is_three_customers

    reply, calls, _ = chat.turn("DO for ong brothers", _ong_brothers_ask())

    assert chat.kind() == "business_query", reply
    assert "Which customer do you mean? Please choose:" in reply, reply
    for n, label in enumerate(INCIDENT_LABELS, 1):
        assert f"{n}. {label}" in reply, reply
    assert _open_question(chat).get("kind") == "customer_pick", _open_question(chat)

    row = _last_turn_row(chat)
    persisted = row.response["reply"]
    assert persisted["quick_replies"] is None, persisted
    sends = [a for a in row.response["actions"] if a.get("kind") == "send_message"]
    assert sends and all(a.get("quick_replies") is None for a in sends), sends
    # The roster itself still travels with the message: that is what a typed "2" resolves against.
    assert [o["label"] for o in persisted["result_set"]] == list(INCIDENT_LABELS), persisted["result_set"]

    reply2, calls2, _ = chat.turn("2", _pick(2))

    order_calls = [a for name, a in calls2 if name == ORDERS_LIST]
    assert order_calls, (reply2, calls2)
    assert order_calls[-1].get("customer_ids") == [ids[INCIDENT_NAMES[1]]], order_calls[-1]
    assert "Which customer do you mean?" not in reply2, reply2
