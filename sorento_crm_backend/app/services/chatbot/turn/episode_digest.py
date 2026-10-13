"""The episode digest: a deterministic, no-LLM summary of a closed episode's turns
(chatbot memory lane A, contract section 5.2 / PLAN-chatbot-memory-26sep.md section
5.2).

**No side effects.** No database library, no ORM handle, no database import of any
kind - `digest()` only looks at the turn dicts it is handed, so it is replayable in CI
and safe to call from both the live writer (`turn/memory.py::write_episode_for_reset`)
and the offline `scripts/backfill_chatbot_episodes.py`, over the identical range of
turns, and get the identical answer (AC-MEM029).

**No figures.** A stock count, a price or an ETA is stale by the next day - the summary
says WHAT was asked and HOW it ended, never the number. Outcome is read from structural
signals the engine really writes (reviewer pass at d89110c0, S5): `branch_kind`, the
`apply` record's `plan.ask` and `plan.denied`, the `looked_up` stage's status and its
`facts.missed` / `facts.sections`, and the `memory` record's open question - never from
a rendered reply, which can say anything.

**Readable.** The summary is a grammar-aware template, not a model call (fix round 6):
what the contact asked about, what they got, what is still open, as plain sentences.
The stored text is what the recall reply and the staff Conversations card both show.

Input: a list of turn dicts, oldest first, each shaped
`{id, created_at, branch_kind, status, message, trace, result_refs}` - the same shape
`chatbot.turns` rows project to (`created_at` a datetime, `trace` the same list of stage/
kind records the trace writer already produces: an `apply` kind record with
`verdict`/`plan`, a `looked_up` stage with `facts.missed`/`facts.sections`, a `tool`
kind record, a `memory` kind record whose `open_question.after` is the question the
reply left open, a `replied` stage whose rendered facts are never read here).
"""
from __future__ import annotations

from datetime import timezone
from typing import Any
from zoneinfo import ZoneInfo

#: `branch_kind` values that decide the outcome outright, before any ask/lookup signal
#: is consulted (AC-MEM022).
_BRANCH_OUTCOME: dict[str, str] = {
    "out_of_scope": "escalated",
    "escalation_declined": "declined",
    "access_denied": "denied",
}

#: A turn with this branch is small talk, not an ask - excluded from `asks` entirely.
_SMALL_TALK_BRANCH = "low_signal"

#: The one close trigger there is today (contract section 3 / PLAN 5.1, Q2 ruling).
_CLOSE_REASON = "topic_switch"

#: The printed line (`episode_line`: date, topic, summary) stays within AC-MEM021's 240
#: chars: the longest `Wed 30 Sep, Product photos and files: ` head is 38, so the
#: sentence gets 200 and the L4 memory layer's worst case is round 5's (fix round 6).
_SUMMARY_CHAR_CAP = 200
_LAST_MESSAGE_CHAR_CAP = 200
#: A `raw` (unresolved) entity token is a customer's own typed text, not a
#: catalog-checked value - capped and whitespace-collapsed (security review 26
#: Sep 2026, S2) so a pasted block of text never turns one entity into a
#: multi-line digest entry or blows out the summary's own char cap.
_ENTITY_TOKEN_CHAR_CAP = 40

_MONTH_ABBR = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

#: The dealers' clock (reviewer pass at d89110c0, S14): a message sent Fri 07:10 in
#: Kuala Lumpur is Thu 23:10 UTC, and must read "Fri".
_LOCAL_TZ = ZoneInfo("Asia/Kuala_Lumpur")

#: Open-question kinds that offer a team (`turn/pending.py::ESCALATION_OFFER_KINDS`).
_OFFER_KINDS = frozenset({"team_pick", "member_offer", "company_pick"})


def local_time(when: Any) -> Any:
    """`when` on the dealers' clock. A naive datetime is UTC (how `chatbot.turns`
    stores it)."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(_LOCAL_TZ)


def _trace(turn: dict[str, Any]) -> list[dict[str, Any]]:
    trace = turn.get("trace")
    return [r for r in trace if isinstance(r, dict)] if isinstance(trace, list) else []


def _kind_records(turn: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [r for r in _trace(turn) if r.get("kind") == kind]


def _stage_record(turn: dict[str, Any], stage: str) -> dict[str, Any] | None:
    for r in _trace(turn):
        if r.get("kind") is None and r.get("stage") == stage:
            return r
    return None


def _apply_record(turn: dict[str, Any]) -> dict[str, Any] | None:
    entries = _kind_records(turn, "apply")
    return entries[-1] if entries else None


def _verdict(turn: dict[str, Any]) -> dict[str, Any]:
    apply_record = _apply_record(turn)
    verdict = (apply_record or {}).get("verdict")
    return verdict if isinstance(verdict, dict) else {}


def _plan(turn: dict[str, Any]) -> dict[str, Any]:
    apply_record = _apply_record(turn)
    plan = (apply_record or {}).get("plan")
    return plan if isinstance(plan, dict) else {}


def _turn_domain(turn: dict[str, Any]) -> str | None:
    domain = _verdict(turn).get("domain_hint")
    if domain:
        return domain
    domains = _plan(turn).get("domains") or []
    return domains[0] if domains else None


def turn_verdict(turn: dict[str, Any]) -> dict[str, Any]:
    """The parser verdict a turn's `apply` record carries (`{}` when it has none)."""
    return _verdict(turn)


def topic_domain(turn: dict[str, Any]) -> str | None:
    """The domain a turn's PLAN acted on, off its `apply` record: the reading the
    topic-switch detector compares (fix lane round 3, R1). The plan, not the parser's
    `domain_hint`: a history question or small talk can carry a hint ("product") while
    planning no domain at all, and must never read as a switch."""
    domains = _plan(turn).get("domains") or []
    return str(domains[0]) if domains else None


def close_trigger(verdict: dict[str, Any], domain: str | None, open_domain: str | None) -> str | None:
    """Why this turn closes the open conversation, or None when it does not (Q2 ruling:
    a conversation ends on a topic switch only). Two readings of a switch, one rule for
    the live engine and the backfill:

    * `topic_reset`: the parser says the message drops the subject ("never mind").
    * `domain_switch`: the turn plans a domain and the open conversation's newest
      planned domain is a different one ("check stock X" then "incoming X"). The
      parser's `topic_reset` stays false there, because the product is the same
      (owner hand test, 28 Sep 2026), so the flag alone never closed it.

    A turn that plans no domain (small talk, a history question, a menu) never
    switches; neither does one with no open domain to switch from."""
    if verdict.get("topic_reset") is True:
        return "topic_reset"
    if domain and open_domain and domain != open_domain:
        return "domain_switch"
    return None


def _entity_token(value: Any) -> str:
    """Whitespace-collapsed and capped at `_ENTITY_TOKEN_CHAR_CAP` (S2) - a
    `canonical_code` is already a short catalog code, but `raw` is whatever the
    customer typed, unbounded and possibly multi-line."""
    return " ".join(str(value).split())[:_ENTITY_TOKEN_CHAR_CAP]


def _entity_display(entities: list[Any]) -> list[str]:
    out: list[str] = []
    for e in entities or []:
        if not isinstance(e, dict):
            continue
        value = e.get("canonical_code") or e.get("raw")
        if not value:
            continue
        token = _entity_token(value)
        if token and token not in out:
            out.append(token)
    return out


def _entity_bucket(entities: list[Any]) -> dict[str, list[str]]:
    """Grouped by entity kind (`hint`), falling back to a generic bucket when a turn's
    entities carry none - the digest's own construction, since `entities` on the wire
    does not always carry `hint` (a synthetic/backfilled turn need not)."""
    buckets: dict[str, list[str]] = {}
    for e in entities or []:
        if not isinstance(e, dict):
            continue
        value = e.get("canonical_code") or e.get("raw")
        if not value:
            continue
        token = _entity_token(value)
        if not token:
            continue
        key = str(e.get("hint") or "entity")
        bucket = buckets.setdefault(key, [])
        if token not in bucket:
            bucket.append(token)
    return buckets


def _merge_entities(target: dict[str, list[str]], source: dict[str, list[str]]) -> None:
    for key, values in source.items():
        bucket = target.setdefault(key, [])
        for v in values:
            if v not in bucket:
                bucket.append(v)


def _open_question(turn: dict[str, Any]) -> dict[str, Any] | None:
    """The question this turn's reply left open, off its `memory` record."""
    entries = _kind_records(turn, "memory")
    after = (entries[-1].get("open_question") or {}).get("after") if entries else None
    return after if isinstance(after, dict) else None


def _outcome(turn: dict[str, Any]) -> str:
    branch_kind = turn.get("branch_kind")
    if branch_kind in _BRANCH_OUTCOME:
        return _BRANCH_OUTCOME[branch_kind]
    plan = _plan(turn)
    if plan.get("denied") and not plan.get("fetch"):
        return "denied"
    if plan.get("ask"):
        return "asked_back"
    looked_up = _stage_record(turn, "looked_up")
    if looked_up is not None:
        facts = looked_up.get("facts") or {}
        if looked_up.get("status") == "failed" or facts.get("missed") or facts.get("sections") == 0:
            return "not_found"
    question = _open_question(turn)
    if question is not None and question.get("kind") not in _OFFER_KINDS:
        return "asked_back"
    return "answered"


def _tools_used(turns: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for turn in turns:
        for entry in _kind_records(turn, "tool"):
            name = entry.get("name")
            if name and name not in out:
                out.append(str(name))
    return out


def _offers(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A team offered in a reply, and what the next turn in the episode did with it:
    accepted (the escalation ran), declined, or no answer."""
    out: list[dict[str, Any]] = []
    for index, turn in enumerate(turns):
        question = _open_question(turn)
        if question is None or question.get("kind") not in _OFFER_KINDS or not question.get("team"):
            continue
        following = turns[index + 1].get("branch_kind") if index + 1 < len(turns) else None
        answer = (
            "accepted"
            if following == "out_of_scope"
            else "declined"
            if following == "escalation_declined"
            else None
        )
        out.append({"team": question.get("team"), "answer": answer})
    return out


def _asks_and_small_talk(turns: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    asks: list[dict[str, Any]] = []
    small_talk = 0
    for turn in turns:
        if turn.get("branch_kind") == _SMALL_TALK_BRANCH:
            small_talk += 1
            continue
        asks.append(
            {
                "domain": _turn_domain(turn),
                "entities": _entity_display(_verdict(turn).get("entities") or []),
                "outcome": _outcome(turn),
            }
        )
    return asks, small_talk


def _date_prefix(when: Any) -> str:
    """`Thu 25 Sep` - the weekday tells the parser (and the staff screen) a line is
    old with no clock math at all (PLAN 5.1: "the day tells the parser a line is
    old")."""
    when = local_time(when)
    weekday = _WEEKDAY_ABBR[when.weekday()]
    month = _MONTH_ABBR[when.month - 1]
    return f"{weekday} {when.day} {month}"


#: What a domain is called in a sentence a person reads (fix round 6, owner hand test
#: 28 Sep 2026: "the structure of our summary is quite messy"). A domain missing here
#: reads as its own name with the underscores spaced out.
_DOMAIN_NOUNS: dict[str, str] = {
    "inventory": "stock",
    "incoming": "incoming stock",
    "order": "orders",
    "master_products": "product details",
    "product_attachment": "product photos and files",
    "promotion": "promotions",
    "forms": "forms",
    "portal_link": "a portal link",
    "resource_attachment": "documents",
    "goods_receive": "goods received",
    "spo_allocation": "stock allocation",
    "ideate": "product ideas",
    "purchase_order": "purchase orders",
    "purchase_cost": "purchase cost",
}

#: The whole summary of an episode that asked nothing. The recall reply skips it.
SMALL_TALK_SUMMARY = "Small talk only, nothing was asked."

#: The stored summary before fix round 6: a date and turn-count header, then
#: semicolon-chained `subject (outcome)` clauses. Read-time code never shows one
#: (`readable_summary`); `scripts/backfill_chatbot_episodes.py` rewrites them.
#: (Plain string reads, no regex module: the turn package stays regex free, see
#: `test_rearch_s2_apply_is_pure`.)
_LEGACY_TAGS = ("answered", "not found", "asked back", "escalated", "declined", "denied")


def _legacy_tags(summary: str) -> list[str]:
    """The `(outcome)` tags of an old summary, in order."""
    found: list[tuple[int, str]] = []
    for tag in _LEGACY_TAGS:
        start = summary.find(f"({tag})")
        while start != -1:
            found.append((start, tag))
            start = summary.find(f"({tag})", start + 1)
    return [tag for _pos, tag in sorted(found)]


def _has_legacy_header(summary: str) -> bool:
    """`Tue 8 Sep, 639 turns: ...` or `Thu 25 Sep: ...`."""
    head, sep, _rest = summary.partition(": ")
    if not sep:
        return False
    words = head.split(",")[0].split(" ")
    if len(words) != 3 or words[0] not in _WEEKDAY_ABBR or words[2] not in _MONTH_ABBR or not words[1].isdigit():
        return False
    tail = head[len(" ".join(words)):].strip()
    if not tail:
        return True
    count = tail.lstrip(",").strip().split(" ")
    return len(count) == 2 and count[0].isdigit() and count[1] in ("turn", "turns")


def _legacy_offers(summary: str) -> list[tuple[str, str]]:
    """`offered warehouse team, no answer` -> `("warehouse", "no answer")`."""
    out: list[tuple[str, str]] = []
    for part in summary.split("offered ")[1:]:
        team, sep, rest = part.partition(" team, ")
        if not sep:
            continue
        answer = next((a for a in ("no answer", "declined", "accepted") if rest.startswith(a)), None)
        if answer:
            out.append((team, answer))
    return out


def domain_noun(domain: str | None) -> str:
    """`inventory` -> `stock`: the word a sentence uses for a domain."""
    if not domain:
        return ""
    return _DOMAIN_NOUNS.get(domain, domain.replace("_", " "))


def topic_label(domain: str | None) -> str:
    """The Topic a recall line and the parser's memory layer print: `Incoming stock`."""
    noun = domain_noun(domain) or "general chat"
    return noun[0].upper() + noun[1:]


def _join(words: list[str]) -> str:
    """`a`, `a and b`, `a, b and c`: a list as a person writes it."""
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _codes(words: list[str], cap: int) -> str:
    """`A, B and C`, or past the cap `A, B plus 3 more`, so a long list never
    nests a second "and" inside the topic list around it."""
    if cap <= 0:
        return ""
    if len(words) <= cap:
        return _join(words)
    return ", ".join(words[:cap]) + f" plus {len(words) - cap} more"


def _display_code(token: str) -> str:
    """A typed product or container code reads in capitals (`srtwc286` ->
    `SRTWC286`); a name such as a customer's stays as typed."""
    return token.upper() if any(ch.isdigit() for ch in token) and " " not in token else token


def _subject_phrase(subjects: dict[str | None, list[str]], code_cap: int, domain_cap: int) -> str:
    parts: list[str] = []
    for domain, codes in subjects.items():
        noun = domain_noun(domain)
        shown = _codes(codes, code_cap)
        if noun and shown:
            parts.append(f"{noun} for {shown}")
        elif noun or shown:
            parts.append(noun or shown)
    if len(parts) > domain_cap:
        parts = parts[:domain_cap] + ["other topics"]
    return _join(parts)


def _was(codes: list[str]) -> str:
    return "was" if len(codes) == 1 else "were"


def _team_phrase(team: Any) -> str:
    return f"the {team} team" if team else "our staff"


def _render_summary(
    asks: list[dict[str, Any]], offers: list[dict[str, Any]], code_cap: int, domain_cap: int
) -> str:
    """Asked, got, still open, in that order (owner round 6 feedback)."""
    subjects: dict[str | None, list[str]] = {}
    answered: list[str] = []
    not_found: list[str] = []
    outcomes: list[str] = []
    for ask in asks:
        codes = [_display_code(c) for c in ask.get("entities") or []]
        domain = ask.get("domain")
        if domain or codes:
            bucket = subjects.setdefault(domain, [])
            bucket.extend(c for c in codes if c not in bucket)
        outcome = ask.get("outcome") or "answered"
        outcomes.append(outcome)
        target = answered if outcome == "answered" else not_found if outcome == "not_found" else None
        if target is not None:
            target.extend(c for c in codes if c not in target)
    # A code answered once and missed once reads as answered.
    not_found = [c for c in not_found if c not in answered]

    if not asks:
        return SMALL_TALK_SUMMARY

    subject = _subject_phrase(subjects, code_cap, domain_cap)
    handed = [o for o in offers if o.get("answer") == "accepted"]
    turned_down = [o for o in offers if o.get("answer") == "declined"]
    unanswered = [o for o in offers if o.get("answer") is None]
    has_miss = "not_found" in outcomes
    # Every code named was missed: a subject-less "answered" (a menu) is no answer.
    all_missed = bool(not_found) and not answered
    has_answer = "answered" in outcomes and not all_missed

    if subject:
        opening = f"Asked about {subject}"
    elif "escalated" in outcomes or "declined" in outcomes:
        opening = "Asked for something the bot does not cover"
    elif "denied" in outcomes:
        opening = "Asked for something this contact cannot see"
    else:
        opening = "Asked a general question"

    sentences: list[str] = []
    if has_answer and not has_miss:
        sentences.append(f"{opening} and got {'an answer' if len(answered) <= 1 else 'answers'}.")
    elif has_miss and not has_answer:
        sentences.append(f"{opening}, but nothing was found.")
    elif subject and set(outcomes) == {"denied"}:
        sentences.append(f"{opening}, which this contact cannot see.")
    else:
        sentences.append(f"{opening}.")
        if has_answer and has_miss:
            if not_found:
                sentences.append(
                    f"{_codes(not_found, 2)} {_was(not_found)} not found, the rest got an answer."
                )
            else:
                sentences.append("Most of it got an answer, but one search found nothing.")

    passed = bool(handed) or "escalated" in outcomes
    refused = bool(turned_down) or "declined" in outcomes
    if passed and refused:
        sentences.append("Was offered our staff, said no once and was passed on once.")
    elif passed:
        sentences.append(f"Was passed to {_team_phrase(handed[0].get('team') if handed else None)}.")
    elif refused:
        team = turned_down[0].get("team") if turned_down else None
        sentences.append(f"Chose not to be passed to {_team_phrase(team)}.")
    if "denied" in outcomes and subject and set(outcomes) != {"denied"}:
        sentences.append("Some of it is not open to this contact.")

    if unanswered:
        sentences.append(
            f"Still open: the offer to pass this to {_team_phrase(unanswered[-1].get('team'))} got no reply."
        )
    elif outcomes[-1] == "asked_back":
        sentences.append("Still open: the bot asked a follow-up question that got no reply.")
    return " ".join(sentences)


def _summary(asks: list[dict[str, Any]], offers: list[dict[str, Any]]) -> str:
    """One or two short sentences a person reads in one pass (fix round 6). No date
    and no turn count: the recall reply and the Conversations card print those from
    the frame's own columns. A long episode shows fewer codes and topics ("and 2
    more") before anything is cut."""
    # Fewer codes before fewer topics: a long episode keeps every topic by name.
    for code_cap, domain_cap in ((3, 4), (2, 4), (1, 4), (0, 4), (0, 3), (0, 2), (0, 1)):
        summary = _render_summary(asks, offers, code_cap, domain_cap)
        if len(summary) <= _SUMMARY_CHAR_CAP:
            return summary
    cut = summary[: _SUMMARY_CHAR_CAP - 1].rsplit(" ", 1)[0].rstrip(",.")
    return cut + "."


_LEGACY_TAG_OUTCOME = {
    "answered": "answered",
    "not found": "not_found",
    "asked back": "asked_back",
    "escalated": "escalated",
    "declined": "declined",
    "denied": "denied",
}


def summary_from_columns(domain: str | None, entities: Any, legacy: str | None = None) -> str:
    """A readable sentence for a frame whose stored summary predates fix round 6 and
    whose turns are not re-digested yet: what was asked from its own `domain` and
    `entities` columns, the handovers and offers the old text's tags still say, and an
    answer only when every tag said answered."""
    codes: list[str] = []
    if isinstance(entities, dict):
        for values in entities.values():
            for v in values if isinstance(values, list) else []:
                token = _entity_token(v)
                if token and token not in codes:
                    codes.append(token)
    tags = [_LEGACY_TAG_OUTCOME[t] for t in _legacy_tags(legacy or "")]
    offers = [
        {"team": team, "answer": None if answer == "no answer" else answer}
        for team, answer in _legacy_offers(legacy or "")
    ]
    if not domain and not codes and not tags and not offers:
        return SMALL_TALK_SUMMARY
    asks: list[dict[str, Any]] = []
    if domain or codes:
        outcome = "answered" if tags and set(tags) == {"answered"} else "unknown"
        asks.append({"domain": domain, "entities": codes, "outcome": outcome})
    asks.extend({"domain": None, "entities": [], "outcome": t} for t in tags if t != "answered")
    if not asks:
        asks.append({"domain": None, "entities": [], "outcome": "unknown"})
    return _summary(asks, offers)


def is_legacy_summary(summary: str | None) -> bool:
    """True for a summary in the pre round 6 shape (header, bracket tags)."""
    if not summary:
        return False
    return _has_legacy_header(summary) or bool(_legacy_tags(summary))


def readable_summary(summary: str | None, domain: str | None, entities: Any) -> str:
    """The stored summary, or, when it is still the old tag chain, the sentence
    `summary_from_columns` builds. Never a bracket tag, never empty."""
    if summary and not is_legacy_summary(summary):
        return summary
    return summary_from_columns(domain, entities, summary)


def episode_line(when: Any, domain: str | None, summary: str) -> str:
    """`Mon 28 Sep, Incoming stock: Asked about ...`: date, topic, sentence. The one
    line the recall reply numbers and the parser's memory layer lists."""
    day = _date_prefix(when) if when is not None else ""
    head = f"{day}, {topic_label(domain)}" if day else topic_label(domain)
    return f"{head}: {summary}"


def digest(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """One episode's digest, from its turns (oldest first). Never empty - the caller
    (`write_episode_for_reset`) already checked the range is non-empty."""
    domains: list[str] = []
    entities: dict[str, list[str]] = {}
    tools_used: list[str] = []
    result_refs: list[Any] = []

    for turn in turns:
        domain = _turn_domain(turn)
        if domain and domain not in domains:
            domains.append(domain)
        _merge_entities(entities, _entity_bucket(_verdict(turn).get("entities") or []))
        for ref in turn.get("result_refs") or []:
            if ref not in result_refs:
                result_refs.append(ref)

    tools_used = _tools_used(turns)
    offers = _offers(turns)
    asks, small_talk_turns = _asks_and_small_talk(turns)
    summary = _summary(asks, offers)

    first_turn = turns[0]
    last_turn = turns[-1]
    last_message = str(last_turn.get("message") or "")[:_LAST_MESSAGE_CHAR_CAP]

    return {
        "domains": domains,
        "entities": entities,
        "asks": asks,
        "offers": offers,
        "small_talk_turns": small_talk_turns,
        "turn_count": len(turns),
        "first_at": first_turn.get("created_at"),
        "last_at": last_turn.get("created_at"),
        "close_reason": _CLOSE_REASON,
        "summary": summary,
        # Extra fields the frame row needs beyond the documented minimum (PLAN 5.2):
        # written into `conversation_frames.domain` / `.intent` / `.tools_used` /
        # `.result_refs` / `.last_user_message` by the caller.
        "domain": domains[0] if domains else None,
        "intent": _verdict(first_turn).get("intent_hint"),
        "tools_used": tools_used,
        "result_refs": result_refs,
        "last_user_message": last_message,
    }
