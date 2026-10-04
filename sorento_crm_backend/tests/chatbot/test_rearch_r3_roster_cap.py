"""R3 RED tests - roster cap is data (UAC AC-1710; PLAN-chatbot-answer-half-reattach.md
"Roster cap (owner ruling 20 Sep 2026): configurable, default 10").

PICKER-NO-CAP (owner, 2 Oct 2026): the default is now 50, the S3 ceiling (migration
`picker_no_cap_0001`); the history below describes the column as R3 introduced it.

New column `chatbot_entity_kinds.roster_cap` (INTEGER NOT NULL, server_default 10),
migration `chatbot_rearch_s10` (down_revision `chatbot_rearch_s9`, the current single
head), exposed on `ChatbotEntityKindBody`/`ChatbotEntityKindResponse`/`_kind_out`
(`app/api/v1/system/chatbot_config.py`), read at the customer roster cap
(`gate.py:938`, today a literal `[:8]`) and the product/attachment roster cap
(`gate.py`'s `specific_options` build, today UNCAPPED). The did-you-mean list cap is
slice R4, out of this file's scope (per the captain's brief).

**Captain ruling, 20 Sep 2026, on the plumbing gap this file's first pass measured
and flagged (no config seam reaches `gate.run_gate` or `resolve_gate.run` today -
`did_you_mean`'s own path never touches either): the name is now PINNED, not
guessed.** `gate.run_gate(..., roster_caps: Mapping[str, int] | None = None)` and
the same keyword on `resolve_gate.run`; keys are entity kinds (`"customer"`,
`"product"`), a missing key or `None` means 10. `turn_runtime.resolve_kinds` reads
the caps off `chatbot_entity_kinds` rows and passes them down - `TestResolveKinds
PassesRosterCapsFromSeededRows` pins that seam directly.

Because the name is now pinned, the cap tests below call `run_gate(...,
roster_caps=...)` DIRECTLY - no graceful-degrade shim, no `inspect.signature`
fallback: a coder who names the parameter differently gets a `TypeError` naming the
wrong keyword, which is the correct failure now that the contract is fixed rather
than guessed.
"""
from __future__ import annotations

import inspect
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.main import app
from app.services.chatbot.lanes.business import gate as gate_mod
from app.services.chatbot.lanes.business import resolve_gate as resolve_gate_mod
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session, pg_session, unique_code

BACKEND_ROOT = Path(__file__).resolve().parents[2]
GATE_PY = BACKEND_ROOT / "app" / "services" / "chatbot" / "lanes" / "business" / "gate.py"
ALEMBIC_VERSIONS = BACKEND_ROOT / "alembic" / "versions"

VIEW = "system.chat_history.view"
MANAGE = "system.chatbot_config.manage"
ENTITY_KINDS_BASE = "/api/v1/system/chatbot/entity-kinds"


# --------------------------------------------------------------------------- #
# Migration
# --------------------------------------------------------------------------- #


class TestMigrationExistsAndChainsOntoTheCurrentHead:
    def test_chatbot_rearch_s10_file_exists_with_the_right_shape(self) -> None:
        path = ALEMBIC_VERSIONS / "chatbot_rearch_s10.py"
        assert path.exists(), f"missing migration: {path}"
        import importlib.util

        spec = importlib.util.spec_from_file_location("chatbot_rearch_s10", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        assert module.revision == "chatbot_rearch_s10"
        assert len(module.revision) <= 32, "alembic head revision ids must be <= 32 chars"
        assert module.down_revision == "chatbot_rearch_s9", (
            "must chain onto the current single head, chatbot_rearch_s9"
        )
        assert hasattr(module, "upgrade") and hasattr(module, "downgrade")

    def test_no_other_migration_already_claims_chatbot_rearch_s9_as_its_parent(self) -> None:
        """A second head off `chatbot_rearch_s9` breaks CI's `check-migration-heads`
        gate - this is the ONE child s9 may have."""
        import importlib.util

        claimants = []
        for path in sorted(ALEMBIC_VERSIONS.glob("*.py")):
            if path.stem == "chatbot_rearch_s10":
                continue
            text = path.read_text(encoding="utf-8")
            if 'down_revision = "chatbot_rearch_s9"' in text or "down_revision = 'chatbot_rearch_s9'" in text:
                claimants.append(path.name)
        assert claimants == [], f"chatbot_rearch_s9 already has a child: {claimants}"


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


class TestModelColumn:
    def test_chatbot_entity_kind_has_roster_cap_defaulting_to_50(self) -> None:
        # PICKER-NO-CAP (owner, 2 Oct 2026): the default moved from 10 to the S3 ceiling.
        from app.models.chatbot_policy import ChatbotEntityKind

        assert hasattr(ChatbotEntityKind, "roster_cap"), (
            "ChatbotEntityKind model has no roster_cap column yet"
        )
        with blank_session() as db:
            row = ChatbotEntityKind(
                id=str(uuid.uuid4()),
                kind=unique_code("kind"),
                label="ZZT kind",
                resolver_source="zzt_source",
                default_narrowing="optional_filter",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            assert row.roster_cap == 50, f"server_default must be 50, got {row.roster_cap!r}"


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

_GRANTS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "ZZT Roster Cap Tester"}


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.add(VIEW)
    _GRANTS.add(MANAGE)
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in _GRANTS
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


@pytest.fixture()
def pg_db():
    with pg_session() as db:
        yield db


@pytest.fixture()
def client(pg_db):
    def _override_db():
        try:
            yield pg_db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    _ACTOR["id"] = str(uuid.uuid4())
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _kind_body(kind: str, *, roster_cap: int = 10) -> dict:
    return {
        "kind": kind,
        "label": f"ZZT {kind}",
        "resolver_source": "zzt_source",
        "did_you_mean": True,
        "default_narrowing": "optional_filter",
        "family_grouping": None,
        "base_property_words": {},
        "roster_cap": roster_cap,
    }


class TestApiRoundTrip:
    def test_post_then_get_carries_roster_cap_in_the_response(self, client) -> None:
        kind = unique_code("kind")
        resp = client.post(ENTITY_KINDS_BASE, json=_kind_body(kind, roster_cap=7))
        assert resp.status_code in (200, 201), resp.text
        body = resp.json()
        assert body.get("roster_cap") == 7, (
            "response_model drops undeclared fields - roster_cap must be a declared "
            f"field on ChatbotEntityKindResponse: {body}"
        )
        get_resp = client.get(f"{ENTITY_KINDS_BASE}/{kind}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json().get("roster_cap") == 7

    def test_put_updates_roster_cap(self, client) -> None:
        kind = unique_code("kind")
        client.post(ENTITY_KINDS_BASE, json=_kind_body(kind, roster_cap=10))
        put_resp = client.put(f"{ENTITY_KINDS_BASE}/{kind}", json=_kind_body(kind, roster_cap=4))
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json().get("roster_cap") == 4

    def test_value_1_is_rejected_422(self, client) -> None:
        kind = unique_code("kind")
        resp = client.post(ENTITY_KINDS_BASE, json=_kind_body(kind, roster_cap=1))
        assert resp.status_code == 422, resp.text

    def test_value_2_is_accepted(self, client) -> None:
        kind = unique_code("kind")
        resp = client.post(ENTITY_KINDS_BASE, json=_kind_body(kind, roster_cap=2))
        assert resp.status_code in (200, 201), resp.text
        assert resp.json().get("roster_cap") == 2


# --------------------------------------------------------------------------- #
# gate.py - the two rosters it cuts, cap threaded straight onto run_gate as
# roster_caps: Mapping[str, int] | None (pinned name; no shim - see module docstring)
# --------------------------------------------------------------------------- #


def _many_customer_matches(n: int) -> list[dict[str, Any]]:
    return [
        {
            "entity_type": "customer",
            "uuid": f"cust-{i:03d}",
            "canonical_code": f"CUST{i:03d}",
            "company_code": "SRT",
            "display": {"customer_name": f"ZZT CUSTOMER {i:03d} SDN BHD"},
        }
        for i in range(n)
    ]


def _many_product_matches(n: int) -> list[dict[str, Any]]:
    return [
        {
            "entity_type": "product",
            "uuid": f"prod-{i:03d}",
            "canonical_code": f"ZZTPROD{i:03d}",
            "match_tier": "prefix",
        }
        for i in range(n)
    ]


class TestCustomerRosterCap:
    @pytest.mark.parametrize("cap", [3, 10])
    def test_customer_roster_never_exceeds_the_cap(self, cap: int) -> None:
        """cap=10 is a MEASURED GREEN CONTROL today (before roster_caps even exists
        as a parameter, this call raises TypeError - see the file docstring): once
        it exists, cap=10 is vacuously satisfied by today's literal `[:8]` (8 <= 10)
        without honouring config at all - `TestNoLiteralCapRemainsInGate` is what
        actually pins the literal's removal. cap=3 is genuinely red (8 > 3)."""
        parser = {
            "domain_hint": "order",
            "entities": [{"raw": "zzt", "hint": "customer", "current_message": True}],
        }
        resolver = {
            "resolutions": [
                {"token": "zzt", "matches": _many_customer_matches(12)},
            ],
            "unresolved_tokens": [],
        }
        gate = gate_mod.run_gate(
            {}, parser=parser, resolver=resolver, roster_caps={"customer": cap}
        )
        assert gate.get("require_specific") is True, gate
        entities = gate.get("compatible_entities") or []
        assert len(entities) <= cap, (
            f"customer roster held {len(entities)} options, cap was {cap}: {entities}"
        )

    def test_a_missing_customer_key_means_50_and_none_means_10(self) -> None:
        """A supplied mapping missing the kind gets the column's own default (50 since
        PICKER-NO-CAP); `roster_caps=None` (a raw port-replay caller) keeps 10."""
        parser = {
            "domain_hint": "order",
            "entities": [{"raw": "zzt", "hint": "customer", "current_message": True}],
        }
        resolver = {
            "resolutions": [{"token": "zzt", "matches": _many_customer_matches(12)}],
            "unresolved_tokens": [],
        }
        gate_missing_key = gate_mod.run_gate(
            {}, parser=parser, resolver=resolver, roster_caps={"product": 3}
        )
        gate_none = gate_mod.run_gate(
            {}, parser=parser, resolver=resolver, roster_caps=None
        )
        assert len(gate_missing_key.get("compatible_entities") or []) == 12
        entities = gate_none.get("compatible_entities") or []
        assert len(entities) <= 10, f"roster_caps=None: default must be 10, got {len(entities)}"


class TestProductRosterCap:
    @pytest.mark.parametrize("cap", [3, 10])
    def test_incoming_product_roster_never_exceeds_the_cap(self, cap: int) -> None:
        parser = {
            "domain_hint": "incoming",
            "entities": [{"raw": "zzt", "hint": "product", "current_message": True}],
        }
        resolver = {
            "resolutions": [
                {"token": "zzt", "matches": _many_product_matches(12)},
            ],
            "unresolved_tokens": [],
        }
        gate = gate_mod.run_gate(
            {}, parser=parser, resolver=resolver, roster_caps={"product": cap}
        )
        assert gate.get("require_specific") is True, gate
        clarification = gate.get("gate_clarification") or ""
        numbered_lines = re.findall(r"^\d+\.", clarification, re.MULTILINE)
        assert len(numbered_lines) <= cap, (
            f"product/attachment roster printed {len(numbered_lines)} lines, cap was "
            f"{cap}: {clarification!r}"
        )


class TestNoLiteralCapRemainsInGate:
    def test_no_bare_8_slice_cuts_a_roster(self) -> None:
        source = GATE_PY.read_text(encoding="utf-8")
        hits = [
            f"{i}: {line.strip()}"
            for i, line in enumerate(source.splitlines(), start=1)
            if "[:8]" in line
        ]
        assert hits == [], f"a literal [:8] still cuts a roster in gate.py: {hits}"


# --------------------------------------------------------------------------- #
# resolve_gate.run accepts the same roster_caps keyword
# --------------------------------------------------------------------------- #


class TestResolveGateRunAcceptsRosterCaps:
    def test_signature_has_roster_caps(self) -> None:
        sig = inspect.signature(resolve_gate_mod.run)
        assert "roster_caps" in sig.parameters, (
            "resolve_gate.run has no roster_caps: Mapping[str, int] | None parameter yet"
        )


# --------------------------------------------------------------------------- #
# turn_runtime.resolve_kinds reads roster_caps off the seeded chatbot_entity_kinds
# rows and passes them down to resolve_gate.run (customer=3, product=7)
# --------------------------------------------------------------------------- #


class TestResolveKindsPassesRosterCapsFromSeededRows:
    def test_resolve_kinds_builds_roster_caps_from_the_kind_rows(self, monkeypatch) -> None:
        """Depends on the model already carrying `roster_cap` (`TestModelColumn`) -
        seeding fails with the SAME missing-column reason until that lands; once it
        does, this test exercises its own real target: does `resolve_kinds` read the
        seeded caps and pass them to `resolve_gate.run`. Not a duplicate of
        `TestModelColumn` - that test pins the column, this one pins the wiring."""
        from app.models.chatbot_policy import ChatbotEntityKind
        from app.services.chatbot import turn_runtime as turn_runtime_mod
        from app.services.chatbot.lanes.business import services as business_services_mod

        with blank_session() as db:
            db.add(
                ChatbotEntityKind(
                    id=str(uuid.uuid4()),
                    kind="customer",
                    label="ZZT customer",
                    resolver_source="zzt_customers",
                    default_narrowing="must_narrow_one",
                    roster_cap=3,
                )
            )
            db.add(
                ChatbotEntityKind(
                    id=str(uuid.uuid4()),
                    kind="product",
                    label="ZZT product",
                    resolver_source="zzt_products",
                    default_narrowing="narrow_to_code",
                    roster_cap=7,
                )
            )
            db.commit()

            calls: list[dict[str, Any]] = []
            real_run = resolve_gate_mod.run

            def _spy_run(ctx, entry, item, **kwargs):
                calls.append(kwargs)
                return real_run(ctx, entry, item, **kwargs)

            monkeypatch.setattr(resolve_gate_mod, "run", _spy_run)
            monkeypatch.setattr(
                business_services_mod,
                "production_services",
                lambda db, *, space_id=None: business_services_mod.ResolveGateServices(
                    access_types=lambda **_: [],
                    resolve_entity=lambda body: {
                        "tokens": list(body.get("tokens") or []),
                        "resolutions": [],
                        "unresolved_tokens": list(body.get("tokens") or []),
                    },
                    probe=lambda **_: None,
                ),
            )

            ctx = {
                "parse": {
                    "output": {
                        "entities": [
                            {"raw": "zzt", "hint": "customer", "current_message": True}
                        ]
                    }
                },
                "contact": {"id": "zzt-contact"},
            }
            turn_runtime_mod.resolve_kinds(
                db, ctx=ctx, branch_kind="business_query", space_id=None, dry_run=True
            )

        assert calls, "resolve_gate.run was never called"
        roster_caps = calls[0].get("roster_caps")
        assert roster_caps is not None, "resolve_kinds did not pass roster_caps at all"
        assert roster_caps.get("customer") == 3, roster_caps
        assert roster_caps.get("product") == 7, roster_caps
