"""The `out_of_scope` lane: hand the turn to a person (S5, AC-501 to AC-507).

Port of the LIVE `sub-escalation` (`fr2u3e6FKg52cPvK` @ `bac9613b`, 10 nodes) plus the
assignment path inside `sub-human-intervention` (@ `ae310ea1`). The live graph is:

    escalation-input -> escalation-context -> clarify-company-gate (If)
        -> [true]  clarify-company-reply       -> escalation-result {arm: 'clarify'}
        -> [false] Call 'sub-human-intervention' -> escalation-result {arm: 'human-intervention'}

**What is NOT here, and why that is the point.** The EXPORT of this workflow carries a
`fresh-entity-gate` and a `clarify-team-gate` / `clarify-team-reply` pair. Neither is live:
they belong to unpromoted builds (B-HB-1 and B-TEAM-1'). Porting them would have shipped
behaviour production has never run, so:

* **H26, the brand half, is closed (#865, owner ruling 27 Sep 2026, fix option 1).** The
  lane still never calls `resolve_and_gate`, but it no longer routes brand-blind: the brand
  of the product the escalation is about (this turn's named product, else the product the
  conversation is focused on) is read off the product row through the `product_brand`
  seam, at the point of use (`_apply_focus_brand`). It used to survive into the escalation
  turn only when some earlier turn happened to mint an offer that stamped it, so a spec
  question answered successfully and then "escalate to marketing" drew the whole team.
* **H27 stays open.** There is no team clarify. A null team is not reachable through the
  real pipeline anyway - `head/output_exchange.derive_routing`'s nullish chain hard-defaults
  `suggested_team` to `customer_service` long before this lane sees it - so the hazard is
  live in the PARSER, not here, and this lane carries whatever arrives, except that a team
  the customer NAMED beats it (fix round 4, `_named_teams`).

Both are marked `xfail(strict=True)` in the tester's suite, so the promotion makes itself
noticed rather than needing to be remembered.

**D14 is evaluated FIRST** (H37). Live's own `test-guard` If sits ahead of the first
send and everything after it, so a dry run reaches no seam at all: no assignee is picked,
no SLA row is created, no cursor moves. `run()` reproduces that ordering literally rather
than guarding each seam, because "guarded afterwards" is what H37 records going wrong.

**D9: the CRM never sends.** Every effect leaves as an `action` for the caller to execute,
in the order the live graph performs them.

**This lane never writes chat history.** `sub-add-comment-respond` does two things when it
runs - the respond.io comment AND a CRM chat-history POST - and it keeps doing both when the
caller executes the `add_comment` action. Writing the comment here as well would double it:
one row from this lane and one from the sub. There is no import of the chat-history service
anywhere in this module, and `test_s5_no_chat_history_write.py` asserts the row count.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from app.services.chatbot import jsc
from app.services.chatbot.contracts import PREVIEW

logger = logging.getLogger(__name__)

# The eight teams a turn can escalate to (was `contracts.SUGGESTED_TEAMS`, AC-1594/S6):
# escalation-lane vocabulary, not domain data, so it lives here rather than on `Policy` -
# `purchasing_certification` and `it_admin` answer from no `chatbot_domains` row of their
# own, so a union over the domain table's `escalation_team_code` could never recover them.
ESCALATION_TEAMS: tuple[str, ...] = (
    "purchasing",
    "purchasing_certification",
    "customer_service",
    "marketing_product",
    "marketing_form",
    "warehouse",
    "marketing_promotion",
    "it_admin",
)

# `escalation-context`'s own STOPGAP mirror of the parser fork's map. The real source is
# the CRM `companies.code` column threaded through the resolver; kept byte-identical here
# and in `head/output_exchange.CO_ALIASES` until that lands.
CO_ALIASES: dict[str, list[str]] = {
    "sorento": ["sorento", "srt"],
    "mocha": ["mocha", "mch"],
    "cabana": ["cabana", "cbn"],
}

# The ONE customer message an out-of-scope handover sends (WA-MSG-TRIM, owner 3 Oct 2026).
# It replaces the old pair, `sorento-sub-respond-sendmsg-respond-routed-to-pic2`'s "Please
# wait for a moment." before the assignment and `...-routed-to-pic`'s "routed to the
# respective person-in-charge (PIC) from {team} team" after it: since 1 Oct 2026 Meta
# charges every outgoing WhatsApp message, so one handover is one message. Owner-approved
# wording, verbatim; the team is on the comment the PIC reads, not in the customer's line.
ROUTED_TO_PIC_REPLY = "Routed to your PIC, they will reply shortly."

# `Call 'sub-add-comment-respond'`'s `comment`, and the timezone its DateTime conversion
# uses. Asia/Kuala_Lumpur is +08:00 with no DST, so a fixed offset is the whole rule.
MALAYSIA = timezone(timedelta(hours=8))
RESPOND_INBOX_URL = "https://app.respond.io/space/{space_id}/inbox/{contact_id}#{message_id}"

# `get-round-robin-assignee`'s body has these two frozen, as literals in the JSON.
NEXT_ASSIGNEE_POLICY_CODE = "NORMAL"
NEXT_ASSIGNEE_TIER = 1


# --------------------------------------------------------------------------- #
# The services seam
# --------------------------------------------------------------------------- #


class ResolveAndGateFn(Protocol):
    def __call__(self, ctx: Any, item: Any) -> Any: ...


class NextAssigneeFn(Protocol):
    def __call__(self, body: dict[str, Any]) -> dict[str, Any]: ...


class SlaCreateFn(Protocol):
    def __call__(self, body: dict[str, Any]) -> dict[str, Any]: ...


class TeamMembersFn(Protocol):
    def __call__(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]: ...


class StaffLookupFn(Protocol):
    """`(first_name) -> [{team_code, team_name, user_id, user_name, respond_user_id}]`."""

    def __call__(self, name: str) -> list[dict[str, Any]]: ...


# --------------------------------------------------------------------------- #
# escalation-input.js
# --------------------------------------------------------------------------- #


def escalation_input(trigger: Any) -> dict[str, Any]:
    """The sub's item carrier: `trigger.item`, whole.

    It exists so the escalation lane's items keep `route-turn`'s `{...access, branch_kind}`
    keys, which `escalation-context` and `clarify-company-reply` both spread over. The
    throw is the node's own and is reproduced: a trigger with no `item` object is a caller
    contract break, not something to paper over with `{}`.
    """
    item = jsc.get(trigger, "item")
    if not isinstance(item, dict):
        raise ValueError(
            "sub-escalation: the trigger carried no `item` object - the contract is "
            "{ ctx, item, is_test }"
        )
    return item


# --------------------------------------------------------------------------- #
# escalation-context.js (live: five outcomes, no `gate`)
# --------------------------------------------------------------------------- #


def _prev_variables(ctx: Any) -> dict[str, Any]:
    """`ctx.session.session_vars.variables`, with the node's own two fallbacks.

    The JS tries `session.session_vars.variables`, then `session.variables`, then `{}` -
    three shapes because the session block has had three over its life. Reproduced whole.
    """
    try:
        session = jsc.get(jsc.get(ctx, "session"), "session_vars")
        variables = jsc.get(session, "variables")
        if jsc.truthy(variables):
            return variables
        direct = jsc.get(jsc.get(ctx, "session"), "variables")
        return direct if jsc.truthy(direct) else {}
    except Exception:  # noqa: BLE001 - the JS's own try/catch, same fallback
        return {}


def _company_keys(company: Any) -> set[str]:
    """Every string a `company_pick` may legitimately match this row by."""
    name = jsc.get(company, "company_name")
    name_key = jsc.js_string(name).lower().strip() if jsc.truthy(name) else ""
    keys: set[str] = set()
    if name_key:
        keys.add(name_key)
    company_id = jsc.get(company, "company_id")
    if jsc.truthy(company_id):
        keys.add(jsc.js_string(company_id).lower())
    for code in (jsc.get(company, "company_code"), jsc.get(company, "code")):
        if isinstance(code, str) and code.strip():
            keys.add(code.lower().strip())
    for alias in CO_ALIASES.get(name_key, []):
        keys.add(alias)
    return keys


def escalation_context(item: dict[str, Any], *, ctx: dict[str, Any]) -> dict[str, Any]:
    """The brand / company axes this escalation routes on. Pure.

    A SIX-rung ladder, in the live body's order (round 4, owner-approved: `carried_
    brand` is the ONE new rung, added 22 Sep 2026):

    1. `picked_member` - the customer picked a row out of the frozen `last_result_set`, so
       both axes are that row's own, verbatim;
    2. `company_pick` - the parser resolved a company name against the OFFERED pool. It
       sits ABOVE the multi-company arm so a resolvable reply routes instead of re-asking;
    3. `sameTeam` - the roster the offer was fetched with, which splits three ways:
       `prior_state` (one company), `prior_state_no_company` (one row, no id) and
       `multi_company_unpicked` (more than one, nobody picked - the clarify arm);
    4. `carried_brand` - the OFFER TURN's own resolved brand
       (`turn_runtime._accepted_pending_brand`, the SAME acceptance carry the agent half
       already uses), read when NONE of the three roster arms above named one. `same_team`
       is never true in the rearch engine today (`_prev_variables` reads `session_vars.
       variables`, a nest `turn/tail.py`'s five session keys never write), so THIS rung is
       what a rearch "yes" after an incoming/ETA miss actually reaches - measured live
       (SRTSC07, 22 Sep 2026): a bare "yes" carried no brand at all, and the whole
       Packing List team rotated instead of drawing the brand-tagged member;
    5. `stated_brand` - a brand the customer named when no roster was involved at all;
    6. `none`.

    **`focus_product` (#865) outranks rungs 4 and 6, never rung 5** and is applied after
    this function, by `_apply_focus_brand`, because it needs a seam and this function is
    pure. Rung 5 is this turn's own `query_brands`, the customer's explicit brand word, and
    it wins over a product carried in focus (fix round 2, S1). The focus rung is the brand
    of the product the escalation is about, read off the product row. It is not a carry
    that can go stale: the focus product itself drops on a topic reset or a newer product
    (`turn/apply.py::_focus_rules`), and the brand is re-read from the row every time.

    Both axes are always what the `get-cs-members` call USED, never re-derived from this
    turn's `query_brands`: re-deriving would narrow the assignee pool to one the customer
    was never shown. `carried_brand` is the ONE exception to "never re-derived" by
    necessity - the rearch engine has no `get-cs-members` roster fetch to remember a
    brand from at all, so it carries the offer turn's OWN resolved brand instead
    (`lanes/business/gate.py`'s `routing_brand`, stamped onto the pending at MINT time,
    read back here through the acceptance carry) - never a brand this turn re-derives
    for itself, which is what the "never re-derived" rule is actually protecting
    against (see also `_accepted_pending_field`'s own docstring for why the picked-
    member and company-pick arms above stay untouched: their brand is a SPECIFIC row's
    own and must keep outranking a generic carry).
    """
    output = jsc.get(jsc.get(ctx, "parse"), "output") or {}
    prev = _prev_variables(ctx)

    team = jsc.get(jsc.get(output, "routing"), "suggested_team") or None
    prev_routing = jsc.get(prev, "routing")
    same_team = bool(
        jsc.truthy(prev_routing)
        and jsc.truthy(team)
        and jsc.get(prev_routing, "suggested_team") == team
    )
    picked = jsc.get(jsc.get(output, "escalation"), "preferred_assignee_id") or None
    row = None
    if picked:
        last_set = jsc.array(jsc.get(prev, "last_result_set"))
        row = jsc.find(last_set, lambda r: jsc.truthy(r) and jsc.get(r, "uuid") == picked)

    query_brands = jsc.get(output, "query_brands")
    stated_brand = (
        jsc.js_string(query_brands[0]).lower()
        if jsc.is_array(query_brands) and len(query_brands) == 1
        else None
    )
    # Round 4 (owner-approved, 22 Sep 2026): `turn_runtime.lane_parse_output` writes
    # this key the SAME way it already writes `preferred_assignee_id`/`company_pick`,
    # under the SAME accept gate `_accepted_pending_field` shares with the agent carry
    # - so an unaccepted turn carries no brand, exactly like it carries no agent.
    raw_carried_brand = jsc.get(jsc.get(output, "escalation"), "carried_brand")
    carried_brand = jsc.js_string(raw_carried_brand).lower() if jsc.truthy(raw_carried_brand) else None

    raw_pick = jsc.get(jsc.get(output, "escalation"), "company_pick")
    company_pick = jsc.js_string(raw_pick).lower().strip() if jsc.truthy(raw_pick) else None

    pick_row = None
    if company_pick:
        # (A) the pool is the companies actually OFFERED: the roster plan when non-empty,
        # else `routing_companies`. Never the union, so a pick can never land on a company
        # whose roster the customer was not shown.
        plan = jsc.array(jsc.get(prev, "routing_roster_plan"))
        source = plan if len(plan) else jsc.array(jsc.get(prev, "routing_companies"))
        pool = [
            c
            for c in source
            if jsc.truthy(c) and (jsc.truthy(jsc.get(c, "company_name")) or jsc.truthy(jsc.get(c, "company_id")))
        ]
        hits = [c for c in pool if company_pick in _company_keys(c)]
        # Deduplicated by identity before the count: two rows for one company are one hit.
        unique = list(
            {
                jsc.js_string(jsc.get(c, "company_id") or jsc.get(c, "company_name")).lower(): c
                for c in hits
            }.values()
        )
        pick_row = unique[0] if len(unique) == 1 else None

    brand_code: Any = None
    company_id: Any = None
    company_name: Any = None
    source_name = "none"

    if row is not None:
        company_id = jsc.get(row, "company_id") or None
        company_name = jsc.get(row, "company_name") or None
        # `'brand_code' in row` is the node's own test: a row that CARRIES the key uses it
        # even when it is null, and only a row without the key falls back to prior state.
        if jsc.has(row, "brand_code"):
            brand_code = jsc.get(row, "brand_code") or None
        else:
            brand_code = (jsc.get(prev, "routing_brand") if same_team else None) or None
        source_name = "picked_member"
    elif pick_row is not None:
        company_id = jsc.get(pick_row, "company_id") or None
        company_name = jsc.get(pick_row, "company_name") or None
        brand_code = jsc.get(pick_row, "brand_code") or None
        source_name = "company_pick"
    elif same_team:
        plan = jsc.array(jsc.get(prev, "routing_roster_plan"))
        if len(plan) == 1:
            company_id = jsc.get(plan[0], "company_id") or None
            company_name = jsc.get(plan[0], "company_name") or None
            brand_code = jsc.get(plan[0], "brand_code") or None
            source_name = "prior_state" if company_id else "prior_state_no_company"
        elif len(plan) > 1:
            source_name = "multi_company_unpicked"
        else:
            companies = jsc.array(jsc.get(prev, "routing_companies"))
            company_id = jsc.get(prev, "routing_company") or None
            match = jsc.find(
                companies, lambda x: jsc.truthy(x) and jsc.get(x, "company_id") == company_id
            )
            company_name = (jsc.get(match, "company_name") or None) if match is not None else None
            if company_id:
                source_name = "prior_state"
            elif len(companies) > 1:
                source_name = "multi_company_unpicked"
            else:
                source_name = "prior_state_no_company"
            # rev-2: NO roster was fetched for this offer, so there is no shown pool for a
            # brand to disagree with - carry the resolved brand rather than dropping it.
            # `next-assignee` narrows to brand-tagged plus untagged members, so this can
            # only refine the pool, and there is no pinned pick on this arm to exclude.
            if source_name != "multi_company_unpicked":
                routing_brand = jsc.get(prev, "routing_brand")
                brand_code = routing_brand if routing_brand is not None else None
    elif carried_brand:
        brand_code = carried_brand
        source_name = "carried_brand"
    elif stated_brand:
        brand_code = stated_brand
        source_name = "stated_brand"

    if brand_code is None and carried_brand and source_name in _ROSTER_WITHOUT_BRAND:
        # #865 round 6 (R6), n8n escalation-context rev-2: "NO roster was fetched for this
        # offer, so there is no shown pool for a brand to disagree with - carry the
        # resolved brand rather than dropping it". The offer named a company and no brand
        # (a silent-company offer, a company clarify), so the offer turn's own brand
        # rides with it; the row still decides the company.
        brand_code = carried_brand

    return {
        **item,
        "brand_code": brand_code,
        "company_id": company_id,
        "company_name": company_name,
        "routing_source": source_name,
        "team": team,
    }


# --------------------------------------------------------------------------- #
# clarify-company-reply.js (live: one shape, no gate branch)
# --------------------------------------------------------------------------- #


def _company_clarify_rows(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """The company POOL this clarify offers, in printed order, one row per name.

    The rows, not just the names, because the numbered pick the customer answers with
    has to route: `engine._question_offered` mints the `company_pick` options off these
    and `turn/apply.py::_answer_offer` hands the picked row's `company_id` back to
    `escalation_context` (hand pass 11, blocker 2). Two pool rows sharing one name
    collapse to one option, exactly as they collapse to one printed name.
    """
    prev = _prev_variables(ctx)
    plan = jsc.array(jsc.get(prev, "routing_roster_plan"))
    pools = plan if len(plan) else jsc.array(jsc.get(prev, "routing_companies"))
    rows: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for entry in pools:
        name = jsc.get(entry, "company_name") if jsc.truthy(entry) else None
        if not jsc.truthy(name) or name in seen:
            continue
        seen.add(name)
        rows.append(
            {
                "idx": len(rows) + 1,
                "company_id": jsc.get(entry, "company_id") or None,
                "company_name": name,
                "brand_code": jsc.get(entry, "brand_code") or None,
            }
        )
    return rows


def _company_clarify_options(ctx: dict[str, Any]) -> list[str]:
    """The company names this clarify offers, in printed order.

    Extracted so the ASK and the quick replies that answer it cannot list different
    companies: `clarify_company_reply` prints these and `_clarify_actions` taps them.
    """
    return [row["company_name"] for row in _company_clarify_rows(ctx)]


def _picker_was_shown(ctx: dict[str, Any]) -> bool:
    """Did the offer being clarified print a numbered member picker? n8n's own test
    (`clarify-company-reply` rev-3: `prev.selection_context === 'member_offer'`)."""
    return jsc.get(_prev_variables(ctx), "selection_context") == "member_offer"


def clarify_company_reply(item: dict[str, Any], *, ctx: dict[str, Any]) -> dict[str, Any]:
    """The ask that goes out when a multi-company offer was not resolved. Pure.

    It COMPOSES only; nothing is sent from here. The copy offers a number, a name, or the
    company, and the companies are bold in both the lead and the parenthetical because the
    parser matches a reply against this same printed pool. Two pool rows sharing one name
    collapse to one printed name, and that single name still resolves (the pool keys are
    per name). With no names at all the ask degrades to number-or-name: never invite a
    reply that cannot resolve.
    """
    rows = _company_clarify_rows(ctx)
    names = [row["company_name"] for row in rows]
    bold = [f"*{name}*" for name in names]
    listed = " / ".join(bold)
    if len(bold) > 1:
        joined = f"{', '.join(bold[:-1])} and {bold[-1]}"
    else:
        joined = bold[0] if bold else ""

    if len(names) == 2:
        lead = f"Both {joined} teams are listed"
    elif joined:
        lead = f"{joined} teams are listed"
    else:
        lead = "More than one team is listed"

    if names and not _picker_was_shown(ctx):
        # #865 round 6 (R4), n8n rev-3 copy (UAC Q5): the offer printed no numbered
        # member list, so the clarify invites the company and nothing it cannot resolve.
        clarify_text = f"{lead} - reply with the company ({' / '.join(names)}) and I'll assign automatically."
    elif names:
        clarify_text = f"{lead} - reply a number, a name, or the company ({listed}) and I'll assign automatically."
    else:
        clarify_text = f"{lead} - reply a number or a name and I'll assign automatically."
    # `clarify_company_options` rides beside the text for the SAME reason
    # `clarify_team_options` does on the team clarify: the question the customer sees and
    # the numbered options the next turn resolves against are built from ONE list, so
    # they cannot name different companies (hand pass 11, blocker 2).
    return {
        **item,
        "clarify_company": True,
        "clarify_text": clarify_text,
        "clarify_company_options": rows,
    }


# --------------------------------------------------------------------------- #
# escalation-result.js
# --------------------------------------------------------------------------- #


def escalation_result(
    *, clarify_team: Any = None, clarify_company: Any = None
) -> dict[str, Any]:
    """The sub's ONE exit. Two mutually exclusive terminals, one output shape.

    `clarify_team` is always `None` on live - there is no `clarify-team-reply` node - and is
    kept in the signature for the day B-TEAM-1' promotes, so the caller does not change
    shape twice.
    """
    if jsc.truthy(clarify_company):
        return {"arm": "clarify", "clarify": clarify_company}
    if jsc.truthy(clarify_team):
        return {"arm": "clarify", "clarify": clarify_team}
    return {"arm": "human-intervention", "clarify": None}


# --------------------------------------------------------------------------- #
# The lane
# --------------------------------------------------------------------------- #


def _pretty_team(team: Any) -> str:
    """Underscores to spaces, for CUSTOMER copy only. The slug is what routing keeps."""
    from app.services.chatbot.tail.outcome import pretty_team

    return pretty_team(team)


def _malaysia(value: Any) -> str:
    """`DateTime.fromISO(v, {zone:'utc'}).setZone('Asia/Kuala_Lumpur')`, formatted.

    Asia/Kuala_Lumpur is +08:00 all year, so the conversion is a fixed offset and needs no
    timezone database. A value that will not parse is printed as-is, which is what Luxon's
    invalid DateTime does rather than throwing the comment away.
    """
    text = jsc.js_string(value)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(MALAYSIA).strftime("%Y-%m-%d %H:%M:%S")


def _clarify_gate(context_item: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """`clarify-company-gate`: an offer whose company pool nobody chose from.

    `multi_company_unpicked` is the trigger on both arms, and it means one thing:
    assigning now would round-robin a pool the customer was never asked to choose from,
    which is the live bug this arm exists to close.

    * The n8n arm, unchanged: the previous turn left a MEMBER offer open and it carried
      rows (`selection_context` + `last_result_set`, the pair `compile-current-state`
      persists).
    * The re-arch arm: a multi-company ROSTER PLAN, which is the pool the escalate offer
      itself listed. Reachable on EITHER path, deliberately (reviewer SF-1, hand pass 11
      final re-check): this engine mints its own offer
      (`turn_runtime.escalation_roster_plan`, off the accepted offer's own options), but
      `tail/member_offer.cs_roster_plan` also emits one row per `gate.routing_companies`
      entry, which `compile-current-state` persists verbatim as the SAME
      `variables.routing_roster_plan` (`sub_answer.miss_roster_plan` caps only ITS OWN
      producer's plan at one row). So a carried-over n8n session with a 2-row plan and no
      `selection_context == "member_offer"` - its own member-roster read came back empty -
      now clarifies where it used to fall through to a blind round robin. Fail-safe by
      direction (a clarify beats a wrong assignment); the owner ruling above ("we clarify
      the company with the user when it is not clear") covers this arm too.
    """
    prev = _prev_variables(ctx)
    if jsc.get(context_item, "routing_source") != "multi_company_unpicked":
        return False
    if len(jsc.array(jsc.get(prev, "routing_roster_plan"))) > 1:
        return True
    return (
        jsc.get(prev, "selection_context") == "member_offer"
        and len(jsc.array(jsc.get(prev, "last_result_set"))) > 0
    )


def run(
    ctx: dict[str, Any],
    item: dict[str, Any],
    *,
    services: Any = None,
    dry_run: bool = False,
    session_factory: Any = None,
) -> dict[str, Any]:
    """One escalation turn: `{arm, clarify, actions, pending}`.

    `services` defaults to the production bundle so `engine.py` can call this as the lane
    without knowing what it needs; every test passes its own.

    `session_factory` is the TURN's factory, and it is what the production branch opens its
    own unit of work from (H56): `run_turn` stamps the contact's company scope on every
    session that factory makes, so the lane's session is scoped like every other one the
    turn opens. It is defence in depth rather than a repair - `post_next_assignee` pins its
    own scope (`_scope_request_to_company`) before it reads `Team` / `AgentTeam`, so the
    draw itself was never failing - but the reads BEFORE that pin
    (`_routing_company_for_body`) and the lane's own unit of work did run unscoped, and one
    mechanism for the whole turn beats a per-callee pin. A caller that injects `services`
    never reaches it, which is why it is optional here and required at `production_session`.

    **The dry-run check is the first thing that happens after the arm is chosen**, which is
    live's own `test-guard` ordering and the whole of H37: n8n called `next-assignee` and
    guarded afterwards, so a test turn moved a real round-robin cursor. Here the seams are
    not reached at all.
    """
    context_item = escalation_context(item, ctx=ctx)

    if _clarify_gate(context_item, ctx):
        clarify = clarify_company_reply(context_item, ctx=ctx)
        result = escalation_result(clarify_company=clarify)
        # R3's marker, and it goes on the TURN ROW only - `response.pending` and the
        # trace. It is NOT what the next turn reads: `compile_state` writes
        # `variables.pending` from `pending_marker.derive`, which emits `escalation_offer`
        # or nothing, and `output_exchange` reads only `escalation_offer` off it. What
        # actually carries a company clarify across the turn boundary is the structured
        # pair the tail re-persists - `selection_context` plus `last_result_set` - which
        # `test_s5_escalation_seams.py` pins end to end. The marker is here so the trace
        # says WHY this turn asked instead of assigning.
        return {
            **result,
            "actions": _clarify_actions(
                jsc.get(clarify, "clarify_text"),
                options=_company_clarify_options(ctx),
                dry_run=dry_run,
            ),
            "pending": {"kind": "company_clarify"},
        }

    result = escalation_result()
    team = jsc.get(context_item, "team")

    if dry_run:
        # D14 / H37, AC-507. No WRITING seam is reached - nothing is drawn, no cursor
        # advances, no SLA row is written - and the turn still returns every action it WOULD
        # have taken, in order, each flagged `dry_run` and `preview`. The executor renders
        # its expressions against this shape, so a dry run that returned a shorter list
        # would be a different contract from the live one and could not be rendered against.
        #
        # The assignee is PREVIEWED, not left blank: `preview_assignee` reads the same pool
        # and the same cursor as the live draw and advances neither, so the owner sees the
        # name the live turn would have picked. "Would assign to somebody" answered the
        # question nobody was asking.
        #
        # The ROUTING decision is previewed too, and that is not a nicety: the owner finds
        # these defects from the console, and the console runs dry (`is_test`). A dry run
        # that skipped the person / team gate would show the inherited team a live turn
        # would never use, which is the very thing the gate exists to stop being shown.
        # Both reads, no writes.
        #
        # `assign_conversation` is always present here: whether the live run omits it
        # depends on `is_already_assigned`, which only the seam knows, so a preview cannot
        # honestly leave it out.
        preview_sla = {
            "initiated_at": PREVIEW,
            "due_at": PREVIEW,
            "due_at_resolution": PREVIEW,
        }
        routed, preview_assignee, routing = _preview_routing(
            ctx, context_item, team, services, session_factory
        )
        if routed is not None and routed["kind"] == "clarify":
            clarify = {
                **context_item,
                "clarify_team": True,
                "clarify_text": routed["text"],
                "clarify_team_options": routed.get("option_pairs") or [],
            }
            return {
                **escalation_result(clarify_team=clarify),
                "actions": _clarify_actions(
                    routed["text"], options=routed.get("options") or [], dry_run=True
                ),
                "pending": {
                    "kind": "team_clarify",
                    "options": routed.get("option_pairs") or [],
                },
                "routing": routing,
            }
        if routed is not None and routed["kind"] == "clarify_product":
            return _product_clarify_result(context_item, routed, dry_run=True, routing=routing)
        if routed is not None and routed["kind"] == "assign":
            team = routed["team"]
        actions = _assignment_actions(
            ctx,
            team,
            assignee=preview_assignee,
            sla=preview_sla,
            include_assign=True,
            dry_run=True,
            preview=True,
        )
        return {**result, "actions": actions, "pending": None, "routing": routing}

    if services is not None:
        return _human_intervention(ctx, context_item, team, services, result)

    # No injected seam, so this is production: the session is opened HERE, off the TURN's
    # own factory (so it carries the contact's company scope, H56), and closed on the way
    # out whether the seams answered or raised. See `escalation_services`.
    from app.services.chatbot.lanes import escalation_services

    with escalation_services.production_session(session_factory) as db:
        return _human_intervention(
            ctx, context_item, team, production_services(db), result
        )


def _human_intervention(
    ctx: dict[str, Any],
    context_item: dict[str, Any],
    team: Any,
    services: Any,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Assign the conversation, or ask which team - one place, both seam sources.

    The person / team decision needs a seam, so it happens HERE rather than in `run()`,
    where the production bundle does not exist yet. So does the focus product's brand
    (#865), for the same reason.
    """
    context_item = _apply_focus_brand(context_item, services)
    routed = _person_routing(ctx, context_item, team, services)
    if routed is not None and routed["kind"] == "clarify":
        # The tail keys on `clarify_text` (`compile_state`'s clarify arm), the same field
        # `clarify-company-reply` writes; the item carries the context so the trace shows
        # what was asked and why.
        clarify = {
            **context_item,
            "clarify_team": True,
            "clarify_text": routed["text"],
            # The teams the ask OFFERED, slug beside label. `compile_state` puts them on
            # the persisted marker so the next turn can resolve a tap against the exact
            # string this ask composed (AC-822).
            "clarify_team_options": routed.get("option_pairs") or [],
        }
        return {
            **escalation_result(clarify_team=clarify),
            # `dry_run=False` is a fact here, not a default: `run()` returns from its own
            # dry-run branch above before this function is reached.
            "actions": _clarify_actions(
                routed["text"], options=routed.get("options") or [], dry_run=False
            ),
            "pending": {
                "kind": "team_clarify",
                "options": routed.get("option_pairs") or [],
            },
            "routing": _routing_record(context_item, None, None),
        }
    asked = _product_clarify(context_item, routed, team, services)
    if asked is not None:
        return _product_clarify_result(
            context_item,
            asked,
            dry_run=False,
            routing=_routing_record({**context_item, "team": asked["team"]}, None, None),
        )
    if routed is not None and routed["kind"] == "assign":
        actions, routing = _assign(
            ctx,
            # The team the draw is made from is the routed one (fix round 4, R1): a team
            # the customer named replaces the parser's in the next-assignee body too.
            {**context_item, "team": routed["team"]},
            routed["team"],
            services,
            assignee=routed["assignee"],
        )
        return {**result, "actions": actions, "pending": None, "routing": routing}
    actions, routing = _assign(ctx, context_item, team, services)
    return {**result, "actions": actions, "pending": None, "routing": routing}


def _apply_focus_brand(context_item: dict[str, Any], services: Any) -> dict[str, Any]:
    """#865: the brand of the product this escalation is about, from the product row.

    Owner ruling 27 Sep 2026 (fix option 1 of the root-cause report on #865): the brand is
    a fact about the product the conversation is about, so the lane reads it at the point
    of use instead of depending on whichever offer an earlier turn happened to mint. The
    product is `item["focus_products"]`, which the engine hands over from THIS turn's
    applied focus: this turn's named product when it named one, else the product the
    previous turn left in focus. Nothing here decides when that carry ends; the focus
    rules already drop it on a topic reset or a newer product.

    It outranks the offer carry and `none` (rungs 4 and 6 of `escalation_context`). It
    never outranks `stated_brand` (rung 5), the brand the customer named on THIS turn:
    "I need the Mocha catalogue, escalate to marketing" after a SORENTO spec answer is a
    Mocha escalation (fix round 2, S1). Nor the three roster arms above them, whose brand
    is a SPECIFIC row the customer was shown. A product with no brand row, products that
    disagree on the brand, a bundle without the seam, or a read that raises leave the item
    exactly as `escalation_context` built it: the escalation is real whether or not the
    brand can be named.

    Fix round 4: the production seam answers `{brand, company, not_found}` (read across
    companies, `escalation_services.focus_product_origin`), and the company the product
    was found in, or the codes no company holds, ride on the item for the routing line.
    A seam that answers a bare brand string (an injected stub) still works.
    """
    source = jsc.get(context_item, "routing_source")
    fills_only = source in _ROSTER_WITHOUT_BRAND and not jsc.truthy(jsc.get(context_item, "brand_code"))
    if source not in _FOCUS_OUTRANKS and not fills_only:
        return context_item
    products = jsc.array(jsc.get(context_item, "focus_products"))
    seam = getattr(services, "product_brand", None) if services is not None else None
    if not products or seam is None:
        return context_item
    try:
        origin = seam(products)
    except Exception:  # noqa: BLE001 - a brand nobody could read is not a failed turn
        logger.warning("chatbot: the focus product's brand could not be read", exc_info=True)
        return context_item
    item = context_item
    brand = origin
    if isinstance(origin, dict):
        brand = origin.get("brand")
        item = {
            **context_item,
            "product_company": origin.get("company") or None,
            "product_not_found": list(origin.get("not_found") or []),
        }
    if not jsc.truthy(brand):
        return item
    if fills_only:
        # R6: the picked row keeps the company and its own routing source.
        return {**item, "brand_code": jsc.js_string(brand).strip().lower()}
    return {
        **item,
        "brand_code": jsc.js_string(brand).strip().lower(),
        "routing_source": "focus_product",
    }


def _product_clarify(
    context_item: dict[str, Any], routed: Any, team: Any, services: Any
) -> dict[str, Any] | None:
    """The did-you-mean for a typed product code no company holds, asked INSIDE the
    escalation (fix round 5, R2), or None.

    Owner hand test, 28 Sep: "pelase escalate to marketing team MWc-SC8609-)PP water
    closet" is an escalation about a product whose code has a typo. The product lane would
    answer a spec sheet or a "Couldn't find ... would you like me to escalate to customer
    service team?" miss; here the customer already said where it goes. So the lane asks
    once, with the resolver's own trigram neighbours (`product_suggestions`, the product
    lane's did-you-mean), names the team it will go to, and the number the customer
    replies with is an acceptance (`turn/apply.py::_answer_offer`) that settles the picked
    code onto the focus, whose brand the draw then reads.

    Only for a code-shaped token (letters and digits: a class word such as "water closet"
    is never asked about), only after the team is settled (a team clarify asks first, one
    question at a time), never over a named person, and never when nothing is near enough
    to offer: then the escalation goes out as before, naming no brand.
    """
    if routed is not None and routed["kind"] != "assign":
        return None
    if routed is not None and routed.get("assignee") is not None:
        return None
    missing = {jsc.js_string(c).strip().upper() for c in jsc.array(jsc.get(context_item, "product_not_found"))}
    seam = getattr(services, "product_suggestions", None) if services is not None else None
    if not missing or seam is None:
        return None
    typed = None
    for entry in jsc.array(jsc.get(context_item, "focus_products")):
        if not isinstance(entry, dict) or entry.get("hint") not in (None, "product"):
            continue
        code = jsc.nullish_str(entry.get("canonical_code") or entry.get("raw")).strip()
        if code.upper() in missing and any(c.isdigit() for c in code) and any(c.isalpha() for c in code):
            typed = jsc.nullish_str(entry.get("raw")).strip() or code
            break
    if typed is None:
        return None
    try:
        codes = [jsc.js_string(c).strip() for c in (seam(typed) or []) if jsc.truthy(c)]
    except Exception:  # noqa: BLE001 - a did-you-mean nobody could read is not a failed turn
        logger.warning("chatbot: the escalation's product did-you-mean did not run", exc_info=True)
        return None
    if not codes:
        return None
    chosen = routed["team"] if routed is not None else team
    lines = "\n".join(f"{i}. {code}" for i, code in enumerate(codes, start=1))
    return {
        "kind": "clarify_product",
        "team": chosen,
        "text": (
            f'Couldn\'t find "{typed}". Did you mean:\n{lines}\n'
            f"Reply with the number and I will pass it to the {_pretty_team(chosen)} team."
        ),
        "option_pairs": [
            {"position": i, "label": code, "product_code": code, "team": chosen}
            for i, code in enumerate(codes, start=1)
        ],
    }


def _product_clarify_result(
    context_item: dict[str, Any], asked: dict[str, Any], *, dry_run: bool, routing: Any
) -> dict[str, Any]:
    """The lane's answer when it asks the product did-you-mean: nothing is drawn, the
    question is sent, and `engine._question_offered` mints its options as an escalation
    offer (`clarify_product`)."""
    clarify = {
        **context_item,
        "team": asked["team"],
        "clarify_product": True,
        "clarify_text": asked["text"],
        "clarify_product_options": asked["option_pairs"],
    }
    return {
        "arm": "clarify",
        "clarify": clarify,
        # The numbered list is the whole answer; no quick replies (as the CS member
        # picker, #1147).
        "actions": _clarify_actions(asked["text"], options=[], dry_run=dry_run),
        "pending": {"kind": "product_clarify", "options": asked["option_pairs"]},
        "routing": routing,
    }


# #865 round 6 (R6): the roster arms whose row named a company but no brand. The offer
# turn's brand, else the focus product's, fills the brand; the row keeps the company.
_ROSTER_WITHOUT_BRAND = frozenset({"company_pick", "prior_state", "prior_state_no_company"})

# The `escalation_context` outcomes the focus product's brand replaces (#865). The roster
# arms (`picked_member`, `company_pick`, the `prior_state*` / `multi_company_unpicked`
# family) are a specific row the customer was shown and keep their own brand, and
# `stated_brand` is the customer's own word on this turn (fix round 2, S1).
_FOCUS_OUTRANKS = frozenset({"carried_brand", "none"})


def _routing_record(
    context_item: dict[str, Any], body: dict[str, Any] | None, assignee: Any
) -> dict[str, Any]:
    """What the escalation trace records about the draw (#865 observability).

    The next-assignee body's routing axes and the rung that chose the brand, plus the
    round-robin cursor key the draw used (`next-assignee` echoes it as `cursor_key`: the
    segment key, with a `~b:<brand>` suffix when the pool narrowed). Before this, the brand
    a lane sent was visible only by joining the SLA row by time.
    """
    source = body if body is not None else {
        "team_code": jsc.get(context_item, "team"),
        "brand_code": jsc.get(context_item, "brand_code"),
    }
    return {
        "team_code": jsc.get(source, "team_code"),
        "brand_code": jsc.get(source, "brand_code"),
        "routing_source": jsc.get(context_item, "routing_source"),
        "cursor_key": jsc.get(assignee, "cursor_key") if isinstance(assignee, dict) else None,
        "assignee_name": jsc.get(assignee, "assignee_name") if isinstance(assignee, dict) else None,
        "brand_matched": jsc.get(assignee, "brand_matched") if isinstance(assignee, dict) else None,
        # Fix round 4: where the product the brand was read from was found, or the codes
        # no company holds.
        "product_company": jsc.get(context_item, "product_company") or None,
        "product_not_found": list(jsc.array(jsc.get(context_item, "product_not_found"))),
    }


def routing_line(routing: Any) -> str | None:
    """The draw `_routing_record` recorded, as one readable line, or None without one.

    Fix round 3 (the owner's 27 Sep retest): the reply the customer sees names only the
    team, so the console and the trace screen show this line to say where it went. Fix
    round 4: it also names the company the product was found in, or says in the same
    line that no company holds the code (the escalation still goes out).
    """
    if not isinstance(routing, dict):
        return None

    def part(key: str) -> str:
        value = jsc.get(routing, key)
        return jsc.js_string(value) if jsc.truthy(value) else "none"

    where = ""
    missing = [jsc.js_string(c) for c in jsc.array(jsc.get(routing, "product_not_found"))]
    if jsc.truthy(jsc.get(routing, "product_company")):
        where = f"found in {part('product_company')}, "
    elif missing:
        where = f"product {', '.join(missing)} not found in any company, "
    return (
        f"Routing: team {part('team_code')}, brand {part('brand_code')}, "
        f"source {part('routing_source')}, {where}assignee {part('assignee_name')}"
    )


def _parser_team(ctx: dict[str, Any], team: Any) -> Any:
    """The team the PARSER itself resolved, not the one the turn inherited.

    `ctx.parse.output.routing` is post-processed, and `output_exchange` carries the previous
    turn's routing into it - so by the time this lane sees it, a turn the parser routed
    nowhere looks routed. `_parser_raw` is the pre-derivation snapshot the same block keeps
    (`output_exchange` sets it, `engine` puts it on `ctx.parse`), and it is what says whether
    THIS message named a team. Falls back to the derived value when there is no snapshot, so
    an injected ctx behaves as it reads.
    """
    raw = jsc.get(jsc.get(ctx, "parse"), "_parser_raw")
    if not isinstance(raw, dict):
        return team
    return jsc.get(jsc.get(raw, "routing"), "suggested_team")


def _pick_staff(hits: list, team: Any) -> Any:
    """The ONE staff member this ask names, or `None` when the roster cannot say.

    One hit is the answer. Several hits for the SAME person are several team memberships,
    not several people (5 of 22 staff on this install are on more than one team), so the
    team the parser already resolved breaks that tie - and only that tie. Two DIFFERENT
    people sharing a name is a real ambiguity and this function refuses to pick one:
    choosing by the parser's team would answer a question about WHO with a fact about WHERE.

    Refusing is NOT the same as asking, and the caller decides which it is (`_person_routing`,
    the `_parser_team` check): a turn the parser routed nowhere asks, and a turn that already
    carries the parser's own team escalates to that team. So an ambiguous name derails
    nothing that knew where it was going - it only ever costs the direct pick.
    """
    if len(hits) == 1:
        return hits[0]
    people = {jsc.js_string(jsc.get(h, "user_id")) for h in hits}
    if len(people) != 1:
        return None
    wanted = jsc.nullish_str(team).strip().lower()
    if not wanted:
        return None
    matched = [h for h in hits if jsc.nullish_str(jsc.get(h, "team_code")).strip().lower() == wanted]
    return matched[0] if len(matched) == 1 else None


def _person_routing(
    ctx: dict[str, Any], context_item: dict[str, Any], team: Any, services: Any
) -> dict[str, Any] | None:
    """"escalate to Nurain" / "escalate to marketing": route by WHO, or ask which team.

    Owner ruling, 6 Sep 2026, from two console turns that both arrived with
    `routing = {suggested_team: null, suggested_agent: null}` and were assigned to whatever
    team the PREVIOUS turn happened to be routed to - the comment named marketing_product
    for a customer-service person and purchasing for a marketing ask.

    Deterministic throughout, and off structured state only (D11 - the name is the parser's
    own `person_mention`, never a regex over the customer's words):

    * A named person is resolved against the staff roster. One match routes to THEIR team
      with them as the assignee - a person the customer named is a direct pick, not a
      round-robin draw. Two different people sharing the name ASKS.
    * **A miss only asks when the PARSER itself resolved no team.** Review of #700: 70 parse
      outputs in the corpus carry a `person_mention` ALONGSIDE a resolved `suggested_team`
      - a greeting, a signature, a name in passing - and every one of those five names
      returns zero roster hits. Asking there would replace correct routing with a question,
      so a miss falls through to that team.

      The team that decides this is `ctx.parse._parser_raw.routing.suggested_team`, the
      parser's OWN answer, never the derived one - and that distinction is the owner's whole
      case, measured on their turn: "escalate to Nurain" arrived with
      `_parser_raw.routing = {suggested_team: null, suggested_agent: null}` while the
      DERIVED routing had already inherited `purchasing` from the previous turn upstream in
      `output_exchange`. Gating on the derived team would make this gate inert on exactly
      the turn it was written for, which is what the first console run showed. With no
      `_parser_raw` (a mocked parse, an injected ctx) the derived team stands in.
    * A parser team that is NOT an exact catalogue member is NARROWED against the
      catalogue before it is used (owner rule R-a, console pass 4, 7 Sep 2026). One
      member matches - assign it; several - ask over exactly those; none - ask over the
      whole vocabulary. See `_catalogue_teams`.
    * No person, no parser team, and not an acceptance: ask ONLY when an escalation offer
      is OPEN (D1 - the stale offer the owner saw consumed). An acceptance
      (`is_escalation_confirmation`) is assigned to the offered team; with no offer there
      is nothing to be wrong about, so the lane carries on exactly as it does today
      (`test_no_team_clarify_on_live_team_flows_through_unguarded`).

    Returns `None` for "nothing to do here", which is every turn that names nobody and
    arrives with a team.
    """
    output = jsc.get(jsc.get(ctx, "parse"), "output") or {}
    person = jsc.nullish_str(jsc.get(output, "person_mention")).strip()

    if person:
        lookup = getattr(services, "staff_lookup", None) if services is not None else None
        if lookup is None:
            return None  # a bundle without the seam behaves exactly as it did before
        try:
            hits = [h for h in jsc.array(lookup(person)) if jsc.truthy(h)]
        except Exception:  # noqa: BLE001 - a failed lookup never guesses
            logger.warning("chatbot: staff lookup did not run", exc_info=True)
            hits = []
        picked = _pick_staff(hits, team)
        if picked is not None:
            return {
                "kind": "assign",
                "team": jsc.get(picked, "team_code"),
                "assignee": {
                    "assignee_id": jsc.get(picked, "user_id"),
                    "assignee_name": jsc.get(picked, "user_name"),
                    "assignee_respond_user_id": jsc.get(picked, "respond_user_id"),
                    "team_set_code": jsc.get(picked, "team_code"),
                    "is_already_assigned": False,
                },
            }
        if jsc.truthy(_parser_team(ctx, team)):
            return None  # the parser itself named a team: the mention was in passing
        return _clarify_over(_team_clarify_pairs(hits), person=person, hits=hits)

    # The PARSER's own team, never the derived one - the same distinction the person arm
    # above makes, and for the same measured reason (owner ruling D1, console pass 3,
    # 6 Sep 2026). A turn that named no team arrives here with `team` already inherited
    # from the stale pending offer (`output_exchange`'s nullish routing chain falls back to
    # the previous turn's), so gating on `team` made this clarify inert on exactly the turn
    # it exists for: turn 9a40182a asked "can someone else help me" over a pending
    # warehouse offer and was silently re-assigned to warehouse.
    #
    # TWO conditions bound it, both from the review of #706 (blocker B1), which measured
    # the first cut firing on EVERY acceptance from turn 2 on:
    #
    # * An ACCEPTANCE is never asked. The post-processor's `is_escalation_confirmation` is
    #   the one accept signal (D11 - the lane reads no words), and a bare "yes" over an
    #   offer arrives here with the parser's own team null by construction: the customer
    #   said yes, not "yes, warehouse".
    # * The premise is an OPEN escalation offer, not "a previous turn had a routing".
    #   `output_exchange` hard-defaults `routing.suggested_team` to `customer_service` and
    #   `compile_state` persists it every turn, so the previous routing is ALWAYS truthy
    #   and gating on it made the arm fire on every turn with no named team. The ruling is
    #   about a stale OFFER being consumed; `offer_is_open` is what says one is open,
    #   read through the same function the post-processor uses so the two ends of the
    #   turn cannot disagree about it (an expired member offer is not open, AC-816).
    #
    # H64 / AC-815 USED to keep a second premise here - INHERITANCE, "this turn named no
    # team, its domain derives none, and the team it would assign to is the previous
    # turn's". It is DELETED (owner rules R-a / R-b, console pass 4, 7 Sep 2026), because
    # it could not tell its own case apart from the one it broke:
    #
    #   * H64:   "escalate to marketing",         parser routing null, prev `purchasing`
    #   * mt-r2: "I want to talk to a human",     parser routing null, prev `purchasing`
    #
    # IDENTICAL at this lane, and D11 forbids reading the two messages to tell them apart.
    # So the premise fired on both and the second one is the regression production showed
    # (turns 1f0428cb / 9089ef88, execs 15501799 / 15502378): a request that named NO team
    # was answered with the eight-team menu instead of being assigned. The discriminator
    # has to come from the PARSER, and now does: under the amended contract (this lane's
    # `_catalogue_teams`, the prompt's ROUTING section) `routing.suggested_team` carries
    # the customer's own team word verbatim when it names a team the catalogue does not
    # hold exactly, and stays null only when the customer named no team at all. H64's turn
    # therefore arrives with `"marketing"` and is narrowed above; mt-r2's arrives null and
    # falls through to here, where the ROUTING CHAIN'S OWN RESULT is exactly right - the
    # carried team when a previous turn had one, else the table's default. Not the default
    # unconditionally: a product browse routes to purchasing by the owner's own table, and
    # "talk to a human" straight after it inherits that, which is the pre-#706 chain and
    # live parity (review of #713, blocker B3; both shapes are pinned in
    # `test_pass4_item5_no_team_named_keeps_default_routing.py`).
    esc = jsc.get(output, "escalation") or {}
    if jsc.get(esc, "is_escalation_confirmation") is True:
        return None
    raw_team = _parser_team(ctx, team)
    named = _named_teams(output)
    parser_pick = _catalogue_teams(raw_team) if jsc.truthy(raw_team) else []
    if named and not (parser_pick and set(parser_pick) <= set(named)):
        # Fix round 4 (R1): the team the customer NAMED beats the parser's suggestion,
        # which is only the fallback when no team is named. "pelase escalate to
        # marketing team MWC-SC8609-PP" came back `suggested_team: purchasing`: a
        # product code reads as the master_products domain, whose routing row is
        # purchasing. A parser team the customer's word agrees with falls through.
        chosen = named
        if len(named) > 1 and "marketing_product" in named and jsc.array(
            jsc.get(context_item, "focus_products")
        ):
            # "marketing" names three teams; about a product, it is Marketing Product
            # (the owner's rule: product escalations go to its brand member there).
            chosen = ["marketing_product"]
        if len(chosen) == 1:
            if chosen[0] == jsc.nullish_str(team).strip().lower():
                return None
            return {"kind": "assign", "team": chosen[0], "assignee": None}
        return _clarify_over([{"team": t, "label": _pretty_team(t)} for t in chosen])
    if jsc.truthy(raw_team):
        # The parser named SOMETHING. Which catalogue members does that word name?
        matched = _catalogue_teams(raw_team)
        if len(matched) == 1:
            if matched[0] == jsc.nullish_str(team).strip().lower():
                return None  # already the team the chain resolved: nothing to correct
            # R-c / AC-815's real complaint: the customer's own word decides, never an
            # unrelated team carried in from an earlier turn. No assignee - a named TEAM
            # is a rotation draw, unlike a named person.
            return {"kind": "assign", "team": matched[0], "assignee": None}
        return _clarify_over(
            [{"team": t, "label": _pretty_team(t)} for t in matched]
            if matched
            else _team_clarify_pairs([])
        )
    from app.services.chatbot.session_state import offer_is_open

    if offer_is_open(_prev_variables(ctx)):
        return _clarify_over(_team_clarify_pairs([]))
    return None


# Fix round 4 (R1): the team words a customer names, longest first, each mapped to the
# word `_catalogue_teams` reads. Found in the parser's `user_goal`, and only after "to" /
# "with" or before "team" / "department", so "trying to get the marketing flyer" names no
# team and "escalate it to marketing" does.
_TEAM_WORDS: dict[str, str] = {
    "marketing products": "marketing_product",
    "marketing product": "marketing_product",
    "product marketing": "marketing_product",
    "marketing promotions": "marketing_promotion",
    "marketing promotion": "marketing_promotion",
    "marketing forms": "marketing_form",
    "marketing form": "marketing_form",
    "purchasing certification": "purchasing_certification",
    "customer services": "customer_service",
    "customer service": "customer_service",
    "customer support": "customer_service",
    "certification": "purchasing_certification",
    "procurement": "purchasing",
    "purchasing": "purchasing",
    "warehouse": "warehouse",
    "marketing": "marketing",
    "it admin": "it_admin",
}
_TEAM_WORD = "|".join(w.replace(" ", r"\s+") for w in _TEAM_WORDS)
_NAMED_TEAM = re.compile(
    rf"\b(?:to|with)\s+(?:the\s+|your\s+)?({_TEAM_WORD})\b"
    rf"|\b({_TEAM_WORD})\s+(?:team|teams|department|dept)\b"
)


def _named_teams(output: Any) -> list[str]:
    """The catalogue teams the customer NAMED on this turn, in catalogue order (R1).

    Read off `user_goal`, the parser's own reading of the message (its spelling fixed:
    "pelase esclate to marekting team" reads "escalate to the marketing team"), never the
    customer's raw text (D11). `routing.suggested_team` cannot carry this on its own: the
    prompt's routing table also fills it from the DOMAIN, so a message naming a team and a
    product code comes back with the product domain's team (prod turn 14875614, "escalate
    to marketing for srtsc07 full height": `suggested_team: purchasing_product`,
    `user_goal: "trying to escalate to marketing for SRtsc07 full height"`).
    """
    goal = jsc.nullish_str(jsc.get(output, "user_goal")).lower()
    teams: set[str] = set()
    for match in _NAMED_TEAM.finditer(goal):
        word = " ".join((match.group(1) or match.group(2)).split())
        teams.update(_catalogue_teams(_TEAM_WORDS[word]))
    return [t for t in ESCALATION_TEAMS if t in teams]


# Fix round 5 (R1): the words that hand a matter to a person, as the parser's `user_goal`
# spells them (its spelling fixed: "pelase esclate" reads "escalate").
_ESCALATE_WORD = re.compile(
    r"\b(?:escalat\w*|eskalasi|transfer\w*|forward\w*"
    r"|hand(?:s|ed|ing)?\s+(?:\w+\s+){0,2}(?:over|to)"
    r"|pass(?:es|ed|ing)?\s+(?:\w+\s+){0,2}(?:on\s+)?to"
    r"|(?:talk|speak)(?:s|ed|ing)?\s+(?:\w+\s+)?(?:to|with))\b"
)


def asks_for_a_named_team(output: Any) -> bool:
    """Did the customer ask to hand this to a team they named (fix round 5, R1)?

    The parser's own MESSAGE TYPE rule 1 says so already: asking for a specific team or to
    escalate is `request_for_help`, "even if they also mention a product or order". The
    owner's "pelase escalate to marketing team MWc-SC8609-)PP water closet" came back a
    master_products business query anyway, while its `user_goal` kept the words. So this
    reads the same field `_named_teams` reads (the parser's reading, never the raw text,
    D11): an escalate word AND a named team. A team mentioned with no escalate word
    ("the spec of SRTWC286 for the marketing team") is not a request for anyone.
    """
    goal = jsc.nullish_str(jsc.get(output, "user_goal")).lower()
    return bool(goal and _ESCALATE_WORD.search(goal) and _named_teams(output))


def _catalogue_teams(word: Any) -> list[str]:
    """The catalogue members the parser's own team WORD names, in catalogue order.

    D11-clean, and worth saying why rather than leaving it to be re-argued: the input is
    `routing.suggested_team`, which the PARSER produced, and the thing it is matched
    against is `SUGGESTED_TEAMS` - OUR OWN routing vocabulary. Nothing here reads the
    customer's message. The D11 rule forbids a regex or substring match over `ctx.text` or
    over a previous reply; a membership test over a fixed catalogue of eight slugs is the
    opposite of that, and it is the ONLY way an ambiguous word can be answered without
    asking the model to guess for us.

    Three outcomes, and every caller wants a different thing from each:

    * exactly one member - the word IS that team ("warehouse", "marketing product",
      "certification" -> `purchasing_certification`);
    * several - the word names a family, not a team ("marketing" -> the three
      `marketing_*` teams). That is the ambiguity owner rule R-a wants ASKED about, over
      those members only, never over the whole catalogue;
    * none - the model sent a word we have no team for. The caller asks over everything,
      which is the only honest list when nothing matched.

    An exact member always wins outright: `purchasing` is a team in its own right and
    must not be read as the family `purchasing_certification` also belongs to.
    """
    token = jsc.nullish_str(word).strip().lower().replace(" ", "_").replace("-", "_")
    if not token:
        return []
    if token in ESCALATION_TEAMS:
        return [token]
    return [t for t in ESCALATION_TEAMS if token in t.split("_")]


def _clarify_over(
    pairs: list[dict[str, Any]], *, person: Any = None, hits: list | None = None
) -> dict[str, Any]:
    """One clarify decision, built from ONE list of `{team, label}` pairs.

    The sentence, the quick replies and the marker the next turn resolves against all come
    from `pairs`, so a tap can never name a team the ask did not offer and the marker can
    never hold a team the customer never saw. `options` stays a list of LABELS because that
    is what `_clarify_actions` puts on the wire as quick replies; `option_pairs` is the same
    list with the slug the router acts on beside each label, and it is what the tail
    persists (AC-822).
    """
    options = [jsc.js_string(jsc.get(p, "label")) for p in pairs]
    return {
        "kind": "clarify",
        "text": _team_clarify_text(person, hits or [], options=options),
        "options": options,
        "option_pairs": pairs,
    }


def _team_clarify_pairs(hits: list) -> list[dict[str, Any]]:
    """The teams this clarify offers, in printed order, as `{team, label}`.

    Extracted for the same reason as the company half: the sentence, the quick replies that
    answer it and the marker that resolves the answer are built from ONE list, so a tap can
    never name a team the ask did not.

    A staff hit's own `team_name` is preferred for the LABEL, because that is the name the
    install gave the team and the customer is being asked to recognise it; `team` is always
    the slug, which is the only string routing can act on. De-duplicated by label, which is
    what the printed list can distinguish.
    """
    pairs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        code = jsc.get(hit, "team_code")
        label = jsc.get(hit, "team_name") or _pretty_team(code)
        if jsc.truthy(label) and label not in seen:
            seen.add(label)
            pairs.append({"team": code, "label": label})
    return pairs or [{"team": t, "label": _pretty_team(t)} for t in ESCALATION_TEAMS]


def _team_clarify_options(hits: list) -> list[str]:
    """The printed labels alone. Kept for `_team_clarify_text`'s no-options fallback."""
    return [jsc.js_string(jsc.get(p, "label")) for p in _team_clarify_pairs(hits)]


def _team_clarify_text(person: Any, hits: list, *, options: list[str] | None = None) -> str:
    """The ask. Names the teams the customer can choose between, and nothing else.

    With hits it is the teams THOSE people are on (that is the whole ambiguity); without,
    it is the set the caller narrowed to - the members the customer's own word named
    (R-a), or the whole routing vocabulary when nothing narrowed it, which is the exact
    set the router can act on. Inventing a shorter list would invite a reply nothing
    could resolve; printing all eight when the word named three is the defect R-a names.
    """
    names = options if options else _team_clarify_options(hits)
    listed = f"{', '.join(names[:-1])} or {names[-1]}" if len(names) > 1 else names[0]
    if person and hits:
        people = {jsc.js_string(jsc.get(h, "user_id")) for h in hits}
        lead = (
            f"{jsc.js_string(person)} is on more than one team"
            if len(people) == 1
            else f"More than one person here goes by {jsc.js_string(person)}"
        )
        return f"{lead}. Which team do you mean - {listed}?"
    if person:
        return (
            f"I could not find anyone called {jsc.js_string(person)}. "
            f"Which team should I pass this to - {listed}?"
        )
    return f"Which team should I pass this to - {listed}?"


def _preview_routing(
    ctx: dict[str, Any],
    context_item: dict[str, Any],
    team: Any,
    services: Any,
    session_factory: Any,
) -> tuple[dict[str, Any] | None, Any, dict[str, Any] | None]:
    """`(routing decision, assignee, routing record)` for a dry run - READS only, in one
    unit of work. The record is `_routing_record`'s, so a dry run's trace shows the same
    next-assignee body a live turn's does.

    Fails soft throughout, on purpose: a bundle with no preview seam (an older injected
    stub), a lookup that raises, or no session factory at all leave the assignee null,
    which is the placeholder AC-507 shipped with. A dry run must never fail a turn over the
    extra detail it is trying to show.
    """

    def _both(bundle: Any) -> tuple[dict[str, Any] | None, Any, dict[str, Any] | None]:
        item = _apply_focus_brand(context_item, bundle)
        routed = _person_routing(ctx, item, team, bundle)
        asked = _product_clarify(item, routed, team, bundle)
        if asked is not None:
            return asked, None, _routing_record({**item, "team": asked["team"]}, None, None)
        if routed is not None and routed["kind"] == "assign" and routed.get("assignee") is None:
            # A named TEAM is a rotation draw (fix round 4, R1), so preview the draw from
            # that team exactly as the live branch makes it.
            item = {**item, "team": routed["team"]}
        elif routed is not None:
            # A named person IS the assignee, and a clarify assigns nobody. Either way
            # there is no rotation to preview.
            return routed, routed.get("assignee"), _routing_record(item, None, routed.get("assignee"))
        seam = getattr(bundle, "preview_assignee", None)
        body = _next_assignee_body(ctx, item)
        if seam is None:
            return routed, None, _routing_record(item, body, None)
        assignee = seam({**body, "preview": True})
        return routed, assignee, _routing_record(item, body, assignee)

    try:
        if services is not None:
            return _both(services)
        if session_factory is None:
            return None, None, None
        # Production dry run: the same read-only unit of work the live branch uses, so both
        # reads are scoped to the contact's company exactly as the draw would be (H56).
        from app.services.chatbot.lanes import escalation_services

        with escalation_services.production_session(session_factory) as db:
            return _both(escalation_services.build(db))
    except Exception:  # noqa: BLE001 - a preview is never worth failing a test turn for
        logger.warning("chatbot: dry-run routing preview did not run", exc_info=True)
        return None, None, None


def _assign(
    ctx: dict[str, Any],
    context_item: dict[str, Any],
    team: Any,
    services: Any,
    *,
    assignee: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Draw an assignee, start the SLA clock, and build the four actions in live's order.

    Returns `(actions, routing record)`; the record is what the trace shows (#865).

    Both seams run before a single action is built, which is what makes the failure shape
    in `engine.py` true: the lane returns its whole list or raises before returning any of
    it, so "assigned but no SLA row" is not a state a caller can observe.

    `assignee` is passed in ONLY when the customer named the person (the staff-lookup arm):
    a direct pick is not a rotation, so the round robin is not drawn from at all. The SLA
    clock still starts, because the escalation is just as real.
    """
    body = None
    if assignee is None:
        body = _next_assignee_body(ctx, context_item)
        assignee = services.next_assignee(body)
    sla = services.sla_create(_sla_body(ctx, context_item, assignee))
    actions = _assignment_actions(
        ctx,
        team,
        assignee=assignee,
        sla=sla,
        # `if-conversation-unassigned`'s true leg. Already assigned in respond.io means
        # someone is on the conversation and re-assigning would take it off them.
        include_assign=jsc.get(assignee, "is_already_assigned") is not True,
        dry_run=False,
        preview=False,
    )
    return actions, _routing_record(context_item, body, assignee)


def _assignment_actions(
    ctx: dict[str, Any],
    team: Any,
    *,
    assignee: Any,
    sla: Any,
    include_assign: bool,
    dry_run: bool,
    preview: bool,
) -> list[dict[str, Any]]:
    """The three actions (assign, comment, one customer message), in execution order.

    ONE builder for the live list and the preview list, so the two can only differ in the
    values a seam would have supplied - never in the shape, the order or the set of keys.
    That is the whole point of AC-507: the executor renders one template against both.

    The `send_message` does not depend on the assignee (a fixed sentence), so it carries
    its real text even in a preview, and only the assignee id, the mention and the three
    timestamps are placeholders. It is the only customer message: the "please wait" that
    used to go before the assignment was folded into it (WA-MSG-TRIM, owner 3 Oct 2026).
    """
    respond_user_id = jsc.get(assignee, "assignee_respond_user_id") if assignee is not None else None
    actions: list[dict[str, Any]] = []
    if include_assign:
        action: dict[str, Any] = {
            "kind": "assign_conversation",
            "respond_user_id": respond_user_id,
            "dry_run": dry_run,
        }
        if preview:
            action["preview"] = True
        actions.append(action)
    comment: dict[str, Any] = {
        "kind": "add_comment",
        "text": _comment_text(ctx, team, sla),
        # The RESPOND user id, not the CRM one, and exactly one of them: the executor maps
        # this to `sub-add-comment-respond`'s `user_id`, which is what respond.io needs to
        # turn a comment into a mention. `assign_conversation` above carries the same id.
        "mention_user_ids": [respond_user_id] if respond_user_id is not None else [],
        "dry_run": dry_run,
    }
    if preview:
        comment["preview"] = True
    actions.append(comment)
    actions.append(_send_message(ROUTED_TO_PIC_REPLY, dry_run))
    return actions


def _clarify_actions(text: Any, *, options: list[str], dry_run: bool) -> list[dict[str, Any]]:
    """The ONE place a clarify becomes something the customer actually receives.

    Prod turn 9dd4cd0f: "I want to escalate to Nurain" composed
    "Nurain is on more than one team. Which team do you mean - DO Customer Service or
    Project Customer Service?", wrote `selection_context: team_clarify`, and returned
    `actions: []`. The executor executes `actions[]` and NOTHING else (plan section 5b,
    ruling 5 Sep 2026) - `reply` is the record of what was composed - so the customer got
    silence and the turn sat waiting for an answer to a question nobody had been asked.

    Every clarify branch of this lane goes through here rather than each appending its own
    action, because the defect was three branches that each independently forgot: the
    company clarify, the live team clarify and its dry-run twin. One builder cannot forget
    on the fourth.

    `quick_replies` is n8n's own shape - a COMMA-JOINED STRING or null, never a list
    (AC-507) - and the options come from the same helper that printed them in the
    sentence, so a tap can never name a team the ask did not.
    """
    words = jsc.nullish_str(text).strip()
    if not words:
        # AC-MEM084/085 (owner ruling Q10): a turn that reaches the reply stage never
        # ends silent. A clarify with no words of its own still sends the handover
        # offer, the no-team wording the accepted-offer regex reads next turn.
        from app.services.chatbot_reply_copy import CHATBOT_REPLY_ESCALATE_OFFER_NO_TEAM

        return [_send_message(CHATBOT_REPLY_ESCALATE_OFFER_NO_TEAM, dry_run)]
    action = _send_message(words, dry_run)
    kept = [jsc.js_string(o).strip() for o in options if jsc.truthy(jsc.js_string(o).strip())]
    if kept:
        action["quick_replies"] = ", ".join(kept)
    return [action]


def _send_message(text: str, dry_run: bool) -> dict[str, Any]:
    """One `send_message` action, in the shape the n8n executor takes.

    `quick_replies` and `result_set` are declared here and left empty ON PURPOSE. They are
    SEALED values that only exist once the tail has composed the reply, and the engine arm
    fills them in from `complete_turn`'s output before the actions leave. Declaring them
    here rather than adding them later keeps every `send_message` the same shape whoever
    built it, so the executor never has to test for a missing key.

    The placeholders match the sealed contract's own empty case (AC-507): `quick_replies`
    null (n8n's `quick_reply` is a comma-joined string or null, never a list) and
    `result_set` `[]` (`compile-current-state`'s own default for `last_result_set`).
    """
    return {
        "kind": "send_message",
        "text": text,
        "quick_replies": None,
        "result_set": [],
        "dry_run": dry_run,
    }


def _next_assignee_body(ctx: dict[str, Any], context_item: dict[str, Any]) -> dict[str, Any]:
    """`get-round-robin-assignee`'s JSON body, key for key.

    `policy_code` and `tier` are literals in the node, not settings - reproduced as
    literals so a change to them is a change to this file and shows up in a diff.
    """
    output = jsc.get(jsc.get(ctx, "parse"), "output") or {}
    return {
        "agent_code": jsc.get(jsc.get(output, "routing"), "suggested_agent"),
        "team_code": jsc.get(context_item, "team"),
        "contact_phone_number": jsc.get(jsc.get(ctx, "contact"), "phone"),
        "policy_code": NEXT_ASSIGNEE_POLICY_CODE,
        "preferred_assignee_id": jsc.get(jsc.get(output, "escalation"), "preferred_assignee_id") or "",
        "tier": NEXT_ASSIGNEE_TIER,
        "brand_code": jsc.get(context_item, "brand_code"),
        "company_id": jsc.get(context_item, "company_id"),
    }


def _sla_body(
    ctx: dict[str, Any], context_item: dict[str, Any], assignee: Any
) -> dict[str, Any]:
    """`conversation-sla-tracking-create`'s JSON body, key for key.

    Three fields prefer the ASSIGNEE's answer over the turn's (`team_set_code`,
    `brand_code`, `company_id`): the round robin may have resolved a narrower pool than the
    turn knew about, and the SLA row has to describe who was actually assigned.
    """
    output = jsc.get(jsc.get(ctx, "parse"), "output") or {}
    message = jsc.get(jsc.get(ctx, "text"), "message")
    message_id = jsc.get(message, "messageId")
    input_message = _input_message(ctx)

    def prefer(key: str, fallback: Any) -> Any:
        value = jsc.get(assignee, key)
        return value if value is not None else fallback

    return {
        "assigned_to_id": jsc.get(assignee, "assignee_id") or "",
        "contact_phone_number": jsc.get(jsc.get(ctx, "contact"), "phone") or "",
        "agent_code": jsc.get(jsc.get(output, "routing"), "suggested_agent") or "",
        "team_set_code": prefer("team_set_code", jsc.get(context_item, "team") or ""),
        "brand_code": prefer("brand_code", jsc.get(context_item, "brand_code") or None),
        "company_id": prefer("company_id", jsc.get(context_item, "company_id") or None),
        "message_id": _numeric_message_id(message_id),
        "source_message_id": None if message_id is None else jsc.js_string(message_id),
        "source_message_text": input_message or "",
    }


def _numeric_message_id(message_id: Any) -> int | None:
    """`ConversationSLATrackingCreate.message_id` is an int FK-ish correlation field -
    respond.io's own ids are base-10; a channel or harness id that is not (a dry-run /
    test sentinel) degrades to None rather than failing the whole escalation, exactly
    as an absent id already does. `source_message_id` (a plain string) is what keeps
    the real value regardless."""
    if message_id is None:
        return None
    try:
        return int(str(message_id).strip())
    except (TypeError, ValueError):
        return None


def _input_message(ctx: dict[str, Any]) -> str:
    """`Call 'sub-human-intervention'`'s `input_message` - expression for expression on
    every branch except the quoted-message fallback, where R1 below deliberately
    improves on the raw n8n expression rather than porting its `undefined` (see that
    ruling's own note).

    Live, from the node's `workflowInputs.value.input_message` (two adjacent `{{ }}`
    blocks, concatenated with no separator by the template):

        {{ ctx.text.message.message.text
           || ctx.text.message.message.attachment?.description
           || '[' + (ctx.text.message.message.type || 'unknown') + ' message]' }}
        {{ ctx.text.message.replyTo?.message
           ? ' reply to: ' + ctx.text.message.replyTo.message.text : '' }}

    Reading only `.text` - which is what this file did first - is right for a typed
    message and wrong for every other kind the bot actually receives: an image, a voice
    note or a document arrives with an empty `text`, so the SLA row's
    `source_message_text` came out blank and the person picking the case up saw no trace
    of what the customer sent. Live falls back to the attachment's description and then to
    a `[image message]` style placeholder naming the type, and appends the quoted message
    when the customer replied to one.

    `replyTo` hangs off the WEBHOOK body (`ctx.text.message`), one level above the message
    body the first chain reads - copying its path from the wrong level is the easy mistake
    here, so both are spelled out above.

    Owner ruling 22 Sep 2026, R1 (AC-EQ-1..3): the quoted body is `text` if truthy, else
    `title` (Respond.io's quick-reply quote shape carries only `title`), else the whole
    " reply to: ..." suffix is dropped - never n8n's `undefined`, which is what reading
    `.text` unguarded renders for a quoted message with neither, per the n8n expression
    quoted above. R1 is a deliberate divergence from that expression for this one
    branch, not a port of it, at the time of this port (22 Sep 2026) - no claim is made
    here about whether n8n's own node has since been fixed; a fresh capture that still
    shows `undefined` for this shape is not a regression in this file.
    """
    envelope = jsc.get(jsc.get(ctx, "text"), "message")
    body = jsc.get(envelope, "message")

    value = jsc.get(body, "text", jsc.UNDEFINED)
    if not jsc.truthy(value):
        value = jsc.get(jsc.get(body, "attachment"), "description", jsc.UNDEFINED)
    if not jsc.truthy(value):
        kind = jsc.get(body, "type", jsc.UNDEFINED)
        value = "[" + (jsc.js_string(kind) if jsc.truthy(kind) else "unknown") + " message]"

    text = jsc.js_string(value)

    quoted = jsc.get(jsc.get(envelope, "replyTo"), "message", jsc.UNDEFINED)
    if jsc.truthy(quoted):
        quoted_text = jsc.get(quoted, "text", jsc.UNDEFINED)
        quoted_body = (
            quoted_text if jsc.truthy(quoted_text) else jsc.get(quoted, "title", jsc.UNDEFINED)
        )
        if jsc.truthy(quoted_body):
            text += " reply to: " + jsc.js_string(quoted_body)
    return text


def _comment_text(ctx: dict[str, Any], team: Any, sla: Any) -> str:
    """`Call 'sub-add-comment-respond'`'s `comment`, byte for byte.

    Verified against the live node expression by substituting its six `{{ }}` blocks and
    diffing the literal skeleton: equal. The three timestamps are Luxon's
    `fromISO(v, {zone:'utc'}).setZone('Asia/Kuala_Lumpur').toFormat('yyyy-MM-dd HH:mm:ss')`,
    which is `%Y-%m-%d %H:%M:%S` at a fixed +08:00 (Malaysia has no DST), and they come from
    the CRM's OWN in-process SLA create - n8n no longer calls that endpoint or composes this
    text.

    The team is the RAW slug here, not prettified: this is an internal note to the person
    picking the case up, and the slug is what they will search the CRM by.

    **No mention markup.** The text carries no `{{@user.<id>}}`; `sub-add-comment-respond`
    prefixes that itself from the `user_id` it is given, which is why the action carries the
    respond user id in `mention_user_ids` and not a formatted string.
    """
    contact_id = jsc.get(jsc.get(ctx, "contact"), "id")
    message_id = jsc.get(jsc.get(jsc.get(ctx, "text"), "message"), "messageId")
    reference = RESPOND_INBOX_URL.format(
        space_id=_space_id(ctx), contact_id=jsc.js_string(contact_id), message_id=jsc.js_string(message_id)
    )
    return (
        f"Team: {jsc.js_string(team)}\n"
        f"⏰ SLA Alert: This contact is routed to you at {_malaysia(jsc.get(sla, 'initiated_at'))}.\n"
        f"You have until {_malaysia(jsc.get(sla, 'due_at'))} to respond.\n"
        f"You have until {_malaysia(jsc.get(sla, 'due_at_resolution'))} to resolve.\n"
        f"Reference message: {reference}"
    )


def _space_id(ctx: dict[str, Any]) -> str:
    """The respond.io workspace the inbox link points at.

    n8n hard-codes `364817` inside the comment string. D5 moved that to the default respond
    workspace row everywhere else; here the ctx does not carry it, so the literal stays and
    is named rather than hidden. Moving it is S8's job, with the rest of the hard-coded id.
    """
    return "364817"


# --------------------------------------------------------------------------- #
# Production bindings
# --------------------------------------------------------------------------- #


def production_services(db: Any) -> Any:
    """The real four seams over a caller-owned session.

    The session is a REQUIRED argument. This lane's writes (the round-robin cursor and the
    SLA row) are a unit of work of their own and must not ride the turn's routing
    transaction - a turn that fails later must not roll the assignment back out from under
    the person who was just told about it - but "its own session" is not the same as "a
    session nobody closes", which is what the first version left behind. `run()` opens one
    with `escalation_services.production_session(session_factory)` and closes it in the
    same breath.
    """
    from app.services.chatbot.lanes import escalation_services

    return escalation_services.build(db)
