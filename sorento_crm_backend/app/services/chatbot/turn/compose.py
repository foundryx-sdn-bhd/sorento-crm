# Composers return data first, text second (PLAN-chatbot-turn-rearch.md "Fetch and
# compose", AC-1531): `compose(envelopes, state, policy, ctx) -> Answer`, `Answer =
# {sections, question, offer, canned, files, actions, text}`. The #930 grammar
# (contract 102 to 105) renders `text` FROM the sections here - `figures` keeps
# today's per-row shape (`{"fields": [{"label", "value"}], ...}`, the same shape
# `lanes/business/fetch.py::output_structurer` already produces), so a domain's own
# composer (kept, unchanged per the plan) has somewhere familiar to write into.
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.services.chatbot.turn.decide import OUTSTANDING_KINDS
from app.services.chatbot.turn.fetch import envelope_missed
from app.services.chatbot.turn.narrow import ledger_family_key, ledger_family_label
from app.services.ledger_family import customer_group_of, customer_header_words
from app.services.chatbot.turn.pending import (
    ask as pending_ask,
    is_roster,
    quick_replies as pending_quick_replies,
    top_selling_pick,
)
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.state import (
    KIND_FIELD_MAP,
    State,
    focus_row_label,
    fold_token,
    escalation_barred,
    token_key,
    offers_escalation,
)
from app.services.chatbot.turn import refer

_ATTACHED_SENTENCE = "I have attached the file(s) below."


def _pretty_team(team: str) -> str:
    """DISPLAY ONLY, underscores to spaces - the same rule `tail.outcome.pretty_team`
    applies, duplicated as one line rather than imported: the `turn` package may not
    import `chatbot.tail` (see `test_turn_package_imports_nothing_from_the_old_seams`
    in the S2 purity tests)."""
    return team.replace("_", " ").strip()


@dataclass
class Section:
    domain: str
    entities: list[Any] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    miss: list[Any] = field(default_factory=list)


@dataclass
class Offer:
    teams: list[str] = field(default_factory=list)


@dataclass
class Answer:
    sections: list[Section] = field(default_factory=list)
    question: Any = None
    offer: Offer | None = None
    canned: list[Any] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""


def _codes_without_rows(entities: list[Any], figures: list[dict[str, Any]]) -> list[str]:
    """Requested codes (uppercase, each once) that no figure row's "Product Code" names.

    Silent when no row carries a "Product Code" field at all (a summary-mode answer
    cannot be matched code by code). Exact match only: "SRTSWT3001" is a prefix of
    "SRTSWT3001-GM", so a substring test over the printed text would hide the bug."""
    present: set[str] = set()
    for fig in figures:
        for f in fig.get("fields") or []:
            if isinstance(f, dict) and f.get("label") == "Product Code" and f.get("value"):
                present.add(str(f["value"]).strip().casefold())
    if not present:
        return []
    absent: list[str] = []
    for entity in entities:
        code = str(entity).strip()
        if not code or code.casefold() in present:
            continue
        if code.upper() not in absent:
            absent.append(code.upper())
    return absent


def _types_without_files(
    product_codes: list[Any], asked_types: list[Any], figures: list[dict[str, Any]]
) -> list[tuple[str, list[str]]]:
    """`[(code, [missing type, ...])]` for each requested product lacking an asked type.

    ATTACHMENT-MULTI R3 (owner ruling 2 Oct 2026, Q2 (a)): the files that exist are sent and
    each gap is named, never silently skipped. Exact match on the rows' "Product Code" and
    "Attachment Type" fields; silent when no row carries both (nothing to match against).
    Each asked type is `{name, keys}` (`turn_runtime.attachment_type_labels`): the presenter
    prints a type's description when it has one, else its name, so both are keys. A type
    with no such entry is never named - it could only ever read as a false gap.
    """
    present: set[tuple[str, str]] = set()
    for fig in figures:
        fields = {
            f.get("label"): str(f.get("value") or "").strip()
            for f in fig.get("fields") or []
            if isinstance(f, dict)
        }
        if fields.get("Product Code") and fields.get("Attachment Type"):
            present.add((fields["Product Code"].casefold(), fields["Attachment Type"].casefold()))
    types = [
        (str(t.get("name")).strip(), {str(k).strip().casefold() for k in t.get("keys") or [] if k})
        for t in asked_types
        if isinstance(t, dict) and str(t.get("name") or "").strip() and t.get("keys")
    ]
    if not present or not types or len(types) != len(asked_types):
        return []
    out: list[tuple[str, list[str]]] = []
    for code in product_codes:
        code_text = str(code).strip()
        if not code_text or any(c.casefold() == code_text.casefold() for c, _ in out):
            continue
        code_key = code_text.casefold()
        missing = [name for name, keys in types if not any((code_key, k) in present for k in keys)]
        if missing:
            out.append((code_text, missing))
    return out


def _row_key(fig: dict[str, Any]) -> tuple:
    return tuple((f.get("label"), f.get("value")) for f in fig.get("fields") or [])


def _render_row(n: int, fig: dict[str, Any]) -> str:
    lines: list[str] = []
    for i, f in enumerate(fig.get("fields") or []):
        prefix = f"{n}. " if i == 0 else ""
        lines.append(f"{prefix}*{f.get('label')}:* {f.get('value')}")
    return "\n".join(lines)


def _file_key(f: dict[str, Any]) -> Any:
    return f.get("url") or f.get("id") or f.get("filename")


def _team_pick_question(
    missed_domains: list[str],
    policy: Policy,
    *,
    agent: str | None = None,
    brand: str | None = None,
):
    """Team pick over the missed domains (contract 106-113, 127; AC-1533). A single
    missed team is yes/no over that one team; two or more become a numbered pick plus
    a "No it's okay" hold option (contract 43).

    `Pending.team` stays a single `str | None` (S2): set when there is exactly one
    team, else None until the pick resolves - each option carries ITS OWN team under
    `payload.team` (captain ruling, 16 Sep 2026).

    `agent` is this minting turn's own `routing.suggested_agent` (SRTSC07, prod
    transcript 22 Sep 2026), carried the same way `team` is: onto the single-team
    offer's own top-level `payload`, and onto EACH multi-team option beside its own
    `payload.team` - so a bare "yes" acceptance turn, which names no agent of its own,
    can still hand `/external/next-assignee` the `(agent_code, team_code)` pair this
    turn actually meant, instead of falling to `DEFAULT_SUGGESTED_AGENT`. `brand` is
    the SAME idiom, one axis over (round 4, owner-approved, 22 Sep 2026): this turn's
    own resolved brand, so a Packing List escalation draws the brand-tagged member
    instead of rotating the whole team.
    """
    teams: list[tuple[str, str]] = []
    seen: set[str] = set()
    for domain in missed_domains:
        row = policy.domain(domain) if policy else None
        team = row.escalation_team_code if row else None
        if not team or team in seen:
            continue
        seen.add(team)
        teams.append((team, row.label if row else str(domain).replace("_", " ")))

    if not teams:
        return None

    if len(teams) == 1:
        team, label = teams[0]
        options = [{"position": 1, "label": label, "entity_type": "team", "payload": {"team": team}}]
        return pending_ask(
            "team_pick",
            options,
            team=team,
            expects="yes_no",
            payload={"agent": agent, "brand_code": brand},
        )

    options = [
        {
            "position": i + 1,
            "label": label,
            "entity_type": "team",
            "payload": {"team": team, "agent": agent, "brand_code": brand},
        }
        for i, (team, label) in enumerate(teams)
    ]
    options.append(
        {
            "position": len(options) + 1,
            "label": "No it's okay",
            "entity_type": "team",
            # Contract 43, the offer hold: picking this option is a DECLINE, and it says
            # so in its own payload rather than being inferred from a null team - a
            # `company_pick` option carries no team either, and "the customer said no"
            # must not be a thing the reader works out from a missing field.
            "payload": {"team": None, "agent": None, "brand_code": None, "hold": True},
        }
    )
    return pending_ask("team_pick", options, team=None, expects="pick")


def _lane_question(envelopes: list[dict[str, Any]], turn_no: int | None = None):
    """The question a LANE asked, as the turn's pending (contract 38, 39).

    A fetch can come back with a question instead of an answer - the outstanding report
    asks which document before it searches, and offers its detail lists after it has -
    and it hands that over on `fetch.outstanding_ask` rather than composing a reply the
    composer would have to parse back. The options are the lane's own rows
    (`{idx, label, value}`), renumbered into the roster shape the tail stores and the
    parser is shown, and the filters it resolved ride on the payload so the turn that
    answers has the subject the question was asked about.

    The FIRST lane to ask wins, because a session holds one open question at a time.
    """
    for env in envelopes:
        ask = env.get("lane_ask")
        if not isinstance(ask, dict) or not ask.get("kind"):
            continue
        rows = [r for r in (ask.get("last_result_set") or []) if isinstance(r, dict)]
        kind = str(ask.get("kind"))
        if kind == "top_selling_pick":
            # PLAN-chatbot-top-x-hot-selling-24sep.md S4: the ranked list's own roster
            # builder (`turn/pending.top_selling_pick`, S1): options carry the printed
            # code and grain, and the payload sends a pick back to the ranking.
            question = top_selling_pick(
                rows, asked_at_turn=turn_no, filters=dict(ask.get("filters") or {})
            )
            if question is not None:
                return question
            continue
        # The OUTSTANDING kinds keep their own `entity_type` (their options are a scope,
        # never a row to pick's own `uuid` - `apply._answer_outstanding` owns the whole
        # answering turn before the generic roster path ever reads `entity_type`). A
        # roster this builder mints for an ENTITY KIND instead (item 1: `form_pick`) needs
        # the bare kind, the same `entity_type` a resolver-matched roster's own
        # `narrow._options` sets - `apply._answer_pending`'s generic pick resolution reads
        # it to know which Focus slot the picked row settles.
        entity_type = kind if kind in OUTSTANDING_KINDS else kind.removesuffix("_pick").removesuffix("_ask")
        options = [
            {
                "position": int(row.get("idx") or i + 1),
                "label": row.get("label"),
                "entity_type": entity_type,
                # The pick's own identity, the same field a resolver-matched option
                # carries (`narrow._options`'s `uuid`) - `apply._answer_pending`'s
                # generic roster resolution reads it to build the fetch entity. Inert
                # for the OUTSTANDING kinds (never reached: they short-circuit first).
                "uuid": row.get("value"),
                # The sales report's drill-downs carry their own typed aliases ("DO" for
                # Delivery orders), which the label match reads (`decide._option_words`).
                "payload": (
                    {"value": row.get("value"), "aliases": list(row["aliases"])}
                    if isinstance(row.get("aliases"), list) and row["aliases"]
                    else {"value": row.get("value")}
                ),
            }
            for i, row in enumerate(rows)
        ]
        if not options:
            continue
        return pending_ask(
            kind,
            options,
            expects="pick",
            asked_at_turn=turn_no,
            payload={
                "domain": env.get("domain"),
                "filters": dict(ask.get("filters") or {}),
            },
        )
    return None


def _with_quantities(codes: list[Any], state: State) -> list[Any]:
    """#1262 fix lane round 2, S1 (AC-S5-4): each envelope code named with the parser's
    own quantity when the focus product row it came from carries one ("M210-GM (x5)"),
    through the ONE label rule `focus_row_label`. The envelope's codes stay bare - the
    ladder probes and the miss list read them as codes."""
    by_code: dict[str, Any] = {}
    for row in getattr(getattr(state, "focus", None), "products", None) or []:
        if not isinstance(row, dict) or row.get("quantity") is None:
            continue
        for key in (row.get("raw"), row.get("canonical_code"), row.get("code")):
            if key:
                by_code.setdefault(fold_token(str(key)).casefold(), row["quantity"])
    if not by_code:
        return codes
    out: list[Any] = []
    for code in codes:
        quantity = by_code.get(fold_token(str(code)).casefold())
        out.append(focus_row_label({"raw": code, "quantity": quantity}) if quantity is not None else code)
    return out


def _header_subjects(entities: list[Any]) -> list[str]:
    """Every subject the answer is FOR, each named once.

    Owner ruling, hand pass 3 row 2 (turn 463413b0): "All" over a customer roster fetched
    fifteen ledgers, correctly, and the header then said one trading name once per ledger
    of it - "CHIN CHUN HARDWARE SDN BHD - [A/C I]" six times over, "JIMMY - I, JIMMY - I".
    Ledgers of one trading name are ONE customer, which is the rule `narrow` already
    groups a roster by, so the header groups by the same one and prints the family's own
    name rather than any one ledger's.

    A code with no family (every product code) keys on itself, so the only thing this
    collapses there is a genuine repeat.
    """
    out: list[str] = []
    seen: set[str] = set()
    for value in entities:
        text = str(value).strip()
        if not text:
            continue
        key = ledger_family_key(text) or text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(ledger_family_label(text))
    return out


#: The most subjects a section header names one by one; past it the header counts them.
HEADER_SUBJECT_MAX = 5

#: Domains whose subjects are products, so a counted header says "products".
_PRODUCT_DOMAINS = frozenset({"inventory", "incoming", "product_attachment", "master_products", "promotion"})


def _routing_brand(ctx: Any) -> Any:
    """The brand a freshly minted `team_pick` stamps: this turn's resolved brand, else the
    focus product's, read only now, at the mint (#865, fix round 2 N2: the read used to run
    on every such turn whether or not anything was minted)."""
    brand = getattr(ctx, "routing_brand", None)
    if brand:
        return brand
    thunk = getattr(ctx, "focus_brand", None)
    return thunk() if callable(thunk) else None


def _code_shaped(token: str) -> bool:
    """A product code is letters AND digits ("srt5764"); a name ("chin chun") or a bare
    number ("1") is not, and gets no escalation offer of its own."""
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


def _line_number(line: str) -> int | None:
    """N for a numbered line ("N. *Product Code:* ...", "N. Sales orders"), else None."""
    head, dot, _rest = line.partition(". ")
    return int(head) if dot and head.isdigit() else None


def _did_you_mean_per_code(
    text: str,
    unplaced: list[str],
    envelopes: list[dict[str, Any]],
    state: State,
    policy: Policy,
    ctx: Any,
    *,
    lane_asked: bool,
) -> tuple[str, Any, bool]:
    """MULTI-CODE-DYM: the reply's unplaced tokens, each as it would be answered alone.

    A token with suggestions (`envelope["unresolved_suggestions"]`, the single-code
    did-you-mean's own candidates and opening sentence, `answer.did_you_mean_head`) gets
    that sentence and its numbered codes; one closing line follows the last of them, with the escalation
    offer the single-code reply makes (none for staff, the salesman line for a barred
    contact). Numbers run on from the reply's own numbered blocks, so no number is
    printed twice, and a code the reply already answered or already offered is not
    offered again. A token with no suggestion keeps "I could not find X.".

    Returns the text, the question to store - one `product_pick` over every offered code
    (AC-1691: two or more), a team offer when one code was offered with the escalation,
    None when a lane already asked this turn's question - and whether any did-you-mean
    was listed (when none was, the miss offer arm offers the escalation instead).
    """
    suggestions: dict[str, dict[str, Any]] = {}
    for env in envelopes:
        carried = env.get("unresolved_suggestions")
        for raw, entry in (carried.items() if isinstance(carried, dict) else []):
            if isinstance(entry, dict) and isinstance(entry.get("rows"), list) and entry.get("head"):
                suggestions.setdefault(raw, entry)
    used = {
        token_key(code)
        for env in envelopes
        for code in (env.get("product_codes") or [])
        if isinstance(code, str)
    }
    numbers = [b for b in (_line_number(line) for line in text.splitlines()) if b is not None]
    n = max(numbers, default=0)
    options: list[dict[str, Any]] = []
    paragraphs: list[str] = []
    plain: list[str] = []
    for token in unplaced:
        lines: list[str] = []
        entry = suggestions.get(token) or {}
        for row in entry.get("rows") or []:
            code = row.get("code") if isinstance(row, dict) else None
            uuid = row.get("uuid") if isinstance(row, dict) else None
            if not code or not uuid or token_key(code) in used:
                continue
            used.add(token_key(code))
            n += 1
            lines.append(f"{n}. {code}")
            options.append(
                {
                    "position": n,
                    "label": code,
                    "code": code,
                    "entity_type": "product",
                    "uuid": uuid,
                    "uuids": [uuid],
                    "payload": {},
                }
            )
        if lines:
            paragraphs.append(str(entry["head"]) + "\n" + "\n".join(lines))
        else:
            plain.append(token)
    if not paragraphs:
        if plain:
            text += "\n" + f"I could not find {_join_words(plain)}."
        return text, None, False

    domain, team = None, None
    for env in envelopes:
        row = policy.domain(env.get("domain")) if policy else None
        if domain is None:
            domain = env.get("domain")
        if row is not None and row.escalation_team_code:
            domain, team = env.get("domain"), row.escalation_team_code
            break
    profile = getattr(state, "profile", None)
    # A question already asked this turn keeps the turn: no offer is stored, so none is
    # printed either.
    offered = bool(team) and offers_escalation(profile) and not lane_asked
    lead_in = "Reply with a code to continue"
    if offered:
        closing = f"{lead_in}, or would you like me to escalate to {_pretty_team(team)} team?"
    elif escalation_barred(profile):
        closing = refer.after(f"{lead_in}.", sep=" ")
    else:
        closing = f"{lead_in}."
    # The codes with no suggestion follow the lists, above the one closing line - the
    # all-miss reply's own order (`answer._unsuggested_line`).
    unsuggested = f"\n\nI could not find {_join_words(plain)}." if plain else ""
    text += "\n\n" + "\n\n".join(paragraphs) + unsuggested + "\n" + closing

    if lane_asked:
        return text, None, True
    agent = getattr(ctx, "suggested_agent", None) if offered else None
    brand = _routing_brand(ctx) if offered else None
    if len(options) >= 2:
        return text, pending_ask(
            "product_pick",
            options,
            team=team if offered else None,
            asked_at_turn=getattr(state, "turn_no", None),
            payload={"domain": domain, "escalate_offered": offered, "agent": agent, "brand_code": brand},
        ), True
    if offered:
        return text, _team_pick_question([domain], policy, agent=agent, brand=brand), True
    return text, None, True


def compose(envelopes: list[dict[str, Any]], state: State, policy: Policy, ctx: Any) -> Answer:
    sections: list[Section] = []
    seen_rows: set[tuple] = set()
    text_parts: list[str] = []
    missed_domains: list[str] = []
    files: list[dict[str, Any]] = []
    seen_file_keys: set[Any] = set()

    for env in envelopes:
        domain = env.get("domain")
        row = policy.domain(domain) if policy else None
        entities = env.get("entities") or []
        figures = env.get("figures") or []
        miss = env.get("miss") or []
        env_files = env.get("files") or []

        sections.append(Section(domain=domain, entities=entities, figures=figures, files=env_files, miss=miss))

        rows_text: list[str] = []
        n = 0
        for fig in figures:
            key = _row_key(fig)
            if key in seen_rows:
                continue
            seen_rows.add(key)
            n += 1
            rows_text.append(_render_row(n, fig))

        # ONE miss rule, shared with the ladder that climbs on it
        # (`turn/fetch.py::envelope_missed`): two copies of "did this domain answer
        # nothing" would let the rung walk and the escalate offer disagree about the
        # same envelope.
        if envelope_missed(env):
            missed_domains.append(domain)

        label = row.label if row else str(domain).replace("_", " ")
        subjects = _with_quantities(_header_subjects(entities), state)
        # W6 (owner hand test round 2, turn 1): a long subject list is counted, never
        # dumped as one line of codes ("*stock* for BRBC22102W, BRBC22108W-1A, ...").
        codes = (
            ", ".join(subjects)
            if len(subjects) <= HEADER_SUBJECT_MAX
            else f"{len(subjects)} {'products' if env.get('domain') in _PRODUCT_DOMAINS else 'items'}"
        )
        # A counted-set answer (AC-1316, "10 taps have certificates.") carries its OWN
        # header, computed off the qualifying total and the class word rather than the
        # domain label - it wins over the generic
        # `*{label}* for {codes}:` line whenever the fetch supplied one, rows or not.
        header_override = env.get("header_override")
        if isinstance(header_override, str) and header_override.strip():
            header = header_override.strip()
        else:
            header = f"*{label}* for {codes}:" if codes else f"*{label}*:"
        # R-d (owner hand pass 7, 19 Sep 2026): the lane's own `lane_text` IS the
        # section, whether or not it carries rows. `lanes/business/fetch.py::
        # output_structurer` already renders the production intro, the per-row
        # grammar WITH its flags (PRODUCT DISCONTINUED, PENDING ALLOCATION) and its
        # date/bool formatting (`_fmt_value`, which this module's own `_render_row`
        # below never applied - measured as the "2026-09-14T00:00:00" defect), and
        # the "_Data last updated: ..._" footer, into ONE string - reusing it
        # verbatim is the one change that keeps every domain's copy production-
        # identical without a second, parallel string table here. A fan-out over
        # several domains still says one thing per section (contract 122) because
        # `lane_text` already names what it is an answer FOR; `own_header`/`denied`
        # already state their own scope the same way (contract 7, AC-1139) and are
        # no longer special-cased - printing `lane_text` verbatim is exactly what
        # those two branches already did.
        #
        # `figures`/`_render_row` below is now a FALLBACK for an envelope that never
        # went through that lane at all (a unit test's own hand-built dict).
        lane_words = env.get("lane_text")
        # #1262 slice 1 (F2): `lane_text` is occasionally the raw text of a FAILED
        # tool call ("Error executing tool crm_outstanding_report: ..."), fed back
        # in by whichever lane last touched `outstanding_carried_*` (the T7/T9
        # hijack the diagnosis transcript recorded) - this module has no way to
        # tell that string apart from a real answer once it has reached
        # `lane_text`, so it is caught by name, once, here, and answered with the
        # SAME neutral line the `error` arm below already gives a broken fetch.
        if isinstance(lane_words, str) and "Error executing tool" in lane_words:
            block = header + "\n" + f"I could not fetch {label} just now, please try again."
        elif isinstance(lane_words, str) and lane_words.strip():
            block = lane_words.strip()
            # Hand pass 12, Group H: a missed leg of a MULTI-domain ask must name
            # itself, never the bare fallback (`lanes/business/fetch.NO_RESULT_INTRO`,
            # imported lazily - this module's own purity test allows `lanes`, unlike
            # `head`/`dialogue`/`tail`/`engine`, but nothing else here needs it).
            # Exact-string, not a `envelope_missed` blanket rule: a richer miss
            # sentence (a report's own refusal, an entitlement line) already names
            # itself and must print untouched - only the tool's OWN generic "found
            # nothing" fallback is replaced, and only when there is a subject to name.
            from app.services.chatbot.lanes.business.fetch import NO_RESULT_INTRO

            if block == NO_RESULT_INTRO and codes:
                subject_label = label.lower() if isinstance(label, str) else str(domain)
                block = f"No {subject_label} found for {codes}."
        elif rows_text:
            block = header + "\n" + "\n\n".join(rows_text)
        elif env.get("denied"):
            block = f"*{label}*: this is not enabled for your account."
        elif env.get("error"):
            # The fetch BROKE - the tool timed out or the call failed - which is neither
            # an answer nor a miss, and the section had nothing to print but its own
            # header. Measured on turn 32425b9a (16 Sep 2026): the MCP call timed out and
            # the customer read `*orders* for HANLIM TRADING SDN BHD:` and nothing else,
            # which reads as "there are none" rather than "ask me again".
            block = header + "\n" + f"I could not fetch {label} just now, please try again."
        else:
            block = header
        # Prod turn f0a2 (1 Oct 2026, "Srtswt3001 / Srtswt3001-gm stock"): both codes
        # resolved, one had no stock rows, and the reply listed only the other. A
        # resolved code with no row in a stock answer is named on one line. The
        # whole-section miss already names every code (above), and an unresolved token
        # is named by "I could not find" below, so neither is repeated here.
        # Only the product subjects (`product_codes`, never a pinned customer), and never
        # on a counted / described set (`header_override`) or a list past the header's
        # own count limit, where every out-of-stock member would land on one line (W6).
        product_codes = env.get("product_codes")
        if (
            domain == "inventory"
            and figures
            and not envelope_missed(env)
            and isinstance(product_codes, list)
            and product_codes
            and len(product_codes) <= HEADER_SUBJECT_MAX
            and not (isinstance(header_override, str) and header_override.strip())
        ):
            absent = _codes_without_rows(product_codes, figures)
            if absent:
                # Above the lane's "_Data last updated: ..._" footer, which closes the
                # section, rather than under it.
                line = f"No stock found for {_join_words(absent)}."
                body, sep, footer = block.rpartition("\n_Data last updated")
                if sep:
                    block = body + "\n" + line + sep + footer
                else:
                    block = block + "\n" + line
        if (
            domain == "product_attachment"
            and figures
            and not envelope_missed(env)
            and isinstance(product_codes, list)
            and product_codes
            and len(product_codes) <= HEADER_SUBJECT_MAX
            and not (isinstance(header_override, str) and header_override.strip())
        ):
            gaps = _types_without_files(product_codes, env.get("attachment_types") or [], figures)
            if gaps:
                lines = "\n".join(f"{code} has no {_join_words(missing)}." for code, missing in gaps)
                # Its own paragraph, and the lane's footer keeps the blank line above it.
                body, sep, footer = block.rpartition("\n_Data last updated")
                block = (
                    body.rstrip("\n") + "\n\n" + lines + "\n" + sep + footer
                    if sep
                    else block.rstrip("\n") + "\n\n" + lines
                )
        # The window the fetch ran with, stated under the header it belongs to (browser
        # pass 6 item 4). Never on a section that states its own scope - the outstanding
        # report and the refusal both do, and the report's own four-line block already
        # carries this exact line (`own_header`), so printing it here too would say the
        # same dates twice in one answer.
        date_line = env.get("date_line")
        if (
            isinstance(date_line, str)
            and date_line.strip()
            and not env.get("own_header")
            and not env.get("denied")
        ):
            head, sep, rest = block.partition("\n")
            block = head + "\n" + date_line.strip() + (sep + rest if sep else "")
        # AC-922, "nothing on any rung": the primary domain missed AND every domain on
        # its ladder missed too. Named once, here, rather than as one empty header per
        # rung - the customer asked about a product, not about three domains.
        rungs_tried = [r for r in (env.get("rungs_tried") or []) if isinstance(r, str)]
        if rungs_tried:
            names = [
                (policy.domain(r).label if (policy and policy.domain(r)) else r)
                for r in rungs_tried
            ]
            block = block + "\n" + f"Nothing on {_join_words(names)} either."
        # Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not merged, owner
        # ruling 24 Sep 2026) for chatbot-stock-ask-v2 S3, D15: "just proceed"
        # answered the products that had a quantity and dropped the rest, so the
        # reply names the ones it did not check - a dealer must never have to work
        # out which of the products they asked about are missing from the answer.
        # LAST, under the rungs line: it is the closing note on this section, and it
        # read as part of the ladder sentence when it sat above one.
        not_checked = [n for n in (env.get("not_checked") or []) if isinstance(n, str)]
        if not_checked:
            block = block + "\n" + f"Not checked: {', '.join(not_checked)}."
        text_parts.append(block)

        for f in env_files:
            key = _file_key(f)
            if key in seen_file_keys:
                continue
            seen_file_keys.add(key)
            files.append(f)

    text = "\n\n".join(text_parts)

    # A token nobody could place is named, never dropped in silence (hand pass 2 item 6,
    # turn 7cf56afe "stock wc287 wc2867 7445": `wc2867` resolved to nothing, the other two
    # resolved, and the answer listed twenty-four codes without a word about the third).
    # Said ONCE for the turn rather than once per section, because it is a fact about the
    # message and not about any one domain. Said on a TOTAL miss too: a header that reads
    # "*incoming stock* for cb2805q:" over "No matching results found." tells a customer
    # their typo exists and has nothing incoming, when the truth is that no such product
    # exists at all ("ETA cb2805q", 17 Sep 2026). Two sentences about one token read as
    # two failures only when one of them is a real answer, which is why the rule used to
    # hold it back.
    unplaced: list[str] = []
    for env in envelopes:
        for token in env.get("unresolved") or []:
            if isinstance(token, str) and token and token not in unplaced:
                unplaced.append(token)
    lane_question = _lane_question(envelopes, getattr(state, "turn_no", None))
    dym_question = None
    dym_listed = False
    if unplaced and text.strip():
        # MULTI-CODE-DYM (owner, 4 Oct 2026, "treat each product code individually"): a
        # token with a did-you-mean gets the one it gets when asked alone; the rest keep
        # this sentence.
        carried = getattr(state, "pending", None)
        asked_this_turn = carried is not None and carried.asked_at_turn == getattr(state, "turn_no", None)
        text, dym_question, dym_listed = _did_you_mean_per_code(
            text,
            unplaced,
            envelopes,
            state,
            policy,
            ctx,
            lane_asked=lane_question is not None or asked_this_turn,
        )

    offer = None
    # ONE open question per turn, and when a lane asked one it is the lane's: a domain
    # that has just asked "which document?" or offered its detail lists is waiting for
    # THAT answer, and an escalate offer over the top of it would leave the customer
    # looking at two numbered lists for one reply - and store the wrong roster for the
    # number they send back.
    # The per-code did-you-mean is this turn's question when no lane asked one, and its
    # closing line already settled the escalation (offered, withheld or the salesman
    # line), so the arm below adds none when one was listed.
    question = lane_question or dym_question
    # Which domains' teams a miss offers. Every section missed: theirs (unchanged). Some
    # section answered but a product CODE the customer typed (letters and digits: a name
    # or a bare number is not one) matched nothing and no did-you-mean was listed for it: the offer that code gets when asked alone (MULTI-CODE-DYM Q4,
    # owner 4 Oct 2026), the first answering domain that has a team.
    if dym_listed:
        offer_domains = []
    elif missed_domains and len(missed_domains) == len(envelopes):
        offer_domains = missed_domains
    elif not dym_listed and text.strip() and any(_code_shaped(t) for t in unplaced):
        offer_domains = [
            env.get("domain")
            for env in envelopes
            if policy and policy.domain(env.get("domain")) and policy.domain(env.get("domain")).escalation_team_code
        ][:1]
    else:
        offer_domains = []
    if question is None and envelopes and offer_domains:
        teams: list[str] = []
        for domain in offer_domains:
            row = policy.domain(domain) if policy else None
            team = row.escalation_team_code if row else None
            if team and team not in teams:
                teams.append(team)
        if teams:
            # #1262 slice 11 (F8), ONE gate for both rules rather than one per call
            # site - guards only the VISIBLE offer (the `Offer` object and its
            # sentence), never the roster re-arm below: that carry is silent
            # bookkeeping for a LATER accepted escalation, not a new offer.
            # - AC-S11-1: staff (`profile.tier == "office"`, owner ruling 2) get no
            #   BOT-INITIATED offer on a miss - they can still ask to escalate
            #   explicitly, a different turn (an `is_escalation_confirmation`
            #   verdict), never this arm.
            # - AC-S11-2: a clarifying question already open when this turn's own
            #   fetch also misses (T3's still-open `kind_pick`, `state.pending`
            #   carried in, never resolved by `_lane_question` above because it is
            #   not THIS turn's own ask) must stay the only open question - a
            #   second one piled on top is what the finding measured.
            # ESCALATION-CONTROL: staff and a barred contact alike get no offer.
            is_staff = not offers_escalation(getattr(state, "profile", None))
            # Phase 3 fix round (26 Sep 2026), review B1/B2: "a clarifying question
            # open" means a question ASKED THIS TURN (a fresh kind pick / did-you-mean
            # / roster the lane just armed), never any carried pending regardless of
            # age - `state.pending is not None` could not tell "a roster still being
            # asked" from "an old, fully-answered one just sitting on state" and hid a
            # DEALER's legitimate offer over the old one too (hand pass 2 item 8).
            carried_for_gate = getattr(state, "pending", None)
            clarifying_open = (
                carried_for_gate is not None
                and carried_for_gate.asked_at_turn == getattr(state, "turn_no", None)
            )
            offer_withheld = is_staff or clarifying_open
            if not offer_withheld:
                offer = Offer(teams=teams)
                # R-f (owner hand pass 7, 19 Sep 2026): a SINGLE team names itself in
                # the offer, the same tail wording `CHATBOT_REPLY_ESCALATE_OFFER`
                # sends ("...to {{team}} team?"), via this module's own
                # `_pretty_team` (the `turn` package may not import `chatbot.tail`).
                # Several teams keep the bare question because the roster right
                # below it is what names them.
                text += (
                    f"\n\nWould you like me to escalate to {_pretty_team(teams[0])} team?"
                    if len(teams) == 1
                    else "\n\nWould you like me to escalate?"
                )
            elif escalation_barred(getattr(state, "profile", None)) and not clarifying_open:
                # ESCALATION-CONTROL (owner ruling Q2): the miss is said above, then the
                # salesman line stands where the offer would; no offer is armed.
                text = refer.after(text)
            carried = carried_for_gate
            if carried is not None and is_roster(carried.kind):
                # Owner hand pass 2, item 8 (turns 29605e65 miss, then 586746d3 "5" and
                # a253e14f "3"): the escalate offer REPLACED the sticky roster the miss
                # was about, so the next number resolved against a team picker and the
                # customer got the offer printed back at them twice. A roster survives
                # its own pick (contract 36) and it survives a miss too - the offer is a
                # SENTENCE appended under the answer, not a second question. The team it
                # would escalate to rides on the roster, so a "yes" over this state still
                # reaches the right team.
                #
                # Phase 3 fix round, review B1: the roster ITSELF always survives (a
                # withheld offer must not also drop the customer's still-open pick),
                # but the escalate stamp below - `escalate_offered` and the team/agent/
                # brand that ride with it - is added ONLY when the offer was actually
                # shown. This ran unconditionally before, so a withheld offer (staff, or
                # a question asked THIS turn) still armed `escalate_offered: True` on
                # the pending, and a later bare "yes" over it routed to an escalation
                # nobody was ever shown.
                payload = dict(carried.payload)
                team = carried.team
                if not offer_withheld:
                    team = carried.team or teams[0]
                    payload["escalate_offered"] = True
                    # SRTSC07 review round 2, SHOULD-A: both halves come from
                    # the SAME source as the `team` expression right above, not
                    # independently. A roster CAN carry a team with no agent at
                    # all (`answer_bridge.py`'s D4 narrower roster,
                    # `turn/apply.py`'s narrow ask) - `carried.payload.get(
                    # "agent") or ctx.suggested_agent` mixed a STALE carried
                    # team with THIS turn's fresh agent whenever that happened,
                    # a pair `/external/next-assignee` has no link for
                    # (measured: an incoming miss with no agent, re-armed under
                    # a later order-domain miss, paired `order_enquiries` with
                    # the old `purchasing` team). When the team is the roster's
                    # OWN (`carried.team` truthy), the agent is the roster's own
                    # too, carried or not - never THIS turn's, which named no
                    # opinion about the roster's team at all. Only when the team
                    # itself falls to `teams[0]` (this turn's own) does the
                    # agent follow it.
                    payload["agent"] = (
                        carried.payload.get("agent")
                        if carried.team
                        else getattr(ctx, "suggested_agent", None)
                    )
                    # Round 4 (owner-approved, 22 Sep 2026): the SAME one-source
                    # rule, one axis over - the brand travels with whichever
                    # source the team came from.
                    payload["brand_code"] = (
                        carried.payload.get("brand_code")
                        if carried.team
                        else _routing_brand(ctx)
                    )
                question = replace(carried, team=team, payload=payload)
            elif not offer_withheld:
                # #1262 slice 11 (F8): no carried roster to attach the offer to, and
                # staff get no BOT-INITIATED one at all - a fresh `team_pick` here
                # would be an escalation offer nobody was shown any sentence for,
                # which is exactly what AC-S11-1 says must not be armed. Phase 3 fix
                # round: a question asked THIS turn withholds it the same way -
                # arming a second, fresh team_pick under an already-open clarifying
                # question is the same double-ask the roster-carry branch above
                # exists to avoid.
                #
                # SRTSC07 (prod transcript, 22 Sep 2026): `ctx.suggested_agent` is this
                # turn's own `routing.suggested_agent` (`TurnContext`, set by
                # `engine.py` off the SAME verdict the team half above is read from),
                # carried onto the fresh offer so a later bare "yes" over it can hand
                # `/external/next-assignee` the `(agent_code, team_code)` pair this
                # turn actually meant. `ctx.routing_brand` (round 4) is the SAME idiom
                # for the brand axis.
                question = _team_pick_question(
                    offer_domains,
                    policy,
                    agent=getattr(ctx, "suggested_agent", None),
                    brand=_routing_brand(ctx),
                )

    actions: list[dict[str, Any]] = []
    if files:
        actions = [
            {"kind": "send_file", "url": f.get("url"), "filename": f.get("filename")} for f in files
        ]
        # R-d: a domain with attachments (incoming, product photos, ...) already
        # opens its OWN `lane_text` with this exact sentence (`sorento_crm_mcp.
        # presenters`'s universal "has attachments" intro) now that `lane_text` is
        # reused verbatim above - appending a second copy read as the sentence
        # twice in one reply.
        if _ATTACHED_SENTENCE not in text:
            text += "\n\n" + _ATTACHED_SENTENCE

    return Answer(
        sections=sections, question=question, offer=offer, canned=[], files=files, actions=actions, text=text
    )


# The sentence each pending kind opens with. One wording per question, in one place, so
# the ask a customer reads and the pending the tail stores can never describe different
# questions (contract 29, 37, 50).
#
# PLAN-chatbot-answer-half-reattach.md R4 (Deleted table, AC-1680): the single-domain
# product / customer / tier picker headers, the did-you-mean header and the
# require-specific clarification header are RETIRED from here - a single-domain business
# turn's roster/miss/tier question is answered by `answer_bridge.py` through production's
# OWN composers (`gate.py`'s own clarification text, `answer.access_level_choice_message`,
# `answer.not_found_error_message` / `miss_suggest`), never a second, parallel wording.
# This table (and `compose_question` below) still serves the kinds those composers do not
# own: the multi-domain team pick, the lane's own outstanding/forms/sales-report offers,
# and the narrowing kinds `narrow.decide` still mints for domains outside the bridge's
# reach (R6 lists and retires those still-shadowed asks).
_ASK_HEADERS: dict[str, str] = {
    "team_pick": "Which team should take this?",
    "company_pick": "Which company do you mean?",
    "member_offer": "Who should take this?",
    # The lane's own wording for the same question (contract 38): the customer read
    # "Outstanding for which document?" when it was asked, and a re-print that opened
    # with a different sentence reads as a second, different question.
    "outstanding_scope": "Outstanding for which document?",
    "outstanding_detail": "Which list would you like?",
    # The sales report's own detail offer (PLAN-chatbot-sales-report.md S4 wiring point
    # 7) asks the same question. Its live wording is the presenter's own sentence,
    # carried verbatim on `payload.filters.offer_text` like the outstanding one's; this
    # is the fallback for a re-print of an offer that stored none.
    "sales_report_detail": "Which list would you like?",
    "kind_pick": "Which one do you mean?",
    "attachment_type_ask": "Which kind of file do you need?",
}


def _join_words(names: list[str]) -> str:
    """`a`, `a or b`, `a, b or c` - the list grammar the rung line reads with."""
    clean = [n for n in names if n]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    return ", ".join(clean[:-1]) + " or " + clean[-1]


#: How the scope line names each axis - the report's own words for the same filters
#: (`sorento_crm_mcp.presenters._outstanding_header_lines`), so a question and an answer
#: about the same subject read the same way.
_SCOPE_AXIS_LABELS: tuple[tuple[str, str], ...] = (
    ("products", "Product"),
    ("customers", "Customer"),
    ("warehouse", "Location"),
)


def _subject_line(state: State | None, asked_kind: str) -> str:
    """What the conversation is already about, for a question about something else.

    Owner journey `chinchun-x-only-customer-survives` / browser pass 7 row 2: "orders for
    chin chun" then "For srtwc286 only" keeps the customer (the fetch two turns later
    proves it) but asks `Which product do you mean?` with no mention of CHIN CHUN
    anywhere, so mid-conversation there is nothing on screen saying the customer
    survived. Only axes this question is NOT about, and each family named once, by the
    same rule the answer header uses.

    Reads each row through `focus_row_label` (hand pass 12 Phase 3 finding P1) so a
    caller that filled `display_name` onto a local copy of the carried rows before
    calling `compose_question` - the same fill `engine.py` already does for the
    HIT-arm scope block - is actually reflected here, instead of always falling back
    to the option's shared rollup `canonical_code`.
    """
    if state is None:
        return ""
    # A roster's kind is `<entity kind>_pick` (`narrow.decide` builds it that way), and
    # the axis it is ASKING about is never part of the scope line - the numbered options
    # under it are that axis.
    asked_axis = KIND_FIELD_MAP.get(asked_kind.removesuffix("_pick").removesuffix("_ask"))
    lines: list[str] = []
    for attr, label in _SCOPE_AXIS_LABELS:
        if attr == asked_axis:
            continue
        rows = getattr(getattr(state, "focus", None), attr, None) or []
        names = [
            str(focus_row_label(r) or "").strip()
            for r in rows
            if isinstance(r, dict)
        ]
        if attr == "customers":
            words = customer_header_words([(n, customer_group_of(n)) for n in names if n])
            if words:
                lines.append(f"{label}: {words}")
            continue
        subjects = _header_subjects([n for n in names if n])
        if subjects:
            lines.append(f"{label}: {', '.join(subjects)}")
    return "\n".join(lines)


#: R8 (owner retest of top selling round 4): the longest menu a ranking conversation prints.
MAX_MENU_OPTIONS = 5


def _in_ranking(state: State | None) -> bool:
    focus = getattr(state, "focus", None)
    return bool(focus is not None and (focus.status == "top_selling" or focus.top_selling))


def _short_question(pending: Any) -> str:
    count = len(pending.options)
    if pending.kind == "top_selling_pick":
        return f"Which item do you mean? Reply with a rank number from 1 to {count}."
    header = _ASK_HEADERS.get(pending.kind, "Which one do you mean?")
    return f"{header} I found {count}, please type a little more of the name."


def compose_question(pending: Any, state: State | None = None) -> Answer:
    """The ask, as an Answer: the subject line, the header, the numbered roster, and the
    same pending back.

    A roster the customer can see is what a bare "1" answers next turn, so the options
    that are PRINTED here are exactly the options the tail stores - one list, never two.
    """
    header = _ASK_HEADERS.get(pending.kind, "Which one do you mean?")
    lines = [header]
    labels: list[str] = []
    for option in pending.options:
        label = option.get("label")
        if label is None:
            continue
        stamp = option.get("stamp")
        printed = f"{label} - {stamp}" if stamp else str(label)
        labels.append(printed)
        lines.append(f"{option.get('position')}. {printed}")
    subject = _subject_line(state, str(getattr(pending, "kind", "")))
    body = "\n".join(([subject] if subject else []) + lines)

    # AC-1102: a question the LANE composed is re-printed in the lane's own bytes. The
    # outstanding report's detail offer is worded by the MCP presenter (R9's one
    # sentence for a single-scope report, the numbered list for two), and rebuilding it
    # from the roster produced two wordings for one offer, one after the other in the
    # same conversation. The offer's own text travels on the question's payload; every
    # other kind has no text of its own and reads the header table above.
    offered = (pending.payload or {}).get("filters") or {}
    verbatim = str(offered.get("offer_text") or "").strip() if isinstance(offered, dict) else ""
    if verbatim:
        body = verbatim
        positions = [o.get("position") for o in pending.options if isinstance(o.get("position"), int)]
        if pending.kind == "sales_report_detail" and positions:
            # PR #1401 fix round 3, F4 (mock v3 section 8, carried into v4): a re-print of
            # the sales report's drill offer says which numbers it takes, first to last
            # (the options continue after the rows printed, so the first may be 11).
            body = f"Please reply with a number from {min(positions)} to {max(positions)}.\n" + verbatim
    elif len(labels) > MAX_MENU_OPTIONS and _in_ranking(state):
        # Owner retest of top selling round 4 (27 Sep 2026, R8): inside a ranking no
        # menu lists more than five options (it listed all 100 ranked codes under
        # "2025?"); the bot asks one short question in words and a typed number or
        # name still picks, off the same stored options.
        body = _short_question(pending)
        labels = []
    action: dict[str, Any] = {
        "kind": "send_message",
        "text": body,
        # AC-1866: a `member_offer` re-print keeps its numbered text list but not the
        # names as quick-reply buttons (owner ruling 23 Sep 2026) - `result_set` below
        # still carries the roster, so a numbered reply still resolves.
        "quick_replies": pending_quick_replies(pending.kind, labels),
        "result_set": list(pending.options),
    }
    return Answer(sections=[], question=pending, offer=None, canned=[], files=[], actions=[action], text=body)


def _join_words_and(items: list[str]) -> str:
    """"a", "a and b", "a, b and c" - the same shape `media_extract.wording.join_
    phrase` uses, copied rather than imported (`turn/` reads no module outside its
    own package and `contracts.py`). NOT `_join_words` above: that one joins on
    "or" for the rung-fallback list ("Nothing on X or Y either.") - a distinct
    word for a distinct grammar, not a formatting variant of the same one."""
    values = [item for item in items if item]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + " and " + values[-1]


def entities_only_reply(
    placed: list[str], unplaced: list[str], *, from_photo: bool, media_prefixed: bool = False
) -> str:
    """S3 (PLAN-chatbot-media-into-turn.md): the entities-only arm's own deterministic
    reply (AC-1824/AC-1825) - never an LLM, never a roster. `placed`/`unplaced` are the
    raw tokens as typed or read, in the order the message named them.

    `media_prefixed`, true on a photo-sourced turn, drops this function's OWN "I read
    ..." lead: `engine.py`'s reply-prefix wrapper (AC-1817, the SAME sentence shape,
    the intake's own raws) already supplies it for every media turn, and printing it
    twice would violate AC-1820 ("the prefix appears exactly once"). A typed turn
    carries no such wrapper, so it stays self-contained.

    Review round B1(a): nothing PLACED is its own case, not "I have ." with an empty
    join - a photo where every code missed says so up front ("I could not match any
    product code in that photo."); a typed message with nothing placed says try again,
    since "What would you like me to know?" has nothing left to be about.

    Browser pass follow-up: the "I could not match..." lead is ALSO gated on
    `media_prefixed`, exactly like the placed branch below - a LIVE photo outcome
    already told the customer what was read ("I read X from that photo.", via the
    engine's own reply-prefix wrapper), so this arm claiming "I could not match ANY
    product code" on top of that would contradict what the wrapper just said. Only
    the `patched_upstream` case (no live outcome, no wrapper prefix at all) still
    needs this arm's own lead to say anything was a photo in the first place.
    """
    if not placed:
        parts: list[str] = []
        if from_photo and not media_prefixed:
            parts.append("I could not match any product code in that photo.")
        if unplaced:
            parts.append(f"Couldn't find {_join_words_and(unplaced)}.")
        parts.append(
            "What would you like me to do with it?" if from_photo else "Ask again with the correct code."
        )
        return " ".join(parts)

    parts = []
    if not media_prefixed:
        lead = (
            f"I read {_join_words_and(placed)} from that photo."
            if from_photo
            else f"I have {_join_words_and(placed)}."
        )
        parts.append(lead)
    if unplaced:
        parts.append(f"Couldn't find {_join_words_and(unplaced)}.")
    parts.append(
        "What would you like me to do with it?" if from_photo else "What would you like me to know?"
    )
    return " ".join(parts)
