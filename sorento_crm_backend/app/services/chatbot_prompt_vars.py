"""Registry variables of the chatbot parser prompt (PLAN-prompt-dynamic-30sep D1-D5).

The owner edits the WORDING of `chatbot_semantic_parser`; every list the CRM owns sits in
that wording as a `{{name}}` token and is rendered HERE, from its table, when a turn
reads the prompt. A domain, status word, entity kind, spec, brand, team, agent or access
level saved on its admin page therefore reaches the next turn with no publish, and a
publish never rewrites the owner's text: removing a token is how he overrides a list,
and nothing puts it back.

Rendered values are cached per process for `CACHE_TTL_SECONDS`, and the cache is cleared
by an `after_commit` hook whenever the committing session wrote a row to one of
`REGISTRY_TABLES` (D5). Another process (the worker) converges within the TTL.

Core module on purpose (a sibling of `chatbot_parser_prompt.py`, which renders the same
domain and entity-kind lines): `ai_prompt_registry` calls it, and core never imports
`app/services/chatbot/` (AC-002), so the two code fallbacks come through
`app.modules.chatbot.lane_vocabulary`.
"""
from __future__ import annotations

import json
import logging
import re as _re_mod
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import event, text as sql
from sqlalchemy.orm import Session

PROMPT_KEY = "chatbot_semantic_parser"

CACHE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class RegistryVariable:
    name: str
    label: str
    source: str  # the admin page's name, as the chip says "from <source>"
    href: str
    tables: tuple[str, ...]
    render: Callable[[Session], str]
    count: Callable[[Session], int]


# --------------------------------------------------------------------------- #
# Readers. Raw SQL on purpose: `brands` is company scoped and the ORM scope filter would
# narrow the list to whoever happens to be asking; the prompt is one text for every turn.
# --------------------------------------------------------------------------- #


def _domain_names(db: Session) -> list[str]:
    return list(db.execute(sql("SELECT name FROM chatbot_domains ORDER BY sort_order, name")).scalars())


def _domain_words(db: Session) -> list[str]:
    """The curated DOMAIN IN MESSAGE list (`chatbot_domain_words`, owner answer 2). An
    install without that list falls back to every switch word and status word."""
    curated = list(db.execute(sql("SELECT word FROM chatbot_domain_words ORDER BY sort_order, word")).scalars())
    if curated:
        return _dedupe(curated)
    words: list[str] = []
    for row in db.execute(sql("SELECT switch_words FROM chatbot_domains ORDER BY sort_order, name")):
        words.extend(row[0] or [])
    for row in _status_rows(db):
        words.extend(row["trigger_words"] or [])
    return _dedupe(words)


def _status_rows(db: Session) -> list[dict]:
    return [
        dict(r)
        for r in db.execute(
            sql(
                "SELECT domain, value, label, trigger_words, prompt_lists FROM chatbot_status_words "
                "ORDER BY sort_order, value"
            )
        ).mappings()
    ]


def _entity_kinds(db: Session) -> list[str]:
    return list(db.execute(sql("SELECT kind FROM chatbot_entity_kinds ORDER BY sort_order, kind")).scalars())


def _brands(db: Session) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    rows = db.execute(
        sql(
            "SELECT brand_name, brand_code FROM brands WHERE is_active "
            "ORDER BY brand_name, brand_code"
        )
    )
    for name, code in rows:
        name = (name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        code = (code or "").strip()
        out.append(f"{name} ({code})" if code else name)
    return out


def _teams(db: Session) -> list[str]:
    """The escalation lane's own team list. Not `agent_teams`: the lane matches the
    parser's team word only against `ESCALATION_TEAMS` (`lanes/escalation.py:1234-1261`),
    so a code from any other source is one it cannot route (owner, grill item 2, 30 Sep
    2026). Next lane: move that list into the `agent_teams` registry."""
    from app.modules.chatbot.lane_vocabulary import escalation_teams

    return list(escalation_teams())


def _agents(db: Session) -> list[str]:
    from app.modules.chatbot.lane_vocabulary import suggested_agents

    known = list(suggested_agents())
    # The agents the owner flagged for the prompt (owner Q-B, 2 Oct 2026); an install with
    # none flagged falls back to the code list.
    codes = list(
        db.execute(sql("SELECT code FROM access_agents WHERE is_active AND in_parser_prompt")).scalars()
    )
    if not codes:
        return known
    order = {code: i for i, code in enumerate(known)}
    return sorted(codes, key=lambda c: (order.get(c, len(order)), c))


def _access_levels(db: Session) -> list[str]:
    return list(
        db.execute(
            sql(
                "SELECT name FROM contact_access_types WHERE is_active "
                "ORDER BY sort_order NULLS LAST, name"
            )
        ).scalars()
    )


_LINE_BREAKS = _re_mod.compile(r"[\r\n\u2028\u2029\x85\x0b\x0c]+")


def _one_line(value: object) -> str:
    """Registry text as ONE line of prompt: no line break of any kind, no policy-block
    marker, no `{{token}}` braces, no control characters (security review M2). Rows
    written through the Chatbot pages are already cleaned on save; the spec registry,
    brands and access types are written elsewhere and reach every turn live."""
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END

    text = _LINE_BREAKS.sub(" ", str(value or ""))
    text = text.replace("\t", " ").replace("\u00a0", " ")
    text = "".join(ch for ch in text if ch.isprintable() or ch == " ")
    # Collapse whitespace BEFORE removing the markers: removing first let a doubled space
    # inside a marker survive the replace and collapse back into the real marker
    # (security pass 2, F1). Repeat until stable: removing one marker can join the halves
    # of another.
    text = " ".join(text.split())
    while True:
        before = text
        for marker in (BLOCKS_BEGIN, BLOCKS_END, "{{", "}}"):
            text = text.replace(marker, " ")
        text = " ".join(text.split())
        if text == before:
            return text


def _quoted(value: object) -> str:
    """A word inside the prompt's own double quotes: a quote in it becomes an apostrophe."""
    return '"' + _one_line(value).replace('"', "'") + '"'


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = (v or "").strip()
        if key and key.lower() not in seen:
            seen.add(key.lower())
            out.append(key)
    return out


# --------------------------------------------------------------------------- #
# Renderers: one text shape per variable, the shape the list had in the wording.
# --------------------------------------------------------------------------- #


# The owner's production text (1 Oct 2026) wraps its hand lists at 89 columns: the status
# words and the domain words reproduce it byte for byte with a greedy wrap at this width.
WRAP_WIDTH = 89


def _wrap(tokens: list[str], *, indent: str = "") -> str:
    """Greedy wrap: tokens joined by one space, a new line (with `indent`) before a token
    that would pass `WRAP_WIDTH`. A token is never split."""
    lines: list[str] = []
    current = ""
    for token in tokens:
        if current and len(current) + 1 + len(token) > WRAP_WIDTH:
            lines.append(current)
            current = indent + token
        else:
            current = f"{current} {token}" if current else token
    if current:
        lines.append(current)
    return "\n".join(lines)


def _status_line(row: dict, pad: int = 0) -> str:
    """One status, the owner's layout: the quoted values padded so the arrows line up,
    the words wrapped at `WRAP_WIDTH` with a 4-space continuation."""
    words = [_quoted(w) for w in row["trigger_words"] or []]
    head = f'  - {_quoted(row["value"]).ljust(pad)} -> {_one_line(row["label"])}'
    tail = [f'Domain {_quoted(row["domain"])}.'] if row["domain"] != "order" else []
    if not words:
        return _wrap([head + "."] + tail, indent="    ")
    tokens = [head + ":"] + [w + "," for w in words[:-1]] + [words[-1] + "."]
    if tail:
        tokens += ["Domain", f"{_quoted(row['domain'])}."]
    return _wrap(tokens, indent="    ")


def _listed(db: Session, prompt_list: str) -> list[dict]:
    """The status rows tagged for one parser prompt list (owner answer 4, 2 Oct 2026)."""
    return [r for r in _status_rows(db) if prompt_list in (r.get("prompt_lists") or [])]


def render_statuses(db: Session) -> str:
    rows = _listed(db, "statuses")
    pad = max((len(_quoted(r["value"])) for r in rows), default=0)
    return "\n".join(_status_line(row, pad) for row in rows)


def render_domain_words(db: Session) -> str:
    words = [_one_line(w) for w in _domain_words(db)]
    return _wrap([w + "," for w in words[:-1]] + words[-1:])


def _render_domains_detail(db: Session) -> str:
    from app.services.chatbot_parser_prompt import domain_line

    rows = db.execute(
        sql(
            "SELECT name, label, intents, switch_words, narrowing, takes_date_filter, "
            "escalation_team_code FROM chatbot_domains ORDER BY sort_order, name"
        )
    ).mappings()
    return "\n".join(_one_line(domain_line(r)) for r in rows)


def _render_entity_kinds_detail(db: Session) -> str:
    rows = db.execute(
        sql(
            "SELECT kind, resolver_source, did_you_mean, default_narrowing "
            "FROM chatbot_entity_kinds ORDER BY kind"
        )
    ).mappings()
    return "\n".join(
        _one_line(f'Entity kind {r["kind"]}: resolver {r["resolver_source"]}. '
        f'Did-you-mean {"on" if r["did_you_mean"] else "off"}. '
        f'Default narrowing {r["default_narrowing"]}.')
        for r in rows
    )


def _render_specs(db: Session) -> str:
    from app.services.chatbot_parser_prompt import specification_lines

    return "\n".join(_one_line(line) for line in specification_lines(db))


def _count(query: str) -> Callable[[Session], int]:
    return lambda db: int(db.execute(sql(query)).scalar() or 0)


_DOMAINS_HREF = "/system-management/chatbot-domains"
_STATUS_HREF = "/system-management/chatbot-status-words"

VARIABLES: dict[str, RegistryVariable] = {
    v.name: v
    for v in (
        RegistryVariable(
            "domains", "Domains", "Chatbot Domains", _DOMAINS_HREF, ("chatbot_domains",),
            lambda db: " | ".join(_one_line(n) for n in _domain_names(db)), lambda db: len(_domain_names(db)),
        ),
        RegistryVariable(
            # The owner's curated list (answer 2); no admin page edits it yet (trigger: the
            # owner asks to edit the list in the UI). Reviewer pass 3, S2.
            "domain_words", "Domain words", "Domain words (no page yet)", "",
            ("chatbot_domains", "chatbot_status_words", "chatbot_domain_words"),
            render_domain_words, lambda db: len(_domain_words(db)),
        ),
        RegistryVariable(
            "domains_detail", "Domains - detail", "Chatbot Domains", _DOMAINS_HREF,
            ("chatbot_domains",), _render_domains_detail, lambda db: len(_domain_names(db)),
        ),
        RegistryVariable(
            "statuses", "Status words", "Chatbot Status Words", _STATUS_HREF,
            ("chatbot_status_words",), render_statuses, lambda db: len(_listed(db, "statuses")),
        ),
        RegistryVariable(
            "status_values", "Status values", "Chatbot Status Words", _STATUS_HREF,
            ("chatbot_status_words",),
            lambda db: "|".join(_one_line(r["value"]) for r in _listed(db, "status_values")),
            lambda db: len(_listed(db, "status_values")),
        ),
        RegistryVariable(
            # The `status` field's values, line 778 of the owner's text (owner answer 4).
            "status_field_values", "Status values - status field", "Chatbot Status Words", _STATUS_HREF,
            ("chatbot_status_words",),
            lambda db: "|".join(_one_line(r["value"]) for r in _listed(db, "status_field_values")),
            lambda db: len(_listed(db, "status_field_values")),
        ),
        RegistryVariable(
            # The owner's "full set" line lists only the order-domain statuses (crew Q5,
            # 1 Oct 2026).
            "order_status_values", "Status values - order domain", "Chatbot Status Words", _STATUS_HREF,
            ("chatbot_status_words",),
            lambda db: "|".join(_one_line(r["value"]) for r in _status_rows(db) if r["domain"] == "order"),
            lambda db: sum(1 for r in _status_rows(db) if r["domain"] == "order"),
        ),
        RegistryVariable(
            "entity_kinds", "Entity kinds", "Chatbot Entity Kinds", "/system-management/chatbot-entity-kinds",
            ("chatbot_entity_kinds",), lambda db: "|".join(_one_line(k) for k in _entity_kinds(db)),
            lambda db: len(_entity_kinds(db)),
        ),
        RegistryVariable(
            "entity_kinds_detail", "Entity kinds - detail", "Chatbot Entity Kinds",
            "/system-management/chatbot-entity-kinds", ("chatbot_entity_kinds",),
            _render_entity_kinds_detail, lambda db: len(_entity_kinds(db)),
        ),
        RegistryVariable(
            "specs", "Specifications", "Product Specifications", "/master-data-management/product-specifications",
            ("product_spec_registry",), _render_specs,
            _count("SELECT count(*) FROM product_spec_registry WHERE is_active"),
        ),
        RegistryVariable(
            "brands", "Brands", "Brands", "/master-data-management/brands", ("brands",),
            lambda db: ", ".join(_one_line(b) for b in _brands(db)), lambda db: len(_brands(db)),
        ),
        RegistryVariable(
            # No admin page edits this list yet (next lane: move it into `agent_teams`).
            "teams", "Teams", "Escalation lane (code list)", "",
            (), lambda db: "|".join(_teams(db)), lambda db: len(_teams(db)),
        ),
        RegistryVariable(
            "agents", "Agents", "Agents and Teams", "/user-management/access-agents", ("access_agents",),
            lambda db: "|".join(_one_line(a) for a in _agents(db)), lambda db: len(_agents(db)),
        ),
        RegistryVariable(
            "access_levels", "Access levels", "Contact Access Types",
            "/user-management/contact-access-types", ("contact_access_types",),
            lambda db: json.dumps([_one_line(a) for a in _access_levels(db)], ensure_ascii=False, separators=(",", ":")),
            lambda db: len(_access_levels(db)),
        ),
    )
}

VARIABLE_NAMES: tuple[str, ...] = tuple(VARIABLES)

REGISTRY_TABLES: frozenset[str] = frozenset(t for v in VARIABLES.values() for t in v.tables)


# --------------------------------------------------------------------------- #
# Cache + invalidation (D5)
# --------------------------------------------------------------------------- #

_CACHE: dict[str, tuple[float, str]] = {}
# The last value each variable rendered successfully. Survives `clear_cache`: when a
# reader fails, the turn gets the list as it last stood rather than an empty one.
_LAST_GOOD: dict[str, str] = {}
# Bumped by every `clear_cache`. A render that started before a registry commit read the
# old rows, so it must not be stored over the commit's invalidation.
_GENERATION = 0
_LOCK = threading.Lock()

logger = logging.getLogger(__name__)


def clear_cache() -> None:
    global _GENERATION
    with _LOCK:
        _CACHE.clear()
        _GENERATION += 1


def render_value(db: Session, name: str) -> str:
    """One variable's current text. Cached; a registry commit clears the cache."""
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(name)
        generation = _GENERATION
    if hit is not None and hit[0] > now:
        return hit[1]
    value = VARIABLES[name].render(db)
    with _LOCK:
        _LAST_GOOD[name] = value
        if generation == _GENERATION:
            _CACHE[name] = (now + CACHE_TTL_SECONDS, value)
    return value


def render_values(db: Session, names: set[str] | list[str] | None = None) -> dict[str, str]:
    wanted = VARIABLE_NAMES if names is None else [n for n in VARIABLE_NAMES if n in set(names)]
    return {name: render_value(db, name) for name in wanted}


def render_values_safe(db: Session, names: set[str] | list[str]) -> dict[str, str]:
    """`render_values` for a live turn: never raises, never leaves a token unfilled. Each
    variable renders in its own savepoint, so one failing reader neither blanks the others
    nor aborts the turn's transaction; a failed one falls back to its last good value,
    and to "" only when it never rendered in this process."""
    out: dict[str, str] = {}
    for name in [n for n in VARIABLE_NAMES if n in set(names)]:
        with _LOCK:
            hit = _CACHE.get(name)
        if hit is not None and hit[0] > time.monotonic():
            out[name] = hit[1]  # a cache hit needs no savepoint (no SQL runs)
            continue
        try:
            with db.begin_nested():
                out[name] = render_value(db, name)
        except Exception:
            logger.warning("registry variable %s failed; using its last good value", name, exc_info=True)
            with _LOCK:
                out[name] = _LAST_GOOD.get(name, "")
    return out


_DIRTY_FLAG = "chatbot_prompt_vars_dirty"


def _touches_registry(session: Session) -> bool:
    for obj in (*session.new, *session.dirty, *session.deleted):
        table = getattr(getattr(obj, "__table__", None), "name", None)
        if table in REGISTRY_TABLES:
            return True
    return False


@event.listens_for(Session, "after_flush")
def _mark_dirty(session: Session, _ctx) -> None:
    if _touches_registry(session):
        session.info[_DIRTY_FLAG] = True


@event.listens_for(Session, "after_commit")
def _clear_on_commit(session: Session) -> None:
    if session.info.pop(_DIRTY_FLAG, False):
        clear_cache()


@event.listens_for(Session, "after_rollback")
def _forget_on_rollback(session: Session) -> None:
    session.info.pop(_DIRTY_FLAG, None)


# --------------------------------------------------------------------------- #
# The editor's "Wired to this agent" panel (R5a)
# --------------------------------------------------------------------------- #


def _last_changed(db: Session, tables: tuple[str, ...]) -> datetime | None:
    latest: datetime | None = None
    for table in tables:
        has_col = db.execute(
            sql(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name = 'updated_at'"
            ),
            {"t": table},
        ).first()
        if not has_col:
            continue
        value = db.execute(sql(f"SELECT max(updated_at) FROM {table}")).scalar()
        if value is not None and (latest is None or value > latest):
            latest = value
    return latest


def describe(db: Session, template: str | None = None) -> list[dict]:
    """Every variable with its source, count, last change, rendered text, and whether
    `template` uses it. Uncached reads: this backs an admin screen, not a turn."""
    from app.services.ai_prompt_registry import extract_tokens

    used = extract_tokens(template or "")
    return [
        {
            "name": v.name,
            "label": v.label,
            "source": v.source,
            "href": v.href,
            "count": v.count(db),
            "last_changed": _last_changed(db, v.tables),
            "rendered": v.render(db),
            "used": v.name in used,
        }
        for v in VARIABLES.values()
    ]


# --------------------------------------------------------------------------- #
# The wording layer (D7): a template with its hand-copied registry lists replaced by
# their tokens. Used once by migration `pdyn_0002_wording_layer` on the production text
# (owner edits included) and by the code fallback, never on a save: after this, the
# owner's text is only ever what he typed.
#
# Each list is replaced only when its registry renders AT LEAST the values the hand list
# carried, so the rendered prompt loses nothing (R6). A list the registry does not cover
# stays literal and is reported, and a list the owner already reworded is not found and
# stays untouched.
# --------------------------------------------------------------------------- #

import re as _re

_SEP_PIPE = _re.compile(r"\s*\|\s*")


def _pipe_values(raw: str) -> list[str]:
    return [v for v in _SEP_PIPE.split(raw.strip()) if v]


def _covers(rendered: list[str], literal: list[str]) -> bool:
    have = {v.strip().lower() for v in rendered}
    return all(v.strip().lower() in have for v in literal)


def wording_layer(template: str, db: Session) -> tuple[str, list[str]]:
    """`(new_template, report)`. `report` has one line per list: replaced or kept, why."""
    report: list[str] = []
    text = template

    def pipe_rule(label: str, pattern: str, token: str, values: list[str]) -> None:
        nonlocal text
        m = _re.search(pattern, text)
        if m is None:
            report.append(f"{label}: not found (reworded or absent), kept")
            return
        literal = _pipe_values(m.group("list"))
        if not _covers(values, literal):
            missing = [v for v in literal if v.lower() not in {x.lower() for x in values}]
            report.append(f"{label}: registry lacks {missing}, kept literal")
            return
        text = text[: m.start("list")] + "{{" + token + "}}" + text[m.end("list") :]
        report.append(f"{label}: -> {{{{{token}}}}}")

    domains = _domain_names(db)
    # Each pipe list goes to the variable that renders that line's OWN list (reviewer pass
    # 3, S1): the variables render tagged or domain-filtered rows since the owner's answer 4.
    def values_of(variable: str) -> list[str]:
        return _pipe_values(render_value(db, variable))

    pipe_rule(
        "domain_hint list",
        r"domain_hint = ONE of: (?P<list>[a-z_]+(?:\s*\|\s*[a-z_]+)+?)\s*\|\s*null",
        "domains", domains,
    )
    pipe_rule(
        "OUTPUT order_status",
        r'"order_status": "(?P<list>[a-z_]+(?:\|[a-z_]+)*?)\|null',
        "status_values", values_of("status_values"),
    )
    pipe_rule(
        "OUTPUT status",
        r'"status": "(?P<list>[a-z_]+(?:\|[a-z_]+)*?)\|null',
        "status_field_values", values_of("status_field_values"),
    )
    pipe_rule(
        "order_status full set",
        r"The full set is now: (?P<list>[a-z_]+(?:\|[a-z_]+)*?)\|null",
        "order_status_values", values_of("order_status_values"),
    )
    pipe_rule(
        "OUTPUT suggested_team",
        r'"suggested_team": "(?P<list>[a-z_]+(?:\|[a-z_]+)+)"',
        "teams", _teams(db),
    )
    pipe_rule(
        "OUTPUT suggested_agent",
        r'"suggested_agent": "(?P<list>[a-z_]+(?:\|[a-z_]+)+)"',
        "agents", _agents(db),
    )
    pipe_rule(
        "entity hint list",
        r'"hint": "(?P<list>[a-z_]+(?:\|[a-z_]+){3,})"',
        "entity_kinds", _entity_kinds(db),
    )

    # ACCESS LEVELS: the JSON list the section draws from.
    m = _re.search(r"drawn\s+ONLY from:\s*\n(?P<list>\[\"[^\]\n]*\"\])", text)
    if m is None:
        report.append("access levels: not found (reworded or absent), kept")
    else:
        literal = json.loads(m.group("list"))
        if _covers(_access_levels(db), literal):
            text = text[: m.start("list")] + "{{access_levels}}" + text[m.end("list") :]
            report.append("access levels: -> {{access_levels}}")
        else:
            report.append(
                f"access levels: registry lacks {[v for v in literal if v not in _access_levels(db)]}, kept literal"
            )

    # ORDER_STATUS FILTER: the status bullets up to the `null -> DEFAULT` bullet.
    m = _re.search(
        r"(?P<list>  - \"outstanding\" -> .*?)(?=  - null -> DEFAULT)", text, flags=_re.S
    )
    if m is None:
        report.append("status bullets: not found (reworded or absent), kept")
    else:
        literal = _re.findall(r'^  - "([a-z_]+)"', m.group("list"), flags=_re.M)
        rows = {r["value"]: r for r in _listed(db, "statuses")}
        # Swapped only when the hand bullets say EXACTLY what the rows render (whitespace
        # aside): any word of the owner's own inside a bullet keeps the block literal
        # (review S5), because `{{statuses}}` renders only what the rows hold.
        expected = "\n".join(_status_line(rows[v]) for v in literal if v in rows)
        same = all(v in rows for v in literal) and " ".join(m.group("list").split()) == " ".join(expected.split())
        if same:
            text = text[: m.start("list")] + "{{statuses}}\n" + text[m.end("list") :]
            report.append("status bullets: -> {{statuses}}")
        else:
            report.append("status bullets: differ from what the registry renders (owner wording or a missing row), kept literal")

    # DOMAIN IN MESSAGE: the comma list between "STATUS word -" and "- in any language".
    m = _re.search(
        r"STATUS word -\s*\n?(?P<list>[^\n].*?)\s+- in any language", text, flags=_re.S
    )
    if m is None:
        report.append("domain words: not found (reworded or absent), kept")
    else:
        literal = [w.strip() for w in _re.split(r",\s*", m.group("list")) if w.strip()]
        covered = {w.lower() for w in _domain_words(db)}
        leftover = [w for w in literal if w.lower() not in covered]
        replacement = "{{domain_words}}" + (f", {', '.join(leftover)}" if leftover else "")
        text = text[: m.start("list")] + replacement + text[m.end("list") :]
        report.append(
            "domain words: -> {{domain_words}}"
            + (f" (kept the words no registry holds: {', '.join(leftover)})" if leftover else "")
        )

    # The publish-time policy blocks become their live variables.
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END

    if BLOCKS_BEGIN in text and BLOCKS_END in text:
        head, rest = text.split(BLOCKS_BEGIN, 1)
        _old, tail = rest.split(BLOCKS_END, 1)
        text = (
            f"{head}{BLOCKS_BEGIN}\n{{{{domains_detail}}}}\n\n{{{{entity_kinds_detail}}}}\n\n"
            f"{{{{specs}}}}\n{BLOCKS_END}{tail}"
        )
        report.append("policy blocks: -> {{domains_detail}}, {{entity_kinds_detail}}, {{specs}}")
    else:
        report.append("policy blocks: no markers, none added")
    return text, report


def with_account_block(text: str) -> str:
    """`text` carrying ACCOUNT_LEDGER_ADDENDUM (#1432, the `account` entity key), so a
    variable version promoted from the owner's text never drops it. The owner's text of
    1 Oct 2026 predates the block: it goes in once, just before the policy blocks (at the
    end when there are none). A text that already has the block, owner-edited or not, is
    returned as it is."""
    from app.services.chatbot_parser_prompt import ACCOUNT_LEDGER_ADDENDUM, BLOCKS_BEGIN

    if "\nCUSTOMER ACCOUNT NUMBER\n" in text:
        return text
    block = ACCOUNT_LEDGER_ADDENDUM.strip("\n")
    if BLOCKS_BEGIN in text:
        head, rest = text.split(BLOCKS_BEGIN, 1)
        return f"{head}{block}\n\n{BLOCKS_BEGIN}{rest}"
    return f"{text.rstrip(chr(10))}\n\n{block}\n"


# --------------------------------------------------------------------------- #
# Publishing after the wording layer exists (R1: republishing never overwrites it).
# --------------------------------------------------------------------------- #

_TOKEN_RE = _re.compile(r"\{\{\s*(" + "|".join(VARIABLE_NAMES) + r")\s*\}\}")


def is_wording_layer(template: str | None) -> bool:
    """A template that carries at least one registry variable."""
    return bool(_TOKEN_RE.search(template or ""))


def wording_layer_exists(session: Session) -> bool:
    """True once any `chatbot_semantic_parser` version carries a registry variable. From
    then on the owner's text is the source of the wording, and every publish path that
    rebuilds a version from the code constant (`chatbot_rearch_s4.publish_policy_blocks`,
    `chatbot_rearch_s12.republish_and_promote`) stands down."""
    rows = session.execute(
        sql("SELECT template FROM ai_prompt_versions WHERE name = :n"), {"n": PROMPT_KEY}
    ).scalars()
    return any(is_wording_layer(t) for t in rows)


def publish_wording_edit(session: Session, *, old: str, new: str, message: str) -> int | None:
    """The way a later migration changes the parser's wording: one exact edit applied to
    the PRODUCTION text (owner edits and variables kept), published as a new UNLABELLED
    version. Returns the version number, or None when `old` is not in the production text
    (already applied, or reworded by the owner) - reported, never forced. The label
    moves only when the owner moves it (R3)."""
    import uuid

    row = session.execute(
        sql(
            "SELECT v.template, v.variables FROM ai_prompt_versions v "
            "JOIN ai_prompt_labels l ON l.version_id = v.id "
            "WHERE l.name = :n AND l.label = 'production'"
        ),
        {"n": PROMPT_KEY},
    ).first()
    if row is None or old not in row[0]:
        return None
    from app.models.ai_prompt import AIPromptVersion

    top = session.execute(
        sql("SELECT coalesce(max(version), 0) FROM ai_prompt_versions WHERE name = :n"),
        {"n": PROMPT_KEY},
    ).scalar()
    version = int(top) + 1
    session.add(
        AIPromptVersion(
            name=PROMPT_KEY,
            version=version,
            type="text",
            template=row[0].replace(old, new, 1),
            variables=list(row[1] or []),
            config_json={"wording_edit": True},
            commit_message=message,
        )
    )
    session.flush()
    return version


# Literal registry lists the drift test (R4) refuses in any version published after the
# wording layer: the shapes the hand lists had.
LITERAL_LIST_PATTERNS: dict[str, str] = {
    "domains": r"domain_hint = ONE of: [a-z_]+ \| [a-z_]+ \| [a-z_]+",
    "status_values": r'"(?:order_)?status": "outstanding\|delivered',
    "teams": r'"suggested_team": "[a-z_]+\|[a-z_]+',
    "agents": r'"suggested_agent": "[a-z_]+\|[a-z_]+',
    "entity_kinds": r'"hint": "product\|[a-z_]+\|[a-z_]+',
    "access_levels": r'drawn\s+ONLY from:\s*\n\["',
    "statuses": r'  - "outstanding" -> orders NOT yet delivered',
}


def literal_lists(template: str) -> list[str]:
    """Names of the registry lists `template` carries as literal text."""
    return [name for name, pattern in LITERAL_LIST_PATTERNS.items() if _re.search(pattern, template or "")]


# --------------------------------------------------------------------------- #
# The rendered-IDENTICAL wording layer (owner hand test #1405, item 3, 1 Oct 2026)
#
# Stricter than `wording_layer`: a hard-coded list becomes its `{{variable}}` only when
# the registry renders EXACTLY the same text today, so the prompt the model receives does
# not change by a single byte. Every list whose registry differs stays literal and is
# reported with what is only in the text and what is only in the registry, so the owner
# decides each one instead of the wording changing silently.
# --------------------------------------------------------------------------- #


def _items(variable: str, text_value: str) -> list[str]:
    """A list's items, for the difference report."""
    raw = (text_value or "").strip()
    if variable in ("domains",):
        return [v for v in _re.split(r"\s*\|\s*", raw) if v]
    if variable in ("status_values", "order_status_values", "status_field_values", "teams", "agents", "entity_kinds"):
        return [v for v in raw.split("|") if v]
    if variable == "access_levels":
        try:
            return [str(v) for v in json.loads(raw)]
        except ValueError:
            return [raw]
    if variable == "domain_words":
        return [w.strip() for w in _re.split(r",\s*", raw.replace("\n", " ")) if w.strip()]
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _first_item_difference(variable: str, literal: str, rendered: str) -> dict:
    """The first item (1-based) where the hand list and the registry part; `<end>` when one
    side runs out first."""
    a, b = _items(variable, literal), _items(variable, rendered)
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return {"item": i + 1, "text": x, "registry": y}
    i = min(len(a), len(b))
    return {
        "item": i + 1,
        "text": a[i] if len(a) > i else "<end>",
        "registry": b[i] if len(b) > i else "<end>",
    }


_IDENTICAL_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("domains", r"domain_hint = ONE of: (?P<list>[a-z_]+(?: \| [a-z_]+)+) \| null"),
    ("status_values", r'"order_status": "(?P<list>[a-z_]+(?:\|[a-z_]+)*)\|null'),
    ("status_field_values", r'"status": "(?P<list>[a-z_]+(?:\|[a-z_]+)*)\|null'),
    ("order_status_values", r"The full set is now: (?P<list>[a-z_]+(?:\|[a-z_]+)*)\|null"),
    ("teams", r'"suggested_team": "(?P<list>[a-z_]+(?:\|[a-z_]+)+)"'),
    ("agents", r'"suggested_agent": "(?P<list>[a-z_]+(?:\|[a-z_]+)+)"'),
    ("entity_kinds", r'"hint": "(?P<list>[a-z_]+(?:\|[a-z_]+){3,})"'),
    ("access_levels", r"drawn\s+ONLY from:\s*\n(?P<list>\[\"[^\]\n]*\"\])"),
    ("statuses", r'(?P<list>  - "outstanding" -> .*?)\n(?=  - null -> DEFAULT)'),
    ("domain_words", r"STATUS word -\s*\n?(?P<list>[^\n].*?)\s+- in any language"),
)


def identical_wording_layer(template: str, db: Session) -> tuple[str, list[dict]]:
    """`(new_template, report)`: each candidate list replaced by its variable only where the
    registry renders exactly that text. Report rows: variable, line (1-based in the
    source), action (`replaced` | `kept literal` | `not found`), and for a kept list the
    items only in the text and only in the registry."""
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END

    report: list[dict] = []
    text = template
    seen_spans: set[tuple[int, int]] = set()

    def record(variable: str, m, literal: str, rendered: str) -> dict:
        line = text[: m.start("list")].count("\n") + 1 if m else None
        row = {"variable": variable, "line": line}
        if m is None:
            row["action"] = "not found"
        elif literal == rendered:
            row["action"] = "replaced"
        else:
            a, b = _items(variable, literal), _items(variable, rendered)
            row.update(
                action="kept literal",
                only_in_text=[x for x in a if x not in b],
                only_in_registry=[x for x in b if x not in a],
                order_or_format_only=sorted(a) == sorted(b),
                first_difference=_first_item_difference(variable, literal, rendered),
            )
        report.append(row)
        return row

    for variable, pattern in _IDENTICAL_CANDIDATES:
        rendered = render_value(db, variable)
        # Each pattern may match in several places (status_values does); handle each once.
        m = None
        for found in _re.finditer(pattern, text, flags=_re.S):
            span = (found.start("list"), found.end("list"))
            if span in seen_spans:
                continue
            m = found
            break
        if m is None:
            record(variable, None, "", rendered)
            continue
        literal = m.group("list")
        row = record(variable, m, literal, rendered)
        if row["action"] == "replaced":
            token = "{{" + variable + "}}"
            text = text[: m.start("list")] + token + text[m.end("list") :]
            seen_spans.add((m.start("list"), m.start("list") + len(token)))
        else:
            seen_spans.add((m.start("list"), m.end("list")))

    # The publish-time policy blocks: each paragraph replaced only when it is exactly what
    # its variable renders today.
    if BLOCKS_BEGIN in text and BLOCKS_END in text:
        head, rest = text.split(BLOCKS_BEGIN, 1)
        block, tail = rest.split(BLOCKS_END, 1)
        parts = block.strip("\n").split("\n\n")
        names = ["domains_detail", "entity_kinds_detail", "specs"]
        line0 = template.split(BLOCKS_BEGIN, 1)[0].count("\n") + 2
        for i, part in enumerate(parts[: len(names)]):
            variable = names[i]
            rendered = render_value(db, variable)
            row = {"variable": variable, "line": line0}
            if part == rendered:
                parts[i] = "{{" + variable + "}}"
                row["action"] = "replaced"
            else:
                a, b = _items(variable, part), _items(variable, rendered)
                row.update(
                    action="kept literal",
                    only_in_text=[x for x in a if x not in b],
                    only_in_registry=[x for x in b if x not in a],
                    order_or_format_only=sorted(a) == sorted(b),
                    first_difference=_first_item_difference(variable, part, rendered),
                )
            report.append(row)
            line0 += part.count("\n") + 2
        lead = block[: len(block) - len(block.lstrip("\n"))]
        trail = block[len(block.rstrip("\n")) :]
        joined = "\n\n".join(parts)
        text = f"{head}{BLOCKS_BEGIN}{lead}{joined}{trail}{BLOCKS_END}{tail}"
    return text, report
