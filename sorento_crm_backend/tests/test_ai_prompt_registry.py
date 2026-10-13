"""Tests for the AI assistant prompt registry (M1 / Phase 2).

Covers the runtime resolver + fallback (UAC B1-B4), the CRUD service including
seed idempotency and version auto-increment (UAC A3-A6, D3-D4), call-site
wiring + metadata stamping through ``respond()`` (UAC C2-C3), and the HTTP
routes' auth + guard behavior (UAC D1/D5/D6).

Everything runs against a blank copy of the real Postgres schema with the LLM
provider / MCP / RAG / entity-resolver stubbed - no network traffic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlalchemy.orm import Session

from app.models.ai_assistant import AIAssistantConfig
from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.models.user import User
from app.schemas.ai_assistant import AIAssistantAuthContext
from app.services import ai_prompt_registry
from app.services.ai_prompt_registry import PROMPT_KEYS, get_prompt, render
from app.services.ai_prompt_seed import seed_prompt_registry
from app.services.ai_prompt_service import AIPromptRegistryError, AIPromptService
from tests._pg_fixture import blank_session


@pytest.fixture
def db() -> Session:
    """A blank Postgres schema, rolled back after the test.

    Was an in-memory sqlite engine over a hand-listed subset of tables with
    JSONB compiled to TEXT. The blank schema carries every table.
    """
    with blank_session() as session:
        try:
            yield session
        finally:
            ai_prompt_registry.bust_cache()


@pytest.fixture
def seeded(db: Session) -> Session:
    ai_prompt_registry.bust_cache()
    seed_prompt_registry(db.get_bind())
    return db


# --------------------------------------------------------------------------- #
# Seed (UAC A3-A6)                                                             #
# --------------------------------------------------------------------------- #


def test_seed_creates_every_key_at_v1_with_a_production_label(seeded: Session):
    names = {r.name for r in seeded.query(AIPromptVersion).all()}
    # Counted from PROMPT_KEYS, never a literal: the registry grows whenever a module adds a
    # node (M0 added `semantic_parser`, ideation added `ideate_extractor`, SCM added its
    # explainer and market advisory), and a hardcoded total turns every one of those into a
    # false failure that says nothing about whether seeding actually works.
    assert names == set(PROMPT_KEYS.keys())
    # Counted against PROMPT_KEYS, not a literal: the key set grows (semantic_parser,
    # ideate_extractor, the SCM pair, spec_understanding...) and a hardcoded number
    # fails on every addition while proving nothing about the seed.
    assert len(names) == len(PROMPT_KEYS)
    for name in PROMPT_KEYS:
        versions = [r.version for r in seeded.query(AIPromptVersion).filter_by(name=name).all()]
        assert versions == [1]
        label = seeded.query(AIPromptLabel).filter_by(name=name, label="production").one()
        v = seeded.query(AIPromptVersion).filter_by(id=label.version_id).one()
        assert v.version == 1


def test_seed_is_idempotent(seeded: Session):
    seed_prompt_registry(seeded.get_bind())
    seed_prompt_registry(seeded.get_bind())
    assert seeded.query(AIPromptVersion).count() == len(PROMPT_KEYS)
    assert seeded.query(AIPromptLabel).count() == len(PROMPT_KEYS)


def test_seed_preserves_existing_custom_system_prompt_as_v2(db: Session):
    db.add(AIAssistantConfig(system_prompt="You are a custom pirate assistant."))
    db.commit()
    seed_prompt_registry(db.get_bind())
    rows = db.query(AIPromptVersion).filter_by(name="agent_system").order_by(AIPromptVersion.version).all()
    assert [r.version for r in rows] == [1, 2]
    label = db.query(AIPromptLabel).filter_by(name="agent_system", label="production").one()
    prod = db.query(AIPromptVersion).filter_by(id=label.version_id).one()
    assert prod.version == 2
    assert prod.template == "You are a custom pirate assistant."


def test_seed_empty_system_prompt_keeps_production_at_v1(db: Session):
    db.add(AIAssistantConfig(system_prompt=""))
    db.commit()
    seed_prompt_registry(db.get_bind())
    label = db.query(AIPromptLabel).filter_by(name="agent_system", label="production").one()
    prod = db.query(AIPromptVersion).filter_by(id=label.version_id).one()
    assert prod.version == 1


# --------------------------------------------------------------------------- #
# Resolver + fallback (UAC B1-B4)                                              #
# --------------------------------------------------------------------------- #


def test_get_prompt_returns_labelled_version(seeded: Session):
    rp = get_prompt(seeded, "agent_system")
    assert rp.version == 1
    assert "Sorento AI Orchestrator" in rp.text


def test_get_prompt_caches_after_first_hit(seeded: Session, monkeypatch):
    # Prime the cache.
    get_prompt(seeded, "router")
    # Any further DB access would raise; a cache hit must avoid it entirely.
    def _boom(*_a, **_k):
        raise AssertionError("DB should not be queried on a cache hit")

    monkeypatch.setattr(seeded, "query", _boom)
    rp = get_prompt(seeded, "router")
    assert rp.version == 1


def test_get_prompt_falls_back_when_db_raises(db: Session, monkeypatch):
    ai_prompt_registry.bust_cache()

    def _raise(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "query", _raise)
    rp = get_prompt(db, "reformulator")
    assert rp.version is None
    assert rp.text == PROMPT_KEYS["reformulator"].fallback()


def test_get_prompt_falls_back_when_no_row(db: Session):
    ai_prompt_registry.bust_cache()  # empty DB, no seed
    rp = get_prompt(db, "synthesizer")
    assert rp.version is None
    assert "USER GUIDE PROTOCOL" in rp.text


def test_render_substitutes_declared_var(seeded: Session):
    text, version = render(seeded, "reformulator", current_date="TODAY_IS_TUESDAY")
    assert version == 1
    assert "TODAY_IS_TUESDAY" in text
    assert "{{current_date}}" not in text


def test_render_missing_declared_var_raises(seeded: Session):
    with pytest.raises(ValueError) as exc:
        render(seeded, "reformulator")
    assert "current_date" in str(exc.value)


def test_publish_moves_label_and_busts_cache(seeded: Session):
    svc = AIPromptService(seeded)
    # Prime cache at v1.
    assert get_prompt(seeded, "router").version == 1
    saved = svc.save_version(
        "router", template="NEW ROUTER TEXT", commit_message="v2", user_id=None
    )
    svc.set_label("router", label="production", version_id=saved["id"], user_id=None)
    rp = get_prompt(seeded, "router")
    assert rp.version == 2
    assert rp.text == "NEW ROUTER TEXT"
    # Rollback to v1.
    v1 = seeded.query(AIPromptVersion).filter_by(name="router", version=1).one()
    svc.set_label("router", label="production", version_id=v1.id, user_id=None)
    assert get_prompt(seeded, "router").version == 1


# --------------------------------------------------------------------------- #
# Save-version validation + auto-increment (UAC A3, D3)                        #
# --------------------------------------------------------------------------- #


def test_save_version_auto_increments_per_name(seeded: Session):
    svc = AIPromptService(seeded)
    a = svc.save_version("router", template="a", commit_message="m", user_id=None)
    b = svc.save_version("router", template="b", commit_message="m", user_id=None)
    assert a["version"] == 2
    assert b["version"] == 3
    # Another key stays at v1.
    latest_agent = (
        seeded.query(AIPromptVersion)
        .filter_by(name="agent_system")
        .order_by(AIPromptVersion.version.desc())
        .first()
    )
    assert latest_agent.version == 1


def test_save_version_unknown_token_hard_blocks(seeded: Session):
    svc = AIPromptService(seeded)
    with pytest.raises(AIPromptRegistryError) as exc:
        svc.save_version(
            "router", template="hi {{oops}}", commit_message="m", user_id=None
        )
    assert exc.value.unknown_tokens == ["oops"]
    assert exc.value.status_code == 422


def test_save_version_missing_declared_var_soft_warns(seeded: Session):
    svc = AIPromptService(seeded)
    # reformulator declares {{current_date}}; omit it -> saved with a warning.
    row = svc.save_version(
        "reformulator", template="no placeholder here", commit_message="m", user_id=None
    )
    assert row["version"] == 2
    assert row["missing_vars"] == ["current_date"]


def test_save_version_blank_commit_message_blocks(seeded: Session):
    svc = AIPromptService(seeded)
    with pytest.raises(AIPromptRegistryError):
        svc.save_version("router", template="x", commit_message="   ", user_id=None)


def test_set_label_reports_production_and_staging(seeded: Session):
    svc = AIPromptService(seeded)
    saved = svc.save_version("router", template="x", commit_message="m", user_id=None)
    labels = svc.set_label("router", label="staging", version_id=saved["id"], user_id=None)
    assert labels == {"production": 1, "staging": 2}


# --------------------------------------------------------------------------- #
# get_versions carries the code fallback (Slice E: a key with no saved         #
# version opened an EMPTY editor - browser finding on every ai_extract_* key). #
# --------------------------------------------------------------------------- #


def test_get_versions_carries_the_fallback_text_for_a_key_with_no_saved_version(db: Session):
    """`db` (unseeded) is the exact bug scenario: no `AIPromptVersion` row
    exists for this key, so `versions` is genuinely empty and the FE has
    nothing but `fallback_text` to seed the editor from."""
    from app.services.ai_prompt_registry import PROMPT_KEYS

    key = "ai_extract_portal_price_tag_request"
    result = AIPromptService(db).get_versions(key)

    assert result["versions"] == []
    assert result["fallback_text"] == PROMPT_KEYS[key].fallback()
    assert "REQUIRED" in result["fallback_text"]


# --------------------------------------------------------------------------- #
# List keys shape (UAC D1)                                                     #
# --------------------------------------------------------------------------- #


def test_list_keys_shape(seeded: Session):
    rows = AIPromptService(seeded).list_keys()
    assert len(rows) == len(PROMPT_KEYS)
    by_name = {r["name"]: r for r in rows}
    assert by_name["agent_system"]["active"] is True
    assert by_name["agent_system"]["production_version"] == 1
    assert by_name["judge"]["active"] is False
    assert by_name["judge"]["activates_in"] == "M3b"
    # M0: semantic_parser is now the active front node; reformulator/router dormant.
    assert by_name["semantic_parser"]["active"] is True
    assert by_name["semantic_parser"]["variables"] == ["current_date"]
    assert by_name["reformulator"]["active"] is False
    assert by_name["router"]["active"] is False


# --------------------------------------------------------------------------- #
# Call-site wiring + metadata stamping (UAC C2-C3)                             #
# --------------------------------------------------------------------------- #


@dataclass
class _FakeChatResult:
    content: str
    prompt_tokens: int = 1
    completion_tokens: int = 1
    total_tokens: int = 2
    tool_calls: list = field(default_factory=list)


class _FakeProvider:
    def __init__(self):
        self.system_texts: list[str] = []

    def chat(self, messages, tools=None, temperature=0.0, model=None, max_tokens=0):
        for m in messages:
            if m.get("role") == "system":
                self.system_texts.append(m.get("content") or "")
        return _FakeChatResult(content="ANSWER")


@pytest.fixture
def wired_chat(seeded: Session, monkeypatch):
    import app.services.ai_assistant_service as svc_module
    from app.services.ai_assistant_service import AIAssistantChatService
    from app.services.entity_resolver import ResolutionResult

    svc = AIAssistantChatService(seeded)
    cfg = svc.cfg.get()
    cfg.api_key_ciphertext = "fake-key"
    cfg.provider = "openai"
    cfg.model = "gpt-4o-mini"
    cfg.is_enabled = True
    cfg.system_prompt = "GARBAGE_CUSTOM_PROMPT_SHOULD_BE_IGNORED"
    seeded.commit()

    svc.gov.build_auth_context = lambda user_id: AIAssistantAuthContext(  # type: ignore[assignment]
        user_id=user_id, role_ids=["r"], permission_slugs=["system.ai_assistant_chat.use"], enabled_modules=[]
    )
    svc._rag_select_tools = lambda *, standalone_query, enabled_tools, top_k: ([], [])  # type: ignore[assignment]
    svc._generate_suggestions = lambda **_k: []  # type: ignore[assignment]
    # Through `monkeypatch`, NOT a bare rebind: a raw `svc_module.resolve_references = ...` is
    # never undone, so it leaked into every test that ran later in the same session and made a
    # genuine resolution path look broken. `**_k` because this stub stands in for a function
    # whose keyword arguments are free to grow.
    monkeypatch.setattr(
        svc_module,
        "resolve_references",
        lambda *_a, **_k: ResolutionResult(tokens=[], resolutions=[], elapsed_ms=0.0),
    )
    # The rate-limit clause is `func.now() - text("interval '1 minute'")`. It
    # used to be stubbed to literal(0), because sqlite cannot parse an interval
    # literal. On Postgres that stub yields `now() - 0` -- "operator does not
    # exist: timestamp with time zone - integer". The real interval works here,
    # so the clause now runs exactly as production runs it.

    provider = _FakeProvider()
    monkeypatch.setattr(svc_module, "get_provider", lambda *a, **k: provider)
    # Neutralize MCP so _run_agent_loop resolves the registry prompts and answers.
    monkeypatch.setattr(svc_module.MCPRuntimeClient, "list_tools_with_schema", lambda self: {})
    return svc, provider


def test_respond_stamps_prompt_versions_metadata(wired_chat, seeded: Session):
    svc, _provider = wired_chat
    user = User(id="c515ac63-4eb5-5bd0-9312-cacee5637b14", email="c2@test.com", name="C2", status="ACTIVE")
    seeded.add(user)
    seeded.commit()

    _conv, msg = svc.respond(user_id="c515ac63-4eb5-5bd0-9312-cacee5637b14", conversation_id=None, message="list all orders")
    versions = (msg.metadata_json or {}).get("prompt_versions")
    assert versions, "prompt_versions must be present and non-empty"
    names = [v["name"] for v in versions]
    # M0: the front node is now the Semantic Parser, not the reformulator.
    assert "semantic_parser" in names
    assert "agent_system" in names
    assert "synthesizer" in names
    for v in versions:
        assert v["version"] == 1  # seeded fallbacks


def test_respond_ignores_config_system_prompt(wired_chat, seeded: Session):
    svc, provider = wired_chat
    user = User(id="52f70404-cbc8-54d4-88d6-418665059b1d", email="c3@test.com", name="C3", status="ACTIVE")
    seeded.add(user)
    seeded.commit()

    svc.respond(user_id="52f70404-cbc8-54d4-88d6-418665059b1d", conversation_id=None, message="list all orders")
    joined = "\n".join(provider.system_texts)
    assert "GARBAGE_CUSTOM_PROMPT_SHOULD_BE_IGNORED" not in joined
    assert "Sorento AI Orchestrator" in joined  # registry agent_system won


def test_dry_run_override_forces_version(wired_chat, seeded: Session):
    svc, provider = wired_chat
    user = User(id="56a15c49-b581-55b5-9e13-6efd5bae0abf", email="dry@test.com", name="Dry", status="ACTIVE")
    seeded.add(user)
    seeded.commit()
    # Save a v2 of agent_system with a detectable marker; do NOT publish it.
    saved = AIPromptService(seeded).save_version(
        "agent_system", template="MARKER_V2_AGENT_SYSTEM", commit_message="m", user_id=None
    )
    svc.respond(
        user_id="56a15c49-b581-55b5-9e13-6efd5bae0abf",
        conversation_id=None,
        message="list all orders",
        prompt_overrides={"agent_system": saved["id"]},
    )
    joined = "\n".join(provider.system_texts)
    assert "MARKER_V2_AGENT_SYSTEM" in joined


# --------------------------------------------------------------------------- #
# HTTP routes: auth + guards (UAC D1, D5, D6)                                  #
# --------------------------------------------------------------------------- #


@pytest.fixture
def api(seeded: Session, monkeypatch):
    """TestClient wired to the seeded sqlite session, with a controllable
    permission gate. ``allow`` holds the granted permission slugs."""
    from fastapi.testclient import TestClient

    import app.dependencies as deps
    from app.main import app
    from app.services.user_service import UserPermissionService

    allow: set[str] = set()

    def _override_db():
        yield seeded

    app.dependency_overrides[deps.get_db] = _override_db
    app.dependency_overrides[deps.get_current_user] = lambda: {"id": "u-admin"}
    # The system router carries a module-guard dependency resolving through
    # get_current_user_or_api_key; override it so auth resolves to our admin.
    app.dependency_overrides[deps.get_current_user_or_api_key] = lambda: {"id": "u-admin"}
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in allow,
    )
    client = TestClient(app)
    try:
        yield client, allow
    finally:
        app.dependency_overrides.pop(deps.get_db, None)
        app.dependency_overrides.pop(deps.get_current_user, None)
        app.dependency_overrides.pop(deps.get_current_user_or_api_key, None)


def test_route_list_prompts_happy(api):
    client, allow = api
    allow.add("system.ai_assistant_settings.view")
    res = client.get("/api/v1/system/ai-assistant/prompts")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == len(PROMPT_KEYS)
    assert {r["name"] for r in body} == set(PROMPT_KEYS.keys())


def test_route_list_prompts_denies_without_permission(api):
    client, _allow = api  # no grants
    res = client.get("/api/v1/system/ai-assistant/prompts")
    assert res.status_code == 403


def test_route_save_version_unknown_token_422(api):
    client, allow = api
    allow.add("system.ai_assistant_settings.edit")
    res = client.post(
        "/api/v1/system/ai-assistant/prompts/router/versions",
        json={"template": "hi {{oops}}", "commit_message": "m"},
    )
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["unknown_tokens"] == ["oops"]
    assert "error" in body


def test_route_save_version_denies_without_edit(api):
    client, allow = api
    allow.add("system.ai_assistant_settings.view")  # view only
    res = client.post(
        "/api/v1/system/ai-assistant/prompts/router/versions",
        json={"template": "x", "commit_message": "m"},
    )
    assert res.status_code == 403


def test_route_unknown_key_404(api):
    client, allow = api
    allow.add("system.ai_assistant_settings.view")
    res = client.get("/api/v1/system/ai-assistant/prompts/not_a_key/versions")
    assert res.status_code == 404


def test_route_get_versions_carries_fallback_text_on_the_wire(api):
    """`response_model` silently drops an undeclared field - asserted on the
    wire, per LESSONS-LEARNT. `router`'s v1 comes from the `seeded` fixture
    behind `api`, so this also proves `fallback_text` rides along even when a
    saved version already exists."""
    client, allow = api
    allow.add("system.ai_assistant_settings.view")
    res = client.get("/api/v1/system/ai-assistant/prompts/router/versions")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "fallback_text" in body
    assert body["fallback_text"]


def test_route_dry_run_dormant_key_400(api):
    client, allow = api
    allow.add("system.ai_assistant_settings.edit")
    # Dormant guard fires before version lookup, so any version_id yields 400.
    res = client.post(
        "/api/v1/system/ai-assistant/prompts/judge/test",
        json={"message": "hi", "version_id": "any"},
    )
    assert res.status_code == 400


def test_is_write_tool_classification():
    """Dry-run write-tool suppression predicate (review finding #2)."""
    from app.services.ai_assistant_service import _is_write_tool

    # write tools stripped during dry-run
    assert _is_write_tool("crm_forms_stock_inquiry_submit")
    assert _is_write_tool("crm_forms_complaint_submit")
    assert _is_write_tool("crm_it_support_ticket_create")
    assert _is_write_tool("crm_forms_entity_attachments_link")
    # read tools survive
    assert not _is_write_tool("crm_portal_link_get")
    assert not _is_write_tool("crm_inventory_stock_balance_list")
    assert not _is_write_tool("crm_master_products_get")
    assert not _is_write_tool("")


# --------------------------------------------------------------------------- #
# per-agent LLM: which model this agent runs on
# --------------------------------------------------------------------------- #
def test_an_agent_with_no_model_set_uses_the_global_default(seeded: Session):
    # Every row means this today, so shipping the column must change nothing until
    # somebody sets a value.
    assert ai_prompt_registry.agent_model(seeded, "spec_understanding") == (None, None)


def test_setting_an_agents_model_is_readable_at_runtime(seeded: Session):
    AIPromptService(seeded).set_agent_model(
        "spec_understanding", label="production", provider="openai", model="gpt-5.4-mini"
    )

    assert ai_prompt_registry.agent_model(seeded, "spec_understanding") == (
        "openai",
        "gpt-5.4-mini",
    )


def test_clearing_an_agents_model_returns_it_to_the_global_default(seeded: Session):
    svc = AIPromptService(seeded)
    svc.set_agent_model("spec_understanding", label="production", provider="openai", model="gpt-5.4-mini")
    svc.set_agent_model("spec_understanding", label="production", provider="", model="")

    assert ai_prompt_registry.agent_model(seeded, "spec_understanding") == (None, None)


def test_one_agents_model_does_not_leak_to_another(seeded: Session):
    AIPromptService(seeded).set_agent_model(
        "spec_understanding", label="production", provider="openai", model="gpt-5.4-mini"
    )

    assert ai_prompt_registry.agent_model(seeded, "semantic_parser") == (None, None)


def test_the_model_shows_up_in_the_agent_list(seeded: Session):
    AIPromptService(seeded).set_agent_model(
        "spec_understanding", label="production", provider="openai", model="gpt-5.4-mini"
    )

    row = next(r for r in AIPromptService(seeded).list_keys() if r["name"] == "spec_understanding")
    assert row["model"] == "gpt-5.4-mini"
    assert row["provider"] == "openai"


# ---------------------------------------------------------------------------
# AC-S2-1 (PLAN-price-tag-r10.md S2): the price tag fallback carries rule (9),
# which stops quantity being copied off the document - quantity is how many
# TAGS marketing wants, never something a scanned document states.
# ---------------------------------------------------------------------------


def test_ac_s2_1_price_tag_fallback_carries_rule_9_omit_quantity():
    from app.services.ai_prompt_registry import _ai_extract_price_tag_fallback

    text = _ai_extract_price_tag_fallback()

    assert "(9)" in text
    assert "Omit `quantity`" in text

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
pytestmark = pytest.mark.parser_prompt_rows
