"""Ports of `escalate-catalog.js` and `build-outcome.js` (AC-201, AC-202, AC-302).

Two nodes, one file, because they are two halves of one question: what did this branch
build, and what does it say?

**`escalate_catalog`** is the canned-copy switch. Nine `branch_kind` arms map to a
sentence plus three behaviour flags (`manualResponse`, `includeResponse`,
`is_escalate_offer`) that `compile-current-state` used to derive from a seven-arm
`isExecuted` ladder. The sentences themselves now come from the prompt registry
(`copy.CannedCopy`, AC-302); the flags stay code, because they are routing, not wording.

**`build_outcome`** is the 15-key producer map. `compile-current-state` used to reach
back to eighteen upstream nodes by name; the hub reads them once, at the one point that
dominates every path into ccs, and puts the result on the item. In the CRM there are no
by-name reads to remove - but the SHAPE has to survive, because ccs's own precedence
ladder is written against the map's keys and the replay corpus grades it.

**A producer that did not run is `None`, and a producer that is not in the graph is also
`None`.** That collapse is n8n's own (`_one`'s `catch` swallows "no node named n"), and
it is why the `outcome_fragment` mechanism exists at all: once a producer moves into a
sub, its key goes quietly null forever. The port keeps the collapse for parity and keeps
the fragment override for the same reason n8n has it.
"""
from __future__ import annotations

from typing import Any, Mapping

from app.services.chatbot import jsc
from app.services.chatbot.contracts import UNSUPPORTED_TOPIC_COPY
from app.services.chatbot.copy import CannedCopy

# The 15 keys, in `build-outcome`'s own order (which is ccs's precedence order for the
# first five). ONE declaration: `compile_state` iterates the map, never a second list.
OUTCOME_KEYS: tuple[str, ...] = (
    # the reply ladder, in ccs's own precedence order
    "build-ideate-reply",
    "build-suggest-offer",
    "build-cs-member-offer",
    "escalate-catalog",
    "central-exchange",
    # quick replies / roster owners
    "access-level-choice-message",
    "promo-picker",
    "validator",
    "crossdomain-zeroset",
    "build-miss-member-offer",
    "dym-annotate-partial",
    "dym-annotate",
    "clarify-company-reply",
    "offer-hold-reply",
    # the ONE multi-item read: ccs uses `.all().map(i => i.json)` and tests for `null`
    "cs-roster-plan",
)

# `sub-output`'s RS-9 carrier stubs: the trigger field each producer name is re-emitted
# from. A carrier runs only when its `g-*` gate saw a non-null value, so "the field is
# null" and "the node did not execute" are the same statement - which is what makes the
# gates expressible as a dict lookup here instead of as eleven If nodes.
CARRIER_FIELDS: dict[str, str] = {
    "build-suggest-offer": "suggest_offer",
    "access-level-choice-message": "access_choice",
    "clarify-company-reply": "clarify",
    "offer-hold-reply": "offer_hold",
}


def pretty_team(team: Any) -> str:
    """`_prettyTeam` - DISPLAY ONLY, underscores to spaces (captain, 2026-08-24).

    Team names are internal slugs and were reaching customer WhatsApp copy verbatim
    (`marketing_promotion_sorento team`). The raw slug is what routing and persistence
    keep; only the interpolation is prettified.
    """
    return jsc.js_string("" if team is None else team).replace("_", " ").strip()


def _template_value(obj: Any, key: str) -> Any:
    """What a JS template literal would print for `obj.key`.

    `${undefined}` is the string "undefined" and `${null}` is "null", and those are
    different: after a JSON round trip an ABSENT key is `undefined` and a written null
    is `null`. `jsc.get` collapses the two, so the presence test is done here.
    """
    if not jsc.has(obj, key):
        return jsc.UNDEFINED
    return obj.get(key)


def escalate_catalog(
    item: Mapping[str, Any],
    ctx: Mapping[str, Any],
    copy: CannedCopy,
    *,
    not_found: Any = None,
    incoming_picker: Any = None,
    access_choice: Any = None,
    suggest_offer: Any = None,
    gate: Any = None,
    offer_hold: Any = None,
) -> dict[str, Any]:
    """`escalate-catalog.js`. The item plus `response` and its three behaviour flags.

    An UNRECOGNISED `branch_kind` falls straight through the switch, exactly as the JS
    does: empty response, `manualResponse` false, `includeResponse` true,
    `is_escalate_offer` false. `access_denied` is such a kind today - it is a
    `route-turn` arm with no catalog case - and reproducing the fall-through rather than
    inventing a case for it is what keeps S3 free to give it real copy.
    """
    qf = jsc.get(jsc.get(ctx, "parse"), "output") or {}
    out = dict(item)
    kind = out.get("branch_kind")

    response = ""
    manual_response = False
    include_response = True
    is_escalate_offer = False

    if kind == "not_found":
        # Source order: build-suggest-offer (ANNOTATED) -> not-found-error-message ->
        # annotate-incoming-picker. The annotated copy is preferred because
        # build-suggest-offer SPREADS rather than mutates, so reading the raw
        # not-found string would render the pre-annotation text and silently discard
        # the "- has certificate" suffixes the probe computed.
        annotated = None
        if jsc.truthy(suggest_offer):
            message = jsc.get(suggest_offer, "escalate_message")
            if isinstance(message, str) and message:
                annotated = suggest_offer
        if annotated is not None:
            nf = annotated
        elif not_found is not None:
            nf = not_found
        else:
            # `$('annotate-incoming-picker').first()` with NO isExecuted check. On the
            # pre-cut graph "not-found-error-message did not run" coincided exactly with
            # "the incoming picker did instead", so a null here is a broken caller, and
            # n8n threw. It throws here too rather than degrading to an empty reply: the
            # engine records a failed turn with a reason, which is strictly better than a
            # customer receiving `undefined`.
            if incoming_picker is None:
                raise ValueError(
                    "escalate-catalog not_found: neither not_found nor incoming_picker "
                    "was supplied, so there is no escalate_message to send"
                )
            nf = incoming_picker
        response = _template_value(nf, "escalate_message")
        manual_response = not jsc.truthy(jsc.get(nf, "require_specific"))
        # offer only when it is not a clarification prompt
        is_escalate_offer = not jsc.truthy(jsc.get(nf, "is_clarification"))

    elif kind == "access_choice":
        if access_choice is None:
            raise ValueError(
                "escalate-catalog access_choice: no access_choice payload was supplied, "
                "so there is no escalate_message to send"
            )
        response = _template_value(access_choice, "escalate_message")
        manual_response = True

    elif kind == "demand_qty":
        response = copy.render("demand_qty")
        manual_response = True

    elif kind == "not_supported":
        topic_key = UNSUPPORTED_TOPIC_COPY.get(jsc.js_string(_template_value(qf, "domain_hint")))
        response = (
            copy.render_in(topic_key, out.get("reply_language")) if topic_key else copy.render("not_supported")
        )
        manual_response = True

    elif kind == "clarify_menu":
        response = copy.render("clarify_menu", user_goal=jsc.js_string(_template_value(qf, "user_goal")))
        manual_response = True

    elif kind == "escalate_offer":
        # #9: prefer the RESOLVED entity's company team over the parser's access-level
        # guess. B-TEAM-1' deleted the parser's hard `?? 'customer_service'` default, so
        # both sides can be null and the no-team sentence is a real branch, not a guard.
        company_team = jsc.get(gate, "company_team") if gate is not None else None
        team = company_team if jsc.truthy(company_team) else jsc.get(jsc.get(qf, "routing"), "suggested_team")
        response = (
            copy.render("escalate_offer", team=pretty_team(team))
            if jsc.truthy(team)
            else copy.render("escalate_offer_no_team")
        )
        manual_response = True
        is_escalate_offer = True

    elif kind == "out_of_scope":
        # RAW slug on purpose: this string is a note for the human who picks the thread
        # up, never sent to the customer (`includeResponse = false`), and the JS
        # interpolates `qf.routing.suggested_team` here without `_prettyTeam`.
        team = jsc.get(jsc.get(qf, "routing"), "suggested_team")
        response = (
            copy.render("out_of_scope", team=team)
            if jsc.truthy(team)
            else copy.render("out_of_scope_no_team")
        )
        manual_response = True
        include_response = False

    elif kind == "escalation_declined":
        # AC-1703's tail, captain's ruling 20 Sep 2026: a decline over a NON-escalation
        # offer (a did-you-mean roster's own attached "would you like me to escalate"
        # sentence, a detail offer) finishes on this SAME arm as an actual escalation
        # decline, but gets R22(a)'s own short acknowledgement, never the generic
        # "Escalation declined." line - that sentence names an escalation nobody asked
        # for. `lane_parse_output` is the only place that knows which of the two this
        # turn was (`apply`'s `trace.lane == "offer_declined"`), so the copy key is
        # chosen off the flag it left on `ctx.parse.output`, not a second branch kind
        # (main itself has no `branch_kind` wire concept - this is one arm choosing
        # between two registry copies, the mechanical port of
        # `lanes/business/__init__.py::_outstanding_offer_closed`'s own pre-existing
        # distinction).
        response = copy.render(
            "offer_declined" if jsc.truthy(jsc.get(qf, "chatbot_declined_offer_copy")) else "escalation_declined"
        )
        manual_response = True
        include_response = True
        is_escalate_offer = False  # -> cs-offer-gate FALSE -> straight to compile-state

    elif kind == "offer_hold":
        # The clarify ask was composed upstream by `offer-hold-reply` (same body as
        # `clarify-company-reply`); pulled by reference - no LLM, no roster refetch.
        response = (
            jsc.js_string(jsc.get(offer_hold, "clarify_text") or "")
            if offer_hold is not None
            else ""
        )
        manual_response = True
        include_response = True
        is_escalate_offer = False

    # `out.response = undefined` is an ABSENT key once n8n serialises the item, not a
    # null - and `not-found-error-message` can legitimately emit no `escalate_message`.
    # Writing null instead would change the bytes `compile-current-state` then reads.
    if response is jsc.UNDEFINED:
        out.pop("response", None)
    else:
        out["response"] = response
    out["manualResponse"] = manual_response
    out["includeResponse"] = include_response
    out["is_escalate_offer"] = is_escalate_offer
    return out


def cs_offer_gate(
    catalog: Mapping[str, Any] | None,
    ctx: Mapping[str, Any],
    gate: Any,
) -> bool:
    """`cs-offer-gate`'s four AND conditions. TRUE = build the numbered member offer.

    `g4-no-double-picker` is the one worth naming: a turn where the entity gate already
    raised a "please choose" picker must not ALSO raise a member picker, or the customer
    sees two numbered lists and their next number resolves against whichever the session
    happened to keep.

    Hand pass 12, Group B2(a): the require-specific gate (`gate.require_specific`) is
    only ONE of the shapes that raises a picker - a plain did-you-mean roster
    (`build_suggest_offer`'s own `suggest_last_result_set`, spread onto `catalog` via
    `lane_item = {**offer, "branch_kind": ...}`, `answer_bridge.answer_for`) raises one
    too, and g4 never read it: turn 99c114fd's own reply printed the did-you-mean roster
    (1-6) AND the member roster (restarting at 1) in one message, with `open_question`
    left as `member_offer` - so a customer picking by the position the text had just
    shown them could not even reach the did-you-mean roster. Checked directly on
    `catalog` rather than threaded as a new parameter: `catalog` already carries it, and
    `escalate_catalog` is the one function on this seam that has read `suggest_offer`
    from the start (its own `not_found`-branch annotation).
    """
    routing = jsc.get(jsc.get(jsc.get(ctx, "parse"), "output") or {}, "routing") or {}
    g1 = jsc.get(catalog, "is_escalate_offer") is True
    g2 = jsc.get(routing, "suggested_team") == "customer_service"
    g3 = jsc.get(routing, "suggested_agent") == "order_enquiries"
    g4 = gate is None or jsc.get(gate, "require_specific") is not True
    g4b = not jsc.truthy(jsc.get(catalog, "suggest_last_result_set"))
    return g1 and g2 and g3 and g4 and g4b


def build_outcome(
    items: list[Mapping[str, Any]],
    producers: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """`build-outcome.js` - the keyed producer map, per item.

    `producers` maps a producer NAME to what it emitted. An absent key means the node
    did not run (or is not in this graph at all), which n8n's `_one` also reports as
    `null`; `cs-roster-plan` is the one multi-item read and carries a LIST.

    `outcome_fragment` on the item wins per key, VERBATIM: a producer that has moved
    into a sub is never asked for at all, which is the point - asking and overriding
    would still go blind the day the node's name changes.
    """
    out: list[dict[str, Any]] = []
    for item in items:
        json_body = dict(item.get("json") or {})
        raw_fragment = json_body.get("outcome_fragment")
        fragment = raw_fragment if isinstance(raw_fragment, dict) else {}
        outcome = {
            key: (fragment[key] if key in fragment else producers.get(key))
            for key in OUTCOME_KEYS
        }
        # leak-stop: `outcome_fragment` was never part of this item's shape and must not
        # ride into ccs's persisted fallback any more than `outcome` itself does.
        json_body.pop("outcome_fragment", None)
        json_body["outcome"] = outcome
        out.append({"json": json_body})
    return out
