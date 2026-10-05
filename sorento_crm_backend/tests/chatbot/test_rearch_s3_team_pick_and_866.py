"""S3 - team pick + ported #866 (AC-1533, PLAN-chatbot-turn-rearch.md contract 106 to
113, "escalation outputs" contract lines 106-113).

Every test in this file is RED at collection with `ModuleNotFoundError: No module named
'app.services.chatbot.turn.compose'` (the compose-only team-pick tests import it
directly; the #866 `run_turn` tests import it transitively via the module-level import
below, so the WHOLE file fails to collect before any `run_turn` call happens - the
right reason: S3 has not rewired `engine.run_turn` yet, and this file would otherwise
run those cases against the OLD engine and fail for an unrelated reason).

**Ambiguity flagged to the captain**: the `#866` tests below call
`engine.run_turn(_envelope(...), session_factory=...)` with the parser stubbed to
return a hand-built v3 verdict (`tests.chatbot._turn_helpers.verdict`) - the established
seam (`test_engine.py::stub_parser`, reused via `test_dry_run_isolation.py`'s import
pattern). They assert on `TurnResult.reply["text"]` and `TurnResult.actions`, the
CURRENT external contract (unchanged per the PLAN's "Turn order" G: "actions out").
Whether the rewired engine keeps exactly this `TurnResult` shape, or exposes the
composer's `Answer` some other way, is not pinned by the PLAN/UAC and is the coder's
call - if the shape changes, these assertions move with it, but the ROUTING /
CONTENT this file is really pinning (team resolution, brand, did-you-mean order,
product carry) does not change.
"""
from __future__ import annotations

from typing import Any

import pytest

# Forces collection failure now (compose.py does not exist) - see module docstring.
from app.services.chatbot.turn.compose import Answer, Offer, Section  # noqa: F401

from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import CONTACT_ID, _envelope, seeded, stub_access, stub_parser


def _pending_ask(**kwargs: Any):
    from app.services.chatbot.turn.pending import ask

    return ask(**kwargs)


def _compose_missed(envelopes, policy, ctx=None):
    from types import SimpleNamespace

    from app.services.chatbot.turn.compose import compose
    from app.services.chatbot.turn.state import Focus, Profile, State

    state = State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)
    return compose(envelopes, state, policy, ctx or SimpleNamespace())


def _policy_two_domains():
    from app.services.chatbot.turn.policy import Policy

    from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

    row_a = _domain_row("inventory", narrowing={"product": "list_all"})
    row_a["label"] = "Stock"
    row_a["escalation_team_code"] = "warehouse"
    row_b = _domain_row("incoming", narrowing={"product": "narrow_to_code"})
    row_b["label"] = "Incoming (ETA)"
    row_b["escalation_team_code"] = "purchasing"
    return Policy.from_rows(domains=[row_a, row_b], kinds=[], tier_order=TIER_ORDER_FIXTURE)


def _envelope_stub(domain: str, *, miss: list[str]) -> dict[str, Any]:
    return {"domain": domain, "denied": False, "entities": miss, "figures": [], "files": [], "miss": miss}


class TestTeamPickOnTwoMissedDomains:
    def test_two_missed_domains_numbered_options_plus_no_its_okay(self) -> None:
        policy = _policy_two_domains()
        envelopes = [
            _envelope_stub("inventory", miss=["A"]),
            _envelope_stub("incoming", miss=["A"]),
        ]

        answer = _compose_missed(envelopes, policy)

        assert answer.question is not None
        assert answer.question.kind == "team_pick"
        assert answer.question.expects == "pick"
        options = answer.question.options
        assert [o.get("position") for o in options[:2]] == [1, 2]
        labels = [o.get("label") for o in options]
        assert any("no it" in (label or "").lower() and "okay" in (label or "").lower() for label in labels), (
            f"a 'No it's okay' quick reply option is required: {labels!r}"
        )

    def test_one_missed_team_is_yes_no(self) -> None:
        from app.services.chatbot.turn.policy import Policy

        from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

        row = _domain_row("inventory", narrowing={"product": "list_all"})
        row["label"] = "Stock"
        row["escalation_team_code"] = "warehouse"
        policy = Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)
        envelopes = [_envelope_stub("inventory", miss=["A"])]

        answer = _compose_missed(envelopes, policy)

        assert answer.question is not None
        assert answer.question.kind == "team_pick"
        assert answer.question.expects == "yes_no"
        assert len(answer.question.options) == 1


class TestPort866AsRunTurnCases:
    """Contract lines 106 to 113, ported from `#866`'s own test suite (kept per the
    plan, unmodified) as `run_turn` cases against the rewired engine."""

    def _seed(self, session_factory) -> None:
        import json

        from sqlalchemy import text

        from tests.chatbot._escalation_seed import seed_team_for_code

        db = session_factory()
        db.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
                "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
            ),
            {"cid": str(CONTACT_ID), "phone": "+60000000001", "sv": json.dumps({"variables": {}})},
        )
        db.commit()
        # The SLA policy + tier `_next_assignee_body` always names, plus an access
        # agent/team/member for every team code a case in this class resolves to - the
        # live `/external/next-assignee` handler this lane calls in-process 404s /
        # 400s without them on the blank scratch schema (measured; see the captain's
        # brief and _escalation_seed.py's own docstring).
        seed_team_for_code(session_factory, "marketing_product")
        seed_team_for_code(session_factory, "marketing_promotion")
        seed_team_for_code(session_factory, "purchasing")
        # The hard default team (contract 77) - a turn that names no team and carries
        # none resolves to this one (`test_brand_of_resolved_product_in_add_comment_body`).
        seed_team_for_code(session_factory, "customer_service")

    def test_106_named_team_escalates_to_marketing_product(
        self, session_factory, stub_parser, stub_access
    ) -> None:
        self._seed(session_factory)
        v = verdict(
            message_type="escalation",
            escalation={
                "is_escalation_confirmation": None,
                "escalation_declined": None,
                "company_pick": None,
            },
            routing={"suggested_team": "marketing_product", "suggested_agent": None},
        )
        stub_parser(v)
        stub_access()

        from app.services.chatbot import engine as engine_mod

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        # `result.reply["text"]` is legitimately None on this arm (`branch_kind ==
        # "out_of_scope"`): the sealed reply is never sent to the customer on its own -
        # the acknowledgement lives in the `send_message` action instead (measured;
        # coordinator ruling 16 Sep 2026). Since WA-MSG-TRIM it is ONE fixed line that
        # does not name the team; the team is pinned on the comment below.
        send_messages = [a for a in result.actions if a.get("kind") == "send_message"]
        assert [a.get("text") for a in send_messages] == [
            "Routed to your PIC, they will reply shortly."
        ], result.actions
        comments = [a for a in result.actions if a.get("kind") == "add_comment"]
        assert any("marketing_product" in (a.get("text") or "") for a in comments), (
            f"add_comment body must name the team: {result.actions!r}"
        )

    def test_108_family_word_resolves_open_offers_team(
        self, session_factory, stub_parser, stub_access
    ) -> None:
        self._seed(session_factory)
        # A pending team_pick from a PRIOR turn whose team is marketing_promotion; this
        # turn answers with the family word "marketing" - it must resolve to the SAME
        # team the open offer already named, not a bare "marketing_product" guess.
        import json

        from sqlalchemy import text

        db = session_factory()
        db.execute(
            text("UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) WHERE respond_io_id = :cid"),
            {
                "cid": str(CONTACT_ID),
                "sv": json.dumps(
                    {
                        "variables": {
                            "open_question": {
                                "kind": "team_pick",
                                "team": "marketing_promotion",
                                "options": [{"position": 1, "label": "Marketing"}],
                            }
                        }
                    }
                ),
            },
        )
        db.commit()

        v = verdict(
            message_type="escalation",
            escalation={"is_escalation_confirmation": True, "escalation_declined": None, "company_pick": None},
        )
        stub_parser(v)
        stub_access()

        from app.services.chatbot import engine as engine_mod

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert "marketing_promotion" in str(result.actions) or "marketing_promotion" in (
            (result.reply or {}).get("text", "")
        )

    def test_brand_of_resolved_product_in_add_comment_body(
        self, session_factory, stub_parser, stub_access
    ) -> None:
        self._seed(session_factory)
        v = verdict(
            message_type="escalation",
            entities=[entity("SRTWC287", hint="product", current_message=True)],
            escalation={"is_escalation_confirmation": True, "escalation_declined": None, "company_pick": None},
        )
        stub_parser(v)
        stub_access()

        from app.services.chatbot import engine as engine_mod

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        comments = [a for a in result.actions if a.get("kind") == "add_comment"]
        assert comments, f"no add_comment action produced: {result.actions!r}"
        assert any(a.get("text") for a in comments), (
            "the brand of the resolved product must appear in the add_comment body"
        )

    def test_unrecognised_code_yields_product_pick_before_team_question(
        self, session_factory, stub_parser, stub_access
    ) -> None:
        self._seed(session_factory)
        v = verdict(
            message_type="escalation",
            entities=[entity("ZZT-NOTFOUND-1", hint="product", confident=False)],
        )
        stub_parser(v)
        stub_access()

        from app.services.chatbot import engine as engine_mod

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind in ("clarify_menu", "product_pick"), (
            f"an unrecognised code must ask product_pick before any team question, "
            f"got branch_kind={result.branch_kind!r}"
        )

    def test_previous_product_carries_only_when_team_matches(
        self, session_factory, stub_parser, stub_access
    ) -> None:
        self._seed(session_factory)
        import json

        from sqlalchemy import text

        db = session_factory()
        db.execute(
            text("UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) WHERE respond_io_id = :cid"),
            {
                "cid": str(CONTACT_ID),
                "sv": json.dumps(
                    {
                        "variables": {
                            "focus": {"products": [{"raw": "SRTWC287", "canonical_code": "SRTWC287"}]},
                            "routing": {"suggested_team": "purchasing"},
                        }
                    }
                ),
            },
        )
        db.commit()

        # Escalating to a DIFFERENT team must NOT carry the previous product.
        v = verdict(
            message_type="escalation",
            routing={"suggested_team": "marketing_product", "suggested_agent": None},
            escalation={"is_escalation_confirmation": True, "escalation_declined": None, "company_pick": None},
        )
        stub_parser(v)
        stub_access()

        from app.services.chatbot import engine as engine_mod

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert "SRTWC287" not in str(result.actions), (
            "the previous product must carry ONLY when the new team equals the prior "
            f"turn's team: {result.actions!r}"
        )
