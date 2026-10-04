"""The runtime seams stages C to G are handed: state in, resolver, tool runner, state out.

`turn/` is pure by contract (AC-1520 - no I/O, no text, no lane imports), so everything
that has to touch a database, an MCP tool or the kept `lanes/business` machinery lives
here instead, one function per seam:

* `load_state` / `turn_number` - stage A's read of the three shelves (focus + pending
  from the five session keys, profile from `respond_contacts.chatbot_profile`).
* `resolve_kinds` - the resolver seam reconciliation needs (AC-1527). It returns the
  plain `{raw: {kind: hits}}` dict `turn/reconcile.py` already takes, plus the gate's
  compatible entities for the fetch below.
* `tool_runner` - what `turn/fetch.py::run_fetch` calls once per `FetchSpec`. It is the
  KEPT lane: `lanes/business.run_fetch` picks the tool, calls it and structures the rows,
  and this wraps that output as the `{domain, denied, entities, figures, files, miss}`
  envelope the composer reads.
* `lane_parse_output` - the `ctx.parse.output` the kept lanes read, projected from the v3
  verdict: the same keys they have always read, plus the two the re-architecture renamed
  (`document` / `status` are projected back onto `order_status` for the order tools) and
  the offered team an acceptance inherits from the open question rather than from a
  post-processed routing chain.
"""
from __future__ import annotations

import copy
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.services.chatbot import jsc
from app.services.chatbot.contracts import (
    DEFAULT_SUGGESTED_AGENT,
    DEFAULT_SUGGESTED_TEAM,
    SALES_REPORT_GROUP_BYS,
    named_count,
)
from app.services.chatbot.turn.apply import names_its_own_ask
from app.services.chatbot.turn.decide import picked_positions
from app.services.chatbot.turn.pending import (
    OFFER_KINDS,
    Pending,
    from_wire,
    offered_companies,
    tick as tick_pending,
)
from app.services.chatbot.turn.plan import FetchSpec
from app.services.chatbot.turn import policy_rows
from app.services.chatbot.turn.reconcile import hits_for_token
from app.services.chatbot.turn.state import (
    EXTRA_KIND_ALIASES,
    KIND_FIELD_MAP,
    Focus,
    Profile,
    State,
    focus_from_wire,
    token_key,
)
from app.services.chatbot import session_state

logger = logging.getLogger(__name__)

# The inverse of `conversation_variables_service`'s read-time migration: the order tools
# still take one `order_status` bucket, and `focus.document` + `focus.status` is what the
# turn now carries. Projected at THIS seam only - nothing persists a bucket again.
_DOCUMENT_STATUS_TO_ORDER_STATUS: dict[tuple[str, str], str] = {
    ("", "outstanding"): "outstanding",
    ("DO", "outstanding"): "do_outstanding",
    ("SO", "outstanding"): "so_outstanding",
    ("DO,SO", "outstanding"): "outstanding_both",
}


@dataclass
class TurnContext:
    """The duck-typed bag `run_fetch`, `compose` and `tail.persist` read.

    Deliberately NOT a class any of them imports: each reads the attributes it needs by
    name (`ctx.tool_runner`, `ctx.trace`, `ctx.db`), which is what lets a test hand any
    of them a `SimpleNamespace` instead.
    """

    db: Any = None
    contact_respond_id: str | None = None
    trace: Any = None
    policy: Any = None
    profile: Profile = field(default_factory=Profile)
    tool_runner: Callable[[str, FetchSpec], dict[str, Any]] | None = None
    # The field-reveal keys this contact holds (`ctx["access"]["attributes"]`, contract
    # 59). `None` is the empty grant set, as it is everywhere else that reads it. The
    # cross-domain ladder is the reader: a rung the contact was never granted is not
    # probed at all (owner ruling, 8 Sep 2026).
    granted_reveals: list[str] | None = None
    access_levels: list[str] = field(default_factory=list)
    contains_flyer: bool = False
    ideation: Any = None
    # SRTSC07 (prod transcript, 22 Sep 2026): this turn's own `routing.suggested_agent`,
    # read by `turn/compose.py::compose` so a freshly minted `team_pick` offer can carry
    # the agent half beside the team it already carries - `answer_bridge`'s own mint
    # sites read it straight off `parser["routing"]`, which needs no ctx field of its
    # own, so this is the ONE mint site that had nowhere else to read it from.
    suggested_agent: str | None = None
    # Round 4 (owner-approved, 22 Sep 2026): the SAME reasoning, one axis over - this
    # turn's own resolved brand (`lanes/business/gate.py`'s `routing_brand`), read by
    # `turn/compose.py::compose` so a freshly minted `team_pick`/roster re-arm can
    # stamp `payload["brand_code"]` beside the agent it already stamps.
    routing_brand: str | None = None
    # #865 (fix round 2, N2): the focus product's brand, as a thunk, for a turn whose
    # resolver never ran (a SETTLED focus product is not re-resolved). A thunk so the
    # products x brands read runs only when compose actually mints a `team_pick`; read
    # through `turn/compose.py::_routing_brand`, never directly.
    focus_brand: Callable[[], str | None] | None = None


# --------------------------------------------------------------------------- #
# Stage A: the three shelves
# --------------------------------------------------------------------------- #


def turn_number(db: Session, contact_respond_id: str) -> int:
    """How many turns this contact has had, this one included (stamps `asked_at_turn`).

    Uses the ORM, not raw SQL, for the same reason `previous_reply_text` does: the table
    is `chatbot.turns`, and only ORM constructs go through the test fixture's
    `schema_translate_map`. A literal `chatbot.turns` in a text() query reads the REAL
    schema under pytest - which the blank-schema fixture never writes to - so this
    counted 0 on every turn and every `asked_at_turn` stamp was 1.
    """
    from app.models.chatbot_turn import ChatbotTurn

    try:
        count = (
            db.query(func.count(ChatbotTurn.id))
            .filter(ChatbotTurn.contact_respond_id == str(contact_respond_id))
            .scalar()
        )
    except Exception:  # noqa: BLE001 - a turn number nobody could read is 1, not a failure
        return 1
    return int(count or 0) or 1


def contact_phone(db: Session, contact_respond_id: str) -> str | None:
    """The contact's phone number off their own row.

    The webhook envelope carries respond.io's contact record, which normally holds it -
    but the retry and poller ingresses rebuild a leaner contact, and the escalation lane's
    round-robin draw REQUIRES a phone number (`post_next_assignee` 400s without one). The
    CRM already knows it, so the turn fills the gap rather than failing the handover.
    """
    try:
        row = db.execute(
            text("SELECT phone_number FROM respond_contacts WHERE respond_io_id = :cid"),
            {"cid": contact_respond_id},
        ).first()
    except Exception:  # noqa: BLE001 - an unknown contact is an unknown phone, not a failure
        return None
    return row[0] if row is not None else None


_PROFILE_COLUMNS = (
    "c.chatbot_profile, c.chatbot_stock_allowed, "
    "c.notify_salesman, c.packing_list_allowed, c.escalation_allowed "
    "FROM respond_contacts c"
)


def _fail_closed_profile() -> tuple[Profile, bool]:
    """What an AMBIGUOUS contact gets: no stock, no recall.

    The same `respond_io_id` can exist in two workspaces (which is why
    `field_access.resolve_contact_id` takes a `space_id` at all). Picking the first row
    would answer one person's stock question with another person's allowance and could
    recall another person's episodes. Two rows is a resolution failure, and a resolution
    failure denies.
    """
    return Profile(stock_allowed=False), False


def _limit(limit: int | None) -> str:
    return f" LIMIT {int(limit)}" if limit is not None else ""


def _profile_rows(
    db: Session, contact_respond_id: str, space_id: str | None, *, limit: int | None = 2
) -> list[Any]:
    """This contact's rows under `space_id`, falling back to the NULL-workspace ones.

    Mirrors `field_access.resolve_contact_with_null_workspace_fallback`, which is the
    resolution the rest of the chatbot already runs (`head/access.check_access`, the
    company scope) on top of `mcp_access_service.evaluate_agent`'s workspace join:
    resolve inside the workspace first, and only then consider the measured 16 contacts
    whose `workspace_id` is NULL and which no join can reach. A contact in a DIFFERENT,
    non-default workspace stays unresolved here on purpose, exactly as it does there.
    """
    if not space_id:
        return list(
            db.execute(
                text(f"SELECT {_PROFILE_COLUMNS} WHERE c.respond_io_id = :cid{_limit(limit)}"),
                {"cid": contact_respond_id},
            ).fetchall()
        )
    scoped = list(
        db.execute(
            text(
                f"SELECT {_PROFILE_COLUMNS} "
                "JOIN respond_workspaces w ON w.id = c.workspace_id "
                f"WHERE c.respond_io_id = :cid AND w.space_id = :space{_limit(limit)}"
            ),
            {"cid": contact_respond_id, "space": str(space_id)},
        ).fetchall()
    )
    if scoped:
        return scoped
    return list(
        db.execute(
            text(
                f"SELECT {_PROFILE_COLUMNS} "
                f"WHERE c.respond_io_id = :cid AND c.workspace_id IS NULL{_limit(limit)}"
            ),
            {"cid": contact_respond_id},
        ).fetchall()
    )


_MEMORY_COLUMNS = "c.id, c.chatbot_memory_level FROM respond_contacts c"


def resolve_contact_pk(
    db: Session, contact_respond_id: str, space_id: str | None
) -> tuple[str, str | None] | None:
    """The ONE `respond_contacts` row this `contact_respond_id` resolves to -
    workspace first, then the NULL-workspace fallback, exactly `_profile_rows`'s
    own two-tier lookup (chatbot memory lane A, security review 26 Sep 2026, B1).

    Returns `(id, chatbot_memory_level)`, or `None` when the id is ambiguous (more
    than one row) or absent. `None` is not "pick one" - every caller reads it as
    "cannot safely touch this contact's memory": no fact write, no frame read, the
    turn's own memory degrades to `off` rather than risking another workspace's
    namesake. Memory reads/writes are stricter than `load_profile`'s own stock/
    recall resolution (which still answers a single ambiguous match by denying
    just those two flags) because a wrong pick here would leak or corrupt a
    DIFFERENT contact's stored profile facts and episodes, not just this turn's
    business answer.
    """
    if not space_id:
        rows = list(
            db.execute(
                text(f"SELECT {_MEMORY_COLUMNS} WHERE c.respond_io_id = :cid LIMIT 2"),
                {"cid": contact_respond_id},
            ).fetchall()
        )
    else:
        scoped = list(
            db.execute(
                text(
                    f"SELECT {_MEMORY_COLUMNS} "
                    "JOIN respond_workspaces w ON w.id = c.workspace_id "
                    "WHERE c.respond_io_id = :cid AND w.space_id = :space LIMIT 2"
                ),
                {"cid": contact_respond_id, "space": str(space_id)},
            ).fetchall()
        )
        rows = scoped or list(
            db.execute(
                text(
                    f"SELECT {_MEMORY_COLUMNS} "
                    "WHERE c.respond_io_id = :cid AND c.workspace_id IS NULL LIMIT 2"
                ),
                {"cid": contact_respond_id},
            ).fetchall()
        )
    if len(rows) != 1:
        if len(rows) > 1:
            logger.warning(
                "chatbot: respond_io_id %s matches %s contacts; memory degraded to "
                "off for this turn rather than picking one",
                contact_respond_id,
                len(rows),
            )
        return None
    return str(rows[0][0]), rows[0][1]


# `console` is its own world: a console turn replays against the operator's own thread
# and must never read the customer's live reply as its "previous response" (nor the other
# way round). Every other ingress - webhook, poller, retry - is the same live stream.
_CONSOLE_INGRESS = "console"


def previous_reply_text(
    db: Session, *, contact_respond_id: str, ingress: str | None, is_test: bool
) -> str | None:
    """The text this contact was last answered with, for the parser's `Previous response:`.

    Read from the newest COMPLETED `chatbot.turns` row rather than from the session,
    because AC-1504 fixes `session_vars` at exactly five keys and a previous reply is not
    one of them. The turn table already holds every answer the bot has given (D15 needs it
    to replay a duplicate delivery), so this is a read of something already written, not a
    new thing to store.

    Scoped three ways, each because crossing it would answer from the wrong conversation:
    the same contact; the same WORLD (`is_test`, so a test turn never reads a live reply);
    and the same side of the console boundary (a console turn replays against the
    operator's own thread). A dry run reads it too - it has to, or the console's second
    turn parses as though the first never happened.

    `status == "done"` is what "completed" means here, and it also excludes the row for
    the turn currently running, which is still `processing` when this is called.
    """
    from app.models.chatbot_turn import ChatbotTurn

    try:
        query = db.query(ChatbotTurn.response).filter(
            ChatbotTurn.contact_respond_id == str(contact_respond_id),
            ChatbotTurn.status == "done",
            ChatbotTurn.is_test.is_(bool(is_test)),
        )
        if str(ingress or "") == _CONSOLE_INGRESS:
            query = query.filter(ChatbotTurn.ingress == _CONSOLE_INGRESS)
        else:
            query = query.filter(ChatbotTurn.ingress != _CONSOLE_INGRESS)
        # PR #1353 fix round 3: `created_at` is Postgres `now()`, the TRANSACTION's start,
        # so two turns written in one transaction tie and the order was arbitrary;
        # `started_at` (the head's own clock, per row) breaks the tie. Same order as
        # `recent_exchanges`, so its newest pair's answer IS this reply.
        row = query.order_by(*_newest_turn_first(ChatbotTurn)).first()
    except Exception:  # noqa: BLE001 - no previous reply is a blank line, never a failure
        logger.warning(
            "chatbot: previous reply lookup failed for %s", contact_respond_id, exc_info=True
        )
        return None
    if row is None:
        return None
    reply = (row[0] or {}).get("reply") if isinstance(row[0], dict) else None
    text_value = reply.get("text") if isinstance(reply, dict) else None
    return str(text_value) if text_value else None


def _envelope_text(envelope: Any) -> str | None:
    """The message text a stored envelope carries (the respond.io payload,
    `message.message.message.text`), else an attachment's description."""
    inner = envelope
    for key in ("message", "message", "message"):
        inner = inner.get(key) if isinstance(inner, dict) else None
    if not isinstance(inner, dict):
        return None
    text = inner.get("text")
    if not (isinstance(text, str) and text.strip()):
        attachment = inner.get("attachment")
        text = attachment.get("description") if isinstance(attachment, dict) else None
    return text if isinstance(text, str) and text.strip() else None


def _newest_turn_first(model: Any) -> tuple[Any, ...]:
    """Newest turn first: `created_at`, then `started_at` for rows that tie on it."""
    return (model.created_at.desc(), model.started_at.desc().nullslast())


def recent_exchanges(
    db: Session,
    *,
    contact_respond_id: str,
    ingress: str | None,
    is_test: bool,
    limit: int = 3,
) -> list[tuple[str, str]]:
    """The last `limit` completed exchanges with this contact, oldest first, as (what
    the contact said, what the bot answered) - the parser's "Recent exchanges" lines
    (PR #1247 round 8: the owner's "is the context too less already?").

    Read from the same rows and under the same three scopes as `previous_reply_text`,
    so the newest pair's answer IS the Previous response. A row with no reply text is
    skipped; a row whose envelope carries no text (a photo or voice note) is "(media)".
    A lookup failure is no exchanges, never a failed turn.
    """
    from app.models.chatbot_turn import ChatbotTurn

    try:
        query = db.query(ChatbotTurn.envelope, ChatbotTurn.response).filter(
            ChatbotTurn.contact_respond_id == str(contact_respond_id),
            ChatbotTurn.status == "done",
            ChatbotTurn.is_test.is_(bool(is_test)),
        )
        if str(ingress or "") == _CONSOLE_INGRESS:
            query = query.filter(ChatbotTurn.ingress == _CONSOLE_INGRESS)
        else:
            query = query.filter(ChatbotTurn.ingress != _CONSOLE_INGRESS)
        rows = query.order_by(*_newest_turn_first(ChatbotTurn)).limit(limit * 2).all()
    except Exception:  # noqa: BLE001 - no history is no lines, never a failure
        logger.warning(
            "chatbot: recent exchanges lookup failed for %s", contact_respond_id, exc_info=True
        )
        return []
    pairs: list[tuple[str, str]] = []
    for envelope, response in rows:
        reply = response.get("reply") if isinstance(response, dict) else None
        answer = reply.get("text") if isinstance(reply, dict) else None
        if not (isinstance(answer, str) and answer.strip()):
            continue
        pairs.append((_envelope_text(envelope) or "(media)", answer))
        if len(pairs) == limit:
            break
    return list(reversed(pairs))


def load_profile(
    db: Session, contact_respond_id: str, *, space_id: str | None = None
) -> tuple[Profile, bool]:
    """`respond_contacts.chatbot_profile` (AC-1503, AC-1548) and `chatbot_stock_allowed`
    onto `Profile.stock_allowed` (S6) - the contact facts the engine reads before it
    routes.

    The second tuple member is a RETIRED recall flag (round 3, AC-MEM054: the
    `chatbot_recall_enabled` column it read is dropped outright, model and DB both -
    the recall re-parse it gated is deleted) - always `False` now, kept only so the
    handful of callers unpacking a 2-tuple do not need their own signature change for
    a value nothing reads any more.

    Resolved inside the workspace, not by `respond_io_id` alone: a respond.io id is only
    unique WITHIN a workspace, so the old single-row SELECT could have handed one
    contact's stock allowance and recall toggle to a namesake in another workspace,
    whichever Postgres returned first. `space_id` defaults to the default workspace's,
    the same value `check_access` and the company scope resolve for the turn. Two
    matching rows deny (see `_fail_closed_profile`); no matching row is a blank profile,
    which leaves stock allowed - "everyone is allowed unless an operator switches the
    contact off" is the S6 ruling, and an unknown contact has nobody to have switched it.

    `grants` stays None - unrestricted. The per-domain reveal gate is the one the
    business lane and `output_structurer` already run off `ctx.access.attributes`
    (contract 59); wiring THIS field to that list would deny every domain that declares
    no `reveal_key` at all, since a domain with no key falls back to its bare name. The
    trigger for wiring it is S5's grant sweep, which gives every domain a key.
    """
    try:
        if space_id is None:
            from app.services.chatbot.head.access import default_space_id

            space_id = default_space_id(db)
        rows = _profile_rows(db, contact_respond_id, space_id)
    except Exception:  # noqa: BLE001 - a contact with no profile row is a blank profile
        # Security review N1: an unreadable profile fails open (stock and escalation both
        # allowed), so it must at least be visible.
        logger.warning("chatbot: profile unreadable for %s", contact_respond_id, exc_info=True)
        return Profile(), False
    if not rows:
        return Profile(), False
    if len(rows) > 1:
        logger.warning(
            "chatbot: respond_io_id %s matches %s contacts in this workspace; "
            "denying stock and recall rather than picking one",
            contact_respond_id,
            len(rows),
        )
        profile, recall = _fail_closed_profile()
        # ESCALATION-CONTROL security review S2 (both rounds): the ambiguity denies stock,
        # and denies the hand-off the same way - the rows share one respond.io id in one
        # workspace, so they are the same person, and an operator who unticks the row they
        # can see must not be undone by the duplicate. Blocked when ANY row is unticked.
        # Every matching row, not the two the ambiguity check read (review nit: `LIMIT 2`
        # would decide on two arbitrary rows of three).
        every_row = _profile_rows(db, contact_respond_id, space_id, limit=None)
        if any(_escalation_allowed(row) is False for row in every_row):
            profile.escalation_allowed = False
        return profile, recall
    row = rows[0]
    raw = row[0] if isinstance(row[0], dict) else {}
    ledgers = raw.get("default_ledgers")
    return (
        Profile(
            tier=raw.get("tier"),
            language=raw.get("language"),
            grants=None,
            default_ledgers=list(ledgers) if isinstance(ledgers, list) else None,
            # NULL cannot happen (NOT NULL, default true); `is not False` keeps the
            # fail-open reading if it ever did.
            stock_allowed=row[1] is not False,
            # S2 (PLAN-chatbot-stock-ask-v2-24sep.md, R7): NULL cannot happen either
            # (NOT NULL, default false) - `is True` keeps the fail-closed reading if it
            # somehow did, matching these two columns' default-OFF rule.
            notify_salesman=row[2] is True,
            packing_list_allowed=row[3] is True,
            stock_availability_only=_stock_availability_only(
                db, contact_respond_id, space_id
            ),
            escalation_allowed=_escalation_allowed(row),
        ),
        False,
    )


def active_brands(db: Session) -> list[dict[str, Any]]:
    """#1262 slice 9 (F1a), round 3 section 6 step 1: the live `Brand` rows the
    parser's `Known brands:` line is built from - `{brand_name, brand_code, id,
    company_id, is_active}` for every ACTIVE brand, read fresh on every call (no
    cache: an operator edit in the table must reach the next turn with no
    restart).

    `app.models.product.Brand`, not the `projects` model's own brand-shaped
    table - `Brand` is the catalogue's own brand row (`brands`, `CompanyScopedMixin`),
    the one `crm_outstanding_report`'s own `Product.brand_id` points at; the
    projects model names something else entirely.

    Company scope is read off `db` itself, not recomputed here: `run_turn`
    (engine.py, `_contact_company_scope` / `_scoped_factory`) already stamps
    every session it opens with the contact's own companies before this ever
    runs, and `Brand`'s `CompanyScopedMixin` auto-filters any ORM query on that
    session to them (`app.models.base.do_orm_execute`) - a second, explicit
    `company_id IN (...)` filter here would be a second copy of the same rule,
    liable to disagree with it the day one changes and the other does not.
    """
    from app.models.product import Brand

    rows = (
        db.query(Brand)
        .filter(Brand.is_active.is_(True))
        .order_by(Brand.brand_name)
        .all()
    )
    return [
        {
            "id": row.id,
            "brand_name": row.brand_name,
            "brand_code": row.brand_code,
            "company_id": row.company_id,
            "is_active": row.is_active,
        }
        for row in rows
    ]


_BRAND_WORD_RE = re.compile(r"[0-9a-z]+")


def _osa_distance(a: str, b: str) -> int:
    """Edit distance counting a swap of two neighbouring letters as one edit
    ("sorneto" is one edit from "sorento")."""
    rows = [list(range(len(b) + 1))]
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            row[j] = min(rows[i - 1][j] + 1, row[j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], rows[i - 2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def brand_rows_for_word(brands: list[dict[str, Any]], word: Any) -> list[dict[str, Any]]:
    """#1262 fix lane round 7, R1: the live brand rows a typed word names, decided by
    the word alone so the same word gives the same brand every time.

    An exact name or code wins, then a word of the message that is a name or code
    ("sorento brand"), then the nearest brand name by spelling: one edit for a word of
    four or five letters, two for a longer one, and only when the nearest distance
    names one brand (a tie names none). Codes are matched exactly, never by spelling.
    No match returns [], which callers say back as a word they could not find."""
    folded = " ".join(jsc.nullish_str(word).casefold().split())
    if not folded:
        return []

    def keys(row: dict[str, Any]) -> set[str]:
        out = {
            " ".join(jsc.nullish_str(row.get(k)).casefold().split())
            for k in ("brand_name", "brand_code")
        }
        out.discard("")
        return out

    exact = [row for row in brands if folded in keys(row)]
    if exact:
        return exact
    words = _BRAND_WORD_RE.findall(folded)
    whole = [row for row in brands if keys(row) & set(words)]
    if whole:
        return whole
    best: int | None = None
    nearest: list[dict[str, Any]] = []
    for w in words:
        if len(w) < 4:
            continue
        limit = 1 if len(w) <= 5 else 2
        for row in brands:
            name = "".join(jsc.nullish_str(row.get("brand_name")).casefold().split())
            if len(name) < 4:
                continue
            distance = _osa_distance(w, name)
            if distance > limit:
                continue
            if best is None or distance < best:
                best, nearest = distance, [row]
            elif distance == best and row not in nearest:
                nearest.append(row)
    names = {"".join(jsc.nullish_str(r.get("brand_name")).casefold().split()) for r in nearest}
    return nearest if len(names) == 1 else []


def brand_entity_word(entity: dict[str, Any]) -> str:
    """The word a brand-hinted entity is resolved by: what the customer typed, and the
    parser's `canonical_code` only when no typed word came with it. The parser's own
    reading of a misspelt word is a guess that differs between runs (round 7, R1), so
    it never decides the brand while the typed word is there."""
    raw = jsc.nullish_str(entity.get("raw")).strip()
    return raw or jsc.nullish_str(entity.get("canonical_code")).strip()


def _brand_hinted_entities_matching_live(
    db: Session, entities: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """#1262 slice 9 (F1a): the `hint: "brand"` entities among `entities` whose own
    `raw` or `canonical_code` matches a LIVE brand's name or code, case-insensitive
    exact - the gate `resolve_kinds` uses to keep a real brand word away from the
    shared resolver entirely. A brand-hinted token NOT on the live list is
    untouched (today's path - AC-S9-3's own guard), because it is not this
    contact's brand to answer for.
    """
    candidates = [
        e
        for e in entities
        if isinstance(e, dict) and jsc.nullish_str(e.get("hint")).strip().lower() == "brand"
    ]
    if not candidates:
        return []
    live = active_brands(db)
    # #1262 fix lane round 7, R1: the same word-alone read the brand ids come from
    # (`brand_rows_for_word`), so a word kept off the resolver always has a brand and a
    # word with none always reaches it and is said back.
    return [e for e in candidates if brand_rows_for_word(live, brand_entity_word(e))]


def order_brand_filter(
    db: Session, lane_out: dict[str, Any], focus: Focus | None
) -> tuple[list[str], list[str]]:
    """#1262 fix lane round 3, B1-r2 and N7: the brand an ORDER fetch is filtered by,
    as `(ids, names)` - the one source the tool args, the carried focus and the
    "Brand:" header line all read.

    The ids: the brand words this turn typed, matched against the live list
    (`lanes.business._resolve_outstanding_brand_ids`, name or code), else the brand
    the conversation already carries (`focus.outstanding_brand_ids`, or the answered
    question's own `outstanding_carried_brand_ids`). A carried id is kept only while it
    is still a live brand in this session's scope. The names are read off those same
    live rows, so a brand typed by its code ("srt") prints "Sorento".
    """
    from app.services.chatbot.lanes.business import _resolve_outstanding_brand_ids

    parse = dict(lane_out)
    if not parse.get("outstanding_carried_brand_ids") and focus is not None:
        parse["outstanding_carried_brand_ids"] = list(focus.outstanding_brand_ids)
    semantic: dict[str, Any] = {}
    try:
        _resolve_outstanding_brand_ids(parse, semantic, db=db)
        live = {row["id"]: row for row in active_brands(db)}
    except Exception:  # noqa: BLE001 - a test double with no real session has no
        # brands either; the fetch then runs exactly as it did before this seam.
        # Round 4, N11: logged, so a real DB error dropping the brand is visible.
        logger.warning("order_brand_filter: live brand lookup failed", exc_info=True)
        return [], []
    ids = [b for b in jsc.array(semantic.get("outstanding_brand_ids")) if b in live]
    names = [
        jsc.nullish_str(live[b].get("brand_name") or live[b].get("brand_code")).strip() for b in ids
    ]
    return ids, [n for n in names if n]


def _escalation_allowed(row: Any) -> bool:
    """ESCALATION-CONTROL: `respond_contacts.escalation_allowed`, the one per-contact
    switch. NOT NULL default true; `is not False` keeps the fail-open reading (allowed) if
    it were ever NULL or missing from the row."""
    return (row[4] if len(row) > 4 else None) is not False


def _stock_availability_only(db: Session, contact_respond_id: str, space_id: str | None) -> bool:
    """Is this contact's stock visibility policy "Availability only" (hand test F1)?

    The same resolution the stock balance read applies (`stock_visibility.
    resolve_policy`: the contact override, else the merged access types, else the
    default), so the engine and the tool can never disagree about who is a dealer. A
    read that fails is not a dealer: today's behaviour, never a silent refusal."""
    try:
        from app.services.stock_visibility import resolve_policy

        # A savepoint, so a failed read cannot leave the turn's session aborted.
        with db.begin_nested():
            policy = resolve_policy(db, contact_respond_id, space_id)
    except Exception:  # noqa: BLE001 - a policy read is a profile fact, not the turn
        logger.warning("chatbot: stock visibility policy unreadable for %s", contact_respond_id)
        return False
    return policy is not None and policy.mode == "availability"


def load_state(session_block: Any, *, profile: Profile, turn_no: int) -> State:
    """Stage A's `State`: focus and pending off the five session keys, profile beside.

    The open question is TICKED on the way in (AC-816 rule 1): an escalation offer the
    customer has ignored for three turns is not loaded at all, so nothing downstream can
    accept it and this turn runs as the fresh message it is.
    """
    five = session_state.five_keys(session_block)
    return State(
        focus=focus_from_wire(five.get("focus")),
        pending=tick_pending(from_wire(five.get("open_question"))),
        profile=profile,
        turn_no=turn_no,
        # Issue #1178: the open idea draft rides in as state, read by `apply()`'s
        # `_continues_open_draft`. Both session shapes, because `five_keys` already
        # folds the n8n-nested `variables.ideation` onto the flat key.
        ideation=five.get("ideation"),
    )


def focus_diff(before: Focus, after: Focus) -> dict[str, Any]:
    """What APPLY changed, for the `apply` trace record (AC-1549)."""
    from app.services.chatbot.turn.state import focus_to_wire

    left, right = focus_to_wire(before), focus_to_wire(after)
    return {key: {"before": left[key], "after": right[key]} for key in left if left[key] != right[key]}


# --------------------------------------------------------------------------- #
# Stage B/C: the verdict the kept lanes read
# --------------------------------------------------------------------------- #


def _prior_suggested_team(session_block: Any) -> str | None:
    """The legacy `variables.routing.suggested_team` nest a previous turn wrote.

    `session_block` here is exactly what `engine.py` hands `build_ctx` as `session=`, so
    this reads it the SAME way `escalation._prev_variables(ctx)` reads `ctx.session` -
    `session_vars.variables`, then `session_vars`'s own bare `variables`, then nothing.
    Any shape mismatch (a blank session, a five-key-only contact with no legacy nest at
    all) reads as "nothing carried", never an error.
    """
    try:
        session_vars = session_block.get("session_vars") if isinstance(session_block, dict) else None
        variables = session_vars.get("variables") if isinstance(session_vars, dict) else None
        if not variables:
            # The node's own second fallback: `ctx.session.variables` directly, bypassing
            # `session_vars` - the session block has had three shapes over its life
            # (`escalation._prev_variables`'s own docstring), reproduced whole.
            variables = session_block.get("variables") if isinstance(session_block, dict) else None
        routing = variables.get("routing") if isinstance(variables, dict) else None
        team = routing.get("suggested_team") if isinstance(routing, dict) else None
        team = str(team).strip() if team else ""
        return team or None
    except Exception:  # noqa: BLE001 - a session shape this cannot read carries nothing
        return None


#: The offer kinds that can name a COMPANY - the escalate offer this engine mints itself
#: (`answer_bridge`), the company clarify the escalation lane asks back
#: (`engine._question_offered`), and (#865 round 6) the CS member picker, whose options
#: name a PERSON but whose payload names the companies whose rosters it printed
#: (`turn.pending.offered_companies`).
_COMPANY_OFFER_KINDS: frozenset[str] = frozenset({"team_pick", "company_pick", "member_offer"})


def escalation_roster_plan(
    pending: Any, *, accepted_lane: str | None, accepted_rules: Any = None
) -> list[dict[str, Any]] | None:
    """`variables.routing_roster_plan`, for an ACCEPTED company-carrying offer this turn
    is about to hand to escalation (security N-3/S2, hand pass 11 review):
    `escalation_context` (`lanes/escalation.py:236-299`) reads this EXACT legacy field,
    unchanged, for BOTH of its company arms - the `company_pick` pool a named company is
    validated against, and the single-row `same_team` roster a bare "yes" routes by. It
    is the field `compile-current-state` (the kept n8n-mirroring path) writes from
    `cs_roster_plan`'s member-offer roster. This engine mints its OWN offers directly
    (`answer_bridge.apply_silent_company_offer` / `_miss_question`) with no
    `compile-current-state` equivalent, so the company those options carried never
    reached escalation on a bare "yes" - `option_payload` is empty on that arm by design
    (`turn/decide.py::picked_positions`, defect 4: a yes/no offer is answered by the word
    alone, never a position, so `turn/apply.py::_answer_offer`'s `picked` stays `None`).

    Built straight off the pending's OWN options - no new pending kind, no new field, the
    SAME row shape `cs_roster_plan` / `sub_answer.miss_roster_plan` already produce
    (`plan_idx`/`company_id`/`company_name`/`brand_code`), one entry per option that
    carries a company. The **id** is the point (hand pass 11 blocker 1): routing reads
    `company_id` and nothing else, so a row carrying only the NAME routes exactly as if
    no company had been named at all.

    `None` for a plain (no-company) yes/no offer - the zero-stock ladder's own bare "Yes"
    needs no roster at all, `team` alone already reaches `escalation_context` through
    `lane_parse_output`'s existing `accepted_team` chain.

    Gated on the ACCEPT rule, not on the lane alone (security nit, hand pass 11): a FRESH
    handover request is also `lane == "escalation"` (`turn/apply.py::_lane`) while a
    still-live offer sits in state, and that turn accepted nothing - its pool is not a
    roster anybody was just shown.
    """
    rules = accepted_rules if isinstance(accepted_rules, (list, tuple, set, frozenset)) else ()
    if (
        accepted_lane != "escalation"
        or "answer_pending_accept" not in rules
        or pending is None
        or pending.kind not in _COMPANY_OFFER_KINDS
    ):
        return None
    # One reader of the offered pool (`offered_companies`), which also carries reviewer
    # N-b's Mapping guard: a persisted option that is not a dict never takes the turn down.
    plan = [{"plan_idx": i, **row} for i, row in enumerate(offered_companies(pending))]
    return plan or None


def _accepted_pending_field(
    pending: Pending | None, verdict: Mapping[str, Any], field: str
) -> str | None:
    """The AGENT or BRAND the customer just picked THIS turn, off an ACCEPTABLE open
    offer - one reader, parameterized only by which payload key it is reading, shared
    by `_accepted_pending_agent` (called from `with_routing_agent_default`, before
    the access read) and `_accepted_pending_brand` (round 4, called from
    `lane_parse_output`, for `escalation_context`'s new `carried_brand` rung). The
    gate, the accept check and the per-option/top-level fall-through are the SAME
    for both fields - only the key read off each payload differs.

    Reuses `turn.decide.picked_positions` - the SAME reading `apply()` uses to tell
    which option a numbered pick answered - rather than re-parsing
    `reference_positions` a second time here (PLAN "one seam"). A multi-team
    `team_pick` names one specific option's own `payload[field]`; a plain
    acceptance (a bare "yes" over a single-option offer, or a roster's attached
    escalate sentence - nothing picked by number) falls to the pending's own
    top-level `payload[field]`, which is where the mint sites
    (`turn/compose.py::_team_pick_question`, `answer_bridge.py::_miss_question`,
    `engine.py::_question_offered`) store the minting turn's own value.

    SRTSC07 review round 1, SHOULD-1: gated on the SAME condition `turn/apply.py:691`
    uses to decide "this offer is acceptable" - `pending.kind in OFFER_KINDS or
    pending.payload.get("escalate_offered") is True`. Without this gate a roster
    pending with NO escalate offer attached (a plain `product_pick`, still open while
    this turn asks for something else entirely) could still supply a value here,
    while `lane_parse_output`'s own team chain (`pending.kind in OFFER_KINDS` alone,
    no `escalate_offered` arm - a fresh, un-accepted handover carries no `accepted_
    team` either) left the team at `DEFAULT_SUGGESTED_TEAM` - the two halves of the
    pair disagreeing and `/external/next-assignee` 404ing on a pool that does not
    exist (measured: `incoming_stock_enquiries` / `customer_service`).

    SHOULD-3: the per-option read only short-circuits on an option that actually
    NAMES a value for this field, or the explicit hold (`payload.get("hold") is
    True`, the "No it's okay" decline). A MEMBER option (a combined roster+CS
    offer's own member half, `_miss_question`'s `combined_member_rows` arm) carries
    no agent/brand of its own - picking one IS still an escalation acceptance
    (`turn/apply.py:546`) - so it falls through to the pending's own top-level value
    instead of returning `None` outright.

    SRTSC07 review round 2, SHOULD-1 residual: the round 1 gate was "ACCEPTABLE",
    not "ACCEPTED" - an `escalate_offered` roster (not in `OFFER_KINDS`) still open
    while THIS turn asks about something else entirely (no yes, no escalation
    confirmation, no number landing on the roster) supplied its carried value
    unconditionally, while the TEAM half stays at `DEFAULT_SUGGESTED_TEAM` for that
    same roster - `lane_parse_output`'s own chain only reads `pending.team` for an
    `OFFER_KINDS` pending (`turn_runtime.py:647`), never a roster, accepted or not.
    An extra accept check now gates ONLY that arm, using the same signal `decide()`
    itself reads at `turn/decide.py:571-575` (a bare "yes"/escalation-confirmation
    flag, vetoed by a decline or a negation) plus a numbered pick that actually
    lands on one of the roster's own options. A proper `OFFER_KINDS` pending
    (`team_pick`/`company_pick`/`member_offer`) kept round 1's behaviour
    unconditionally at the time - its team was ALSO read unconditionally at that
    same `lane_parse_output` line, so the two halves stayed in step either way.

    Historical note (S10, fix round 6, 23 Sep 2026): `lane_parse_output` no longer
    has an `OFFER_KINDS` pending arm at all - `turn/apply.py::_answer_pending`
    already makes the acceptance judgement this paragraph describes and stamps it
    onto `trace.team`, which reaches `lane_parse_output` as `accepted_team` (the
    chain's own first read). The paragraph above is kept as the design history for
    THIS function's own accept-check, not a claim about `lane_parse_output`'s
    current shape.

    SRTSC07 review round 2 NITs: (A) a numbered pick only counts as landing on the
    roster's OWN escalation offer when the matched option is MEMBER-typed - the
    same reading `turn/apply.py::_picks_a_member_option` uses to decide a roster
    pick is an escalation acceptance rather than a business one. Without it, "2"
    over an ordinary PRODUCT option in a did-you-mean roster that also carries an
    attached escalate sentence carried the roster's agent onto a plain business
    fetch, with the team still defaulted (no assign happens, but the access check
    and any miss-offer minted off that same turn saw the mismatched pair). (B) the
    accept signal is vetoed by the SAME two reads `decide()`'s own qualifier uses
    (`facts["declined"]` off `escalation.escalation_declined`, `facts["negated"]`
    off `is_affirmative is False`) - a verdict can carry BOTH `is_affirmative: true`
    and an explicit decline (the parser is not asked to keep the two mutually
    exclusive), and `decide()` treats the decline as decisive either way.
    """
    if pending is None:
        return None
    payload = pending.payload if isinstance(pending.payload, Mapping) else {}
    acceptable_offer = pending.kind in OFFER_KINDS
    escalate_offered_roster = not acceptable_offer and payload.get("escalate_offered") is True
    if not acceptable_offer and not escalate_offered_roster:
        return None
    try:
        picked = picked_positions(pending, dict(verdict))
    except Exception:  # noqa: BLE001 - a malformed verdict must not break the carry
        picked = None
    picked_position = None
    if picked:
        positions, _via = picked
        if len(positions) == 1:
            picked_position = positions[0]
    if escalate_offered_roster:
        escalation = verdict.get("escalation")
        escalation = escalation if isinstance(escalation, Mapping) else {}
        landed_on_a_member_option = picked_position is not None and any(
            isinstance(opt, Mapping)
            and opt.get("position") == picked_position
            and opt.get("entity_type") == "member"
            for opt in pending.options
        )
        declined = escalation.get("escalation_declined") is True
        negated = verdict.get("is_affirmative") is False
        accepted = (
            verdict.get("is_affirmative") is True
            or escalation.get("is_escalation_confirmation") is True
            or landed_on_a_member_option
        ) and not (declined or negated)
        if not accepted:
            return None
    if picked_position is not None:
        for opt in pending.options:
            if not isinstance(opt, Mapping) or opt.get("position") != picked_position:
                continue
            opt_payload = opt.get("payload")
            opt_payload = opt_payload if isinstance(opt_payload, Mapping) else {}
            value = opt_payload.get(field)
            if value or opt_payload.get("hold") is True:
                return value or None
            # A matched option that names neither a value for this field nor the
            # hold flag (a MEMBER option) falls through to the pending's own
            # top-level value below, rather than returning `None` here.
            break
    value = payload.get(field)
    return value or None


def _accepted_pending_agent(pending: Pending | None, verdict: Mapping[str, Any]) -> str | None:
    """The agent half - see `_accepted_pending_field`'s own docstring for the shared
    gate/accept-check/fall-through this and `_accepted_pending_brand` both share."""
    return _accepted_pending_field(pending, verdict, "agent")


def _accepted_pending_brand(pending: Pending | None, verdict: Mapping[str, Any]) -> str | None:
    """The brand half (round 4, owner-approved, 22 Sep 2026 - "a MOCHA product's
    escalation goes to Lucas, a SORENTO product's to Jereen"). Read by
    `lane_parse_output`, not `with_routing_agent_default`: the brand has no access
    check to sit ahead of (unlike the agent), and it is written onto
    `out["escalation"]["carried_brand"]` for `escalation_context`'s own new rung to
    read back, the same "one flag on ctx.parse.output" idiom `preferred_assignee_id`
    / `company_pick` already use. See `_accepted_pending_field`'s own docstring for
    the shared gate/accept-check/fall-through."""
    return _accepted_pending_field(pending, verdict, "brand_code")


# A message that is ONLY a count: "10", "show 10", "the first 10", "10 please", "top 20".
_BARE_COUNT_RE = re.compile(
    r"^(?:(?:show|list|give|send|bagi|tunjuk)\s+(?:me\s+)?)?"
    r"(?:(?:the\s+)?(?:first|top)\s+)?"
    r"(\d{1,4})"
    r"(?:\s+(?:please|pls|plz|only|je|sahaja))?[.!]?$",
    re.IGNORECASE,
)


def with_set_count_from_text(
    verdict: dict[str, Any], message: Any, *, carried: Any
) -> dict[str, Any]:
    """The verdict with `top_n` read off a bare count, while "how many should I show?"
    is open (`focus.set_page`, armed only when a set was too long to list).

    Reviewer S1 on PR #833. The follow-up read only the parser's `top_n`, and nothing
    in the parser prompt covers a bare "10" given as the answer to a count question: a
    null `top_n` answered nothing, and a `reference_positions: [10]` would pick row 10 of
    a list that was never sent. With the question open and the message nothing BUT a
    count, the count is that number and no position is picked. A message that names
    anything else (a subject, a word the regex does not know) is left to the parser.
    """
    if not isinstance(carried, dict) or not isinstance(message, str):
        return verdict
    if any(isinstance(e, dict) and e.get("current_message") is True for e in (verdict.get("entities") or [])):
        return verdict
    # W4: after a LISTED page (`shown` > 0), "another N" continues the set, read whatever
    # the parser made of it. Round 3 W2 (owner hand test, "why when i say 10, it gives
    # some other answer"): so does a bare count - "10" after a page of 30 is rows 31 to
    # 40 of the same set, never a row pick (a set answer mints no pick roster) and never
    # the parser's own reading of it.
    shown = int((carried.get("set_key") or {}).get("shown") or 0)
    more = set_continue_count(message)
    if more is not None:
        return {**verdict, "top_n": more, "reference_positions": [], SET_CONTINUE_KEY: True}
    if shown == 0 and named_count(verdict.get("top_n")) is not None:
        return verdict
    # Line 1 is the customer's own text; a quoted "reply to: ..." rides on line 2
    # (`engine.build_latest_user_message`).
    lines = message.strip().splitlines()
    match = _BARE_COUNT_RE.match(lines[0].strip()) if lines else None
    if not match:
        return verdict
    count = int(match.group(1))
    if count <= 0:
        return verdict
    if shown > 0:
        return {**verdict, "top_n": count, "reference_positions": [], SET_CONTINUE_KEY: True}
    return {**verdict, "top_n": count, "reference_positions": []}


#: The verdict keys that say WHAT was asked, kept with an open clarify so its answer
#: re-runs that ask (round 4 R5).
_CLARIFY_ASK_KEYS = (
    "message_type",
    "intent_hint",
    "domain_hint",
    "scope_intent",
    "match_mode",
    "requested_attributes",
    "user_goal",
    "access_levels",
)
_CLARIFY_LEAD_WORDS = frozenset({"the", "a", "an", "i", "mean", "meant", "yes", "ya", "its", "it", "is"})


def set_clarify_carry(verdict: dict[str, Any], predicate: Any, resolved: Any) -> dict[str, Any] | None:
    """What an open clarify question was about, for `focus.set_clarify`, or None.

    Round 4 R5 (owner console test on PR #833: "i clarify if it is tap or wash basin and i
    said tap, you supposed to do the searching"). Two questions carry it: the product-type
    clarify ("I don't know 'water tap basin' as a product type. Did you mean tap or wash
    basin?", AC-1320, retired in fix round 10: no product types are offered) and the unknown
    value one (R6, "I know P trap and S trap."). Kept:
    the unknown words, the options offered, and the ask itself (intent, domain, attribute
    and this message's own entities), so the answer is that ask with the word replaced."""
    term: str | None = None
    options: list[str] = []
    unknown = []
    if isinstance(predicate, dict):
        unknown = [u for u in (predicate.get("unknown_values") or []) if isinstance(u, dict)]
    if not unknown and isinstance(resolved, dict):
        unknown = [u for u in (resolved.get("unknown_spec_values") or []) if isinstance(u, dict)]
    if unknown:
        term = jsc.nullish_str(unknown[0].get("said")).strip()
        options = [jsc.nullish_str(k).strip().lower() for k in unknown[0].get("known") or []]
    options = [o for o in options if o]
    if not term or not options:
        return None
    ask = {key: verdict.get(key) for key in _CLARIFY_ASK_KEYS}
    ask["entities"] = [
        dict(e)
        for e in (verdict.get("entities") or [])
        if isinstance(e, dict) and e.get("current_message") is True
    ]
    return {"term": term, "options": options, "ask": ask}


def _replace_word(text: Any, term: str, option: str) -> Any:
    if not isinstance(text, str):
        return text
    if text.strip().lower() == term.lower():
        return option
    return re.sub(re.escape(term), option, text, flags=re.IGNORECASE)


def with_clarify_answer(verdict: dict[str, Any], message: Any, *, carried: Any) -> dict[str, Any]:
    """The carried ask with the unknown word replaced, when this message answers the open
    clarify with one of its options; else the verdict unchanged.

    Round 4 R5 (owner console test on PR #833): "tap" after "Did you mean tap or wash
    basin?" was read on its own, a product search that listed two codes. The answer is
    the question's own ask again, "any water tap basin" with "water tap basin" read as
    "tap", so the customer gets the counted set ("2 taps have stock."). A message that is
    not one of the options is a question of its own and is left to the parser."""
    if not isinstance(carried, dict) or not isinstance(message, str):
        return verdict
    term = jsc.nullish_str(carried.get("term")).strip()
    options = [o for o in (carried.get("options") or []) if isinstance(o, str) and o]
    ask = carried.get("ask") if isinstance(carried.get("ask"), dict) else None
    lines = message.strip().splitlines()
    words = re.findall(r"[a-z0-9]+", lines[0].lower()) if lines else []
    while words and words[0] in _CLARIFY_LEAD_WORDS:
        words = words[1:]
    reply = " ".join(words)
    option = next((o for o in options if " ".join(re.findall(r"[a-z0-9]+", o.lower())) == reply), None)
    if not term or ask is None or option is None:
        return verdict
    entities = []
    replaced = False
    for e in ask.get("entities") or []:
        if not isinstance(e, dict):
            continue
        raw = e.get("raw")
        new_raw = _replace_word(raw, term, option)
        replaced = replaced or new_raw != raw
        entities.append({**e, "raw": new_raw, "canonical_code": None, "current_message": True})
    if not replaced:
        entities.append({"raw": option, "hint": "product_type", "canonical_code": None, "current_message": True, "confident": True})
    out = {**verdict, **{k: v for k, v in ask.items() if k != "entities"}}
    out["entities"] = entities
    out["user_goal"] = _replace_word(ask.get("user_goal"), term, option)
    out["top_n"] = None
    out["reference_positions"] = []
    return out


#: The entity hints that describe WHICH products a set is: its class word, its spec
#: words, its brand, and the codes of an earlier answer.
_SET_DESCRIBING_HINTS = frozenset({"product_type", "category", "spec", "specification", "brand", "product"})


def with_new_set_words(verdict: dict[str, Any]) -> dict[str, Any]:
    """The verdict without an EARLIER turn's set words, when this message names a class
    word of its own.

    Round 3 W3 (owner hand test on PR #833, turn 4): "which basin has cert" after a
    water closet set came back with the water closet words and codes beside "basin",
    `current_message: false` - the parser reads them off the conversation. They blended
    into "Product type: Wash basin or Water closet" or, carrying the listed codes, asked
    for the attachment type of a water closet. A new class word starts a new set: the
    earlier class, spec, brand and product entities are dropped. A customer, a location
    or a document from earlier is not part of the set and stays.
    """
    entities = [e for e in (verdict.get("entities") or []) if isinstance(e, dict)]
    names_a_class = any(
        e.get("current_message") is True and jsc.nullish_str(e.get("hint")).strip().lower() in CLASS_HINTS
        for e in entities
    )
    if not names_a_class:
        return verdict
    kept = [
        e
        for e in entities
        if e.get("current_message") is True
        or jsc.nullish_str(e.get("hint")).strip().lower() not in _SET_DESCRIBING_HINTS
    ]
    if len(kept) == len(entities):
        return verdict
    return {**verdict, "entities": kept}


#: Set on the verdict when the message asked for "another N" of the carried set;
#: `turn/apply.py` pages a LISTED set only then.
SET_CONTINUE_KEY = "set_continue"

# "another 40", "can give another 40?", "40 more", "next 40", "lagi 10": the customer asks
# for more of a list, naming how many (W4, owner hand test round 2). The bot never offers
# this; it only answers it.
_CONTINUE_COUNT_RE = re.compile(
    r"\b(?:another|next|more|lagi|further)\s+(\d{1,4})\b|\b(\d{1,4})\s+(?:more|lagi|further)\b",
    re.IGNORECASE,
)


def set_continue_count(message: Any) -> int | None:
    """The count in an "another N" message, or None."""
    if not isinstance(message, str):
        return None
    lines = message.strip().splitlines()
    match = _CONTINUE_COUNT_RE.search(lines[0]) if lines else None
    if not match:
        return None
    count = int(match.group(1) or match.group(2))
    return count if count > 0 else None


#: The domains whose help requests are answered rather than escalated (contract 21, 22):
#: the same pair `turn/apply._HELP_EXEMPT_DOMAINS` keeps.
_NAMED_TEAM_EXEMPT_DOMAINS = frozenset({"portal_link", "ideate"})


def with_named_team_escalation(verdict: dict[str, Any]) -> dict[str, Any]:
    """An escalate word plus a named team is a help request, whatever else the message
    carries (#865 round 5, R1).

    The parser prompt's MESSAGE TYPE rule 1 already says it: asking for a specific team or
    to escalate is `request_for_help`, and it "takes priority over business_query,
    clarification, and casual ... even if they also mention a product or order". The
    owner's "pelase escalate to marketing team MWc-SC8609-)PP water closet" came back a
    master_products business query and was answered with a spec sheet; its `user_goal`
    still read "escalate ... to the marketing team". This makes the prompt's rule hold on
    the verdict, from the parser's own reading of the message
    (`lanes/escalation.asks_for_a_named_team`, the reader round 4's `_named_teams` uses;
    never the raw text, D11).

    Stamps `escalation.named_teams`, the catalogue teams the customer named, the one
    structured fact `turn/apply.py` acts on: such a turn plans no fetch, asks no narrowing
    or kind question, and accepts no open offer made for a different team (an explicit
    team beats a pending offer, #706). Its product words stay on the verdict as the
    escalation's focus.
    """
    from app.services.chatbot.lanes.escalation import _named_teams, asks_for_a_named_team

    if verdict.get("domain_hint") in _NAMED_TEAM_EXEMPT_DOMAINS or not asks_for_a_named_team(verdict):
        return verdict
    escalation = verdict.get("escalation") if isinstance(verdict.get("escalation"), dict) else {}
    return {
        **verdict,
        "message_type": "request_for_help",
        "escalation": {**escalation, "named_teams": _named_teams(verdict)},
    }


# n8n `output_exchange` rev-5 (`_coFillers` / `_coNegators`), byte-identical word lists:
# the company-pick tier strips confirmation and request words before matching, and a
# negator anywhere refuses the pick ("not mocha" is never a pick for Mocha).
_CO_FILLERS = frozenset(
    "yes ya yeah yep yup ok okay okie oki k sure please pls plz pl kindly team the a an to for "
    "of on at in route assign escalate escalation pass send forward transfer connect pick "
    "choose select prefer handle help one lah la leh lor ah go with it that this then can "
    "could would like want need you me my us i ill id company side instead guys ppl people "
    "staff department dept group thanks thank ty tq".split()
)
_CO_NEGATORS = frozenset(
    "no not nope nah never dont neither nor none without except cancel stop".split()
)
# (C) a product-code-like token ("MUB6201", "MWCX7608-SH-S10") refuses the pick.
_CO_PRODUCT_TOKEN = re.compile(r"^[a-z]{2,}[a-z0-9-]*\d", re.IGNORECASE)


def _co_token(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def company_pick(verdict: Mapping[str, Any], pending: Pending | None, message: Any) -> str | None:
    """Which OFFERED company this reply names, or None (#865 round 6, R3).

    The port of the n8n parser fork's `_coCompanyPick` (sorento-crm-n8n PR #23, rev-5),
    which the #952 re-architecture deleted with `head/output_exchange` and never
    replaced, so a reply of "mocha" or "srt" to a two-company offer reached routing only
    when the model happened to fill `escalation.company_pick`. The rules, as n8n has
    them:

    * (A) the pool is the companies the offer SHOWED (`offered_companies`), never the
      union: "yes mocha" over a Sorento-only offer picks nothing.
    * (B) a company matches by name, code or alias (`lanes/escalation.CO_ALIASES`), on a
      word boundary, and exactly one company may match.
    * (C) the reply counts only when it is short (up to four words, and a remainder of
      two or more words also needs no entity of its own and no domain question), or
      longer with at most six words left once the filler words are stripped and no
      entity or domain question of its own; a product-code-like token refuses it.
    * (D) a negator anywhere ("not mocha", "no") refuses it.
    * The parser's own `escalation.company_pick` is the semantic fallback, accepted only
      when it names exactly one offered company, the reply is not a bare confirmation
      (it strips to nothing), carries no negator and is not a domain question.

    `message` is the customer's own text: this tier reads it, as n8n's does
    (D11-reproduced: `output_exchange` `_coCompanyPick`, `latest_user_message`).
    """
    from app.services.chatbot.lanes.escalation import CO_ALIASES

    pool: dict[str, set[str]] = {}
    for row in offered_companies(pending):
        name = str(row["company_name"])
        key = name.lower().strip()
        keys = pool.setdefault(name, {key})
        keys.update(CO_ALIASES.get(key, []))
    if not pool:
        return None

    raw_reply = re.split(r"\s*reply to:", str(message or ""), flags=re.IGNORECASE)[0].strip()
    words = raw_reply.split()
    kept = [w for w in words if _co_token(w) not in _CO_FILLERS]
    has_negator = any(_co_token(w) in _CO_NEGATORS for w in words)
    product_token = any(_CO_PRODUCT_TOKEN.match(re.sub(r"[^a-z0-9-]", "", w, flags=re.IGNORECASE)) for w in words)
    current_entity = any(
        isinstance(e, Mapping) and e.get("current_message") is True for e in (verdict.get("entities") or [])
    )
    domain_question = (
        bool(verdict.get("domain_hint")) or verdict.get("message_type") in ("business_query", "clarification")
    ) and verdict.get("is_affirmative") is not True
    short_ok = 0 < len(words) <= 4 and (len(kept) < 2 or not (current_entity or domain_question))
    long_ok = len(words) > 4 and 0 < len(kept) <= 6 and not current_entity and not domain_question

    def hits(texts: list[str]) -> str | None:
        found = {
            name
            for name, keys in pool.items()
            if any(re.search(rf"(^|[^a-z0-9]){re.escape(k)}([^a-z0-9]|$)", t) for k in keys for t in texts)
        }
        return next(iter(found)) if len(found) == 1 else None

    if has_negator:
        return None
    if not product_token:
        texts = [" ".join(kept).lower()] if (short_ok or long_ok) and kept else []
        mention = verdict.get("person_mention")
        if isinstance(mention, str) and mention.strip():
            texts.append(mention.strip().lower())
        picked = hits(texts) if texts else None
        if picked is not None:
            return picked
    if not kept or domain_question:
        return None
    raw_pick = (verdict.get("escalation") or {}).get("company_pick") if isinstance(verdict.get("escalation"), Mapping) else None
    if not isinstance(raw_pick, str) or not raw_pick.strip():
        return None
    wanted = raw_pick.lower().strip()
    direct = [name for name, keys in pool.items() if wanted in keys]
    if len(direct) == 1:
        return direct[0]
    return None if direct else hits([wanted])


def with_company_pick(verdict: dict[str, Any], *, pending: Pending | None, message: Any) -> dict[str, Any]:
    """The verdict with the company this reply picked off an open escalation offer
    (#865 round 6, R3), or with the parser's unvalidated pick removed.

    A pick IS the acceptance (n8n Tier 2.5: `{is_escalation_confirmation: true,
    company_pick}`), so `apply()` accepts the offer and `escalation_context` routes by
    the picked row. The parser's own `company_pick` survives only when it validates
    against the offered pool; over an offer that names no company it is left untouched.
    A numbered pick, or a request for a team the offer was not made for (#706), is not
    this tier's to read.
    """
    offered = offered_companies(pending)
    if not offered or verdict.get("reference_positions"):
        return verdict
    escalation = dict(verdict.get("escalation") or {})
    named = escalation.get("named_teams")
    if named and pending is not None and pending.team not in named:
        return verdict
    if names_its_own_ask(verdict):
        # #1323 case 2: the parser read an ask of its own ("mocha brand", "how about
        # mocha"), so the company it names is that ask's brand, never the pick - and a
        # pick here would also wipe the entities and domain the parser just read.
        if escalation.get("company_pick"):
            escalation["company_pick"] = None
            escalation["is_escalation_confirmation"] = False
            return {**verdict, "escalation": escalation}
        return verdict
    picked = company_pick(verdict, pending, message)
    if picked is None:
        if escalation.get("company_pick"):
            escalation["company_pick"] = None
            return {**verdict, "escalation": escalation}
        return verdict
    return {
        **verdict,
        "domain_hint": None,
        "entities": [],
        "is_affirmative": True,
        "escalation": {
            **escalation,
            "is_escalation_confirmation": True,
            "escalation_declined": False,
            "company_pick": picked,
            "company_pick_by": "reply",
        },
    }


def with_routing_agent_default(
    verdict: dict[str, Any],
    *,
    pending: Pending | None = None,
) -> dict[str, Any]:
    """The verdict with `routing.suggested_agent` filled in ONCE, before access.

    Hand-pass 1 finding 2b (16 Sep 2026): `engine.run_turn` checked access on the
    parser's RAW `routing.suggested_agent`, and the `DEFAULT_SUGGESTED_AGENT` fallback
    only ran later, in `lane_parse_output`, for the lanes - so an unrouted webhook turn
    (the parser named no agent, which is most casual and many business turns) asked the
    access service about agent `None`. The default now lands here, at the one seam every
    turn passes through after the parser (and after the recall re-parse) and before the
    access read; `lane_parse_output` no longer carries its own copy. The team half of the
    routing pair stays in `lane_parse_output`, because its chain reads the pending offer
    and the prior session, which are lane inputs, not a verdict fact.

    SRTSC07 (prod transcript, 22 Sep 2026): the agent half used to stop here, at the
    hard default, while the team half two lines below (`lane_parse_output`) carried
    an accepted offer's team forward. `pending` is the SAME extra input that chain
    already reads, added here as a keyword so every existing one-arg caller keeps
    working unchanged. Precedence, in order: a NAMED agent (this turn's own, from the
    parser - never overridden); the accepted option's agent off `pending`
    (`_accepted_pending_agent`, the customer picked it THIS turn); the hard default,
    last.

    SRTSC07 review round 1, SHOULD-4: a `session=` keyword and its own
    `_prior_suggested_agent` fallback (`variables.routing.suggested_agent`, one turn
    back) used to sit between the two - removed. No writer in this codebase ever
    produces that nest for the agent half: `turn/tail.py`'s own five session keys hold
    no `variables.routing` at all, and `overwrite_for_contact` replaces the whole
    block rather than merging into a legacy nest. `_prior_suggested_team` keeps its
    own copy of that read because a REAL writer exists for the team half (measured:
    reviewer round 1 found none for the agent half) - the two chains are allowed to
    differ in length for that reason.
    """
    out = dict(verdict)
    routing = dict(out.get("routing") or {})
    if not routing.get("suggested_agent"):
        routing["suggested_agent"] = _accepted_pending_agent(pending, out) or DEFAULT_SUGGESTED_AGENT
    out["routing"] = routing
    _report_status_means_order_domain(out)
    return out


def _report_status_means_order_domain(out: dict[str, Any]) -> None:
    """A REPORT status word always routes to the `order` domain. Mutates `out`.

    Main's finding 3(b) (owner live testing, 19 Sep 2026,
    PLAN-chatbot-sales-report.md), re-homed: it lived in the retired
    `head/output_exchange._post_process`, and this is the seam that now normalises a
    fresh verdict before anything reads it. The measured emission for "Srt5674 August
    total sale quantity" parsed the report status and still named `master_products`,
    and BOTH report overrides in `lanes/business/__init__.py::run_fetch` gate on
    `domain == "order"` - so the turn fell through to the product-master listing. The
    same hole swallows an outstanding ask whose domain came back `inventory`.

    Reads ONLY the parser's own structured fields, never the message text, and only
    the status axis - `OUTSTANDING_ORDER_STATUS` is the set `resolve_gate.py`'s own R20
    rule already shares between the outstanding buckets and `sales_report`, so this
    extends one existing normalisation rather than adding a second.
    """
    from app.services.chatbot.lanes.business import _LOW_STOCK_INTENT
    from app.services.chatbot.lanes.business.resolve_gate import OUTSTANDING_ORDER_STATUS

    if jsc.js_string(out.get("intent_hint") or "").strip() == _LOW_STOCK_INTENT:
        # The ONE intent with a tool override of its own, and `run_fetch` already ranks
        # it ahead of both report overrides (`intent_hint == _LOW_STOCK_INTENT` is its
        # first branch). "reorder report" / "what is below level" records
        # `order_status: "outstanding"` off its own words with `domain_hint:
        # "inventory"`, and flipping the domain here sent three recorded low-stock
        # turns to the order lane (`test_turn_replay.py`'s `handbuilt-lsr-001`,
        # `-lsr-002`, `handbuilt-rp-003`: `pending options ['stock'] -> ['orders']`).
        # Same precedence, stated at both seams.
        return
    status = jsc.js_string(out.get("status") or "").strip()
    if status not in OUTSTANDING_ORDER_STATUS:
        status = jsc.js_string(out.get("order_status") or "").strip()
    if status not in OUTSTANDING_ORDER_STATUS:
        return
    prior = out.get("domain_hint")
    if prior == "order":
        return
    # A message that names SEVERAL domains ("stock and outstanding for 7445") states its
    # own list and this rule is not about it: `asks` is what the plan fans out on, and
    # rewriting the single hint under it would drop the other ask.
    if out.get("asks"):
        return
    out["domain_hint"] = "order"
    out["domain_corrected"] = f"{prior}->order (order_status {status})"


def lane_parse_output(
    verdict: dict[str, Any],
    *,
    focus: Focus | None = None,
    pending: Pending | None = None,
    domain: str | None = None,
    accepted_team: str | None = None,
    accepted_assignee: str | None = None,
    accepted_company: str | None = None,
    declined_offer_copy: bool = False,
    prior_session: Any = None,
    policy: Any = None,
) -> dict[str, Any]:
    """`ctx.parse.output` for the kept lanes, projected from the v3 verdict.

    The verdict travels VERBATIM but for four projections, each of which exists because
    a kept reader asks its question in the pre-rearch vocabulary:

    * `order_status` - the order tools' one bucket, from `document` + `status` (D6);
    * `domain_hint` - pinned to the domain being fetched, so a two-domain fan-out picks
      the right tool per leg instead of the first domain's tool twice (contract 122);
    * `routing.suggested_team` / `routing.suggested_agent` - the retired
      `output_exchange` chain never emitted a null routing pair (`DEFAULT_SUGGESTED_TEAM`
      / `DEFAULT_SUGGESTED_AGENT`, `contracts.py`), which is what let the escalation
      lane's round-robin draw assume both were always populated (`_next_assignee_body`
      400s on neither). Reproduced here, ONE LAYER UP from that lane - `escalation.py`
      itself keeps NO guard of its own (H27, deliberately: `test_s5_escalation_lane.py`'s
      `test_no_team_clarify_on_live_team_flows_through_unguarded` and
      `test_two_staff_with_the_same_name_in_different_teams_clarifies_instead_of_guessing`
      both call `escalation.run()` directly with a hand-built ctx and pin a null/inherited
      team flowing through UNGUARDED - the default belongs to the layer that builds
      `ctx.parse.output`, not to the lane that reads it). Chain, in order (owner ruling
      23 Sep 2026, R9, satisfied here by `accepted_team` alone - see the S10 note
      below): the team an ACCEPTED offer just named (`apply`'s `trace.team`, the
      option the customer picked off the roster - it outranks the rest because they
      picked it THIS turn, and a multi-team offer's own `pending.team` stays null
      until they do); a NAMED team (this turn's own, read straight off the verdict);
      THIS TURN's own domain (`policy.domain(domain_hint).escalation_team_code`,
      R6/R7 - a parser emission naming no team gets the domain's real escalation
      team, a deterministic fact about the question just asked, read before a stale
      carry from a PREVIOUS turn, AC-EQ-16/17/20); a PREVIOUS turn's own carried
      routing (`_prior_suggested_team`, test_pass4_item5's B3 - "the carried team
      when a previous turn had one, else the table's default" - still applies when
      THIS turn names no resolvable domain at all, AC-EQ-19); the hard default
      (`DEFAULT_SUGGESTED_TEAM`), last of all, for a domain `policy` cannot resolve
      (no `policy` in scope, or a domain with no escalation row) and no prior carry
      either. The parser's own answer stays untouched on `_parser_raw`, which is
      what `escalation._parser_team` reads to tell "this turn named a team" from
      "this turn accepted one".

      **S10 (fix round 6, "simplest thing that works"): there is no separate OPEN-
      OFFER arm here any more.** R9 (23 Sep 2026) ruled that an open offer's team
      wins only on an actual acceptance - but `turn/apply.py`'s own `_answer_pending`
      (:285, :675) ALREADY computes exactly that judgement and stamps it onto
      `trace.team` the moment `decide()` calls a turn an acceptance
      (`answer_pending_accept`), and `engine.py` already hands `trace.team` in here
      as `accepted_team` (the very first read above). A `pending.team` arm gated by
      its own copy of the same accept signal (`_pending_offer_answered`, added in
      fix round 5, deleted here) was judging the SAME fact TWICE - reviewer kill
      test, fix round 6: deleting both the arm and the helper left the full suite
      green except two unit tests (AC-EQ-21/22) that called this function directly
      with `pending=` but no `accepted_team=`, a shape `engine.run_turn` never
      actually produces (a real acceptance always sets `accepted_team`). Those two
      tests are re-pointed to pass `accepted_team`, the production shape, instead.
      AC-EQ-18 (a fresh, unaccepted question over an open offer) is byte-identical
      to AC-EQ-20 once the arm is gone - folded into it, not duplicated.
    """
    out = dict(verdict)
    if domain:
        out["domain_hint"] = domain

    document = list(out.get("document") or [])
    if focus is not None and not document:
        document = list(focus.document)
    status = out.get("status") or (focus.status if focus is not None else None)
    if status:
        key = (",".join(sorted(document)), status)
        out["order_status"] = _DOCUMENT_STATUS_TO_ORDER_STATUS.get(key, status)
    else:
        out.setdefault("order_status", None)
    # Contract 38: an outstanding ask that named NO document is asked which one before
    # anything is fetched. The lane's gate reads this flag and adds the grant check it
    # is the only place able to make; the flag itself was set by the retired
    # `head/output_exchange._post_process`, and this is the seam that now knows the same
    # fact - it is the one computing the document axis, from the same two fields.
    # Read off the PROJECTED bucket, not off `status` alone: `("", "outstanding")` is
    # the one bucket in the map above that means "outstanding, no document named", and
    # reading it here keeps this true for a caller still speaking the pre-rearch
    # vocabulary (`order_status` straight on the verdict, no `document`/`status` pair).
    out["outstanding_scope_ask_candidate"] = (
        jsc.js_string(out.get("order_status") or "").strip() == "outstanding"
    )
    # PLAN-chatbot-sales-report.md S4 wiring point 2: the sales report's channel, off the
    # FOCUS when this message named none of its own. The turn that answers a customer
    # picker ("1") or a detail offer ("1") types no channel word, and a re-run that
    # dropped it would count every channel under a header still saying "Dealer". One
    # carry, one seam - the same place the document/status projection is made - rather
    # than a second session key the two could disagree about.
    if not out.get("sales_channel") and focus is not None and focus.sales_channel:
        out["sales_channel"] = focus.sales_channel
    # PLAN-po-spo-warehouse-29sep S5: the PO/SPO sort axis, a default, never an override.
    if not out.get("sort_by") and focus is not None and focus.sort:
        out["sort_by"] = focus.sort.get("by")
        if not out.get("sort_dir"):
            out["sort_dir"] = focus.sort.get("dir")
    # PLAN-chatbot-top-x-hot-selling-24sep.md "Lane wiring (S4)" point 8: the top
    # selling ask's axes, off the FOCUS (`turn/apply._top_selling_rules` already laid
    # this turn's own values over the carried ones). One key, read by
    # `lanes/business._fetch_semantic_input`.
    if (
        jsc.js_string(out.get("order_status") or "").strip() == "top_selling"
        and focus is not None
        and focus.top_selling
    ):
        out["top_selling"] = dict(focus.top_selling)
    # Fix lane round 8 (owner hand test, 28 Sep 2026): an outstanding ask after a ranking
    # is the ORDINARY outstanding ask with the ranked codes as its products
    # (`apply._hop_to_report` wrote them on the hop). The same carried keys an answering
    # turn sends (`outstanding_carry`), so the lane's outstanding override, its scope
    # question and its header read them the way they read any carried subject.
    hop = focus.top_selling.get("hop") if focus is not None and isinstance(focus.top_selling, dict) else None
    hop_codes = [c for c in (hop.get("product_codes") or []) if c] if isinstance(hop, dict) else []
    if hop_codes and not out.get("outstanding_carried_product_code"):
        out["outstanding_carried_product_code"] = hop_codes[0]
        if len(hop_codes) > 1:
            out["outstanding_carried_product_codes"] = list(hop_codes)

    routing = dict(out.get("routing") or {})
    if accepted_team:
        # R9 is satisfied here: `accepted_team` IS the open offer's team whenever
        # THIS turn genuinely accepted one - `turn/apply.py::_answer_pending` only
        # stamps `trace.team` (what `engine.py` passes in as `accepted_team`) on the
        # `answer_pending_accept` rule, the acceptance judgement itself. A turn that
        # did not accept anything passes `accepted_team=None` and falls straight
        # through to the domain fill below - see S10 in this function's own
        # docstring for the fix round 6 history (a redundant second arm, judging the
        # SAME fact off `pending` directly, used to sit here and was deleted).
        routing["suggested_team"] = accepted_team
    if not routing.get("suggested_team"):
        # Owner ruling 23 Sep 2026, R7 (AC-EQ-16/17/19), supersedes R6's own
        # ordering (22 Sep 2026). Chain, in order: an ACCEPTED offer's team (above);
        # THIS TURN's domain (`policy.domain(domain_hint).escalation_team_code`,
        # `turn/policy_rows.py`) - a deterministic fact about the QUESTION the
        # parser was just asked, read before the prior-turn carry because a turn
        # that named a real domain is a fresher fact than whatever team a stale
        # session happened to be carrying (AC-EQ-16/17: an incoming ask carrying
        # "purchasing" forward must not paint the NEXT turn's plain stock question
        # "purchasing" too - AC-EQ-20 is the same shape for an unanswered open
        # offer, which reaches this same fallback since it named no `accepted_team`
        # either); a PREVIOUS turn's own carried routing (`_prior_suggested_team`,
        # test_pass4_item5's B3) - still applies when THIS turn names no resolvable
        # domain at all (AC-EQ-19, `policy.domain(None)` is `None`); the hard
        # default (`DEFAULT_SUGGESTED_TEAM`), last of all.
        domain_row = policy.domain(out.get("domain_hint")) if policy else None
        domain_team = domain_row.escalation_team_code if domain_row else None
        routing["suggested_team"] = (
            domain_team or _prior_suggested_team(prior_session) or DEFAULT_SUGGESTED_TEAM
        )
    # `suggested_agent`'s default is applied once, upstream, by `with_routing_agent_default`
    # (finding 2b) - the access read and the lanes see the same value.
    out["routing"] = routing
    # AC-1700: a POSITION over an accepted `member_offer` names that SPECIFIC member
    # (`apply`'s `trace.assignee`) - `lanes/escalation.py::escalation_context` reads
    # this exact key (`output.escalation.preferred_assignee_id`), the retired
    # `head/output_exchange`'s own field, now written by the seam that replaced it.
    # `None` (a bare "yes") leaves whatever the verdict already carried untouched.
    if accepted_assignee:
        out["escalation"] = {**(out.get("escalation") or {}), "preferred_assignee_id": accepted_assignee}
    # The company half of the same idiom (hand pass 11, blocker 2): a POSITION over the
    # company clarify names a COMPANY the customer just picked, and `escalation_context`
    # resolves a named company through ONE key - `output.escalation.company_pick`, which
    # it then validates against the offered pool (`escalation.py:232-255`). Writing the
    # picked option's name here means the position and the typed word reach routing
    # through the SAME seam instead of two, and the validation still runs on both. The
    # verdict's own `company_pick` (the customer typed the name) outranks it: this only
    # fills what the message itself did not say.
    if accepted_company and not (out.get("escalation") or {}).get("company_pick"):
        out["escalation"] = {**(out.get("escalation") or {}), "company_pick": accepted_company}
    # Round 4 (owner-approved, 22 Sep 2026): the brand half of the SAME acceptance
    # carry the agent uses - `_accepted_pending_brand` shares `_accepted_pending_
    # field`'s gate/accept-check with `_accepted_pending_agent`, so an unaccepted
    # turn carries no brand either. `escalation_context`'s own new `carried_brand`
    # rung reads this key back (`lanes/escalation.py`), after its picked-member/
    # company-pick/same-team arms - a SPECIFIC row's own brand there must still
    # outrank a generic carry, which is why this writes a NEW key rather than
    # touching `company_pick`/`preferred_assignee_id` at all.
    carried_brand = _accepted_pending_brand(pending, verdict)
    if carried_brand:
        out["escalation"] = {**(out.get("escalation") or {}), "carried_brand": carried_brand}
    # AC-1703's tail, captain's ruling 20 Sep 2026: a decline over a NON-escalation
    # offer (a did-you-mean roster's own attached escalate sentence, a detail offer)
    # finishes on the SAME `escalation_declined` branch kind as an actual escalation
    # decline - `apply`'s `trace.lane == "offer_declined"` is the only place that
    # knows which of the two this turn was, so it is carried here rather than a
    # second branch kind, the same "one flag on ctx.parse.output" idiom
    # `preferred_assignee_id` just above uses. `escalate_catalog` reads it back to
    # choose R22(a)'s own registry copy over the generic "Escalation declined." line.
    if declined_offer_copy:
        out["chatbot_declined_offer_copy"] = True
    return out


# --------------------------------------------------------------------------- #
# Stage E: resolve, then the tool
# --------------------------------------------------------------------------- #


# `_token_key`: the key the resolver's OWN answer is filed under, for a join against
# it. Separators are REMOVED, not just lower-cased: `resolve_gate.resolve_entity_body`
# sends a product entity through `_PRODUCT_FOLD` (`[-\s]+`) before the resolver ever
# sees it, so a token the customer typed as `SRTWT165-FT` comes back in
# `unresolved_tokens` as `SRTWT165FT`. Keying one side with the hyphen and the other
# without silently failed the join for EVERY hyphenated code - measured on turn
# bb921451 (hand pass 7, B1): `unresolved_tokens: ["SRTWT165FT"]` against an entity
# whose `raw` is `SRTWT165-FT` gave `unplaced == {}`, so the "I could not find X"
# sentence and the unfiltered-catalogue guard (`_without_guesses`) were both dead for
# exactly the codes that need them most. Sorento product codes are hyphenated far more
# often than not.
#
# Hand pass 12 Phase 3 finding F8: this used to be its own copy of `turn.state.
# token_key`'s exact body (`turn/reconcile.py::_key` carried a second one) - moved
# there, beside `fold_token` itself (the ONE copy `turn.narrow._token_of`, R1, also
# uses), and re-imported here under its own established name so every other module's
# docstring reference to `turn_runtime._token_key` still resolves.
_token_key = token_key


def _entity_token_key(entity: dict[str, Any]) -> str:
    """`_token_key` of whichever of the three names a row spells its code under - the key
    to join a plan/compatible row against `unplaced_tokens`."""
    return _token_key(_code_of(entity))


def _entity_token_key(entity: dict[str, Any]) -> str:
    """`_token_key` of whichever of the three names a row spells its code under - the key
    to join a plan/compatible row against `unplaced_tokens`."""
    return _token_key(_code_of(entity))


def _entity_is_unplaced(entity: dict[str, Any], unplaced: dict[str, str]) -> bool:
    """Does this entity carry NOTHING real - no uuid of its own, and its own token key
    is one the resolver could not place THIS turn?

    Hand pass 12 Phase 3 finding F10 (AC-1704 regression): a roster PICK already gives
    its built entity a real `uuid` (`turn/apply.py::_answer_pending`) straight from the
    option the customer chose - by TYPING BACK the roster's own offered code, exactly
    like a position pick. The resolver is independently asked about the SAME raw text
    this message typed (`with_carried_entities` hands over the message's own entities
    untouched, never the focus carry, whenever the message names any at all) and can
    report that exact text as unplaced - the two facts are not in conflict, a pick
    settled it and a fresh, parallel resolve of the same text found nothing new. The
    two `would_be_unfiltered`/`_answered_unfiltered` guards below used to read ONLY the
    token-key membership, so an entity that already carried a real uuid was still
    counted as "nothing placed" the moment its own text also failed the resolver,
    which silently dropped the second tool call a continued roster pick needs.
    """
    return not entity.get("uuid") and _entity_token_key(entity) in unplaced


def unplaced_tokens(entities: list[Any], resolved: Any) -> dict[str, str]:
    """The tokens THIS message named that the resolver could not place, lower-cased.

    `unresolved_tokens` is the resolver's own verdict and the only honest one: a token it
    lists there has no match, whatever it may also have returned under that token's name.
    Measured on turn 7cf56afe ("stock wc287 wc2867 7445"): `wc2867` is in
    `unresolved_tokens` and the same resolution carries ten `spec_search` rows matched on
    the CLASS word it read out of the whole message ("Sorento water closet."), none of
    which is the thing the customer typed.
    """
    named: dict[str, str] = {}
    for entity in jsc.array(entities):
        raw = jsc.nullish_str(jsc.get(entity, "raw")).strip()
        key = _token_key(raw)
        if key and key not in named:
            named[key] = raw
    missed = {_token_key(x) for x in jsc.array(jsc.get(resolved, "unresolved_tokens"))}
    # Keyed on the folded token, valued with the word the CUSTOMER typed, so the answer
    # can quote it back rather than a normalised copy of it.
    return {key: raw for key, raw in named.items() if key in missed}


def absorbed_tokens(entities: list[Any], resolved: Any) -> dict[str, str]:
    """Typed tokens the resolver answered ONLY with another typed token's exact match.

    Prod turn f0a2 (1 Oct 2026), replayed on the crew copy: "Srtswt3001 / Srtswt3001-gm
    stock". The resolver placed `srtswt3001` on SRTSWT3001-GM (a non-exact match), the
    gate deduped the two tokens onto that one product, and SRTSWT3001 vanished: not in
    `entities`, not in `unresolved_tokens`, so no sentence anywhere named it. A token
    whose every matched code is a DIFFERENT code the customer also typed, and none of
    whose matches is the token itself, has not been answered - it is reported unplaced,
    so the reply says "I could not find SRTSWT3001." rather than nothing.
    """
    typed: dict[str, str] = {}
    for entity in jsc.array(entities):
        raw = jsc.nullish_str(jsc.get(entity, "raw")).strip()
        key = _token_key(raw)
        if key and key not in typed:
            typed[key] = raw
    if len(typed) < 2:
        return {}
    codes_by_token: dict[str, set[str]] = {}
    for resolution in jsc.array(jsc.get(resolved, "resolutions")):
        token = _token_key(jsc.get(resolution, "token"))
        if token not in typed:
            continue
        bucket = codes_by_token.setdefault(token, set())
        for match in jsc.array(jsc.get(resolution, "matches")):
            code = _token_key(jsc.get(match, "canonical_code") or jsc.get(match, "code"))
            if code:
                bucket.add(code)
    out: dict[str, str] = {}
    for token, codes in codes_by_token.items():
        others = set(typed) - {token}
        if codes and token not in codes and codes <= others:
            out[token] = typed[token]
    return out


def companies_by_uuid(resolved: Any) -> dict[str, dict[str, Any]]:
    """R-g (owner hand pass 7, 19 Sep 2026): the resolver's own COMPANY per match, keyed
    by uuid - `{"company_id", "company_name"}`, both stamped by the same pass
    (`entity_resolver._attach_company_info`, which writes the id and the name together).

    Read straight off the RAW resolver payload (never off `gate.py`'s own
    `compatible_entities`/`by_uuid`, which `tests/chatbot/test_replay.py`'s frozen corpus
    compares byte-for-byte; an extra key there broke 100+ recorded fixtures that carry a
    `company_name` on a match with nothing to do with this feature). Flattened the same
    three ways `gate.py::run_gate` flattens its own `flat` list (resolutions, intersection,
    by_entity_type), so a match this module never reads through `compatible_entities`
    still contributes its company - `candidates_by_kind` below only reads the entry for a
    uuid that IS in `compatible`.

    The ID is what ROUTES (`lanes/escalation.py::_next_assignee_body` posts `company_id`
    and nothing else; `external/next_assignee.py` falls back to the CONTACT's company
    when it is absent), so a reader that carries only the name carries nothing routable -
    the hand pass 11 blocker this pair exists to close.
    """
    out: dict[str, dict[str, Any]] = {}

    def _consume(matches: Any) -> None:
        for m in jsc.array(matches):
            uuid = jsc.nullish_str(jsc.get(m, "uuid")).strip()
            company = jsc.get(m, "company_name")
            if uuid and isinstance(company, str) and company.strip():
                out.setdefault(
                    uuid,
                    {
                        "company_id": jsc.nullish_str(jsc.get(m, "company_id")).strip() or None,
                        "company_name": company.strip(),
                    },
                )

    from app.services.chatbot.lanes.business.gate import _flatten_by_entity_type

    for resolution in jsc.array(jsc.get(resolved, "resolutions")):
        _consume(jsc.get(resolution, "matches"))
    _consume(jsc.get(resolved, "intersection"))
    _consume(_flatten_by_entity_type(jsc.get(resolved, "by_entity_type")))
    return out


def company_names_by_uuid(resolved: Any) -> dict[str, str]:
    """`companies_by_uuid`, names only - the shape `candidates_by_kind` has always read.

    Public (reviewer N-a, hand pass 11): `answer_bridge` imported the private name across
    module boundaries, which is a fact about this seam's audience, not about its privacy.
    """
    return {uuid: row["company_name"] for uuid, row in companies_by_uuid(resolved).items()}


def _without_guesses(
    compatible: list[dict[str, Any]], resolved: Any, unplaced: dict[str, str]
) -> list[dict[str, Any]]:
    """`compatible` minus the rows that ONLY a guess matched.

    ONLY when the resolver said it could not place a token this message named. That
    verdict is the whole trigger: "stock for srttwc286" put `srttwc286` in
    `unresolved_tokens` and then returned a SECOND resolution under the token `stock for
    srttwc286` - the whole message, which the resolve route adds as a phrase of its own -
    carrying 200 products at `match_tier: spec_search`, `similarity: 0.0`, and the
    inventory fetch ran for every one of them (turn 11476016).

    A phrase-shaped token is NOT a guess by itself: in AND mode the resolver reports the
    intersection under exactly that shape, and those rows are the named token's real
    answer ("Incoming and stock for 7445" -> the four SRTWT7445 variants,
    `unresolved_tokens` empty). So the phrase test only applies once a named token has
    already been reported unplaced.

    A row a REAL token also matched stays, because then it is that token's answer and the
    guess merely agreed.

    A COUNTED DESCRIBED SET is not a guess either (hand pass 12 R8, owner ruling, turn
    7c39e638 "close couple wc available stock in p trap"): when the resolver's own answer
    carries a `predicate` with a positive `qualifying_total`, the phrase-shaped resolution
    IS that set - `references._emit_spec_matches` emits exactly the qualifying candidates
    `resolve_product_set` counted, over membership the described words actually bound, not
    a similarity reach at a token nothing placed. Turn 11476016's own defect (the
    `similarity: 0.0` catalogue sweep this function exists for) carries no predicate at
    all: `resolve_gate.resolve_entity_body` withholds `require` for a turn that names a
    typed CODE and no class word, so the guard below still fires there unchanged.
    """
    if not unplaced:
        return compatible
    described = jsc.get(resolved, "predicate")
    qualifying = jsc.get(described, "qualifying_total") if isinstance(described, dict) else None
    set_answered = isinstance(qualifying, int) and not isinstance(qualifying, bool) and qualifying > 0
    asked = {_token_key(t) for t in jsc.array(jsc.get(resolved, "tokens"))} - {""}
    placed: set[str] = set()
    guessed: set[str] = set()
    for resolution in jsc.array(jsc.get(resolved, "resolutions")):
        token = _token_key(jsc.get(resolution, "token"))
        # `asked` is only evidence when the answer carries it: a stub or an older
        # recording that lists no `tokens` at all must not turn every resolution it
        # DOES carry into a guess.
        phrase = bool(asked) and token not in asked and not set_answered
        bucket = guessed if (token in unplaced or phrase) else placed
        for match in jsc.array(jsc.get(resolution, "matches")):
            uuid = jsc.nullish_str(jsc.get(match, "uuid")).strip()
            if uuid:
                bucket.add(uuid)
    drop = guessed - placed
    if not drop:
        return compatible
    return [e for e in compatible if jsc.nullish_str(e.get("uuid")).strip() not in drop]


@dataclass(frozen=True)
class ResolveOutcome:
    """The resolver seam's own answer for this turn (AC-1681, PLAN-chatbot-answer-half-
    reattach.md slice R2).

    The first seven fields are `resolve_kinds`'s own long-standing 7-tuple, unchanged in
    shape, order and meaning (see that function's own docstring for what each one is).
    `payload` is new: the raw dict `resolve_gate.run` returned for this turn (`resolved`,
    `gate`, `aggregate`, `tier_gate`, `_exit_kind`), carried through so a later slice can
    read the resolver's REAL gate, its tier-gate outcome and its exit kind without a
    second resolver call. `None` on `resolve_kinds`'s two existing early returns (no
    entities named; the resolver raised) - there is no resolver answer to carry then.
    """

    resolved_kinds: dict[str, dict[str, int]]
    compatible_entities: list[dict[str, Any]]
    predicate: dict[str, Any] | None
    resolved_candidates: dict[str, list[dict[str, Any]]]
    unplaced_tokens: dict[str, str]
    spec_tier: bool
    payload: dict[str, Any] | None


def resolve_kinds(
    db: Session,
    *,
    ctx: dict[str, Any],
    branch_kind: str,
    space_id: str | None,
    dry_run: bool,
    stamp_incoming: bool = False,
    stamp_customer: bool = False,
    stamp_promotion: bool = False,
    stamp_purchase_order: bool = False,
    roster_caps: Mapping[str, int] | None = None,
    enforced_scope: bool = False,
) -> ResolveOutcome:
    """Ask the resolver what each named token actually IS (AC-1527).

    Returns a `ResolveOutcome` whose first six fields are
    `(resolved_kinds, compatible_entities, predicate, resolved_candidates,
    unplaced_tokens, spec_tier)`, where `unplaced_tokens` is
    `{folded token: the word the customer typed}` for every token this message named
    that the resolver could not place, and `spec_tier` is `spec_tier_matched(resolved)` -
    which tier of the ONE product ladder answered, and therefore whether this turn
    renders as a counted set or as a list.

    A seventh field, `unplaced_alternatives`, is gone with its producer (reviewer S3,
    review round): its only consumer, `make_tool_runner`'s `alts_by_token`, went with
    `_alternatives_ask` when production's own did-you-mean took that job back, and the
    resolver's `resolutions[*].alternatives` is still read where it belongs - by
    `miss_suggest.build_suggest_offer`'s own D1 arm.

    The resolver and its gate are
    the KEPT ones (`lanes/business/resolve_gate.py`); what is dropped is its picker half,
    which `turn/narrow.py` now decides from the policy instead.

    A resolver that cannot answer is not a failed turn: reconciliation simply has nothing
    to say, and the entities the parser named stand as they are.

    `stamp_incoming` is the caller's own answer to "is this turn about to ask a product
    roster under incoming?" - the engine knows it from the plan, the gate cannot know it
    at all (see `resolve_gate.probe_incoming`), and it is what earns the extra probe.
    `stamp_customer` is its twin for the customer roster's has/no-DO stamps (owner hand
    pass 2, item 2), and the engine withholds it for an OUTSTANDING ask for R20's reason:
    the probe measures DELIVERED orders, which is the opposite population from the
    outstanding report's own DO block, so the stamp would contradict the answer.
    `stamp_promotion` and `stamp_purchase_order` are the same seam for those two domains'
    product rosters (owner hand pass 3, rows 1 and 7): has promo / no promo, has PO / no
    PO, measured by the domain's own per-product read.

    `branch_kind` decides which `entry` this call makes into `resolve_gate.run`
    (`lanes.business.ENTRY_BY_BRANCH_KIND`, R2) - a real, computed branch_kind, not a
    literal, so a promotion ask reaches the tier-gate arm ("access_check") instead of
    the generic "resolve" every branch used to share.

    `roster_caps` (PLAN-chatbot-answer-half-reattach.md "Roster cap") is
    `{entity kind: chatbot_entity_kinds.roster_cap}`. The engine passes its own read
    of the already-loaded `Policy` object (no second query); a caller with no policy
    of its own leaves it `None` and this function reads `chatbot_entity_kinds`
    directly - either way it is forwarded to `resolve_gate.run` -> `gate.run_gate`,
    the one place a roster is actually cut.
    """
    from app.services.chatbot.lanes.business import ENTRY_BY_BRANCH_KIND
    from app.services.chatbot.lanes.business import pickers
    from app.services.chatbot.lanes.business import resolve_gate
    from app.services.chatbot.lanes.business import services as business_services

    entry = ENTRY_BY_BRANCH_KIND.get(branch_kind, "resolve")
    output_block_for_domain = jsc.get(jsc.get(ctx, "parse"), "output") or {}
    entities = output_block_for_domain.get("entities") or []
    # #1262 slice 9 (F1a), owner ruling 7: a brand-hinted token matching a LIVE
    # brand (name or code, case-insensitive exact) resolves inside the chatbot,
    # never through the shared resolver - no order-domain fan-out to
    # customer/transporter (`entity_resolver._DOMAIN_HINT_EXPANSIONS["order"]
    # ["brand"]`, untouched - it still serves n8n/MCP callers), no reconcile
    # re-type, no roster, no kind pick.
    #
    # Security B1 / SF3 (review round, 26 Sep 2026, two passes): ORDER domain only -
    # the strip ran unconditionally for every domain, which also blinded
    # promotion/inventory turns naming only a brand (`gate.py`'s own brand-grouping
    # code ~1546-1556 and `tier_gate.py`'s own `query_brands` derivation ~207-223
    # both read `parser.get("entities")` for a `hint: "brand"` entity ON PURPOSE).
    #
    # Second pass: dropped from the TOKEN LIST that reaches the resolver only
    # (`resolver_excluded_entity_ids`, threaded through `resolve_gate.run` into
    # `resolve_entity_body`'s own token building) - NEVER from `ctx.parse.output.
    # entities` itself. A mixed order+promotion turn (`branch_kind: "check_promotion"`,
    # `domain_hint: "order"`) enters `resolve_gate.run` at `entry == "access_check"`,
    # which reads `parser = ctx.parse.output` (the SAME object, not a copy) to run
    # `tier_gate` BEFORE resolve-entity is even called - mutating that object here
    # blinded `tier_gate`'s own `query_brands` fallback exactly the same way the
    # domain-unscoped version blinded promotion/inventory, just narrowed to this one
    # mixed shape. `apply()` reads `verdict.get("entities")` directly (never this
    # function's own ctx), so the entity still settles onto `focus.brands` exactly as
    # any other confident entity would (`turn/apply.py::_focus_rules`'s generic
    # per-hint grouping) - untouched by either strip.
    is_order_domain = (
        jsc.nullish_str(output_block_for_domain.get("domain_hint")).strip().lower() == "order"
    )
    live_brands_read = True
    try:
        matched_brands = (
            _brand_hinted_entities_matching_live(db, entities) if is_order_domain else []
        )
    except Exception:  # noqa: BLE001 - a caller handing over a test double with no
        # real session (every `resolve_kinds` test that stubs `resolve_gate.run`
        # entirely and never seeds a live `Brand` table) has no opinion on brand
        # matches either; the token falls through to the shared resolver
        # unchanged, exactly as it did before this slice.
        matched_brands = []
        live_brands_read = False
    resolver_excluded_entity_ids = (
        frozenset(id(e) for e in matched_brands) if matched_brands else None
    )
    # The entities THIS call would resolve against, brand-excluded ids aside - used
    # ONLY for the entity-less early return just below, never handed to the resolver
    # itself as a replacement list (that stays `ctx`'s own, unmutated).
    entities_after_exclusion = (
        [e for e in entities if id(e) not in resolver_excluded_entity_ids]
        if resolver_excluded_entity_ids
        else entities
    )
    # Security B1/S1 (review round, 20 Sep 2026): the `access_check` entry is about the
    # CONTACT, not about anything the message named - `resolve_gate.run` reads the
    # entitlement and runs the tier gate BEFORE resolve-entity is even called, and main's
    # own `run_until_exit` enters there for every `check_promotion` turn whether or not a
    # product was named. Skipping the resolver for an entity-less turn was a rearch-only
    # shortcut, and it cost the two turns that need the entitlement most: a bare "promo"
    # (no product) fell through to `narrow.py`'s entitlement-BLIND tier menu, and the turn
    # that ANSWERS a tier pick (a bare "1", no entity of its own) reached `_tier_gate`
    # with no entitlement to recompose against at all.
    if not entities_after_exclusion and entry != "access_check":
        return ResolveOutcome({}, [], None, {}, {}, False, None)
    if roster_caps is None:
        from app.models.chatbot_policy import ChatbotEntityKind

        try:
            roster_caps = {
                row.kind: row.roster_cap for row in db.query(ChatbotEntityKind).all()
            }
        except Exception:  # noqa: BLE001 - a caller handing over a test double with no
            # real session (every `resolve_kinds` test that stubs `resolve_gate.run`
            # entirely) has no opinion on roster caps either; `gate.run_gate`'s own
            # `legacy_default` then stands, exactly as it does for any other caller
            # that never adopted this column.
            roster_caps = None
    services = business_services.production_services(db)
    try:
        payload = resolve_gate.run(
            ctx,
            entry,
            {"branch_kind": branch_kind},
            services=services,
            space_id=space_id,
            probe_default_start=resolve_gate.default_probe_start(),
            dry_run=dry_run,
            roster_caps=roster_caps,
            resolver_excluded_entity_ids=resolver_excluded_entity_ids,
            account_levels=business_services.account_levels_reader(db),
            # ACCOUNT-LEDGER: an enforced contact's own ledger is never an exact-name sibling.
            account_exact_names=not enforced_scope,
        )
    except Exception:  # noqa: BLE001 - see the docstring: nothing to reconcile, not a failure
        logger.warning("chatbot: the resolver did not answer", exc_info=True)
        return ResolveOutcome({}, [], None, {}, {}, False, None)

    resolved = payload.get("resolved")
    by_token: dict[str, dict[str, int]] = {}
    for resolution in jsc.array(jsc.get(resolved, "resolutions")):
        token = jsc.nullish_str(jsc.get(resolution, "token")).strip()
        if not token:
            continue
        hits = by_token.setdefault(token, {})
        for match in jsc.array(jsc.get(resolution, "matches")):
            kind = jsc.nullish_str(jsc.get(match, "entity_type")).strip().lower()
            if kind:
                hits[kind] = hits.get(kind, 0) + 1

    gate = payload.get("gate") if isinstance(payload.get("gate"), dict) else {}
    compatible = [e for e in jsc.array(gate.get("compatible_entities")) if isinstance(e, dict)]
    unplaced = unplaced_tokens(entities, resolved)
    for key, raw in absorbed_tokens(entities, resolved).items():
        unplaced.setdefault(key, raw)
    compatible = _without_guesses(compatible, resolved, unplaced)
    if is_order_domain and live_brands_read:
        # #1262 fix lane round 3, S5: a brand word that is NOT on the live list, typed
        # beside one that is ("Sorento and XYZ"), must be said back like any other
        # token nothing could place, never filtered away in silence. The live brand
        # narrows the fetch; this one gets the same "could not find" line.
        placed_tokens = {
            _token_key(jsc.get(r, "token"))
            for r in jsc.array(jsc.get(resolved, "resolutions"))
            if jsc.array(jsc.get(r, "matches"))
        }
        matched_ids = {id(e) for e in matched_brands}
        for e in entities:
            if (
                isinstance(e, dict)
                and id(e) not in matched_ids
                and jsc.nullish_str(e.get("hint")).strip().lower() == "brand"
            ):
                raw = jsc.nullish_str(e.get("raw")).strip()
                key = _token_key(raw)
                if key and key not in placed_tokens:
                    unplaced.setdefault(key, raw)
    # The attribute-first `predicate` block (AC-1534): the resolver counted the set the
    # question described, and the count is what the answer's own header says. It rides
    # the gate to the tool trigger, where `fetch.output_structurer` prepends it.
    predicate = gate.get("predicate") if isinstance(gate.get("predicate"), dict) else None
    # The has/no stamps are the PICKER's, and the picker writes them onto its own
    # annotated item (the `offer` exit), not onto the gate it was handed. Fall back to
    # the gate so a turn that never reached the picker still groups its candidates.
    # Every customer option shows the NAME; the code is the fallback, not the default
    # (captain ruling, 16 Sep 2026, browser pass 3 turn 10).
    fill_customer_names(db, compatible)

    annotated = payload.get("annotate_incoming")
    if not isinstance(annotated, dict) and stamp_incoming and compatible:
        # The roster this turn is about to print IS the incoming picker's roster, reached
        # from the other side: the customer switched domain without naming the product
        # again, so nothing was ambiguous and the gate never took its picker arm. Probe
        # the same tool it would have, and annotate a COPY of the gate - `annotate_incoming`
        # writes `escalate_message` onto what it is handed, and the gate the rest of the
        # turn reads must keep saying what the gate said.
        probe = resolve_gate.probe_incoming(
            services,
            ctx=ctx,
            entities=compatible,
            aggregate=payload.get("aggregate"),
            space_id=space_id,
        )
        annotated = pickers.annotate_incoming(copy.deepcopy(gate), probe=probe)
    stamps_from = annotated if isinstance(annotated, dict) else gate

    customer_bases: set[str] | None = None
    if stamp_customer and any(
        jsc.nullish_str(e.get("entity_type")).strip().lower() == "customer" for e in compatible
    ):
        # The roster this turn is about to print IS the customer picker's roster, reached
        # from the other side - nothing was ambiguous to the gate, so it never took its
        # picker arm and the lines went out bare. Same probe, same rule, one annotator
        # (`pickers.customer_bases_with_do`).
        #
        # F2 (R2): the gate's OWN `customer_probe_entities` (built at `gate.py:975-979`)
        # carries the product alongside the customers when the picker arm actually ran
        # and substituted a customer-only `compatible_entities` (`gate.py:864-866`,
        # `1053-1061`) - probing `compatible` there measures every DO for the customer,
        # not just this product's, and can stamp "no DO" on a customer who has DOs, just
        # none for the product actually asked. Falls back to `compatible` when the gate
        # never built one (this turn's own picker arm did not run).
        probe_entities = [
            e for e in jsc.array(gate.get("customer_probe_entities")) if isinstance(e, dict)
        ] or compatible
        probe = resolve_gate.probe_customer(
            services,
            ctx=ctx,
            entities=probe_entities,
            aggregate=payload.get("aggregate"),
            default_start=resolve_gate.default_probe_start(),
            space_id=space_id,
        )
        customer_bases = pickers.customer_bases_with_do(probe)

    product_stamp: tuple[set[str], str, str] | None = None
    wants_product_stamp = (stamp_promotion or stamp_purchase_order) and any(
        jsc.nullish_str(e.get("entity_type")).strip().lower() == "product" for e in compatible
    )
    if wants_product_stamp and not isinstance(stamps_from.get("incoming_by_code"), dict):
        # Rows 1 and 7: the promotion and purchase-order rosters carry the same has/no
        # stamp the incoming roster does, from the same seam. The incoming stamp wins when
        # both are on the table: it is the domain the gate itself measured, and one line
        # cannot say two things. `promotion` first for the same reason the plan lists it
        # first - a turn is asked for one of these domains, not both.
        probe_fn = (
            resolve_gate.probe_promotion if stamp_promotion else resolve_gate.probe_purchase_order
        )
        codes = pickers.product_codes_in(
            probe_fn(
                services,
                ctx=ctx,
                entities=compatible,
                aggregate=payload.get("aggregate"),
                space_id=space_id,
            )
        )
        if codes is not None:
            product_stamp = (
                (codes, "has promo", "no promo")
                if stamp_promotion
                else (codes, "has PO", "no PO")
            )

    return ResolveOutcome(
        by_token,
        compatible,
        predicate,
        candidates_by_kind(
            stamps_from, compatible, customer_bases, product_stamp, company_names_by_uuid(resolved)
        ),
        unplaced,
        spec_tier_matched(resolved),
        payload,
    )


def fill_customer_names(db: Session, entities: list[dict[str, Any]]) -> None:
    """Give every customer row the CRM's own name for it, where the resolver gave none.

    `gate._display_name` can only carry a name the resolver's match already had
    (`display.customer_name` / `debtor_name`), and several probes match a customer
    without one - so a roster printed "1. 300-C043  2. 300-C124  3. 300-C001  4. CHIN
    CHUN HARDWARE SDN BHD - [A/C I]" and asked the reader to choose between three
    account codes nobody has ever typed and one name (browser pass 3, turn 10). The
    names are in `customers`, keyed by the very uuid the match carries, so they are read
    back here - one indexed query, on a path that has already made an LLM call and a
    resolver run - and the roster, the answer header and the report's own `Customer:`
    echo then all say the same thing.

    Read through the ORM (`_customer_echo` reads the same column the same way), written
    onto the row IN PLACE so the fetch's own entities carry it too. A row whose
    `customer_name` is empty keeps its code: falling back to the code is the honest
    answer when there is no name to print.
    """
    from app.models.order import Customer

    wanted: dict[str, list[dict[str, Any]]] = {}
    for row in entities:
        if jsc.nullish_str(row.get("entity_type")).strip().lower() != "customer":
            continue
        display = row.get("display_name")
        if isinstance(display, str) and display.strip():
            continue
        uuid = row.get("uuid")
        if uuid:
            wanted.setdefault(str(uuid), []).append(row)
    if not wanted:
        return
    try:
        found = (
            db.query(Customer.id, Customer.customer_name)
            .filter(Customer.id.in_(list(wanted)))
            .all()
        )
    except Exception:  # noqa: BLE001 - a name nobody could read is a code, not a failure
        logger.warning("chatbot: the customer names did not come back", exc_info=True)
        return
    for customer_id, name in found:
        if not name or not str(name).strip():
            continue
        for row in wanted.get(str(customer_id), []):
            row["display_name"] = str(name).strip()


def pickers_module():
    """`lanes.business.pickers`, imported lazily - `candidates_by_kind` is called from
    tests with a hand-built gate and must not drag the lane in at module import."""
    from app.services.chatbot.lanes.business import pickers

    return pickers


def candidates_by_kind(
    gate: dict[str, Any],
    compatible: list[dict[str, Any]],
    customer_bases_with_do: set[str] | None = None,
    product_stamp: tuple[set[str], str, str] | None = None,
    company_names_by_uuid: dict[str, str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """The resolver's own rows, grouped by entity kind, for the narrower's roster.

    This is what turns "which product do you mean? 1. wc286" into the ten-row roster the
    journey describes: the rows are what the resolver MATCHED, carrying the uuid a pick
    resolves to, and `stamp` is the fact the kept picker probe already measured about
    each one ("has incoming"). Deduped on identity, in the resolver's own order - the
    order the customer will read the numbers in.
    """
    from app.services.chatbot.lanes.business.fetch import is_uuid as is_uuid_value

    stamps = gate.get("incoming_by_code") if isinstance(gate.get("incoming_by_code"), dict) else {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: dict[str, set[str]] = {}
    for row in compatible:
        kind = jsc.nullish_str(row.get("entity_type")).strip().lower()
        # `code` is what the RESOLVER calls it; `canonical_code` / `raw` are what the
        # parser and the focus call it. One row shape reaches here from both.
        code = jsc.js_string(row.get("code") or row.get("canonical_code") or row.get("raw"))
        identity = jsc.js_string(row.get("uuid") or code)
        if not kind or not identity:
            continue
        if identity in seen.setdefault(kind, set()):
            continue
        seen[kind].add(identity)
        # #1262 slice 2 (F1c): `uuid` is a real uuid or nothing - falling back to
        # `code` here is how a kind-pick's printed label ("Sorento (customer)") ended
        # up as a candidate's own `uuid`.
        row_uuid = row.get("uuid")
        built: dict[str, Any] = {
            "raw": code or row.get("raw"),
            "canonical_code": code or None,
            "uuid": row_uuid if is_uuid_value(row_uuid) else None,
            "hint": kind,
        }
        family = row.get("uuids")
        if isinstance(family, list) and family:
            built["uuids"] = list(family)
        # Finding 3 (owner, hand-pass 1): the gate carries the resolver's own human
        # label as `display_name` - for customers only, by `gate._display_name`'s rule,
        # because a customer's `canonical_code` is an ACCOUNT code the customer never
        # typed while a product code IS its name. Carried onto the candidate as `name`
        # so a roster prints "HANLIM TRADING SDN BHD", not "300-H030".
        display = row.get("display_name")
        if isinstance(display, str) and display.strip():
            built["name"] = display.strip()
        # R-g (owner hand pass 7, 19 Sep 2026): the same additive carry as `name`
        # above, off the RAW resolver's own `company_name` (`company_names_by_uuid`,
        # read off `resolved` rather than off `gate.py`'s own `compatible_entities` -
        # see that function's own docstring for why) - `turn/narrow.py::_options` reads
        # it so a code that is a SEPARATE record in two companies stays two options,
        # never merged into one because they share a label.
        company_name = (company_names_by_uuid or {}).get(row.get("uuid") or "")
        if isinstance(company_name, str) and company_name.strip():
            built["company_name"] = company_name.strip()
        if code in stamps:
            built["stamp"] = "has incoming" if stamps[code] else "no incoming"
        elif kind == "product" and product_stamp is not None:
            # Rows 1 and 7: the same line a product carries under incoming, for the
            # promotion and purchase-order rosters - `(codes measured, has, no)`, so the
            # two domains that needed it do not each grow a branch of their own.
            codes_with, has_label, no_label = product_stamp
            built["stamp"] = has_label if _norm_code(code) in codes_with else no_label
        elif kind == "customer" and customer_bases_with_do is not None:
            # Item 2: a customer line says what a product line says - whether the thing
            # it names has anything to show. `None` is "not measured" (the probe failed,
            # or its page saturated) and reads as no stamp at all, never as "no DO".
            base = pickers_module().customer_base(built.get("name") or code)
            built["stamp"] = "has DO" if base in customer_bases_with_do else "no DO"
        grouped.setdefault(kind, []).append(built)
    return grouped


def _norm_code(value: Any) -> str:
    """`pickers._norm`'s own rule, for the one comparison this module makes itself."""
    return jsc.nullish_str(value).strip().lower()


def _spec_window(out: dict[str, Any], spec: FetchSpec) -> dict[str, Any]:
    """The window THIS fetch is for, as the lane's own two keys.

    `FetchSpec.date_window` is the plan's answer to "which dates" - `turn/apply.py` puts
    the focus window on every spec whose domain `takes_date_filter` - and until now it had
    no reader at all: the only projection was `outstanding_carry`'s, which runs on an
    ANSWERING turn alone. So a customer picking "all" off a roster armed inside a question
    the conversation had already dated read `Order date: all`, and the scope question said
    it would search a window nobody had asked for (owner round 8, R21's own chain).

    A DEFAULT, never an override: a window this turn named is already on the verdict and
    stays (N2, the same rule `outstanding_carry` keeps for the answering turn).
    """
    window = spec.date_window if isinstance(spec.date_window, dict) else None
    if not window:
        return out
    if out.get("date_filter_start") or out.get("date_filter_end"):
        return out
    if not (window.get("start") or window.get("end")):
        return out
    out = dict(out)
    out["date_filter_start"] = window.get("start")
    out["date_filter_end"] = window.get("end")
    if not out.get("date_mode"):
        out["date_mode"] = window.get("mode")
    return out


# Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not merged, owner ruling
# 24 Sep 2026) for chatbot-stock-ask-v2 S3.
def _int(value: Any) -> int | None:
    """A quantity as an int, or None for anything that is not one.

    SEC-N4 (#1118 security review, round 1): a digit STRING counts, because a value
    read back off a session row written by an older build (or by hand) is whatever
    JSON carried - and `requested_quantities` is validated at the route, where one
    bad value is a 400 that kills the whole fetch rather than one product. `bool` is
    not a number here: `True` is 1 in Python and a quantity of one is not what a
    boolean meant.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _spec_quantities(
    out: dict[str, Any], spec: FetchSpec, entities: list[dict[str, Any]]
) -> dict[str, Any]:
    """`{product uuid: quantity}` for this fetch, as the lane's own key (D13, D20,
    ported from PR #1118, not merged).

    Two sources, one shape, and this is the one seam where both halves exist. The
    OPEN TASK's own slots win where a task drove the fetch (`turn/task.py::
    StockQtyTask.to_fetch` stamps them on the spec) - they carry quantities this
    message never repeated, which is the whole point of the task. Otherwise it is
    the quantities THIS message stated per entity, joined by CODE to the uuid the
    resolver placed: the first turn of a stock ask has no task yet, because nothing
    has told the engine a quantity is required until the reply says so (D25).
    """
    carried = spec.filters.get("requested_quantities")
    if isinstance(carried, dict) and carried:
        # SEC-N4: coerced here too, not only in `StockQtyTask.to_fetch` - this is the
        # LAST seam before the value becomes a query param, and a caller that built
        # the spec by hand must not be able to 400 the whole fetch with one bad slot.
        coerced = {
            str(key): _int(value)
            for key, value in carried.items()
            if _int(value) is not None
        }
        return {**out, "requested_quantities": coerced} if coerced else out
    by_code: dict[str, int] = {}
    for e in jsc.array(out.get("entities")):
        if not isinstance(e, dict):
            continue
        quantity = _int(e.get("quantity"))
        if quantity is None:
            continue
        # AVAIL-MODE-REPLIES (owner Q2 (a), 2 Oct 2026): a code named twice in one message
        # is one line whose quantities add up ("SRT5674 x 2 ... SRT5674 x 3" asks for 5).
        # Each entity counts once, however many of its names carry the code.
        for code in {
            value.strip().casefold()
            for value in (e.get("canonical_code"), e.get("raw"))
            if isinstance(value, str) and value.strip()
        }:
            by_code[code] = by_code.get(code, 0) + quantity
    if not by_code:
        # D13 lives in ONE place (review round 9, finding 5): `turn/apply.py::
        # _normalise_demand_qty` writes a single named code's top-level `demand_qty`
        # onto the entity itself, before the task step, the narrowing or this seam
        # read anything - so by the time a fetch is built the quantity is always per
        # entity, whichever field the parser happened to fill.
        return out
    quantities: dict[str, int] = {}
    for e in entities:
        uuid = e.get("uuid") if isinstance(e, dict) else None
        if not isinstance(uuid, str) or not uuid:
            continue
        for name in ("code", "canonical_code", "raw"):
            code = e.get(name)
            if not isinstance(code, str) or not code.strip():
                continue
            quantity = by_code.get(code.strip().casefold())
            if quantity is not None:
                quantities[uuid] = quantity
                break
    # AVAIL-MODE-REPLIES rule 5: a typed token that is the prefix of exactly ONE resolved
    # code ("SRTWC287-S" placed SRTWC287-S-150 alone) is that code, so its quantity goes
    # with it. A prefix of several codes is a family, asked as a pick, and carries none.
    resolved_codes = {
        str(e["uuid"]): e.get("code") or e.get("canonical_code")
        for e in entities
        if isinstance(e, dict) and isinstance(e.get("uuid"), str) and e.get("uuid")
        and isinstance(e.get("code") or e.get("canonical_code"), str)
    }
    for token, quantity in by_code.items():
        if any((code or "").casefold() == token for code in resolved_codes.values()):
            continue
        prefixed = [
            uuid for uuid, code in resolved_codes.items() if (code or "").casefold().startswith(token)
        ]
        if len(prefixed) == 1 and prefixed[0] not in quantities:
            quantities[prefixed[0]] = quantity
    return {**out, "requested_quantities": quantities} if quantities else out


def outstanding_carry(
    out: dict[str, Any], focus: Focus, answered: dict[str, Any]
) -> dict[str, Any]:
    """The ANSWERED outstanding question's own subject, as the lane's carried keys.

    Contract 38 and 39: the turn that answers "1" / "2" / "both" types no product, no
    customer and no location of its own - the subject rode in on the FOCUS, already
    resolved, and it has to ride back out on it or the re-run silently widens to every
    customer and every warehouse under a header that says otherwise. Read off `focus`
    and nothing else: the retired head kept a second copy of this on a session key of
    its own (`outstanding_filters`) and the two could disagree about what the question
    had been about.

    Only on an ANSWERING turn (`FetchSpec.filters["outstanding"]`, stamped by
    `turn/apply.py`): D10 says the carried code WINS over re-resolution, and on an
    ordinary turn that would override the resolver's own exact-code pick with whatever
    token the customer happened to type (AC-1119).
    """
    out = dict(out)
    detail = answered.get("detail")
    if detail in ("so", "do", "both"):
        # AC-1138: the same tool, one argument more - the MCP layer swaps in the
        # numbered list. Not a different question and not a different report.
        out["outstanding_detail_pick"] = detail
    elif answered.get("kind") == "sales_report_detail" and detail in SALES_REPORT_GROUP_BYS:
        # AC-SR-28: a sales report drill-down, re-run as `group_by` (`fetch.py`).
        out["outstanding_detail_pick"] = detail

    # EVERY carried product, not the first: "all" over a ten-variant roster settles ten
    # codes onto the focus, and carrying one of them re-ran the report for that one under
    # a header naming it (turn 0a6f0379 then f8ed3b97, 16 Sep 2026).
    #
    # AC-1692's own rule, one layer up: this function's own docstring says the focus
    # rides in "already resolved" - `canonical_code` (D10's own settle,
    # `_settle_question_subject`, or the resolver's own match) is that evidence,
    # `raw` alone is not. The customer loop below never falls back to a raw guess at
    # all; this one used to, and a refinement's own unresolvable product word
    # ("cheaper") rode out as `outstanding_carried_product_code` - the same
    # never-resolved-code-echoed-back bug `_spec_row` fixes at the fetch seam,
    # caught here first since this is where it enters `parse_output`.
    carried_codes: list[str] = []
    for entity in focus.products:
        if not isinstance(entity, dict):
            continue
        code = entity.get("canonical_code") or entity.get("code")
        if code and str(code) not in carried_codes:
            carried_codes.append(str(code))
    if carried_codes:
        out["outstanding_carried_product_code"] = carried_codes[0]
        if len(carried_codes) > 1:
            out["outstanding_carried_product_codes"] = carried_codes
    # #1262 slice 2 (F1c): only a real uuid rides out as a carried customer id - a
    # kind-pick's printed label ("Sorento (customer)") settled onto `focus.customers`
    # must never reach the next turn's `outstanding_carried_customer_ids`.
    from app.services.chatbot.lanes.business.fetch import is_uuid as _is_uuid

    ids = [e["uuid"] for e in focus.customers if isinstance(e, dict) and _is_uuid(e.get("uuid"))]
    if ids:
        out["outstanding_carried_customer_ids"] = ids
    # #1262 slice 9 (F1a) follow-up: the same carry, for the brand the scope question
    # (or an open detail offer) was asked about - already-resolved uuids on
    # `focus.outstanding_brand_ids` (settled by `_settle_question_subject`), never
    # re-resolved (D10).
    brand_ids = [b for b in focus.outstanding_brand_ids if _is_uuid(b)]
    if brand_ids:
        out["outstanding_carried_brand_ids"] = brand_ids
    for entity in focus.warehouse:
        if not isinstance(entity, dict):
            continue
        codes = [c for c in (entity.get("warehouse_codes") or []) if c]
        if codes:
            out["outstanding_carried_warehouse_codes"] = codes
        token = entity.get("raw") or entity.get("canonical_code")
        if token:
            out["outstanding_carried_location_token"] = str(token)
        break  # D5/AC-1105: one location per report

    window = focus.date_window or {}
    if not out.get("date_filter_start") and not out.get("date_filter_end"):
        # N2: the carried window is a DEFAULT, not an override - a pick that narrows the
        # window ("2, but only 2026") is answered over ITS dates, not the old ones.
        if window.get("start") or window.get("end"):
            out["date_filter_start"] = window.get("start")
            out["date_filter_end"] = window.get("end")
            if not out.get("date_mode"):
                out["date_mode"] = window.get("mode")
    return out


def _record_key_rerun_split(
    domain: str,
    verdict: dict[str, Any],
    entities: list[dict[str, Any]],
    resolved: dict[str, dict[str, int]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]] | None:
    """Hand pass 12 round 2, Group C (owner ruling): the rerun's own entities (this
    domain's own RECORD KEY alone, `policy_rows.RECORD_KEY_KIND`), the ones it drops,
    and the WORDS the customer actually typed for the record key (read off `verdict.
    entities` directly, never off the resolved row's own `canonical_code` - a
    shipment resolves to the resolver's own canonical identity, `entity_resolver.py::
    _probe_inbound_shipment`'s `shipment_number`, which is NOT the container number a
    customer typed and the match came in on; measured directly, C1's own turn) - or
    `None` when this fetch is not a record-key-rerun candidate at all.

    `decide.py:427-478`'s own REFINE table is untouched - a combined call still
    carries every carried filter on its FIRST try, exactly as it does today. This is
    the ANSWERING side only, and it reads THIS message's own typed entities (`verdict.
    entities`, `current_message: True`) rather than re-deriving the decision: a typed
    FILTER (customer, product, date, brand - anything whose hint the domain does not
    name as ITS OWN record) never matches `RECORD_KEY_KIND` and so never reruns (C3,
    C4, C6's own green guards). A record key typed ALONE (nothing to drop) or a domain
    with no record key at all both read `None` too - there is nothing to rerun for.

    Hand pass 12 round 4, R4 (owner ruling 5): whether this message's own entity IS the
    domain's record key is decided by what the RESOLVER placed its token as, never by
    what the parser hinted. `resolved` is the resolver's own by-token map
    (`resolve_kinds`'s `by_token`), joined through the ONE fold-aware copy of that join,
    `reconcile.hits_for_token`. Turn 50082c60: a container number on an incoming focus
    was hinted `product` by the parser, the resolver placed it as an `inbound_shipment`
    and reconciliation rewrote the kind, but this gate still read the hint - so the
    combined call's miss stood with no rerun at all, while the correctly-hinted sibling
    turn reran on the shipment alone and answered. The hint is the FALLBACK only, for a
    token the resolver said nothing about, which leaves a resolver-less caller reading
    exactly as it did before. The MIRROR mishint (hinted `inbound_shipment`, resolved
    `product`) still never reruns: a resolved `product` is not this domain's record key,
    so this gate refuses it on the same fact the `keep`/`drop` split below already
    refuses it on.
    """
    record_kind = policy_rows.RECORD_KEY_KIND.get(domain)
    if not record_kind:
        return None

    def _fold(kind: Any) -> Any:
        return EXTRA_KIND_ALIASES.get(kind, kind) if isinstance(kind, str) else kind

    def _typed_the_record_key(entity: dict[str, Any]) -> bool:
        hits = hits_for_token(resolved, entity.get("raw"))
        if hits:
            return any(_fold(kind) == record_kind for kind, count in hits.items() if count)
        return _fold(entity.get("hint")) == record_kind

    current_kind_entities = [
        e
        for e in (verdict.get("entities") or [])
        if isinstance(e, dict) and e.get("current_message") is True and _typed_the_record_key(e)
    ]
    if not current_kind_entities:
        return None

    keep = [e for e in entities if _fold(e.get("entity_type")) == record_kind]
    drop = [e for e in entities if _fold(e.get("entity_type")) != record_kind]
    if not keep or not drop:
        return None
    typed_words = [
        w
        for w in (
            jsc.js_string(e.get("canonical_code") or e.get("raw")).strip()
            for e in current_kind_entities
        )
        if w
    ]
    return keep, drop, typed_words


def _entity_display(entity: dict[str, Any]) -> str | None:
    """The word a rerun's own dropped-filter sentence names a DROPPED row by - its
    code where it has one (a product, an order number carried on the focus with raw
    == code), else the raw a customer typed with no code to resolve to (a customer
    name seeded with no separate code, R12 round 2's own C5 fixture)."""
    value = entity.get("code") or entity.get("canonical_code") or entity.get("raw")
    return jsc.js_string(value).strip() or None if value else None


def _drop_focus_entities(focus: Focus, dropped: list[dict[str, Any]]) -> None:
    """Hand pass 12 round 2, Group C: the carried entity a record-key rerun just
    proved wrong (the combined call missed; the rerun on the record key alone did
    not) must not still ride the focus into the NEXT turn - `focus.after` drops it,
    the same "what the ANSWER was about" rule `turn/apply.py::_narrow_and_plan`
    already applies for a freshly-settled kind (S6d's own comment there), applied
    here for a kind a rerun just SETTLED THE OPPOSITE WAY about.
    """
    codes_by_kind: dict[str, set[str]] = {}
    for entity in dropped:
        kind = entity.get("entity_type")
        code = entity.get("code") or entity.get("canonical_code") or entity.get("raw")
        if isinstance(kind, str) and kind and code:
            codes_by_kind.setdefault(kind, set()).add(str(code))
    for kind, codes in codes_by_kind.items():
        attr = KIND_FIELD_MAP.get(kind)
        # P6 (hand pass 12 Phase 3): `KIND_FIELD_MAP` only names the four plural
        # fields - an "order"/"inbound_shipment"/etc kind lives in `focus.extra`
        # instead (through the SAME `EXTRA_KIND_ALIASES` fold `apply._set_kind_field`
        # writes it through), and used to be silently skipped here, so a dropped
        # extra-bucket entity rode straight into the next turn.
        if not attr:
            extra_key = EXTRA_KIND_ALIASES.get(kind, kind)
            current = focus.extra.get(extra_key)
            if not isinstance(current, list):
                continue
            focus.extra[extra_key] = [
                row
                for row in current
                if not (
                    isinstance(row, dict)
                    and str(row.get("canonical_code") or row.get("code") or row.get("raw")) in codes
                )
            ]
            continue
        current = getattr(focus, attr, None)
        if not isinstance(current, list):
            continue
        kept = [
            row
            for row in current
            if not (
                isinstance(row, dict)
                and str(row.get("canonical_code") or row.get("code") or row.get("raw")) in codes
            )
        ]
        setattr(focus, attr, kept)


def make_tool_runner(
    db: Session,
    *,
    ctx: dict[str, Any],
    verdict: dict[str, Any],
    focus: Focus,
    compatible_entities: list[dict[str, Any]],
    predicate: dict[str, Any] | None,
    unplaced: dict[str, str],
    space_id: str | None,
    dry_run: bool,
    turn_trace: Any,
    counted_set: bool = False,
    resolver_gate: dict[str, Any] | None = None,
    resolver_tier_gate: dict[str, Any] | None = None,
    resolved_kinds: dict[str, dict[str, int]] | None = None,
    policy: Any = None,
    customer_scope: dict[str, Any] | None = None,
    unplaced_suggestions: dict[str, list[dict[str, Any]]] | None = None,
) -> Callable[[str, FetchSpec], dict[str, Any]]:
    """The ONE seam that reaches a tool: `run_fetch` calls it once per `FetchSpec`.

    The tool pick, the argument build, the MCP call and the row structuring are all the
    kept lane's (`lanes/business.run_fetch` -> `lanes/business/fetch.output_structurer`),
    so a domain answers with exactly the rows it answers with today; what is new is that
    the lane is asked once PER DOMAIN, from a plan, instead of once per turn.

    `resolver_gate` is the resolver's OWN gate for this turn (R2, `ResolveOutcome.
    payload["gate"]`) - `gate_reason`, `require_specific`, `customer_probe_entities`,
    `company_team`, `gate_debug`, whatever it carries. The gate handed to
    `lanes.business.run_fetch` starts from it, unchanged, so those fields reach the
    lane for the first time; `compatible_entities` and `predicate` are still set
    exactly as today (the per-domain narrowed spec entities, the counted-set block),
    overriding whatever `resolver_gate` itself carried under those two keys.
    `resolver_gate=None` (no resolver ran) reproduces today's synthetic gate exactly -
    a bare `compatible_entities` key and nothing else.

    `resolver_tier_gate` (PLAN-chatbot-answer-half-reattach.md slice R3, captain ruling
    20 Sep 2026) is the resolver's OWN `tier_gate` output (`ResolveOutcome.
    payload["tier_gate"]`) - the entitled-tiers-and-availability read from the real
    `access_check` entry, the source of the has/no-promotion stamps. It reaches
    `lanes.business.run_fetch` only when NO tier has been settled yet
    (`spec.filters.get("tier")` falsy - no pick has happened): once a tier IS settled,
    today's synthetic `_tier_gate(spec, verdict, focus)` recompose stands, unchanged -
    that is the KEPT lane's own tier x brand entitlement recomposition, not something
    the resolver's raw read replaces.

    `resolved_kinds` is the resolver's OWN by-token map (`ResolveOutcome.resolved_kinds`,
    the same one reconciliation rewrites a mishinted kind from). One reader: the
    record-key rerun gate below, which asks what the resolver PLACED this message's
    token as rather than what the parser hinted (R4, owner ruling 5). `None` (no
    resolver ran) falls back to the hint, exactly as this read behaved before.
    """
    from app.services.chatbot.lanes import business
    from app.services.chatbot.lanes.business import fetch as business_fetch
    from app.services.chatbot.lanes.business import gate as gate_mod
    from app.services.chatbot.lanes.business import services as business_services

    def runner(domain: str, spec: FetchSpec) -> dict[str, Any]:
        page_predicate: dict[str, Any] | None = None
        page_ids: list[str] = []
        carry = spec.filters.get("set_page")
        if isinstance(carry, dict):
            # Security B2: the entitlement comes off the CARRY (what the first answer
            # counted under), never off this turn's verdict - a bare count states no
            # tier, and an empty list is read downstream as "no tier filter at all".
            page_predicate, page_ids = page_the_set(
                db,
                carry,
                size=spec.filters.get("top_n"),
                stock_policy=_contact_stock_policy(db, ctx, space_id),
            )
        # R6 (fix round 2), corrected in fix round 4: `policy` is threaded through
        # so a null `routing.suggested_team` gets a domain-aware fill here too,
        # rather than the flat `DEFAULT_SUGGESTED_TEAM` literal - but this is the
        # DOMAIN RUNG ONLY. Unlike `engine.py`'s own `lane_parse_output` call, this
        # one passes no `pending`/`prior_session`, so the fetch context's own
        # `routing.suggested_team` can never come from an open offer or a carried
        # prior-turn team the way the turn's real `ctx.parse.output` can (measured,
        # fix round 4: case-025 turn 1's fetch context reads "purchasing" off the
        # domain rung while its parse output reads "warehouse" off the open offer -
        # the two genuinely disagree). Not a bug worth chasing today: nothing reads
        # this fetch context's own `routing.suggested_team` - `lane_out` feeds tool
        # ARGS (access levels, date filters, entities), never an escalate sentence
        # or a pending write, both of which read the turn's real `ctx.parse.output`
        # instead. Flagged here so the next reader does not assume parity.
        lane_out = lane_parse_output(verdict, focus=focus, domain=domain, policy=policy)
        lane_out = _spec_window(lane_out, spec)
        answered = spec.filters.get("outstanding")
        if isinstance(answered, dict):
            lane_out = outstanding_carry(lane_out, focus, answered)
            if _offer_picks_the_so_list(answered, lane_out):
                # Owner hand test, 3 Oct 2026: "1" / the "Sales order list" button under a
                # customer-subject outstanding summary is the SO list over that summary's
                # customers and window, not the same report again. A product-subject
                # report keeps its own SO detail rows (AC-1138): the SO list has no product.
                lane_out = {**lane_out, "so_list": True, "outstanding_detail_pick": None}
                # The conversation is on the SO list now: a period typed next continues
                # it (`_asks_for_so_list` reads the carried document), not the summary.
                focus.status = None
                focus.document = ["SO"]
        brand_names: list[str] = []
        ranking = jsc.js_string(lane_out.get("order_status") or "").strip() == "top_selling"
        if domain == "order" and not ranking:
            # A top selling ranking narrows by its own brand (`focus.top_selling`'s
            # `brand_ids`, `engine._top_selling_narrowing`); written onto this carry it
            # outlived the ranking and filtered the next report by it (PR #1273, main
            # merge: "sorento brand" in the ranking sent `brand_ids` to "can show me the DO").
            # #1262 fix lane round 3, B1-r2: the brand is a carried axis like the
            # customer. Resolved once here, sent as is (`run_fetch` takes these ids),
            # written back onto the focus so the next turn carries it, and named on
            # the header off the same ids (the envelope's `brand_names`).
            brand_ids, brand_names = order_brand_filter(db, lane_out, focus)
            if brand_ids:
                lane_out = {**lane_out, "outstanding_brand_ids": brand_ids}
            from app.services.chatbot.lanes.business import typed_brand_words

            if brand_ids or typed_brand_words(lane_out):
                # Round 4, S6: a typed brand off the list ends the carry (ruling 13).
                focus.outstanding_brand_ids = list(brand_ids)
        lane_ctx = {
            **ctx,
            "parse": {**(ctx.get("parse") or {}), "output": lane_out},
            # The contact's customer scope (`engine._customer_scope_gate`), read by
            # `lanes.business.run_fetch`; a lane-only key, never on the engine's own ctx.
            "customer_scope": customer_scope,
        }
        if page_predicate is not None:
            entities = [
                {"uuid": pid, "entity_type": "product", "canonical_code": None}
                for pid in page_ids
            ]
        elif spec.filters.get("task"):
            # Ported from PR #1118 (not merged): an OPEN TASK's own fetch is about the
            # TASK's subjects, all of them (`turn/task.py::StockQtyTask.to_fetch`): a
            # turn answering two of four products resolves only those two, and
            # `_entities_for` below would keep exactly the resolver's two - so the
            # answer would silently drop the two the dealer had already given a
            # quantity for. The task is the only honest record of what the question is
            # about.
            entities = [_spec_row(e) for e in spec.entities]
        else:
            entities = _entities_for(spec, compatible_entities)
        # Hand pass 12, Group F: a multi-ledger customer pick's own entities carry no
        # `display_name` at all (`turn/apply.py::_answer_pending` leaves it off on
        # purpose for an option covering several uuids) - filled in here, the same
        # DB-backed read `resolve_kinds` already does for a freshly-resolved customer
        # (line ~906 above), so the miss header can name each ledger rather than
        # falling back to the option's own rollup code.
        fill_customer_names(db, entities)
        # SO-NUMBER-ASK: SO numbers this message typed that nothing placed are answered
        # by the lane from `sales_orders` (`lanes/business/run_fetch`'s `so_numbers` arm),
        # and so are not "could not find" words for compose to name a second time.
        so_numbers = _so_numbers_asked(domain, verdict, unplaced)
        envelope_unplaced = unplaced
        if so_numbers:
            lane_out = {**lane_out, "so_numbers": so_numbers}
            envelope_unplaced = {k: v for k, v in unplaced.items() if v not in so_numbers}
            # Answered here, so never carried: a word left on `focus.extra["order"]` without a
            # uuid is handed to the resolver again on the next order turn and named as a miss
            # there (owner hand test: "okay how about all my sales order?" replied
            # `Couldn't find: "SO422056"` over the `self_reference` answer).
            _drop_focus_entities(focus, [{"entity_type": "order", "raw": w} for w in so_numbers])
        elif _asks_for_so_list(domain, verdict, focus, lane_out):
            # "all my sales orders" / "my SOs": the SO list over the customers in scope
            # (`lanes/business/run_fetch`'s `so_list` arm). The document is read off the
            # focus too, so a typed period answering "Which period?" runs the same ask.
            lane_out = {**lane_out, "so_list": True}
        # Ported from PR #1118 (not merged), D13/D20: the dealer's own quantity per
        # product, resolved to uuids here - `lanes/business/fetch.py` reads it
        # straight off the lane input.
        lane_out = _spec_quantities(lane_out, spec, entities)
        lane_ctx = {**lane_ctx, "parse": {**(lane_ctx.get("parse") or {}), "output": lane_out}}
        # R2: start from the resolver's own gate (gate_reason, require_specific,
        # customer_probe_entities, company_team, gate_debug, ...) - `compatible_entities`
        # and `predicate` are still set exactly as today, below, overriding whatever
        # `resolver_gate` itself carried under those two keys.
        gate: dict[str, Any] = dict(resolver_gate) if isinstance(resolver_gate, dict) else {}
        gate["compatible_entities"] = entities
        block = page_predicate if page_predicate is not None else predicate
        if block is not None:
            gate["predicate"] = block
        # R3: a tier already settled by a pick keeps today's synthetic recompose; an
        # UNSETTLED tier hands the resolver's own real tier_gate through instead, so the
        # per-tier promotion probe (`lanes.business.run_fetch`'s own tier_ask arm) sees
        # what the contact actually holds rather than nothing at all.
        tier_gate_value = (
            _tier_gate(spec, verdict, focus, resolver_tier_gate)
            if spec.filters.get("tier")
            else resolver_tier_gate
        )
        payload = {"gate": gate, "tier_gate": tier_gate_value, "ctx": lane_ctx}
        # Security B1, FAIL CLOSED: a tier-filtered fetch whose recomposed access
        # levels came out EMPTY must never reach the tool. Downstream an empty list
        # is not "nothing matches", it is "no tier filter":
        # `lanes/business/fetch.py` copies it into the tool args,
        # `api/v1/marketing/promotions.py` hands it to
        # `contact_access_type_service.translate_names_to_codes`, which returns None
        # for an empty name list, and the service reads None as "no access-level
        # filter" - so the contact reads promotions of every tier. It is empty only
        # when the contact holds no entitlement the recompose could keep, which is a
        # genuine absence: answered with the SAME `not_found` fragment the
        # unfiltered-answer guard below hands to production's own miss composer, so
        # the words are production's and no tool call goes out.
        #
        # Security B2, the same rule one seam over: a carried set whose page 1
        # entitlement was never recorded cannot be re-counted honestly, and a page with
        # no ids leaves the fetch with NO entities at all - the most unfiltered read
        # there is. `page_the_set` refuses it; this is where that refusal becomes the
        # same production miss.
        # Hand pass 12 round 3, R6 (owner ruling): a stock/inventory tool must never
        # be called with zero filters when the message named an entity and NONE of
        # them placed - a genuinely unresolved product code (turn 328e8b00,
        # "Srtks8060-BL stock") reached the tool with `entities_in: 1,
        # total_uuids_passed: 0` and answered about the whole catalogue. The SAME
        # facts `_answered_unfiltered` already reads to REWRITE the reply after the
        # tool ran (`unplaced`/`entities`) are read HERE, before it runs, so the call
        # never goes out at all - scoped to inventory/incoming only (never `order`,
        # whose outstanding-report ask filters by CODE, not id, and is narrowed by
        # exactly an unplaced token, `_answered_unfiltered`'s own carve-out).
        #
        # Hand pass 12 round 4, R8 (owner ruling, turn 7c39e638 "close couple wc
        # available stock in p trap"): NO entity at all is the same unfiltered read
        # when the message NAMED tokens and the resolver placed none of them - the
        # two `category`-hinted phrases resolved to nothing placeable, the narrower
        # had no candidate to hand the fetch, and `crm_inventory_stock_balance_list`
        # ran with zero filters over 97 products ("+96 more"). `unplaced` IS the
        # "this message named something the resolver could not place" verdict, so a
        # genuinely entity-less ask ("what's on promotion") names no token, leaves
        # `unplaced` empty and still calls its tool - which is the distinction the
        # old `and bool(entities)` was standing in for, and got wrong for the case
        # where nothing survived to be counted at all.
        would_be_unfiltered = (
            domain in ("inventory", "incoming")
            and bool(unplaced)
            and all(_entity_is_unplaced(e, unplaced) for e in entities)
        )
        # Hotfix 22 Sep 2026 (PLAN-chatbot-stock-no-subject-hotfix-22sep.md, live
        # contact 423729104 turns 59-60): the guard above needs `bool(unplaced)`, and
        # the whole-book read that got through carried NOTHING unplaced. Turn 59's
        # "Srtwc8608-p-rl" resolved only as a `product_set` - a kind
        # `ALLOWED["inventory"]` does not take - so the carry was parked on
        # `focus.extra["product_set"]`, handed back to the resolver on turn 60
        # ("Stock"), left `unplaced` empty either way, and `compatible_entities` came
        # out empty: `crm_inventory_stock_balance_list` ran with zero filters and
        # answered with 50 rows of the catalogue. A bare "stock?" on turn 1 is the
        # same hole from the other side - no entities at all, so the resolver never
        # even runs and `unplaced` is empty for that reason instead.
        #
        # `run_gate` is ASKED the question rather than given a second copy of its
        # tables: an entity-less ask in this domain is exactly what `ALLOWS_EMPTY` /
        # `INTENTS_ALLOWING_EMPTY` already rule on (`inventory` is False on purpose -
        # "a bare 'stock?' must ask which product" - and `low_stock_report` is the one
        # intent exempted from it, PLAN-low-stock-report S6), and its verdict carries
        # both the reason string and the `allowed_lookup` production's own miss
        # composer reads to build the scope-needed sentence. Scoped to `inventory`:
        # the trigger to widen is a SECOND domain measured dumping unfiltered, not the
        # fact that other `ALLOWS_EMPTY` rows are False too.
        #
        # A predicate block IS a filter (the described-set ask, "which taps have
        # stock"), so a turn carrying one is never refused here.
        #
        # Reviewer S1: `would_be_unfiltered` is EXCLUDED, not merely redundant with
        # this. Every entity it refuses is unplaced and therefore uuid-less, so
        # without the exclusion this condition also fires on the R6/R8 refusals and
        # stamps `scope_gate` on them - which re-points their miss text at a gate
        # whose `compatible_entities` is empty and loses the breakdown bullets
        # (`answer.py`'s own per-entity lines), for a turn that is not a scope gap at
        # all: the customer DID name a subject, it just did not resolve.
        # `not_found_error_message` says the same thing in its own `needs_scope`
        # condition (`and not has_unresolved`). Those two refusals keep the
        # resolver's gate and their existing "could not find X" wording untouched;
        # `scope_gate` rides ONLY the hole this hotfix opened the guard for.
        no_subject_gate: dict[str, Any] | None = None
        if (
            domain == "inventory"
            and block is None
            and not would_be_unfiltered
            and not any(isinstance(e, dict) and e.get("uuid") for e in entities)
        ):
            probe_gate = gate_mod.run_gate(
                {},
                parser={**lane_out, "domain_hint": domain, "entities": []},
                resolver={"resolutions": []},
            )
            if probe_gate.get("gate_passed") is False:
                no_subject_gate = probe_gate
        exhausted = page_predicate is not None and bool(page_predicate.get("exhausted")) and not page_ids
        if exhausted:
            # Round 3 W2: every product of the carried set is listed already. No tool call:
            # the reply is the set's own header, closed by "That is all N.".
            from app.services.chatbot.lanes.business import answer as business_answer

            said = business_answer.build_set_header(
                int(page_predicate.get("qualifying_total") or 0),
                0,
                jsc.nullish_str(page_predicate.get("set_noun")).strip() or "products",
                page_predicate.get("require") or {},
                description=page_predicate.get("description"),
                previous_total=page_predicate.get("previous_total"),
                exhausted=True,
            )
            others = business_answer.other_brands_line(
                page_predicate.get("other_brands"), page_predicate.get("require") or {}
            )
            text = f"{said}\n\n{others}" if others else said
            fragment: dict[str, Any] = {"fetch": {"has_result": True, "response": text, "set_header": said}}
        elif (
            (
                spec.filters.get("tier")
                and isinstance(tier_gate_value, dict)
                and not tier_gate_value.get("access_levels_recomposed")
            )
            or (page_predicate is not None and page_predicate.get("entitlement_missing"))
            # A recount that found nothing has no ids to send, and a tool called with
            # no product filter answers about the whole catalogue: a miss, not a call.
            or (page_predicate is not None and not page_ids)
            or would_be_unfiltered
            or no_subject_gate is not None
        ):
            fragment = {
                "fetch": {"has_result": False, "response": business_fetch.NO_RESULT_INTRO},
                "outcome": "not_found",
            }
            if no_subject_gate is not None:
                # The refusal's OWN reason, for `answer_bridge.answer_for`'s miss
                # composer - which otherwise reads the RESOLVER's gate, and the
                # resolver here either never ran (nothing to resolve) or placed the
                # carry perfectly well and has no opinion about scope at all. The
                # words stay `answer.not_found_error_message`'s own `needs_scope`
                # branch; this carries only the gate verdict that branch keys on.
                fragment["scope_gate"] = no_subject_gate
        else:
            fragment = business.run_fetch(
                payload,
                services=business_services.fetch_services(db),
                dry_run=dry_run,
                space_id=space_id,
                trace=turn_trace,
                db=db,
            )
        # Hand pass 12 round 2, Group C (owner ruling): a combined call (a REFINE
        # turn's own carried filter PLUS this message's own record key,
        # `decide.py:427-478`'s table, unchanged) that MISSED reruns exactly ONCE,
        # on the record key alone - through the SAME `business.run_fetch` call the
        # first try used, never a second path around the access/narrowing/scope
        # gate. `page_predicate is None` keeps a `set_page` continuation (its own,
        # unrelated, id-only entities) out of this.
        if page_predicate is None and not (fragment.get("fetch") or {}).get("has_result"):
            # The customer scope's own rows (`engine._scoped_compatible`) are not something
            # the message typed, so they are no "combined call" to split: a rerun on the
            # record key alone would send the same scoped arguments a second time.
            rerun_split = _record_key_rerun_split(
                domain, verdict, [e for e in entities if not e.get("scope")], resolved_kinds
            )
            # P3 (hand pass 12 Phase 3, reviewer S1): the `would_be_unfiltered` guard
            # above is computed once, before the FIRST `run_fetch`, and this rerun
            # never re-consults it - `_record_key_rerun_split`'s own `keep` can carry
            # a record-key entity that never resolved to a uuid at all (a bogus token
            # naming NOTHING real), and the rerun used to fire anyway, a second
            # tool call with zero uuids on it. Nothing to rerun on when no KEPT row
            # carries one.
            if rerun_split is not None and any(
                isinstance(e, dict) and e.get("uuid") for e in rerun_split[0]
            ):
                keep_entities, dropped_entities, record_words = rerun_split
                rerun_gate = dict(gate)
                rerun_gate["compatible_entities"] = keep_entities
                rerun_fragment = business.run_fetch(
                    {"gate": rerun_gate, "tier_gate": tier_gate_value, "ctx": lane_ctx},
                    services=business_services.fetch_services(db),
                    dry_run=dry_run,
                    space_id=space_id,
                    trace=turn_trace,
                    db=db,
                )
                if (rerun_fragment.get("fetch") or {}).get("has_result"):
                    dropped_words = [w for w in (_entity_display(e) for e in dropped_entities) if w]
                    if dropped_words and record_words:
                        note = (
                            f"{', '.join(dropped_words)} does not match "
                            f"{', '.join(record_words)} - dropped, showing "
                            f"{', '.join(record_words)} on its own:"
                        )
                        rerun_fetch = dict(rerun_fragment.get("fetch") or {})
                        original_text = jsc.js_string(rerun_fetch.get("response") or "").strip()
                        rerun_fetch["response"] = (
                            f"{note}\n\n{original_text}" if original_text else note
                        )
                        rerun_fragment = {**rerun_fragment, "fetch": rerun_fetch}
                    _drop_focus_entities(focus, dropped_entities)
                    fragment = rerun_fragment
                    entities = keep_entities
        if not brand_names and _answered_unfiltered(fragment, entities, unplaced):
            # Every subject this fetch had is a token the resolver could not place, so
            # there was nothing to filter by - and a tool called with no filter answers
            # about everything. "stock for srttwc286" reached
            # `crm_inventory_stock_balance_list` with every entity skipped
            # `for_bad_uuid` and replied "5,783 products have stock. Showing 5." (turns
            # 11476016 / 5cf452cd / 9de8ac20, 16 Sep 2026). A miss is the honest answer:
            # the composer names the token and offers the escalation exactly as it does
            # for a code that exists and has nothing to show.
            #
            # Read off the fragment rather than short-circuited before the lane, because
            # the lane answers in more ways than by calling a tool - the gate's own
            # customer and incoming pickers come back from here with no tool at all, and
            # refusing to call it would have swallowed the picker too.
            #
            # PLAN-chatbot-answer-half-reattach.md R4 (Deleted table): this used to try a
            # rearch-only trigram roster of its own (`_alternatives_ask`,
            # `_resolve_dominant_neighbours`) before falling back to a flat miss. That
            # machinery is retired - `outcome: "not_found"` is all this fragment states,
            # so a single-domain business turn's `answer_bridge.answer_for` (via its own
            # `raw_fragment` trigger) can answer it with PRODUCTION's own did-you-mean
            # roster (`miss_suggest.dym_transform` / `dym_annotate`, off the resolver's
            # OWN trigram alternatives on `resolved`), never a second, parallel one.
            fragment = {
                "fetch": {"has_result": False, "response": business_fetch.NO_RESULT_INTRO},
                "outcome": "not_found",
            }
        envelope = envelope_of(
            fragment,
            spec,
            entities,
            # The render follows the TIER that matched (AC-1534): a SPEC-tier answer is
            # the counted set, a code-tier answer is the list, and a carried page is a
            # set by construction.
            counted_set=page_predicate is not None or counted_set,
            denial_text=(
                domain_denial_text(db, domain)
                if fragment.get("outcome") == "access_denied"
                else None
            ),
            ran_with=lane_out,
            unplaced=envelope_unplaced,
            suggestions=unplaced_suggestions,
            raw_fragment=fragment,
            brand_names=brand_names,
            attachment_types=(
                attachment_type_labels(db, entities) if spec.domain == "product_attachment" else None
            ),
        )
        if page_predicate is not None:
            # W4: what an "another N" after this page continues from; after the last page
            # it stays, so a further count says "That is all" (round 3 W2). The engine
            # keeps it on `focus.set_page`.
            envelope["set_carry"] = page_predicate.get("next_carry") if (page_ids or exhausted) else None
        return envelope

    return runner


#: The entity hints that name a CLASS of product rather than one of them. They are what
#: the resolve body forwards as `scope_terms` (`resolve_gate.resolve_entity_body`), which
#: is what a described set is scoped BY (AC-1534).
CLASS_HINTS = ("product_type", "category")

#: The resolver's own name for its last tier: the SPEC/class search it falls back to when
#: the code tiers matched nothing (`api/v1/system/references._emit_spec_matches`). Every
#: other tier - exact, prefix, substring, and, scope - matched a CODE.
SPEC_TIER = "spec_search"


def class_scope_terms(verdict: dict[str, Any]) -> list[str]:
    """The class words the PARSER named this turn, in order, deduped.

    What the described set is scoped BY, read off the verdict's entity hints and never
    off the message words (D1/AC-1520).
    """
    terms: list[str] = []
    for entity in verdict.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        if jsc.nullish_str(entity.get("hint")).strip().lower() not in CLASS_HINTS:
            continue
        raw = jsc.nullish_str(entity.get("raw")).strip()
        if raw and raw not in terms:
            terms.append(raw)
    return terms


def spec_tier_matched(resolved: Any) -> bool:
    """Did this turn's products come from the SPEC tier rather than from a code?

    ONE product lane, one ladder: the resolver tries the code tiers and falls back to the
    spec/class search only when they matched nothing, so "SRTWC286 got stock" and "which
    water closet got stock" walk the same path and only the second reaches the fallback.
    The counted-set answer ("N water closets have stock." over the listed set) is
    simply how a SPEC-tier match RENDERS; a code-tier match renders as the
    list. The render had been following the `require` predicate instead, which
    `predicate.derive_require` builds from the INTENT alone (`check_stock` ->
    `{"stock": true}`) - so it rides every stock, incoming and promotion turn there is,
    and "7445" (matched by code, five real variants) was answered "5 taps have stock."
    with a page describing every product that has stock (turns 92d565a5 / b383d402 /
    2e7ca929, 17 Sep 2026).

    "All of them, and at least one" is the same reading the GATE already earned for the
    same tier (`gate.run_gate`'s C4 branch, AC-1326) and `_strip_word_token_product_
    matches` (AC-1327): a group whose product matches are not ALL `spec_search` is an
    ordinary code answer. Non-product matches are not part of the question.
    """
    products: list[Any] = []
    rows = jsc.get(resolved, "intersection")
    if isinstance(rows, list):
        products = [m for m in rows if isinstance(m, dict) and m.get("entity_type") == "product"]
    else:
        for resolution in jsc.array(jsc.get(resolved, "resolutions")):
            for match in jsc.array(jsc.get(resolution, "matches")):
                if isinstance(match, dict) and match.get("entity_type") == "product":
                    products.append(match)
    return bool(products) and all(m.get("match_tier") == SPEC_TIER for m in products)


def _entitled_names(values: Any) -> list[str]:
    """The access-level NAMES in `values`, trimmed, non-empty, in order."""
    return [v for v in jsc.array(values) if isinstance(v, str) and v.strip()]


def set_page_carry(
    predicate: dict[str, Any] | None,
    spec: FetchSpec,
    scope_terms: list[str],
    *,
    access_levels: Any = None,
    shown: int = 0,
    offer_only: bool = False,
) -> dict[str, Any] | None:
    """The set a too-long counted answer asked about, for `focus.set_page`.

    `offer_only` (owner hand test of rounds 4 to 6 on PR #833, item 6): a set listed in
    full whose reply closed with "Other brands with stock: Cabana 3, Mocha 2. Name one to
    see them." is carried too, only so the brand named next narrows THIS set
    (`with_brand_from_offer`). It never continues as a page.

    No paging (owner ruling, 26 Sep 2026): the carry exists only so the answer to "how
    many should I show?" - the parser's own count key, `top_n` - can list that many of
    the SAME set. The engine writes it only when the rows were withheld (more than
    `answer.SET_LIST_MAX` qualifying, no count named) and clears it on the next turn
    whatever that turn is. `{set_key}` and nothing more: the set is RE-DESCRIBED from
    `set_key` rather than carried as a list of ids, so a session never holds two hundred
    uuids and the answer is counted over live data.

    Never without a scope term: `set_key` describes the population by its `require` leg
    and its class words, so an empty `scope_terms` describes "every product that has
    stock". A spec tier reached with nothing to scope by is a miss, not a set.

    **Security B2 (re-check round, 20 Sep 2026): `access_levels` is part of the
    description, not beside it.** The same `require` leg and the same class words read
    under two entitlements are two different populations, so the levels page 1 actually
    answered under (`lanes/business._fetch_semantic_input`'s own, off
    `tier_gate.access_levels_recomposed`, carried out as the envelope's
    `access_levels_used`) belong INSIDE `set_key`. Without it the recount had nothing
    to count by and fell to the PARSER's `access_levels`, which is empty in 249 of 249
    real captures: `product_predicate_service._access_level_codes` reads an empty name
    list as "no tier filter", so a recount of a promotion set counted and named products
    whose only promotion is restricted to a tier the contact does not hold. The key is
    ALWAYS written, empty list included - a promotion carry with no levels recorded is
    refused by `page_the_set` rather than read as "no restriction".
    """
    # W4 (owner hand test round 2): the resolver's own description of the set it counted
    # (`predicate.set_key`: the bound specs, the brand, the ids LOOKUP matched) is what a
    # page replays, so "10" after "144 Sorento wall hung basins" pages THAT set, never
    # the parser's class word re-read without its brand (which answered 334).
    described = predicate.get("set_key") if isinstance(predicate, dict) else None
    described = described if isinstance(described, dict) else {}
    has_description = bool(described.get("specs") or described.get("brand") or described.get("product_ids"))
    if not predicate or not (scope_terms or has_description):
        return None
    total = int(predicate.get("qualifying_total") or 0)
    offered = [
        {"brand": str(o.get("brand")), "count": int(o.get("count") or 0)}
        for o in (predicate.get("other_brands") or predicate.get("set_brands") or [])
        if isinstance(o, dict) and str(o.get("brand") or "").strip() and o.get("count")
    ]
    if offer_only and not offered:
        return None
    if total <= 0 or (shown >= total and not offer_only):
        return None
    labels = [c for c in (predicate.get("class_labels") or []) if isinstance(c, str)]
    from app.services.chatbot.lanes.business.answer import set_noun_for

    key: dict[str, Any] = {
        "require": predicate.get("require") or {},
        "scope_terms": list(scope_terms),
        "domain": spec.domain,
        "set_noun": predicate.get("set_noun") or set_noun_for(labels),
        "access_levels": _entitled_names(access_levels),
        "total": total,
        "shown": int(shown),
    }
    if has_description:
        key["specs"] = list(described.get("specs") or [])
        key["brand"] = described.get("brand")
        key["brand_default"] = bool(described.get("brand_default"))
        key["product_ids"] = list(described.get("product_ids") or [])
    if offered:
        key["other_brands"] = offered
    if offer_only:
        key["offer_only"] = True
    return {"set_key": key}


#: The verdict key `with_brand_from_offer` sets: the offered brand the message chose.
SET_BRAND_KEY = "set_brand"

#: Words around a brand name that do not change which brand was named ("show me cabana").
_BRAND_PICK_FILLER = frozenset(
    {"show", "see", "me", "only", "the", "please", "pls", "for", "in", "brand", "ok", "okay", "how", "about", "what", "and", "then"}
)


def with_brand_from_offer(verdict: dict[str, Any], message: Any, *, carried: Any) -> dict[str, Any]:
    """The verdict with `SET_BRAND_KEY` set when the message names one of the brands the
    last set answer offered ("Other brands with stock: Cabana 3, Mocha 2. Name one to see
    them.") and nothing else.

    Owner hand test of rounds 4 to 6 on PR #833, item 6: "cabana" answered "1,136 products
    have stock" over every Cabana product, when the offer had said Cabana 3. The brand
    named there narrows the SAME set (`turn/apply.py`, `set_brand_chosen`), so the count
    in the offer and the narrowed answer are one count. A message that names a product,
    a class or any other subject is a new question and is left alone."""
    if not isinstance(carried, dict) or not isinstance(message, str):
        return verdict
    offered = (carried.get("set_key") or {}).get("other_brands") or []
    names = {str(o.get("brand") or "").strip().lower(): str(o.get("brand") or "").strip() for o in offered if isinstance(o, dict)}
    names.pop("", None)
    if not names:
        return verdict
    for e in verdict.get("entities") or []:
        if not isinstance(e, dict) or e.get("current_message") is not True:
            continue
        if str(e.get("hint") or "").strip().lower() != "brand":
            return verdict
    lines = message.strip().splitlines()
    words = [w for w in re.findall(r"[a-z0-9]+", lines[0].lower()) if w not in _BRAND_PICK_FILLER] if lines else []
    said = " ".join(words)
    chosen = names.get(said)
    if chosen is None:
        return verdict
    return {
        **verdict,
        SET_BRAND_KEY: chosen,
        "entities": [e for e in verdict.get("entities") or [] if not (isinstance(e, dict) and e.get("current_message") is True)],
        "reference_positions": [],
    }


def _contact_stock_policy(db: Session, ctx: dict[str, Any], space_id: str | None):
    """The asking contact's stock visibility policy (None for no contact), resolved the
    way the resolver resolves it for the first answer (`references._stock_policy_for`)."""
    contact_id = jsc.nullish_str(jsc.get(jsc.get(ctx, "contact"), "id")).strip()
    if not contact_id:
        return None
    from app.services.field_access import resolve_contact_with_null_workspace_fallback
    from app.services.stock_visibility import resolve_policy

    resolved = resolve_contact_with_null_workspace_fallback(db, contact_id=contact_id, space_id=space_id)
    return resolve_policy(db, resolved or contact_id, space_id)


def page_the_set(
    db: Session,
    carry: dict[str, Any],
    *,
    access_levels: Any = None,
    size: int | None = None,
    stock_policy: Any = None,
):
    """The first `size` products of a carried set: `(predicate, product_ids)`.

    `size` is the count the customer named (capped at `answer.SET_LIST_MAX`, the
    default). `stock_policy` is the asking contact's stock visibility policy, so a
    dealer's recount of a stock set covers only the locations it allows, exactly as the
    first answer's did (`product_predicate_service._leg_stock`).

    The set is re-counted from its own description, which is what makes the carry two
    small values instead of a list - and what makes a page honest when the catalogue
    moved between the two turns.

    **Security B2: the entitlement is part of that description.** The recount runs under
    the levels the CARRY recorded (`set_key.access_levels`, written by `set_page_carry`
    from the levels the first answer's own fetch used), never under anything this turn's parser
    happened to state - a bare count states none, and an empty name list reaches
    `product_predicate_service._access_level_codes` as "no tier filter at all", which is
    how a recount of a promotion set once widened past the first answer. A carry that
    records NO levels is refused (`qualifying_total: 0`, no ids, `entitlement_missing`),
    so the runner answers the miss instead of reading the set unfiltered.

    `access_levels` is for a caller that already knows the entitlement and holds a carry
    written before it was recorded: the first recount under it STAMPS the carry.
    Production's own recount (`make_tool_runner.runner`) passes nothing at all.
    """
    from app.services.chatbot.lanes.business import answer as answer_mod
    from app.services.product_predicate_service import resolve_product_set

    key = carry.get("set_key") or {}
    entitled = _entitled_names(key.get("access_levels"))
    if not entitled:
        stamped = _entitled_names(access_levels)
        if stamped:
            key["access_levels"] = list(stamped)
            entitled = stamped
    # Only the promotion leg is tier-restricted (`product_predicate_service._leg_promotion`
    # is the one leg that reads `access_levels`), so only a set with one needs a recorded
    # entitlement to be recounted honestly; a certificate, stock, incoming or attachment
    # set has none to record and must not be refused for lacking it.
    if not entitled and (key.get("require") or {}).get("promotion"):
        return {
            "require": key.get("require") or {},
            "qualifying_total": 0,
            "truncated": False,
            "unrecognized_terms": [],
            "class_labels": [],
            "entitlement_missing": True,
            "set_noun": key.get("set_noun") or "products",
        }, []
    # W4: a carry that holds the resolver's own description replays it exactly (specs,
    # brand, LOOKUP ids); an older carry falls back to its class words.
    described = "specs" in key or "brand" in key or "product_ids" in key
    outcome = resolve_product_set(
        db,
        require=key.get("require") or {},
        specs=list(key.get("specs") or []) if described else [],
        free_terms=None,
        scope_terms=None if described else list(key.get("scope_terms") or []),
        limit=answer_mod.SET_ID_CAP,
        product_ids=(list(key.get("product_ids") or []) or None) if described else None,
        brand=key.get("brand") if described else None,
        brand_is_default=bool(key.get("brand_default")) if described else False,
        access_levels=entitled,
        stock_policy=stock_policy,
    )
    total = int(outcome.get("qualifying_total") or 0)
    ids = [
        c.get("product_id") or c.get("id")
        for c in (outcome.get("candidates") or [])
        if isinstance(c, dict)
    ]
    ids = [i for i in ids if i]
    limit = answer_mod.SET_LIST_MAX if not size else min(int(size), answer_mod.SET_LIST_MAX)
    # "another N" continues from where the last list stopped.
    offset = max(0, int(key.get("shown") or 0))
    page_ids = ids[offset : offset + limit]
    predicate = {
        "require": outcome.get("require") or key.get("require") or {},
        "qualifying_total": total,
        "truncated": bool(outcome.get("truncated")),
        "unrecognized_terms": [],
        "class_labels": [],
        "set_noun": key.get("set_noun") or "products",
    }
    if offset:
        predicate["offset"] = offset
    if total and offset >= total:
        # Round 3 W2: a count after the last page. Every product was listed already, and
        # the reply says so ("That is all 62.") rather than calling the tool with nothing.
        predicate["exhausted"] = True
    # The count the question was asked over: said again when the page counts another.
    asked_total = int(key.get("total") or 0)
    if asked_total and asked_total != total:
        predicate["previous_total"] = asked_total
    for field in ("description", "row_labels", "brand", "other_brands"):
        if outcome.get(field):
            predicate[field] = outcome[field]
    if outcome.get("certificate_ids"):
        predicate["certificate_ids"] = outcome["certificate_ids"]
    # The next page's carry: the same description, moved past what this page lists.
    # Kept after the last page too (round 3 W2), so a further count says "That is all".
    next_key = {**key, "total": total, "shown": min(offset + len(page_ids), total) if page_ids else offset}
    predicate["next_carry"] = {"set_key": next_key} if total else None
    return predicate, page_ids


def _tier_gate(
    spec: FetchSpec,
    verdict: dict[str, Any],
    focus: Focus,
    resolver_tier_gate: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The tier the narrower already settled, in the shape the kept fetch reads.

    `access_levels_recomposed` is what `lanes/business._fetch_semantic_input` sends to
    the tool as `access_levels`, and that parameter takes access-level NAMES: the route
    (`api/v1/marketing/promotions.py`) translates them through
    `contact_access_types.name` before it overlaps them with the promotion's own JSONB
    of CODES, and an unrecognised name translates to an empty code list, which the
    service reads as "filter by these and there are none" (`marketing_service`'s own
    `q.filter(text("false"))`). Sending the bare tier `"dealer"` therefore answered
    every promotion question with "No matching results found." while 50 dealer
    promotions sat on the clone (browser pass 6, turn 0bd47e62).

    The tier x brand recomposition is the KEPT lane's own rule and it is called, never
    copied (`lanes/business/tier_gate.recompose`): the tier the customer picked plus the
    brands this conversation is about, intersected with the entitlement NAMES the
    envelope carries, is the set of compound levels the CRM understands
    ("dealer" + `["Sorento Dealer", "Mocha Dealer"]` -> both). A tier the contact does
    not hold falls back to their real entitlement, which is the kept lane's own Q23
    reading - answer at what they may see rather than at nothing.

    **Security B1 (review round, 20 Sep 2026): the entitlement comes from the RESOLVER,
    never from the parser's words.** `verdict["access_levels"]` is what the MESSAGE
    stated (`head/parser.py`), and it is empty in 249 of 249 real captures
    (`documentation/plans/chatbot/parser-prompt-inventory.md:106`) - so recomposing
    against it on the turn that ANSWERS a tier pick produced an empty
    `access_levels_recomposed`, which `api/v1/marketing/promotions.py` ->
    `contact_access_type_service.translate_names_to_codes` reads as "no access-level
    filter at all", answering with promotions of every tier. `resolver_tier_gate` is
    `resolve_gate.run`'s own `access_check` output: `name` is the contact's REAL
    entitled compound names and `entitled_tiers` the tiers those map to
    (`lanes/business/tier_gate.py`). A chosen tier outside `entitled_tiers` is dropped
    before the recompose, so a pick off a stale or blind menu cannot widen the read.

    **Security S4 (re-check round, 20 Sep 2026): no resolver tier gate means NO
    entitlement, never the parser's claim.** The verdict used to be the fallback here for
    "a caller with no resolver answer at all", and that left the one exposure B1 was
    about: a message that STATES a tier became the authority whenever
    `resolver_tier_gate` is not a dict, which three production shapes reach - the
    resolver raising (`turn_runtime.py:776-778` logs it and returns `payload=None`), a
    plan naming both `ideate` and `promotion` (the `ideate` entry builds no tier gate,
    `lanes/business/__init__.py:45-49`), and a recount of a carried set (the answer to
    "how many should I show?", `page_the_set`), which runs no resolver. All three now
    recompose to `[]`, and the runner's fail-closed guard above answers the miss.
    """
    tier = spec.filters.get("tier")
    if not tier:
        return None
    from app.services.chatbot.lanes.business.tier_gate import recompose

    resolver_gate = resolver_tier_gate if isinstance(resolver_tier_gate, dict) else None
    entitled = (
        _entitled_names(resolver_gate.get("name")) if resolver_gate is not None else []
    )
    # AC-1698 ("1 and 2", "all"): `narrow_by_tier`'s own settle carries every chosen
    # tier, a list once more than one was picked (a single pick stays the scalar
    # `filter_value` always was) - `recompose` already takes several (`jsc.array`),
    # so every chosen tier reaches ONE fetch's own `access_levels`, never just the last.
    tiers = [t for t in (tier if isinstance(tier, list) else [tier])]
    if resolver_gate is not None:
        held = {
            jsc.js_string(t).strip().lower()
            for t in jsc.array(resolver_gate.get("entitled_tiers"))
        }
        tiers = [t for t in tiers if jsc.js_string(t).strip().lower() in held]
    recomposed = recompose(tiers, list(focus.brands or []), entitled)["access_levels"]
    if not recomposed:
        recomposed = sorted(entitled, key=jsc.js_string)
    return {
        "tier_pick": tier,
        "tier_pick_domain": spec.domain,
        "access_levels_recomposed": recomposed,
    }


def _with_carried_document_types(parse_output: dict[str, Any], focus: Focus) -> dict[str, Any]:
    """The carried document types a product-attachment FETCH turn still needs resolving.

    ATTACHMENT-MULTI (tester re-run 2 Oct 2026, crew trace turn 29a88795): "photo and
    certification for CB11" rostered, the customer replied "3", and the parser echoed the
    picked product as the turn's own entity (`entity_op: reuse`). A turn that names its own
    entities is otherwise handed over untouched, so the carried "photo" / "certificate" -
    stored as the parser's raw words, no uuid, because a roster turn settles nothing - never
    reached the resolver, the fetch dropped both as `missing_or_bad_uuid` and answered with
    every file of every type, and the "has no Certification" line had no types to name.

    Only the document types, only those with no uuid, only when this turn named none of its
    own, and only when the one domain in play is `product_attachment` - any other domain's
    gate would read a document type as an incompatible entity.
    """
    own = [e for e in parse_output.get("entities") or [] if isinstance(e, dict)]
    if any(str(e.get("hint") or "").strip().lower() == "attachment_type" for e in own):
        return parse_output
    domain = parse_output.get("domain_hint")
    domains = [d for d in (getattr(focus, "domains", []) or []) if d]
    if domain != "product_attachment" and not (not domain and domains == ["product_attachment"]):
        return parse_output
    rows = (getattr(focus, "extra", {}) or {}).get("attachment_type") or []
    carried: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("uuid"):
            continue
        code = row.get("canonical_code") or row.get("raw")
        if not jsc.truthy(code):
            continue
        carried.append(
            {
                "raw": row.get("raw") or code,
                "hint": "attachment_type",
                "canonical_code": code,
                "current_message": False,
                "confident": True,
            }
        )
    if not carried:
        return parse_output
    return {**parse_output, "entities": [*own, *carried]}


def with_carried_entities(
    parse_output: dict[str, Any], focus: Focus, *, unsettled_only: bool = False
) -> dict[str, Any]:
    """What the RESOLVER is asked about on a turn that named nothing (contract 33, 35).

    "incoming", typed after an inventory answer about ten SRTWC286 variants, names no
    product at all - so the resolver was never asked, the narrower had only the carried
    rows to go on, and the fetch reached the tool with a token no `*_ids` param could be
    built from. The conversation's own subject is handed over instead: the same entity
    shape the parser emits, flagged `current_message: false` so every downstream reader
    that distinguishes "typed this turn" from "carried" (the low stock prune, the
    outstanding report's typed-code match) still can.

    A turn that names its own entities is untouched - this is the EMPTY case, and the
    case of a turn that names only a document word ("cert?", "photo?"): that word says
    what to answer, not about what, so the subject and its filters are carried beside it
    (fix round 9 on PR #833, the owner's "cert?" after "any gunmetal basin has
    incoming?" answered every certified product).

    `unsettled_only` is the FETCH turn's version of the same question, and the narrower
    reading of it. A carry that already holds a `uuid` is settled: it is in the plan
    already, and re-resolving it is how a stale subject gets back into an answer. A
    carry that holds only a TOKEN is in the plan as a word no `*_ids` param can be built
    from, so the fetch either drops it (promotion, browser pass 6 turn 0bd47e62: "Promo
    for srtwc286" then "1" fetched every dealer promotion, scoped to no product at all)
    or sends the token where a uuid belongs. Those rows, and only those, are handed over
    on a turn that fetches.
    """
    own = [e for e in parse_output.get("entities") or [] if isinstance(e, dict)]
    domain_word_only = bool(own) and all(
        str(e.get("hint") or "").strip().lower() == "attachment_type" and e.get("current_message") is not False
        for e in own
    )
    if parse_output.get("entities") and not domain_word_only:
        return _with_carried_document_types(parse_output, focus) if unsettled_only else parse_output
    carried: list[dict[str, Any]] = []
    # Every kind the focus holds, `extra` included. `KIND_FIELD_MAP` names four kinds
    # and the rest of them live in the catch-all, so an `attachment_type` carried from
    # "photos for srtwc286" was never handed to the resolver, never became an
    # `attachment_type_ids` argument, and the answer to the product pick that followed
    # listed Certification PDFs under a header that said "photo" (turns d77231ed /
    # 2b9aaa96, 16 Sep 2026).
    by_kind: list[tuple[str, list[Any]]] = [
        (kind, getattr(focus, attr, []) or []) for kind, attr in KIND_FIELD_MAP.items()
    ]
    by_kind += [
        (kind, rows or [])
        for kind, rows in (getattr(focus, "extra", {}) or {}).items()
        if isinstance(rows, list)
    ]
    for kind, rows in by_kind:
        if domain_word_only and kind == "attachment_type":
            # This turn's own document word replaces the carried one.
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if unsettled_only and row.get("uuid"):
                continue
            code = row.get("canonical_code") or row.get("raw")
            if not jsc.truthy(code):
                continue
            entity: dict[str, Any] = {
                "raw": row.get("raw") or code,
                "hint": kind,
                "canonical_code": code,
                "current_message": False,
                "confident": True,
            }
            if row.get("uuid"):
                entity["uuid"] = row["uuid"]
            # Fix round 8 on PR #833: a grounded specification keeps its registry key and
            # value, so "cert?" after "any gunmetal basin has incoming?" asks about the
            # SAME gunmetal basins.
            for name in ("spec_key", "spec_value", "spec_unit", "spec_label", "spec_words", "spec_known"):
                if name in row:
                    entity[name] = row[name]
            carried.append(entity)
    if not carried:
        return parse_output
    return {**parse_output, "entities": [*carried, *own]}


def attachment_type_labels(db: Session, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`[{name, keys}]` per attachment-type entity of a fetch, read by uuid (ATTACHMENT-MULTI R3).

    `name` is `attachment_types.type_name`, what the customer reads (R5). `keys` are the
    spellings an answer row's "Attachment Type" field can carry: the MCP presenter prints the
    description when the type has one, else the name. An entity with no uuid, or a failed
    read, yields `[]` - the composer then names no gap rather than a false one.
    """
    ids = [
        jsc.js_string(e.get("uuid"))
        for e in entities
        if isinstance(e, dict)
        and jsc.nullish_str(e.get("entity_type")).strip().lower() == "attachment_type"
    ]
    if not ids or not all(_UUID_TEXT.match(i) for i in ids):
        return []
    from app.models.resources import AttachmentType

    try:
        with db.begin_nested():
            rows = db.query(AttachmentType).filter(AttachmentType.id.in_(ids)).all()
    except Exception:  # noqa: BLE001 - fail closed: no gap line, never a wrong one
        logger.warning("chatbot: attachment_type label lookup failed", exc_info=True)
        return []
    by_id = {str(row.id): row for row in rows}
    out: list[dict[str, Any]] = []
    for type_id in ids:
        row = by_id.get(type_id)
        if row is None or not row.type_name:
            return []
        keys = [k for k in (row.type_name, row.description) if k]
        out.append({"name": row.type_name, "keys": keys})
    return out


def _is_certificate_type(db: Session, attachment_type_id: Any) -> bool:
    """`attachment_types.is_certificate` for the RESOLVED row, never the customer's
    own words. `False` for anything unresolved/unreadable - fail closed to the
    default (`marketing_product`) team rather than mis-route on a lookup that could
    not run (a bad uuid, a row deleted since the resolver matched it).

    Run in a SAVEPOINT (`company_scope.stamp_lookup_companies`'s own convention): a
    non-uuid id (a test double's own placeholder, a resolver row this turn's own
    kind is not really a product/attachment_type at all) aborts the STATEMENT on
    Postgres, and a bare try/except around the query alone leaves the WHOLE caller
    transaction poisoned for every later query this same turn makes - measured
    directly writing this function (`test_rearch_r11_catalogue.py`'s own fixture
    uuid is not RFC 4122 shaped) as a cascade of unrelated `InFailedSqlTransaction`
    errors from every db read after this one.
    """
    if not jsc.truthy(attachment_type_id):
        return False
    from app.models.resources import AttachmentType

    try:
        with db.begin_nested():
            row = db.query(AttachmentType).filter(AttachmentType.id == attachment_type_id).first()
    except Exception:  # noqa: BLE001 - a lookup failure is not a reason to mis-route
        logger.warning("chatbot: attachment_type certificate lookup failed", exc_info=True)
        return False
    return bool(row is not None and row.is_certificate)


def answer_parse_output(
    parse_output: dict[str, Any] | None,
    *,
    gate: Any,
    domains: Any = (),
    db: Session | None = None,
) -> dict[str, Any]:
    """What the ANSWER composers read, which is what the RESOLVER read (AC-1701/AC-1702).

    `lanes/business/__init__.py::complete_answer` takes its `parser` off
    `ctx["parse"]["output"]`, and on the pre-rearch head that object had ALREADY been
    post-processed: `head/output_exchange._post_process` restored the open question's
    own domain onto a bare positional pick, and its section (B) ("product_attachment:
    re-attach attachment_type if the current turn lacks one") put the conversation's
    settled document type back on a turn that named none - carrying the RESOLVED type
    name, not the customer's word for it. A recorded turn's own pending payload still
    shows that shape verbatim (`{"raw": "Product Photos", "canonical_code": "Product
    Photos", "uuid": ...}`, `tests/chatbot/replay_turns/console/focus-015-r-c-photo-for-
    srtwc286-product-pick-keeps-the-attachment-type-no-re-ask.json`), and #750's own
    decision 3 is the same rule for the stamp surface: the noun the customer reads is
    the resolved attachment type, never "photo" / "gambar".

    The re-architecture kept the carry on a SEPARATE `resolver_ctx` and handed the
    composers the bare verdict instead, so `answer.not_found_error_message` saw
    `domain_hint = None` and no attachment_type entity at all. Two live misses came out
    of that one gap: "But no null matched these" after a roster pick (turn
    0f732289-fe38-4a3e-bbd5-d472f32fa524, `jsc.js_string(None)`) and "But no photo
    matched these" when the type WAS named this turn (turn
    bf87f9ef-2ebb-49e8-8b29-bc9edd355eb1), both against a breakdown bullet that already
    printed "attachment_type: Product Photos" two lines above.

    Two narrowings, both safety rather than tidiness. The domain is restored only when
    the turn restated none AND the plan names exactly one - a two-domain plan has no
    single answer, and `turn/compose.py` composes those. A type name is taken only when
    the gate settled exactly one `attachment_type` row: several is still a family, the
    same rule `tail/compile_state.reconcile_entities` applies to a multi-match token.
    """
    out = dict(parse_output) if isinstance(parse_output, dict) else {}
    g = gate if isinstance(gate, dict) else {}
    type_rows = [
        row
        for row in jsc.array(g.get("compatible_entities"))
        if isinstance(row, dict)
        and jsc.nullish_str(row.get("entity_type")).strip().lower() == "attachment_type"
        and jsc.truthy(row.get("code"))
    ]
    type_names = [jsc.js_string(row.get("code")).strip() for row in type_rows]
    if len(type_names) == 1:
        entities: list[Any] = []
        for entity in jsc.array(out.get("entities")):
            if (
                isinstance(entity, dict)
                and jsc.nullish_str(entity.get("hint")).strip().lower() == "attachment_type"
            ):
                entities.append({**entity, "raw": type_names[0], "canonical_code": type_names[0]})
            else:
                entities.append(entity)
        out["entities"] = entities
        # Hand pass 11, defect 2 (owner ruling): a RESOLVED attachment_type row
        # (never a regex over the customer's text, D11) whose own
        # `attachment_types.is_certificate` is true routes this turn's escalation to
        # `purchasing_certification` / `general_enquiries`, deterministically and
        # post-LLM - main's deleted `head/output_exchange.py:85-120` made the same
        # domain+is_cert call, and the v39 prompt line names the SAME team the
        # rendered domain block (`chatbot_domains.escalation_team_code`) then
        # contradicts. Applied HERE, the first and only place this function settles
        # which attachment_type the turn is actually about, so the corrected team
        # reaches the miss offer sentence, the pending roster's own `team` stamp
        # (carried across a did-you-mean pick, contract 108), and the escalation
        # lane on "yes" - all three read `out["routing"]`, never a second copy.
        if db is not None and _is_certificate_type(db, type_rows[0].get("uuid")):
            out["routing"] = {
                **(out.get("routing") or {}),
                "suggested_team": "purchasing_certification",
                "suggested_agent": "general_enquiries",
            }
    if not jsc.truthy(out.get("domain_hint")):
        named = [d for d in (domains or []) if d]
        if len(named) == 1:
            out["domain_hint"] = named[0]
    # Hand pass 9, D4: a bare positional pick ("1") parses as `message_type: "casual"` -
    # correctly, that IS what the words are - but this function's own two callers
    # (`answer_bridge.question_for`/`answer_for`) only ever run on a turn ALREADY routed
    # to the business_query/check_promotion branch (`_run_stages`'s own gate on
    # `branch_kind`), so by the time either reads this output the turn IS a business
    # answer, whatever the parser called the customer's one-word reply. Left uncoerced,
    # `answer.crossdomain_zeroset`'s own `message_type != "business_query"` gate read
    # the pick's literal "casual" and switched the whole cross-domain ladder off - an
    # after-a-pick incoming miss never climbed to stock at all (live turn
    # 139f5282-4968-4802-bd11-501de55bca50), where the identical FRESH-ask shape
    # climbs correctly because the customer's own words there really did carry
    # "business_query".
    out["message_type"] = "business_query"
    return out


def _code_of(entity: dict[str, Any]) -> str:
    """The CODE a row carries, whichever of the three names it spells it under.

    `gate.py` renames the resolver's `canonical_code` to `code` when it builds
    `compatible_entities`; the parser, the focus and a picked option all say
    `canonical_code`; a bare parser entity has only its `raw` token. Reading one
    spelling and not the others is what made the match below fall through for EVERY
    spec that named an entity (every compatible row answered `"null"`).
    """
    return jsc.js_string(entity.get("code") or entity.get("canonical_code") or entity.get("raw")).strip().lower()


def _so_numbers_asked(domain: str, verdict: dict[str, Any], unplaced: dict[str, str]) -> list[str]:
    """The SO numbers an order ask typed that the resolver could not place - the lane's
    `so_numbers` (SO-NUMBER-ASK). Only when EVERY word this message typed went unplaced:
    a message that also placed a subject of its own keeps today's answer for it."""
    if domain != "order" or not unplaced:
        return []
    from app.services.chatbot import so_status

    typed = [
        jsc.nullish_str(jsc.get(e, "raw")).strip()
        for e in jsc.array(jsc.get(verdict, "entities"))
        if isinstance(e, dict) and e.get("current_message") is not False
    ]
    typed = [t for t in typed if t]
    if not typed or any(_token_key(t) not in unplaced for t in typed):
        return []
    return [unplaced[_token_key(t)] for t in typed if so_status.is_so_number(t)]


def _offer_picks_the_so_list(answered: dict[str, Any], lane_out: dict[str, Any]) -> bool:
    """Did this turn pick the outstanding detail offer's "Sales order list" on a report
    about customers only? A product, brand or location narrows the report in a way the SO
    list cannot, so those keep the report's own SO detail rows (AC-1138)."""
    if answered.get("kind") != "outstanding_detail" or answered.get("detail") != "so":
        return False
    return not (
        jsc.truthy(lane_out.get("outstanding_carried_product_code"))
        or jsc.array(lane_out.get("outstanding_carried_product_codes"))
        or jsc.array(lane_out.get("outstanding_carried_brand_ids"))
        or jsc.array(lane_out.get("outstanding_carried_warehouse_codes"))
        or jsc.truthy(lane_out.get("outstanding_carried_location_token"))
    )


def _asks_for_so_list(domain: str, verdict: dict[str, Any], focus: Focus, lane_out: dict[str, Any]) -> bool:
    """An order ask about sales orders with no SO number and no status word: the SO list
    (owner option (2) on PR #1435). "outstanding" keeps the outstanding report; a message
    that typed a subject of its own (a product, an order number) keeps today's answer."""
    if domain != "order" or jsc.js_string(lane_out.get("order_status") or "").strip():
        return False
    document = [str(d).upper() for d in (verdict.get("document") or focus.document or [])]
    if document != ["SO"]:
        return False
    return not any(
        isinstance(e, dict) and e.get("current_message") is not False and e.get("hint") != "customer"
        for e in jsc.array(verdict.get("entities"))
    )


def _answered_unfiltered(
    fragment: dict[str, Any], entities: list[dict[str, Any]], unplaced: dict[str, str]
) -> bool:
    """Did a TOOL answer this fetch when every subject it had was an unplaced token?

    "Unfiltered" means the tool had nothing to narrow BY. A tool that takes entity ids
    gets none from a token the resolver could not place - every row is skipped
    `missing_or_bad_uuid` - and answers about the whole catalogue. The outstanding report
    is the exception and is named as one: it filters on the product CODE the customer
    typed rather than on an id, states that code in its own scope header, and is
    therefore narrowed by exactly the token in question (`case-071`, `case-058`, whose
    recorded tool args carry `product_code` for a token their own resolver stub reports
    as unresolved).

    An EMPTY entity list is the MOST unfiltered a fetch can be (R1, security review
    N-1): the subject never resolved at all, so there was nothing to narrow BY, whatever
    `unplaced` still names. `entities and not all(...)` is what actually gates the
    non-empty case below - an empty `entities` is falsy and skips that check rather than
    returning False for it, so this one guard clause covers both shapes.

    A linked contact's customer-scope rows (`engine._scoped_compatible`, `scope: True`)
    are not subjects either: the message did not type them, they only bound who the
    answer may be about. SO-NUMBER-ASK (PR #1433 tester note): "status of SO422056"
    left the SO token unplaced, the orders list ran on the dealer's twelve linked
    customers alone, and the reply was a 20-row DO dump closed by "I could not find
    SO422056." A dealer's own customer word never reaches this as unplaced - it is
    answered from the links and never sent to the resolver (AC-CS-12).
    """
    if not unplaced:
        return False
    subjects = [e for e in entities if not e.get("scope")]
    if subjects and not all(_entity_is_unplaced(e, unplaced) for e in subjects):
        return False
    fetched = fragment.get("fetch") if isinstance(fragment.get("fetch"), dict) else {}
    if fetched.get("outstanding_report"):
        return False
    tool = fetched.get("tool") if isinstance(fetched.get("tool"), dict) else {}
    return bool(tool.get("name"))


def _spec_row(entity: dict[str, Any]) -> dict[str, Any]:
    """A plan entity in the shape the gate's own rows use.

    `code` is the name every code reader downstream (`fetch.outstanding_product_codes`,
    the low-stock prune, the report's typed-code match) reads first: a spec entity that
    reaches the tool without it is a product with no code at all.

    AC-1692's own rule, one layer up: `raw` stands in for a missing `code` ONLY when
    something else already confirms this row settled to a real record (a `uuid`) - a
    fully unplaced guess (no `canonical_code`, no `uuid`, `raw` alone) is not a code
    just because nothing else was typed. Measured live: a refinement's own unresolved
    product word ("cheaper") reached this fallback with neither, and every downstream
    code reader then sent it to the tool verbatim as `product_code=cheaper`.
    """
    # #1262 slice 2 (F1c): `uuid` is a real uuid or it is absent - never `canonical_code`
    # promoted into it, which is how a kind-pick's printed label ("Sorento (customer)")
    # ended up in `uuid` here (the diagnosis transcript's own recorded state).
    from app.services.chatbot.lanes.business.fetch import is_uuid as _is_uuid

    raw_uuid = entity.get("uuid")
    uuid = raw_uuid if _is_uuid(raw_uuid) else None
    settled_raw = entity.get("raw") if uuid else None
    return {
        "entity_type": entity.get("hint"),
        "uuid": uuid,
        "code": entity.get("canonical_code") or settled_raw,
        "canonical_code": entity.get("canonical_code") or settled_raw,
        "raw": entity.get("raw"),
        **({"display_name": entity["name"]} if entity.get("name") else {}),
    }


def _entities_for(spec: FetchSpec, compatible: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The resolver's own rows for the kinds it answered for this turn, plus the plan's
    own rows for every kind it did not.

    A kind this spec NARROWED to keeps only what the narrower chose. Every OTHER kind the
    resolver matched passes through untouched: the order domain narrows on customer as
    well as product, and filtering the whole list down to one kind dropped the product and
    the location out of "SRTWT7445 outstanding for Dealer A in IB" - the report then
    printed `Product: all` over a question about one product.

    The second half is the CARRY, and it is what made "i want to see hanlim only customer"
    answer for every product HANLIM ever bought (turn 834ac587, 16 Sep 2026). The
    conversation was about three SRTWC286 variants, the message named a customer, and
    `_focus_rules` kept both halves on the focus exactly as it should - but the resolver
    is only ever asked about the tokens THIS message named, so `compatible` held the six
    HANLIM ledgers and no product at all, and a filter that keeps only resolver rows kept
    only the customer. A kind the resolver did not answer for this turn is a kind whose
    subject was settled on an earlier one, so the plan's own rows are the only evidence
    there is about it, and they are the same rows this function has always fallen back to
    when the resolver answered nothing at all.

    Both halves are KIND-SCOPED to the domain, by the kept lane's own matrix
    (`gate.ALLOWED`, never a second copy). The gate already scopes what it hands back,
    but it scopes by the ONE `domain_hint` the verdict carries, and a bare positional
    ("1") carries none - so on those turns every kind the resolver matched passed through
    to every domain the plan fetched. Measured on the owner's 17 Sep turns: a
    `resource_attachment` ask three minutes earlier left two container-status
    spreadsheets on `focus.extra["attachment"]`, and the "1" that answered a product
    roster fetched incoming AND stock under the header "*incoming stock* for 14.09.2026
    Container Status 2026.xlsx, 11.09.2026 Container Status 2026 MOCHA.xlsx, cb2805q"
    (turns 725e39fd / 543b9a02). The plan knows the domain per fetch, which is what the
    gate could not - so the same matrix is applied here, per spec.
    """
    from app.services.chatbot.lanes.business.gate import ALLOWED

    allowed = ALLOWED.get(spec.domain)
    if allowed is not None:
        compatible = [e for e in compatible if e.get("entity_type") in allowed]
    kinds = {e.get("hint") for e in spec.entities if e.get("hint")}
    codes = {_code_of(e) for e in spec.entities}
    picked = [
        e
        for e in compatible
        if e.get("entity_type") not in kinds
        or not codes
        or _code_of(e) in codes
    ]
    answered = {e.get("entity_type") for e in picked}
    return picked + [e for e in map(_spec_row, spec.entities) if e["entity_type"] not in answered]


#: A uuid is an internal identity and never a subject a person reads (the frontend's own
#: "no UUIDs in the UI" rule, here at the seam the answer text is built from): browser
#: pass 2 read `*incoming stock* for 65514803-1609-4fe8-8b60-2e908c8f9bd4:`.
_UUID_TEXT = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.IGNORECASE
)


def _answer_subject(entity: dict[str, Any]) -> str:
    """What the answer's header CALLS this entity - a name, else a code, never a uuid.

    The resolver's own human label wins where it has one (`display_name`, written for
    customers only, `gate._display_name`: an account code is not what the roster printed
    and not what the customer typed). Everything else is its code.
    """
    for key in ("display_name", "name", "code", "canonical_code", "raw"):
        value = entity.get(key)
        if not jsc.truthy(value):
            continue
        label = jsc.js_string(value).strip()
        if label and not _UUID_TEXT.match(label):
            return label
    return ""


def domain_denial_text(db: Session, domain: str) -> str | None:
    """Contract 7's refusal, for a domain the contact is not granted.

    The ONE registered `access_denied` template, rendered through the same
    `canned.field_grant_denied_text` the retired `complete_answer` called - no new
    prose, and no second wording to keep in step. `None` for a domain with no subject
    registered, which leaves the composer's generic denied line as the fallback.
    """
    from app.services.chatbot import copy as copy_mod
    from app.services.chatbot.lanes import canned as canned_lanes
    from app.services.chatbot.lanes.business.answer import DOMAIN_GRANT_SUBJECT

    subject = DOMAIN_GRANT_SUBJECT.get(str(domain or ""))
    if not subject:
        return None
    try:
        return canned_lanes.field_grant_denied_text(copy_mod.resolve(db), subject)
    except Exception:  # noqa: BLE001 - a missing copy row must not fail the turn
        logger.warning("chatbot: the domain refusal copy did not render", exc_info=True)
        return None


def _lane_text_without_withheld_header(
    text: Any, set_header: Any, *, counted_set: bool, set_described: bool = False
) -> Any:
    """#1262 slice 6 (F6a): `header_override` (below) is already withheld when
    `counted_set` is False - this is the SAME withholding for the other carrier.
    `lanes/business/fetch.py`'s report builder bakes the identical `set_header`
    string as the FIRST line of `response`/`lane_text` whenever a predicate rode
    on the ctx, with no `counted_set` check of its own ("got eta" over carried
    products counted 0 against the leftover word "eta" and printed "0 products
    have incoming stock." above the real ETA rows). Stripped by exact prefix, off
    the same `set_header` string `header_override` itself is built from - never a
    guess at the header's shape.

    A header over a DESCRIBED set (`set_described`, integration round 11 on PR #833)
    stays: it is the one intro line of that set's reply ("Certificates found for
    gunmetal wash basins (2).") on a follow-up turn that carries the set, not a
    count over leftover words.
    """
    if counted_set or set_described or not isinstance(text, str) or not isinstance(set_header, str):
        return text
    prefix = set_header.strip()
    if prefix and text.startswith(prefix):
        return text[len(prefix) :].lstrip("\n")
    return text


def _suggestions_by_typed_word(
    unplaced: dict[str, str] | None, suggestions: dict[str, list[dict[str, Any]]] | None
) -> dict[str, dict[str, Any]]:
    """`suggestions` (keyed by the resolver's token) under the unplaced word the customer
    typed for the same token key, with the sentence that opens its list; a token with
    none is left out."""
    if not unplaced or not suggestions:
        return {}
    from app.services.chatbot.lanes.business.answer import did_you_mean_head

    by_key = {_token_key(token): rows for token, rows in suggestions.items() if rows}
    return {
        raw: {"head": did_you_mean_head(raw), "rows": list(by_key[key])}
        for key, raw in unplaced.items()
        if key in by_key
    }


def envelope_of(
    fragment: dict[str, Any],
    spec: FetchSpec,
    entities: list[dict[str, Any]],
    *,
    denial_text: str | None = None,
    ran_with: dict[str, Any] | None = None,
    unplaced: dict[str, str] | None = None,
    counted_set: bool = True,
    raw_fragment: dict[str, Any] | None = None,
    brand_names: list[str] | None = None,
    attachment_types: list[dict[str, Any]] | None = None,
    suggestions: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """The kept lane's fetch fragment as the composer's envelope (AC-1530, AC-1531).

    `ran_with` is the lane input this fetch actually went out with, read for one fact:
    the date window. Whatever put it there - the verdict's own dates, the focus window
    `_spec_window` defaults in, the outstanding answer's carried filters - the answer
    has to say which dates it searched (browser pass 6 item 4, turn 60927579: "This
    month only" reached the tool as 2026-09-01 to 2026-09-30 and the reply header said
    nothing about it, so a customer reading "No matching results found." could not tell
    an empty month from an empty product).
    """
    fetched = fragment.get("fetch") if isinstance(fragment.get("fetch"), dict) else {}
    # The whole-domain grant gate refused before any tool was picked
    # (`lanes/business.run_fetch`'s `outcome="access_denied"`). That is a DENIED
    # envelope, not an empty one: without this the composer rendered a bare
    # `*last purchase cost* for M218:` header and contract 7's refusal sentence was
    # never said - the tool was still never called, so nothing leaked, but the customer
    # was told nothing either.
    refused = fragment.get("outcome") == "access_denied"
    codes = [name for name in (_answer_subject(e) for e in entities) if name]
    rows = fetched.get("answers")
    figures = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    files = fetched.get("attachments")
    has_result = bool(fetched.get("has_result")) and bool(figures)
    envelope: dict[str, Any] = {
        "domain": spec.domain,
        "denied": refused,
        "entities": codes,
        # The PRODUCT subjects alone, for the composer's "No stock found for X" line: a
        # customer pinned from an earlier turn is a subject too, and is not a stock code.
        "product_codes": [
            name
            for name in (
                _answer_subject(e)
                for e in entities
                if jsc.nullish_str(e.get("entity_type")).strip().lower() == "product"
            )
            if name
        ],
        # ATTACHMENT-MULTI R3: the document types this fetch asked for, `{name, keys}`
        # (`attachment_type_labels`), so the composer can name a product lacking one.
        "attachment_types": list(attachment_types or []),
        "figures": figures,
        "files": [f for f in files if isinstance(f, dict)] if isinstance(files, list) else [],
        "miss": [] if has_result else codes,
        "has_result": has_result,
        # The tokens THIS MESSAGE named that the resolver could not place. The composer
        # says so beside the answer, so a message naming three codes of which one is a
        # typo does not quietly answer for two (hand pass 2 item 6, turn 7cf56afe "stock
        # wc287 wc2867 7445"). Turn-wide rather than per-entity: they are the tokens no
        # fetch could be about, which is why none of them is in `entities`.
        "unresolved": list(unplaced.values()) if unplaced else [],
        # MULTI-CODE-DYM: each of those tokens' did-you-mean (`answer.did_you_mean_by_token`,
        # keyed by the resolver's echo), re-keyed by the word the customer typed, so the
        # composer offers a missing code beside found ones what it offers when asked alone.
        "unresolved_suggestions": _suggestions_by_typed_word(unplaced, suggestions),
        # The TOOL's own verdict, before the rows test above: a report or a refusal
        # renders as `lane_text` with no figures yet DID find something, and the
        # composer's miss rule (an offer to escalate) must not read it as a miss.
        "tool_has_result": bool(fetched.get("has_result")),
        # The lane's own failure, when the fetch did not complete: neither a hit nor a
        # miss, so the composer offers nothing on it (the error text is the answer).
        "error": fragment.get("error") if isinstance(fragment.get("error"), str) else None,
        # The lane's OWN rendered sentence. The composer renders the rows itself
        # (#930's grammar, contract 102); this is what a tool with no rows to render -
        # a report, a refusal, a miss suggestion - has to say instead. A refused domain
        # says contract 7's registered sentence.
        "lane_text": _lane_text_without_withheld_header(
            denial_text if refused else fetched.get("response"),
            fetched.get("set_header"),
            counted_set=counted_set,
            set_described=bool(fetched.get("set_described")),
        ),
        # A counted-set answer's own header ("10 taps have certificates.",
        # AC-1316) - unlike `lane_text` this travels ALONGSIDE rows, not
        # instead of them: the composer still renders `figures` through its own
        # per-row grammar, only the domain-generic header line is replaced.
        #
        # ONLY when this turn described a class (`counted_set`). The `require` leg is
        # derived from the INTENT (`predicate.derive_require`: check_stock ->
        # {"stock": true}), so a predicate rides every stock / incoming / promotion
        # turn there is, and its `class_labels` are unioned from whatever the qualifying
        # candidates happen to belong to - not from anything the customer said. "7445"
        # was answered "5 taps have stock." (turn 92d565a5) and a three-domain fan-out
        # over a carried code printed "0 products have incoming stock." over all three
        # sections, stock rows included (turn d5128c67). A code is not a class, so its
        # answer keeps the domain's own header.
        "header_override": fetched.get("set_header") if counted_set else None,
        # Ported from PR #1118 (not merged), D25: what the stock reply said about a
        # quantity being required, per product. The composer never reads it (the
        # sentence is the presenter's); `engine.py` rebuilds the open stock task from
        # it (`turn/task.py::tasks_after_reply`).
        "stock_availability": (
            fetched.get("stock_availability")
            if isinstance(fetched.get("stock_availability"), list)
            else []
        ),
        # Ported from PR #1118 (not merged), D15: the products a "just proceed"
        # dropped, named by the reply so the dealer can see what was not checked.
        # Stamped on the spec by the task, carried here because the composer prints
        # it under the section it belongs to.
        "not_checked": [
            name for name in (spec.filters.get("not_checked") or []) if isinstance(name, str)
        ],
        # The lane's OWN question, when the fetch asked one instead of (or beside)
        # answering: contract 38's "which document?" and contract 39's detail offer both
        # come back as `outstanding_ask` = `{kind, last_result_set, filters}`. The
        # composer turns it into the turn's open question - before this it was dropped
        # here, so the report printed "Reply 1 for the sales order list" and stored the
        # ESCALATE offer instead, and the "1" that came back resolved against the wrong
        # roster.
        #
        # `forms_ask` (item 1, 17 Sep 2026) is the same shape, armed by
        # `output_structurer` off a forms BROWSE with several rows - the two never both
        # carry a value on the same envelope (one tool per spec), so reading either is
        # safe. `alternatives_ask` (R-c, hand pass 6) is the third: the fuzzy roster
        # `make_tool_runner.runner` arms over an entity miss's own trigram neighbours,
        # armed only when the fetch never ran filtered - the same "one tool per spec"
        # rule keeps the three from ever colliding.
        "lane_ask": (
            fetched.get("outstanding_ask")
            or fetched.get("forms_ask")
            or fetched.get("alternatives_ask")
        ),
        # AC-1139: this reply already states the scope it searched, in its own words
        # and its own order (the report's four header lines, and the same four above
        # the scope question). The composer's generic `*orders* for <code>:` line would
        # say it a second time, differently, above the answer.
        # The sales analysis prints its own header too (company, channel, basis, period).
        "own_header": bool(fetched.get("outstanding_report") or fetched.get("own_header")),
        "outcome": fragment.get("outcome"),
        "tool": (fetched.get("tool") or {}).get("name") if isinstance(fetched.get("tool"), dict) else None,
        # The window this fetch ran with, already in the words the scope question uses
        # for it (`lanes/business.order_date_text` is the one writer). Absent when the
        # fetch had no window - the header then says nothing rather than "all", which is
        # the report's own line and belongs with the other three.
        "date_line": _date_line(ran_with),
        # R3 (PLAN-chatbot-answer-half-reattach.md): `lanes.business.run_fetch`'s own
        # tier-ask arm - the REAL "entitled, must pick a tier" path, discovered only
        # once the fetch actually ran the per-tier promotion probe. `fragment` (not
        # `fetched`) carries the top-level `_fetch_arm` marker `fetch_result` sets;
        # `fetched` (== `fragment["fetch"]`) is the item `answer_bridge.question_for`'s
        # own `fetch=` kwarg expects. `None` on every other fetch, which is the ONLY
        # value `turn/compose.py` (which never reads this key) will ever see.
        "tier_ask_fetch": fetched if fragment.get("_fetch_arm") == "tier-ask" else None,
        # Security B2 (AC-1333's own rule on main, `lanes/business/__init__.py:1449-
        # 1455`): the RECOMPOSED access levels this fetch actually went out with
        # (`_fetch_semantic_input`'s own, off `tier_gate.access_levels_recomposed` when
        # a tier gate ran). `engine.py` records it on the set-page carry so a later
        # count answer recounts the set under the SAME entitlement rather than under the
        # parser's own, empty, list. `None` on every arm that never called the tool.
        "access_levels_used": fetched.get("access_levels"),
        # The top selling question this reply asked (`group` / `metric` / `basis` /
        # `how_many`), or None when it asked nothing. `engine.py` records it on
        # `focus.top_selling` (`turn/apply.record_top_selling_asked`), reviewer B2 on
        # PR #1273.
        "top_selling_asked": fetched.get("top_selling_asked"),
        # The item codes a listed ranking printed, recorded on the slot by the same
        # `record_top_selling_asked` (fix lane round 8).
        "top_selling_codes": fetched.get("top_selling_codes"),
        # Slot keys the reply asks to forget (a category word it said it does not
        # know), applied by the same `record_top_selling_asked`.
        "top_selling_drop": fetched.get("top_selling_drop"),
        # LOWSTOCK-FILTER-ASK: the `required_fields` slot a lane left open, recorded on
        # `focus.required_ask` by `engine.py` for the next message only.
        "required_ask": fetched.get("required_ask"),
    }
    if raw_fragment is not None:
        # R4 (PLAN-chatbot-answer-half-reattach.md): the UNTOUCHED `business.run_fetch`
        # fragment itself, a general-purpose escape hatch - `answer_bridge.answer_for`
        # reads `raw_fragment.get("outcome")` / `raw_fragment.get("fetch")` off it to
        # discover a genuine absence (`outcome == "not_found"`) after a fetch has
        # actually run, the same way `tier_ask_fetch` above discovers a tier-ask.
        # `turn/compose.py` never reads this key. ABSENT (not merely `None`) unless the
        # caller supplies one - `make_tool_runner.runner` is the one caller that does.
        envelope["raw_fragment"] = raw_fragment
    if brand_names:
        # #1262 fix lane round 3, B1-r2: the live brands this order fetch was filtered
        # by (`order_brand_filter`), for the "Brand:" line on both order headers.
        envelope["brand_names"] = list(brand_names)
    return envelope


def _date_line(ran_with: dict[str, Any] | None) -> str | None:
    """`Order date: 01/09/2026 to 30/09/2026`, or None when the fetch had no window."""
    if not isinstance(ran_with, dict):
        return None
    start = ran_with.get("date_filter_start")
    end = ran_with.get("date_filter_end")
    if not (start or end):
        return None
    from app.services.chatbot.lanes.business import order_date_text

    return f"Order date: {order_date_text(start, end)}"
