"""The eight branch kinds the CRM finishes itself (S3, AC-301).

n8n answers six of them with `escalate-catalog` and the shared compile/send path, reached
straight off the `route` Switch (`route-turn`'s own `TAG_ONLY` set is what reduces five of
those items to `{branch_kind}`; only `offer_hold` has a Set node of its own,
`tag-offer-hold`). The CRM already has every piece - `route.decide` stamps the item,
`tail/outcome.escalate_catalog` is the catalog, and the S2 tail is the compile path - so
this module is small on purpose: it builds the `sub-output` FRAGMENTS each lane would have
handed the tail, and the tail runs unchanged. No second pipeline, no second copy of the
reply ladder.

Two of the eight are not catalog arms and are handled here:

* **`access_denied`** never reaches `compile-current-state` at all. The `route` Switch's
  `access_denied` output goes STRAIGHT to `sorento-sub-respond-sendmsg-respond5` (no tag
  node on that arm), whose `message` expression IS the whole reply - so the CRM composes
  that one string and skips
  the tail. That is not an optimisation: a contact who is not allowed the agent must not
  have the turn written into their memory, and running the tail would write it.
* **`offer_hold`** has no canned text. `offer-hold-reply.js` COMPOSES the clarify ask from
  the persisted pool, and `escalate-catalog` then reads `clarify_text` off it by reference.
  `offer_hold_clarify_text` is that composer, and it is deliberately a standalone function
  because the same body is deployed to `clarify-company-reply` too (its own header says
  so), which S5 will need.
"""
from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.services.chatbot import jsc
from app.services.chatbot.tail.reply_ladder import EM_DASH
from app.services.chatbot.contracts import (
    CRM_COMPLETED_BRANCH_KINDS,
    SELF_CLOSING_BRANCH_KINDS,
)
from app.services.chatbot.copy import CannedCopy

# The eight `branch_kind`s S3 answers, PROJECTED off the one declaration rather than
# repeated: `contracts.CRM_COMPLETED_BRANCH_KINDS` is what the code can finish across every
# slice, and `delegate_for` reads it together with `system_settings.chatbot_completed_lanes`
# to decide a turn. This subset only says which of them THIS module knows how to compose,
# so the kinds with a lane module of their own come off: `low_signal` is answered by
# `lanes/casual.py` (S4) and `out_of_scope` by `lanes/escalation.py` (S5).
COMPLETED_BRANCH_KINDS: frozenset[str] = CRM_COMPLETED_BRANCH_KINDS - SELF_CLOSING_BRANCH_KINDS

# The one lane that answers WITHOUT the tail, and therefore without a session write.
NO_SESSION_WRITE_BRANCH_KINDS: frozenset[str] = frozenset({"access_denied"})


def access_denied_text(db: Session, ctx: Mapping[str, Any], copy: CannedCopy) -> str:
    """`sorento-sub-respond-sendmsg-respond5`'s `message` expression, verbatim -
    with one R18-named exception (S3, AC-1307).

    `Sorry, you are not allowed to access {agent}`, where the agent is the parser's
    `suggested_agent` with **em-dashes folded to hyphens**. That fold is n8n's own
    (`.replace(/\\u2014/g, '-')`) and it is on the AGENT, not on the sentence: an agent
    name that arrives with an em-dash in it is a parser emission, and the customer must
    not read a character the repo forbids anywhere else.

    When the denied agent is `ideation`, the reply goes through the SAME LLM
    composer the ideate lane itself uses (`ideation_turn_service.
    compose_ideate_denial_reply`), facts `{denied: "ideation"}`, falling back to
    this same canned template on any failure - the one `if agent == "ideation"`
    case R18 names as the sole exception to "the engine is not changed by this
    plan".
    """
    routing = jsc.get(jsc.get(jsc.get(ctx, "parse"), "output") or {}, "routing") or {}
    agent = jsc.get(routing, "suggested_agent")
    # ATTACHMENT-MULTI: the agent is a slug (`incoming_stock_enquiries`); the customer reads
    # "incoming stock enquiries". The `ideation` check below reads the raw value.
    folded = jsc.js_string(agent if jsc.truthy(agent) else "").replace(EM_DASH, "-").replace("_", " ")
    fallback_text = copy.render("access_denied", team=folded)
    if agent != "ideation":
        return fallback_text

    from app.services.ideation_turn_service import compose_ideate_denial_reply

    inner = jsc.get(jsc.get(jsc.get(ctx, "text"), "message"), "message") or {}
    user_message = jsc.js_string(jsc.get(inner, "text") or "")
    return compose_ideate_denial_reply(
        db, user_message=user_message, fallback_text=fallback_text
    )


def field_grant_denied_text(copy: CannedCopy, subject: str) -> str:
    """The SAME `access_denied` template, subject a plain-language FEATURE name rather
    than an agent (D6, PLAN-chatbot-last-purchase-cost.md, review B1).

    `access_denied_text` above answers "is this contact allowed the AGENT" - the
    head-level refusal, keyed on `routing.suggested_agent` (`purchasing`,
    `warehouse`, ...). A whole-domain field-reveal gate answers a DIFFERENT
    question - "is this contact allowed the FEATURE" - so it names the feature
    ("purchase cost") rather than the team, and takes no `ctx`: there is no agent to
    read. No new wording is invented; both callers render the one registered
    `access_denied` key.
    """
    return copy.render("access_denied", team=subject)


#: Contract 61's subject: the FEATURE the stock-denial switch refuses, named in the
#: customer's own plain language rather than by a domain key, exactly as
#: `answer.DOMAIN_GRANT_SUBJECT` names the one it pairs with.
STOCK_DENIED_SUBJECT = "stock"


def stock_denied_text(copy: CannedCopy) -> str:
    """Contract 61: the contact is not allowed stock checks and asked for one anyway.

    The SAME registered `access_denied` template every other refusal renders, with the
    feature as its subject (`field_grant_denied_text`) - a denied stock check is the
    same class of answer as a denied field reveal, and inventing a second wording for it
    is how the two drift. A refusal is also the whole reply: the rows are never fetched
    on this arm, so there is nothing else to say and nothing to leak.
    """
    return field_grant_denied_text(copy, STOCK_DENIED_SUBJECT)


def offer_hold_clarify_text(
    *,
    routing_roster_plan: Any,
    routing_companies: Any,
    copy: CannedCopy | None = None,
    picker_shown: bool = True,
) -> str:
    """`offer-hold-reply.js` - the clarify ask, composed from the PERSISTED pool.

    ONE body, deployed to both `offer-hold-reply` and `clarify-company-reply` (that node's
    own header says so), which is why this is a function and not an arm of a switch.

    The pool is `routing_roster_plan` when it has rows, else `routing_companies`. Names are
    DEDUPED - two pool rows sharing a company collapse to one printed name, and the
    single-name parenthetical still resolves because the parser keys the pick per name.

    Three shapes, and the third is the reason the copy is split in two:

    * two or more names: "Both *A* and *B* teams are listed" / "*A*, *B* and *C* teams ...";
    * one name: "*A* teams are listed" (the JS's own wording, plural verb and all);
    * NO names at all (a degraded pool): the company affordance is DROPPED, because the
      parser's company-pick arm refuses every pick against an empty pool and inviting a
      reply that cannot resolve is worse than asking for one that can.
    """
    from app.services.chatbot.copy import fallback_copy

    canned = copy if copy is not None else fallback_copy()
    plan = routing_roster_plan if jsc.is_array(routing_roster_plan) else []
    pools = plan if len(plan) > 0 else (routing_companies if jsc.is_array(routing_companies) else [])

    names: list[str] = []
    for row in pools:
        name = jsc.get(row, "company_name")
        if jsc.truthy(name) and name not in names:
            names.append(name)
    bold = [f"*{jsc.js_string(n)}*" for n in names]
    joined = (
        f"{', '.join(bold[:-1])} and {bold[-1]}" if len(bold) > 1 else (bold[0] if bold else "")
    )
    if len(names) == 2:
        lead = f"Both {joined} teams are listed"
    elif joined:
        lead = f"{joined} teams are listed"
    else:
        lead = "More than one team is listed"

    if names and not picker_shown:
        # #865 round 6 (R4), n8n rev-3 copy branch: no numbered member list was shown, so
        # the ask invites the company alone (the same sentence `escalation.
        # clarify_company_reply` composes for this case).
        return lead + f" - reply with the company ({' / '.join(jsc.js_string(n) for n in names)}) and I'll assign automatically."
    if names:
        return lead + canned.render("offer_hold", companies=" / ".join(bold))
    return lead + canned.render("offer_hold_no_companies")


def fragments_for(
    branch_kind: str,
    item: Mapping[str, Any],
    ctx: Mapping[str, Any],
    prev_variables: Mapping[str, Any],
    copy: CannedCopy,
) -> dict[str, Any]:
    """The `sub-output` trigger fields this lane would have handed the tail.

    Everything the tail needs and nothing it does not: `item` is what `route-turn`
    stamped, and `offer_hold` is the one producer output a canned lane supplies (the
    catalog reads `clarify_text` off it by reference). The other eleven trigger fields are
    null on every canned lane, because none of them runs a resolver, a gate or a fetch.

    **`offer_hold` hands the tail the FULL stamp where n8n hands it `{branch_kind}`.**
    `offer_hold` is not in `route-turn`'s `TAG_ONLY` set, so its item carries `ctx.access`
    plus the tier stamp; n8n then runs it through `tag-offer-hold`, a Set that assigns
    `branch_kind` alone and therefore drops everything else before `Call 'sub-output'6`.
    Kept rather than reproduced because the difference is additive and measured to be
    inert: the item is only spread by `escalate-catalog` (`out = dict(item)`), and
    `compile-current-state` reads exactly four keys off that fragment (`response`,
    `manualResponse`, `includeResponse`, `is_escalate_offer`), none of which an access
    stamp carries. Reproducing the Set would mean the trace screen showed an item the
    router never emitted. Revisit if a tail node ever reads the fragment by spread.
    """
    fragments: dict[str, Any] = {"item": dict(item)}
    if branch_kind == "offer_hold":
        fragments["offer_hold"] = {
            "clarify_company": True,
            "clarify_text": offer_hold_clarify_text(
                routing_roster_plan=prev_variables.get("routing_roster_plan"),
                routing_companies=prev_variables.get("routing_companies"),
                copy=copy,
                picker_shown=prev_variables.get("selection_context") == "member_offer",
            ),
        }
    return fragments
