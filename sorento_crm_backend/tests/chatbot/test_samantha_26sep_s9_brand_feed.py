"""Phase 2 RED tests - issue #1262 (the Samantha case), GROUP C brand slice, slice 9
(finding F1a), UAC `chatbot-samantha-slices-26sep-acceptance-criteria.md` AC-S9-1 /
AC-S9-2. Round 3 section 6 steps 1 to 3 (the brand feed): the engine reads the live
`brands` table for the contact's companies and writes one `Known brands:` line into the
parser's user block, at BOTH call sites (first parse and the recall re-parse); the
prompt refers to that line instead of a hard-coded name list.

Written before any of the feed exists - `build_user_block` has no `brands` parameter
today, and neither `engine.py` call site (~1423, ~1496) passes one. No implementation
looked at beyond what is read here to confirm the RED reason.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.product import Brand
from app.services.chatbot.head import parser as parser_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, stub_access, stub_parser  # noqa: F401


def _seed_brand(session_factory, *, name: str, code: str, is_active: bool = True, company_id: str = DEFAULT_COMPANY_ID) -> str:
    import uuid

    db = session_factory()
    # This harness's sessions default to `UNSET` company scope (fail-closed) unlike the
    # root `tests/conftest.py` Sorento default - stamped explicitly here so the seeded
    # row is both insertable and later queryable by this file's own direct reads
    # (`_active_brand_rows`). A real turn's own session gets its scope from
    # `engine._scoped_factory`/`_contact_company_scope`, never from this helper.
    set_company_scope(db, frozenset({company_id}))
    row = Brand(id=str(uuid.uuid4()), brand_name=name, brand_code=code, is_active=is_active, company_id=company_id)
    db.add(row)
    db.commit()
    return row.id


def _active_brand_rows(session_factory, *, company_ids) -> list[dict[str, Any]]:
    """The read step design step 1 describes: active brands for the given companies,
    freshly queried - no cache, no memoisation. Exercised directly here since no
    production function under this name exists yet; the production wiring is graded
    separately by `TestEngineCallSitesPassBrands` below."""
    db = session_factory()
    set_company_scope(db, frozenset(company_ids))
    rows = (
        db.query(Brand)
        .filter(Brand.is_active.is_(True), Brand.company_id.in_(list(company_ids)))
        .order_by(Brand.brand_name)
        .all()
    )
    return [{"brand_name": r.brand_name, "brand_code": r.brand_code} for r in rows]


class TestKnownBrandsLine:
    """AC-S9-1: one `Known brands:` line, active brands only, built fresh every call."""

    def test_user_block_carries_the_live_brand_list_active_only(self, session_factory) -> None:
        _seed_brand(session_factory, name="Sorento", code="SRT")
        _seed_brand(session_factory, name="Mocha", code="MCH")
        _seed_brand(session_factory, name="Cabana", code="CBN", is_active=False)

        active = _active_brand_rows(session_factory, company_ids=[DEFAULT_COMPANY_ID])
        assert [r["brand_name"] for r in active] == ["Mocha", "Sorento"]

        # `build_user_block` has no `brands` parameter today - this is the missing
        # argument the whole slice adds, not a fixture bug.
        block = parser_mod.build_user_block(
            previous_response=None,
            latest_user_message="brand Sorento",
            pending_kind=None,
            brands=active,
        )
        assert "Known brands: Mocha (MCH), Sorento (SRT)" in block, block
        assert "Cabana" not in block, block

    def test_a_brand_edited_in_the_table_shows_up_on_the_next_build_with_no_restart(
        self, session_factory
    ) -> None:
        brand_id = _seed_brand(session_factory, name="Sorento", code="SRT")
        first = _active_brand_rows(session_factory, company_ids=[DEFAULT_COMPANY_ID])
        block1 = parser_mod.build_user_block(
            previous_response=None, latest_user_message="x", pending_kind=None, brands=first,
        )
        assert "Sorento (SRT)" in block1, block1

        db = session_factory()
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        db.query(Brand).filter(Brand.id == brand_id).update({"is_active": False})
        db.commit()

        second = _active_brand_rows(session_factory, company_ids=[DEFAULT_COMPANY_ID])
        block2 = parser_mod.build_user_block(
            previous_response=None, latest_user_message="x", pending_kind=None, brands=second,
        )
        assert "Sorento" not in block2, block2


class TestKnownBrandsLineAbsentWhenEmpty:
    def test_no_brands_no_known_brands_line(self) -> None:
        block = parser_mod.build_user_block(
            previous_response=None, latest_user_message="hi", pending_kind=None, brands=[],
        )
        assert "Known brands" not in block, block
        block_none = parser_mod.build_user_block(
            previous_response=None, latest_user_message="hi", pending_kind=None, brands=None,
        )
        assert "Known brands" not in block_none, block_none


#: `stub_access()` (test_engine.py) points `engine_mod.default_space_id` at this
#: literal - the seeded `respond_workspaces` row below must carry the SAME value or
#: `_contact_company_scope`'s Respond.io-id branch (which JOINS `respond_workspaces`
#: on `space_id`) finds nothing and the scope resolves empty, same gotcha
#: `test_outstanding_lane.py::_seed_contact`'s own comment names.
_SPACE_ID = "364817"


def _seed_contact_with_brand(session_factory) -> None:
    """A contact scoped to Sorento (`DEFAULT_COMPANY_ID`, already seeded on every blank
    schema - `tests/_pg_fixture.py`'s own `after_create` DDL event) - see
    `test_engine_company_scope.py`'s module docstring for the same fact."""
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_workspaces (id, space_id, name, api_key_ciphertext) "
            "VALUES (gen_random_uuid(), :sid, 'ZZT s9 brand-feed workspace', 'ZZT-cipher') "
            "ON CONFLICT DO NOTHING"
        ),
        {"sid": _SPACE_ID},
    )
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars, workspace_id) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb), "
            "(SELECT id FROM respond_workspaces WHERE space_id = :sid LIMIT 1))"
        ),
        {"cid": str(CONTACT_ID), "phone": "+60000000031", "sv": json.dumps({"variables": {}}), "sid": _SPACE_ID},
    )
    db.execute(
        text(
            "INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
            "SELECT gen_random_uuid(), id, :company_id FROM respond_contacts WHERE respond_io_id = :cid"
        ),
        {"cid": str(CONTACT_ID), "company_id": DEFAULT_COMPANY_ID},
    )
    db.commit()


class TestEngineCallSitesPassBrands:
    """AC-S9-1's other half: the engine's parse must carry the live brand list, not
    just `build_user_block` accepting one.

    Re-pointed in integration round 5 of the chatbot memory lane (PR #1304, 28 Sep
    2026), call shape only: the engine now builds the user block through
    `turn.context.assemble(ContextLayers)` (memory lane A, contract section 6.1), and the
    recall re-parse is deleted (contract section 3, Q5 ruling), so the frames a
    backward reference needs ride the ONE parse. `context_mod.assemble` is wrapped by a
    recorder that forwards to the real function, and each test grades both the
    `brands` the layers carried and the `Known brands:` line the parser actually read."""

    def _capture(self, monkeypatch) -> list[tuple[Any, str]]:
        from app.services.chatbot.turn import context as context_mod

        calls: list[tuple[Any, str]] = []
        real = context_mod.assemble

        def _spy(layers: Any) -> Any:
            text_out, report = real(layers)
            calls.append((layers, text_out))
            return text_out, report

        monkeypatch.setattr(context_mod, "assemble", _spy)
        return calls

    def test_the_first_parse_passes_brands(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact_with_brand(session_factory)
        _seed_brand(session_factory, name="Sorento", code="SRT")
        calls = self._capture(monkeypatch)
        stub_parser(_parser_output())
        stub_access()

        from app.services.chatbot import engine as engine_mod

        engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert calls, "context.assemble was never called"
        layers, block = calls[0]
        assert any(
            isinstance(b, dict) and b.get("brand_name") == "Sorento"
            for b in (layers.brands or [])
        ), layers.brands
        assert "Known brands: Sorento (SRT)" in block, block

    def test_the_recall_reparse_also_passes_brands(
        self, session_factory, stub_access, monkeypatch
    ) -> None:
        """Was: the recall re-parse also passes `brands`. The re-parse is gone (memory
        lane A, Q5), so the same promise now reads: a backward-reference turn at a level
        that recalls frames parses ONCE, and that one parse carries both the recalled
        frame and the brand list."""
        from app.models.conversation_frame import ConversationFrame
        from tests.chatbot._turn_helpers import verdict

        _seed_contact_with_brand(session_factory)
        _seed_brand(session_factory, name="Sorento", code="SRT")
        db = session_factory()
        db.execute(
            text("UPDATE respond_contacts SET chatbot_memory_level = 'episodes' WHERE respond_io_id = :cid"),
            {"cid": str(CONTACT_ID)},
        )
        db.commit()
        db.add(
            ConversationFrame(
                contact_id=str(CONTACT_ID),
                contact_respond_id=str(CONTACT_ID),
                space_id="364817",
                channel="whatsapp",
                domain="inventory",
                intent="check_stock",
                summary="ZZT recall summary for brand feed",
                status="closed",
            )
        )
        db.commit()

        calls = self._capture(monkeypatch)
        parses: list[str] = []

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        def fake_parse(config, user_block):
            parses.append(user_block)
            return verdict(anaphora={"backward_reference": True})

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", fake_parse)
        stub_access()

        from app.services.chatbot import engine as engine_mod

        engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert len(parses) == 1, f"one parse per turn, no recall re-parse: got {len(parses)}"
        assert len(calls) == 1, f"one assembled user block per turn: got {len(calls)}"
        layers, block = calls[0]
        assert any(
            isinstance(b, dict) and b.get("brand_name") == "Sorento" for b in (layers.brands or [])
        ), layers.brands
        assert "Known brands: Sorento (SRT)" in parses[0], parses[0]
        assert "ZZT recall summary for brand feed" in parses[0], parses[0]


class TestPromptHasNoHardCodedBrandList:
    """AC-S9-2: the parser prompt refers to the `Known brands` line instead of
    hard-coding "Sorento, Mocha, or Cabana" - shipped as a new UNLABELLED version of
    `chatbot_semantic_parser` (owner ruling 10, same immutable-versions-plus-movable-
    labels split as migrations 475/480/490/521), never by editing the `production`
    label directly.

    Coordinator round, 26 Sep 2026: `create_all` (the blank-schema fixture every
    other test in this file uses) never runs an alembic migration, so a DB read of
    `ai_prompt_versions` on that schema finds nothing regardless of whether the
    migration exists - the wrong seam to grade this from. Follows
    `test_parser_growth_r1_reachability.py::TestTheOutstandingVocabularyIsPublished`
    instead: import the migration module directly and assert on its own
    `_full_text()` (the exact body it publishes), no DB needed."""

    def _migration(self):
        import importlib.util
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "alembic"
            / "versions"
            / "chatbot_known_brands.py"
        )
        assert path.exists(), (
            "no migration publishes the Known-brands prompt, so it reaches no live "
            "prompt version (AC-S9-2)"
        )
        spec = importlib.util.spec_from_file_location("zzt_known_brands_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_migration_publishes_known_brands_and_drops_the_hardcoded_list(self) -> None:
        """`_full_text()` is `SEMANTIC_PARSER_PROMPT` (the body `production` sits on) -
        the one this AC is about, and (fix lane round 2, N2) the only body this
        migration publishes."""
        module = self._migration()
        assert callable(module.publish)
        full = module._full_text()
        assert "Known brands" in full, full[:200]
        assert "Sorento, Mocha, or Cabana" not in full, (
            "the published FULL prompt must no longer hard-code the brand list"
        )


class TestN2DowngradeOnlyDropsWhatThisMigrationPublished:
    """Fix lane round 2, N2: the legacy `SLIM_V1` body (the draft's `_slim_text()`), which an
    EARLIER migration already published - so this migration's own SLIM publish is always
    a no-op, and its downgrade, which deleted by template equality over BOTH texts,
    deleted that earlier migration's unlabelled SLIM row. The downgrade must drop only
    the version this migration published."""

    def test_downgrade_keeps_an_earlier_unlabelled_slim_row(self, session_factory, monkeypatch) -> None:
        from types import SimpleNamespace

        from app.models.ai_prompt import AIPromptVersion

        module = TestPromptHasNoHardCodedBrandList._migration(None)
        db = session_factory()
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(module.__file__).resolve().parent.parent))
        from _legacy_prompt_bodies import SEMANTIC_PARSER_PROMPT_SLIM_V1 as slim_v1
        db.add(
            AIPromptVersion(
                name="chatbot_semantic_parser", version=901, type="text", template=slim_v1, variables=[]
            )
        )
        db.add(
            AIPromptVersion(
                name="chatbot_semantic_parser",
                version=902,
                type="text",
                template=module._full_text(),
                variables=[],
            )
        )
        db.commit()
        monkeypatch.setattr(module, "op", SimpleNamespace(get_bind=lambda: db.connection()))

        module.downgrade()

        check = session_factory()
        left = {
            v
            for (v,) in check.query(AIPromptVersion.version)
            .filter(AIPromptVersion.name == "chatbot_semantic_parser")
            .all()
        }
        assert 901 in left, "the downgrade deleted an earlier migration's unlabelled SLIM row"
        assert 902 not in left, "the downgrade must still drop the version this migration published"

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
import pytest  # noqa: E402

pytestmark = pytest.mark.parser_prompt_rows
