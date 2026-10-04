"""M3a write-confirmation gate - unit tests.

Covers PLAN-ai-assistant-evals-guardrails §11 U7: a write tool NEVER executes
without an explicit Confirm. These test the deterministic gate mechanism
(summarize / serve-prompt / load / resolve), NOT the LLM that decides to call a
write tool. Offline + blank Postgres schema, CI-safe.
"""
from __future__ import annotations

import types

import pytest
from sqlalchemy.orm import Session

from app.models.ai_assistant import AIAssistantConversation
from app.models.user import User
from app.services.ai_assistant_service import AIAssistantChatService
from tests._pg_fixture import blank_session


@pytest.fixture
def db_session() -> Session:
    with blank_session() as session:
        yield session


@pytest.fixture
def conv(db_session: Session):
    user = User(id="95709c37-0fb4-5c00-8686-536c019e6fb7", email="u1@test.com", name="U", status="ACTIVE")
    db_session.add(user)
    db_session.flush()  # parent must land before the FK child (no ORM relationship to order it)
    c = AIAssistantConversation(user_id="95709c37-0fb4-5c00-8686-536c019e6fb7", title="t")
    db_session.add(c)
    db_session.commit()
    return c


@pytest.fixture
def svc(db_session: Session) -> AIAssistantChatService:
    s = AIAssistantChatService(db_session)
    s._turn_trace = None
    s._turn_prompt_versions = []
    s._parse_token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return s


_CFG = types.SimpleNamespace(model="gpt-x", provider="openai")

_TICKET_ARGS = {
    "space_id": "",
    "contact_id": "",
    "payload_json": '{"title": "Broken printer L3", "priority": "high", '
    '"actor_user_id": "95709c37-0fb4-5c00-8686-536c019e6fb7", "source_conversation_id": "c1"}',
}


# --------------------------------------------------------------------------- #
# _summarize_write - human verb+object+label, plumbing dropped                 #
# --------------------------------------------------------------------------- #
def test_summarize_uses_payload_title_and_verb(svc: AIAssistantChatService):
    out = svc._summarize_write("crm_it_support_ticket_create", _TICKET_ARGS)
    assert out == "create it support ticket: Broken printer L3"
    # Never leak the raw payload_json / internal keys.
    assert "payload_json" not in out
    assert "actor_user_id" not in out
    assert "source_conversation_id" not in out


def test_summarize_verb_from_suffix(svc: AIAssistantChatService):
    assert svc._summarize_write("crm_complaint_close", {"complaint_id": "C-1"}).startswith("close complaint")
    assert svc._summarize_write("crm_purchase_request_approve", {"id": "P-1"}).startswith("approve purchase request")


def test_is_write_tool_gates_all_mutating_verbs():
    from app.services.ai_assistant_service import _is_write_tool

    for name in (
        "crm_complaint_submit", "crm_it_support_ticket_create", "crm_portal_link_get_link",
        "crm_complaint_close", "crm_order_cancel", "crm_purchase_request_approve",
        "crm_purchase_request_reject", "crm_complaint_update", "crm_x_delete",
    ):
        assert _is_write_tool(name), f"{name} MUST be gated"
    # Read tools must NOT be gated.
    for name in (
        "crm_complaints_list", "crm_order_analytics", "crm_master_customers_list",
        "crm_portal_link_get", "user_guides_read", "crm_sla_conversation_tracking_dashboard",
    ):
        assert not _is_write_tool(name), f"{name} must NOT be gated"


def test_summarize_truncates_long_label(svc: AIAssistantChatService):
    payload = '{"title": "%s"}' % ("z" * 200)
    out = svc._summarize_write("crm_x_submit", {"payload_json": payload})
    assert out.endswith("…")
    assert len(out) < 120


# --------------------------------------------------------------------------- #
# serve → load → resolve round-trip                                            #
# --------------------------------------------------------------------------- #
def _serve(svc, conv):
    pending = {
        "tool_name": "crm_it_support_ticket_create",
        "args": _TICKET_ARGS,
        "summary": svc._summarize_write("crm_it_support_ticket_create", _TICKET_ARGS),
    }
    return svc._serve_pending_confirmation(
        conv=conv, config=_CFG, pending=pending, request_started=0.0
    )


def test_serve_persists_pending_and_asks_to_confirm(svc, conv):
    _c, msg = _serve(svc, conv)
    pc = (msg.metadata_json or {}).get("pending_confirmation")
    assert pc and pc["status"] == "pending"
    assert pc["tool_name"] == "crm_it_support_ticket_create"
    assert "Confirm" in msg.content and "Cancel" in msg.content


def test_load_finds_then_clears_after_resolve(svc, conv):
    _serve(svc, conv)
    loaded = svc._load_pending_confirmation(conv.id)
    assert loaded is not None and loaded["tool_name"] == "crm_it_support_ticket_create"
    svc._mark_confirmation_resolved(loaded["_message_id"], "confirmed")
    # Resolved → no longer resumable (idempotency backstop).
    assert svc._load_pending_confirmation(conv.id) is None


class _FakeMCP:
    def __init__(self, *a, **k):
        self.calls = []

    def call_tool(self, tool_name, args):
        self.calls.append((tool_name, args))
        return '{"reference": "TKT-42", "status": "open"}'


def test_confirm_executes_and_reports(svc, conv, monkeypatch):
    import app.services.ai_assistant_service as m

    fake = _FakeMCP()
    monkeypatch.setattr(m, "MCPRuntimeClient", lambda *a, **k: fake)

    _serve(svc, conv)
    pending = svc._load_pending_confirmation(conv.id)
    _c, msg = svc._resolve_pending_confirmation(
        conv=conv, user_id="95709c37-0fb4-5c00-8686-536c019e6fb7", config=_CFG,
        pending=pending, action="confirm", request_started=0.0,
    )
    # The stored write actually ran, exactly once, with the stored args.
    assert fake.calls == [("crm_it_support_ticket_create", _TICKET_ARGS)]
    assert "Done" in msg.content and "TKT-42" in msg.content
    assert msg.metadata_json["pending_confirmation"]["status"] == "confirmed"
    assert msg.metadata_json["tool_calls"] == [
        {"tool_name": "crm_it_support_ticket_create", "ok": True}
    ]


def test_cancel_does_not_execute(svc, conv, monkeypatch):
    import app.services.ai_assistant_service as m

    fake = _FakeMCP()
    monkeypatch.setattr(m, "MCPRuntimeClient", lambda *a, **k: fake)

    _serve(svc, conv)
    pending = svc._load_pending_confirmation(conv.id)
    _c, msg = svc._resolve_pending_confirmation(
        conv=conv, user_id="95709c37-0fb4-5c00-8686-536c019e6fb7", config=_CFG,
        pending=pending, action="cancel", request_started=0.0,
    )
    assert fake.calls == []  # NO write on cancel
    assert "cancel" in msg.content.lower()
    assert msg.metadata_json["pending_confirmation"]["status"] == "cancelled"


def test_double_confirm_is_idempotent(svc, conv, monkeypatch):
    import app.services.ai_assistant_service as m

    fake = _FakeMCP()
    monkeypatch.setattr(m, "MCPRuntimeClient", lambda *a, **k: fake)

    _serve(svc, conv)
    pending = svc._load_pending_confirmation(conv.id)
    svc._resolve_pending_confirmation(
        conv=conv, user_id="95709c37-0fb4-5c00-8686-536c019e6fb7", config=_CFG,
        pending=pending, action="confirm", request_started=0.0,
    )
    # A second Confirm click finds nothing pending → cannot double-write.
    assert svc._load_pending_confirmation(conv.id) is None
    assert len(fake.calls) == 1


def test_confirm_denied_when_user_lacks_permission(svc, conv, monkeypatch):
    """Real-user RBAC gate: a mapped write tool must NOT execute when the actual
    logged-in user lacks the permission - even though dispatch would run as the
    shared act-as principal."""
    import app.services.ai_assistant_service as m
    import app.services.user_service as us

    fake = _FakeMCP()
    monkeypatch.setattr(m, "MCPRuntimeClient", lambda *a, **k: fake)
    monkeypatch.setattr(us.UserPermissionService, "check_user_has_permission", lambda self, uid, slug: False)

    pending = {
        "tool_name": "crm_complaint_close",
        "args": {"complaint_id": "cmp-1"},
        "summary": "close complaint: complaint_id=cmp-1",
    }
    svc._serve_pending_confirmation(conv=conv, config=_CFG, pending=pending, request_started=0.0)
    loaded = svc._load_pending_confirmation(conv.id)
    _c, msg = svc._resolve_pending_confirmation(
        conv=conv, user_id="95709c37-0fb4-5c00-8686-536c019e6fb7", config=_CFG,
        pending=loaded, action="confirm", request_started=0.0,
    )
    assert fake.calls == []  # NO write when unauthorized
    assert msg.metadata_json["pending_confirmation"]["status"] == "denied"
    assert "permission" in msg.content.lower()


def test_confirm_allowed_when_user_has_permission(svc, conv, monkeypatch):
    import app.services.ai_assistant_service as m
    import app.services.user_service as us

    fake = _FakeMCP()
    monkeypatch.setattr(m, "MCPRuntimeClient", lambda *a, **k: fake)
    monkeypatch.setattr(us.UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True)

    pending = {
        "tool_name": "crm_complaint_close",
        "args": {"complaint_id": "cmp-1"},
        "summary": "close complaint: complaint_id=cmp-1",
    }
    svc._serve_pending_confirmation(conv=conv, config=_CFG, pending=pending, request_started=0.0)
    loaded = svc._load_pending_confirmation(conv.id)
    _c, msg = svc._resolve_pending_confirmation(
        conv=conv, user_id="95709c37-0fb4-5c00-8686-536c019e6fb7", config=_CFG,
        pending=loaded, action="confirm", request_started=0.0,
    )
    assert fake.calls == [("crm_complaint_close", {"complaint_id": "cmp-1"})]
    assert msg.metadata_json["pending_confirmation"]["status"] == "confirmed"


def test_confirm_reports_failure_without_crashing(svc, conv, monkeypatch):
    import app.services.ai_assistant_service as m

    class _Boom:
        def __init__(self, *a, **k): ...
        def call_tool(self, tool_name, args):
            raise RuntimeError("mcp down")

    monkeypatch.setattr(m, "MCPRuntimeClient", lambda *a, **k: _Boom())
    _serve(svc, conv)
    pending = svc._load_pending_confirmation(conv.id)
    _c, msg = svc._resolve_pending_confirmation(
        conv=conv, user_id="95709c37-0fb4-5c00-8686-536c019e6fb7", config=_CFG,
        pending=pending, action="confirm", request_started=0.0,
    )
    assert msg.metadata_json["pending_confirmation"]["status"] == "failed"
    assert "didn't go through" in msg.content or "failed" in msg.content.lower()


# --------------------------------------------------------------------------- #
# crm_ideation_turn: a write tool whose NAME does not end in a write verb       #
# (security review S2b-S5, finding B1b)                                         #
# --------------------------------------------------------------------------- #
def test_ideation_turn_is_a_write_tool_despite_its_name():
    """`_is_write_tool` matched a verb SUFFIX, and this tool ends in `_turn`.

    Both of the assistant's write controls read this one predicate - the
    write-confirm gate in the agent loop and the prompt dry-run suppression - so
    a False here is two silent bypasses, not one. The tool mints or mutates a
    real ideation draft against a real respond.io contact, so it is a write by
    what it does, not by what it is called.
    """
    from app.services.ai_assistant_service import _is_write_tool

    assert _is_write_tool("crm_ideation_turn")
    # The suffix rule is untouched, and it still must not gate a read.
    assert not _is_write_tool("crm_ideation_board_list")


def test_ideation_turn_carries_a_confirm_permission():
    from app.services.ai_assistant_service import AIAssistantChatService

    assert (
        AIAssistantChatService._WRITE_TOOL_PERMISSIONS["crm_ideation_turn"]
        == "ideation.board.view"
    )


def test_ideation_turn_confirm_denied_when_user_lacks_the_permission(svc, conv, monkeypatch):
    import app.services.ai_assistant_service as m
    import app.services.user_service as us

    fake = _FakeMCP()
    monkeypatch.setattr(m, "MCPRuntimeClient", lambda *a, **k: fake)
    monkeypatch.setattr(
        us.UserPermissionService, "check_user_has_permission", lambda self, uid, slug: False
    )

    pending = {
        "tool_name": "crm_ideation_turn",
        "args": {"respond_io_id": "900000008", "message_text": "an idea"},
        "summary": "run ideation turn",
    }
    svc._serve_pending_confirmation(conv=conv, config=_CFG, pending=pending, request_started=0.0)
    loaded = svc._load_pending_confirmation(conv.id)
    _c, msg = svc._resolve_pending_confirmation(
        conv=conv, user_id="95709c37-0fb4-5c00-8686-536c019e6fb7", config=_CFG,
        pending=loaded, action="confirm", request_started=0.0,
    )

    assert fake.calls == []
    assert msg.metadata_json["pending_confirmation"]["status"] == "denied"


def test_ideation_turn_confirm_runs_when_the_user_has_the_permission(svc, conv, monkeypatch):
    import app.services.ai_assistant_service as m
    import app.services.user_service as us

    fake = _FakeMCP()
    monkeypatch.setattr(m, "MCPRuntimeClient", lambda *a, **k: fake)
    monkeypatch.setattr(
        us.UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True
    )

    args = {"respond_io_id": "900000008", "message_text": "an idea"}
    pending = {"tool_name": "crm_ideation_turn", "args": args, "summary": "run ideation turn"}
    svc._serve_pending_confirmation(conv=conv, config=_CFG, pending=pending, request_started=0.0)
    loaded = svc._load_pending_confirmation(conv.id)
    _c, msg = svc._resolve_pending_confirmation(
        conv=conv, user_id="95709c37-0fb4-5c00-8686-536c019e6fb7", config=_CFG,
        pending=loaded, action="confirm", request_started=0.0,
    )

    assert fake.calls == [("crm_ideation_turn", args)]
    assert msg.metadata_json["pending_confirmation"]["status"] == "confirmed"
