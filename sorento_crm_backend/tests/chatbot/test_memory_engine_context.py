"""S3 - memory reaches the parser through the ENGINE, under budget, once per turn -
tester-first RED, from the UAC and the lane A contract (sections 2, 3, 6).

Covers AC-MEM062, AC-MEM063, AC-MEM070, AC-MEM073.

**No implementation exists yet.** `app/services/chatbot/turn/context.py` does not exist,
and the engine still calls the OLD `memory_mod.recall`/`memory_mod.episodes_block`
(`engine.py:1492-1520`) behind `chatbot_recall_enabled` + `anaphora.backward_reference`,
which is exactly the double-parser-call S3 must delete. Every test below fails either at
`ModuleNotFoundError` (the `context` trace event / assemble path) or on a real assertion
against today's still-live recall re-parse.

Postgres only (`tests/chatbot/conftest.py::session_factory`, blank scratch schema); every
contact/frame chain seeded fresh.

**Ambiguity flagged to the captain**: "an `off` contact's user block equals today's" is
tested the same way `test_context_assemble.py`'s own off-level test does (byte parity
against `parser.build_user_block`), but THROUGH the engine this time - captured via the
`stub_parser`'s `on_call` hook, the same seam `test_rearch_s3_profile_hints.py` uses.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.conversation_frame import ConversationFrame
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access, stub_parser  # noqa: F401


def _cid() -> str:
    return f"ZZT-ctxeng-{uuid.uuid4().hex[:10]}"


def _seed_contact(session_factory, contact_respond_id: str, *, memory_level: str | None = None) -> None:
    # `chatbot_recall_enabled` is retired (round 3, AC-MEM054, merged 5b110df8) -
    # dropped from the table outright, so this seed no longer names it.
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": contact_respond_id, "phone": f"+6011{uuid.uuid4().hex[:8]}", "sv": json.dumps({"variables": {}})},
    )
    db.commit()
    if memory_level is not None:
        db.execute(
            text("UPDATE respond_contacts SET chatbot_memory_level = :lvl WHERE respond_io_id = :cid"),
            {"lvl": memory_level, "cid": contact_respond_id},
        )
        db.commit()


def _seed_closed_frame(session_factory, *, contact_respond_id: str, summary: str, days_ago: int = 1) -> None:
    db = session_factory()
    when = datetime.now() - timedelta(days=days_ago)
    db.add(ConversationFrame(
        contact_id=contact_respond_id, contact_respond_id=contact_respond_id, space_id="0",
        channel="whatsapp", domain="inventory", status="closed", close_reason="topic_switch",
        summary=summary, entities={"product": ["SRTWB1455"]}, turn_ids=[f"ZZT-ctxeng-frame-{uuid.uuid4().hex[:6]}"],
        started_at=when, opened_at=when, last_activity_at=when, closed_at=when,
    ))
    db.commit()


# --------------------------------------------------------------------------- #
# AC-MEM062: every parse records a `context` trace event
# --------------------------------------------------------------------------- #


class TestContextTraceEvent:
    def test_context_event_carries_layers_total_and_cap(self, session_factory, stub_parser, stub_access) -> None:
        from app.services.chatbot import engine as engine_mod
        from app.models.chatbot_turn import ChatbotTurn

        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid, memory_level="full")
        stub_access()
        stub_parser(verdict(domain_hint="inventory", entities=[entity("SRTWB1455")]))
        envelope = _envelope()
        envelope.message["message"]["messageId"] = "ZZT-ctxeng-trace-1"
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        context_records = [r for r in (row.trace or []) if isinstance(r, dict) and r.get("kind") == "context"]
        assert context_records, f"expected a `context` trace event, trace was: {row.trace}"
        payload = context_records[-1]
        assert set(payload) >= {"level", "layers", "total_est_tokens", "cap"}, payload
        assert payload["cap"] == 1800, payload
        for entry in payload["layers"]:
            assert set(entry) >= {"layer", "est_tokens", "cap", "dropped"}, entry


# --------------------------------------------------------------------------- #
# AC-MEM052 (round 3, merged 5b110df8): the `context` trace event also records
# WHERE the level came from - own / default / off.
# --------------------------------------------------------------------------- #


class TestContextEventLevelSource:
    def _context_payload(self, session_factory, result):
        from app.models.chatbot_turn import ChatbotTurn

        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        records = [r for r in (row.trace or []) if isinstance(r, dict) and r.get("kind") == "context"]
        assert records, f"expected a `context` trace event, trace was: {row.trace}"
        return records[-1]

    def _set_system_memory(self, session_factory, *, enabled: bool, default_level: str) -> None:
        from app.models.user import SystemSetting

        db = session_factory()
        row = db.query(SystemSetting).first()
        if row is None:
            row = SystemSetting()
            db.add(row)
        row.chatbot_memory = {"enabled": enabled, "default_level": default_level}
        db.commit()

    def test_a_contacts_own_level_reports_source_own(
        self, session_factory, stub_parser, stub_access
    ) -> None:
        from app.services.chatbot import engine as engine_mod

        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid, memory_level="full")
        stub_access()
        stub_parser(verdict(domain_hint="inventory", entities=[entity("SRTWB1455")]))
        envelope = _envelope()
        envelope.message["message"]["messageId"] = "ZZT-ctxeng-src-own-1"
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        payload = self._context_payload(session_factory, result)
        assert payload.get("level_source") == "own", payload

    def test_no_own_level_with_memory_enabled_reports_source_default(
        self, session_factory, stub_parser, stub_access
    ) -> None:
        from app.services.chatbot import engine as engine_mod

        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid, memory_level=None)
        self._set_system_memory(session_factory, enabled=True, default_level="episodes")
        stub_access()
        stub_parser(verdict(domain_hint="inventory", entities=[entity("SRTWB1455")]))
        envelope = _envelope()
        envelope.message["message"]["messageId"] = "ZZT-ctxeng-src-default-1"
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        payload = self._context_payload(session_factory, result)
        assert payload.get("level_source") == "default", payload

    def test_memory_disabled_with_no_own_level_reports_source_off(
        self, session_factory, stub_parser, stub_access
    ) -> None:
        from app.services.chatbot import engine as engine_mod

        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid, memory_level=None)
        self._set_system_memory(session_factory, enabled=False, default_level="full")
        stub_access()
        stub_parser(verdict(domain_hint="inventory", entities=[entity("SRTWB1455")]))
        envelope = _envelope()
        envelope.message["message"]["messageId"] = "ZZT-ctxeng-src-off-1"
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        payload = self._context_payload(session_factory, result)
        assert payload.get("level_source") == "off", payload


# --------------------------------------------------------------------------- #
# AC-MEM056 (round 3, merged 5b110df8): toggling "Memory for all contacts" on
# changes the NEXT turn's resolved level for a contact with no own level, and
# does not change one that has its own level set - across two REAL turns, not a
# unit call, so a per-turn cache (there is none today, but nothing pins it) would
# be caught here.
# --------------------------------------------------------------------------- #


class TestAC_MEM056TogglingMemoryAffectsOnlyNoOwnLevelContacts:  # noqa: N801
    def _context_payload(self, session_factory, result):
        from app.models.chatbot_turn import ChatbotTurn

        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        records = [r for r in (row.trace or []) if isinstance(r, dict) and r.get("kind") == "context"]
        assert records, f"expected a `context` trace event, trace was: {row.trace}"
        return records[-1]

    def _set_system_memory(self, session_factory, *, enabled: bool, default_level: str) -> None:
        from app.models.user import SystemSetting

        db = session_factory()
        row = db.query(SystemSetting).first()
        if row is None:
            row = SystemSetting()
            db.add(row)
        row.chatbot_memory = {"enabled": enabled, "default_level": default_level}
        db.commit()

    def test_a_no_own_level_contact_follows_the_switch_across_turns(
        self, session_factory, stub_parser, stub_access
    ) -> None:
        from app.services.chatbot import engine as engine_mod

        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid, memory_level=None)
        self._set_system_memory(session_factory, enabled=False, default_level="conversation")
        stub_access()
        stub_parser(verdict(domain_hint="inventory", entities=[entity("SRTWB1455")]))

        e1 = _envelope()
        e1.message["message"]["messageId"] = "ZZT-ctxeng-mem056-noown-1"
        r1 = engine_mod.run_turn(e1, session_factory=session_factory)
        p1 = self._context_payload(session_factory, r1)
        assert p1.get("level") == "off", p1
        assert p1.get("level_source") == "off", p1

        self._set_system_memory(session_factory, enabled=True, default_level="conversation")

        e2 = _envelope()
        e2.message["message"]["messageId"] = "ZZT-ctxeng-mem056-noown-2"
        r2 = engine_mod.run_turn(e2, session_factory=session_factory)
        p2 = self._context_payload(session_factory, r2)
        assert p2.get("level") == "conversation", (
            f"a no-own-level contact's NEXT turn must pick up the newly-enabled "
            f"system default: {p2}"
        )
        assert p2.get("level_source") == "default", p2

    def test_a_contact_with_its_own_level_off_ignores_the_switch(
        self, session_factory, stub_parser, stub_access
    ) -> None:
        from app.services.chatbot import engine as engine_mod

        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid, memory_level="off")
        self._set_system_memory(session_factory, enabled=False, default_level="full")
        stub_access()
        stub_parser(verdict(domain_hint="inventory", entities=[entity("SRTWB1455")]))

        e1 = _envelope()
        e1.message["message"]["messageId"] = "ZZT-ctxeng-mem056-own-1"
        r1 = engine_mod.run_turn(e1, session_factory=session_factory)
        p1 = self._context_payload(session_factory, r1)
        assert p1.get("level") == "off", p1
        assert p1.get("level_source") == "own", p1

        self._set_system_memory(session_factory, enabled=True, default_level="full")

        e2 = _envelope()
        e2.message["message"]["messageId"] = "ZZT-ctxeng-mem056-own-2"
        r2 = engine_mod.run_turn(e2, session_factory=session_factory)
        p2 = self._context_payload(session_factory, r2)
        assert p2.get("level") == "off", (
            f"a contact with an explicit own level of off must NOT change when the "
            f"system switch flips on: {p2}"
        )
        assert p2.get("level_source") == "own", p2


# --------------------------------------------------------------------------- #
# AC-MEM063 (Q5): recall re-parse is gone
# --------------------------------------------------------------------------- #


class TestRecallDeleted:
    def test_memory_module_has_no_recall_or_episodes_block(self) -> None:
        from app.services.chatbot.turn import memory as memory_mod

        assert not hasattr(memory_mod, "recall"), (
            "memory.recall must be deleted - the last-3-summaries context layer "
            "replaces it (contract section 5.4)"
        )
        assert not hasattr(memory_mod, "episodes_block"), "memory.episodes_block must be deleted"

    def test_parser_called_exactly_once_even_with_backward_reference_and_recall_toggle_on(
        self, session_factory, stub_access, monkeypatch
    ) -> None:
        """Forces the OLD code's second-parse branch to have something to re-parse
        with (`memory_mod.recall` stubbed non-empty) - otherwise this passes today
        for the WRONG reason (no frames means nothing to recall, so today's code
        also calls the parser once, coincidentally)."""
        from app.services.chatbot import engine as engine_mod
        from app.services.chatbot.head import parser as parser_mod
        from app.services.chatbot.turn import memory as memory_mod

        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid, memory_level="full")  # chatbot_recall_enabled=True too
        stub_access()

        if hasattr(memory_mod, "recall"):
            monkeypatch.setattr(
                memory_mod, "recall",
                lambda contact_respond_id, verdict, db, k=3: [
                    {"id": "ZZT-fake-frame-1", "domain": "inventory", "intent": None,
                     "summary": "stock SRTWB1455 (answered)", "entities": {}}
                ],
            )

        calls: list[str] = []

        def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        def fake_parse(config, user_block):
            calls.append(user_block)
            return verdict(
                domain_hint="inventory", entities=[entity("SRTWB1455")],
                anaphora={"backward_reference": True},
            )

        import pytest as _pytest
        from unittest import mock

        with mock.patch.object(parser_mod, "resolve_config", fake_resolve_config), \
             mock.patch.object(parser_mod, "parse", fake_parse):
            envelope = _envelope()
            envelope.message["message"]["messageId"] = "ZZT-ctxeng-oneparse-1"
            engine_mod.run_turn(envelope, session_factory=session_factory)

        assert len(calls) == 1, f"expected exactly ONE parser call, got {len(calls)}"

    def test_no_recall_trace_kind_anywhere_in_the_turn(self, session_factory, stub_parser, stub_access) -> None:
        from app.services.chatbot import engine as engine_mod
        from app.models.chatbot_turn import ChatbotTurn

        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid, memory_level="full")
        stub_access()
        stub_parser(verdict(domain_hint="inventory", entities=[entity("SRTWB1455")], anaphora={"backward_reference": True}))
        envelope = _envelope()
        envelope.message["message"]["messageId"] = "ZZT-ctxeng-norecall-1"
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        kinds = {r.get("kind") for r in (row.trace or []) if isinstance(r, dict)}
        assert "recall" not in kinds, f"the 'recall' trace kind must be gone, found in: {kinds}"

    def test_frames_search_route_still_answers(self, session_factory, monkeypatch) -> None:
        from fastapi.testclient import TestClient

        from app.dependencies import get_db, get_external_api_user
        from app.main import app
        from app.services.user_service import UserPermissionService

        def _override_db():
            yield session_factory()

        monkeypatch.setattr(
            UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True
        )
        monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"superadmin"})
        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_external_api_user] = lambda: {"id": "ZZT-external-test", "role": "superadmin"}
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/external/memory/frames/search",
                json={"contact_id": "ZZT-no-such-contact", "space_id": "0", "query_text": "stock", "k": 3},
            )
            assert resp.status_code == 200, resp.text
            assert "frames" in resp.json(), resp.json()
        finally:
            app.dependency_overrides.clear()

    def test_full_level_contact_parse_includes_last_frame_summary_and_earlier_live_message(
        self, session_factory, stub_access
    ) -> None:
        from app.services.chatbot import engine as engine_mod
        from app.services.chatbot.head import parser as parser_mod

        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid, memory_level="full")
        _seed_closed_frame(session_factory, contact_respond_id=cid, summary="Thu 25 Sep: stock SRTWB1455 (answered).")
        stub_access()

        captured: list[str] = []

        def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        def fake_parse(config, user_block):
            captured.append(user_block)
            return verdict(domain_hint="inventory", entities=[entity("SRTWB1455")])

        from unittest import mock

        with mock.patch.object(parser_mod, "resolve_config", fake_resolve_config), \
             mock.patch.object(parser_mod, "parse", fake_parse):
            e1 = _envelope()
            e1.message["message"]["messageId"] = "ZZT-ctxeng-full-1"
            e1.message["message"]["message"]["text"] = "stock SRTWB1455"
            engine_mod.run_turn(e1, session_factory=session_factory)

            e2 = _envelope()
            e2.message["message"]["messageId"] = "ZZT-ctxeng-full-2"
            e2.message["message"]["message"]["text"] = "and in kuching?"
            engine_mod.run_turn(e2, session_factory=session_factory)

        assert len(captured) == 2
        second_block = captured[1]
        # Fix round 6: an old-shape stored summary reaches the parser as the readable
        # line (`readable_summary`), never its bracket tags.
        assert "Stock: Asked about stock for SRTWB1455 and got an answer." in second_block, second_block
        assert "(answered)" not in second_block, second_block
        assert "stock SRTWB1455" in second_block, (
            "the LIVE episode's earlier message (this same conversation's first turn) "
            f"must also reach the second parse: {second_block}"
        )

    def test_off_level_contact_user_block_equals_today(self, session_factory, stub_access) -> None:
        from app.services.chatbot import engine as engine_mod
        from app.services.chatbot.head import parser as parser_mod

        cid = str(CONTACT_ID)
        db = session_factory()
        db.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
                "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
            ),
            {"cid": cid, "phone": f"+6011{uuid.uuid4().hex[:8]}", "sv": json.dumps({"variables": {}})},
        )
        db.commit()
        stub_access()

        captured: list[str] = []

        def fake_resolve_config(db2, *, current_date, override_version_id=None, grants=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        def fake_parse(config, user_block):
            captured.append(user_block)
            return verdict(domain_hint="inventory", entities=[entity("SRTWB1455")])

        from unittest import mock

        with mock.patch.object(parser_mod, "resolve_config", fake_resolve_config), \
             mock.patch.object(parser_mod, "parse", fake_parse):
            envelope = _envelope()
            envelope.message["message"]["messageId"] = "ZZT-ctxeng-off-1"
            engine_mod.run_turn(envelope, session_factory=session_factory)

        assert captured, "the parser was never called"
        block = captured[0]
        assert "Earlier in this conversation" not in block, block
        assert "Recent conversations" not in block, block
        assert "About this contact" not in block, block

    def test_off_level_contact_still_learns_and_saves_a_stated_fact(
        self, session_factory, stub_access
    ) -> None:
        """AC-MEM049 (round 3, plan 6.0): NO memory layer reaches the parser at
        level off, but a stated `profile_statements` fact on a LIVE turn is still
        learned and saved - "Off" gates what the assistant is TOLD, never what it
        LEARNS."""
        from app.services.chatbot import engine as engine_mod

        cid = str(CONTACT_ID)
        db = session_factory()
        db.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
                "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
            ),
            {"cid": cid, "phone": f"+6011{uuid.uuid4().hex[:8]}", "sv": json.dumps({"variables": {}})},
        )
        db.commit()
        stub_access()

        from app.services.chatbot.head import parser as parser_mod
        from unittest import mock

        def fake_resolve_config(db2, *, current_date, override_version_id=None, grants=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        def fake_parse(config, user_block):
            return verdict(
                domain_hint="inventory",
                entities=[entity("SRTWB1455")],
                profile_statements=[{"key": "role", "value": "purchaser"}],
            )

        with mock.patch.object(parser_mod, "resolve_config", fake_resolve_config), \
             mock.patch.object(parser_mod, "parse", fake_parse):
            envelope = _envelope()
            envelope.message["message"]["messageId"] = "ZZT-ctxeng-off-fact-1"
            engine_mod.run_turn(envelope, session_factory=session_factory)

        stored = session_factory().execute(
            text("SELECT chatbot_profile FROM respond_contacts WHERE respond_io_id = :c"), {"c": cid}
        ).scalar()
        facts = (stored or {}).get("facts") or []
        assert any(f.get("key") == "role" and f.get("value") == "purchaser" for f in facts), (
            f"a stated fact must be learned and saved even at context level off, got facts={facts}"
        )

    def test_a_dry_run_never_writes_the_profile_at_any_level(
        self, session_factory, stub_access
    ) -> None:
        """AC-MEM049/AC-MEM033: "a dry run still never writes the profile" - D14's
        blanket no-writes-outside-`chatbot.turns` rule, pinned specifically for a
        stated `profile_statements` fact so a level-off carve-out cannot also
        accidentally carve out dry runs."""
        from app.services.chatbot import engine as engine_mod

        cid = str(CONTACT_ID)
        db = session_factory()
        db.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
                "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
            ),
            {"cid": cid, "phone": f"+6011{uuid.uuid4().hex[:8]}", "sv": json.dumps({"variables": {}})},
        )
        db.commit()
        stub_access()

        from app.services.chatbot.head import parser as parser_mod
        from unittest import mock

        def fake_resolve_config(db2, *, current_date, override_version_id=None, grants=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        def fake_parse(config, user_block):
            return verdict(
                domain_hint="inventory",
                entities=[entity("SRTWB1455")],
                profile_statements=[{"key": "role", "value": "purchaser"}],
            )

        with mock.patch.object(parser_mod, "resolve_config", fake_resolve_config), \
             mock.patch.object(parser_mod, "parse", fake_parse):
            envelope = _envelope(test_run_id="ZZT-ctxeng-dry-fact-1")
            assert envelope.dry_run is True
            engine_mod.run_turn(envelope, session_factory=session_factory)

        stored = session_factory().execute(
            text("SELECT chatbot_profile FROM respond_contacts WHERE respond_io_id = :c"), {"c": cid}
        ).scalar()
        facts = (stored or {}).get("facts") or []
        assert not any(f.get("key") == "role" for f in facts), (
            f"a dry run must never write the profile, even at level off, got facts={facts}"
        )


# --------------------------------------------------------------------------- #
# AC-MEM070: memory never overrides the current message
# --------------------------------------------------------------------------- #


class TestMemoryNeverOverridesCurrentMessage:
    def test_usual_products_srtwb1455_message_m483bl_keeps_only_m483bl(
        self, session_factory, stub_parser, stub_access
    ) -> None:
        """A UNIT-level guard, not the load-bearing proof of AC-MEM070: with the
        parser STUBBED to return a verdict already naming only M483-BL, nothing in
        today's engine reads `chatbot_profile.facts` into focus at all, so this
        passes today whether or not the coder's context layer exists - the real
        proof that memory never overrides what the MODEL sees is the live-parser
        8.2 evaluation and the replay case in `replay_turns/memory/case-d-
        no-carry-over-negative.json` (file 7), which drives the assembled PROMPT
        the model reads, not just APPLY's handling of an already-resolved verdict."""
        from app.services.chatbot import engine as engine_mod
        from app.models.chatbot_turn import ChatbotTurn

        cid = str(CONTACT_ID)
        _seed_contact(session_factory, cid, memory_level="full")
        db = session_factory()
        db.execute(
            text(
                "UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) WHERE respond_io_id = :cid"
            ),
            {
                "cid": cid,
                "p": json.dumps({"facts": [
                    {"key": "usual_products", "value": ["SRTWB1455"], "source": "tallied",
                     "source_ref": None, "first_seen": "2026-09-01", "last_seen": "2026-09-20",
                     "seen_count": 3, "set_by": None},
                ]}),
            },
        )
        db.commit()
        stub_access()
        stub_parser(verdict(domain_hint="inventory", entities=[entity("M483-BL")]))
        envelope = _envelope()
        envelope.message["message"]["messageId"] = "ZZT-ctxeng-override-1"
        envelope.message["message"]["message"]["text"] = "stock M483-BL"
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        memory_records = [r for r in (row.trace or []) if isinstance(r, dict) and r.get("kind") == "memory"]
        assert memory_records, row.trace
        focus_after = json.dumps(memory_records[-1].get("focus", {}).get("after") or {})
        assert "SRTWB1455" not in focus_after, (
            f"a tallied usual_products fact must never be carried alongside a "
            f"current-message product it was not asked about: {focus_after}"
        )
        assert "M483-BL" in focus_after, focus_after


# --------------------------------------------------------------------------- #
# AC-MEM073: a new respond_contacts row defaults level NULL, recall_enabled false
# --------------------------------------------------------------------------- #


class TestNewContactDefaults:
    def test_new_contact_has_null_memory_level_and_resolves_to_off(self, session_factory) -> None:
        cid = _cid()
        db = session_factory()
        db.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
                "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
            ),
            {"cid": cid, "phone": f"+6011{uuid.uuid4().hex[:8]}", "sv": json.dumps({})},
        )
        db.commit()
        row = db.execute(
            text(
                "SELECT chatbot_memory_level FROM respond_contacts "
                "WHERE respond_io_id = :cid"
            ),
            {"cid": cid},
        ).one()
        assert row[0] is None, row
        # AC-MEM054 / AC-MEM073 (round 3): chatbot_recall_enabled is dropped; a new contact
        # follows the system default, which ships off.
        from app.modules.chatbot.lane_vocabulary import default_chatbot_memory
        from app.services.chatbot.turn.memory import resolve_level

        assert resolve_level(row[0], default_chatbot_memory()) == "off"
