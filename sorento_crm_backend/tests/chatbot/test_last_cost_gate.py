"""AC-16, AC-17, AC-18, AC-25, AC-27, AC-28, AC-29 (chatbot-last-purchase-cost): the
whole-domain field-reveal gate, the parser prompt addendum, the gate matrix row, the
`top_n` passthrough, and the migration 513 publish.

`documentation/plans/chatbot/PLAN-chatbot-last-purchase-cost.md`;
`documentation/plans/chatbot/chatbot-last-purchase-cost-acceptance-criteria.md`.

AC-16/AC-17 drive the REAL `engine.run_turn`, mirroring `tests/chatbot/
test_s6c_engine_paths.py`'s `_srtwc8517_resolved_bundle()` pattern (a canned
`ResolveGateServices` that resolves the named product without touching the real DB
resolver) and `tests/chatbot/test_broaden_domain_switch_e2e.py`'s `fetch_services`
recording stub - so the assertions are on OBSERVABLE turn behaviour (reply text, MCP call
count, the persisted trace) rather than on any particular internal call site the domain
grant check ends up living in.

Postgres only (`tests/_pg_fixture.py` via `tests/chatbot/conftest.py`'s
`session_factory`). No em or en dashes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.models.chatbot_turn import ChatbotTurn
from app.models.user import SystemSetting
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes.business import fetch
from app.services.chatbot.lanes.business import gate
from app.services.chatbot.lanes.business.services import AnswerServices, FetchServices, ResolveGateServices
from tests.chatbot.conftest import set_chatbot_switches, validating_resolve_entity
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access, stub_parser  # noqa: F401

PRODUCT_UUID = "44444444-4444-4444-4444-444444444444"
WAREHOUSE_UUID = "55555555-5555-5555-5555-555555555555"
BRAND_UUID = "66666666-6666-6666-6666-666666666666"
TOOL = "crm_procurement_po_last_cost_list"
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


# --------------------------------------------------------------------------- #
# AC-16 / AC-17: the whole-domain grant, through a real turn.
# --------------------------------------------------------------------------- #


def _cost_parser_output(**overrides: Any) -> dict[str, Any]:
    return _parser_output(
        domain_hint="purchase_cost",
        intent_hint="check_po_cost",
        user_goal="what did we pay for M218",
        entities=[
            {
                "raw": "M218",
                "hint": "product",
                "canonical_code": None,
                "current_message": True,
                "confident": True,
            }
        ],
        **overrides,
    )


def _m218_resolved_bundle() -> ResolveGateServices:
    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "tokens": ["M218"],
            "resolutions": [
                {
                    "raw": "M218",
                    "matches": [
                        {
                            "uuid": PRODUCT_UUID,
                            "entity_type": "product",
                            "canonical_code": "M218",
                        }
                    ],
                }
            ],
            "unresolved_tokens": [],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(_resolve_entity),
        probe=lambda **_: None,
    )


def _wire(session_factory, monkeypatch, *, mcp_call) -> None:
    set_chatbot_switches(session_factory, business_lane=True)
    db = session_factory()
    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting()
        db.add(row)
    row.chatbot_completed_lanes = ["business_query"]
    db.commit()

    bundle = _m218_resolved_bundle()
    monkeypatch.setattr(
        engine_mod.business_services, "production_services", lambda db, *, space_id=None: bundle
    )
    monkeypatch.setattr(
        engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=mcp_call)
    )
    monkeypatch.setattr(
        engine_mod.business_services,
        "answer_services_for",
        lambda session_factory: AnswerServices(
            mcp_probe=lambda name, args: {"answers": [], "has_result": False},
            family_fetch=lambda q: {"data": []},
        ),
    )


def _turn_row(session_factory, turn_id: str) -> ChatbotTurn:
    return session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()


def _granted_cost_row() -> dict[str, Any]:
    return {
        "items": [
            {
                "fields": [
                    {"key": "po_number", "label": "PO Number", "value": "PO-2026/09-0013"},
                    {"key": "product_code", "label": "Product Code", "value": "M218"},
                    {"key": "po_quantity", "label": "PO Quantity", "value": 19},
                    {"key": "po_date", "label": "PO Date", "value": "2026-05-01"},
                    {"key": "unit_cost", "label": "Cost / unit", "value": "CNY 110.00"},
                    {"key": "discount_per_unit", "label": "Discount / unit", "value": "CNY 66.00"},
                    {
                        "key": "unit_cost_after_discount",
                        "label": "Cost after discount / unit",
                        "value": "CNY 44.00",
                    },
                    {"key": "warehouse", "label": "Warehouse", "value": "BRW-SMC"},
                ],
            }
        ],
        "has_result": True,
        "intro": "Here is the last purchase cost per product and location.",
        "restricted_fields": {
            "unit_cost": "purchase_orders.cost",
            "discount_per_unit": "purchase_orders.cost",
            "unit_cost_after_discount": "purchase_orders.cost",
        },
    }


class TestAC16DomainRefusedWithoutGrant:
    def test_ac16_domain_refused_without_grant(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        """A contact without `purchase_orders.cost` asking "last purchase cost for M218"
        gets the access_denied canned reply, its SUBJECT "purchase cost" rather than the
        parser's agent guess - captain's ruling: the exact string is
        "Sorry, you are not allowed to access purchase cost". No MCP tool is called; the
        trace carries {"skipped": "not_granted", "needs": "purchase_orders.cost"}.

        The expected string is built off the FALLBACK template
        (`app.services.chatbot_reply_copy.CHATBOT_REPLY_ACCESS_DENIED`), not typed out a
        second time, and not read off a DB row: `copy_mod.resolve(db)` (`app.services.
        chatbot.copy`) falls back to this exact template when the prompt registry has no
        seeded `chatbot_reply_access_denied` row, which a `blank_session()` schema never
        does."""
        from app.services.chatbot_reply_copy import CHATBOT_REPLY_ACCESS_DENIED

        expected_text = CHATBOT_REPLY_ACCESS_DENIED.replace("{{team}}", "purchase cost")
        assert expected_text == "Sorry, you are not allowed to access purchase cost"

        calls: list[tuple[str, dict]] = []

        def mcp_call(name: str, args: dict) -> str:
            calls.append((name, dict(args)))
            raise AssertionError(f"no tool call is expected without the grant: {name}")

        _wire(session_factory, monkeypatch, mcp_call=mcp_call)
        stub_parser(_cost_parser_output())
        stub_access(attributes=[])

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.status == "done", result.error
        assert result.delegate is None
        assert calls == [], f"the tool must never be called without the grant: {calls!r}"

        text = result.reply.get("text") or ""
        assert text == expected_text, text

        trace = (_turn_row(session_factory, result.turn_id).trace) or []
        skip_events = [
            e
            for e in trace
            if isinstance(e, dict)
            and e.get("skipped") == "not_granted"
            and e.get("needs") == "purchase_orders.cost"
        ]
        assert skip_events, f"no not_granted trace event found: {trace!r}"


class TestAC17GrantedContactGetsCostLines:
    def test_ac17_granted_contact_gets_cost_lines(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        """The same contact WITH the key gets the answer; the reply contains
        "Cost / unit" and "Cost after discount / unit" and no UUID."""
        calls: list[tuple[str, dict]] = []

        def mcp_call(name: str, args: dict) -> str:
            calls.append((name, dict(args)))
            assert name == TOOL, name
            return json.dumps(_granted_cost_row())

        _wire(session_factory, monkeypatch, mcp_call=mcp_call)
        stub_parser(_cost_parser_output())
        stub_access(attributes=["purchase_orders.cost"])

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.status == "done", result.error
        assert [c[0] for c in calls] == [TOOL], calls

        text = result.reply.get("text") or ""
        assert "Cost / unit" in text, text
        assert "Cost after discount / unit" in text, text
        assert not _UUID_RE.search(text), text


# --------------------------------------------------------------------------- #
# AC-18: `output_structurer` drops/keeps the three money fields (belt and braces -
# the SAME general mechanism `test_growth_r1_review_fixes.py` already grades for
# `purchase_orders.supplier`, applied to the new field names).
# --------------------------------------------------------------------------- #


class TestAC18OutputStructurerDropsMoneyFieldsWithoutGrant:
    _RESTRICTED = {
        "unit_cost": "purchase_orders.cost",
        "discount_per_unit": "purchase_orders.cost",
        "unit_cost_after_discount": "purchase_orders.cost",
    }

    def _result(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "fields": [
                        {"key": "po_number", "label": "PO Number", "value": "PO-1"},
                        {"key": "unit_cost", "label": "Cost / unit", "value": "CNY 110.00"},
                        {
                            "key": "discount_per_unit",
                            "label": "Discount / unit",
                            "value": "CNY 66.00",
                        },
                        {
                            "key": "unit_cost_after_discount",
                            "label": "Cost after discount / unit",
                            "value": "CNY 44.00",
                        },
                    ]
                }
            ],
            "has_result": True,
            "intro": "intro",
            "restricted_fields": self._RESTRICTED,
        }

    def test_ac18_drops_without_grant(self) -> None:
        out = fetch.output_structurer(self._result(), {"access": {"attributes": []}})
        keys = {f["key"] for f in out["answers"][0]["fields"]}
        assert keys == {"po_number"}, keys

    def test_ac18_keeps_with_grant(self) -> None:
        out = fetch.output_structurer(
            self._result(), {"access": {"attributes": ["purchase_orders.cost"]}}
        )
        keys = {f["key"] for f in out["answers"][0]["fields"]}
        assert keys == {
            "po_number",
            "unit_cost",
            "discount_per_unit",
            "unit_cost_after_discount",
        }, keys


class TestAC18bOutputStructurerSupplierIndependentGrant:
    """Owner ruling, 12 Sep 2026 ("we should show supplier also"): `supplier` is gated on
    its OWN key `purchase_orders.supplier`, independent of `purchase_orders.cost` - a
    contact granted only the cost key still never sees the supplier name, and a contact
    granted only the supplier key still never sees the money fields."""

    _RESTRICTED = {
        "unit_cost": "purchase_orders.cost",
        "discount_per_unit": "purchase_orders.cost",
        "unit_cost_after_discount": "purchase_orders.cost",
        "supplier": "purchase_orders.supplier",
    }

    def _result(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "fields": [
                        {"key": "po_number", "label": "PO Number", "value": "PO-1"},
                        {"key": "unit_cost", "label": "Cost / unit", "value": "CNY 110.00"},
                        {
                            "key": "discount_per_unit",
                            "label": "Discount / unit",
                            "value": "CNY 66.00",
                        },
                        {
                            "key": "unit_cost_after_discount",
                            "label": "Cost after discount / unit",
                            "value": "CNY 44.00",
                        },
                        {"key": "supplier", "label": "Supplier", "value": "Acme Supplies"},
                    ]
                }
            ],
            "has_result": True,
            "intro": "intro",
            "restricted_fields": self._RESTRICTED,
        }

    def test_ac18b_cost_grant_alone_drops_supplier(self) -> None:
        out = fetch.output_structurer(
            self._result(), {"access": {"attributes": ["purchase_orders.cost"]}}
        )
        keys = {f["key"] for f in out["answers"][0]["fields"]}
        assert keys == {
            "po_number",
            "unit_cost",
            "discount_per_unit",
            "unit_cost_after_discount",
        }, keys

    def test_ac18b_both_grants_keeps_all_four_money_and_name_fields(self) -> None:
        out = fetch.output_structurer(
            self._result(),
            {
                "access": {
                    "attributes": ["purchase_orders.cost", "purchase_orders.supplier"]
                }
            },
        )
        keys = {f["key"] for f in out["answers"][0]["fields"]}
        assert keys == {
            "po_number",
            "unit_cost",
            "discount_per_unit",
            "unit_cost_after_discount",
            "supplier",
        }, keys


# --------------------------------------------------------------------------- #
# AC-25: the parser prompt addendum, pinned as literal text - no LLM call.
# --------------------------------------------------------------------------- #

_PHRASES = ["last purchase cost", "what did we pay", "上次采购价", "harga belian terakhir"]
# Selling-price phrasings that must stay OUT of purchase_cost (review SF3, 12 Sep 2026) -
# "how much does it cost" and "berapa harga" are the everyday words for what WE CHARGE, and
# must route to master_products / promotion / check_stock instead. Checked against
# WHITESPACE-COLLAPSED text: the prompt bodies hard-wrap long lines with a literal "\n",
# and a phrase that happens to wrap across one must not read as absent.
_NOT_PHRASES = ["how much does it cost", "berapa harga"]


def _collapsed(text: str) -> str:
    return re.sub(r"\s+", " ", text)


@pytest.mark.parametrize(
    # SEMANTIC_PARSER_PROMPT_SLIM retired outright (D8, "one prompt lineage (v3
    # shape), v1 and SLIM retired") - mechanical port 16 Sep 2026, same shape as
    # `test_parser_low_stock_words.py`'s own SLIM retirement this session.
    "body_name",
    ["SEMANTIC_PARSER_PROMPT"],
)
def test_ac25_parser_prompt_teaches_purchase_cost(body_name: str) -> None:
    import app.services.chatbot_parser_prompt as prompt_mod
    from app.services.chatbot.turn.policy import default_policy

    body = getattr(prompt_mod, body_name)
    for phrase in _PHRASES:
        assert phrase in body, f"{body_name} missing phrase {phrase!r}"
    assert 'intent_hint "check_po_cost"' in body, body_name
    assert 'domain_hint "purchase_cost"' in body, body_name

    match = re.search(r"domain_hint = ONE of:[^\n]*", body)
    assert match is not None, f"{body_name}: no 'domain_hint = ONE of:' literal found"
    tokens = [t.strip() for t in match.group(0).split(":", 1)[1].split("|")]
    assert "purchase_cost" in tokens, f"{body_name}: {tokens!r}"

    collapsed = _collapsed(body)
    for phrase in _NOT_PHRASES:
        assert phrase in collapsed, f"{body_name} missing NOT-purchase_cost phrase {phrase!r}"

    # purchase_cost has NO switch words, precedent = how purchase_order shipped: "cost" is
    # the everyday word for the SELLING price too, and a whole-token switch on it would drag
    # every such ask into this domain and refuse it for every ungranted contact. Routing is
    # the parser prompt's job alone. Domain-level, so this only needs asserting once, not
    # once per body - kept inside the parametrized test purely for a single call site.
    # Mechanical port 16 Sep 2026 (AC-1592/AC-1594, S6): `contracts.DOMAIN_SPEC` deleted,
    # successor is `turn/policy.py`'s domain-row table (same accessor tester 8 already
    # used to port `test_low_stock_lane.py`'s equivalent DOMAIN_SPEC read).
    domain = default_policy().domain("purchase_cost")
    assert domain is not None
    assert domain.switch_words == ()


# --------------------------------------------------------------------------- #
# AC-27: the gate matrix row.
# --------------------------------------------------------------------------- #


def test_ac27_gate_allows_product_and_warehouse_no_allows_empty() -> None:
    assert gate.ALLOWED["purchase_cost"] == ["product", "warehouse", "category", "brand"]
    assert "purchase_cost" not in gate.ALLOWS_EMPTY

    out = gate.run_gate(
        {},
        parser={"domain_hint": "purchase_cost", "entities": []},
        resolver={"resolutions": []},
    )
    assert out["gate_passed"] is False


def test_ac27b_warehouse_entity_passes_warehouse_ids() -> None:
    """A purchase_cost ask with a resolved warehouse entity, alongside a product, passes
    `warehouse_ids` to the tool - the same seam AC-28 drives. `TYPE_TO_PARAM["warehouse"]`
    is a GLOBAL mapping (not per-domain), so this may already be green - if so it stands
    as the regression pin, not a red-to-green transition."""
    trigger = {
        "entities": [
            {"entity_type": "product", "uuid": PRODUCT_UUID, "code": "M218"},
            {"entity_type": "warehouse", "uuid": WAREHOUSE_UUID, "code": "BRW"},
        ],
        "tool": TOOL,
        "semantic_input": {"contact_id": "1", "space_id": "s"},
    }
    out = fetch.entity_ids_transformer(trigger)
    assert out.get("warehouse_ids") == [WAREHOUSE_UUID]
    assert out.get("product_ids") == [PRODUCT_UUID]


def test_ac27c_brand_only_ask_is_refused_not_found_with_zero_mcp_calls() -> None:
    """The tool is in `policy_rows.ENTITY_FILTER_REQUIRED_TOOLS`, and a brand-only ask
    (the gate passes - brand is an allowed entity type for `purchase_cost` - but
    `TYPE_TO_PARAM` has no "brand" key, so `entity_ids_transformer` builds no `*_ids` at
    all) is refused as `not_found` with ZERO MCP calls, never answered from the unscoped
    branch (which would otherwise hand back a plain `top_n` cap over EVERY product's
    cost line, a directory dump nobody asked for). Mirrors `test_s6b_fetch_lane.py::
    TestEngineDispatch::test_a_resource_attachment_fetch_with_no_resolved_entity_never_ships_unfiltered`
    for the sibling document-tool rule.

    Mechanical port 16 Sep 2026 (AC-1592/AC-1594, S6): `fetch.ENTITY_FILTER_REQUIRED_
    TOOLS` moved to `turn/policy_rows.py` (`fetch.py`'s own comment names the move and
    the reason - a hand-curated per-tool exception list, not domain/kind policy data).
    """
    from app.services.chatbot.turn import policy_rows

    assert TOOL in policy_rows.ENTITY_FILTER_REQUIRED_TOOLS

    from app.services.chatbot.lanes import business as business_mod

    calls: list[tuple[str, dict]] = []

    def mcp_call(name: str, args: dict) -> str:
        calls.append((name, dict(args)))
        raise AssertionError(f"the tool must never be called on an unfiltered ask: {name}")

    services = FetchServices(mcp_call=mcp_call)
    payload = {
        "_exit_kind": "continue",
        "gate": {
            "compatible_entities": [
                {"uuid": BRAND_UUID, "entity_type": "brand", "code": "Acme"}
            ]
        },
        "ctx": {
            "parse": {"output": {"domain_hint": "purchase_cost"}},
            "contact": {"id": "1"},
            # Granted, so this test isolates the ENTITY_FILTER_REQUIRED_TOOLS rule from
            # AC-16's whole-domain grant gate - an access_denied outcome here would be
            # red for the WRONG reason.
            "access": {"attributes": ["purchase_orders.cost"]},
        },
    }

    fragment = business_mod.run_fetch(payload, services=services, dry_run=False)

    assert calls == [], f"the tool must never be called: {calls!r}"
    assert fragment.get("kind") == "error", fragment
    assert fragment.get("outcome") == "not_found", fragment


# --------------------------------------------------------------------------- #
# AC-28: `top_n` passes straight through, never aliased to `limit`.
# --------------------------------------------------------------------------- #


def test_ac28_top_n_passes_direct() -> None:
    assert TOOL in fetch.TOP_N_DIRECT_TOOLS

    trigger = {
        "entities": [{"entity_type": "product", "uuid": PRODUCT_UUID, "code": "M218"}],
        "tool": TOOL,
        "semantic_input": {"contact_id": "1", "space_id": "s", "top_n": 3},
    }
    out = fetch.entity_ids_transformer(trigger)
    assert out.get("top_n") == 3
    assert "limit" not in out


# --------------------------------------------------------------------------- #
# AC-29: migration 513 publishes both prompt bodies, idempotent, no label moved -
# mirrors `tests/test_chatbot_warehouse_cue_migration.py::
# test_publish_is_idempotent_one_new_version_each_then_none`'s own pin for 487 EXACTLY,
# stale-v1 shape and all.
#
# Why the stale v1: on a genuinely blank schema `seed_prompt_registry` seeds v1 from the
# CURRENT full fallback (`app.services.chatbot_parser_prompt.SEMANTIC_PARSER_PROMPT`),
# which by AC-25 already carries the last-cost vocabulary once this lane ships - so a v1
# seeded from the live constant would make the FULL half of the first `publish()` a
# guaranteed no-op (the template it tries to insert already exists as v1) and the test
# would never see the "adds one new version" behaviour it exists to pin. Seeding v1 with
# a STALE placeholder first, the same way 487's own test does, is what keeps the FULL
# half of `publish()` genuinely exercised regardless of what the live prompt constant
# says.
# --------------------------------------------------------------------------- #

MIGRATION_FILE = "513_chatbot_parser_last_cost.py"


def _load_migration():
    import importlib.util

    path = (
        Path(__file__).resolve().parent.parent.parent
        / "alembic"
        / "versions"
        / MIGRATION_FILE
    )
    spec = importlib.util.spec_from_file_location("migration_under_test_513", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ac29_migration_513_publish_idempotent() -> None:
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from app.services.ai_prompt_registry import PROMPT_KEYS
    from tests._pg_fixture import blank_session

    module = _load_migration()
    with blank_session() as session:
        from app.services.ai_prompt_seed import seed_prompt_registry

        spec = PROMPT_KEYS["chatbot_semantic_parser"]
        session.add(
            AIPromptVersion(
                name="chatbot_semantic_parser",
                version=1,
                type="text",
                template="STALE FULL PROMPT TEXT (pre last-cost fix)",
                variables=list(spec.variables),
            )
        )
        session.commit()
        # v1 already exists (the stale row above), so this only adds the production
        # label pointing at it - it never overwrites a live row.
        seed_prompt_registry(session.get_bind())

        production_label_before = (
            session.query(AIPromptLabel)
            .filter(
                AIPromptLabel.name == "chatbot_semantic_parser",
                AIPromptLabel.label == "production",
            )
            .first()
        )
        assert production_label_before is not None
        v1 = (
            session.query(AIPromptVersion)
            .filter(
                AIPromptVersion.name == "chatbot_semantic_parser",
                AIPromptVersion.version == 1,
            )
            .first()
        )
        assert production_label_before.version_id == v1.id

        first = module.publish(session)
        assert isinstance(first, dict) and set(first) == {"full", "slim"}
        assert isinstance(first["full"], int) and first["full"] >= 2, (
            "first publish() must create a new FULL version above the stale v1"
        )
        assert isinstance(first["slim"], int) and first["slim"] >= 2, (
            "first publish() must create a new SLIM version above the stale v1"
        )
        assert first["full"] != first["slim"], "FULL and SLIM texts differ, so must be distinct versions"

        second = module.publish(session)
        assert second == {"full": None, "slim": None}, (
            "a second publish() call must add no new version for either body"
        )

        rows = (
            session.query(AIPromptVersion)
            .filter(AIPromptVersion.name == "chatbot_semantic_parser")
            .all()
        )
        versions = [row.version for row in rows]
        assert versions.count(first["full"]) == 1
        assert versions.count(first["slim"]) == 1

        production_label_after = (
            session.query(AIPromptLabel)
            .filter(
                AIPromptLabel.name == "chatbot_semantic_parser",
                AIPromptLabel.label == "production",
            )
            .first()
        )
        assert production_label_after is not None
        assert production_label_after.version_id == v1.id, (
            "publish() must never move the production label off the stale v1 - "
            "promoting is a deliberate, separate action in the admin UI"
        )


# --------------------------------------------------------------------------- #
# AC-33: the tool is kept OUT of RAG / cosine tool-search, and its capability-summary
# description never leaks the internal field-reveal key. SF1 (security review,
# PLAN-chatbot-last-purchase-cost.md, 12 Sep 2026): RAG tool-search - the in-app
# assistant's embedding pick, and n8n's cosine pick for anything outside the chatbot's own
# `DOMAIN_SPEC -> select_tool` path - has NO field-reveal drop, so a retrieved call would
# answer with an unfiltered cost figure to whoever the assistant is running as. The chatbot
# reaches the tool ONLY through `DOMAIN_SPEC`, never through this pool.
# --------------------------------------------------------------------------- #


def test_ac33_tool_is_skipped_from_embedding_and_hides_the_grant_key() -> None:
    from app.services import mcp_tool_capability_service as capability_mod

    assert TOOL in capability_mod._EMBEDDING_SKIP_TOOLS

    intent = capability_mod.TOOL_INTENTS.get(TOOL)
    assert intent is not None, f"{TOOL} has no ToolIntent entry"
    assert "purchase_orders.cost" not in intent.description, (
        "the internal field-reveal key must never reach the customer-visible "
        "capability summary"
    )

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
pytestmark = pytest.mark.parser_prompt_rows
