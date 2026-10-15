"""The ONE engine seam for parser v2 (CONTRACT-PV2-13oct.md section 6). Owner PV2-CORE.

`engine._run_stages_body` builds a `TurnV2Context` at the parse step when
`flag.parser_mode` says "v2" and calls `run_v2`. That delegates to PV2-CARRY's
`pv2.turn.run_turn_v2(ctx) -> TurnV2Result(verdict, focus, direct_reply | None, state)`:

- `verdict` (the 59-key v1 verdict, resolved codes) + `focus`: the engine continues its
  normal stages exactly as if the parser had returned that verdict;
- `direct_reply` (clarify, small talk, report, idea): the engine closes the turn with that
  text through the same close/trace path, no lane runs.

`state` (the ConversationState) is written to `respond_contacts.session_vars["pv2"]` by
`persist_state` after the turn (a dry run hands it back in `session_patch` instead), so
the v1 tail's wholesale five-key write cannot erase it.

Optional attributes the hook reads off a TurnV2Result: `usage` (provider usage dict),
`trace` (dict for the `pv2_turn` trace event: index_sha, version, version_id, acts, ...),
`branch_kind` (a `contracts.BRANCH_KINDS` value for a direct reply; default `low_signal`),
`quick_replies` (str | None).
"""
from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)

STATE_KEY = "pv2"


class V2Unavailable(RuntimeError):
    """PV2-CARRY's `pv2.turn` is not on this build: the turn runs v1."""


@dataclass
class TurnV2Context:
    session_factory: Callable[[], Any]
    envelope: Any
    turn_id: str
    contact_respond_id: str
    contact_pk: str | None
    text: str
    # The contact's stored session vars as this turn read them: the engine's
    # `{"respond_io_id": ..., "session_vars": {...}}` block, so the pv2 state is at
    # `session_block["session_vars"]["pv2"]` (on a dry run, the harness's injected copy).
    session_block: dict[str, Any]
    # v1 `turn.state.State` as loaded (focus, pending, profile) and the v1 profile.
    state_in: Any
    profile: Any
    # `turn_runtime.recent_exchanges` rows, and the memory intake dict (profile facts,
    # summaries, earlier messages).
    recent: list[Any]
    memory_intake: dict[str, Any]
    now: datetime
    dry_run: bool
    turn_trace: Any
    # The Prompts screen's "run a turn" version pin, dry runs only (same rule as v1).
    override_version_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """A console turn belongs to its run (`envelope.test_run_id`): a state stored by
        another run, and the earlier-session summaries, are not this chat's memory."""
        run = getattr(self.envelope, "test_run_id", None)
        if not (run and getattr(self.envelope, "is_test", False)):
            return
        block = self.session_block if isinstance(self.session_block, dict) else {}
        nested = isinstance(block.get("session_vars"), dict)
        sv = block["session_vars"] if nested else block
        stored = sv.get(STATE_KEY)
        if STATE_KEY in sv and not (isinstance(stored, dict) and stored.get("run_id") == run):
            kept = {k: v for k, v in sv.items() if k != STATE_KEY}
            self.session_block = {**block, "session_vars": kept} if nested else kept
        if isinstance(self.memory_intake, dict) and self.memory_intake.get("summaries"):
            self.memory_intake = {**self.memory_intake, "summaries": []}


def _turn_module():
    try:
        return importlib.import_module("app.services.chatbot.pv2.turn")
    except ModuleNotFoundError as exc:
        if exc.name == "app.services.chatbot.pv2.turn":
            raise V2Unavailable("pv2.turn (PV2-CARRY) is not on this build") from exc
        raise


def available() -> bool:
    try:
        _turn_module()
    except V2Unavailable:
        return False
    return True


def run_v2(ctx: TurnV2Context) -> Any:
    """CARRY's `run_turn_v2`. Raises `V2Unavailable` when CARRY is absent and lets
    `call.ParserV2Error` through (the engine turns it into a failed `understood`)."""
    return _turn_module().run_turn_v2(ctx)


def customer_scope(db: Any, contact_respond_id: Any, space_id: Any, profile: Any) -> dict[str, Any] | None:
    """R5-8: the customer scope a PV2 turn holds the contact to, at both of its seams (the resolver and the
    business fetch a PV2 Lookup runs): v1's own (`business.services.customer_scope`), except that a contact
    with no customer links who is neither staff (the office tier, or `contact_asker`'s staff) nor a salesperson
    is held to none: enforced with no ids, so no customer is searched, named or fetched. v1 turns never read it."""
    from app.services.chatbot.lanes.business import services as business_services

    scope = business_services.customer_scope(db, contact_respond_id, space_id)
    if scope is not None or not _unlinked_non_staff(db, contact_respond_id, space_id, profile):
        return scope
    return {"ids": [], "names": {}, "linked": [], "staff": False, "enforced": True, "levels": {},
            "refusal": None}


def _unlinked_non_staff(db: Any, contact_respond_id: Any, space_id: Any, profile: Any) -> bool:
    """Not staff by PV2's own signals (the office tier, an active office access type: `turn.is_staff_contact`),
    and the one asker rule (`contact_asker.classify`) reads neither staff nor a salesperson (V1-DOC-SCOPE-SALES:
    a salesperson keeps staff scope). A contact that cannot be resolved is unclassified (the closed side)."""
    from app.services import contact_asker
    from app.services import contact_customer_scope as scope_mod
    from app.services.chatbot.turn.state import is_staff_profile
    from app.services.field_access import resolve_contact_with_null_workspace_fallback

    if is_staff_profile(profile):
        return False
    contact = str(contact_respond_id or "").strip()
    internal = resolve_contact_with_null_workspace_fallback(
        db, contact_id=contact, space_id=(str(space_id).strip() or None) if space_id is not None else None,
    ) if contact else None
    if not internal:
        return True
    if scope_mod.is_office_staff(db, internal):
        return False
    return contact_asker.classify(db, internal) not in ("salesperson", "staff")


def state_dump(state: Any) -> dict[str, Any] | None:
    if state is None:
        return None
    if hasattr(state, "model_dump"):
        return state.model_dump(mode="json")
    return dict(state)


def persist_state(
    db: Any, *, respond_io_id: str, state: Any, contact_pk: str | None = None
) -> None:
    """Write `session_vars["pv2"]` alone, leaving every other key as stored, on ONE contact
    row: the turn's resolved `contact_pk` when it has one (`respond_io_id` is not unique),
    else the row the v1 writer locks (`overwrite_for_contact`'s own lookup). Called after
    the v1 tail's own write, so the two never race inside one turn."""
    from sqlalchemy import text

    dumped = state_dump(state)
    if dumped is None:
        return
    row_id = contact_pk
    if not row_id:
        row = db.execute(
            text("SELECT id FROM respond_contacts WHERE respond_io_id = :cid FOR UPDATE"),
            {"cid": respond_io_id},
        ).first()
        if row is None:
            return
        row_id = row[0]
    db.execute(
        text(
            "UPDATE respond_contacts SET session_vars = jsonb_set("
            "COALESCE(session_vars, '{}'::jsonb), '{pv2}', CAST(:s AS jsonb), true) "
            "WHERE id = :id"
        ),
        {"s": json.dumps(dumped, default=str), "id": row_id},
    )
    db.commit()


def trace_facts(result: Any, *, mode: str = "v2") -> dict[str, Any]:
    facts = {"parser_mode": mode}
    extra = getattr(result, "trace", None)
    if isinstance(extra, dict):
        facts.update(extra)
    return facts
