"""S6 cluster 4 owner ruling (16 Sep 2026, UAC "S6 rulings" block):

"An open question is answered only when the PARSER says so. `answers_open_question`
`{resolved, picks, answer}` becomes a declared parser schema key (it was read by APPLY
but never produced: the parser only emits `reference_positions`). APPLY: `resolved:
true` = apply the picks; `resolved: false` with an attempt = re-print the same
question; absent/null = the message is not an answer, run it for what it is and CARRY
the pending unchanged (sticky, never repeated). Owner: 'follow the old bot, it is
smarter, no hard coding, LLM aware'."

Three reds:

1. `head/parser.py::PARSE_OUTPUT_JSON_SCHEMA` does not declare `answers_open_question`
   at all today (measured: absent from both `properties` and `required`) - the parser
   is never actually asked to emit it, so `turn/apply.py`'s read of it
   (`verdict.get("answers_open_question")`) can only ever see the harness's own
   hand-built default.
2. The fallback `SEMANTIC_PARSER_PROMPT` does not document the key either (measured:
   the literal string is absent) - an LLM parsing against the live prompt has no
   instruction to ever emit it.
3. `turn/apply.py::_answer_pending` cannot currently tell an EXPLICIT "that did not
   answer it" (`resolved: false`) from "this message was never an attempt at all"
   (`answers_open_question` absent/null): both fall through to the same unconditional
   re-print (`answer_pending_unresolved`, `tests/chatbot/test_rearch_s2_number_answers.py
   ::test_a_position_out_of_range_leaves_state_unchanged_and_reprints` pins the FIRST of
   these and is left untouched). The absent/null case must instead run the message for
   what it is (no short-circuit) and carry the pending forward unresolved.
"""
from __future__ import annotations

import pytest

from tests.chatbot._turn_helpers import PENDING_KINDS, ROSTER_KINDS, build_policy, verdict
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures


# `test_schema_declares_answers_open_question_as_a_required_object_key` and
# `test_prompt_documents_the_answers_open_question_key` RETIRED (owner ruling, hand pass
# 3, 17 Sep 2026 - `chatbot-turn-rearch-acceptance-criteria.md`'s "Hand pass 3 rulings"
# block): `answers_open_question` is retired from the parser schema, the prompt and
# APPLY outright, so a schema/prompt asserting its presence is now asserting the wrong
# thing on purpose. The retirement's own behaviour (carry, never re-print, on a message
# that answers nothing) is unchanged and stays covered by
# `TestAbsentAnswerCarriesThePendingWithoutReprinting` below, restated without the
# retired key.


def _state_with_pending(kind: str):
    from app.services.chatbot.turn.pending import ask
    from app.services.chatbot.turn.state import Focus, Profile, State

    options = [
        {"position": 1, "label": "A", "uuid": "u1", "uuids": ["u1"], "entity_type": "product", "payload": {"domain": "incoming"}},
        {"position": 2, "label": "B", "uuid": "u2", "uuids": ["u2"], "entity_type": "product", "payload": {"domain": "incoming"}},
    ]
    pending = ask(kind, options, team=None, asked_at_turn=1)
    return State(focus=Focus(), pending=pending, profile=Profile())


class TestAbsentAnswerCarriesThePendingWithoutReprinting:
    """`answers_open_question` absent/null on a pending turn is NOT an attempt to
    answer - `apply()` must not short-circuit into a re-print of the same question, and
    the pending must survive untouched for the NEXT turn to still be able to answer it.
    """

    @pytest.mark.parametrize("kind", PENDING_KINDS)
    def test_a_casual_message_with_no_entities_does_not_reprint(self, kind):
        from app.services.chatbot.turn.apply import apply

        state = _state_with_pending(kind)
        v = verdict(message_type="casual", entities=[], answers_open_question=None)

        state2, plan = apply(state, v, build_policy())

        assert "answer_pending_unresolved" not in plan.trace.rules_fired, plan.trace.rules_fired
        assert state2.pending is not None, "the pending must survive, not be cleared"
        assert state2.pending.kind == kind

    def test_a_casual_message_with_a_pending_open_fetches_nothing(self):
        """AC-1593 finding, 16 Sep 2026 console browser pass 2 (handpass2, contact
        CONTACT N, turn f8fe14a4 "hello" with a `team_pick` offer open): the recorded turn
        re-ran the PRIOR order fetch verbatim instead of answering as the casual
        exchange it plainly is - "hello" carries no business of its own.

        `test_a_casual_message_with_no_entities_does_not_reprint` above already proves
        the pending survives and the question is not re-printed; this proves the other
        half of the same claim, that `apply()`'s returned `Plan` carries no fetch at
        all for a casual message - not just that the pending is untouched. Measured:
        `plan.fetch` is a non-empty `[FetchSpec(domain='order', ...)]` today because a
        casual message with an open pending still carries the OLD focus.domains into
        the fetch step, the same way a real business message would."""
        from app.services.chatbot.turn.apply import apply
        from app.services.chatbot.turn.pending import ask
        from app.services.chatbot.turn.state import Focus, Profile, State

        options = [
            {
                "position": 1,
                "label": "orders",
                "entity_type": "team",
                "payload": {"team": "customer_service"},
            },
        ]
        pending = ask("team_pick", options, team="customer_service", asked_at_turn=1)
        state = State(focus=Focus(domains=["order"]), pending=pending, profile=Profile())
        v = verdict(message_type="casual", entities=[], answers_open_question=None)

        state2, plan = apply(state, v, build_policy())

        assert plan.fetch == [], (
            "a casual message with no entities must yield NO fetch while a pending "
            f"sits open - got {plan.fetch!r}"
        )
        assert state2.pending is not None and state2.pending.kind == "team_pick", (
            "the pending must still be carried, unchanged"
        )

    @pytest.mark.parametrize("kind", PENDING_KINDS)
    def test_a_business_query_with_its_own_entities_runs_normally_not_a_reprint(self, kind):
        """A message that names its own business (a domain, an entity) while a roster
        sits open is not answering the roster either - it must be run for what it IS
        (contract: the pending is carried, not re-asked verbatim)."""
        from app.services.chatbot.turn.apply import apply

        state = _state_with_pending(kind)
        v = verdict(
            message_type="business_query",
            domain_hint="promotion",
            entities=[],
            answers_open_question=None,
        )

        state2, plan = apply(state, v, build_policy())

        assert "answer_pending_unresolved" not in plan.trace.rules_fired, plan.trace.rules_fired
        # The turn ran for what it is: `promotion` reaches the plan's domains, rather
        # than the plan being nothing but a verbatim re-ask of the OLD pending question.
        assert "promotion" in plan.domains, plan.domains
        assert state2.pending is not None, "the pending must survive unresolved, sticky"
        assert state2.pending.kind == kind


class TestTheCasualTailWritesThePendingBackUnchanged:
    """AC-1593 (browser pass 3, 16 Sep 2026, turn `d9c09b50` "hello" while an escalate
    offer sat open): `apply()` already proves it carries `state.pending` unchanged
    through a casual/idle message (the class above, `test_a_casual_message_with_a_
    pending_open_fetches_nothing`) - this is the OTHER half, that the real engine's tail
    actually WRITES that carried pending back to the session, not just that `apply()`
    computed it correctly in isolation.

    The `low_signal` lane is the one to watch: it is the ONLY arm that completes through
    `engine.complete_turn` (the pre-rearch tail entry point, built for the delegate
    pipeline) rather than `turn/tail.py::session_payload` directly (`_run_answer`'s own
    tail) - measured in `engine.py::_run_casual_lane`. Whether that older completion
    path still carries `turn/apply.py`'s NEW `state.pending` through to the five-key
    session write is exactly what a casual/idle turn needs and is not proven anywhere
    else in this tree.
    """

    def test_hello_on_a_dry_run_hands_back_the_same_open_question_it_was_seeded_with(
        self, session_factory, stub_parser, stub_access
    ) -> None:
        import json

        from sqlalchemy import text

        from app.services.chatbot import engine as engine_mod
        from tests.chatbot._turn_helpers import verdict as v3_verdict
        from tests.chatbot.test_engine import CONTACT_ID, _envelope

        db = session_factory()
        existing = db.execute(
            text("SELECT 1 FROM respond_contacts WHERE respond_io_id = :cid"),
            {"cid": str(CONTACT_ID)},
        ).first()
        if existing is None:
            db.execute(
                text(
                    "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
                    "VALUES (gen_random_uuid()::text, :cid, :phone, CAST('{}' AS jsonb))"
                ),
                {"cid": str(CONTACT_ID), "phone": "+60000000009"},
            )
            db.commit()
        seeded_pending = {
            "kind": "team_pick",
            "team": "customer_service",
            "expects": "yes_no",
            "options": [
                {
                    "position": 1,
                    "label": "orders",
                    "entity_type": "team",
                    "payload": {"team": "customer_service"},
                }
            ],
        }
        db.execute(
            text(
                "UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) "
                "WHERE respond_io_id = :cid"
            ),
            {"cid": str(CONTACT_ID), "sv": json.dumps({"variables": {"open_question": seeded_pending}})},
        )
        db.commit()

        stub_parser(
            v3_verdict(
                message_type="casual",
                entities=[],
                is_affirmative=None,
                escalation={
                    "is_escalation_confirmation": None,
                    "escalation_declined": None,
                    "company_pick": None,
                },
                answers_open_question=None,
            )
        )
        stub_access()

        envelope = _envelope(is_test=True)  # dry run: D14, ZERO writes outside chatbot.turns
        envelope.message["message"]["message"]["text"] = "hello"

        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert result.session_patch is not None, "a dry run must hand back what it would have written"
        after = (result.session_patch or {}).get("open_question")
        assert after is not None, "the open question must still be there after a casual reply"
        assert after.get("kind") == "team_pick", after
        assert after.get("team") == "customer_service", after
        assert after.get("options") == seeded_pending["options"], after
