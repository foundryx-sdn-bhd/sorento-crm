"""Three architecture invariants driven through the PURE turn package (`decide`,
`apply`, `state`) with hand-built verdicts - no LLM, no DB, no engine.py. Written from
the captain's rulings on the chatbot-turn-rearch lane (18 Sep 2026) alongside the hand
pass 5 journey chains (`tests/chatbot/journeys/handpass5-*.json`), which pin the same
defects at the full-engine layer. `tests.chatbot._turn_helpers` supplies the same
`verdict()`/`entity()`/`build_policy()` builders `test_rearch_s2_apply_is_pure.py`
already uses for this exact seam.
"""
from __future__ import annotations

from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.pending import ask as pending_ask
from app.services.chatbot.turn.policy import default_policy
from app.services.chatbot.turn.state import Focus, Profile, State

from tests.chatbot._turn_helpers import build_policy, entity, verdict


def test_a_new_ask_that_fetches_clears_the_stale_pending():
    """(a) After a NEW_ASK decision whose fetch ran, `pending` is None unless the
    answer itself armed one.

    Pins hand pass 5 turns 38/42/43 (owner console, contact 900000008, 2026-09-17
    05:27-05:30 +09): 'Incoming and stock CB2805A' left an incoming-stock roster on
    the wire; 'any markeitng forms?' is a fresh business ask that fetched a forms
    roster of its own (a NEW_ASK by `decide()`'s own rule - it names no entity, but
    the shape is the same class of turn: a fetch that runs while an older pending is
    still open); the recorded turn 43 ('1') answered the STALE incoming/stock roster
    instead of the forms list it should have closed.

    Reproduced here at the `apply()` seam with an entity-bearing NEW_ASK (a plain
    product code named over an unrelated open roster), which `decide()` reads as
    NEW_ASK unambiguously (`decide.Decision.kind == "new_ask"`, `why ==
    "names_its_own_entity"`) - the same class of message, isolated from any resolver
    ambiguity. `_answer_pending` (turn/apply.py) has five branches for what a pending
    can become; a NEW_ASK matches none of the first four (no position picked, not an
    acceptance, not a decline, not a negation-with-entities) and falls into the
    generic "nothing matched the open question" tail, which is written for CARRY
    ("the message runs as itself... the tail keeps the carried pending") but ALSO
    catches NEW_ASK - the fifth branch does not read `decision.kind` at all. Currently
    red: `new_state.pending` still holds the stale `product_pick` roster after a
    NEW_ASK whose own fetch ran clean (a single resolved candidate, no fresh ask
    armed - `plan.ask is None`).
    """
    stale_roster = pending_ask(
        "product_pick",
        [
            {
                "position": 1,
                "label": "SRTWC286-SH-200",
                "code": "SRTWC286-SH-200",
                "uuid": "u1",
                "uuids": ["u1"],
                "entity_type": "product",
                "payload": {},
            },
            {
                "position": 2,
                "label": "SRTWC286-SH-P",
                "code": "SRTWC286-SH-P",
                "uuid": "u2",
                "uuids": ["u2"],
                "entity_type": "product",
                "payload": {},
            },
        ],
        asked_at_turn=1,
        expects="pick",
        payload={"domain": "inventory", "domains": ["inventory"]},
    )
    state = State(focus=Focus(), pending=stale_roster, profile=Profile(), turn_no=2)
    v = verdict(entities=[entity("CB2805A", "product")], domain_hint="incoming")
    policy = build_policy()
    resolved_cb2805a = {
        "raw": "CB2805A",
        "canonical_code": "CB2805A",
        "uuid": "cb2805a-uuid",
        "current_message": True,
        "confident": True,
    }

    new_state, plan = apply(state, v, policy, candidates={"product": [resolved_cb2805a]})

    assert plan.fetch, "setup did not exercise a real fetch: " + repr(plan)
    assert plan.ask is None, "setup accidentally armed a fresh ask: " + repr(plan.ask)
    assert new_state.pending is None, (
        "a NEW_ASK whose fetch ran left the stale roster open instead of closing it: "
        f"{new_state.pending!r}"
    )


def test_fetch_spec_entities_only_carry_the_kinds_the_domain_declares():
    """(b) A FetchSpec for domain D carries only entity kinds D's own narrowing rows
    declare (`turn/policy_rows.py` DEFAULT_DOMAIN_ROWS, the real seed
    `chatbot_rearch_s0` writes and `Policy.default_policy()` falls back to) - never an
    `extra.*` kind (`state.Focus.extra`, the bag any kind outside {product, customer,
    warehouse, brand} lands in).

    Measured against the real seed rather than a hand-built fixture policy, per the
    brief: `resource_attachment` narrows on nothing (`narrowing={}`) and `incoming`
    narrows only on `product` (`narrowing={"product": "narrow_to_code"}`); neither
    declares `attachment_type`, the kind `product_attachment` alone declares
    (`narrowing={"attachment_type": "narrow_by_type", "product": "must_narrow_one"}`).
    Pins the same hand pass 5 precondition as R5's journey chain
    (`handpass5-eta-typo-and-attachment-carry.json`, case
    'attachment-turn-never-carries-its-xlsx-name-into-a-later-incoming-stock-ask'):
    turn 29 ('container setatus reports') puts an attachment-kind entity into the
    focus, and a LATER incoming/stock ask under a different domain must never carry
    it into that domain's own fetch.

    Unlike the journey chain (which drives the full engine and is red today because
    the customer-facing header-composition layer downstream of `apply()` still prints
    the carried filenames), this is a narrower claim scoped to `apply()`'s own
    `FetchSpec` construction - `_narrow_and_plan` (turn/apply.py) only ever calls
    `narrow_decide` for a `kind` that is a KEY of the domain's own `row.narrowing`
    dict, so an `extra.*` kind a domain never declares structurally cannot reach that
    domain's `FetchSpec.entities` through this seam. Kept as a real assertion (not
    deleted for being currently green) - it is the drift guard for this exact class of
    regression, the way `test_parser_schema_guard.py` is for the verdict schema: a
    future domain narrowing on a new `extra.*` kind, or a narrower helper that reads
    `focus.extra` outside the declared-kind loop, breaks it immediately.
    """
    policy = default_policy()
    resource_attachment = policy.domain("resource_attachment")
    incoming = policy.domain("incoming")
    product_attachment = policy.domain("product_attachment")
    assert resource_attachment is not None, "resource_attachment domain missing from the seed"
    assert incoming is not None, "incoming domain missing from the seed"
    assert product_attachment is not None, "product_attachment domain missing from the seed"
    assert resource_attachment.narrowing == {}, resource_attachment.narrowing
    assert "attachment_type" not in incoming.narrowing, incoming.narrowing
    assert "attachment_type" in product_attachment.narrowing, product_attachment.narrowing

    focus = Focus()
    focus.extra["attachment_type"] = [
        {
            "raw": "14.09.2026 Container Status 2026.xlsx",
            "canonical_code": "14.09.2026 Container Status 2026.xlsx",
            "hint": "attachment_type",
            "current_message": False,
            "confident": True,
        }
    ]
    state = State(focus=focus, pending=None, profile=Profile(grants=None), turn_no=1)
    v = verdict(entities=[entity("CB2805A", "product")], domain_hint="incoming")
    resolved_cb2805a = {
        "raw": "CB2805A",
        "canonical_code": "CB2805A",
        "uuid": "cb2805a-uuid",
        "current_message": True,
        "confident": True,
    }

    new_state, plan = apply(state, v, policy, candidates={"product": [resolved_cb2805a]})

    assert plan.fetch, "setup did not exercise a real fetch: " + repr(plan)
    for spec in plan.fetch:
        row = policy.domain(spec.domain)
        declared_kinds = set(row.narrowing) if row is not None else set()
        for e in spec.entities:
            hint = e.get("hint")
            assert hint is None or hint in declared_kinds, (
                f"FetchSpec for domain {spec.domain!r} (declares {sorted(declared_kinds)}) "
                f"carries an entity of kind {hint!r} it never declared: {e!r}"
            )
    # And the carried `attachment_type` value itself never reached the `incoming`
    # fetch by any route (not just kind-tagged - the literal value either).
    incoming_specs = [s for s in plan.fetch if s.domain == "incoming"]
    assert incoming_specs, "setup did not produce an incoming FetchSpec: " + repr(plan.fetch)
    for spec in incoming_specs:
        values = {e.get("canonical_code") or e.get("raw") for e in spec.entities}
        assert "14.09.2026 Container Status 2026.xlsx" not in values, spec.entities


def test_set_page_is_read_only_by_the_answer_to_how_many():
    """(c) No paging (owner ruling, 26 Sep 2026). `focus.set_page` is the set a too-long
    counted answer asked "how many should I show?" about, and the one turn that reads
    it is that question's answer: a count (`top_n`) naming no new subject lists that
    many of the SAME set. A "more" (`continuation`) pages nothing, and every turn -
    the count's answer included - leaves nothing carried behind it.
    """
    carried_set_page = {
        "set_key": {"domain": "product_attachment", "kind": "certificate"},
    }
    policy = build_policy()

    # Read: the count answers the carried set, once.
    count_state = State(focus=Focus(set_page=dict(carried_set_page)), pending=None, profile=Profile())
    new_count_state, count_plan = apply(count_state, verdict(top_n=3, continuation=True), policy)

    assert len(count_plan.fetch) == 1, count_plan.fetch
    assert count_plan.fetch[0].filters.get("set_page") == carried_set_page
    assert count_plan.fetch[0].filters.get("top_n") == 3
    assert new_count_state.focus.set_page is None

    # A "more" is not a count: nothing is paged, and the carry is closed.
    more_state = State(focus=Focus(set_page=dict(carried_set_page)), pending=None, profile=Profile())
    new_more_state, more_plan = apply(
        more_state, verdict(message_type="clarification", user_goal="more", continuation=True), policy
    )
    assert not any(isinstance(s.filters.get("set_page"), dict) for s in more_plan.fetch), more_plan.fetch
    assert new_more_state.focus.set_page is None

    # Drop: an ordinary, non-continuation NEW_ASK for an unrelated product must not
    # carry the stale page position forward.
    drop_state = State(
        focus=Focus(set_page=dict(carried_set_page)), pending=None, profile=Profile()
    )
    new_ask_verdict = verdict(entities=[entity("SRTWT7445", "product")], domain_hint="incoming")
    resolved_srtwt7445 = {
        "raw": "SRTWT7445",
        "canonical_code": "SRTWT7445",
        "uuid": "srtwt7445-uuid",
        "current_message": True,
        "confident": True,
    }

    new_drop_state, drop_plan = apply(
        drop_state, new_ask_verdict, policy, candidates={"product": [resolved_srtwt7445]}
    )

    assert drop_plan.fetch, "setup did not exercise a real fetch: " + repr(drop_plan)
    assert new_drop_state.focus.set_page is None, (
        "a plain new ask (a list answer, not a continuation) must drop the stale "
        f"set_page carry, not ride it forward: {new_drop_state.focus.set_page!r}"
    )
