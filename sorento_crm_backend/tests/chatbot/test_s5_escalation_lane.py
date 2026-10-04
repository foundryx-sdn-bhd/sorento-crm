"""S5 - the escalation lane (`branch_kind == out_of_scope`). AC-501 to AC-507.

RED, written before `app/services/chatbot/lanes/escalation.py` exists (Phase 2, test-first).
Everything below fails today with `ModuleNotFoundError: No module named
'app.services.chatbot.lanes'` (or, for the engine test, `AttributeError` from
`monkeypatch.setattr` naming an attribute `engine.py` does not have yet) - the right reason,
per the brief.

**Correction (5 Sep 2026, live captures + live node bodies).** The FIRST pass at this file
was ported from the `sub-escalation-live` EXPORT, which carries uncommitted edits from
another lane. The live workflow (`fr2u3e6FKg52cPvK` @ version `bac9613b`, 10 nodes,
confirmed by 66 fresh captures) is SIMPLER:

    escalation-input -> escalation-context -> clarify-company-gate (If)
        -> [true]  clarify-company-reply -> escalation-result {arm: clarify}
        -> [false] Call 'sub-human-intervention'  -> escalation-result {arm: human-intervention}

There is NO `fresh-entity-gate`, NO `clarify-team-gate` / `clarify-team-reply`, NO
`restore-item` on live - those are stale riders from an unpromoted build (B-TEAM-1' /
B-HB-1, still in the plan's hazard table as "port the fix S5", not yet promoted). Consequences
for this file, verified against the live node bodies (shas recorded in the scratchpad
`live-bodies/INDEX.txt` this correction was built from):

* `escalation-context.live.js`'s ladder has FIVE outcomes, no `gate` parameter anywhere:
  `picked_member` -> `company_pick` -> `sameTeam` (three sub-arms: `prior_state` /
  `prior_state_no_company` / `multi_company_unpicked`, the last from `routing_roster_plan`
  or `routing_companies` holding more than one row) -> `stated_brand` -> `none`. It computes
  `team = (o.routing || {}).suggested_team || null` with NO team default at this layer.
* `escalation-context` never calls a resolver: `resolve_and_gate` is a service the lane
  keeps in its seam for the day B-HB-1 promotes, but on live it is NEVER invoked (H26 stays
  open - noted, not fixed, by this slice).
* A null `routing.suggested_team` is not actually reachable through the real pipeline today:
  the ALREADY-PORTED parser (`head/output_exchange.py`'s `derive_routing` nullish chain)
  hard-defaults `suggested_team` to `"customer_service"` before escalation-context ever sees
  it ("Both chains end in a HARD default, which is why this body never emits a null team" -
  `output_exchange.py`'s own comment). That is the live mechanism the correction calls "the
  LIVE default applies" - it lives in the parser, not in this lane, and it is why H27
  (no hard default) stays open on live: nothing here asks a clarifying question, a team
  simply never arrives null in practice. Feeding the lane a null team directly (as the tests
  below still do, to test the LANE'S OWN behaviour at its input boundary) is therefore
  testing state the real pipeline does not produce today - which is exactly why the
  "would ask" tests are `xfail(strict=True)`, not deleted: B-TEAM-1' promoting adds
  `clarify-team-gate` for real, and `strict=True` makes that promotion NOTICEABLE (an
  unexpected pass fails the suite until the `xfail` marker is removed).
* `clarify-company-reply.live.js` has no `gate`/no-roster branch either - one clarify-text
  shape, not two.
* `escalation-result.live.js` checks `clarify-company-reply` only - no `clarify-team-reply`
  branch to fall through to.
* The assignment path (`live-sub-human-intervention@ae310ea1`, the same `test-guard` / `get-
  round-robin-assignee` / `if-conversation-unassigned` / `Assign or unassign a Conversation1`
  / `conversation-sla-tracking-create` / `Call 'sub-add-comment-respond'` /
  `sorento-sub-respond-sendmsg-respond-routed-to-pic{,2}` bodies) is IDENTICAL to what the
  first pass already encoded - it has no Code nodes to differ. `test_assignment_actions_in_order`
  and its siblings are unchanged. `test-guard` sits before `sorento-sub-respond-sendmsg-
  respond-routed-to-pic2` and everything after it, which is the H37 gate D14 reproduces:
  nothing side-effecting runs before the dry-run check.

Ported nodes, source read-only from
`sorento_crm_n8n/n8n-workflows-init/export/sub-escalation-live/` (topology only - the Code
node BODIES there are the stale export and are NOT used to derive expectations) and the
live bodies fetched separately: `escalation-input`, `escalation-context`, `clarify-company-
gate` (an `If`, no Code body), `clarify-company-reply`, `escalation-result`, and the
assignment path inside `sub-human-intervention`.

**This file PINS a contract the plan states only at the `run()` level. The exact function
names below are this tester's design decision, made explicit here so the coder implements
against them rather than guessing:**

    app/services/chatbot/lanes/escalation.py
        escalation_input(trigger) -> dict                     # port of escalation-input.js
        escalation_context(item, *, ctx) -> dict               # port of escalation-context.js (live)
        clarify_company_reply(item, *, ctx) -> dict            # port of clarify-company-reply.js (live)
        escalation_result(*, clarify_team=None, clarify_company=None) -> dict
                                                                # port of escalation-result.js;
                                                                # `clarify_team` is always None on
                                                                # live (kept for B-TEAM-1' promotion)
        run(ctx, item, *, services, dry_run=False) -> dict
            -> {"arm": "clarify" | "human-intervention", "clarify": dict | None,
                "actions": list[dict], "pending": dict | None}

    `services` is a duck-typed namespace (see `_services()` below) with four callables:
    `resolve_and_gate(ctx, item)`, `next_assignee(body)`, `sla_create(body)`, `team_members(...)`.
    On live, `run()` NEVER calls `resolve_and_gate` (no fresh-entity-gate to trigger it) and
    NEVER calls anything side-effecting when `dry_run` is True (H37: the dry-run check runs
    BEFORE any of them, never after - the live `test-guard` If is itself first).

    `app/services/chatbot/engine.py` imports `run` under the name `run_escalation_lane`
    (the same "import the function by name into engine's namespace so a test can monkeypatch
    `engine_mod.<name>`" pattern the file already uses for `check_access` / `default_space_id` /
    `post_process` / `suggest_follow_up`) and calls it when `branch_kind == "out_of_scope"`
    AND `"out_of_scope"` is in `system_settings.chatbot_completed_lanes` (contract addition,
    5 Sep 2026: a JSON list column, default `[]`). With the lane's name absent from that list
    (the default), the turn keeps pre-S5 behaviour - delegated to n8n, `run_escalation_lane`
    never called - even though `"out_of_scope"` is (or will be) in
    `contracts.CRM_COMPLETED_BRANCH_KINDS`; the branch-kind set says the CRM CAN finish the
    lane, the settings list says whether it currently DOES, per tenant. `system_settings_row`
    (`tests/chatbot/conftest.py`) is the seeding seam for all three engine tests below.

`pending` is a plain dict, not yet required to validate against `contracts.Pending`.

Nothing here reaches an LLM, n8n, respond.io, or a real MCP server.
"""
from __future__ import annotations

import re
from typing import Any
from unittest.mock import Mock

import pytest

from app.services.chatbot.lanes.escalation import ESCALATION_TEAMS  # was contracts.SUGGESTED_TEAMS (AC-1594)
from tests.chatbot import _corpus

_XFAIL_CLARIFY_CHECKS_INHERITED_TEAM = (
    "D1's clarify checks the CURRENT (already-inherited) team, never whether it was "
    "freshly named this turn vs inherited from a stale offer - silently re-assigns "
    "instead of asking (follow-up, PR #952)"
)

# --------------------------------------------------------------------------- #
# Shared builders
# --------------------------------------------------------------------------- #


def _ctx(
    *,
    routing: dict | None = None,
    escalation: dict | None = None,
    query_brands: list | None = None,
    entities: list | None = None,
    prev_variables: dict | None = None,
    contact_phone: str = "+60120000527",
    contact_id: str = "ZZT-esc-1",
    current_assignee: str | None = None,
    person_mention: str | None = None,
    text: str | None = None,
    parser_raw: dict | None = None,
) -> dict:
    """The six-key hub shape `build_ctx` produces, filled in with what the escalation lane
    reads: `ctx.parse.output.{routing,escalation,query_brands,entities,person_mention}` and
    `ctx.session.session_vars.variables` (the ladder's `prev`).

    `person_mention` is the parser's already-emitted key (`head/parser.py:130` - a required
    output field, not new machinery) - item L's owner ruling reads it here."""
    contact: dict[str, Any] = {"id": contact_id, "phone": contact_phone}
    if current_assignee is not None:
        contact["assignee"] = {"id": current_assignee}
    return {
        "contact": contact,
        "text": {
            "message": {
                "messageId": "ZZT-esc-msg-1",
                "message": {"type": "text", "text": text or "help"},
            }
        },
        "session": {"session_vars": {"variables": prev_variables or {}}},
        "parse": {
            # `_parser_raw` is the pre-derivation snapshot `output_exchange` keeps and
            # `engine` puts on `ctx.parse`; the lane reads the team off it because the
            # DERIVED routing has already inherited the previous turn's (AC-815).
            **({"_parser_raw": parser_raw} if parser_raw is not None else {}),
            "output": {
                "routing": routing or {"suggested_team": None, "suggested_agent": None},
                "escalation": escalation or {"is_escalation_confirmation": True, "company_pick": None},
                "query_brands": query_brands or [],
                "entities": entities or [],
                "person_mention": person_mention,
            }
        },
        "access": {"allowed": True, "decision": "allow"},
        "media": None,
    }


def _item(*, branch_kind: str = "out_of_scope", **overrides: Any) -> dict:
    """`route-turn`'s output item for `out_of_scope` - `{**access, branch_kind}` (it is
    NOT a tag-only arm), which is also `escalation-input`'s own output verbatim."""
    base = {
        "allowed": True,
        "decision": "allow",
        "agent_name": "General Enquiries",
        "attributes": None,
        "all_attributes_allowed": None,
        "branch_kind": branch_kind,
    }
    base.update(overrides)
    return base


def _services(
    *,
    gate: dict | None = None,
    assignee: dict | None = None,
    sla: dict | None = None,
    members: list | None = None,
):
    """A `services` seam with recorded calls, never touching a database or the network.

    `resolve_and_gate` stays in the seam for the day B-HB-1 / B-TEAM-1' promotes (see the
    module docstring); on live it is asserted NEVER called."""
    default_assignee = {
        "assignee_id": "usr-pic-1",
        "assignee_email": "pic@sorento.example",
        "assignee_name": "PIC One",
        "assignee_respond_user_id": "respond-usr-1",
        "team_set_code": "CS",
        "brand_code": None,
        "company_id": None,
        "is_already_assigned": False,
    }
    default_sla = {
        "id": "sla-row-1",
        "initiated_at": "2026-09-05T04:00:00+00:00",
        "due_at": "2026-09-05T08:00:00+00:00",
        "due_at_resolution": "2026-09-06T04:00:00+00:00",
    }
    return type(
        "Services",
        (),
        {
            "resolve_and_gate": Mock(return_value=gate),
            "next_assignee": Mock(return_value={**default_assignee, **(assignee or {})}),
            # The READ-ONLY twin of `next_assignee` (AC-507, amended 6 Sep 2026): a dry run
            # names the assignee the live turn would draw, without advancing the cursor.
            "preview_assignee": Mock(return_value={**default_assignee, **(assignee or {})}),
            "sla_create": Mock(return_value={**default_sla, **(sla or {})}),
            "team_members": Mock(return_value=members or []),
        },
    )()


def _kl_fmt(iso: str) -> str:
    """`DateTime.fromISO(x, {zone:'utc'}).setZone('Asia/Kuala_Lumpur').toFormat('yyyy-MM-dd
    HH:mm:ss')` - the exact Luxon call `Call 'sub-add-comment-respond'`'s `comment` input
    uses on `conversation-sla-tracking-create`'s `initiated_at` / `due_at` /
    `due_at_resolution` (live-bodies/live-sub-human-intervention@ae310ea1). MYT is UTC+8,
    no DST."""
    from datetime import datetime, timedelta, timezone

    dt = datetime.fromisoformat(iso).astimezone(timezone.utc) + timedelta(hours=8)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# AC-501: escalation-context's live precedence ladder (H26, H27)
# --------------------------------------------------------------------------- #


def _rank_case(case_id: str):
    """`(ctx_kwargs, expected)` for one rung of the LIVE ladder (no `gate` rank - see the
    module docstring; `resolved_entity` was never live and is dropped)."""
    if case_id == "picked_member":
        prev = {
            "last_result_set": [
                {"uuid": "member-9", "company_id": "c-sorento", "company_name": "Sorento", "brand_code": "sorento"}
            ]
        }
        escalation = {"is_escalation_confirmation": True, "preferred_assignee_id": "member-9", "company_pick": None}
        ctx_kwargs = dict(prev_variables=prev, escalation=escalation, routing={"suggested_team": "customer_service"})
        expected = {"brand_code": "sorento", "company_id": "c-sorento", "company_name": "Sorento", "routing_source": "picked_member"}
    elif case_id == "company_pick_name":
        prev = {
            "routing_roster_plan": [
                {"company_id": "c-mocha", "company_name": "Mocha", "brand_code": "mch"},
                {"company_id": "c-cabana", "company_name": "Cabana", "brand_code": "cbn"},
            ]
        }
        escalation = {"is_escalation_confirmation": True, "company_pick": "Mocha"}
        ctx_kwargs = dict(prev_variables=prev, escalation=escalation, routing={"suggested_team": "customer_service"})
        expected = {"brand_code": "mch", "company_id": "c-mocha", "company_name": "Mocha", "routing_source": "company_pick"}
    elif case_id == "company_pick_id":
        prev = {"routing_companies": [{"company_id": "c-cabana", "company_name": "Cabana", "brand_code": "cbn"}]}
        escalation = {"is_escalation_confirmation": True, "company_pick": "C-CABANA"}
        ctx_kwargs = dict(prev_variables=prev, escalation=escalation, routing={"suggested_team": "customer_service"})
        expected = {"brand_code": "cbn", "company_id": "c-cabana", "company_name": "Cabana", "routing_source": "company_pick"}
    elif case_id == "company_pick_code":
        prev = {"routing_companies": [{"company_id": "c-mocha", "company_name": "Mocha", "brand_code": "mch", "company_code": "MCH2"}]}
        escalation = {"is_escalation_confirmation": True, "company_pick": "mch2"}
        ctx_kwargs = dict(prev_variables=prev, escalation=escalation, routing={"suggested_team": "customer_service"})
        expected = {"brand_code": "mch", "company_id": "c-mocha", "company_name": "Mocha", "routing_source": "company_pick"}
    elif case_id == "company_pick_alias":
        prev = {"routing_companies": [{"company_id": "c-sorento", "company_name": "Sorento", "brand_code": "sorento"}]}
        escalation = {"is_escalation_confirmation": True, "company_pick": "srt"}
        ctx_kwargs = dict(prev_variables=prev, escalation=escalation, routing={"suggested_team": "customer_service"})
        expected = {"brand_code": "sorento", "company_id": "c-sorento", "company_name": "Sorento", "routing_source": "company_pick"}
    elif case_id == "multi_company_unpicked":
        # LIVE: no `gate` rank at all - this is `sameTeam`'s `rp.length > 1` sub-arm.
        prev = {
            "routing": {"suggested_team": "purchasing_certification"},
            "routing_roster_plan": [
                {"company_id": "c-a", "company_name": "A"},
                {"company_id": "c-b", "company_name": "B"},
            ],
        }
        ctx_kwargs = dict(prev_variables=prev, routing={"suggested_team": "purchasing_certification"})
        expected = {"brand_code": None, "company_id": None, "company_name": None, "routing_source": "multi_company_unpicked"}
    elif case_id == "sameTeam_prior_state":
        prev = {"routing": {"suggested_team": "marketing_product"}, "routing_roster_plan": [{"company_id": "c-zeta", "company_name": "Zeta", "brand_code": "z"}]}
        ctx_kwargs = dict(prev_variables=prev, routing={"suggested_team": "marketing_product"})
        expected = {"brand_code": "z", "company_id": "c-zeta", "company_name": "Zeta", "routing_source": "prior_state"}
    elif case_id == "stated_brand":
        ctx_kwargs = dict(routing={"suggested_team": "customer_service"}, query_brands=["mocha"])
        expected = {"brand_code": "mocha", "company_id": None, "company_name": None, "routing_source": "stated_brand"}
    elif case_id == "none":
        ctx_kwargs = dict(routing={"suggested_team": "customer_service"}, query_brands=[])
        expected = {"brand_code": None, "company_id": None, "company_name": None, "routing_source": "none"}
    else:  # pragma: no cover - guarded by the parametrize ids below
        raise ValueError(case_id)
    return ctx_kwargs, expected


RANK_IDS = [
    "picked_member",
    "company_pick_name",
    "company_pick_id",
    "company_pick_code",
    "company_pick_alias",
    "multi_company_unpicked",
    "sameTeam_prior_state",
    "stated_brand",
    "none",
]


@pytest.mark.parametrize("case_id", RANK_IDS)
def test_escalation_context_ladder(case_id: str) -> None:
    """AC-501: `brand_code` / `company_id` / `company_name` / `routing_source` match the
    LIVE `escalation-context.js`'s precedence exactly - five outcomes plus `none`, no `gate`
    rank (over the pool-identity variants company_pick shares: name / id / code / alias)."""
    from app.services.chatbot.lanes.escalation import escalation_context

    ctx_kwargs, expected = _rank_case(case_id)
    ctx = _ctx(**ctx_kwargs)
    item = _item()

    out = escalation_context(item, ctx=ctx)

    for key, value in expected.items():
        assert out[key] == value, f"{case_id}: {key} = {out.get(key)!r}, expected {value!r}"
    # The item is SPREAD, never replaced (escalation-context.js: `...$input.first().json`).
    assert out["branch_kind"] == "out_of_scope"
    assert out["allowed"] is True


@pytest.mark.xfail(strict=True, reason="pending B-TEAM re-port; not live at bac9613b")
def test_no_hard_default_team() -> None:
    """FUTURE (B-TEAM-1'): once `clarify-team-gate` promotes, a null team asks instead of
    silently defaulting. NOT live today - see `test_no_team_clarify_on_live` for what
    `bac9613b` actually does with the same input. `strict=True` so the promotion itself
    flips this test green and forces the marker's removal."""
    from app.services.chatbot.lanes.escalation import run

    ctx = _ctx(routing={"suggested_team": None, "suggested_agent": None})
    item = _item()
    services = _services()

    result = run(ctx, item, services=services)

    assert result["arm"] == "clarify"
    assert result["pending"] is not None
    assert result["pending"]["kind"] == "team_clarify"
    services.next_assignee.assert_not_called()
    services.team_members.assert_not_called()


def test_no_team_clarify_on_live_team_flows_through_unguarded() -> None:
    """H27 stays open on LIVE (`bac9613b`, 10 nodes): `escalation-context.live.js` applies
    no default (`team = o.routing.suggested_team || null` - null stays null) and there is no
    `clarify-team-gate` in the live graph to catch it, so a null team proceeds straight into
    the human-intervention arm, unguarded - never asking, never defaulting AT THIS LAYER.

    This input is synthetic at the lane's own boundary: in the real pipeline `routing.
    suggested_team` is never actually null by the time it gets here, because the ALREADY-
    PORTED parser (`head/output_exchange.py`'s nullish chain) hard-defaults it to
    `"customer_service"` first - "the LIVE default" the correction names lives one layer up,
    not in this lane. Both facts matter: the lane itself has no guard (H27 unfixed) AND the
    parser's own hard default is what actually prevents the gap from biting in practice."""
    from app.services.chatbot.lanes.escalation import run

    ctx = _ctx(routing={"suggested_team": None, "suggested_agent": "general_enquiries"})
    item = _item(brand_code=None, company_id=None, company_name=None, routing_source="none")
    services = _services()

    result = run(ctx, item, services=services)

    assert result["arm"] == "human-intervention"
    assert result["pending"] is None
    services.next_assignee.assert_called_once()
    body = services.next_assignee.call_args[0][0]
    assert body.get("team_code") in (None, "")


def test_clarify_company_ask_always_in_reply() -> None:
    """AC-505 / H2: the multi-company-unpicked case is ALWAYS answered with the clarify ask,
    in the SAME synchronous call that decided it needed one - the race cannot exist because
    there is no separate later step that could still be composing it."""
    from app.services.chatbot.lanes.escalation import run

    ctx = _ctx(
        routing={"suggested_team": "customer_service"},
        prev_variables={
            # Live's sameTeam gate is `!!(prev.routing && team && prev.routing.suggested_team
            # === team)` - without a `routing` key here `sameTeam` is false and the ladder
            # never reaches the `rp.length > 1` arm that produces `multi_company_unpicked`
            # (matches `_rank_case("multi_company_unpicked")` above).
            "routing": {"suggested_team": "customer_service"},
            "selection_context": "member_offer",
            "last_result_set": [{"uuid": "m1"}],
            "routing_roster_plan": [
                {"company_id": "c-mocha", "company_name": "Mocha"},
                {"company_id": "c-sorento", "company_name": "Sorento"},
            ],
        },
    )
    item = _item(brand_code=None, company_id=None, company_name=None, routing_source="multi_company_unpicked", team="customer_service")
    services = _services()

    result = run(ctx, item, services=services)

    assert result["arm"] == "clarify"
    assert result["pending"]["kind"] == "company_clarify"
    assert result["clarify"]["clarify_text"] == (
        "Both *Mocha* and *Sorento* teams are listed - reply a number, a name, or the "
        "company (*Mocha* / *Sorento*) and I'll assign automatically."
    )
    # The ask has to be an ACTION, not only `clarify_text`: the executor executes
    # `actions[]` and nothing else (plan section 5b), so a clarify that composed words and
    # returned an empty list left the customer in silence (prod turn 9dd4cd0f).
    assert [a["kind"] for a in result["actions"]] == ["send_message"]
    assert result["actions"][0]["text"] == result["clarify"]["clarify_text"]
    assert result["actions"][0]["quick_replies"] == "Mocha, Sorento"
    services.next_assignee.assert_not_called()


def test_clarify_gate_fires_on_multi_row_roster_with_no_member_offer_state() -> None:
    """Reviewer SF-1 (hand pass 11 final re-check): the multi-row-roster arm IS reachable
    on the n8n path, contrary to `_clarify_gate`'s old comment. `sub_answer.miss_roster_plan`
    caps ITS OWN producer's plan at one row, but `tail/member_offer.cs_roster_plan` (the
    OTHER producer of `variables.routing_roster_plan`) emits one row per
    `gate.routing_companies` entry, and `compile-current-state` persists that verbatim - so
    a session carrying a 2-row plan with NO `selection_context == "member_offer"` (its own
    member roster read came back empty) must still clarify, not fall through to a blind
    round robin. `test_clarify_company_ask_always_in_reply` above also carries
    `selection_context: "member_offer"`, so it cannot tell the two arms apart; this ctx is
    the SAME shape `test_escalation_context_ladder`'s `multi_company_unpicked` rank case
    uses, run through the lane instead of just `escalation_context`."""
    from app.services.chatbot.lanes.escalation import run

    ctx = _ctx(
        routing={"suggested_team": "customer_service"},
        prev_variables={
            "routing": {"suggested_team": "customer_service"},
            "routing_roster_plan": [
                {"company_id": "c-mocha", "company_name": "Mocha"},
                {"company_id": "c-sorento", "company_name": "Sorento"},
            ],
        },
    )
    item = _item(brand_code=None, company_id=None, company_name=None, routing_source="multi_company_unpicked", team="customer_service")
    services = _services()

    result = run(ctx, item, services=services)

    assert result["arm"] == "clarify"
    assert result["pending"] is not None
    assert result["pending"]["kind"] == "company_clarify"
    services.next_assignee.assert_not_called()


# --------------------------------------------------------------------------- #
# AC-502: the assignment path, in order (unchanged - `sub-human-intervention` has no
# Code nodes, so the live bodies are byte-identical to the first pass)
# --------------------------------------------------------------------------- #


def _assignment_ctx_and_item() -> tuple[dict, dict]:
    ctx = _ctx(routing={"suggested_team": "customer_service", "suggested_agent": "general_enquiries"})
    item = _item(brand_code=None, company_id=None, company_name=None, routing_source="none", team="customer_service")
    return ctx, item


def test_assignment_actions_in_order() -> None:
    """AC-502: the four actions, in the order the caller must execute them, plus the
    out-of-scope acknowledgement text (`escalate-catalog`'s `includeResponse: false` state
    text, 'Informed the user that request is out of scope...') is NOT one of them - that
    text belongs to the S2 tail's session bookkeeping, not to anything the S5 lane sends."""
    from app.services.chatbot.lanes.escalation import run

    ctx, item = _assignment_ctx_and_item()
    sla_result = {
        "id": "sla-row-1",
        "initiated_at": "2026-09-05T04:00:00+00:00",
        "due_at": "2026-09-05T08:00:00+00:00",
        "due_at_resolution": "2026-09-06T04:00:00+00:00",
    }
    services = _services(
        assignee={"assignee_respond_user_id": "respond-usr-7", "assignee_id": "usr-7", "is_already_assigned": False},
        sla=sla_result,
    )

    result = run(ctx, item, services=services)

    assert result["arm"] == "human-intervention"
    assert result["clarify"] is None
    kinds = [a["kind"] for a in result["actions"]]
    assert kinds == ["send_message", "assign_conversation", "add_comment", "send_message"]

    first_send, assign, comment, second_send = result["actions"]
    assert "out of the scope" in first_send["text"].lower()
    assert assign["respond_user_id"] == "respond-usr-7"
    # `Call 'sub-add-comment-respond'`'s own `user_id` input is
    # `$('get-round-robin-assignee').first().json.assignee_respond_user_id` - the RESPOND
    # id, not the CRM `assignee_id` - because the comment endpoint it calls resolves respond
    # ids, exactly like `Assign or unassign a Conversation1`'s `assigneeUserId` above. The
    # comment sub takes a SINGLE `user_id`, so the action's list is always one element.
    assert comment["mention_user_ids"] == ["respond-usr-7"]
    # `Call 'sub-add-comment-respond'`'s own `comment` input, verbatim (live-bodies/
    # live-sub-human-intervention@ae310ea1/Call_sub-add-comment-respond_.node.json):
    # `Team: {team}\n⏰ SLA Alert: This contact is routed to you at {initiated_at MYT}.\n
    # You have until {due_at MYT} to respond.\nYou have until {due_at_resolution MYT} to
    # resolve.\nReference message: https://app.respond.io/space/364817/inbox/{contact_id}
    # #{message_id}` - `initiated_at`/`due_at`/`due_at_resolution` off THIS turn's
    # `sla_create` result, converted UTC -> Asia/Kuala_Lumpur.
    expected_comment = (
        f"Team: {item['team']}\n"
        f"⏰ SLA Alert: This contact is routed to you at {_kl_fmt(sla_result['initiated_at'])}.\n"
        f"You have until {_kl_fmt(sla_result['due_at'])} to respond.\n"
        f"You have until {_kl_fmt(sla_result['due_at_resolution'])} to resolve.\n"
        "Reference message: https://app.respond.io/space/364817/inbox/"
        f"{ctx['contact']['id']}#{ctx['text']['message']['messageId']}"
    )
    assert comment["text"] == expected_comment
    assert "routed" in second_send["text"].lower() and "customer service" in second_send["text"].lower()

    for action in result["actions"]:
        assert "Informed the user that request is out of scope" not in str(action)

    services.next_assignee.assert_called_once()
    body = services.next_assignee.call_args[0][0]
    assert set(body) == {
        "agent_code",
        "team_code",
        "contact_phone_number",
        "policy_code",
        "preferred_assignee_id",
        "tier",
        "brand_code",
        "company_id",
    }
    assert body["team_code"] == "customer_service"
    assert body["agent_code"] == "general_enquiries"
    assert body["contact_phone_number"] == ctx["contact"]["phone"]
    assert body["policy_code"] == "NORMAL"
    assert body["tier"] == 1

    services.sla_create.assert_called_once()
    sla_body = services.sla_create.call_args[0][0]
    assert isinstance(sla_body, dict)
    assert sla_body.get("contact_phone_number") == ctx["contact"]["phone"]


def test_conversation_already_assigned_skips_assign() -> None:
    """AC-502: `if-conversation-unassigned`'s false leg - already assigned in respond.io,
    so no `assign_conversation`, but the SLA clock still starts."""
    from app.services.chatbot.lanes.escalation import run

    ctx, item = _assignment_ctx_and_item()
    services = _services(assignee={"is_already_assigned": True})

    result = run(ctx, item, services=services)

    kinds = [a["kind"] for a in result["actions"]]
    assert "assign_conversation" not in kinds
    assert kinds == ["send_message", "add_comment", "send_message"]
    services.sla_create.assert_called_once()


# --------------------------------------------------------------------------- #
# AC-503 / H37: dry run never reaches a side-effecting seam ("test-guard" on live)
# --------------------------------------------------------------------------- #


def test_dry_run_never_reaches_next_assignee(session_factory) -> None:
    """D14 evaluated FIRST (H37: n8n called next-assignee and guarded afterwards - live's
    own `test-guard` If sits before `sorento-sub-respond-sendmsg-respond-routed-to-pic2` and
    everything after it, which is exactly this ordering).

    Ruling (5 Sep 2026, AC-503/AC-507, AMENDED 6 Sep 2026): a dry run still RETURNS all four
    would-be actions, in order, with `dry_run: true` and PREVIEW placeholders in place of
    whatever only a WRITING seam could produce - the comment text carries the literal
    `<preview>` marker in place of the SLA timestamps (no `conversation-sla-tracking-create`
    row exists to read them from), and every action is flagged `preview: true`. This is what
    lets a dry-run turn's trace/preview UI show the customer AND the CRM exactly what would
    happen, not just that something would.

    The amendment (owner console pass, 6 Sep 2026): the ASSIGNEE is no longer a null
    placeholder. `preview_assignee` reads the same pool and the same cursor as the live draw
    and advances neither, so the preview names who the live turn WOULD pick. What this test
    pins is therefore no longer "nobody was named" but the thing that actually matters: the
    round-robin cursor did not move and no SLA row exists.

    Row counts on the real (blank) Postgres schema are the second net for H37 itself: even
    if the coder's `run()` bypassed the injected `services` and called a real production
    function directly, that would show up here as a nonzero count, where a call-count
    assertion on the mock alone would not catch it."""
    from app.models.access import AgentTeamRoundRobinCursor
    from app.models.sla import ConversationSLATracking
    from app.services.chatbot.lanes.escalation import run

    ctx, item = _assignment_ctx_and_item()
    services = _services()

    result = run(ctx, item, services=services, dry_run=True)

    kinds = [a["kind"] for a in result["actions"]]
    assert kinds == ["send_message", "assign_conversation", "add_comment", "send_message"]
    assert all(a.get("dry_run") is True for a in result["actions"])

    _first_send, assign, comment, _second_send = result["actions"]
    assert assign["respond_user_id"] == "respond-usr-1", (
        "the preview names the assignee the live turn would draw (AC-507 as amended)"
    )
    assert assign.get("preview") is True
    assert comment["mention_user_ids"] == ["respond-usr-1"]
    assert comment.get("preview") is True
    assert "<preview>" in comment["text"]

    # The WRITING seams stay untouched: the preview is a read.
    services.next_assignee.assert_not_called()
    services.sla_create.assert_not_called()

    db = session_factory()
    assert db.query(AgentTeamRoundRobinCursor).count() == 0, (
        "a dry run must not create or advance a round-robin cursor row"
    )
    assert db.query(ConversationSLATracking).count() == 0


# --------------------------------------------------------------------------- #
# AC-504: node replay against the LIVE captures (workflow fr2u3e6FKg52cPvK @ bac9613b)
# --------------------------------------------------------------------------- #

_corpus.NODE_SLUGS.setdefault("escalation-input", ("sub-escalation-live",))
_corpus.NODE_SLUGS.setdefault("escalation-context", ("sub-escalation-live",))
_corpus.NODE_SLUGS.setdefault("clarify-company-reply", ("sub-escalation-live",))
_corpus.NODE_SLUGS.setdefault("escalation-result", ("sub-escalation-live",))


def _run_escalation_input(fixture: _corpus.Fixture) -> list:
    from app.services.chatbot.lanes.escalation import escalation_input

    trigger = fixture.first("When Executed by Another Workflow")
    return [{"json": escalation_input(trigger)}]


def _run_escalation_context(fixture: _corpus.Fixture) -> list:
    from app.services.chatbot.lanes.escalation import escalation_context

    item = (fixture.input[0] or {}).get("json") or {}
    ctx = fixture.first("build-ctx")["ctx"]
    return [{"json": escalation_context(item, ctx=ctx)}]


def _run_clarify_company_reply(fixture: _corpus.Fixture) -> list:
    from app.services.chatbot.lanes.escalation import clarify_company_reply

    item = (fixture.input[0] or {}).get("json") or {}
    ctx = fixture.first("build-ctx")["ctx"]
    return [{"json": clarify_company_reply(item, ctx=ctx)}]


def _run_escalation_result(fixture: _corpus.Fixture) -> list:
    from app.services.chatbot.lanes.escalation import escalation_result

    # `clarify-team-reply` never runs on live (no such node); this stays wired for the day
    # B-TEAM-1' promotes and the node reappears in a capture.
    clarify_team = fixture.first("clarify-team-reply") if fixture.upstream("clarify-team-reply") else None
    clarify_company = fixture.first("clarify-company-reply") if fixture.upstream("clarify-company-reply") else None
    return [{"json": escalation_result(clarify_team=clarify_team, clarify_company=clarify_company)}]


_S5_RUNNERS = {
    "escalation-input": _run_escalation_input,
    "escalation-context": _run_escalation_context,
    "clarify-company-reply": _run_clarify_company_reply,
    "escalation-result": _run_escalation_result,
}


def _s5_fixtures() -> list[_corpus.Fixture]:
    out: list[_corpus.Fixture] = []
    for node in _S5_RUNNERS:
        out.extend(_corpus.vendored(node))
        out.extend(_corpus.full_corpus(node))
    return out


@pytest.mark.parametrize(
    "fixture", _corpus.graded(_s5_fixtures()), ids=lambda f: f"{f.node}/{f.name}"
)
def test_replay(fixture: _corpus.Fixture) -> None:
    """AC-504: every real capture for the four live S5 nodes, byte-equal after a JSON round
    trip. `clarify-company-reply` has zero real captures in this batch (the clarify arm was
    never exercised in the capture window - honestly reported, not fabricated); its
    parametrize list is simply empty until a capture exists."""
    actual = _corpus.json_round_trip(_S5_RUNNERS[fixture.node](fixture))
    expected = _corpus.json_round_trip(fixture.expected)
    assert actual == expected, (
        f"{fixture.node}/{fixture.name} diverges from the captured n8n output\n"
        f"fixture: {fixture.path}"
    )


def test_reasoned_fixtures_are_replayed_and_reported(capsys) -> None:
    rows = _corpus.reasoned(_s5_fixtures())
    if not rows:
        pytest.skip("no `reasoned` fixtures for the S5 nodes on this corpus")
    agree, differ = [], []
    for fixture in rows:
        actual = _corpus.json_round_trip(_S5_RUNNERS[fixture.node](fixture))
        expected = _corpus.json_round_trip(fixture.expected)
        (agree if actual == expected else differ).append(f"{fixture.node}/{fixture.name}")
    with capsys.disabled():
        print(f"\n  S5 reasoned fixtures: {len(rows)} replayed, {len(agree)} agree, {len(differ)} differ")


# --------------------------------------------------------------------------- #
# fresh-entity-gate (H26 - NOT live at bac9613b; kept for the B-HB-1 promotion)
# --------------------------------------------------------------------------- #


@pytest.mark.xfail(strict=True, reason="pending B-TEAM re-port; not live at bac9613b")
def test_fresh_entity_gate_calls_resolve() -> None:
    """FUTURE (B-HB-1): once `fresh-entity-gate` promotes, a fresh entity on this turn
    resolves and gates before the ladder runs. NOT live today - see
    `test_no_resolve_call_on_live_escalation_lane` for what `bac9613b` actually does.
    `strict=True` so the promotion flips this test green and forces the marker's removal."""
    from app.services.chatbot.lanes.escalation import run

    ctx_with_entity = _ctx(
        routing={"suggested_team": "customer_service"},
        entities=[{"raw": "widget", "hint": "product", "current_message": True}],
    )
    services = _services(gate=None)
    run(ctx_with_entity, _item(), services=services)
    services.resolve_and_gate.assert_called_once()


def test_no_resolve_call_on_live_escalation_lane() -> None:
    """H26 stays open on live: `bac9613b` has no `fresh-entity-gate` / `Call 'sub-resolve-
    and-gate'` anywhere in the graph, so the escalation lane never calls the resolver -
    brand-blind routing is not fixed by this slice, it is reproduced and noted. A fresh
    entity on the turn changes nothing about this: `resolve_and_gate` is never called
    either way."""
    from app.services.chatbot.lanes.escalation import run

    with_entity = _services(gate=None)
    run(
        _ctx(routing={"suggested_team": "customer_service"}, entities=[{"raw": "widget", "hint": "product", "current_message": True}]),
        _item(),
        services=with_entity,
    )
    with_entity.resolve_and_gate.assert_not_called()

    without_entity = _services(gate=None)
    run(_ctx(routing={"suggested_team": "customer_service"}, entities=[]), _item(), services=without_entity)
    without_entity.resolve_and_gate.assert_not_called()


# --------------------------------------------------------------------------- #
# R3: the pending marker replaces the frozen-string read
# --------------------------------------------------------------------------- #


@pytest.mark.xfail(strict=True, reason="pending B-TEAM re-port; not live at bac9613b")
def test_pending_marker_written_for_team_clarify() -> None:
    """FUTURE (B-TEAM-1'): a `team_clarify` pending marker. NOT live - see
    `test_no_team_clarify_on_live_team_flows_through_unguarded`."""
    from app.services.chatbot.lanes.escalation import run

    team_ctx = _ctx(routing={"suggested_team": None, "suggested_agent": None})
    team_result = run(team_ctx, _item(), services=_services())
    assert team_result["pending"]["kind"] == "team_clarify"


def test_pending_marker_written_for_company_clarify_and_none_for_assignment() -> None:
    """R3, the live half: `company_clarify` lands in `pending`; a plain assignment writes
    none."""
    from app.services.chatbot.lanes.escalation import run

    company_ctx = _ctx(
        routing={"suggested_team": "customer_service"},
        prev_variables={
            # See test_clarify_company_ask_always_in_reply: `routing` must be present or
            # live's `sameTeam` gate never fires and the ladder cannot reach
            # `multi_company_unpicked`.
            "routing": {"suggested_team": "customer_service"},
            "selection_context": "member_offer",
            "last_result_set": [{"uuid": "m1"}],
            "routing_roster_plan": [
                {"company_id": "c-mocha", "company_name": "Mocha"},
                {"company_id": "c-sorento", "company_name": "Sorento"},
            ],
        },
    )
    company_item = _item(brand_code=None, company_id=None, company_name=None, routing_source="multi_company_unpicked", team="customer_service")
    company_result = run(company_ctx, company_item, services=_services())
    assert company_result["pending"]["kind"] == "company_clarify"

    assignment_ctx, assignment_item = _assignment_ctx_and_item()
    assignment_result = run(assignment_ctx, assignment_item, services=_services())
    assert assignment_result["pending"] is None


# --------------------------------------------------------------------------- #
# D11: semantic in, deterministic processing - no regex over the raw envelope
# --------------------------------------------------------------------------- #


def test_no_raw_text_regex_in_lane() -> None:
    """D11: everything after the parser works on STRUCTURED state. `_CO_ALIASES` may exist
    (it does in the JS - a stopgap company-code/alias map) but it is applied to
    `escalation.company_pick`, the parser's OWN output, never to `ctx.text` / `ctx['text']`,
    the customer's raw message."""
    import inspect

    import app.services.chatbot.lanes.escalation as escalation_mod

    source = inspect.getsource(escalation_mod)
    for banned in ('ctx["text"]', "ctx['text']", '.get("text")', ".get('text')"):
        assert banned not in source, f"lane reads the raw envelope directly via {banned!r}"

    for lineno, line in enumerate(source.splitlines(), start=1):
        if re.search(r"\bre\.(match|search|findall|sub|compile)\(", line):
            assert "text" not in line.lower() or "clarify_text" in line, (
                f"line {lineno} looks like a regex touching raw text: {line.strip()!r}"
            )


# --------------------------------------------------------------------------- #
# Engine wiring: out_of_scope finishes the turn, delegate is None
# --------------------------------------------------------------------------- #


def test_out_of_scope_finishes_in_turn(session_factory, system_settings_row, monkeypatch) -> None:
    """`out_of_scope` moves from `DELEGATED_BRANCH_KINDS` to `CRM_COMPLETED_BRANCH_KINDS`
    (S5's whole point) ONLY when `"out_of_scope"` is in `system_settings.
    chatbot_completed_lanes` (JSON list, default `[]`) - see
    `test_out_of_scope_delegates_when_completed_lanes_is_default_empty` for the off case.
    Seeded here: the head's own stages (received/understood/access/routed) are unchanged,
    then the engine calls the lane at `looked_up` and composes the reply at `replied`;
    delegate becomes null and the row closes `done`.

    A seam failure fails the turn at `looked_up` with today's generic error reply and NO
    partial assignment - the lane's actions are either the complete set (assign + SLA
    together) or absent entirely, never assign without SLA, because a lane that raises
    partway through never returns an action list at all.

    Assumption pinned by this test: `engine.py` imports the lane's `run` function under the
    name `run_escalation_lane` (see the module docstring) - if `monkeypatch.setattr` here
    raises `AttributeError` naming a DIFFERENT missing attribute than that, the coder should
    rename the import to match rather than treat this as a design objection; the name itself
    is not part of any AC.
    """
    import json as _json

    from sqlalchemy import text

    from app.models.chatbot_turn import ChatbotTurn
    from app.models.user import SystemSetting
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.contracts import Envelope
    from app.services.chatbot.head import parser as parser_mod

    contact_id = "ZZT-esc-engine-1"
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": contact_id, "phone": "+60000000099", "sv": _json.dumps({"variables": {}})},
    )
    db.commit()

    setting = db.query(SystemSetting).filter(SystemSetting.id == system_settings_row.id).one()
    setting.chatbot_completed_lanes = ["out_of_scope"]
    db.commit()

    before_session_vars = db.execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
        {"c": contact_id},
    ).scalar()
    before_count = db.execute(
        text("SELECT count(*) FROM respond_contacts WHERE respond_io_id = :c"),
        {"c": contact_id},
    ).scalar()

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test"
        )

    escalation_qf = {
        "message_type": "request_for_help",
        "intent_hint": "check_product",
        "domain_hint": "master_products",
        "scope_intent": "specific",
        "is_affirmative": None,
        "user_goal": "wants a human",
        "access_levels": [],
        "broaden_axis": None,
        "date_mode": None,
        "date_filter_start": None,
        "date_filter_end": None,
        "match_mode": "and",
        "demand_qty": None,
        "entities": [],
        "entity_op": "replace_combine",
        "scope_exclusive": False,
        "requested_attributes": [],
        "contains_flyer": False,
        "reference_positions": [],
        "reference_target": None,
        "person_mention": None,
        "is_active": None,
        "order_status": None,
        "correction": False,
        "routing": {"suggested_team": "customer_service", "suggested_agent": "general_enquiries", "team_source": "inferred"},
        "escalation": {"is_escalation_confirmation": True, "company_pick": None},
    }

    def fake_parse(config, user_block):
        return escalation_qf

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", fake_parse)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True,
            "decision": "allow",
            "agent_name": "General Enquiries",
            "attributes": None,
            "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")

    lane_actions = [
        {"kind": "send_message", "text": "Your request is out of scope...", "dry_run": False},
        {"kind": "assign_conversation", "respond_user_id": "respond-usr-1", "dry_run": False},
        {"kind": "add_comment", "text": "Team: customer_service", "mention_user_ids": ["respond-usr-1"], "dry_run": False},
        {"kind": "send_message", "text": "This inquiry has been routed...", "dry_run": False},
    ]

    def fake_run_escalation_lane(ctx, item, *, dry_run=False, session_factory=None):
        return {"arm": "human-intervention", "clarify": None, "actions": lane_actions, "pending": None}

    monkeypatch.setattr(engine_mod, "run_escalation_lane", fake_run_escalation_lane)

    envelope = Envelope(
        contact={"id": contact_id, "phone": "+60000000099", "custom_fields": []},
        message={
            "event_type": "message.received",
            "contact": {"id": contact_id},
            "message": {
                "messageId": "ZZT-esc-engine-msg-1",
                "contactId": contact_id,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": "I need to speak to a human"},
            },
        },
    )

    result = engine_mod.run_turn(envelope, session_factory=session_factory)

    assert result.branch_kind == "out_of_scope"
    assert result.delegate is None
    assert result.status == "done"
    # The tail's compose step is free to ENRICH a `send_message` action to the full
    # transport-contract shape (`{text, quick_replies, result_set}`, plan "Transport
    # contract") - checked as a subset match, not literal equality, so a normalisation like
    # that isn't a false red.
    assert [a["kind"] for a in result.actions] == [a["kind"] for a in lane_actions]
    for actual, expected in zip(result.actions, lane_actions):
        for key, value in expected.items():
            assert actual.get(key) == value, f"action {key}: {actual.get(key)!r} != {value!r}"
    # `escalate-catalog`'s `includeResponse: false` state text is TAIL bookkeeping (written
    # to session_vars.response below), never a `send_message` action - see
    # test_assignment_actions_in_order's docstring for the same distinction at the lane
    # level.
    for action in result.actions:
        assert "Informed the user that request is out of scope" not in str(action)

    row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
    assert row.status == "done", row.error
    # `out_of_scope` runs the tail like every other CRM-completed lane (decision, 5 Sep
    # 2026): today's n8n routes it through `tag-out-of-scope` -> `sub-output`, which
    # persists the session (routing axes, the "Informed the user..." state text). `replied`
    # is where the lane's reply/actions are composed; `remembered` is the tail's session
    # write, one stage further. There is no `sent` stage - D9, the CRM never sends.
    assert row.stage == "remembered"
    # A sub-event row in `row.trace` has no `stage` key at all (only `kind`) -
    # `Trace.persisted()` interleaves them with stage records now (trace-shape theme).
    stage_records = [r for r in row.trace if r.get("stage")]
    stages = [r["stage"] for r in stage_records]
    assert stages == ["received", "understood", "access", "routed", "looked_up", "replied", "remembered"]
    assert all(r["status"] == "ok" for r in stage_records)

    # The session write itself: same contact row (no new insert), but the stored
    # session_vars actually changed - the tail wrote SOMETHING (routing axes and/or the
    # state text at minimum), not a no-op pass-through of the `{"variables": {}}` seed.
    after_count = session_factory().execute(
        text("SELECT count(*) FROM respond_contacts WHERE respond_io_id = :c"),
        {"c": contact_id},
    ).scalar()
    after_session_vars = session_factory().execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
        {"c": contact_id},
    ).scalar()
    assert after_count == before_count == 1
    assert after_session_vars != before_session_vars


def test_out_of_scope_dry_run_carries_session_patch_and_writes_nothing(
    session_factory, system_settings_row, monkeypatch
) -> None:
    """D14 extended to the tail: on a dry run the turn still runs the tail (so the reply and
    actions are the real ones the customer would get), but the WRITE is replaced by handing
    the would-be patch back on `TurnResponse.session_patch`, and `respond_contacts.
    session_vars` is untouched - same guarantee `TestDryRun` already proves for the head-only
    case in `test_engine.py`, now covering the tail S5 added."""
    import json as _json

    from sqlalchemy import text

    from app.models.chatbot_turn import ChatbotTurn
    from app.models.user import SystemSetting
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.contracts import Envelope
    from app.services.chatbot.head import parser as parser_mod

    contact_id = "ZZT-esc-engine-dry-1"
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": contact_id, "phone": "+60000000096", "sv": _json.dumps({"variables": {}})},
    )
    db.commit()

    setting = db.query(SystemSetting).filter(SystemSetting.id == system_settings_row.id).one()
    setting.chatbot_completed_lanes = ["out_of_scope"]
    db.commit()

    before_session_vars = db.execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
        {"c": contact_id},
    ).scalar()

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test"
        )

    escalation_qf = {
        "message_type": "request_for_help",
        "intent_hint": "check_product",
        "domain_hint": "master_products",
        "scope_intent": "specific",
        "is_affirmative": None,
        "user_goal": "wants a human",
        "access_levels": [],
        "broaden_axis": None,
        "date_mode": None,
        "date_filter_start": None,
        "date_filter_end": None,
        "match_mode": "and",
        "demand_qty": None,
        "entities": [],
        "entity_op": "replace_combine",
        "scope_exclusive": False,
        "requested_attributes": [],
        "contains_flyer": False,
        "reference_positions": [],
        "reference_target": None,
        "person_mention": None,
        "is_active": None,
        "order_status": None,
        "correction": False,
        "routing": {"suggested_team": "customer_service", "suggested_agent": "general_enquiries", "team_source": "inferred"},
        "escalation": {"is_escalation_confirmation": True, "company_pick": None},
    }

    def fake_parse(config, user_block):
        return escalation_qf

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", fake_parse)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True,
            "decision": "allow",
            "agent_name": "General Enquiries",
            "attributes": None,
            "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")

    # Ruling (5 Sep 2026, AC-503/AC-507): the same preview-placeholder shape
    # test_dry_run_never_reaches_next_assignee pins at the lane level - `assign_conversation`
    # has no real draw (`respond_user_id: null`) and `add_comment` has nobody to mention
    # (`mention_user_ids: []`), both flagged `preview: true`, and the comment text carries
    # the literal `<preview>` marker in place of SLA timestamps no `sla_create` call ever
    # produced. This engine test fakes the lane entirely, so it is reproduced here for the
    # stub to be a faithful preview of what the real lane returns.
    dry_lane_actions = [
        {"kind": "send_message", "text": "Your request is out of scope...", "dry_run": True},
        {"kind": "assign_conversation", "respond_user_id": None, "dry_run": True, "preview": True},
        {
            "kind": "add_comment",
            "text": "Team: customer_service\n<preview> SLA times are not computed on a dry run.",
            "mention_user_ids": [],
            "dry_run": True,
            "preview": True,
        },
        {"kind": "send_message", "text": "This inquiry has been routed...", "dry_run": True},
    ]

    def fake_run_escalation_lane(ctx, item, *, dry_run=False, session_factory=None):
        assert dry_run is True, "H37: dry_run must reach the lane, not be dropped on the way in"
        return {"arm": "human-intervention", "clarify": None, "actions": dry_lane_actions, "pending": None}

    monkeypatch.setattr(engine_mod, "run_escalation_lane", fake_run_escalation_lane)

    envelope = Envelope(
        contact={"id": contact_id, "phone": "+60000000096", "custom_fields": []},
        is_test=True,
        message={
            "event_type": "message.received",
            "contact": {"id": contact_id},
            "message": {
                "messageId": "ZZT-esc-engine-dry-msg-1",
                "contactId": contact_id,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": "I need to speak to a human"},
            },
        },
    )
    assert envelope.dry_run is True

    result = engine_mod.run_turn(envelope, session_factory=session_factory)

    assert result.status == "done"
    assert result.delegate is None
    assert [a["kind"] for a in result.actions] == ["send_message", "assign_conversation", "add_comment", "send_message"]
    assert all(a.get("dry_run") is True for a in result.actions)
    _first_send, assign, comment, _second_send = result.actions
    assert assign["respond_user_id"] is None
    assert comment["mention_user_ids"] == []
    assert result.session_patch is not None and isinstance(result.session_patch, dict)
    # The TOP-LEVEL flag, not just the per-action one. It read false on this lane
    # because the arm built its own TurnResult and never set it, so a caller that
    # switched on the response's `is_test` (which is what it is for) saw a live turn.
    assert result.is_test is True

    after_session_vars = session_factory().execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
        {"c": contact_id},
    ).scalar()
    assert after_session_vars == before_session_vars

    row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
    assert row.is_test is True
    assert row.stage == "remembered"


def test_out_of_scope_seam_failure_fails_at_looked_up_with_no_partial_assignment(
    session_factory, system_settings_row, monkeypatch
) -> None:
    """Same `chatbot_completed_lanes` seed as the happy path - without it the turn would
    just delegate (today's behaviour) and never reach the lane at all, which would make
    this test pass for the wrong reason."""
    import json as _json

    from sqlalchemy import text

    from app.models.chatbot_turn import ChatbotTurn
    from app.models.user import SystemSetting
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.contracts import Envelope
    from app.services.chatbot.head import parser as parser_mod

    contact_id = "ZZT-esc-engine-fail-1"
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": contact_id, "phone": "+60000000098", "sv": _json.dumps({"variables": {}})},
    )
    db.commit()

    setting = db.query(SystemSetting).filter(SystemSetting.id == system_settings_row.id).one()
    setting.chatbot_completed_lanes = ["out_of_scope"]
    db.commit()

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test"
        )

    escalation_qf = {
        "message_type": "request_for_help",
        "intent_hint": "check_product",
        "domain_hint": "master_products",
        "scope_intent": "specific",
        "is_affirmative": None,
        "user_goal": "wants a human",
        "access_levels": [],
        "broaden_axis": None,
        "date_mode": None,
        "date_filter_start": None,
        "date_filter_end": None,
        "match_mode": "and",
        "demand_qty": None,
        "entities": [],
        "entity_op": "replace_combine",
        "scope_exclusive": False,
        "requested_attributes": [],
        "contains_flyer": False,
        "reference_positions": [],
        "reference_target": None,
        "person_mention": None,
        "is_active": None,
        "order_status": None,
        "correction": False,
        "routing": {"suggested_team": "customer_service", "suggested_agent": "general_enquiries", "team_source": "inferred"},
        "escalation": {"is_escalation_confirmation": True, "company_pick": None},
    }

    def fake_parse(config, user_block):
        return escalation_qf

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", fake_parse)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True,
            "decision": "allow",
            "agent_name": "General Enquiries",
            "attributes": None,
            "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")

    def fake_run_escalation_lane_boom(ctx, item, *, dry_run=False, session_factory=None):
        raise RuntimeError("next-assignee is unreachable")

    monkeypatch.setattr(engine_mod, "run_escalation_lane", fake_run_escalation_lane_boom)

    envelope = Envelope(
        contact={"id": contact_id, "phone": "+60000000098", "custom_fields": []},
        message={
            "event_type": "message.received",
            "contact": {"id": contact_id},
            "message": {
                "messageId": "ZZT-esc-engine-fail-msg-1",
                "contactId": contact_id,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": "I need to speak to a human"},
            },
        },
    )

    result = engine_mod.run_turn(envelope, session_factory=session_factory)

    assert result.status == "failed"
    assert result.reply["text"] == parser_mod.PARSER_ERROR_REPLY
    assert all(a["kind"] != "assign_conversation" for a in result.actions)
    assert all(a["kind"] != "add_comment" for a in result.actions)

    row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
    assert row.status == "failed"
    assert row.stage == "looked_up"
    assert "next-assignee is unreachable" in row.error


# RETIRED (AC-1592, this session): `test_out_of_scope_delegates_when_completed_lanes_is_
# default_empty` pinned that `system_settings.chatbot_completed_lanes` defaulting to `[]`
# keeps `out_of_scope` delegated to n8n - `chatbot_completed_lanes` no longer gates
# completion at all (contract 73 superseded, `delegate.py` module docstring), and
# `out_of_scope` is in `CRM_COMPLETED_BRANCH_KINDS` unconditionally, so the escalation
# lane runs regardless of the row's contents today. No replacement named: the property
# (a lane can be held back by an empty settings list) is gone by design, not moved -
# same finding `test_s3_switch_and_complete_by_body.py::TestTheCompletedLaneSwitch`'s own
# retirement note makes independently.


# --------------------------------------------------------------------------- #
# Owner console defect L (owner ruling: "LOOKUP"). Evidence: "escalate to Nurain"
# (customer service staff) and "escalate to marketing" both arrived with
# `_parser_raw.routing = {suggested_team: null, suggested_agent: null}`, and the engine
# INHERITED the previous turn's team (marketing_product / purchasing) instead - `add_comment`
# named the wrong team.
#
# D11: the parser already emits `person_mention` (`head/parser.py:130`, a required output
# key) - the escalation lane reads it here and resolves it against a staff roster seam
# (pinned below as `services.staff_lookup(name) -> list[dict]`; the coder may rename or
# fold it into the already-declared-but-unwired `team_members` seam in
# `escalation_services.py` if that is the better fit - see that module's own "declared and
# NOT wired" note). This lane test does NOT touch a real staff table; the seam is mocked,
# same discipline as `next_assignee` / `sla_create` throughout this file.
#
# NOTE for the coder: the parser prompt's team vocabulary may need "marketing" mapped to
# `marketing_product` for part (c)'s bare "escalate to marketing" phrasing to even reach
# this lane with a recognisable, if unmapped, team WORD - that prompt change is OUTSIDE
# this lane and outside this test file; this suite only pins what the LANE does once a
# `person_mention` or an explicit-but-unmapped ask reaches it with `routing` null.
# --------------------------------------------------------------------------- #


class TestPersonMentionEscalationRoutesByStaffLookup:
    @staticmethod
    def _services_with_staff(hits: list[dict]):
        services = _services()
        services.staff_lookup = Mock(return_value=hits)
        return services

    def test_a_named_person_routes_to_their_team_as_preferred_assignee(self) -> None:
        """(a) `person_mention` resolves to exactly one staff member -> route to THEIR
        team, with them as the preferred/drawn assignee - never the inherited prior team."""
        from app.services.chatbot.lanes.escalation import run

        ctx = _ctx(
            routing={"suggested_team": None, "suggested_agent": None},
            person_mention="Nurain",
            prev_variables={
                "routing": {"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"}
            },
            text="escalate to Nurain",
        )
        item = _item(brand_code=None, company_id=None, company_name=None, routing_source="none")
        services = self._services_with_staff(
            [
                {
                    "team_code": "customer_service",
                    "team_name": "Customer Service",
                    "user_id": "usr-nurain",
                    "user_name": "Nurain Binti X",
                    "respond_user_id": "respond-usr-nurain",
                }
            ]
        )

        result = run(ctx, item, services=services)

        assert result["arm"] == "human-intervention"
        assign = next(a for a in result["actions"] if a["kind"] == "assign_conversation")
        comment = next(a for a in result["actions"] if a["kind"] == "add_comment")
        assert assign["respond_user_id"] == "respond-usr-nurain", (
            f"must route to Nurain's OWN team, never the inherited prior team: {assign!r}"
        )
        assert "Team: customer_service" in comment["text"], (
            f"the comment must name Nurain's team, not the inherited marketing_product: "
            f"{comment['text']!r}"
        )
        services.next_assignee.assert_not_called(), (
            "a named person is a direct pick, not a round-robin draw"
        )

    def test_two_staff_with_the_same_name_in_different_teams_clarifies_instead_of_guessing(
        self,
    ) -> None:
        """(b) An ambiguous name (two staff, two teams) must clarify with the team list,
        never silently pick one or inherit the prior team."""
        from app.services.chatbot.lanes.escalation import run

        ctx = _ctx(
            routing={"suggested_team": None, "suggested_agent": None},
            person_mention="Ali",
            prev_variables={
                "routing": {"suggested_team": "purchasing", "suggested_agent": "general_enquiries"}
            },
            text="escalate to Ali",
        )
        item = _item(brand_code=None, company_id=None, company_name=None, routing_source="none")
        services = self._services_with_staff(
            [
                {
                    "team_code": "customer_service",
                    "team_name": "Customer Service",
                    "user_id": "usr-ali-1",
                    "user_name": "Ali bin Abu",
                    "respond_user_id": "respond-ali-1",
                },
                {
                    "team_code": "purchasing",
                    "team_name": "Purchasing",
                    "user_id": "usr-ali-2",
                    "user_name": "Ali Hassan",
                    "respond_user_id": "respond-ali-2",
                },
            ]
        )

        result = run(ctx, item, services=services)

        assert result["arm"] == "clarify", (
            f"two staff sharing a name must clarify, not guess: {result!r}"
        )
        assert result["clarify"] is not None
        clarify_text = str(result["clarify"])
        assert "Customer Service" in clarify_text or "customer_service" in clarify_text
        assert "Purchasing" in clarify_text or "purchasing" in clarify_text
        assert not any(a["kind"] == "assign_conversation" for a in result["actions"]), (
            "an ambiguous name must never assign"
        )
        services.next_assignee.assert_not_called()

    def test_an_unmapped_explicit_team_word_clarifies_rather_than_inheriting_the_prior_team(
        self,
    ) -> None:
        """(c) No person_mention, routing null, the customer named a team in words the
        parser did not map - clarify with the team list, NEVER silently inherit whatever
        team the PREVIOUS turn happened to be routed to."""
        from app.services.chatbot.lanes.escalation import run

        ctx = _ctx(
            routing={"suggested_team": None, "suggested_agent": None},
            person_mention=None,
            # The parser's own word for this message under the amended contract (R-a,
            # console pass 4): "marketing" is not one of the eight catalogue teams, so it
            # is exactly the "explicit but unmapped" word this test is named for.
            parser_raw={"routing": {"suggested_team": "marketing", "suggested_agent": None}},
            # Not an acceptance - nothing was offered. `_ctx`'s default escalation says
            # confirmed, which the lane did not read until B1 (review of #706) and now
            # reads first; an "acceptance" is assigned, so the default would grade nothing.
            escalation={"is_escalation_confirmation": False, "company_pick": None},
            prev_variables={
                "routing": {"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"}
            },
            text="escalate to marketing",
        )
        item = _item(brand_code=None, company_id=None, company_name=None, routing_source="none")
        services = self._services_with_staff([])

        result = run(ctx, item, services=services)

        assert result["arm"] == "clarify", (
            "an explicit-but-unmapped team ask with no person_mention must clarify, not "
            f"silently inherit the previous turn's team: {result!r}"
        )
        assert not any(a["kind"] == "assign_conversation" for a in result["actions"])
        services.next_assignee.assert_not_called(), (
            "must never silently draw against the inherited prior team"
        )

    def test_the_owners_turn_asks_even_though_the_derived_routing_inherited_a_team(
        self,
    ) -> None:
        """The owner's turn, measured on the console run of 6 Sep 2026: "escalate to Nurain"
        arrived with `_parser_raw.routing = {suggested_team: null, suggested_agent: null}`
        while the DERIVED routing had already inherited `purchasing` from the previous turn.
        Gating the ask on the derived team would make this gate inert on exactly the turn it
        was written for - which is what the first console run showed."""
        from app.services.chatbot.lanes.escalation import run

        ctx = _ctx(
            routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
            person_mention="Nurain",
            parser_raw={"routing": {"suggested_team": None, "suggested_agent": None}},
            prev_variables={
                "routing": {"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"}
            },
            text="escalate to Nurain",
        )
        item = _item(
            brand_code=None, company_id=None, company_name=None, routing_source="none",
            team="purchasing",
        )
        services = self._services_with_staff([])  # nobody by that name on this install

        result = run(ctx, item, services=services)

        assert result["arm"] == "clarify", (
            "the parser routed this turn nowhere, so the inherited team must not be used: "
            f"{result!r}"
        )
        assert not any(a["kind"] == "assign_conversation" for a in result["actions"])
        services.next_assignee.assert_not_called()

    def test_a_person_mention_with_a_resolved_team_still_escalates_when_the_roster_misses(
        self,
    ) -> None:
        """Review of #700. 70 parse outputs in the corpus carry a `person_mention` NEXT TO a
        resolved `suggested_team` - a greeting, a signature, a name in passing - and all five
        of those names return zero roster hits. Clarifying there would replace correct
        routing with a question. The ask belongs to the NULL-routing turn (the owner's own
        case); a miss with a team routes to that team."""
        from app.services.chatbot.lanes.escalation import run

        ctx = _ctx(
            routing={"suggested_team": "purchasing", "suggested_agent": "general_enquiries"},
            person_mention="CONTACT AA",
            # The PARSER resolved the team from this message, so nothing was inherited.
            parser_raw={"routing": {"suggested_team": "purchasing", "suggested_agent": "general_enquiries"}},
            text="hi CONTACT AA, please escalate this",
        )
        item = _item(
            brand_code=None, company_id=None, company_name=None, routing_source="none",
            team="purchasing",
        )
        services = self._services_with_staff([])  # nobody by that name

        result = run(ctx, item, services=services)

        assert result["arm"] == "human-intervention", (
            f"a roster miss must not swallow a turn that already knows its team: {result!r}"
        )
        comment = next(a for a in result["actions"] if a["kind"] == "add_comment")
        assert "Team: purchasing" in comment["text"]
        services.next_assignee.assert_called_once()

    def test_one_person_on_two_teams_is_not_two_people(self) -> None:
        """5 of the 22 staff on this install are on more than one team, so a name can come
        back twice for ONE person. That is a membership question, not an identity one: the
        team the parser already resolved settles it, and the turn escalates to that person
        rather than asking."""
        from app.services.chatbot.lanes.escalation import run

        ctx = _ctx(
            routing={"suggested_team": "customer_service", "suggested_agent": "order_enquiries"},
            person_mention="Ali",
            text="escalate to Ali",
        )
        item = _item(
            brand_code=None, company_id=None, company_name=None, routing_source="none",
            team="customer_service",
        )
        services = self._services_with_staff(
            [
                {
                    "team_code": "customer_service",
                    "team_name": "Customer Service",
                    "user_id": "usr-ali",
                    "user_name": "Ali bin Abu",
                    "respond_user_id": "respond-ali",
                },
                {
                    "team_code": "purchasing",
                    "team_name": "Purchasing",
                    "user_id": "usr-ali",
                    "user_name": "Ali bin Abu",
                    "respond_user_id": "respond-ali",
                },
            ]
        )

        result = run(ctx, item, services=services)

        assert result["arm"] == "human-intervention", (
            f"one person on two teams must not read as two people: {result!r}"
        )
        assign = next(a for a in result["actions"] if a["kind"] == "assign_conversation")
        assert assign["respond_user_id"] == "respond-ali"
        comment = next(a for a in result["actions"] if a["kind"] == "add_comment")
        assert "Team: customer_service" in comment["text"]
        services.next_assignee.assert_not_called()

    def test_no_team_no_person_but_a_pending_escalation_still_continues_today_unguarded(
        self,
    ) -> None:
        """(d) Guard: an escalation ask with no team, no person, but a carried PENDING
        escalation context (the `multi_company_unpicked` roster-continuation path this
        file already covers) must keep working exactly as it does today - the new
        person/team-word gate must not swallow this legitimate follow-up."""
        from app.services.chatbot.lanes.escalation import run

        prev = {
            "routing": {"suggested_team": "customer_service", "suggested_agent": "order_enquiries"},
            "routing_roster_plan": [
                {"company_id": "c-a", "company_name": "A"},
                {"company_id": "c-b", "company_name": "B"},
            ],
            "selection_context": "member_offer",
            "last_result_set": [{"uuid": "member-1"}, {"uuid": "member-2"}],
        }
        ctx = _ctx(
            routing={"suggested_team": "customer_service", "suggested_agent": "order_enquiries"},
            person_mention=None,
            prev_variables=prev,
            text="yes please escalate",
        )
        item = _item(
            brand_code=None,
            company_id=None,
            company_name=None,
            routing_source="multi_company_unpicked",
        )
        services = _services()

        result = run(ctx, item, services=services)

        assert result["arm"] == "clarify", (
            "the EXISTING multi-company-unpicked continuation must still clarify exactly "
            f"as it does today: {result!r}"
        )
        assert result["clarify"] is not None


# --------------------------------------------------------------------------- #
# Owner ruling, console pass 3 (6 Sep 2026), item D1: a pending escalation offer must
# not consume a request naming a DIFFERENT team. Evidence turn 9a40182a: pending
# {kind: escalation_offer, team: warehouse, domain: inventory}; "escalate to marketing"
# assigned + commented "Team: warehouse" instead.
#
# `test_output_exchange_rules.py::TestOwnerRulingD1PendingOfferTeamMismatch` covers the
# `is_escalation_confirmation` half. This file covers the OTHER half, at the lane's own
# `run()` boundary: `escalation_context`'s team ladder (this file's own module
# docstring) reads `ctx.parse.output.routing.suggested_team` with NO fallback to
# `_parser_raw` at all outside the person-mention arm - `_person_routing`'s "no team, no
# person, but a previous turn HAD one" clarify (~line 673) checks the CURRENT (already
# inherited) `team`, never `_parser_team(ctx, team)` (the parser's OWN pre-derivation
# answer, exec 13633742's own fix for the person-mention arm). So a turn whose CURRENT
# team is only INHERITED from the stale pending offer (routing.suggested_team ==
# "warehouse", exactly as `output_exchange.py`'s nullish chain falls back to when the
# parser named nothing) reads as "team already known" and silently reassigns, rather
# than asking - reproducing turn 9a40182a's shape one layer down from the
# `is_escalation_confirmation` bug.
# --------------------------------------------------------------------------- #


class TestOwnerRulingD1LaneTeamMismatch:
    def test_a_named_different_team_assigns_there_not_the_stale_pending_team(self) -> None:
        """Guard: when the CURRENT turn's routing already names a team different from
        the stale pending one (output_exchange.py's own req_help branch, covered in
        test_output_exchange_rules.py, already wins here), the lane must assign to the
        NAMED team - today's existing behaviour, kept green."""
        from app.services.chatbot.lanes.escalation import run

        ctx = _ctx(
            routing={"suggested_team": "marketing_promotion", "suggested_agent": "general_enquiries"},
            prev_variables={
                "routing": {"suggested_team": "warehouse", "suggested_agent": "general_enquiries"},
                "pending": {"kind": "escalation_offer", "team": "warehouse", "domain": "inventory"},
            },
            text="escalate to marketing",
        )
        item = _item(brand_code=None, company_id=None, company_name=None, routing_source="none")
        services = _services()

        result = run(ctx, item, services=services)

        assert result["arm"] == "human-intervention"
        kinds = [a["kind"] for a in result["actions"]]
        assert "assign_conversation" in kinds
        comment = next(a for a in result["actions"] if a["kind"] == "add_comment")
        assert comment["text"].startswith("Team: marketing_promotion\n"), (
            f"a named different team must be the one assigned and commented, not the "
            f"stale pending 'warehouse': {comment['text']!r}"
        )
        services.next_assignee.assert_called_once()
        assert services.next_assignee.call_args[0][0]["team_code"] == "marketing_promotion"

    @pytest.mark.xfail(strict=True, reason=_XFAIL_CLARIFY_CHECKS_INHERITED_TEAM)
    def test_an_inherited_team_with_no_parser_answer_asks_instead_of_silently_reassigning(
        self,
    ) -> None:
        """RED (the actual gap): the CURRENT team is only INHERITED (routing.suggested_team
        fell back to the stale pending offer's "warehouse", per output_exchange.py's own
        nullish chain when the parser named nothing this turn) - `_person_routing`'s "no
        team, no person" clarify checks the current (already-inherited) team, so it never
        fires, and the lane silently re-assigns to the stale team instead of asking which
        one the customer means."""
        from app.services.chatbot.lanes.escalation import run

        ctx = _ctx(
            routing={"suggested_team": "warehouse", "suggested_agent": "general_enquiries"},
            parser_raw={"routing": {"suggested_team": None, "suggested_agent": None}},
            # What `output_exchange` actually stamps on this turn (rule D1(b)): NOT an
            # acceptance. `_ctx`'s default escalation says confirmed, which would make the
            # lane assign - correctly - and grade nothing (review of #706, B1).
            escalation={"is_escalation_confirmation": False, "team_unresolved": True},
            prev_variables={
                "routing": {"suggested_team": "warehouse", "suggested_agent": "general_enquiries"},
                "pending": {"kind": "escalation_offer", "team": "warehouse", "domain": "inventory"},
            },
            text="can someone else help me",
        )
        item = _item(brand_code=None, company_id=None, company_name=None, routing_source="none")
        services = _services()

        result = run(ctx, item, services=services)

        assert result["arm"] == "clarify", (
            f"RED (the actual gap): a team that is only INHERITED from a stale pending "
            f"offer, with the parser itself naming nothing this turn, must ask which "
            f"team rather than silently re-assign. Got arm={result['arm']!r}, "
            f"actions={result['actions']!r}"
        )
        assert not any(a["kind"] == "assign_conversation" for a in result["actions"]), (
            f"no assign_conversation may fire while the team is genuinely unresolved: "
            f"{result['actions']!r}"
        )
        services.next_assignee.assert_not_called()


# --------------------------------------------------------------------------- #
# Owner console defect L, the LIVE shape (prod turn 9dd4cd0f, 6 Sep 2026). The executor
# executes `actions[]` and NOTHING else (plan section 5b, ruling 5 Sep 2026) - `reply` is
# the record of what was composed. Three clarify branches of this lane composed real words
# and returned `actions: []`, so the customer was asked nothing and the turn sat waiting
# for the answer.
# --------------------------------------------------------------------------- #


class TestEveryClarifyIsSomethingTheCustomerReceives:
    def _ambiguous_person_ctx(self):
        """"I want to escalate to Nurain" - the parser named a PERSON and no team, which
        is what `_parser_team` reads off `_parser_raw` to tell a mention that routes from
        one made in passing."""
        return _ctx(
            routing={"suggested_team": "customer_service", "suggested_agent": "general_enquiries"},
            person_mention="Nurain",
            text="I want to escalate to Nurain",
            parser_raw={"routing": {"suggested_team": None, "suggested_agent": None}},
        )

    def _two_team_services(self):
        services = _services()
        services.staff_lookup = lambda person: [
            {
                "user_id": "u-1",
                "user_name": "Nurain A",
                "respond_user_id": "r-1",
                "team_code": "do_customer_service",
                "team_name": "DO Customer Service",
            },
            {
                "user_id": "u-1",
                "user_name": "Nurain A",
                "respond_user_id": "r-1",
                "team_code": "project_customer_service",
                "team_name": "Project Customer Service",
            },
        ]
        return services

    def test_an_ambiguous_person_asks_the_question_out_loud(self) -> None:
        from app.services.chatbot.lanes.escalation import run

        result = run(
            self._ambiguous_person_ctx(),
            _item(team="customer_service"),
            services=self._two_team_services(),
        )
        assert result["pending"]["kind"] == "team_clarify"
        sends = [a for a in result["actions"] if a["kind"] == "send_message"]
        assert len(sends) == 1, (
            f"the clarify must be sent exactly once: {result['actions']!r}"
        )
        assert sends[0]["text"] == result["clarify"]["clarify_text"]
        assert "DO Customer Service" in sends[0]["quick_replies"]
        assert "Project Customer Service" in sends[0]["quick_replies"]

    def test_the_dry_run_twin_sends_the_same_ask_flagged(self) -> None:
        """The owner tests from the console and the console runs `is_test`, so the preview
        arm is the one they would have seen the silence on."""
        from app.services.chatbot.lanes.escalation import run

        result = run(
            self._ambiguous_person_ctx(),
            _item(team="customer_service"),
            services=self._two_team_services(),
            dry_run=True,
        )
        assert result["pending"]["kind"] == "team_clarify"
        sends = [a for a in result["actions"] if a["kind"] == "send_message"]
        assert len(sends) == 1, result["actions"]
        assert sends[0]["text"] == result["clarify"]["clarify_text"]
        assert sends[0]["dry_run"] is True

    @pytest.mark.xfail(strict=True, reason=_XFAIL_CLARIFY_CHECKS_INHERITED_TEAM)
    def test_the_no_team_clarify_is_sent_too(self) -> None:
        """The other clarify branch: no team this turn and an OPEN offer, so the lane asks
        which team rather than letting the stale offer swallow the request (D1).

        The premise moved with owner rule R-a (console pass 4, 7 Sep 2026): it used to be
        "a previous turn carried a non-default team", which also fired on a request that
        named no team and no offer, and that is the regression `test_pass4_item5_*` grades.
        What this class exists to grade is unchanged - a clarify the lane DECIDES on is a
        clarify the customer RECEIVES, with tappable teams."""
        from app.services.chatbot.lanes.escalation import run

        ctx = _ctx(
            routing={"suggested_team": None, "suggested_agent": "general_enquiries"},
            # Not an acceptance (see the B1 note on the AC-815 unmapped-team test).
            escalation={"is_escalation_confirmation": False, "company_pick": None},
            prev_variables={
                "routing": {"suggested_team": "purchasing"},
                "pending": {
                    "kind": "escalation_offer",
                    "team": "purchasing",
                    "domain": "inventory",
                },
            },
        )
        result = run(ctx, _item(team=None), services=_services())
        assert result["pending"]["kind"] == "team_clarify"
        sends = [a for a in result["actions"] if a["kind"] == "send_message"]
        assert len(sends) == 1, result["actions"]
        assert sends[0]["text"] == result["clarify"]["clarify_text"]
        assert sends[0]["quick_replies"], (
            "the teams must be tappable, or the customer has to spell one correctly: "
            f"{sends[0]!r}"
        )


# --------------------------------------------------------------------------- #
# Review of #706, blocker B1. The D1 clarify arm in `_person_routing` ("no person, no
# parser team, and a previous turn HAD one: ask") fired on EVERY acceptance from turn 2 on.
# `output_exchange` hard-defaults `routing.suggested_team` to `customer_service` and
# `compile_state` persists it every turn, so `prev_team` is always truthy, and the parser's
# own team is null whenever the customer did not name one - which is every bare "yes".
# Route sends `is_escalation_confirmation` straight into this lane and `run()` reaches
# `_person_routing` unconditionally, so AC-817 rule 3's "a bare yes still confirms" was
# true at the post-processor and false end to end. Measured through `run()` on base
# 89ab3773c versus #706's HEAD: all three cases below returned `clarify` on HEAD.
# --------------------------------------------------------------------------- #


class TestAnAcceptanceIsNeverAskedWhichTeam:
    def _run(
        self,
        *,
        text: str,
        offered: str | None,
        confirmed: bool,
        prev_team: str,
        parser_team: str | None = None,
    ):
        from app.services.chatbot.lanes.escalation import run

        prev: dict = {
            "routing": {"suggested_team": prev_team, "suggested_agent": "general_enquiries"},
        }
        if offered is not None:
            prev["pending"] = {"kind": "escalation_offer", "team": offered, "domain": "inventory"}
            prev["response"] = f"Would you like me to escalate to {offered} team?"
        ctx = _ctx(
            # The DERIVED routing has already inherited the previous team (output_exchange's
            # nullish chain); the parser's OWN snapshot named `parser_team`, which is null
            # for every case here except the one whose message names a team (R-a).
            routing={"suggested_team": prev_team, "suggested_agent": "general_enquiries"},
            parser_raw={"routing": {"suggested_team": parser_team, "suggested_agent": None}},
            escalation={"is_escalation_confirmation": confirmed, "company_pick": None},
            prev_variables=prev,
            text=text,
        )
        item = _item(brand_code=None, company_id=None, company_name=None, routing_source="none")
        services = _services()
        return run(ctx, item, services=services), services

    def test_a_bare_yes_over_a_pending_warehouse_offer_assigns_to_warehouse(self) -> None:
        """Capture output_exchange/parser-15145532: "yes" over a pending warehouse offer.
        The post-processor stamps `is_escalation_confirmation: true`; the lane must then
        ASSIGN to the offered team, never ask which team."""
        result, services = self._run(
            text="yes", offered="warehouse", confirmed=True, prev_team="warehouse"
        )
        assert result["arm"] == "human-intervention", (
            f"an accepted offer is assigned, not re-asked: arm={result['arm']!r}"
        )
        comment = next(a for a in result["actions"] if a["kind"] == "add_comment")
        assert comment["text"].startswith("Team: warehouse\n"), comment["text"]
        services.next_assignee.assert_called_once()

    def test_escalate_over_a_pending_purchasing_offer_assigns_to_purchasing(self) -> None:
        """Capture exec-13484619: "ESCALATE" over a pending purchasing offer, emitted
        `request_for_help` with a null routing and the model's own confirmation flag
        true."""
        result, services = self._run(
            text="ESCALATE", offered="purchasing", confirmed=True, prev_team="purchasing"
        )
        assert result["arm"] == "human-intervention", result["arm"]
        comment = next(a for a in result["actions"] if a["kind"] == "add_comment")
        assert comment["text"].startswith("Team: purchasing\n"), comment["text"]

    def test_a_fresh_ask_with_no_pending_offer_flows_through_unguarded(self) -> None:
        """"can someone help me" with NO offer open and only the hard-defaulted
        `customer_service` routing carried from the previous turn. Nothing was offered,
        so there is no stale team to be wrong about: the lane behaves exactly as it did
        before D1 and assigns to the derived team."""
        result, _ = self._run(
            text="can someone help me",
            offered=None,
            confirmed=False,
            prev_team="customer_service",
        )
        assert result["arm"] == "human-intervention", (
            f"with no open offer the D1 clarify has no premise: arm={result['arm']!r}"
        )

    @pytest.mark.xfail(strict=True, reason=_XFAIL_CLARIFY_CHECKS_INHERITED_TEAM)
    def test_a_fresh_ask_over_an_open_offer_still_asks(self) -> None:
        """The arm's one real case, kept: turn 9a40182a's shape."""
        result, services = self._run(
            text="can someone else help me",
            offered="warehouse",
            confirmed=False,
            prev_team="warehouse",
        )
        assert result["arm"] == "clarify", result["arm"]
        services.next_assignee.assert_not_called()

    def test_an_expired_offer_is_not_an_open_one(self) -> None:
        """A pending marker whose clock ran out (AC-816 rule 1's ttl) is not an open
        offer: the same premise `output_exchange.offer_is_open` applies, read here
        through the same function so the two ends of the turn cannot disagree. The
        previous routing is the carried DEFAULT, so H64's premise is absent too, and the
        turn assigns."""
        from app.services.chatbot.lanes.escalation import run

        ctx = _ctx(
            routing={"suggested_team": "customer_service", "suggested_agent": "general_enquiries"},
            parser_raw={"routing": {"suggested_team": None, "suggested_agent": None}},
            escalation={"is_escalation_confirmation": False, "company_pick": None},
            prev_variables={
                "routing": {"suggested_team": "customer_service", "suggested_agent": "general_enquiries"},
                "pending": {"kind": "member_offer", "team": "warehouse", "ttl": 0},
            },
            text="can someone help me",
        )
        item = _item(brand_code=None, company_id=None, company_name=None, routing_source="none")
        result = run(ctx, item, services=_services())
        assert result["arm"] == "human-intervention", result["arm"]

    @pytest.mark.xfail(strict=True, reason=_XFAIL_CLARIFY_CHECKS_INHERITED_TEAM)
    def test_an_open_offer_for_the_default_team_still_asks(self) -> None:
        """Re-review of #706: the ONE shape only the open-offer premise catches. The
        offer is for `customer_service` and the previous routing is `customer_service`,
        so H64's inheritance premise is absent (that is the carried default); the offer
        being OPEN is the whole reason to ask. Red with `if offer_is_open(prev)` removed,
        confirmed on the way in."""
        result, services = self._run(
            text="can someone help me",
            offered="customer_service",
            confirmed=False,
            prev_team="customer_service",
        )
        assert result["arm"] == "clarify", (
            f"an open offer is a premise on its own, whatever team it is for: {result['arm']!r}"
        )
        assert result["pending"]["kind"] == "team_clarify", result["pending"]
        # AC-822: the marker carries the teams the ask OFFERED, slug beside label, so the
        # answer has something to resolve against. Nothing narrowed this one, so it is the
        # whole vocabulary.
        assert result["pending"]["options"] == [
            {"team": t, "label": t.replace("_", " ")} for t in ESCALATION_TEAMS
        ], result["pending"]["options"]
        services.next_assignee.assert_not_called()

    def test_an_ambiguous_team_word_with_no_offer_still_asks(self) -> None:
        """H64 / AC-815, restated on the amended parser contract (owner rules R-a / R-c,
        console pass 4, 7 Sep 2026). The owner's "escalate to marketing" turn had NO offer
        open and a `purchasing` routing carried from the previous turn; assigning it to
        purchasing is the complaint AC-815 was written for, and it must still not happen.

        What MOVED is the stub, not the assertion. The premise the lane used to reach this
        by - "the carried team is not the hard default" - could not tell this turn apart
        from "I want to talk to a human" over the same carried team (both arrived with a
        null parser routing), so it also fired on the second, which is the regression
        production showed (mt-r2, `test_pass4_item5_*`). Under the amended contract the
        parser is what tells them apart: it carries the customer's own word, `"marketing"`,
        which names three catalogue teams and is therefore asked about. A message naming no
        team still arrives null and is assigned, which is the sibling test below."""
        result, services = self._run(
            text="escalate to marketing",
            offered=None,
            confirmed=False,
            prev_team="purchasing",
            parser_team="marketing",
        )
        assert result["arm"] == "clarify", result["arm"]
        assert result["clarify"]["clarify_text"].count("marketing") == 3, (
            "an ambiguous word asks over the members it names, not the whole catalogue: "
            f"{result['clarify']['clarify_text']!r}"
        )
        services.next_assignee.assert_not_called()

    def test_a_request_naming_no_team_is_assigned_not_asked(self) -> None:
        """The turn the premise above used to break (owner rule R-a): same carried
        `purchasing`, same null parser routing at the OLD contract, but the message names
        no team at all. It keeps the default routing rather than being handed the menu."""
        result, services = self._run(
            text="I want to talk to a human",
            offered=None,
            confirmed=False,
            prev_team="purchasing",
        )
        assert result["arm"] == "human-intervention", result["arm"]
        services.next_assignee.assert_called_once()

    def test_a_domain_that_routes_itself_is_not_inherited(self) -> None:
        """Guard on the inheritance test: a turn whose DOMAIN derives a team is routed by
        its domain, not by the previous turn, even when the parser's own routing is null
        and the previous routing happens to name the same team."""
        from app.services.chatbot.lanes.escalation import run

        ctx = _ctx(
            routing={"suggested_team": "warehouse", "suggested_agent": "general_enquiries"},
            parser_raw={"routing": {"suggested_team": None, "suggested_agent": None}},
            escalation={"is_escalation_confirmation": False, "company_pick": None},
            prev_variables={
                "routing": {"suggested_team": "warehouse", "suggested_agent": "general_enquiries"},
            },
            text="escalate my stock question",
        )
        ctx["parse"]["output"]["domain_hint"] = "inventory"
        item = _item(brand_code=None, company_id=None, company_name=None, routing_source="none")
        result = run(ctx, item, services=_services())
        assert result["arm"] == "human-intervention", result["arm"]
        comment = next(a for a in result["actions"] if a["kind"] == "add_comment")
        assert comment["text"].startswith("Team: warehouse\n"), comment["text"]
