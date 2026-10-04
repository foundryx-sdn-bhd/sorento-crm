"""The bridge between the new turn engine and production's own answer half
(PLAN-chatbot-answer-half-reattach.md slices R3 and R4; AC-1683, AC-1684, AC-1694,
AC-1697, AC-1698, AC-1699 to AC-1705).

One rule: the new engine decides WHICH turn this is; production decides WHAT the reply
says. `question_for` covers the two resolver exits a single-domain business turn can
raise BEFORE a fetch ever runs - `access_ask` (the price-tier picker) and `offer` (the
gate's own ambiguous-customer / ambiguous-product picker). `answer_for` (R4) covers the
MISS half - a genuine absence, either at the resolver's own exit or discovered once a
fetch has actually run - by calling production's own miss chain
(`not_found_error_message` -> `miss_suggest.run_miss_lane`) and the SAME text ladder
`question_for` uses (`tail.reply.compose_from_fragments`, R4's own extraction of
`engine.run_tail`'s body, so both walk one function, never two). Both functions return
`None` outside their own territory, so the caller falls back to `turn/compose.py` for
everything else (a multi-domain plan, a lane question, a team pick, R5's own hit arm).

**Bridge home, captain ruling 20 Sep 2026**: this module lives OUTSIDE `turn/`, as a
sibling of `turn_runtime.py` (the existing impure adaptor between the pure core and the
kept `lanes/business` + `tail` seams) - `turn/`'s own purity guard
(`test_rearch_s2_apply_is_pure.py`) forbids any file under that package from importing
`chatbot.tail`, and this module imports `tail.outcome` / `tail.reply` / `tail.compose`
freely, by design. No file under `turn/` may import this module either
(`test_rearch_r3_answer_bridge.py::TestTurnPackagePurity`).

**Access arm has two triggers, exactly production `complete_answer:1578-1588`**:
`exit_kind == "access_ask"` (the resolver's own exit for a contact with NO access rows
configured at all) OR the fetch fragment's own `_fetch_arm == "tier-ask"` (the real
"entitled, must pick a tier" path - `resolve_gate.run` exits `"continue"` here; the ASK
is decided one step later, inside `lanes.business.run_fetch`'s own tier-ask arm, once
the per-tier promotion probe has run - that is where the has/no-promotion stamps come
from). `tier_source` is `fetch` on the second trigger, `payload` on the first - measured
off `lanes/business/__init__.py::run_fetch`'s own `fetch_result` shape.

**MEASURED TRAP**: `answer.access_level_choice_message`'s own `tier_last_result_set`
rows carry `entity_type: "access_tier"` (`answer.py:324`), which
`turn/apply.py::_set_kind_field` does not recognise - only the literal `"tier"` reaches
`focus.tier` (`_CODE_ONLY_FIELDS`). Every tier option this bridge mints is rewritten to
`entity_type: "tier"` before it goes anywhere near `pending.ask`.

**The miss arm's two triggers (R4), exactly `lanes/business/__init__.py::
complete_answer:1650`**:

  (a) `payload["_exit_kind"] == "not_found"` - the resolver's own exit, nothing resolved
      at all (H11's zero-tool case). No fetch has run; `envelope` is `None`.
  (b) `envelope["raw_fragment"]` is the error arm `business._error_fragment(...,
      outcome="not_found")` mints - a genuine absence discovered once a fetch actually
      ran (`turn_runtime.make_tool_runner.runner`'s own "answered unfiltered" fallback,
      or a document tool refused for want of a filter). `payload` handed to the miss
      chain folds the fetch item in exactly as `complete_answer`'s own `delegate_payload`
      does: `{**payload, "fetch": fetch_item}`.

`answer_for` returns `None` for a hit-shaped envelope (R5's own arm), an `access_denied`
error arm (that refusal path is unchanged) and an infrastructure error arm (`outcome`
neither `not_found` nor `access_denied` - `complete_answer` RAISES on this arm today; a
caller reaching it here must not quietly render it as a miss). A multi-domain plan is a
CALLER-side decision (the engine never calls the bridge for one) and keeps `turn/
compose.py`'s own copy (owner ruling, Hazards section).

**Every question the miss arm raises is a `Pending` minted by `turn.pending.ask`
(AC-1684)**, in precedence order (mirrors `engine.run_tail`'s own `_question_offered`,
the same precedence `tail.reply_ladder.compose_reply`'s `result_set` reads):

  1. The CS member offer (`producers["build-cs-member-offer"]`), when it built one -
     `member_offer`, options from `cs_last_result_set`, `respond_user_id` on the
     payload so an answering turn can assign by it.
  2. A did-you-mean / require-specific roster (`offer["suggest_last_result_set"]`) -
     `{entity_kind}_pick` (contract 36 roster), options in printed order, each carrying
     the SAME has/no stamp the text prints (derived from the composed text itself - none
     of `run_miss_lane`'s own roster rows carry a `stamp` field, only the rendered line
     does), `payload["domain"]` / `payload["escalate_offered"]` so a pick continues the
     ORIGINAL ask (AC-1704).
  3. A plain escalate offer with no roster at all (`producers["escalate-catalog"].
     is_escalate_offer`) - `team_pick`, the same one-option "Yes" shape `engine.run_tail`'s
     own `_question_offered` mints for a canned lane's escalate catalog.

**AC-1705's cross-domain stock ladder** rides on every miss attempt
(`_run_crossdomain_ladder`, run BEFORE the miss text so its own team-name mutation
lands first - hand pass 9 item 2 - then `_apply_crossdomain_render` folds its render
onto the composed text afterwards): `answer.crossdomain_zeroset` / `run_crossdomain`
already gate themselves to `domain_hint in ("incoming", "inventory")` and a genuinely
resolved, probeable product, so calling them unconditionally is a no-op (no MCP call at
all) for every other domain's miss. `tail.compose.crossdomain_compose` folds the
rendered block above the escalate marker, exactly as it would for the (dead, pre-rearch)
`complete_turn` path - this is genuinely NEW wiring for the single-domain miss, not a
reattachment of something the new engine already ran (measured: neither function has a
live caller on this branch before R4).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Mapping

from app.services.chatbot.lanes.business import answer as answer_mod
from app.services.chatbot.lanes.business import miss_suggest as miss_mod
from app.services.chatbot.lanes.business import services as business_services
from app.services.chatbot.lanes.business.services import AnswerServices
from app.services.chatbot.tail import compose as tail_compose
from app.services.chatbot.tail import reply as reply_mod
from app.services.chatbot.tail import scope_block
from app.services.chatbot.turn import compose as turn_compose
from app.services.chatbot.turn import fetch as run_fetch
from app.services.chatbot.turn import pending
from app.services.chatbot.turn.state import escalation_barred, is_staff_profile, offers_escalation

# AC-1691's umbrella: no roster is ever asked with fewer than two options, in any
# domain and for any entity kind - `narrow.decide`'s own rule for every other roster
# kind, applied here too.
_MIN_ROSTER_OPTIONS = 2

_ORDER_DATE_LINE_RE = re.compile(r"^Order date: .*$")

logger = logging.getLogger(__name__)


def apply_scope_block(
    answer: turn_compose.Answer,
    *,
    domain: str | None,
    qf: Mapping[str, Any] | None,
    gate_json: Mapping[str, Any] | None,
    resolver_json: Mapping[str, Any] | None,
    focus_customers: Any = None,
    focus_products: Any = None,
) -> turn_compose.Answer:
    """R5's HIT arm (AC-1694 to AC-1696): a single-domain ORDER fetch that genuinely
    found rows opens with the Customer/Product/Dates scope block ahead of
    production's own intro. The caller's own `Answer` (from `turn/compose.py`,
    unchanged - this never touches `sections`/`question`/`offer`/`files`/`actions`)
    is returned AS-IS for every other domain (`tail.scope_block.search_scope_header`
    returns `None`).

    `turn/compose.py`'s own per-section `date_line` decoration
    (`"Order date: ..."`, one line under the header) restates the SAME window the
    scope block's own `Dates:` line already states, once this arm applies - stripped
    here rather than suppressed at its own seam, which would need to know in advance
    whether this bridge was about to run.

    BEST EFFORT, main's own wrapper verbatim in intent ("a disclosure bug must never
    block the answer", `origin/main:tail/compile_state.py::_search_scope_header`): the
    port had no wrapper of its own, so a raise anywhere in here was caught by
    `engine.py`'s fetch/compose handler instead and turned a turn that had genuinely
    found rows into `status="failed"` with "Could not look an answer up." The rows go
    out without the header rather than not at all.
    """
    try:
        scope = scope_block.search_scope_header(
            domain=domain,
            qf=qf,
            gate_json=gate_json,
            resolver_json=resolver_json,
            focus_customers=focus_customers,
            focus_products=focus_products,
        )
        if scope is None or not answer.text:
            return answer
        from dataclasses import replace

        lines = [line for line in answer.text.split("\n") if not _ORDER_DATE_LINE_RE.match(line)]
        return replace(answer, text=f"{scope}\n\n" + "\n".join(lines))
    except Exception:  # noqa: BLE001 - a disclosure bug must never block the answer
        logger.warning("chatbot: the search-scope header did not render", exc_info=True)
        return answer


def apply_crossdomain_hit(
    answer: turn_compose.Answer,
    *,
    envelope: Mapping[str, Any] | None,
    parser: Mapping[str, Any] | None,
    resolved: Any,
    entities_names: Any,
    crossdomain_ladder: Mapping[str, Any] | None,
    ctx: Any,
    services: AnswerServices,
    contact_id: Any,
    space_id: str | None,
    trace: Any = None,
    dry_run: bool = True,
    asked_at_turn: int | None = None,
    turn_id: str | None = None,
    focus_products: Any = None,
    profile: Any = None,
) -> turn_compose.Answer:
    """Hand pass 11, defect 1: a single-domain inventory/incoming HIT whose rows all
    read 0 on hand climbs the SAME cross-domain ladder a miss does, instead of
    printing the zero stock summary alone. Production's own HIT arm
    (`origin/main lanes/business/__init__.py:1585-1650`) calls `answer.
    run_crossdomain` on every answered turn, not only a miss; the caller's own
    `envelope_missed` gate keeps this bridge's MISS arm and `turn/fetch.py::_climb`
    from ever reaching a HIT at all (rows exist, so neither trigger fires), which is
    the gap this closes - one ladder per turn either way
    (`bridge_owns_ladder` covers this arm too).

    `envelope` is the fetch's OWN envelope (`turn_runtime.envelope_of`'s return) - its
    OWN rows live under `figures` (`envelope_of` renames `fetch.answers` to `figures`
    on the way in; `turn/fetch.py::_fetch_one` never re-adds an `answers` key of its
    own). `crossdomain_zeroset` needs the ORIGINAL `answers`/`fields` shape
    (`_envelope_items` tries `answers` before `items`, and `_field_val` reads each
    row's `fields`) - `figures` already carries that shape verbatim (`envelope_of`'s
    own `figures = [r for r in rows if isinstance(r, dict)]`, no re-keying), so
    `{"answers": envelope.get("figures") or []}` is the adapter, not a second reader.
    A no-op (byte-identical `answer`) for every PARSER `domain_hint` but
    inventory/incoming (`crossdomain_zeroset`'s own gate, `lanes/business/answer.py:
    481-484` - a statement about the hint, never about which domain this HIT's own
    fetch actually answered) and for a turn with nothing zero to probe
    (`_run_crossdomain_ladder`'s own "no MCP call" case), so calling this
    unconditionally on every single-domain HIT costs nothing on the other ~99% of
    turns. No `domain` parameter of its own (nit N-1, security review): nothing here
    ever read the fetched domain, and the docstring naming it invited exactly this
    misreading.

    BEST EFFORT, the same convention `apply_scope_block` uses one function up: a
    turn that genuinely found rows must never fail because this ladder could not
    run.

    Security N-3 (hand pass 11 security review): a rung that fires appends
    `tail.compose.crossdomain_compose`'s own locked "Would you like me to escalate
    to X team?" phrase to `text`, but minted no `Pending` of its own - the miss arm's
    identical phrase does, through `_miss_question`'s bare "Yes" arm, so a customer's
    "yes" answered a HIT-side offer with nothing to match against. `_crossdomain_offer_
    pending` mints the SAME `team_pick` shape, team off the rung's own `_xdBlock["team"]`
    (`lanes/business/answer.py:1079`, set from `crossdomain_zeroset`'s own `team`
    read at `:489` - the exact value the phrase itself prints; owner ruling 22 Sep
    2026, R6 retired the PO-rung override that used to overwrite it after the fact,
    so this is now the question's OWN origin-domain team, unconditionally).

    `focus_products` (hand pass 12 round 3, R5, owner ruling): a DID-YOU-MEAN PICK
    runs no resolver of its own (`resolver_payload is None`, the SAME fact `_ladder_
    resolved`'s own docstring names for the MISS arm), so `resolved` alone has nothing
    for `crossdomain_zeroset`'s own `resolved.intersection` read to climb from - the
    ladder fired for a DIRECT ask (a fresh resolve) but never after a pick settled the
    SAME product. Fed in the SAME shape `_ladder_resolved` already builds, from the
    FOCUS carry `apply_scope_block`'s own `focus_products` parameter already reads
    one call up (`engine.py`'s `state_out.focus.products`) - the picked product's real
    `canonical_code`/`uuid`, never a second resolve.
    """
    try:
        if not answer.text or not isinstance(envelope, Mapping):
            return answer
        figures = envelope.get("figures")
        item = {"answers": [r for r in figures if isinstance(r, dict)]} if isinstance(figures, list) else {}
        # Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not merged, owner
        # ruling 24 Sep 2026) for chatbot-stock-ask-v2 S3, D17, review round 10: the
        # DEALER's availability block rides along with the rows, because it is what
        # turns the whole ladder off (`crossdomain_zeroset`'s own `stock_availability`
        # gate). Rebuilt from `figures` alone, this item lost the block, so that gate
        # never saw it on a HIT and the ladder listed rungs beside the verdict and
        # offered an escalation the dealer had not asked for. A stray offer also
        # competes with the dealer's own open stock task.
        availability = envelope.get("stock_availability")
        if isinstance(availability, list) and availability:
            item["stock_availability"] = availability
        result = _run_crossdomain_ladder(
            parser=parser,
            resolved=_hit_ladder_resolved(resolved, focus_products),
            entities_names=entities_names,
            crossdomain_ladder=crossdomain_ladder,
            ctx=ctx,
            services=services,
            contact_id=contact_id,
            space_id=space_id,
            trace=trace,
            dry_run=dry_run,
            item=item,
        )
        result = _prefix_zero_note(result)
        from dataclasses import replace

        # ESCALATION-CONTROL: staff and a barred contact alike get no offer.
        is_staff = not offers_escalation(profile)
        text = _apply_crossdomain_render(answer.text, result, answered=True, include_offer=not is_staff)
        if text == answer.text:
            return answer
        # #1262 slice 11 (F8): staff get the rung's own rendered block (the text
        # change above still fires) but no escalation offer ARMED either - a
        # `team_pick` with no visible sentence pointing at it is the same "offer
        # nobody was shown" gap the composer's own arm closes.
        #
        # Reviewer N-d: no `else answer.question` arm otherwise, because it was
        # unreachable - `_apply_crossdomain_render` changed the text, and that is the
        # SAME `_xdBlock` `any`/`block` gate `_crossdomain_offer_pending` reads, so
        # the pending is never `None` past the equality check above.
        question = None if is_staff else _crossdomain_offer_pending(result, asked_at_turn=asked_at_turn)
        return replace(answer, text=text, question=question)
    except Exception:  # noqa: BLE001 - a disclosure bug must never block the answer
        logger.warning(
            "chatbot turn %s: the cross-domain zero-stock ladder did not run", turn_id, exc_info=True
        )
        return answer


def _crossdomain_offer_pending(
    result: Mapping[str, Any], *, asked_at_turn: int | None
) -> pending.Pending | None:
    """The bare "Yes" `team_pick` `_miss_question`'s catalog arm mints for the
    identical locked phrase on a miss - `None` when the rung rendered nothing
    (`_apply_crossdomain_render`'s own gate, so a caller checking `text == answer.text`
    first never needed to call this at all)."""
    render = result.get("render") if isinstance(result, Mapping) else None
    block = render.get("_xdBlock") if isinstance(render, Mapping) else None
    if not isinstance(block, Mapping) or block.get("any") is not True or not block.get("block"):
        return None
    team = block.get("team")
    # SRTSC07 review round 2, item 3 (agent) / round 4 (brand, owner-approved, same
    # reason): deliberately UNSTAMPED, both axes. `team` above comes off the
    # cross-domain RUNG's own render block, not off this turn's `routing`/`gate` at
    # all (this function takes neither as a parameter to read one from) - there is
    # no "this turn's agent" or "this turn's brand" to stamp here, so the carry
    # falls through to the default chain exactly as it did before either fix.
    return pending.ask(
        "team_pick",
        [{"position": 1, "label": "Yes", "entity_type": "team", "payload": {}}],
        team=team if isinstance(team, str) and team else None,
        asked_at_turn=asked_at_turn,
        expects="yes_no",
    )


def apply_silent_company_offer(
    answer: turn_compose.Answer,
    *,
    envelope: Mapping[str, Any] | None,
    parser: Mapping[str, Any] | None,
    gate: Mapping[str, Any] | None = None,
    asked_at_turn: int | None = None,
    turn_id: str | None = None,
    db: Any = None,
    ctx: Any = None,
    profile: Any = None,
) -> turn_compose.Answer:
    """Hand pass 11, defect 3 (multi-company HIT parity): a HIT in ONE of several
    searched companies still offers to escalate to the SILENT company's own team -
    production's own shape (owner: "for items that exist in sorento and mocha, we
    search both sides ... offer to escalate to either company"). The rows and the
    silent-company sentence itself are ALREADY correct today (`lanes/business/
    fetch.py:2483-2531`, unchanged production, confirmed reading `envelope.
    lookup_companies`/`shown_cos` off the SAME fetch item this reads) - only the
    OFFER, which a HIT never had a reason to print before, is missing.

    `envelope` is `turn_runtime.envelope_of`'s return; its `raw_fragment.fetch` is
    the untouched `lanes.business.fetch.output_structurer` output (`envelope_of`
    only ever RENAMES `fetch.answers` to `figures` on the outer dict, the
    `raw_fragment` it also carries is the ORIGINAL) - `lookup_companies` and the
    rows' own `company_name`/"Company" field live there, nowhere else on the outer
    envelope. Best effort and a no-op (byte-identical `answer`) whenever
    `lookup_companies` has one company or none, or every searched company already
    shows a row - the ~99% of single-company turns this must never touch.

    Reviewer S2 / N-3 (hand pass 11): a HIT arm that only replaces `text` leaves
    `answer.question` `None`, so the offer's own "yes" had nothing to answer - the
    follow-up reached escalation only through the parser's generic
    `is_escalation_confirmation`, with no company at all. This mints the SAME
    `team_pick` shape `_miss_question`'s escalate-catalog arm mints for a miss - one
    option per silent company, `payload: {"company": name, "company_id": id}` - so a
    later "yes" escalates WITH the company already named, through the EXISTING
    `parser.escalation.company_pick` seam (`lanes/escalation.py:232-252`); no new
    pending kind. The **id** is what routes (`_next_assignee_body` posts `company_id`
    and nothing else), and `lookup_companies` entries carry it already
    (`app/services/company_scope.py:437-439`, `{"id", "name"}`).

    ONE silent company, never two (hand pass 11, reviewer blocker 2): production caps
    the identical derivation at one for a measured reason - `sub_answer.miss_roster_plan
    :318-323`, "two or more would persist a multi-entry `routing_roster_plan`, which the
    escalation lane turns into `routing_source: multi_company_unpicked` ... and hands to
    a real round-robin assign on a pool the customer never picked". The MISS arm has a
    clarify to fall back on because its own sentence already names the companies it
    searched; a HIT's sentence names none, so the honest offer is the single one.
    """
    try:
        if escalation_barred(profile):
            # ESCALATION-CONTROL: a barred contact is offered no team, silent or not.
            return answer
        if not answer.text or not isinstance(envelope, Mapping):
            return answer
        if answer.question is not None:
            # SF-4 / security should-fix 1: the lane already has an open question of its
            # own (the outstanding / sales-report detail offers, `turn/compose.py::
            # _lane_question`, contracts 38 and 39, or the zero-stock ladder's own rung
            # offer one bridge up). ONE escalate question per turn: appending a second
            # discarded the first, and the customer's follow-up then answered a question
            # they were never asked.
            return answer
        if envelope.get("denied") or run_fetch.envelope_missed(dict(envelope)):
            # Security N-2: this function's own guard, not just the call site's. It mints
            # a PENDING now, so a second caller would hang an answerable escalate offer
            # off a refusal or a miss - neither of which has rows to be silent about.
            return answer
        raw_fragment = envelope.get("raw_fragment")
        fetched = raw_fragment.get("fetch") if isinstance(raw_fragment, Mapping) else None
        if not isinstance(fetched, Mapping):
            return answer
        lookup_cos = fetched.get("lookup_companies")
        if not isinstance(lookup_cos, list) or len(lookup_cos) < 2:
            return answer
        rows = fetched.get("answers")
        rows = rows if isinstance(rows, list) else []

        def _row_company(row: Any) -> str:
            for f in row.get("fields") or [] if isinstance(row, Mapping) else []:
                key = str(f.get("key") or "").strip().lower() if isinstance(f, Mapping) else ""
                label = str(f.get("label") or "").strip().lower() if isinstance(f, Mapping) else ""
                if key == "company_name" or label == "company":
                    return str(f.get("value") or "").strip()
            return ""

        shown = {c for c in (_row_company(r) for r in rows) if c}
        if not shown:
            return answer
        silent = [
            {"name": str(c.get("name") or "").strip(), "id": c.get("id")}
            for c in lookup_cos
            if isinstance(c, Mapping) and str(c.get("name") or "").strip() and str(c.get("name") or "").strip() not in shown
        ]
        if len(silent) != 1:
            # Production's own cap, and its own degradation: exactly one, or nothing at
            # all (`sub_answer.miss_roster_plan:318-323`).
            return answer
        company = silent[0]
        routing = (parser or {}).get("routing") if isinstance(parser, Mapping) else None
        raw_team = (routing or {}).get("suggested_team") or "customer_service"
        team = answer_mod._pretty_team(raw_team)
        offer = f"Would you like me to escalate to *{company['name']}* {team} team?"
        question = pending.ask(
            "team_pick",
            [
                {
                    "position": 1,
                    "label": company["name"],
                    "entity_type": "team",
                    "payload": {
                        "company": company["name"],
                        "company_id": company["id"],
                        # Reviewer SF-3 (hand pass 11 final): no join against
                        # `gate.routing_companies` any more - a brand nothing verified
                        # for the SILENT company was scope beyond this offer's own
                        # blocker. Key kept so the roster row shape stays the same.
                        "brand_code": None,
                    },
                }
            ],
            team=raw_team,
            asked_at_turn=asked_at_turn,
            expects="yes_no",
            # SRTSC07 review round 2, item 3: `raw_team` is already THIS turn's own
            # `routing.suggested_team`, so the agent half rides beside it on the
            # pending's top-level payload, the same as `_miss_question`'s own
            # bare-"Yes"/company-clarify arms - a bare "yes" over this offer answers
            # it without a position. `brand_code` (round 4, owner-approved) is the
            # SAME turn-level idiom, off the `gate` this function already takes as a
            # parameter - never the option's OWN `brand_code` above, which stays
            # `None` for the SILENT company (reviewer SF-3, unchanged).
            payload={
                "agent": (routing or {}).get("suggested_agent"),
                "brand_code": gate.get("routing_brand") if isinstance(gate, Mapping) else None,
            },
        )
        from dataclasses import replace

        picker = _miss_company_picker(
            answer.text,
            offer,
            company=company,
            routing=routing or {},
            db=db,
            ctx=ctx,
            asked_at_turn=asked_at_turn,
            brand=gate.get("routing_brand") if isinstance(gate, Mapping) else None,
        )
        if picker is not None:
            return replace(answer, text=f"{answer.text}\n\n{picker[0]}", question=picker[1])
        return replace(answer, text=f"{answer.text}\n\n{offer}", question=question)
    except Exception:  # noqa: BLE001 - a disclosure bug must never block the answer
        logger.warning(
            "chatbot turn %s: the silent-company escalate offer did not render", turn_id, exc_info=True
        )
        return answer


#: n8n `miss-roster-gate` LANE rev-3/rev-4: the member picker is for customer order
#: enquiries ONLY ("there is no need to get the members for incoming lol, this only applies
#: for customer order enquiries"); every other lane keeps the plain company-named phrase.
_MISS_PICKER_ROUTING = ("customer_service", "order_enquiries")

_NUMBERED_LINE = re.compile(r"^(\d+)\.\s", re.MULTILINE)


def _miss_company_picker(
    text: str,
    offer: str,
    *,
    company: Mapping[str, Any],
    routing: Mapping[str, Any],
    db: Any,
    ctx: Any,
    asked_at_turn: int | None,
    brand: Any,
) -> tuple[str, pending.Pending] | None:
    """#865 round 6 (R1), n8n `build-miss-member-offer` (UAC M1): a partial miss on an
    order enquiry offers the MISS company's own customer service members, numbered on
    from the reply's own numbered blocks ("a stray 2 must still pick the right row"),
    then the single-company yes sentence. `(text, member_offer pending)`, or None - off
    the order routing pair, without a session, or when that company has no member - in
    which case the plain offer stands.
    """
    if (routing.get("suggested_team"), routing.get("suggested_agent")) != _MISS_PICKER_ROUTING or db is None:
        return None
    from app.services.chatbot.tail import member_offer as member_mod

    plan = [
        {
            "plan_idx": 0,
            "company_id": company.get("id"),
            "company_name": company["name"],
            "brand_code": None,
            "codes": [],
            "multi_company": False,
            "companies": [company["name"]],
        }
    ]
    built = member_mod.build_cs_member_offer(
        {"response": offer}, plan, member_mod.fetch_rosters(db, plan, ctx if isinstance(ctx, Mapping) else {})
    )
    rows = built.get("cs_last_result_set") or []
    if built.get("member_offer") is not True or not rows:
        return None
    offset = max((int(n) for n in _NUMBERED_LINE.findall(text or "")), default=0)
    options = [
        option
        for option in (member_option({**row, "idx": offset + i + 1}, offset + i + 1) for i, row in enumerate(rows))
        if option
    ]
    if not options:
        # CHATBOT-EMPTY-ROUTE-PICK: a picker never renders with zero rows; the plain
        # offer stands.
        return None
    lines = "\n".join(f"{o['position']}. {o['label']}" for o in options)
    picker_text = f"{offer}\n\n{member_mod.PICKER_HEADER}\n{lines}\n\n{member_mod.PICKER_CLOSE}"
    question = pending.ask(
        "member_offer",
        options,
        team=routing.get("suggested_team"),
        asked_at_turn=asked_at_turn,
        payload={
            "agent": routing.get("suggested_agent"),
            "brand_code": brand,
            "roster_plan": [{"company_id": company.get("id"), "company_name": company["name"], "brand_code": None}],
        },
    )
    return picker_text, question


def _compose_text(lane_item: dict[str, Any], *, ctx: Any, canned: Any, db: Any, **values: Any) -> str:
    """`tail.reply.compose_from_fragments`, the SAME chain `complete_answer` walks for
    every canned/business reply - the plan's own "text = tail/outcome.escalate_catalog +
    tail/reply_ladder.compose_reply" line, extracted once (R4) so no arm in this module
    keeps a second copy of the ladder."""
    result = reply_mod.compose_from_fragments(lane_item, ctx, canned, values, db=db)
    return result.get("text") or ""


def _roster_option(row: dict[str, Any], *, kind: str, position: int) -> dict[str, Any] | None:
    """One roster row -> a `Pending` option, in the SAME shape `turn/narrow.py::_options`
    produces (contract 103) - EVERY arm this module mints a roster from shares this one
    builder (coordinator add-on, 20 Sep 2026: `session_state._legacy_option`'s own
    `uuids` only fills through a customer-only `families` lookup this module never
    supplies, so a lone product roster carried no `uuids` at all).

    `uuids` is always populated: a resolver/candidate row's own family (`row["uuids"]`)
    when it has one, else the single `uuid` standing for itself - never empty when a
    `uuid` exists, matching `narrow._options`'s own rule that a lone candidate is still
    a family of one.
    """
    if not isinstance(row, dict):
        return None
    uuid_val = row.get("uuid")
    code = row.get("code") or row.get("canonical_code") or row.get("product") or row.get("value")
    label = row.get("title") or row.get("name") or row.get("label") or code
    family = row.get("uuids")
    uuids = list(family) if isinstance(family, list) and family else ([uuid_val] if uuid_val else [])
    option: dict[str, Any] = {
        "position": int(row.get("idx") or position),
        "label": label,
        "entity_type": row.get("entity_type") or kind,
        "uuid": uuid_val,
        "uuids": uuids,
        "payload": {},
    }
    if code is not None:
        option["code"] = code
    # AC-1695: the SAME field `turn/narrow.py::_options` carries beside `label` - a
    # customer's own human name, separate from its code, so `turn/apply.py::
    # _answer_pending`'s own pick-to-entity conversion (which reads `option.get(
    # "name")` specifically, not "label") can put the NAME on the focus entity a
    # pick just settled, not the account code nobody typed. Missing here left a
    # picked customer's own scope-block line reading its code back at the
    # customer who just read the name on the roster. `title` first: the gate's own
    # ambiguous-customer picker rows (`resolve_gate.if_customer_picker`) carry the
    # printed name under `title`, never `name` (measured live writing this fix).
    name = row.get("title") or row.get("name")
    if name:
        option["name"] = name
    stamp = row.get("stamp")
    if stamp:
        option["stamp"] = stamp
    for key in ("value", "team"):
        if row.get(key) is not None:
            option["payload"][key] = row[key]
    return option


def _tier_options(rows: list[dict[str, Any]], *, asked_at_turn: int | None) -> pending.Pending | None:
    """`tier_last_result_set` rows -> a `tier_pick` `Pending`, or `None` when fewer than
    two tiers are on offer (AC-1691) - the caller then has a text-only `Answer`, settling
    the single tier outright rather than asking about it."""
    fixed_rows = [{**row, "entity_type": "tier"} for row in rows]
    options = [
        option
        for option in (
            _roster_option(row, kind="tier_pick", position=i + 1)
            for i, row in enumerate(fixed_rows)
        )
        if option
    ]
    if len(options) < _MIN_ROSTER_OPTIONS:
        return None
    # PR #1353 fix round 1: the domain this roster was asked FOR, as every other roster
    # records it (contract 121, `_offer_answer` below). With no `domain` the pick
    # judgement (`apply._answer_pending`) had no roster domain to compare the message's
    # own "promotion" against, so a pick whose verdict kept `domain_hint: promotion`
    # read as a pick in ANOTHER domain. The tier ask is the promotion lane's alone
    # (`tier_gate.needs_tier_ask`).
    return pending.ask("tier_pick", options, asked_at_turn=asked_at_turn, payload={"domain": "promotion"})


def _access_ask_answer(
    payload: dict[str, Any] | None,
    *,
    fetch: dict[str, Any] | None,
    parser: dict[str, Any] | None,
    ctx: Any,
    canned: Any,
    db: Any,
    asked_at_turn: int | None,
) -> turn_compose.Answer:
    if isinstance(fetch, dict) and fetch.get("_fetch_arm") == "tier-ask":
        tier_source = fetch
    elif isinstance(payload, dict) and payload.get("_exit_kind") == "access_ask":
        # `resolve_gate.run`'s own `exit_item` spreads `tier_gate_out` FIRST for this
        # exit (`resolve_gate.py`'s own access_ask arm), so `payload` itself already
        # carries `name`/`entitled_tiers` at the top level.
        tier_source = payload
    else:
        # R5 (AC-1697's own tier axis, the "product_attachment roster copy" class of
        # gap on the ACCESS side): a resolver `not_found` exit for a promotion ask
        # still carries the SAME tier-gate read (`resolve_gate.run`'s `entry ==
        # "access_check"` runs before the product is even looked up), just nested
        # under `payload["tier_gate"]` rather than flattened - the product genuinely
        # not resolving is not a verdict on which access levels this contact holds.
        # Measured live: `TestRegressionGuardsMustStayGreen::
        # test_promotion_ask_for_a_not_found_product_still_shows_the_three_tier_picker`,
        # a contact entitled to 3 tiers whose typed product never resolves at all
        # (`_exit_kind == "not_found"`, `tier_gate.tier_ask is True`) used to fall
        # through to `turn/narrow.py`'s generic, entitlement-blind tier ask instead of
        # this composer.
        tier_source = payload.get("tier_gate") if isinstance(payload, dict) else None
    lane_item = {
        **answer_mod.access_level_choice_message(tier_source, parser=parser),
        "branch_kind": "access_choice",
    }
    text = _compose_text(lane_item, ctx=ctx, canned=canned, db=db, access_choice=lane_item)
    rows = lane_item.get("tier_last_result_set")
    rows = rows if isinstance(rows, list) else []
    question = _tier_options(rows, asked_at_turn=asked_at_turn)
    return turn_compose.Answer(text=text, question=question)


def _picker_family_uuids(payload: Mapping[str, Any], uuid_val: Any) -> list[str] | None:
    """AC-1694/contract 103: a customer roster option's `uuids` must be its WHOLE
    family (every company's account for that trading name), not just the
    representative row `gate.py`'s own `compatible_entities` carries.

    `gate.py`'s `compatible_entities` shape is pinned byte-for-byte by
    `test_replay.py`'s recorded n8n capture, so the family cannot be added there -
    but `payload["picker_families"]` (`{base name: [every uuid in that family]}`) is
    gate.py's own GENUINE, pre-existing field for exactly this (already consumed by
    `session_state.py`'s legacy carry). Invert it once per turn and look the
    representative's own uuid up in it - a representative is always a member of its
    own family."""
    families = payload.get("picker_families")
    if not isinstance(families, dict) or uuid_val is None:
        return None
    key = str(uuid_val)
    for family in families.values():
        if isinstance(family, list) and key in {str(u) for u in family}:
            return [str(u) for u in family]
    return None


def _offer_answer(
    payload: dict[str, Any],
    *,
    parser: dict[str, Any] | None,
    ctx: Any,
    canned: Any,
    db: Any,
    asked_at_turn: int | None,
) -> turn_compose.Answer:
    lane_item = {**payload, "branch_kind": "not_found"}
    # The resolver's OWN gate (`require_specific` is True for both the customer and the
    # incoming/product picker exits, per `resolve_gate.if_incoming_picker` /
    # `if_customer_picker`) - passed through so `compose_from_fragments`'s own
    # `cs_offer_gate` correctly blocks the CS-member-offer merge (its g4 condition) the
    # same way it does for every other require-specific picker, rather than reading
    # `gate=None` as "no opinion, merge is allowed".
    text = _compose_text(
        lane_item, ctx=ctx, canned=canned, db=db, incoming_picker=lane_item, gate=payload.get("gate")
    )
    entities = [e for e in (payload.get("compatible_entities") or []) if isinstance(e, dict)]
    question = None
    if len(entities) >= _MIN_ROSTER_OPTIONS:
        entity_kind = entities[0].get("entity_type") or "product"
        kind = f"{entity_kind}_pick"
        rows = entities
        if entity_kind == "customer":
            rows = [
                {**e, "uuids": family} if family else e
                for e, family in (
                    (e, _picker_family_uuids(payload, e.get("uuid"))) for e in entities
                )
            ]
        options = [
            option
            for option in (
                _roster_option(row, kind=kind, position=i + 1) for i, row in enumerate(rows)
            )
            if option
        ]
        if len(options) >= _MIN_ROSTER_OPTIONS:
            # AC-1704/contract 121: the domain a pick continues answering FOR - missing
            # here (`pending.ask` with no `payload=` at all) left `payload.domain` unset
            # and a bare number's answer unable to say which ask it settled. `status`
            # is the SAME carry `_answer_outstanding` already does for
            # `outstanding_detail`/`sales_report_detail` (`focus.status`, projected
            # onto `order_status` by `turn_runtime.lane_parse_output`) - a sales
            # report's own ambiguous-customer pick otherwise answers "1" with the
            # generic order tool, never `crm_sales_report`, since the ASKING turn's
            # `order_status` reached the verdict directly (no `document`/`status`
            # pair for `_focus_rules` to carry it on `focus.status` itself) and the
            # answering turn's own verdict never repeats it.
            ask_payload: dict[str, Any] = {"domain": (parser or {}).get("domain_hint")}
            # AC-1708: and EVERY domain the message named, when it named more than one.
            # `domain` alone settled the focus to a single one (`turn/apply.py`'s
            # contract-121 carry), so answering a two-domain ask's customer picker
            # fetched the first section and silently dropped the second. Read off the
            # verdict's own `asks` (contract 122), the same way `turn/apply.py` reads
            # it, and answered by the same reader, which already takes `domains` first.
            ask_domains = [
                row.get("domain")
                for row in ((parser or {}).get("asks") or [])
                if isinstance(row, dict) and row.get("domain")
            ]
            if len(ask_domains) > 1:
                ask_payload["domains"] = ask_domains
            order_status = (parser or {}).get("order_status")
            if order_status:
                ask_payload["status"] = order_status
            question = pending.ask(
                kind,
                options,
                # D4 (hand pass 9): this roster's OWN `team` was never stamped at
                # all, so a pick over it that misses had nothing to restore its
                # team from and fell to the generic default ("customer service")
                # instead of the domain's own team ("purchasing" for incoming) -
                # `_miss_question`'s SAME roster-mint (item 2 above) already
                # stamps `team=` this exact way. Not the domain's policy-level
                # `escalation_team_code` (unavailable here): the ASKING turn's own
                # verdict already routed correctly (`suggested_team`), the same
                # field this bridge trusts everywhere else.
                team=(parser or {}).get("routing", {}).get("suggested_team"),
                asked_at_turn=asked_at_turn,
                payload=ask_payload,
            )
    return turn_compose.Answer(text=text, question=question)


def question_for(
    payload: dict[str, Any] | None,
    *,
    fetch: dict[str, Any] | None = None,
    parser: dict[str, Any] | None,
    ctx: Any,
    canned: Any,
    db: Any = None,
    asked_at_turn: int | None,
) -> turn_compose.Answer | None:
    """The ASK seam (PLAN "Design"): `None` outside its two arms, so the caller falls
    back to `turn/compose.py` for a multi-domain plan, a lane question or a team pick.

    `fetch` is `lanes.business.run_fetch`'s own tier-ask fragment (its `"fetch"` key,
    not the wrapper dict `run_fetch` returns) - see the module docstring for the
    measured shape. `payload` is the resolver's own exit item
    (`ResolveOutcome.payload`). `db` is optional (R3's two arms never need a live CS
    roster read; `_offer_answer` hands the real gate through so `cs_offer_gate` blocks
    it on its own require_specific condition regardless).
    """
    if not isinstance(payload, dict):
        return None
    exit_kind = payload.get("_exit_kind")
    fetch_arm = fetch.get("_fetch_arm") if isinstance(fetch, dict) else None
    tier_gate = payload.get("tier_gate")
    # R5: a `not_found` exit still needing a tier pick (the product never resolved,
    # but `entry == "access_check"` already ran the tier gate before it tried) - the
    # "continue" exit's own tier-ask (`fetch_arm == "tier-ask"`, run AFTER the real
    # per-tier promotion probe) is untouched; this is the OTHER exit that same gate
    # can leave the resolver on, never overlapping with it (`_access_ask_answer`'s own
    # `tier_source` docstring names the shape difference).
    needs_tier_ask = (
        exit_kind == "not_found"
        and isinstance(tier_gate, dict)
        and tier_gate.get("tier_ask") is True
    )
    if exit_kind == "access_ask" or fetch_arm == "tier-ask" or needs_tier_ask:
        return _access_ask_answer(
            payload, fetch=fetch, parser=parser, ctx=ctx, canned=canned, db=db, asked_at_turn=asked_at_turn
        )
    if exit_kind == "offer":
        return _offer_answer(
            payload, parser=parser, ctx=ctx, canned=canned, db=db, asked_at_turn=asked_at_turn
        )
    return None


# --------------------------------------------------------------------------- #
# R4 - the miss arm
# --------------------------------------------------------------------------- #

_NUMBERED_LINE_RE = re.compile(r"^\s*(\d+)\.\s*(.+?)\s*$")


def _stamps_by_position(text: str) -> dict[int, str]:
    """Position -> the has/no suffix that line prints, whatever follows the LAST
    " - " on a numbered line. None of `run_miss_lane`'s own roster rows
    (`suggest_last_result_set`) carry a `stamp` field - every arm (the sibling picker,
    the D1 did-you-mean, the require-specific picker) bakes the suffix into the
    rendered TEXT only - so the option's own stamp is read back off the SAME text the
    customer sees, never re-derived from a probe a second time."""
    stamps: dict[int, str] = {}
    for line in (text or "").split("\n"):
        match = _NUMBERED_LINE_RE.match(line)
        if not match:
            continue
        content = match.group(2)
        if " - " in content:
            stamps[int(match.group(1))] = content.rsplit(" - ", 1)[1].strip()
    return stamps


def member_option(row: dict[str, Any], position: int) -> dict[str, Any] | None:
    """A `cs_last_result_set` row as a `member_offer` option.

    Hand pass 12 Phase 3 finding P2 (corrects this docstring's own earlier claim):
    an answering turn assigns the escalation BY the option's own `uuid` (a real
    `users.id`) - `turn/apply.py::_answer_offer` stamps `trace.assignee` from it,
    and `/external/next-assignee`'s own `get_member_assignee` matches
    `preferred_assignee_id` against `TeamMember.user_id`, never against a
    respond.io id. `respond_user_id` still rides on the payload (not
    `session_state._legacy_option`'s own value/team pair) because it is what a
    dry-run preview and any OTHER reader of this option (never the assign call
    itself, which re-reads the resolved member's own respond id from the DB)
    needs without a second lookup."""
    if not isinstance(row, dict):
        return None
    return {
        "position": int(row.get("idx") or position),
        "label": row.get("label"),
        "entity_type": "member",
        "uuid": row.get("uuid"),
        "payload": {
            "respond_user_id": row.get("respond_user_id"),
            # #865 round 6: the company whose roster listed this member, so picking them
            # routes to that company (`turn/apply.py::_answer_offer` hands it on as the
            # company pick) - n8n's `picked_member` arm, the row's own pair verbatim.
            "company": row.get("company_name"),
            "company_id": row.get("company_id"),
            "brand_code": row.get("brand_code"),
        },
    }


def roster_plan_of(member: Any) -> list[dict[str, Any]]:
    """The companies a built member picker printed rosters for, as pool rows
    (`tail/member_offer.build_cs_member_offer` keeps its plan on `routing_companies`)."""
    plan = member.get("routing_companies") if isinstance(member, Mapping) else None
    return [
        {
            "company_id": row.get("company_id") or None,
            "company_name": row.get("company_name"),
            "brand_code": row.get("brand_code") or None,
        }
        for row in (plan if isinstance(plan, list) else [])
        if isinstance(row, Mapping) and row.get("company_name")
    ]


def _cs_offer_eligible(catalog: Any, routing: Mapping[str, Any], gate: Any) -> bool:
    """`tail/outcome.py::cs_offer_gate`'s own g1/g2/g3/g4, minus its g4b - the ONE
    condition this bridge deliberately does not inherit (hand pass 12 round 3, owner
    ruling R2): g4b blocks `cs_offer_gate` outright whenever a did-you-mean roster
    already exists, which is exactly the shape this bridge now COMBINES rather than
    drops. `cs_offer_gate` itself stays untouched - `engine.run_tail`'s own canned /
    escalation / casual lanes still walk it unmodified, and this bridge is where the
    plan's own "Bridge home" ruling put every divergence from that shared ladder."""
    g1 = isinstance(catalog, Mapping) and catalog.get("is_escalate_offer") is True
    g2 = routing.get("suggested_team") == "customer_service"
    g3 = routing.get("suggested_agent") == "order_enquiries"
    g4 = gate is None or not (isinstance(gate, Mapping) and gate.get("require_specific") is True)
    return g1 and g2 and g3 and g4


def _cs_roster_text_block(member_options: list[dict[str, Any]]) -> str:
    """The escalation-offer sentence a combined roster appends to the did-you-mean text,
    production wording verbatim off turn 99c114fd's own second message (`.claude/
    handpass/hp12-turns-21sep.json`): "To escalate, choose who to route to. Reply the
    number or name: ... Or just reply 'yes' and we'll assign automatically." Positions
    are read straight off the ALREADY-COMBINED options, so the printed numbers and the
    pending's own `option.position` can never disagree."""
    from app.services.chatbot.tail import member_offer as member_mod

    lines = "\n".join(f"{o.get('position')}. {o.get('label')}" for o in member_options)
    return f"\n\n{member_mod.ROSTER_HEADER}\n{lines}\n\n{member_mod.ROSTER_CLOSE}"


def _stamped_roster_options(rows: list[Any], *, kind: str, text: str) -> list[dict[str, Any]]:
    """`rows` (a `suggest_last_result_set` or a require-specific picker's own
    `compatible_entities`) -> options in `_roster_option`'s shape, each carrying the
    SAME has/no stamp the composed TEXT prints - neither roster row shape carries a
    `stamp` field of its own, only the rendered line does."""
    stamps = _stamps_by_position(text)
    options: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        option = _roster_option(row, kind=kind, position=i + 1)
        if option is None:
            continue
        if not option.get("stamp"):
            stamp = stamps.get(option.get("position"))
            if stamp:
                option["stamp"] = stamp
        options.append(option)
    return options


def _breakdown_gate(gate: Any, raw_fragment: Any) -> Any:
    """`gate`, with `compatible_entities` widened to the FETCH's own effective subject -
    for `not_found_error_message`'s breakdown bullets ONLY (D1, hand pass 9).

    The resolver's OWN `gate.compatible_entities` is built from THIS message's own
    entities alone, which is empty for a bare positional pick - the picked entity never
    reaches the resolver at all (`turn/apply.py::_answer_pending` assembles it directly
    from the roster option). The FETCH step's own per-domain synthetic gate
    (`turn_runtime.make_tool_runner`'s own `gate["compatible_entities"] = entities`)
    merges the carried FOCUS with this turn's own entities - the effective subject the
    fetch itself was scoped to, the SAME input the primary tool call used - and rides
    along on `raw_fragment.delegate_payload.gate`, the "kept lane" fragment
    `lanes.business.run_fetch` already returns.

    NOT used for `_miss_question`'s own require-specific roster (its `gate.
    compatible_entities` read is the AMBIGUOUS-CANDIDATE set a customer must choose
    among, a different list with a different job) or for `run_miss_lane` - only the
    breakdown bullets need the wider, already-settled subject. Measured: passing the
    widened gate to the require-specific roster too raised a phantom 4th "photo"
    option with no real uuid, because the fetch's synthetic gate carries a
    NOT-cleanly-resolved word straight through, which `not_found_error_message`'s own
    `_ms_is_uuid`/base-name guard already drops from the BULLETS but the roster builder
    does not."""
    delegate_gate = (
        raw_fragment.get("delegate_payload", {}).get("gate")
        if isinstance(raw_fragment, Mapping) and isinstance(raw_fragment.get("delegate_payload"), Mapping)
        else None
    )
    if isinstance(delegate_gate, Mapping) and delegate_gate.get("compatible_entities"):
        return {**gate, "compatible_entities": delegate_gate["compatible_entities"]}
    return gate


def _unlisted_words(resolved: Mapping[str, Any], envelope: Any) -> list[str]:
    """The envelope's `unresolved` words the resolver's own `unresolved_tokens` does not
    already carry (compared by `turn.state.token_key`, separators folded)."""
    from app.services.chatbot.turn.state import token_key

    words = envelope.get("unresolved") if isinstance(envelope, Mapping) else None
    already = resolved.get("unresolved_tokens")
    listed = {token_key(t) for t in (already if isinstance(already, list) else [])}
    out: list[str] = []
    for word in words if isinstance(words, list) else []:
        key = token_key(word)
        if key and key not in listed:
            listed.add(key)
            out.append(str(word))
    return out


def _scope_gate(raw_fragment: Any) -> Any:
    """The FETCH step's own scope refusal, when it made one - otherwise `None`.

    Hotfix 22 Sep 2026 (PLAN-chatbot-stock-no-subject-hotfix-22sep.md): a turn that
    `turn_runtime.make_tool_runner.runner` refused BEFORE the tool ran, because the
    domain requires a scoping entity and this fetch carried none, rides its own
    `run_gate` verdict on the fragment as `scope_gate`. That verdict is what
    `not_found_error_message`'s `needs_scope` branch keys on (`gate_passed: False`
    plus a `requires a scoping entity` reason, and the `gate_debug.allowed_lookup`
    the sentence lists its filters from), and the RESOLVER's own gate cannot supply
    it: on a bare "stock?" the resolver never ran at all (no entity to resolve), and
    on the carried-subject turn it placed the carry perfectly well and has no
    opinion about scope. Read for the miss TEXT only - `run_miss_lane` and the
    breakdown bullets keep the resolver's gate, exactly as before.
    """
    if not isinstance(raw_fragment, Mapping):
        return None
    scope_gate = raw_fragment.get("scope_gate")
    return scope_gate if isinstance(scope_gate, Mapping) else None


def _ladder_resolved(resolved: Any, raw_fragment: Any) -> Any:
    """`resolved`, widened the SAME way `_breakdown_gate` widens `gate` - for
    `answer.crossdomain_zeroset`'s own read ONLY (D4, hand pass 9).

    `crossdomain_zeroset` needs a PROBEABLE product (a code with a uuid) to activate at
    all; on a picked-position turn `resolved` is `{}` (the resolver never ran - nothing
    was left unsettled for it to resolve, see `bridge_answers_a_miss`'s own docstring
    note in `engine.py`), so the ladder was permanently inactive for every after-a-pick
    miss regardless of `crossdomain_ladder`/`domain_hint`/`message_type` all being
    correct. Built in the SAME shape `crossdomain_zeroset`'s own "no `resolutions`" arm
    already reads (`TestIssue736SeparatorInsensitiveRequestedSet`'s own fixture:
    `{"tokens": [...], "intersection": [...]}`), from the identical delegate gate
    `_breakdown_gate` reads - the picked product is a real, uuid-carrying entity there
    either way."""
    if isinstance(resolved, Mapping) and (resolved.get("resolutions") or resolved.get("intersection")):
        return resolved
    delegate_gate = (
        raw_fragment.get("delegate_payload", {}).get("gate")
        if isinstance(raw_fragment, Mapping) and isinstance(raw_fragment.get("delegate_payload"), Mapping)
        else None
    )
    compat = delegate_gate.get("compatible_entities") if isinstance(delegate_gate, Mapping) else None
    products = [
        c
        for c in (compat if isinstance(compat, list) else [])
        if isinstance(c, Mapping) and c.get("entity_type") == "product" and c.get("canonical_code") and c.get("uuid")
    ]
    if not products:
        return resolved
    base = dict(resolved) if isinstance(resolved, Mapping) else {}
    base["tokens"] = [p["canonical_code"] for p in products]
    base["intersection"] = list(products)
    return base


def _hit_ladder_resolved(resolved: Any, focus_products: Any) -> Any:
    """`_ladder_resolved`'s own gap, closed for the HIT arm (hand pass 12 round 3, R5,
    owner ruling): a did-you-mean PICK that HITS still runs no resolver of its own, so
    `apply_crossdomain_hit`'s own `resolved` is exactly as empty as the MISS arm's was
    before `_ladder_resolved` existed - the ladder fired for a DIRECT ask (a fresh
    resolve) but never after a pick settled the very same product. Built from the
    FOCUS carry instead of `_ladder_resolved`'s own delegate gate (a HIT's caller,
    `engine.py`, has the picked product on `state_out.focus.products` already - no
    fetch fragment to read a gate off), in the identical `{tokens, intersection}` shape
    `crossdomain_zeroset`'s own "no `resolutions`" arm reads."""
    if isinstance(resolved, Mapping) and (resolved.get("resolutions") or resolved.get("intersection")):
        return resolved
    products = [
        {
            "entity_type": "product",
            "canonical_code": row.get("canonical_code"),
            "uuid": row.get("uuid"),
        }
        for row in (focus_products if isinstance(focus_products, list) else [])
        if isinstance(row, Mapping) and row.get("canonical_code") and row.get("uuid")
    ]
    if not products:
        return resolved
    base = dict(resolved) if isinstance(resolved, Mapping) else {}
    base["tokens"] = [p["canonical_code"] for p in products]
    base["intersection"] = products
    return base


def _miss_question(
    offer: Mapping[str, Any],
    producers: Mapping[str, Any],
    *,
    gate: Any,
    parser: Mapping[str, Any] | None,
    asked_at_turn: int | None,
    text: str,
    resolved: Any = None,
    combined_member_rows: list[Any] | None = None,
) -> pending.Pending | None:
    """AC-1684: every question the miss arm raises, in the SAME precedence
    `tail.reply_ladder.compose_reply`'s own `result_set` already reads (member offer
    first, then the roster a producer built, then a bare escalate offer).

    `combined_member_rows` is hand pass 12 round 3's own addition (owner ruling R2): a
    `cs_last_result_set`, pre-fetched by the CALLER (`answer_for`, via `tail/member_
    offer.py`'s own `cs_roster_plan -> fetch_rosters -> build_cs_member_offer` chain -
    the SAME one `producers["build-cs-member-offer"]` would have carried had `cs_offer_
    gate`'s own g4b not suppressed it for THIS exact shape, a did-you-mean roster that
    also earns a CS offer). When both a did-you-mean/require-specific roster AND this
    exist, they mint ONE pending, continuously numbered - the did-you-mean/customer
    options first, the CS members continuing after (`idx` on each row is overwritten
    with its post-offset position before `member_option` reads it, since `member_
    option` always prefers a row's own `idx` over the position it is called with)."""
    routing = (parser or {}).get("routing") or {}
    team = routing.get("suggested_team")
    # SRTSC07 (prod transcript, 22 Sep 2026): carried onto the pending beside `team`, so
    # the acceptance turn's `(agent_code, team_code)` pair reaches `/external/
    # next-assignee` intact - the pair it resolves a pool by - instead of the acceptance
    # turn's own null `suggested_agent` falling to `DEFAULT_SUGGESTED_AGENT`.
    agent = routing.get("suggested_agent")
    # Round 4 (owner-approved, 22 Sep 2026): this turn's own resolved brand, off the
    # SAME gate this function already takes as a parameter - `lanes/business/
    # gate.py::run_gate`'s own `routing_brand`.
    brand = gate.get("routing_brand") if isinstance(gate, Mapping) else None

    # The did-you-mean roster (`build_suggest_offer`'s D1/D2/D3 arms all populate
    # `suggest_last_result_set`). The require-specific PICKER (F6/AC-1701) is a FOURTH
    # surface `build_suggest_offer` never gives its own `suggest_last_result_set` at
    # all - it only rewrites `escalate_message` in place (answer.py's own "4th surface"
    # comment) - so that roster's rows are `gate.compatible_entities` instead, the SAME
    # rows `gate.py`'s own numbered picker text was built from (FIX A).
    rows = offer.get("suggest_last_result_set") or None
    if not rows and isinstance(gate, Mapping) and gate.get("require_specific") is True:
        rows = [e for e in (gate.get("compatible_entities") or []) if isinstance(e, dict)]
    roster_kind: str | None = None
    roster_options: list[dict[str, Any]] = []
    if rows:
        entity_kind = next(
            (row.get("entity_type") for row in rows if isinstance(row, dict) and row.get("entity_type")),
            None,
        ) or "product"
        roster_kind = f"{entity_kind}_pick"
        roster_options = _stamped_roster_options(rows, kind=roster_kind, text=text)

    # Owner ruling R2, hand pass 12 round 3: a did-you-mean/require-specific roster that
    # ALSO earns a CS member offer mints ONE pending, not two - the member half CONTINUES
    # the roster's own numbering rather than restarting at 1. Checked first, ahead of
    # both the "member alone" and "roster alone" arms below (either would otherwise fire
    # instead, dropping half the offer).
    if (
        roster_options
        and len(roster_options) >= _MIN_ROSTER_OPTIONS
        and combined_member_rows
    ):
        # P7 (hand pass 12 Phase 3, security L1): a bare COUNT of roster_options
        # under-counts once `_stamped_roster_options` has SKIPPED a row (a non-dict
        # row `_roster_option` refuses outright) - a surviving row's own `idx` can
        # still be the pool's highest position even though fewer options survived to
        # be counted. The member half must start after the roster's own HIGHEST
        # position, never after a bare count of what is left.
        offset = max((option.get("position") for option in roster_options), default=0)
        member_options = [
            option
            for option in (
                member_option({**row, "idx": offset + i + 1}, offset + i + 1)
                for i, row in enumerate(combined_member_rows)
                if isinstance(row, Mapping)
            )
            if option
        ]
        if member_options:
            return pending.ask(
                roster_kind,
                roster_options + member_options,
                team=team,
                asked_at_turn=asked_at_turn,
                payload={
                    "domain": (parser or {}).get("domain_hint"),
                    "escalate_offered": True,
                    "agent": agent,
                    "brand_code": brand,
                },
            )

    member = producers.get("build-cs-member-offer")
    if isinstance(member, Mapping) and member.get("member_offer") is True:
        member_rows = member.get("cs_last_result_set") or []
        options = [
            option
            for option in (member_option(row, i + 1) for i, row in enumerate(member_rows))
            if option
        ]
        if options:
            # SRTSC07 review round 1, SHOULD-2: the member roster's own offer needed
            # the same top-level stamp as the other mint sites in this function -
            # picking a member option IS an escalation acceptance (`turn/apply.py:546`).
            return pending.ask(
                "member_offer",
                options,
                # #865 round 6: the team the picker was fetched for, and the companies whose
                # rosters it printed - the pool a bare "yes" clarifies over and a company
                # word picks from (`turn.pending.offered_companies`).
                team=team,
                asked_at_turn=asked_at_turn,
                payload={"agent": agent, "brand_code": brand, "roster_plan": roster_plan_of(member)},
            )

    if roster_options:
        # AC-1691's umbrella, "in any domain and for any entity kind" - the SAME guard
        # `_tier_options` and `_offer_answer` already carry, and the one arm of this
        # module that was missing it (reviewer S5). A one-row `suggest_last_result_set`
        # is not a choice; the escalate offer below is the honest question for it.
        if len(roster_options) >= _MIN_ROSTER_OPTIONS:
            return pending.ask(
                roster_kind,
                roster_options,
                team=team,
                asked_at_turn=asked_at_turn,
                payload={
                    "domain": (parser or {}).get("domain_hint"),
                    "escalate_offered": True,
                    "agent": agent,
                    "brand_code": brand,
                },
            )

    catalog = producers.get("escalate-catalog")
    if isinstance(catalog, Mapping) and catalog.get("is_escalate_offer") is True:
        # Hand pass 11, defect 3: a miss `answer.py:3112` reported "checked in X
        # and Y" (`_and_list` of two-or-more companies) must clarify WHICH one
        # before escalating - `escalation.py::_clarify_over`'s own company pairs.
        # `_searched_companies` (security SF-2) reads the resolver/gate structures
        # directly, never the already-composed sentence.
        #
        # The offer itself stays production's own sentence (owner ruling, hand pass
        # 11: "we clarify the company with the user when it is not clear") - the
        # COMPANIES ride on the options, so the answering turn can resolve a named
        # one against the offered pool, and a bare "yes" over more than one reaches
        # the escalation lane's own company clarify instead of a blind assign
        # (`escalation.py::_clarify_gate`). `company_id` is what routes; the label is
        # what the customer reads and what `escalation.company_pick` matches on.
        companies = _searched_companies(resolved, gate)
        if len(companies) >= _MIN_ROSTER_OPTIONS:
            return pending.ask(
                "team_pick",
                [
                    {
                        "position": i + 1,
                        "label": row["company_name"],
                        "entity_type": "team",
                        "payload": {
                            "company": row["company_name"],
                            "company_id": row.get("company_id"),
                            "brand_code": row.get("brand_code"),
                        },
                    }
                    for i, row in enumerate(companies)
                ],
                team=team,
                asked_at_turn=asked_at_turn,
                expects="yes_no",
                # SRTSC07: the company clarify's own bare "yes" (no numbered pick) is
                # answered by the generic accept arm, so this offer's top-level
                # `payload["agent"]` is what the acceptance carry reads. `brand_code`
                # (round 4) is the SAME turn-level idiom, one axis over - never the
                # per-option `brand_code` two lines up, which is that SPECIFIC
                # company's own and stays untouched.
                payload={"agent": agent, "brand_code": brand},
            )
        return pending.ask(
            "team_pick",
            [{"position": 1, "label": "Yes", "entity_type": "team", "payload": {}}],
            team=team,
            asked_at_turn=asked_at_turn,
            expects="yes_no",
            payload={"agent": agent, "brand_code": brand},
        )
    return None


def _searched_companies(resolved: Any, gate: Any) -> list[dict[str, Any]]:
    """Which companies this turn's fetch actually searched, off the RESOLVER/GATE
    structures - never off the already-composed reply text (security SF-2, hand pass
    11 security review: that text quotes the customer's own raw token back verbatim,
    `label_token` in `lanes/business/answer.py:3152-3156`, so a hostile raw token
    could mint attacker-labelled escalate options if this reader ever regexed the
    sentence for "checked in X and Y" instead).

    One row per company, in the `routing_roster_plan` shape routing already speaks
    (`company_id` / `company_name` / `brand_code`): the ID is what
    `lanes/escalation.py::_next_assignee_body` posts and the NAME routes nothing at all
    (hand pass 11, blocker 1). `brand_code` is always `None` (reviewer SF-3, hand pass 11
    final): a join against `gate.routing_companies` narrowed the assignee draw by a brand
    nothing verified for the silent company, and it was scope beyond this offer's own
    blocker. The key stays so the row shape matches `routing_roster_plan` elsewhere.

    Mirrors the join `lanes/business/answer.py:2941-2954` performs for the "checked in X
    and Y" SENTENCE, including its `_NO_TOOL_ID` skip (SF-3, hand pass 11 review): a
    `brand` / `category` match reaches `compatible_entities` but `entity-ids-transformer`
    maps neither to a tool param, so counting its company here offered to escalate to a
    company the sentence never claimed to have searched - "a false statement ... not
    recoverable", in `_NO_TOOL_ID`'s own words. `turn_runtime.companies_by_uuid` builds
    the per-uuid map (resolutions, intersection and by_entity_type alike - the OR-mode
    fallback this lane's own multi-company miss takes reports its matches under
    `resolutions`, but an AND-mode hit reports them under `intersection` instead, and
    this reader must not care which).
    """
    from app.services.chatbot.lanes.business.answer import NO_TOOL_ID
    from app.services.chatbot.turn_runtime import companies_by_uuid

    co_by_uuid = companies_by_uuid(resolved)
    compat = gate.get("compatible_entities") if isinstance(gate, Mapping) else None
    searched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in compat if isinstance(compat, list) else []:
        if not isinstance(c, Mapping):
            continue
        if str(c.get("entity_type") or "") in NO_TOOL_ID:
            continue
        company = co_by_uuid.get(c.get("uuid"))
        if not company or company["company_name"] in seen:
            continue
        seen.add(company["company_name"])
        searched.append(
            {
                "company_id": company.get("company_id"),
                "company_name": company["company_name"],
                "brand_code": None,
            }
        )
    return searched


def _answered_fresh(parser: Mapping[str, Any] | None) -> bool:
    """Did THIS message settle its own pick by typing a fresh entity, or by naming a
    did-you-mean slot specifically - main's fresh-typed picker gate
    (`tail/compile_state.py`, ported to this bridge as `answer_for`'s own roster-
    survival check, hand pass 9 D2). Only reader: the roster-survival patch below.

    Two signals, either one retires a still-open roster rather than letting it carry
    to a later, different position:

    * `reference_target == "dym"` - the parser's own dym-slot marker
      (`lanes/business/answer.py`'s S5 positional-pick comment: "on a dym pick the LLM
      emits the candidate's DYM slot and the parser has already spent it resolving the
      entity"). AC-1704's certificate roster answers this way.
    * a `current_message: True` entity - the message TYPED something new (a code, a
      name), the other half of main's own `fresh_typed` gate. AC-1704's certificate
      roster answers this way too, when the customer types the code instead of the
      number.

    A bare positional pick (`reference_positions` only, no `reference_target`, no
    entities) - D1/D2/D3's own shape - carries neither, so the roster it answers stays
    open for a later, different position (contract 36).
    """
    p = parser if isinstance(parser, Mapping) else {}
    if p.get("reference_target") == "dym":
        return True
    return any(
        isinstance(e, Mapping) and e.get("current_message") is True for e in (p.get("entities") or [])
    )


def _run_crossdomain_ladder(
    *,
    parser: Mapping[str, Any] | None,
    resolved: Any,
    entities_names: Any,
    crossdomain_ladder: Mapping[str, Any] | None,
    ctx: Any,
    services: AnswerServices,
    contact_id: Any,
    space_id: str | None,
    trace: Any = None,
    dry_run: bool = True,
    item: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """AC-1705's cross-domain stock ladder: `answer.run_crossdomain`'s own call, split
    out from the FOLD below (`_apply_crossdomain_render`, hand pass 9 item 2) so a
    caller can run this BEFORE building any text that reads the ladder's own render,
    then fold the SAME result's render afterwards - never a second `run_crossdomain`
    call, which would probe twice for one fact.

    Owner ruling 22 Sep 2026, R6 retired the reason this split originally existed:
    `_apply_crossdomain_rung` no longer mutates `parser["routing"]["suggested_team"]`
    at all (`_CROSSDOMAIN_RUNG_TEAM` is deleted - a stock-origin ask is ALWAYS
    warehouse and an incoming-origin ask is ALWAYS purchasing, whichever rung answers,
    so there is no team update left for a caller to race). Ordering still matters for
    a different, unrelated reason: the ladder's own RENDERED TEXT (the "but PO is
    placed" block) has to exist before `not_found_error_message` / `miss_suggest.
    run_miss_lane` compose the miss reply, so `_apply_crossdomain_render` can fold it
    in - this call still has to run first, it just never again changes which team the
    escalate sentence names (that is now `turn_runtime.lane_parse_output`'s own
    domain-aware fallback, filled before this function ever sees the parser dict - see
    that function's own docstring).

    `answer.run_crossdomain` gates itself to `domain_hint in ("incoming", "inventory")`
    and at least one probeable (uuid-carrying) product (`crossdomain_zeroset`'s own
    domain/probeable checks) - calling it unconditionally on every miss is therefore a
    genuine no-op (no MCP call, `render=None`) for every other domain, never a second
    ladder policy of this module's own. `item` defaults to `{}`, equivalent to main's
    own validator item on a MISS: `crossdomain_zeroset` reads that item for
    `returned_codes` alone, and on a miss that set is empty either way. Hand pass 11,
    defect 1: a caller answering a single-domain HIT whose rows read 0 on hand (the
    ladder never used to reach a HIT at all - `envelope_missed` is false for it, so
    neither this bridge's own miss triggers nor `turn/fetch.py::_climb` ever ran)
    passes the validated fetch envelope here instead - `crossdomain_zeroset`'s own
    `_rows_all_zero` per-code read (`dh == "inventory"` only) is what turns "found but
    every row is 0" into a `zero: True` miss entry the SAME ladder probes for.

    Every OTHER argument is the one `complete_answer` hands its own `run_crossdomain`
    (`lanes/business/__init__.py:1685-1710`), reviewer B3/S2:

    * `entities_names` is the resolver aggregate's own `name` (the contact's entitled
      level names). `None` skips `crossdomain_probe_args`'s intersection entirely and
      sends the PARSER's claimed `access_levels` verbatim, so a level the customer's
      words named but the contact does not hold reached the probe. This bridge passes
      `[]` rather than main's `None` when the aggregate did not run, which is a
      DELIBERATE fail-closed divergence: the intersection then keeps nothing and the
      unverified claim is dropped. `[]` is inert today only because no tool the ladder
      can reach reads `access_levels` at all (`crm_inventory_stock_balance_list`,
      `crm_incoming_stock_list`, `crm_procurement_po_placed_list`; the rung probe does
      not even send the key) - a future rung on an access-level-gated tool would inherit
      the "empty means no filter" reading B1 was about, and must pass the entitlement
      here instead (security N5).
    * `crossdomain_ladder` is `system_settings.chatbot_crossdomain_ladder`
      (`engine._crossdomain_ladder`). `answer._next_crossdomain_rung` returns `None`
      for a non-dict ladder, so without it the SECOND rung - the PO rung, migration
      `491_chatbot_ladder_incoming_po`, owner ruling 8 Sep 2026 - never ran at all.
    * `trace` is the turn's own `TurnTrace`, so the rung probes appear on the trace
      screen under this turn rather than nowhere.
    """
    session_block = ctx.get("session") if isinstance(ctx, Mapping) else None
    access = ctx.get("access") if isinstance(ctx, Mapping) else None
    granted = access.get("attributes") if isinstance(access, Mapping) else None
    return answer_mod.run_crossdomain(
        dict(item) if isinstance(item, Mapping) else {},
        parser=parser,
        resolved=resolved,
        session_block=session_block,
        entities_names=entities_names,
        services=services,
        contact_id=contact_id,
        space_id=space_id,
        dry_run=dry_run,
        crossdomain_ladder=(
            dict(crossdomain_ladder) if isinstance(crossdomain_ladder, Mapping) else None
        ),
        trace=trace,
        granted=granted,
    )


#: `crossdomain_render`'s own per-row grammar (`answer.py`, `f"*{label}:* {value}"`) for
#: the field the incoming / PO rungs key their rows by. Anchored to the line START and to
#: the line END, so the value read is the WHOLE code: a code that is a string PREFIX of
#: the one on the line (SRTWC6022-SH-UF inside SRTWC6022-SH-UF-NEW) is never mistaken
#: for it, and a code that merely extends it is not either.
#:
#: The leading `- ` is REQUIRED in the alternation, not decorative: `crossdomain_render`
#: renders a row as `f"- {field_lines}"`, so the FIRST field's line carries the bullet
#: and every later one does not. Product Code is the first field on a live incoming rung
#: row (measured, turn 27f60a71 on the clone: the block reads
#: `- *Product Code:* SRTWC6022-SH-UF-NEW`), so a pattern anchored on `^\*Product Code:`
#: alone matched NOTHING there, `_block_product_codes` returned the empty "no opinion"
#: set, and the note fell back to naming every zero code - which is the defect this
#: function was added to close, reappearing through its own anchor.
_RUNG_ROW_CODE_RE = re.compile(r"^(?:-[ \t]*)?\*Product Code:\*[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _block_product_codes(block_text: Any) -> set[str]:
    """The product codes the rung's rendered block actually NAMES.

    MEASURED on live turn 8781cd47 ("check stock srtwc6022", ONE typed family token
    onto TWO real products, BOTH reading 0 on hand): the incoming rung renders rows
    for SRTWC6022-SH-UF-NEW only, yet `crossdomain_render`'s own zero-entry lookup
    deliberately ALSO matches a prefixed sibling's rows (its finding-7 arm, so a typed
    family prefix finds the family's rows on the other side) - so SRTWC6022-SH-UF ends
    up in neither `nothing_codes` nor the block, and `_prefix_zero_note` below named
    both codes where production names one ("No stock for SRTWC6022-SH-UF-NEW.", owner's
    prod paste, `tests/chatbot/journeys/hp11-zero-stock.json`).

    Read off the rendered TEXT rather than carried on `_xdBlock`: that dict is graded
    key-for-key against the n8n crossdomain-render capture corpus
    (`test_s6c_answer_lane.py` / `test_s6c_engine_paths.py`, 13 captures), so a new key
    there is a parity failure - measured, not assumed. An EMPTY result means the rung's
    rows are not product-keyed at all, which every caller must read as "no opinion"
    rather than as "none of them".
    """
    if not isinstance(block_text, str) or not block_text:
        return set()
    return {m.group(1) for m in _RUNG_ROW_CODE_RE.finditer(block_text) if m.group(1)}


def _prefix_zero_note(result: Mapping[str, Any]) -> Mapping[str, Any]:
    """Hand pass 11, defect 1: `"No {primary_word} for {codes}."`, the SAME string
    template `answer.py::crossdomain_render`'s own `only_other_note` already uses
    (`f"No {primary_word} for {', '.join(only_other)}."`) - built here rather than
    inside that shared function, because its OWN gate deliberately never fires for a
    `zero: True` code (owner ruling 11 Sep 2026, second ruling, R2(b): "a zero code
    never earns AC-820's own 'no {primary} for X' only-other line either way - the
    zero sentence two paragraphs later already says the same thing" - true for a
    MISS, where `not_found_error_message` already named the code up front, but a HIT
    has no miss sentence of its own naming it at all). `crossdomain_render` itself
    stays byte-identical (`test_crossdomain_ladder.py`'s own
    `test_stock_origin_zero_but_incoming_answers_no_po_probe` pins "no zero sentence"
    for the SAME shape on the miss path).

    Only for a code whose OWN rung genuinely rendered something (not in the block's
    own `nothing_codes` - that shape already gets its own sentence from
    `_apply_crossdomain_rung`/`nothing_note`, unchanged): the mixed set's non-zero
    code never reaches `zeroset.missing` at all, so it is never named here either.
    """
    zeroset = result.get("zeroset") if isinstance(result, Mapping) else None
    xd = zeroset.get("_xd") if isinstance(zeroset, Mapping) else None
    render = result.get("render") if isinstance(result, Mapping) else None
    block = render.get("_xdBlock") if isinstance(render, Mapping) else None
    if not isinstance(xd, Mapping) or not isinstance(block, Mapping):
        return result
    nothing_codes = {c for c in (block.get("nothing_codes") or []) if isinstance(c, str)}
    rendered_codes = _block_product_codes(block.get("block"))
    codes: list[str] = []
    for m in xd.get("missing") or []:
        if not (isinstance(m, Mapping) and m.get("zero") is True):
            continue
        code = m.get("code") or m.get("_n")
        if not (isinstance(code, str) and code) or code in nothing_codes or code in codes:
            continue
        if rendered_codes and code not in rendered_codes:
            continue
        codes.append(code)
    if not codes:
        return result
    # Nit N-2 (reviewer, hand pass 11): always "stock", never "incoming" - `zero: True`
    # (the only way a `missing` entry survives the loop above) is stamped ONLY on the
    # `dh == "inventory"` arm (`lanes/business/answer.py:683-692`), so `origin_domain`
    # (== `dh`) is always "inventory" here; the incoming half of the old ternary was
    # unreachable dead code.
    note = f"No stock for {', '.join(codes)}."
    old_block_text = block.get("block") or ""
    new_block = dict(block)
    new_block["block"] = f"{note}\n\n{old_block_text}" if old_block_text else note
    new_render = {**render, "_xdBlock": new_block}
    return {**result, "render": new_render}


def _apply_crossdomain_render(
    text: str, result: Mapping[str, Any], *, answered: bool = False, include_offer: bool = True
) -> str:
    """The rung's own rendered block, folded above the escalate marker, from the
    ALREADY-COMPUTED `result` `_run_crossdomain_ladder` (above) returned - this
    function never calls the ladder itself, so running it early (before the miss text)
    and folding its render late (after) never probes twice for one fact.

    `answered` (hand pass 11, defect 1): a HIT with rows folds through
    `crossdomain_compose`'s PARTIAL-turn branch instead of its TOTAL-MISS one - the
    block goes UNDER the primary answer, WITH the locked "Would you like me to
    escalate to X team?" question (the miss branch never appends that phrase itself;
    a genuine miss already has it from `not_found_error_message`'s own escalate ask,
    which a HIT has none of). That branch's own guard reads `variables.
    last_result_set` for non-emptiness only - never its contents - so a single
    truthy sentinel is enough to say "this turn answered something", the same fact
    `text` already carrying real rows establishes.

    `include_offer` (#1262 slice 11, F8): both call sites pass `not is_staff_profile
    (profile)` - the rung's own block still renders for a staff rep, only the
    escalate phrase is withheld.
    """
    render = result.get("render")
    if not isinstance(render, Mapping):
        return text
    block = render.get("_xdBlock")
    if not isinstance(block, Mapping) or block.get("any") is not True or not block.get("block"):
        return text
    variables: dict[str, Any] = {"last_result_set": [True]} if answered else {}
    sealed = {"reply": {"text": text, "session_patch": {"user_response": text, "variables": variables}}}
    merged = tail_compose.crossdomain_compose(
        sealed,
        result={"result": {"xd": {"block": dict(block)}}},
        answered=answered,
        include_offer=include_offer,
    )
    merged_text = (merged.get("reply") or {}).get("session_patch", {}).get("user_response")
    return merged_text if isinstance(merged_text, str) and merged_text else text


def _miss_triggers(
    payload: dict[str, Any], envelope: dict[str, Any] | None
) -> tuple[Any, Any, tuple[bool, bool, bool]]:
    """`(raw_fragment, fetch_item, (via_resolver_exit, via_error_fragment,
    via_fetched_empty))`: `answer_for`'s three triggers, read off the payload and the
    envelope alone. Shared with `answers_a_miss`, so the engine can ask BEFORE it builds
    anything the miss would need (#865 fix round 2, N2)."""
    raw_fragment = envelope.get("raw_fragment") if isinstance(envelope, dict) else None
    fragment_outcome = raw_fragment.get("outcome") if isinstance(raw_fragment, dict) else None
    fetch_item = None
    if isinstance(raw_fragment, dict):
        maybe_fetch = raw_fragment.get("fetch")
        fetch_item = maybe_fetch if isinstance(maybe_fetch, dict) else None

    # SO-NUMBER-ASK: the lane answered the SO words the resolver could not place, and its
    # reply already names the ones no SO answered (`lanes/business/run_fetch`'s
    # `so_numbers` arm), so the resolver's own exit is no miss of its own.
    so_status_answered = isinstance(fetch_item, Mapping) and fetch_item.get("so_status") is True
    # LOWSTOCK-FILTER-ASK (owner hand test, 3 Oct 2026, turn 4a90dd1d): a low stock
    # report ran and said its own line; a word the resolver missed on the same turn
    # never turns that reply into "Could not find inventory".
    low_stock_answered = isinstance(fetch_item, Mapping) and fetch_item.get("low_stock_report") is True
    via_resolver_exit = payload.get("_exit_kind") == "not_found" and not (so_status_answered or low_stock_answered)
    via_error_fragment = fragment_outcome == "not_found"
    # R5 (AC-1699, AC-1702): a THIRD trigger - the resolver settled a real subject,
    # the fetch genuinely ran for it, and the tool came back with zero rows. This
    # fragment carries NO top-level `outcome` at all (`kind: "result"`, the ordinary
    # hit shape `lanes.business.fetch.output_structurer` returns via `fetch_result`) -
    # `fetch_item.get("has_result")` is what actually says whether it found anything,
    # the same field `envelope_of`'s own `has_result` computation reads. Checked on
    # `raw_fragment`/`fetch_item` directly, never the OUTER `envelope`'s own
    # `turn/fetch.py::envelope_missed` rule - that rule reads several fields
    # (`figures`, `denied`, `tool_has_result`, `entities`, `miss`) a hand-built test
    # envelope carrying only `raw_fragment` never populates, and every existing
    # `TestMissArmReturnsNoneOutsideItsOwnTerritory` case is exactly such a fixture.
    # `raw_fragment.get("kind") == "error"` (an access_denied refusal or an
    # infrastructure failure, both from `business._error_fragment`) is excluded the
    # same way `via_error_fragment` already excludes an infra failure (neither
    # `not_found` nor `access_denied` outcome) - only the ordinary "result" kind ever
    # reaches this trigger.
    via_fetched_empty = (
        not via_error_fragment
        and isinstance(raw_fragment, Mapping)
        and raw_fragment.get("kind") == "result"
        and isinstance(fetch_item, Mapping)
        and not fetch_item.get("has_result")
    )
    return raw_fragment, fetch_item, (via_resolver_exit, via_error_fragment, via_fetched_empty)


def answers_a_miss(payload: dict[str, Any], envelope: dict[str, Any] | None) -> bool:
    """Will `answer_for` answer this envelope as a miss? Its own trigger rule, nothing
    else: the engine reads the focus product's brand for a resolver-less miss only when
    this is true, so a hit never pays for that read (#865 fix round 2, N2)."""
    return any(_miss_triggers(payload, envelope)[2])


def answer_for(
    payload: dict[str, Any] | None,
    *,
    envelope: dict[str, Any] | None,
    parser: dict[str, Any] | None,
    ctx: Any,
    canned: Any,
    services: AnswerServices,
    db: Any,
    asked_at_turn: int | None,
    roster_caps: Mapping[str, int] | None = None,
    crossdomain_ladder: Mapping[str, Any] | None = None,
    turn_id: str | None = None,
    trace: Any = None,
    dry_run: bool = True,
    carried_pending: Any = None,
    profile: Any = None,
    dealer_stock_ask: bool = False,
    dealer_incoming_ask: bool = False,
) -> turn_compose.Answer | None:
    """The MISS seam (R4): `None` outside its own two triggers (see module docstring),
    so a hit, an `access_denied` refusal, an infrastructure error and a multi-domain plan
    all fall through to the caller's own fallback (`turn/compose.py`, or R5's own hit
    arm).

    `carried_pending` is `state.pending` as `apply()` left it, BEFORE this miss's own
    question overwrites it (`engine.py`'s own `state_out.pending`) - the same value
    `turn/compose.py::compose`'s identical carried-roster check reads. Optional and
    `None` on every caller that predates hand pass 9 (a missing carry just means the
    roster-preservation rule below never fires, same as before it existed)."""
    if not isinstance(payload, dict):
        return None
    raw_fragment, fetch_item, (via_resolver_exit, via_error_fragment, via_fetched_empty) = (
        _miss_triggers(payload, envelope)
    )
    if not (via_resolver_exit or via_error_fragment or via_fetched_empty):
        return None

    # The SAME three reads `complete_answer` opens with
    # (`lanes/business/__init__.py:1553-1560`), coercions included: a non-dict
    # `resolved`/`gate` is `{}` there, never `None`, and `entities_names` is the
    # resolver aggregate's own entitled level names, `None` only when the aggregate
    # genuinely did not run (which both readers handle).
    resolved = payload.get("resolved") if isinstance(payload.get("resolved"), dict) else {}
    gate = payload.get("gate") if isinstance(payload.get("gate"), dict) else {}
    # #1262 fix lane round 3, B1-r2: the brand the fetch sent, for the miss header.
    gate = dict(scope_block.with_brand_names(gate, envelope) or {})
    # #1262 fix lane round 3, S5: the words the turn could not place
    # (`envelope["unresolved"]`, `turn_runtime.resolve_kinds`'s `unplaced`) are the
    # resolver's own `unresolved_tokens` plus one thing only the chatbot knows - a
    # brand word that is not on the live list. Added here by token key, so a word the
    # resolver already listed is never named twice.
    extra_unplaced = _unlisted_words(resolved, envelope)
    if extra_unplaced:
        resolved = {
            **resolved,
            "unresolved_tokens": [*(resolved.get("unresolved_tokens") or []), *extra_unplaced],
        }
    # SO-NUMBER-ASK: a linked contact's customer-scope rows (`engine._scoped_compatible`,
    # `scope: True`) are not what the message asked about. On a miss over a word nobody
    # could place ("status of SO422056") they printed a Customer/Product/Dates header and
    # a "customer: <link> (+11 more)" bullet above the miss line, as if the dealer had
    # named their own accounts. The miss is about the word, so only the word is named.
    if resolved.get("unresolved_tokens") and gate.get("compatible_entities"):
        gate["compatible_entities"] = [
            e
            for e in gate["compatible_entities"]
            if not (isinstance(e, dict) and e.get("scope"))
        ]
    # D4 (hand pass 9): a bare positional pick's own verdict names no team of its
    # own - the customer typed "1", not the original ask - so `turn_runtime.
    # lane_parse_output`'s own generic fallback (`DEFAULT_SUGGESTED_TEAM`,
    # "customer_service"/"general_enquiries") answered every after-a-pick miss,
    # ladder rung included, regardless of which team the ORIGINAL ask (the roster
    # still-open turn) was actually routed to. `carried_pending.team` is that team
    # (`_offer_answer`'s and `_miss_question`'s own roster-minting arms both stamp
    # it there off THAT turn's `routing.suggested_team` at ask time), so a pick over
    # it restores the same team (live turn 139f5282-4968-4802-bd11-501de55bca50:
    # escalated to "customer service", not the incoming domain's own "purchasing").
    if carried_pending is not None and carried_pending.team:
        # A pick answering an EXISTING carried roster names no team of its own - a
        # bare "1" is not a customer statement about who should handle this - so the
        # roster's own established team always wins here, never `lane_parse_output`'s
        # own generic default ("customer_service"/"general_enquiries",
        # `turn_runtime.py`'s own `DEFAULT_SUGGESTED_TEAM`) that turn's bare verdict
        # would otherwise carry.
        parser = {
            **(parser if isinstance(parser, Mapping) else {}),
            "routing": {
                **((parser or {}).get("routing") or {}),
                "suggested_team": carried_pending.team,
            },
        }
    aggregate = payload.get("aggregate") if isinstance(payload.get("aggregate"), dict) else None
    # Security S2. Main's own expression is `aggregate.get("name") if aggregate is not
    # None else None`, and `None` tells `answer.crossdomain_probe_args` to skip its
    # intersection and send the PARSER's claimed `access_levels` verbatim - a level the
    # customer's WORDS named, which nothing has checked against the contact's entitlement.
    # The aggregate only runs on the `access_check` entry (the promotion lane), so every
    # incoming/inventory miss - the only origins the cross-domain ladder has - took that
    # `None` branch. `[]` instead of `None` is the one deliberate divergence: an
    # unverified claim is dropped rather than forwarded. It is byte-identical to main
    # whenever the parser claimed nothing, which is 249 of 249 real captures
    # (`documentation/plans/chatbot/parser-prompt-inventory.md:106`), so the only turn
    # that can observe the difference is the one making an unverified claim.
    entities_names = aggregate.get("name") if aggregate is not None else []
    full_payload = (
        {**payload, "fetch": fetch_item} if (via_error_fragment or via_fetched_empty) else payload
    )
    contact_id = (ctx.get("contact") or {}).get("id") if isinstance(ctx, Mapping) else None
    space_id = business_services.fetch_space_id(db) if db is not None else None
    # Hand pass 9, item 2: the ladder runs BEFORE the miss text - matching main's own
    # sequencing (`complete_answer` calls `run_crossdomain` ahead of `_run_miss_half`).
    # Owner ruling 22 Sep 2026, R6 retired the TEAM half of why this ordering mattered
    # (`_apply_crossdomain_rung` no longer mutates `parser["routing"]["suggested_team"]`
    # at all - see `_run_crossdomain_ladder`'s own docstring); the ordering still holds
    # for the ladder's own RENDERED TEXT, which has to exist before
    # `not_found_error_message` composes the miss reply so it can be folded in. The
    # render itself is folded onto `text` at the very end, from this SAME
    # `crossdomain_result` - never a second `run_crossdomain` call.
    crossdomain_result = _run_crossdomain_ladder(
        parser=parser,
        resolved=_ladder_resolved(resolved, raw_fragment),
        entities_names=entities_names,
        crossdomain_ladder=crossdomain_ladder,
        ctx=ctx,
        services=services,
        contact_id=contact_id,
        space_id=space_id,
        trace=trace,
        dry_run=dry_run,
        # Ported from PR #1118 (not merged), D17, review round 10: the dealer's
        # availability block turns the whole ladder off (`crossdomain_zeroset`'s own
        # gate). The MISS arm hands the ladder an empty item, so the block has to
        # travel here too - measured unreachable on this shape today (an availability
        # reply carries an entry per named product, so it is a HIT), carried anyway
        # because "no rung on a dealer reply" is a rule about the REPLY, not about
        # which arm composed it.
        item=(
            {"stock_availability": envelope["stock_availability"]}
            if isinstance(envelope, Mapping)
            and isinstance(envelope.get("stock_availability"), list)
            and envelope.get("stock_availability")
            # PR #1329 fix round 2: a dealer's incoming miss is the same rule. The
            # fetch envelope keeps no `result_type`, so the presenter's own marker for a
            # dealer's incoming reply is named here from the contact's profile.
            else {"result_type": "incoming_dealer"}
            if dealer_incoming_ask
            else None
        ),
    )

    miss_gate = _scope_gate(raw_fragment)
    if miss_gate is None:
        miss_gate = _breakdown_gate(gate, raw_fragment)
    not_found = answer_mod.not_found_error_message(
        full_payload,
        parser=parser,
        resolved=resolved,
        gate=miss_gate,
        profile=profile,
        granted_keys=((ctx.get("access") or {}).get("attributes") if isinstance(ctx, Mapping) else None),
    )
    offer = miss_mod.run_miss_lane(
        not_found,
        parser=parser,
        resolved=resolved,
        gate=gate,
        services=services,
        # Reviewer B2. `miss_suggest._sibling_gate`'s FOURTH condition is
        # `if build_result is None: return False`, so omitting this closed the sibling
        # gate unconditionally and a partially-typed variant code never got its
        # sibling-family offer - the has-incoming / no-incoming picker - even though
        # every other condition held. Main's own `complete_answer` passes this exact
        # literal on its combined not-found/error arm
        # (`lanes/business/__init__.py:1660-1665`) with a comment saying why, and the
        # literal is TRUE for all three of this function's triggers: `answer_for` is
        # the MISS seam and returns `None` for a hit, and `via_fetched_empty`'s own
        # trigger condition IS `not fetch_item.get("has_result")`.
        build_result={"has_result": False},
        contact_id=contact_id,
        space_id=space_id,
        # Main passes the turn id (`complete_answer`'s own `execution_id=turn_id`), so
        # the miss probes correlate with the turn on the trace screen; a synthetic
        # "bridge-turn-N" correlates with nothing (reviewer N4).
        execution_id=turn_id if turn_id else f"bridge-turn-{asked_at_turn}",
        # Main passes the lane's own flag rather than a hardcoded literal (N3). Inert
        # today - `run_miss_lane`'s own docstring says D14 suppresses WRITES and this
        # lane has none - but it is a real flag the engine holds.
        dry_run=dry_run,
        roster_caps=roster_caps,
        profile=profile,
    )
    lane_item = {**offer, "branch_kind": "not_found"}
    values = {
        "not_found": not_found,
        "incoming_picker": None,
        "access_choice": None,
        "suggest_offer": offer,
        "gate": gate,
        "offer_hold": None,
    }
    composed = reply_mod.compose_from_fragments(lane_item, ctx, canned, values, db=db)
    text = composed.get("text") or ""
    producers = composed.get("producers") or {}

    # Owner ruling R2, hand pass 12 round 3: a did-you-mean/require-specific roster that
    # ALSO earns a CS member offer must show the roster IN THIS SAME REPLY, not drop it -
    # `cs_offer_gate`'s own g4b (round 2) suppressed `producers["build-cs-member-offer"]`
    # outright whenever this roster exists, so it never rides `composed` at all. Fetched
    # independently here, off the SAME roster-builder chain `cs_offer_gate` itself would
    # have driven, only when that producer is genuinely absent (i.e. g4b, not one of its
    # other three conditions, is the reason it is missing) and a roster is actually on
    # offer - `_miss_question` does the combining; this only supplies the extra rows.
    combined_member_rows: list[Any] | None = None
    already_member = producers.get("build-cs-member-offer")
    if not (isinstance(already_member, Mapping) and already_member.get("member_offer") is True):
        # P8/F7 (hand pass 12 Phase 3): a `gate.compatible_entities` fallback used to
        # sit here for `gate.require_specific is True` - dead code. `_cs_offer_eligible`
        # below's own g4 requires `require_specific is not True`, so that branch could
        # never survive to the eligibility check that gates this whole combine.
        combine_rows = offer.get("suggest_last_result_set") if isinstance(offer, Mapping) else None
        if (
            combine_rows
            and len(combine_rows) >= _MIN_ROSTER_OPTIONS
            and db is not None
            # ESCALATION-CONTROL: no routing roster for a barred contact.
            and not escalation_barred(profile)
            and _cs_offer_eligible(producers.get("escalate-catalog"), routing=(parser or {}).get("routing") or {}, gate=gate)
        ):
            from app.services.chatbot.tail import member_offer as member_mod

            plan = member_mod.cs_roster_plan(gate if isinstance(gate, Mapping) else None)
            responses = member_mod.fetch_rosters(db, plan, ctx if isinstance(ctx, Mapping) else {})
            fetched = member_mod.build_cs_member_offer({}, plan, responses)
            if fetched.get("member_offer") is True:
                combined_member_rows = fetched.get("cs_last_result_set") or None

    question = _miss_question(
        offer,
        producers,
        gate=gate,
        resolved=resolved,
        parser=parser,
        asked_at_turn=asked_at_turn,
        text=text,
        combined_member_rows=combined_member_rows,
    )
    # Phase 3 fix round (26 Sep 2026), review B1/SF2 + B1 follow-up: the SAME audience
    # gate the crossdomain ladder rung above already applies to its own offer TEXT
    # (`include_offer=not is_staff_profile(profile)`) - `_miss_question`'s
    # escalate-catalog branch mints a bare "Yes" `team_pick` with no staff check at
    # all, so a staff contact's order miss armed a hidden escalation nobody was ever
    # shown a sentence for.
    #
    # A genuine ambiguity roster (`pending.is_roster`, e.g. a did-you-mean or member
    # pick) is NOT an escalation offer and must still be ASKED for staff - it is a
    # clarifying question, not a bot-initiated offer (dropping it entirely, the first
    # attempt, left `answer.question is None` while the composed TEXT still numbered
    # the candidates and ended "...or would you like me to escalate to X team?" -
    # `build_suggest_offer`'s own `profile`-gated `_cont` closure, threaded through
    # `run_miss_lane` above, is what strips THAT sentence). The roster survives with
    # its `escalate_offered` stamp and `team` stripped instead - the same shape
    # `turn/compose.py`'s identical audience gate leaves a withheld roster in.
    if is_staff_profile(profile) and question is not None:
        if pending.is_roster(question.kind):
            if question.payload.get("escalate_offered") is True or question.team is not None:
                from dataclasses import replace as _replace_q

                stripped_payload = dict(question.payload)
                stripped_payload.pop("escalate_offered", None)
                question = _replace_q(question, team=None, payload=stripped_payload)
        else:
            question = None
    if escalation_barred(profile) and question is not None:
        # ESCALATION-CONTROL: a barred contact's roster is still ASKED (a clarifying
        # question, not an offer); its escalation half goes, and an escalation offer
        # goes whole.
        from app.services.chatbot.escalation_control import strip_pending

        question, _dropped = strip_pending(question)
    if combined_member_rows and question is not None:
        member_options = [o for o in question.options if o.get("entity_type") == "member"]
        if member_options and any(o.get("entity_type") != "member" for o in question.options):
            # The combine actually happened (the pending carries BOTH groups) - append
            # the CS roster's own sentence, continuing the SAME positions the pending's
            # options already carry.
            text = f"{text}{_cs_roster_text_block(member_options)}"
    if (
        question is not None
        and question.kind == "team_pick"
        and carried_pending is not None
        and pending.is_roster(carried_pending.kind)
        and not _answered_fresh(parser)
    ):
        # Contract 36 / `turn/compose.py`'s own identical rule (hand pass 2, item 8): a
        # roster survives its own pick AND a miss over it - the escalate offer is a
        # SENTENCE the roster carries, not a second question replacing it. Without this,
        # a picked position that missed swapped the still-open roster for a bare
        # `team_pick`, and a LATER, different position had nothing left to match against
        # (hand pass 9 D2, live turns 93d45184-5530-4d04-8f6d-bdda695d241e /
        # 640b6464-fc9b-44c8-9aaf-9dce42358d51) - the roster's own SUBSEQUENT position no
        # longer had anything to be read against once `state.pending` became the
        # one-option `team_pick`, so `decide()` fell to a CARRY reading and reused the
        # FIRST pick's stale focus verbatim (measured: `rules_fired: ["answer_pending_
        # not_an_answer", "reuse_alive"]`, `decision: {"kind": "carry", "why":
        # "nothing_answered"}`) instead of replacing it with the SECOND pick's own
        # product. `_miss_question` only ever mints this exact bare-"Yes" `team_pick`
        # shape on its last, catalog-only branch - a fresh did-you-mean/require-specific
        # roster or a member offer is a genuinely NEW question and replaces the carry as
        # it already does.
        #
        # The guard is `_answered_fresh`, not `payload.get("escalate_offered")`
        # (coder 35's own first attempt, flagged as a live conflict, not committed):
        # measured directly (temporary debug prints, removed before commit) that D2's
        # own photo roster and AC-1704's certificate roster are BYTE-IDENTICAL on
        # `escalate_offered` - BOTH already carry `True` from birth, because both are
        # minted by `_miss_question`'s own did-you-mean/require-specific branch, which
        # stamps it unconditionally. The field that actually tells them apart is the
        # ANSWERING message's own verdict, not the roster's payload: AC-1704's "position"
        # case answers with `reference_target: "dym"` (the parser's own dym-slot marker,
        # `lanes/business/answer.py`'s S5 comment: "on a dym pick the LLM emits the
        # candidate's DYM slot and the parser has already spent it resolving the
        # entity") and its "code" case answers with a genuinely typed entity
        # (`current_message: True`) - main's fresh-typed picker gate (`tail/compile_
        # state.py`, this test module's docstring measurement note 1)
        # retires a roster on EITHER signal. D2/D3's own bare positional picks
        # (`reference_positions` only, no `reference_target`, no entities) carry
        # neither, so the roster stays open for a later, different position - the
        # SAME rule main already applies, not a new heuristic.
        from dataclasses import replace as _replace

        question = _replace(
            carried_pending,
            team=carried_pending.team or question.team,
            payload={**carried_pending.payload, "escalate_offered": True},
        )
    # #1262 slice 11 (F8): same audience gate as the HIT-side ladder rung above.
    text = _apply_crossdomain_render(
        text, crossdomain_result, include_offer=offers_escalation(profile)
    )
    if dealer_stock_ask:
        # Owner ruling 26 Sep 2026 (hand test F1): a dealer's stock ask never offers
        # an escalation. The did-you-mean is a pick of the suggested code(s), carrying
        # the quantity the dealer typed - one candidate included, which AC-1691's
        # two-option minimum above would otherwise turn into a team offer. With no
        # candidate at all, `engine._dealer_refers_to_salesman` strips the offer.
        dealer = _dealer_did_you_mean(offer, parser, asked_at_turn)
        if dealer is not None:
            return turn_compose.Answer(text=dealer[0], question=dealer[1])
    return turn_compose.Answer(text=text, question=question)


def _dealer_did_you_mean(
    offer: Any, parser: Mapping[str, Any] | None, asked_at_turn: int | None
) -> tuple[str, pending.Pending] | None:
    from app.services.chatbot import dealer_stock as dealer_mod

    if not isinstance(offer, Mapping):
        return None
    rows = [row for row in (offer.get("suggest_last_result_set") or []) if isinstance(row, Mapping)]
    if not rows:
        return None
    named = [
        e
        for e in ((parser or {}).get("entities") or [])
        if isinstance(e, Mapping) and e.get("hint") in (None, "product")
    ]
    typed = next(
        (
            c.get("for_raw")
            for c in (offer.get("dym_candidates") or [])
            if isinstance(c, Mapping) and c.get("for_raw")
        ),
        None,
    ) or next((e.get("raw") for e in named if e.get("raw")), "")
    quantities = {
        int(e["quantity"])
        for e in named
        if isinstance(e.get("quantity"), (int, float)) and not isinstance(e.get("quantity"), bool)
    }
    quantity = next(iter(quantities)) if len(quantities) == 1 else (parser or {}).get("demand_qty")
    return dealer_mod.did_you_mean(
        str(typed),
        [dict(row) for row in rows],
        quantity=quantity,
        asked_at_turn=asked_at_turn,
    )
