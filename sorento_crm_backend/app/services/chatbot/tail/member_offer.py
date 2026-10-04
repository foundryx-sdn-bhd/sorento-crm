"""The CS member offer: `cs-roster-plan` + the roster read + `build-cs-member-offer`.

Three n8n nodes become three functions, and the HTTP hop in the middle disappears. n8n
runs `get-cs-members` once per plan item against `GET /external/team-members`; the CRM
already owns that service, so `fetch_rosters` calls it in process. The SHAPE it returns
is deliberately the HTTP node's (`{"body": [...]}` per plan item, `{"error": ...}` on a
failure) because `build_cs_member_offer` is graded against captured items of exactly that
shape, and reshaping it would make the corpus ungradeable to save one dict.

**A company whose roster read fails degrades to an empty roster, never to a failed turn.**
That is n8n's `onError: continueRegularOutput` on the HTTP node, and it is the right
behaviour: one company's misconfigured team must not cost the customer the whole offer.
The empty company is then DISCLOSED in the rendered list rather than silently dropped.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from app.services.chatbot import jsc

logger = logging.getLogger(__name__)


def cs_roster_plan(gate: Any) -> list[dict[str, Any]]:
    """`cs-roster-plan.js` - ONE item per company whose CS roster must be fetched.

    These items ARE the pool identity: `compile-current-state` persists them verbatim as
    `variables.routing_roster_plan`, and `escalation-context` assigns from that same pair
    on the bare-"yes" turn, so the fetch-time and assignment-time axes cannot drift apart.

    No resolve this turn (the gate did not run, or found no companies) gives ONE fallback
    item carrying the gate's `routing_brand` when it has one, which makes exactly today's
    single, null-guarded call.
    """
    g = gate if isinstance(gate, dict) else {}
    companies = jsc.get(g, "routing_companies")
    cos = (
        companies
        if jsc.is_array(companies) and len(companies) > 0
        else [
            {
                "company_id": None,
                "company_name": None,
                "brand_code": jsc.get(g, "routing_brand") or None,
                "codes": [],
            }
        ]
    )
    names = [jsc.get(x, "company_name") for x in cos]
    return [
        {
            "plan_idx": index,
            "company_id": jsc.get(c, "company_id") or None,
            "company_name": jsc.get(c, "company_name") or None,
            "brand_code": jsc.get(c, "brand_code") or None,
            "codes": jsc.array(jsc.get(c, "codes")),
            # customer-facing names for those codes: the note must never print a
            # debtor code
            "labels": jsc.array(jsc.get(c, "labels")),
            "multi_company": len(cos) > 1,
            "companies": [n for n in names if jsc.truthy(n)],
        }
        for index, c in enumerate(cos)
    ]


#: The routing picker's frame, ONE copy: the line over the numbered members and the line
#: under them. Every site that prints the frame reads these, and `order_list.list_reply`
#: (R6: no routing picker inside an open order list) takes the frame out by the same
#: strings - CHATBOT-EMPTY-ROUTE-PICK: the header and the close once stood over an empty
#: list because only the rows were taken out.
#: REFER-ONLY-FIXES: every printing site keeps a blank line between the rows and the close;
#: `order_list._without_picker` reads the rows as the lines under a header up to that blank.
PICKER_HEADER = "Please choose who to route to (reply with the number):"
PICKER_CLOSE = "If you have no preference, just reply 'yes' and we'll assign automatically."
#: The multi-company close's opening words (`build_cs_member_offer`'s `cs_multi_close`
#: names the companies after them).
PICKER_MULTI_CLOSE_PREFIX = "If you have no preference, reply with the company name"
#: The combined did-you-mean + roster wording (`answer_bridge._cs_roster_text_block`,
#: `reply_ladder`): production text verbatim off turn 99c114fd.
ROSTER_HEADER = "To escalate, choose who to route to. Reply the number or name:"
ROSTER_CLOSE = "Or just reply 'yes' and we'll assign automatically."


def fetch_rosters(
    db: Any,
    plan: Sequence[Mapping[str, Any]],
    ctx: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """`get-cs-members`, once per plan item, in process (D1: the HTTP hop was transport).

    The URL n8n builds is
    `?agent_code=<suggested_agent>&team_code=<suggested_team>&tier=1&contact_id=<contact>`
    plus `brand_code` / `company_id` when the plan item carries them, so those are the
    arguments passed here - the same resolution `next-assignee` uses, which is what makes
    an id offered here always acceptable there.
    """
    from app.services.team_roster_service import list_team_roster

    routing = jsc.get(jsc.get(jsc.get(ctx, "parse"), "output") or {}, "routing") or {}
    contact_id = jsc.get(jsc.get(ctx, "contact"), "id")
    out: list[dict[str, Any]] = []
    for item in plan:
        try:
            members = list_team_roster(
                db,
                agent_code=jsc.get(routing, "suggested_agent"),
                team_code=jsc.get(routing, "suggested_team"),
                tier=1,
                contact_id=str(contact_id) if contact_id not in (None, "") else None,
                brand_code=jsc.get(item, "brand_code") or None,
                company_id=jsc.get(item, "company_id") or None,
            )
            out.append({"body": members})
        except Exception as exc:  # noqa: BLE001 - n8n's onError: continueRegularOutput
            logger.warning(
                "cs roster read failed for company_id=%s brand=%s: %s",
                jsc.get(item, "company_id"),
                jsc.get(item, "brand_code"),
                exc,
            )
            out.append({"error": str(exc)})
    return out


def _roster_at(responses: Sequence[Any], index: int) -> list[Any]:
    """`rosterAt` - the member list for plan item `index`, `[]` on anything unusable."""
    if index >= len(responses):
        return []
    r = responses[index]
    if not jsc.truthy(r) or jsc.get(r, "error"):
        return []  # onError item => empty roster for that company
    body = jsc.get(r, "body")
    if jsc.is_array(body):
        return body  # fullResponse shape
    if jsc.is_array(r):
        return r  # legacy: one item, json = array
    return []


def _is_row(member: Any) -> bool:
    """Exclude members with no `respond_user_id` - respond.io assign cannot reach them."""
    return bool(jsc.truthy(jsc.get(member, "user_id")) and jsc.truthy(jsc.get(member, "respond_user_id")))


def build_cs_member_offer(
    catalog: Mapping[str, Any],
    plan: Sequence[Mapping[str, Any]],
    responses: Sequence[Any],
) -> dict[str, Any]:
    """`build-cs-member-offer.js` - the numbered escalation offer plus its roster.

    The roster is stored as `cs_last_result_set` + `selection_context = 'member_offer'`
    so the NEXT turn's position pick resolves to a member. An EMPTY roster falls back to
    the catalog's generic offer, which round-robins on a bare "yes".
    """
    plan_items: list[Mapping[str, Any]] = (
        list(plan)
        if len(plan) > 0
        else [
            {
                "plan_idx": 0,
                "company_id": None,
                "company_name": None,
                "brand_code": None,
                "codes": [],
                "multi_company": False,
                "companies": [],
            }
        ]
    )

    members: list[dict[str, Any]] = []
    legacy_split = (
        len(plan_items) == 1
        and len(responses) > 1
        and not any(
            jsc.truthy(r)
            and (jsc.is_array(jsc.get(r, "body")) or jsc.is_array(r) or jsc.truthy(jsc.get(r, "error")))
            for r in responses
        )
    )
    if legacy_split:
        # legacy: get-cs-members split the array into one item per member
        first = plan_items[0]
        company_name = jsc.get(first, "company_name")
        members = [
            {
                **m,
                "company_id": jsc.get(first, "company_id") or None,
                "company_name": company_name or None,
                "brand_code": jsc.get(first, "brand_code") or None,
                "companies": [company_name] if jsc.truthy(company_name) else [],
                "company_ids": [jsc.get(first, "company_id") or None],
            }
            for m in responses
            if _is_row(m)
        ]
    else:
        seen: dict[Any, dict[str, Any]] = {}
        for index, p in enumerate(plan_items):
            for m in [x for x in _roster_at(responses, index) if _is_row(x)]:
                user_id = jsc.get(m, "user_id")
                prev = seen.get(user_id)
                company_name = jsc.get(p, "company_name")
                company_id = jsc.get(p, "company_id") or None
                # Dedupe by user_id across companies (first company wins the assignment
                # axes), but MEMBERSHIP is a set: the row records every company whose
                # roster returned it, so the renderer and the persisted plan both see a
                # shared member as belonging to all of them.
                if prev is not None:
                    if jsc.truthy(company_name) and company_name not in prev["companies"]:
                        prev["companies"].append(company_name)
                    if company_id not in prev["company_ids"]:
                        prev["company_ids"].append(company_id)
                    continue
                row = {
                    **m,
                    "company_id": company_id,
                    "company_name": company_name or None,
                    "brand_code": jsc.get(p, "brand_code") or None,
                    "companies": [company_name] if jsc.truthy(company_name) else [],
                    "company_ids": [company_id],
                }
                seen[user_id] = row
                members.append(row)

    out: dict[str, Any] = dict(catalog)
    out["routing_companies"] = list(plan_items)  # evidence/debug: the plan behind the roster

    if len(members) == 0:
        out["selection_context"] = None
        out["member_offer"] = False
        out["cs_last_result_set"] = []
        return out

    multi = len(plan_items) > 1

    def _in_company(member: Mapping[str, Any], p: Mapping[str, Any]) -> bool:
        ids = jsc.get(member, "company_ids")
        ids = ids if jsc.is_array(ids) else [jsc.get(member, "company_id") or None]
        return any((i or None) == (jsc.get(p, "company_id") or None) for i in ids)

    if not multi:
        numbered = "\n".join(f"{i + 1}. {jsc.js_string(jsc.get(m, 'name'))}" for i, m in enumerate(members))
    else:
        lines: list[str] = []
        empty: list[Any] = []
        printed: set[int] = set()
        # The group header carries the company - bold, presenter style `*Company:*` - and
        # the member lines are plain `n. Name`. A shared member keeps ONE number and
        # appears under each group it belongs to.
        for p in plan_items:
            group = [(m, i + 1) for i, m in enumerate(members) if _in_company(m, p)]
            if not group:
                if jsc.truthy(jsc.get(p, "company_name")):
                    empty.append(jsc.get(p, "company_name"))
                continue
            lines.append(f"*{jsc.get(p, 'company_name') or 'Other'}:*")
            for m, n in group:
                lines.append(f"{n}. {jsc.js_string(jsc.get(m, 'name'))}")
                printed.add(n)
        # EVERY ROW PRINTS. A member placed under no header would vanish from the printed
        # list while KEEPING its number in `cs_last_result_set`, inviting the customer to
        # pick a number they were never shown. Unreachable today by construction, held by
        # code rather than by inference about upstream shapes.
        for i, m in enumerate(members):
            if (i + 1) in printed:
                continue
            companies = jsc.get(m, "companies")
            labels = (
                companies
                if jsc.is_array(companies) and len(companies) > 0
                else ([jsc.get(m, "company_name")] if jsc.truthy(jsc.get(m, "company_name")) else [])
            )
            name = jsc.js_string(jsc.get(m, "name"))
            lines.append(
                f"{i + 1}. {name} ({' / '.join(jsc.js_string(x) for x in labels)})" if labels else f"{i + 1}. {name}"
            )
        for name in empty:
            lines.append(f"[ {jsc.js_string(name)}: no customer-service members are configured - omitted. ]")
        numbered = "\n".join(lines)

    names = [jsc.get(p, "company_name") for p in plan_items]
    names = [n for n in names if jsc.truthy(n)]
    bold_names = [f"*{jsc.js_string(n)}*" for n in names]
    # MULTI-company: a bare "yes" cannot assign (escalation-context clarifies on
    # `multi_company_unpicked`), so the closing sentence asks for the COMPANY instead.
    # Written ONCE here and exported as `cs_multi_close` so ccs's merge arm cannot drift.
    multi_close = (
        f"If you have no preference, reply with the company name ({' / '.join(bold_names)}) "
        "and we'll assign accordingly."
        if multi
        else None
    )
    out["cs_multi_close"] = multi_close
    offer_company = (
        jsc.js_string(jsc.get(plan_items[0], "company_name"))
        if (not multi and plan_items and jsc.truthy(jsc.get(plan_items[0], "company_name")))
        else None
    )
    out["cs_offer_company"] = offer_company

    # SINGLE company: the offer names the company inside the escalate phrase, e.g.
    # `Would you like me to escalate to *Sorento* customer service team?`. The parser
    # contract is the PREFIX regex, so inserting after "to" is contract-safe. The team
    # half spans MULTIPLE WORDS on purpose: since the display prettifier landed, every
    # multi-word team renders with spaces, and a `\S+ team\?` form silently matched
    # nothing and dropped the label.
    catalog_response = jsc.get(catalog, "response")
    base = catalog_response if jsc.truthy(catalog_response) else "Would you like me to escalate to customer service team?"
    named = _name_company(base, offer_company)
    tail_close = multi_close or PICKER_CLOSE
    out["response"] = f"{named}\n\n{PICKER_HEADER}\n{numbered}\n\n{tail_close}"
    out["member_offer"] = True
    out["selection_context"] = "member_offer"
    out["cs_last_result_set"] = [
        {
            "idx": i + 1,
            "label": jsc.get(m, "name"),
            "uuid": jsc.get(m, "user_id"),
            "respond_user_id": jsc.get(m, "respond_user_id"),
            "company_id": jsc.get(m, "company_id") or None,
            "company_name": jsc.get(m, "company_name") or None,
            "brand_code": jsc.get(m, "brand_code") or None,
            "company_ids": jsc.get(m, "company_ids")
            if jsc.is_array(jsc.get(m, "company_ids"))
            else [jsc.get(m, "company_id") or None],
            "companies": jsc.get(m, "companies")
            if jsc.is_array(jsc.get(m, "companies"))
            else ([jsc.get(m, "company_name")] if jsc.truthy(jsc.get(m, "company_name")) else []),
        }
        for i, m in enumerate(members)
    ]
    # A member offer is a manual response - it must skip the business-summary overwrite.
    out["manualResponse"] = True
    out["includeResponse"] = True
    return out


_ESCALATE_TEAM_RE = None


def _name_company(text: Any, offer_company: str | None) -> Any:
    """`nameCompany` - insert `*Company*` after "escalate to", first match, case-insensitive.

    A no-op when there is no single company (multi turns) or when the text is not a
    string, and also a no-op on a string that already carries `*Company*` after "to",
    because `*` is outside the team character class.
    """
    global _ESCALATE_TEAM_RE
    if offer_company is None or not isinstance(text, str):
        return text
    if _ESCALATE_TEAM_RE is None:
        import re

        _ESCALATE_TEAM_RE = re.compile(
            r"(would you like me to escalate to )((?:[a-z0-9-]+ )*[a-z0-9-]+ team\?)",
            re.IGNORECASE,
        )
    return _ESCALATE_TEAM_RE.sub(
        lambda m: f"{m.group(1)}*{offer_company}* {m.group(2)}", text, count=1
    )
