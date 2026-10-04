"""S4 graceful fallback replies: the key-free replay corpus (plan sections 7.3 and 8.1,
AC-MEM080 to AC-MEM093).

Each case under `replay_memory/fallback/` is one exchange of plan 7.3 (plus the owner's own
hand-test wording, case 00). A case seeds the contact's history, plays its earlier turns
live through the real `engine.run_turn`, then plays the graded turn with a recorded parser
verdict and a stubbed clarifier, and grades what the dealer was SENT (every
`send_message` action, joined), the parser prompt, the clarifier prompt, the escalation
comment and the profile afterwards.

A case marked `needs_memory: true` is re-run with the contact at level `off` over the SAME
history and must then FAIL its own reply and prompt expectations (AC-MEM067's kill test,
applied to this corpus): a memory case that stays green without memory is a defect in the
case.

Same sibling-directory reason as `test_memory_replay_ablation.py`: `test_turn_replay.py`
globs every json under `replay_turns/` and holds it to its recorded-envelope shape.

Postgres only (`tests/chatbot/conftest.py::session_factory`).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import object_session

from app.models.chatbot_turn import ChatbotTurn
from app.models.conversation_frame import ConversationFrame
from tests.chatbot._turn_helpers import verdict as verdict_defaults
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access  # noqa: F401

CASES_DIR = Path(__file__).parent / "replay_memory" / "fallback"
CASE_FILES = sorted(CASES_DIR.glob("*.json"))

DEFAULT_CLARIFIER = {"ack": "Sure.", "language": "en"}
SPACE_ID = "364817"


def _load_case(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _plays() -> list[tuple[Path, str]]:
    """(case, message) pairs: a case may grade several wordings of one turn."""
    out: list[tuple[Path, str]] = []
    for path in CASE_FILES:
        turn = _load_case(path)["turn"]
        for message in turn.get("messages") or [turn["message"]]:
            out.append((path, message))
    return out


PLAYS = _plays()
PLAY_IDS = [f"{p.stem}::{m}" for p, m in PLAYS]
MEMORY_PLAYS = [(p, m) for p, m in PLAYS if _load_case(p).get("needs_memory") is True]
MEMORY_PLAY_IDS = [f"{p.stem}::{m}" for p, m in MEMORY_PLAYS]


class _Lane:
    """What the stubs saw: clarifier prompts, tool calls."""

    def __init__(self) -> None:
        self.clarifier_answer: dict[str, Any] = dict(DEFAULT_CLARIFIER)
        self.clarifier_prompts: list[str] = []
        self.tool_calls: list[str] = []


@pytest.fixture()
def lane(session_factory, system_settings_row, monkeypatch) -> _Lane:
    """Memory reaches a contact only through its own level (the switch stays Off), the
    `low_signal` lane completes in the CRM with a stubbed clarifier, and every MCP tool
    answers "nothing" without the network."""
    from app.services.ai_assistant_service import MCPRuntimeClient
    from app.services.chatbot.lanes import casual

    system_settings_row.chatbot_memory = {"enabled": False, "default_level": "full"}
    system_settings_row.chatbot_completed_lanes = ["low_signal"]
    object_session(system_settings_row).commit()

    state = _Lane()
    monkeypatch.setattr(casual, "resolve_for_prompt", lambda db, *, ctx: {"resolutions": []})
    monkeypatch.setattr(casual, "resolve_clarifier_config", lambda db, **_: object())

    def fake_clarifier(config, prompt):
        state.clarifier_prompts.append(prompt)
        return json.dumps(state.clarifier_answer)

    monkeypatch.setattr(casual, "call_clarifier", fake_clarifier)

    def fake_tool(self, name, arguments):
        state.tool_calls.append(name)
        return json.dumps({"answers": []})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_tool)
    return state


def _seed_contact(session_factory, given: dict[str, Any], *, level: str | None) -> str:
    db = session_factory()
    pk = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars, "
            "chatbot_memory_level, first_name) "
            "VALUES (:pk, :cid, :phone, CAST(:sv AS jsonb), :lvl, :first)"
        ),
        {
            "pk": pk,
            "cid": str(CONTACT_ID),
            "phone": f"+6011{uuid.uuid4().hex[:8]}",
            "sv": json.dumps({"variables": {}}),
            "lvl": level,
            "first": given.get("first_name"),
        },
    )
    if given.get("profile_facts"):
        db.execute(
            text("UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) WHERE id = :pk"),
            {"pk": pk, "p": json.dumps({"facts": given["profile_facts"]})},
        )
    db.commit()
    if given.get("customer"):
        _seed_customer(session_factory, pk, given["customer"])
    return pk


def _seed_customer(session_factory, contact_pk: str, customer: dict[str, Any]) -> None:
    """The contact's primary linked customer and its salesperson: the live CRM link
    `profile_facts.crm_view` reads."""
    from app.models.access import RespondContactCustomer
    from app.models.order import Customer
    from app.models.sales_agent import SalesAgent
    from app.services.company_scope import DEFAULT_COMPANY_ID

    # Stamped explicitly: all three are company-scoped, and a scoped read never matches
    # a NULL company (`test_memory_profile_facts.py::_seed_customer_link`).

    db = session_factory()
    agent_id = None
    if customer.get("salesperson"):
        agent = SalesAgent(
            sales_agent=f"ZZT-{uuid.uuid4().hex[:6]}".upper(),
            person_label=customer["salesperson"],
            is_active=True,
            company_id=DEFAULT_COMPANY_ID,
        )
        db.add(agent)
        db.flush()
        agent_id = agent.id
    row = Customer(
        customer_code=f"{customer['code']}-{uuid.uuid4().hex[:4]}",
        customer_name=customer["name"],
        sales_agent_id=agent_id,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    # The contact's workspace and company membership: the turn's sessions are scoped
    # to it (`engine._contact_company_scope`), and a contact with none sees no customer
    # (`test_outstanding_lane.py::_seed_contact`, same shape).
    db.execute(
        text(
            "INSERT INTO respond_workspaces (id, space_id, name, api_key_ciphertext) "
            "VALUES (gen_random_uuid(), :sid, 'ZZT S4 workspace', 'ZZT-cipher') ON CONFLICT DO NOTHING"
        ),
        {"sid": SPACE_ID},
    )
    db.execute(
        text(
            "UPDATE respond_contacts SET workspace_id = "
            "(SELECT id FROM respond_workspaces WHERE space_id = :sid LIMIT 1) WHERE id = :pk"
        ),
        {"sid": SPACE_ID, "pk": contact_pk},
    )
    db.execute(
        text(
            "INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
            "VALUES (gen_random_uuid(), :pk, :company)"
        ),
        {"pk": contact_pk, "company": DEFAULT_COMPANY_ID},
    )
    db.add(
        RespondContactCustomer(
            contact_id=contact_pk, customer_id=row.id, is_primary=True, company_id=DEFAULT_COMPANY_ID
        )
    )
    db.commit()


def _seed_frames(session_factory, frames: list[dict[str, Any]], *, is_test: bool) -> None:
    db = session_factory()
    for f in frames:
        when = datetime.now() - timedelta(days=f.get("days_ago", 1))
        db.add(
            ConversationFrame(
                contact_id=str(CONTACT_ID),
                contact_respond_id=str(CONTACT_ID),
                space_id="0",
                channel="whatsapp",
                domain=f.get("domain", "inventory"),
                status="closed",
                is_test=is_test,
                close_reason="topic_switch",
                summary=f["summary"],
                entities=f.get("entities") or {},
                turn_ids=[f"ZZT-s4-frame-{uuid.uuid4().hex[:8]}"],
                started_at=when,
                opened_at=when,
                last_activity_at=when,
                closed_at=when,
            )
        )
    db.commit()


def _run_turn(session_factory, stub_access, *, message: str, verdict_overrides: dict, n: int, console: bool):
    from unittest import mock

    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.head import parser as parser_mod

    stub_access()
    v = verdict_defaults(**verdict_overrides)

    def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test"
        )

    captured: list[str] = []

    def fake_parse(config, user_block):
        captured.append(user_block)
        return v

    with mock.patch.object(parser_mod, "resolve_config", fake_resolve_config), mock.patch.object(
        parser_mod, "parse", fake_parse
    ):
        envelope = _envelope(**({"is_test": True, "ingress": "console"} if console else {}))
        envelope.message["message"]["messageId"] = f"ZZT-s4-{uuid.uuid4().hex[:8]}-{n}"
        envelope.message["message"]["message"]["text"] = message
        result = engine_mod.run_turn(envelope, session_factory=session_factory)
    # Minutes apart and in order, so every range and "newest" reads the play order.
    db = session_factory()
    db.query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).update(
        {"created_at": datetime.now(timezone.utc) - timedelta(minutes=30 - n)}
    )
    db.commit()
    return result, (captured[-1] if captured else "")


def sent_text(result: Any) -> str:
    """Everything the dealer was sent this turn, in order."""
    return "\n".join(
        str(a.get("text") or "")
        for a in (getattr(result, "actions", None) or [])
        if isinstance(a, dict) and a.get("kind") == "send_message"
    )


def comment_text(result: Any) -> str:
    return "\n".join(
        str(a.get("text") or "")
        for a in (getattr(result, "actions", None) or [])
        if isinstance(a, dict) and a.get("kind") == "add_comment"
    )


def _play(session_factory, stub_access, lane: _Lane, case: dict[str, Any], message: str, *, ablate: bool):
    given = case.get("given") or {}
    level = "off" if ablate else given.get("memory_level")
    # A console case plays every turn as a Chatbot Console dry run (its own `is_test`
    # world, D14's console exception): the escalation lane previews its actions there
    # instead of reaching the round robin and the SLA seams.
    console = bool(case.get("console"))
    contact_pk = _seed_contact(session_factory, given, level=level)
    if given.get("frames"):
        _seed_frames(session_factory, given["frames"], is_test=console)
    for i, live in enumerate(given.get("live_turns") or []):
        _run_turn(
            session_factory,
            stub_access,
            message=live["message"],
            verdict_overrides=live.get("verdict") or {},
            n=i,
            console=console,
        )
    turn = case["turn"]
    lane.clarifier_answer = dict(turn.get("clarifier") or DEFAULT_CLARIFIER)
    lane.clarifier_prompts.clear()
    lane.tool_calls.clear()
    result, prompt = _run_turn(
        session_factory,
        stub_access,
        message=message,
        verdict_overrides=turn.get("verdict") or {},
        n=20,
        console=console,
    )
    return result, prompt, contact_pk


def _profile_facts(session_factory, contact_pk: str) -> list[dict[str, Any]]:
    row = session_factory().execute(
        text("SELECT chatbot_profile FROM respond_contacts WHERE id = :pk"), {"pk": contact_pk}
    ).scalar()
    return list((row or {}).get("facts") or [])


def _grade(case_name: str, expected: dict[str, Any], result: Any, prompt: str, lane: _Lane) -> list[str]:
    """Every expectation that does NOT hold, as a message. Empty = the case passes."""
    misses: list[str] = []
    sent = sent_text(result)
    if "branch_kind" in expected and result.branch_kind != expected["branch_kind"]:
        misses.append(f"branch_kind {result.branch_kind!r}, expected {expected['branch_kind']!r}")
    for phrase in expected.get("reply_contains") or []:
        if phrase not in sent:
            misses.append(f"reply lacks {phrase!r}")
    for phrase in expected.get("reply_not_contains") or []:
        if phrase in sent:
            misses.append(f"reply carries {phrase!r}")
    if "reply_starts_with" in expected and not sent.startswith(expected["reply_starts_with"]):
        misses.append(f"reply does not start with {expected['reply_starts_with']!r}")
    for phrase in expected.get("prompt_contains") or []:
        if phrase not in prompt:
            misses.append(f"parser prompt lacks {phrase!r}")
    clarifier_prompt = lane.clarifier_prompts[-1] if lane.clarifier_prompts else ""
    for phrase in expected.get("clarifier_prompt_contains") or []:
        if phrase not in clarifier_prompt:
            misses.append(f"clarifier prompt lacks {phrase!r}")
    for phrase in expected.get("comment_contains") or []:
        if phrase not in comment_text(result):
            misses.append(f"escalation comment lacks {phrase!r}")
    if expected.get("no_tool_call") and lane.tool_calls:
        misses.append(f"the lane called tools {lane.tool_calls}")
    # AC-MEM085: every turn that reaches the reply stage sends a visible line.
    if not sent.strip():
        misses.append("no visible send_message action")
    return [f"{case_name}: {m}" for m in misses] + ([f"{case_name}: sent was:\n{sent}"] if misses else [])


@pytest.mark.parametrize(("case_path", "message"), PLAYS, ids=PLAY_IDS)
def test_fallback_case(case_path: Path, message: str, session_factory, stub_access, lane) -> None:
    case = _load_case(case_path)
    result, prompt, contact_pk = _play(session_factory, stub_access, lane, case, message, ablate=False)
    expected = case.get("expected") or {}
    misses = _grade(case_path.name, expected, result, prompt, lane)
    fact = expected.get("profile_fact_after")
    if fact is not None:
        facts = {f.get("key"): f for f in _profile_facts(session_factory, contact_pk)}
        got = facts.get(fact["key"])
        if got is None or got.get("value") != fact["value"]:
            misses.append(f"{case_path.name}: profile fact {fact['key']} is {got!r}, expected {fact['value']!r}")
    assert not misses, "\n".join(misses)


@pytest.mark.parametrize(("case_path", "message"), MEMORY_PLAYS, ids=MEMORY_PLAY_IDS)
def test_needs_memory_case_fails_under_ablation(
    case_path: Path, message: str, session_factory, stub_access, lane
) -> None:
    case = _load_case(case_path)
    expected = case.get("expected") or {}
    graded = {
        k: expected[k]
        for k in ("reply_contains", "reply_starts_with", "prompt_contains", "clarifier_prompt_contains", "comment_contains")
        if expected.get(k)
    }
    assert graded, f"{case_path.name}: a needs_memory case must grade the reply or the prompt"
    result, prompt, _pk = _play(session_factory, stub_access, lane, case, message, ablate=True)
    assert _grade(case_path.name, graded, result, prompt, lane), (
        f"{case_path.name}: marked needs_memory:true but every reply and prompt expectation "
        f"still holds with the contact at level off - this case does not test memory"
    )
