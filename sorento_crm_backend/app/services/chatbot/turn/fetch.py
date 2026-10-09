# Fetch fan-out: loop `plan.fetch` in order, resolve -> gate -> tool -> envelope
# (PLAN-chatbot-turn-rearch.md "Fetch and compose", AC-1530). A denied domain
# (already decided by `turn/apply.py::_narrow_and_plan`, S2) never reaches a tool -
# it yields a `denied=True` envelope and nothing else.
#
# `ctx` here is a duck-typed bag (`SimpleNamespace` in tests, whatever `engine.py`
# builds in production): `ctx.tool_runner(domain, spec) -> dict` is the ONE seam that
# actually reaches a tool (an MCP call in production, a stub in every test), and
# `ctx.trace` is `app.services.chatbot.trace.TurnTrace`, whose `.add(kind, payload)`
# records one `tool` event per fetched domain (contract 129: "one tool entry per
# domain").
from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.services.chatbot.turn.plan import FetchSpec, Plan


def _denied_envelope(domain: str) -> dict[str, Any]:
    return {
        "domain": domain,
        "denied": True,
        "entities": [],
        "figures": [],
        "files": [],
        "miss": [],
    }


def envelope_missed(env: dict[str, Any]) -> bool:
    """Did this domain answer nothing? ONE rule, read by the ladder here and by the
    composer's own miss line.

    A domain missed when it rendered nothing and was neither refused nor broken: either
    every code it was asked about came back in `miss`, or - when the fetch carried no
    named code at all (an order ask narrowed by customer only, a report scoped by date) -
    the TOOL itself said it had no result. `tool_has_result`, not `has_result`, because
    the latter is ANDed with the figures: a report rendered as `lane_text` found rows and
    is not a miss.
    """
    if env.get("figures") or env.get("denied") or env.get("error"):
        return False
    # A tool that renders its OWN answer has no `figures` for the test above and its
    # codes are never in the rows either, so `envelope_of` puts every code it was asked
    # about in `miss` - which read as a total miss and offered to escalate over a report
    # that had just answered in full (outstanding report, measured). Its own verdict is
    # the only evidence there is about whether it found anything, and that is what the
    # paragraph above already says this rule does.
    if env.get("tool_has_result") is True and str(env.get("lane_text") or "").strip():
        return False
    entities = [e for e in (env.get("entities") or [])]
    miss = [m for m in (env.get("miss") or [])]
    if entities:
        return bool(miss) and set(entities) <= set(miss)
    return env.get("tool_has_result") is not True


def _fetch_one(ctx: Any, domain: str, spec: FetchSpec) -> dict[str, Any]:
    result = ctx.tool_runner(domain, spec)
    envelope: dict[str, Any] = {"domain": domain, "denied": False}
    if isinstance(result, dict):
        envelope.update(result)
    trace = getattr(ctx, "trace", None)
    if trace is not None:
        trace.add("tool", {"domain": domain})
    return envelope


def _ladder_of(ctx: Any, domain: str) -> list[str]:
    """`chatbot_domains.ladder` for this domain, in its configured order (AC-1501)."""
    policy = getattr(ctx, "policy", None)
    row = policy.domain(domain) if policy is not None else None
    return [r for r in (getattr(row, "ladder", ()) or ()) if isinstance(r, str) and r]


def _rung_grant_missing(ctx: Any, rung: str) -> str | None:
    """The reveal key this RUNG needs and this contact does not hold, or None.

    Owner ruling, 8 Sep 2026: what is ON ORDER is a per-contact reveal
    (`purchase_orders.placed`), so the PO rung does not probe for a contact nobody
    granted it - no probe, no PO lines - while the DIRECT ask for purchase orders keeps
    its own gating. A rung is the bot volunteering a document nobody asked for, which is
    the difference.

    `answer._CROSSDOMAIN_RUNG_GRANT` is that rule, and it is READ here rather than
    copied: it is the one place it has ever been written down
    (`test_crossdomain_ladder.py` pins its contents), and two copies would let the ladder
    that probes and the gate that refuses disagree about the same rung. Deliberately NOT
    the rung domain's own `reveal_key`: that column carries per-FIELD reveals too
    (`inventory.sellable` hides a column, it does not refuse the domain), so reading it
    here would silently stop the inventory rung for every contact who may still see
    stock. Imported inside the function to keep this module's import graph the pure one
    its header describes.
    """
    from app.services.chatbot.lanes.business.answer import _CROSSDOMAIN_RUNG_GRANT

    need = _CROSSDOMAIN_RUNG_GRANT.get(rung)
    if not need:
        return None
    raw = getattr(ctx, "granted_reveals", None)
    granted = set(raw) if isinstance(raw, (list, tuple, set, frozenset)) else set()
    return None if need in granted else need


def run_fetch(plan: Plan, ctx: Any, *, bridge_owns_ladder: bool = False) -> list[dict[str, Any]]:
    """`bridge_owns_ladder` (reviewer S1; the plan's own "Deleted" table, row 4): this
    turn's miss is going to be answered by `answer_bridge.answer_for`, which walks
    production's OWN ladder (`answer.run_crossdomain`, per product code, over the
    configured rungs) - so a rung climbed here is fetched and then discarded, because
    the caller composes from `envelopes[0]` alone. Measured: one incoming miss called
    `crm_inventory_stock_balance_list` twice, once per MCP seam, for the same fact.

    The CALLER decides, because only it knows: the predicate is exactly the one
    `engine.py` already uses to gate its own `answer_bridge.answer_for` call, computed
    once and passed here, so the two can never disagree and a single-domain miss the
    bridge will NOT answer (an outstanding report, an open ask, no resolver payload)
    keeps its own climb. Default `False` is today's behaviour unchanged for every
    caller that has no bridge behind it.
    """
    envelopes: list[dict[str, Any]] = []
    # Every domain this turn is ASKED about, whichever order they are fetched in. A
    # ladder rung is the bot volunteering a domain nobody asked for, so a domain that IS
    # on the plan is never a rung: "stock, incoming and PO" fetched all three, the
    # incoming leg missed, and its ladder then fetched stock a SECOND time and printed
    # the same row again under a second header (turn 12a6fff3, 16 Sep 2026).
    planned = {spec.domain for spec in plan.fetch}

    for spec in plan.fetch:
        envelope = _fetch_one(ctx, spec.domain, spec)
        envelopes.append(envelope)
        if not bridge_owns_ladder:
            _climb(ctx, spec, envelope, envelopes, planned)

    for domain in plan.denied:
        envelopes.append(_denied_envelope(domain))

    return envelopes


def _climb(
    ctx: Any,
    spec: FetchSpec,
    primary: dict[str, Any],
    envelopes: list[dict[str, Any]],
    planned: set[str] | None = None,
) -> None:
    """Contract 3 and 125: a domain that answered nothing asks the next one along.

    The ladder is `chatbot_domains.ladder`, walked IN ORDER, and it stops at the first
    rung with rows - the customer asked one question, so they get one answer, from
    whichever domain has it. Every rung goes through the SAME `tool_runner` the primary
    fetch used (same gate, same trace event), and a rung this contact was never granted
    is skipped before the probe runs (`_rung_grant_missing`).

    A rung that answers is appended as its own envelope and the composer prints it as a
    second section under the primary's miss line (contract 122, one section per domain).
    When every rung misses, nothing is appended: the rungs tried are recorded ON the
    primary envelope so the miss line can name them once, which is AC-922's "nothing on
    any rung" shape rather than three empty headers.

    R1 (AC-1688, security review N-1): a rung never runs for a subject that did not
    resolve. The primary fetch is allowed to run with an empty `spec.entities` (that is
    how `_answered_unfiltered` recognises an unresolved-token miss and reports it as
    one), but every rung reuses the SAME spec (`replace(spec, domain=rung)`) - an empty
    entity list would climb the whole ladder and hand each rung's tool the same no-filter
    spec, which is how "ETA cb2805q" once dumped 50 unrelated products under an
    "Incoming" header. Nothing left to narrow BY is nothing to climb with.

    R1 addendum: an EMPTY list is not the only "nothing to narrow BY" shape -
    `narrow.decide`'s `unplaced_never_offered` outcome leaves a NON-empty entities list
    whose members carry no resolved uuid at all (the resolver never placed them), and
    the same unfiltered climb happened for that shape too ("stock and eta for
    SRTWT2634" fired a real `crm_procurement_po_placed_list` call whose args skipped
    every entity `missing_or_bad_uuid`). Refused on the SAME signal
    `lanes/business/fetch.py::entity_ids_transformer` already reads per entity before
    building a tool's `*_ids` (`entity_has_resolved_uuid`, imported lazily to keep this
    module's own import graph the pure one its header describes) - not a second uuid
    rule. A spec with at least one genuinely resolved entity beside an unresolved one
    still climbs: one real subject is something to narrow by.
    """
    if not envelope_missed(primary):
        return
    from app.services.chatbot.lanes.business.fetch import entity_has_resolved_uuid

    if not any(entity_has_resolved_uuid(e) for e in spec.entities):
        return
    rungs = [r for r in _ladder_of(ctx, spec.domain) if r not in (planned or set())]
    if not rungs:
        return

    tried: list[str] = []
    skipped: list[dict[str, str]] = []
    answered: str | None = None
    for rung in rungs:
        missing = _rung_grant_missing(ctx, rung)
        if missing:
            # Not tried and not a miss: the contact was never offered this rung, so it is
            # not one of the domains the miss line names either.
            skipped.append({"rung": rung, "needs": missing})
            continue
        tried.append(rung)
        rung_envelope = _fetch_one(ctx, rung, replace(spec, domain=rung))
        rung_envelope["crossdomain_rung"] = spec.domain
        if not envelope_missed(rung_envelope):
            envelopes.append(rung_envelope)
            answered = rung
            break
    if answered is None:
        primary["rungs_tried"] = list(tried)

    trace = getattr(ctx, "trace", None)
    if trace is not None:
        event: dict[str, Any] = {
            "domain": spec.domain,
            "rungs_tried": list(tried),
            "answered": answered,
        }
        if skipped:
            event["skipped"] = list(skipped)
        trace.add("crossdomain", event)
