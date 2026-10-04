# The narrower: per (domain, entity kind) policy, proceed / ask roster / ask type /
# ask tier / filter optional (PLAN-chatbot-turn-rearch.md "The policy", AC-1526).
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.chatbot.turn.state import (
    EXTRA_KIND_ALIASES,
    KIND_FIELD_MAP,
    Focus,
    Profile,
    fold_token,
)
# Core owns the ledger-family rule (`app/services/ledger_family.py`): the stock-ask record
# names a customer-less ask by it too, and core may not import this package. `compose.py`
# and `session_state.py` keep reading the two names from here.
from app.services.ledger_family import ledger_family_key, ledger_family_label

# Which suffix an ask carries, by policy value.
_ROSTER_POLICIES = {"narrow_to_code", "must_narrow_one", "narrow_by_tier"}
_TYPE_POLICIES = {"narrow_by_type"}

#: B1 (owner hand pass 7, 19 Sep 2026, turn "SRTWT165-FT CERT"): every roster has a
#: hard cap - measured, main's `gate.py` caps its own customer-base picker at 8
#: ("8 lines is already a lot") but has no cap for a PRODUCT roster, so 10 is this
#: one's own number. A roster this wide is never a real choice: it printed 200
#: unrelated product codes over one unresolved token. PICKER-NO-CAP (owner, 2 Oct
#: 2026): 50, the same ceiling `chatbot_entity_kinds.roster_cap` starts at.
_ROSTER_CAP = 50


def _candidates(focus: Focus, kind: str) -> list[dict[str, Any]]:
    if kind == "tier":
        return [{"raw": t, "canonical_code": t} for t in focus.tier]
    attr = KIND_FIELD_MAP.get(kind)
    if attr:
        value = getattr(focus, attr, [])
        return list(value) if isinstance(value, list) else []
    # EXTRA_KIND_ALIASES: "customer_order"/"order_number" read the SAME "order" bucket
    # `apply._set_kind_field` writes them to (hand pass 12, Group B).
    return list(focus.extra.get(EXTRA_KIND_ALIASES.get(kind, kind), []))


def _choices(candidates: list[dict[str, Any]], grouping: str | None) -> int:
    """How many CHOICES these rows really are: one per family where the kind has a
    family rule, one per row otherwise."""
    if grouping != "ledger_family":
        return len(candidates)
    keys = {_family_of(c, grouping) or str(c.get("uuid") or id(c)) for c in candidates}
    return len(keys)


def _token_of(candidate: dict[str, Any]) -> str:
    """The word the customer typed for this row, folded the resolver's OWN way.

    R1 (AC-1692): `turn_runtime.unplaced`/`unplaced_tokens` is keyed by `_token_key`,
    which folds `[-\\s]+` out of the token (`resolve_gate._PRODUCT_FOLD`) before the
    resolver ever sees it - a customer typing `SRTWT165-FT` comes back unresolved as
    `SRTWT165FT`. Not folding this side too silently failed the `unplaced` membership
    check below for every hyphenated or spaced code, which is most Sorento codes, and
    let the customer's own unresolved token come back as a one-option roster.
    """
    raw = str(candidate.get("raw") or candidate.get("canonical_code") or "").strip()
    return fold_token(raw)


def _code_of(candidate: dict[str, Any]) -> str:
    """The code this row IS - the resolver's own, the token's only where it matched one."""
    return str(candidate.get("canonical_code") or candidate.get("raw") or "").strip()


def _family_of(candidate: dict[str, Any], grouping: str | None) -> str | None:
    """The key rows of one family share, or None when this kind has no family rule.

    `chatbot_entity_kinds.family_grouping` is the rule and it had no reader at all until
    now: the `customer` row has said `ledger_family` since the S0 seed, and the owner's
    hand pass 2 (item 1) read a six-line roster, one per ledger of ONE trading name, for
    "Delivery for hanlim". Ledgers of one name are one customer.
    """
    if grouping != "ledger_family":
        return None
    name = candidate.get("name") or candidate.get("label") or candidate.get("raw")
    if not name:
        return None
    key = ledger_family_key(str(name))
    return key or None


def _options(
    candidates: list[dict[str, Any]],
    kind: str,
    grouping: str | None = None,
    cap: int | None = None,
) -> list[dict[str, Any]]:
    """One numbered row per candidate.

    `uuid` is the identity the pick resolves to and `uuids` is the FAMILY that identity
    stands for (contract 103): one code with several ledgers or several rows is still one
    thing to the person reading the list, and picking it must yield every member. A
    resolver row carries both; a focus entity has only its own code, which is its own
    family of one.

    `stamp` is what the picker probe already measured about that row ("has incoming"),
    kept as its OWN field rather than folded into `label` - `label` is the resolver's
    own code (contract 28's roster lists what the resolver found, not a sentence about
    it), what a pick resolves back onto (`apply._answer_pending`'s `raw`); the renderer
    is what prints `"{label} - {stamp}"` for the customer to read.

    `code` rides beside `uuid` because they answer two different questions and a pick
    needs both (browser pass 2, turns 4 / 8 / 6): the uuid is what a tool that filters
    by id is given, the code is what a tool that filters by code is given and what the
    answer's own header names. Carrying only the uuid is what printed
    `*incoming stock* for 65514803-1609-4fe8-8b60-2e908c8f9bd4:` and asked the
    outstanding report for `product_code = <uuid>`. For a customer the code and the
    label differ (an account code nobody typed, against the name the roster printed),
    so both are kept.
    """
    # R-g (owner hand pass 7, 19 Sep 2026): which LABELS are shared by more than one
    # COMPANY - the same product code can be a separate record per company
    # (`gate.py`'s own `specific_options` already suffixes a duplicate-code picker
    # line with the company for exactly this reason). Only a label straddling more
    # than one company earns the suffix; a single-company code is untouched.
    company_counts: dict[str, set[str]] = {}
    if grouping != "ledger_family":
        for c in candidates:
            label0 = c.get("name") or c.get("raw") or c.get("canonical_code")
            company0 = c.get("company_name")
            if label0 and company0:
                company_counts.setdefault(str(label0).strip().casefold(), set()).add(
                    str(company0).strip()
                )

    built: list[dict[str, Any]] = []
    by_label: dict[Any, dict[str, Any]] = {}
    for c in candidates:
        code = c.get("canonical_code") or c.get("raw")
        identity = c.get("uuid") or code
        family = c.get("uuids")
        # `name` is the resolver's human label, present only where the code is not what
        # a person would recognise (customers, `turn_runtime.candidates_by_kind`); the
        # pick still resolves through `uuid`, so the label is free to be the name.
        name = c.get("name")
        label = name or c.get("raw") or code
        stamp = c.get("stamp")
        company = c.get("company_name")
        uuids = list(family) if isinstance(family, list) and family else ([identity] if identity else [])
        # ONE LINE PER LABEL, and the line carries every row behind it (contract 103's
        # own rule, read one level up from the code): a family is one code across
        # several ledgers, and a customer is one NAME across several ledgers - browser
        # pass 3 turn 10 printed one trading name once per account and asked the customer
        # to choose between rows they cannot tell apart. The pick still yields every
        # member, because `uuids` is the union.
        family = _family_of(c, grouping)
        if family:
            # Item 1: one line per TRADING NAME. The ledgers of one customer are one
            # choice to the person reading the list ("CHIN CHUN HARDWARE SDN BHD -
            # [A/C I]" and "- [A/C II]" are the same shop), and the pick still reaches
            # every ledger because `uuids` is the union.
            key = family
            label = ledger_family_label(str(name or label or ""))
        else:
            label_key = str(label).strip().casefold() if label else str(identity)
            multi_company = bool(company) and len(company_counts.get(label_key, ())) > 1
            # R-g: a code that is TWO records (one per company) is TWO options, never
            # merged into one because they happen to share a label - browser pass 3's
            # own rule for a ledger family, read the other way for a company.
            key = (label_key, company) if multi_company else label_key
            if multi_company:
                label = f"{label} ({company})"
        merged = by_label.get(key)
        if merged is not None:
            for u in uuids:
                if u not in merged["uuids"]:
                    merged["uuids"].append(u)
            if stamp and not merged.get("stamp"):
                merged["stamp"] = stamp
            continue
        option: dict[str, Any] = {
            "position": len(built) + 1,
            "label": label,
            "code": code,
            "uuid": identity,
            "uuids": uuids,
            "entity_type": kind,
            "payload": {},
        }
        if name:
            option["name"] = name
        if stamp:
            option["stamp"] = stamp
        by_label[key] = option
        built.append(option)
    # B1: a hard cap, in the resolver's own order (already the relevance order the
    # candidates arrived in) - a roster nobody could realistically read through is not
    # a choice, whatever built it. AC-1710/reviewer S4: the number is this kind's own
    # `chatbot_entity_kinds.roster_cap`, handed down by the caller that holds the
    # Policy; `_ROSTER_CAP` is the fallback for a caller with no policy of its own
    # (every direct `decide()` call in the tests), the same role `gate.py`'s named
    # legacy default plays on its side.
    return built[: cap if isinstance(cap, int) and cap > 0 else _ROSTER_CAP]


@dataclass
class NarrowOutcome:
    ask_kind: str | None
    ask_options: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    filter_value: Any
    #: A short reason the caller traces instead of the plain `{domain}.{kind}:
    #: {policy_value}` line, when this outcome is not the policy's ordinary read
    #: (currently only "settled_carry" - see `must_narrow_one` below).
    note: str | None = None


def decide(
    *,
    kind: str,
    policy_value: str,
    focus: Focus,
    profile: Profile,
    attributes: tuple[str, ...] | list[str] = (),
    resolved_candidates: list[dict[str, Any]] | None = None,
    just_picked: bool = False,
    family_grouping: str | None = None,
    unplaced: frozenset[str] | set[str] | None = None,
    roster_cap: int | None = None,
) -> NarrowOutcome:
    """`attributes` is the verdict's `requested_attributes` - what the question asked ABOUT.

    A question that names its own attribute has already answered the narrower's question
    (contract 114 to 120): "which taps have a certificate" needs no "which kind of file?",
    and a count of what this contact may see needs no tier pick before it can be counted.
    So a named attribute satisfies `narrow_by_type` and silences `narrow_by_tier`, and
    changes nothing for any other policy value.

    `roster_cap` is this kind's own `chatbot_entity_kinds.roster_cap` (AC-1710,
    reviewer S4): the rosters this function still raises - customer, attachment_type,
    kind_pick - cut at the CONFIGURED number, not at the module's literal 10. The
    caller holds the Policy, so the caller reads it; `None` keeps `_ROSTER_CAP` for a
    caller with no policy of its own.
    """
    if policy_value == "not_applicable":
        return NarrowOutcome(None, [], [], None)

    candidates = _candidates(focus, kind)

    if just_picked and policy_value in ("narrow_to_code", "must_narrow_one"):
        # Owner hand pass 2, item 10: this kind was answered by a NUMBERED PICK on this
        # very turn. Both roster policies below ask about a CARRY - rows that arrived
        # from an earlier turn and might still be a choice - and neither is a rule about
        # an answer the customer has just given. Re-asking one printed the roster back
        # over its own answer (turn 0a181cfd, "All" over a ten-variant product roster).
        # `narrow_by_tier` is deliberately NOT here: its own branch already reads the
        # tier the pick just wrote and turns it into the FILTER the promotion fetch
        # needs, and short-circuiting it would send the tier through as an entity.
        return NarrowOutcome(None, [], candidates, None, note="just_picked")

    # What the RESOLVER matched for the tokens this turn named. It outranks the focus
    # rows for a narrowing decision, because a roster the customer is asked to choose
    # from has to list things that exist: "wc286" is one focus entity and ten real
    # products, and offering the customer their own typo back is not a choice.
    if resolved_candidates:
        # Owner hand pass 2, item 6, the other half (turn c45e2929, "Outstsnding DO for
        # 7445"): a token THIS message named that the resolver read as SEVERAL things is a
        # roster, whatever the domain does with it afterwards - "a product token on an order
        # ask resolves (roster when ambiguous) and filters the report; never dropped
        # silently". The measured verdict for that turn is `resolved: false, ambiguous:
        # true` over nine SRTWT7445 variants, and a filter takes ONE value, so the
        # `optional_filter` arm below quietly filtered the customer's report by whichever
        # variant the resolver happened to list first. The roster policies already ask this
        # exact question (same options, same builder); this says a FILTER has to know which
        # one it is filtering by too.
        #
        # Only for what this MESSAGE named, and only while it is unsettled: the subject a
        # conversation carries has already been answered for (contract 33 / 35, contract
        # 36's settled roster), and re-asking it on every follow-up turn is the loop the
        # sticky roster exists to avoid. `current_message` is honest for the first time here
        # (`state.focus_from_wire` down-flags what it reads back), which is what coder 12
        # measured as this rule's missing signal.
        #
        # A token that IS one of the codes it matched is not ambiguous at all (AC-1119,
        # console run 3: the owner typed `SRTWT7445`, which exists, and the report ran for
        # `SRTWT7445-LV-GM`). The typed code is the filter and the family siblings beside it
        # are noise, which is the rule the lane already applies downstream - asked here in
        # the same terms so the two cannot disagree about the same token.
        typed_now = {
            _token_of(c).casefold()
            for c in candidates
            if c.get("current_message") is True and not c.get("uuid")
        } - {""}
        # `_token_of` folds (R1); `_code_of` is the resolver's own unfolded code, so the
        # join folds this side too rather than silently stop matching a hyphenated one.
        typed_exactly = any(
            fold_token(_code_of(row).casefold()) in typed_now for row in resolved_candidates
        )
        # PLAN-chatbot-sales-report.md S19: on a SALES REPORT ask a product token is a
        # PREFIX that COVERS its whole family ("Srt5674" is SRT5674 and SRT5674-N and
        # SRT5674-NL, one report over all of them), so an "ambiguous" product is not a
        # choice at all there and there is nothing to ask - the owner's journey says
        # nothing but the customer picker is ever asked. Keyed on the STATUS axis, not
        # on the domain: the outstanding report answers under the same `order` domain
        # and keeps the exact-code rule (AC-1119) this is the opposite of.
        covers_the_family = kind == "product" and focus.status == "sales_report"
        if (
            policy_value == "optional_filter"
            and typed_now
            and not typed_exactly
            and not covers_the_family
            and _choices(resolved_candidates, family_grouping) > 1
        ):
            return NarrowOutcome(
                f"{kind}_pick",
                _options(resolved_candidates, kind, family_grouping, roster_cap),
                [],
                None,
                note="ambiguous_filter_asks",
            )
        # R-a (owner hand pass 6, 17 Sep 2026): a `narrow_to_code` domain re-rosters only
        # for codes THIS resolve is not already fully settled on - `must_narrow_one` and
        # `narrow_by_tier` keep re-rostering on any ambiguity regardless (unchanged).
        # The parser echoes the carried subject back as its own `entities` every turn it
        # continues the domain (`current_message: false` - the prompt's own "CONTINUE
        # it" rule), so `resolved_candidates` comes back non-empty on a plain domain
        # switch too, re-resolving the SAME already-settled family rather than naming a
        # new one. `typed_now` (above) is NOT the right signal for this - it reads
        # `current_message` off the FOCUS carry, and a low-confidence entity ("wc286")
        # never reaches the focus at all (`_focus_rules` only writes confident ones), so
        # gating on it silently dropped a genuinely fresh, ambiguous token's own roster.
        # Comparing CODES instead: if every code the resolver just matched is already a
        # settled (uuid'd) row on the carry, nothing new is on the table, and the fetch
        # runs the settled family rather than asking about it again - "i want stock,
        # incoming and PO" over a settled ten-variant carry rostered the same ten codes
        # back at the customer who had already settled them.
        settled_codes = {_code_of(c).casefold() for c in candidates if c.get("uuid")} - {""}
        resolved_codes = {_code_of(c).casefold() for c in resolved_candidates} - {""}
        already_settled = bool(resolved_codes) and resolved_codes <= settled_codes
        reroster_on_switch = policy_value in ("must_narrow_one", "narrow_by_tier")
        if policy_value in _ROSTER_POLICIES and kind != "tier" and (reroster_on_switch or not already_settled):
            if typed_exactly:
                # R-e (owner hand pass 7, 19 Sep 2026, turn "ETA srtwc8518-SH"): a
                # token that IS one of the resolver's own matched codes settles the
                # narrowing outright, whatever fuzzy family siblings came back
                # beside it - the same rule `ambiguous_filter_asks` above already
                # applies for `optional_filter`. SRTWC8518-SH is an exact
                # product_code; its SH-P/SH-200/SH-300 siblings are noise for THIS
                # narrowing the same way they are for a filter, and rostering them
                # anyway is what asked "which one?" about a code the customer had
                # already named exactly.
                exact_rows = [
                    row
                    for row in resolved_candidates
                    if fold_token(_code_of(row).casefold()) in typed_now
                ]
                return NarrowOutcome(None, [], exact_rows, None, note="typed_exact_settles")
            if _choices(resolved_candidates, family_grouping) <= 1:
                # A code that resolves to exactly one thing IS narrowed to a code -
                # there is nothing left to ask. One FAMILY is one thing too (item 1):
                # six ledgers of one trading name are six rows and one customer, and a
                # picker over them asks the customer to choose between accounts they
                # cannot tell apart.
                return NarrowOutcome(None, [], list(resolved_candidates), None)
            if kind == "product":
                # AC-1690 (PLAN-chatbot-answer-half-reattach.md "Narrowing follows
                # production"): a product roster is `gate.py`'s own job now, for the
                # two `REQUIRE_SPECIFIC_DOMAINS` it names (incoming,
                # product_attachment) - never this narrower's, for ANY domain. Every
                # candidate settles through as a silent prefix filter; the gate's own
                # `require_specific`/`gate_passed` read (independent of this policy
                # value) is what decides whether the fetch's own not_found composes
                # its richer "search needs to be more specific" text with has/no
                # stamps (product_attachment, incoming) or the family just answers
                # unfiltered (purchase_order, spo_allocation - the measured violation:
                # neither domain is in `REQUIRE_SPECIFIC_DOMAINS` despite sharing this
                # SAME policy value).
                return NarrowOutcome(None, [], list(resolved_candidates), None)
            return NarrowOutcome(
                f"{kind}_pick",
                _options(resolved_candidates, kind, family_grouping, roster_cap),
                [],
                None,
            )
        if policy_value not in _ROSTER_POLICIES or kind == "tier":
            candidates = list(resolved_candidates)
        # else: `narrow_to_code` and every resolved code is already settled on the
        # carry - `candidates` stays the CARRIED focus rows assigned above, not the
        # re-resolved echo, so the `settled_carry` tail below runs the fetch for the
        # family already on the focus.

    if policy_value == "list_all":
        return NarrowOutcome(None, [], candidates, None)

    if (
        policy_value in _ROSTER_POLICIES
        and kind != "tier"
        and candidates
        and unplaced
        and all(_token_of(c).casefold() in unplaced for c in candidates)
    ):
        # A roster never offers a token the RESOLVER could not place. "ETA cb2805q" was
        # answered "Which product do you mean? 1. cb2805q" - a one-option menu whose only
        # option is the word the customer had just typed, which is no choice at all (turn
        # e69a0b1b, 17 Sep 2026). The not-found path owns this turn instead: the fetch
        # runs with the unplaced token as its only subject and `turn_runtime` reads that
        # back as the miss it is ("I could not find cb2805q."), which is the same sentence
        # a typo gets anywhere else (coder 16 item 2).
        return NarrowOutcome(None, [], candidates, None, note="unplaced_never_offered")

    if policy_value in _ROSTER_POLICIES:
        if policy_value == "narrow_by_tier" and kind == "tier":
            if attributes and not candidates:
                return NarrowOutcome(None, [], [], profile.tier)
            if candidates:
                # AC-1698 ("1 and 2", "all"): every tier the customer picked settles,
                # not just the last - `apply._set_kind_field` already files every one
                # of them onto `focus.tier` (a list); `filter_value` carrying only
                # `candidates[-1]` threw the rest away before the fetch ever saw them,
                # so "1 and 2" fetched Dealer promotions alone. A single pick stays a
                # bare string (unchanged shape, `turn_runtime._tier_gate`'s own
                # `spec.filters["tier"]` scalar path).
                values = [
                    v
                    for v in (c.get("canonical_code") or c.get("raw") for c in candidates)
                    if v
                ]
                return NarrowOutcome(None, [], [], values[0] if len(values) == 1 else values)
            if profile.tier:
                return NarrowOutcome(None, [], [], profile.tier)
            return NarrowOutcome("tier_pick", [], [], None)
        if policy_value == "must_narrow_one":
            # Ruling (16 Sep 2026, captain): the narrower must never re-ask for an
            # entity the conversation already settled. A focus-carried entity with a
            # `uuid` IS settled (the resolver matched it, or the customer picked it
            # off a roster) - never re-asked. SEVERAL distinct names in play at once
            # (three different customers named the same turn) is a real choice on the
            # table and still asks - unchanged from before. A SINGLE bare name with
            # nothing else to compare it against is handed to the RESOLVER this turn
            # instead of guessed at here: `resolved_candidates` above already covers
            # "the resolver found several for this one token" (asks) and "found
            # exactly one" (settles); reaching HERE with one un-uuid'd candidate means
            # there was no resolver answer for it at all this turn, so it passes
            # through, deferred, rather than an ask manufactured from a name alone.
            if candidates and all(c.get("uuid") for c in candidates):
                return NarrowOutcome(None, [], candidates, None, note="settled_carry")
            if kind != "product" and _choices(candidates, family_grouping) > 1:
                return NarrowOutcome(
                    f"{kind}_pick",
                    _options(candidates, kind, family_grouping, roster_cap),
                    [],
                    None,
                )
            # AC-1690: a product roster is `gate.py`'s own job (see the identical
            # guard above, in the `resolved_candidates` branch) - `product_attachment`
            # is the one domain that configures kind "product" as `must_narrow_one`,
            # and it settles through unfiltered here the same way.
            return NarrowOutcome(
                None, [], candidates, None, note="settled_carry" if candidates else None
            )
        # AC-1526's own wording: "product under incoming and purchase cost asks for
        # a code" - a NAMED candidate is asked about, not assumed. What settles it is
        # IDENTITY: a candidate carrying a `uuid` is one the resolver matched or the
        # customer picked off this very roster, and asking again for a code you
        # already hold re-prints the same question forever (contract 36's own sticky
        # roster is what feeds it back). Reached by `narrow_to_code` AND by
        # `narrow_by_tier` on a kind other than `tier` (its own `kind == "tier"` branch
        # is handled above, near the top of this `if policy_value in _ROSTER_POLICIES`
        # block) - not "narrow_to_code only" (measured, R1).
        if candidates and not all(c.get("uuid") for c in candidates):
            # AC-1691: a roster never offers fewer than two choices - one ambiguous
            # candidate is a settled thing to fetch, not a question, the same rule
            # `must_narrow_one` already applies above for its own policy value.
            # AC-1690: never for kind "product" either way (the identical guard
            # above) - `incoming`/`purchase_order`/`spo_allocation` all configure it
            # as `narrow_to_code`.
            if kind != "product" and _choices(candidates, family_grouping) > 1:
                return NarrowOutcome(
                    f"{kind}_pick",
                    _options(candidates, kind, family_grouping, roster_cap),
                    [],
                    None,
                )
            return NarrowOutcome(None, [], candidates, None, note="settled_carry")
        # R-a (owner ruling, hand pass 6, 17 Sep 2026): re-roster on a domain switch
        # ONLY where the domain must narrow to ONE (`must_narrow_one`/`narrow_by_tier`
        # above). A SETTLED carry - every candidate holds a uuid, so an ambiguous typed
        # token this message named would already have been caught by the branch just
        # above - runs for ALL of them under `narrow_to_code` too, the same
        # `settled_carry` rule `must_narrow_one` already applies. This retires the 16
        # Sep browser-pass-2 ruling that offered a roster here whenever a settled carry
        # held more than one distinct code ("incoming", with no product named, over a
        # ten-variant carry): "hmm ok, any purchase cost" over that same carry re-asked
        # the roster it had already answered instead of running purchase cost at all.
        if candidates:
            # R6 (captain ruling, 16 Sep 2026): a SETTLED carry - every candidate holds
            # a uuid, the resolver matched it or the customer picked it - is the thing
            # to fetch, not nothing. Returning `[]` here left the incoming fetch with no
            # entity after the pick that narrowed it (contract 33 / 35: the follow-up
            # without repeating the product, the domain switch that keeps it).
            return NarrowOutcome(None, [], candidates, None, note="settled_carry")
        return NarrowOutcome(None, [], [], None)

    if policy_value in _TYPE_POLICIES:
        if not candidates:
            if attributes:
                return NarrowOutcome(None, [], [], attributes[0])
            return NarrowOutcome(f"{kind}_ask", [], [], None)
        return NarrowOutcome(None, [], candidates, None)

    # "optional_filter" and anything unrecognised: narrow when named, never asked.
    value = candidates[0].get("canonical_code") or candidates[0].get("raw") if candidates else None
    return NarrowOutcome(None, [], candidates, value)
