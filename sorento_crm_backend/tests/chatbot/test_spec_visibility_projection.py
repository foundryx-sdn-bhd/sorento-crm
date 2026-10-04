"""Spec visibility reaches the chatbot: `check_access`'s `hidden_spec_keys`, the
product-spec projection drop, the "not available" miss line and the turn trace.

UAC `documentation/plans/chatbot/spec-visibility-policy-acceptance-criteria.md`
AC-14..AC-17. PLAN `documentation/plans/chatbot/PLAN-spec-visibility-policy.md`
"Chatbot seam".

`TestCheckAccessHiddenSpecKeys` runs on the blank-schema `session_factory` fixture
(`tests/chatbot/conftest.py`), the same seam `tests/chatbot/test_contact_field_reveals.py`
uses. The projection tests call `fetch.output_structurer` directly (no DB), following
`tests/chatbot/test_product_spec_projection.py` and `test_restricted_fields.py`. The
trace test exercises the real `business.run_fetch`, following `test_trace_add.py`.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.models.user import SystemSetting
from app.services.chatbot import engine as engine_mod
from app.services.chatbot import trace as trace_mod
from app.services.chatbot.head.access import check_access
from app.services.chatbot.lanes import business
from app.services.chatbot.lanes.business import fetch
from app.services.chatbot.lanes.business.services import (
    AnswerServices,
    FetchServices,
    ResolveGateServices,
)
from tests._pg_fixture import unique_code
from tests.chatbot.conftest import set_chatbot_switches, validating_resolve_entity
from tests.chatbot.test_engine import (
    CONTACT_ID as _ENGINE_CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,
    stub_parser,
)

CONTACT_ID = "ZZT-spec-visibility-1"
SPACE_ID = "364817"


def _seed_contact_in_segment(session_factory, *, segment_code: str, segment_name: str) -> str:
    """A respond contact tagged to one market segment, in a workspace so
    `check_access` can resolve it by `(respond_io_id, space_id)`."""
    db = session_factory()
    workspace_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO respond_workspaces (id, space_id, api_key_ciphertext) "
            "VALUES (:id, :space_id, 'x')"
        ),
        {"id": workspace_id, "space_id": SPACE_ID},
    )
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, workspace_id, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, :wid, CAST(:sv AS jsonb))"
        ),
        {"cid": CONTACT_ID, "phone": "+60000000098", "wid": workspace_id, "sv": json.dumps({})},
    )
    db.execute(
        text(
            "INSERT INTO market_segments (id, code, name, is_active) "
            "VALUES (gen_random_uuid(), :code, :name, true)"
        ),
        {"code": segment_code, "name": segment_name},
    )
    db.commit()
    contact_id = db.execute(
        text("SELECT id FROM respond_contacts WHERE respond_io_id = :cid"), {"cid": CONTACT_ID}
    ).scalar()
    db.execute(
        text(
            "INSERT INTO respond_contact_market_segments (contact_id, segment_code) "
            "VALUES (:cid, :code)"
        ),
        {"cid": contact_id, "code": segment_code},
    )
    db.commit()
    return contact_id


def _seed_registry_keys(session_factory, keys: list[tuple[str, str]]) -> None:
    db = session_factory()
    for key, label in keys:
        db.execute(
            text(
                "INSERT INTO product_spec_registry (id, spec_key, label, data_type, is_active) "
                "VALUES (gen_random_uuid(), :key, :label, 'enum', true)"
            ),
            {"key": key, "label": label},
        )
    db.commit()


def _seed_default_policy(session_factory, *, excluded_spec_keys: list[str]) -> None:
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO spec_visibility_policies (id, spec_keys, excluded_spec_keys) "
            "VALUES (gen_random_uuid(), NULL, :excluded)"
        ),
        {"excluded": excluded_spec_keys},
    )
    db.commit()


def _seed_spec_key(session_factory, key: str, label: str, *, is_active: bool = True) -> None:
    from app.models.product_spec import ProductSpecRegistry

    db = session_factory()
    db.add(
        ProductSpecRegistry(
            id=str(uuid.uuid4()), spec_key=key, label=label, data_type="enum", is_active=is_active
        )
    )
    db.commit()


def _set_spec_key_active(session_factory, key: str, is_active: bool) -> None:
    from app.models.product_spec import ProductSpecRegistry

    db = session_factory()
    db.query(ProductSpecRegistry).filter(ProductSpecRegistry.spec_key == key).update(
        {"is_active": is_active}
    )
    db.commit()


def _seed_contact(session_factory) -> str:
    from app.models.access import RespondContact

    db = session_factory()
    row = RespondContact(
        id=unique_code("CONTACT"),
        phone_number=f"+60{uuid.uuid4().int % 10**9:09d}",
        name="ZZT Contact",
    )
    db.add(row)
    db.commit()
    return row.id


def _seed_policy_row(
    session_factory,
    *,
    contact_id: str | None = None,
    spec_keys: list[str] | None = None,
    excluded_spec_keys: list[str] | None = None,
) -> None:
    from app.models.access import SpecVisibilityPolicy

    db = session_factory()
    db.add(
        SpecVisibilityPolicy(
            id=str(uuid.uuid4()),
            contact_id=contact_id,
            segment_code=None,
            spec_keys=spec_keys,
            excluded_spec_keys=excluded_spec_keys,
        )
    )
    db.commit()


class TestCheckAccessHiddenSpecKeys:
    def test_check_access_carries_hidden_spec_keys_sorted(self, session_factory):
        """AC-14: a retail-segment contact inherits the default policy (retail
        carries no row of its own); `check_access` resolves it once per turn, the
        same seam `attributes` already uses."""
        _seed_registry_keys(
            session_factory,
            [
                ("thickness", "Thickness"),
                ("board_thickness", "Drainer board / countertop thickness"),
                ("material", "Material"),
            ],
        )
        _seed_default_policy(session_factory, excluded_spec_keys=["thickness", "board_thickness"])
        _seed_contact_in_segment(session_factory, segment_code="retail", segment_name="Retail")

        access = check_access(
            session_factory(),
            agent_code="general_enquiries",
            contact_id=CONTACT_ID,
            space_id=SPACE_ID,
        )

        assert access["hidden_spec_keys"] == ["board_thickness", "thickness"]

    def test_hidden_keys_keeps_a_key_that_was_deactivated_after_the_policy_was_stored(
        self, session_factory
    ):
        """Security finding B1 (moved from `tests/test_spec_visibility_policy.py`,
        AC-002 `tests/chatbot/test_import_boundary.py`: only files under
        `tests/chatbot/` and the module's own doorways may import
        `app.services.chatbot`).

        Deactivating a spec key AFTER a policy already named it must not
        un-hide it - `is_active` is a merchandising decision (should the
        parser still offer/derive this key), not "does this key still exist"
        for a stored policy's read.

        Exercises `check_access`'s real seam (`_hidden_spec_keys`, private but
        it IS the unit under test: the exact call that resolves
        `resolve_policy` and feeds its result into `hidden_keys` with
        whichever registry list it obtains) rather than reimplementing the
        registry read here, so this stays correct whatever helper backs it."""
        from app.services.chatbot.head.access import _hidden_spec_keys

        _seed_spec_key(session_factory, "thickness", "Thickness", is_active=True)
        _seed_spec_key(session_factory, "material", "Material", is_active=True)
        _seed_policy_row(session_factory, spec_keys=None, excluded_spec_keys=["thickness"])
        _set_spec_key_active(session_factory, "thickness", False)

        # Hide-these shape: the default row named `thickness` explicitly; an
        # unresolvable contact falls back to it (fail-closed).
        hidden = _hidden_spec_keys(
            session_factory(), contact_id="ZZT-NO-SUCH-CONTACT", space_id=None
        )
        assert "thickness" in hidden

        # Show-only shape: a contact override naming ONLY `material` must
        # still hide `thickness` - it was never in the show list, active or
        # not.
        contact_id = _seed_contact(session_factory)
        _seed_policy_row(
            session_factory, contact_id=contact_id, spec_keys=["material"], excluded_spec_keys=None
        )

        hidden_show_only = _hidden_spec_keys(
            session_factory(), contact_id=contact_id, space_id=None
        )
        assert "thickness" in hidden_show_only


# --------------------------------------------------------------------- projection


def _product_envelope(specs: list[dict]) -> dict:
    fields = [
        {"key": "company_name", "label": "Company", "value": "Sorento"},
        {"label": "Product Code", "value": "SRTKS8825"},
        {"label": "Product Name", "value": "Kitchen Sink"},
        {"label": "List Price", "value": "MYR 199.00"},
        {"label": "Dimensions", "value": "500 x 360 x 350 mm"},
    ]
    for spec in specs:
        fields.append({"key": f"spec:{spec['key']}", "label": spec["label"], "value": spec["value"]})
    return {
        "result_type": "products",
        "intro": "Here are the matching products.",
        "items": [{"title": "SRTKS8825", "fields": fields}],
        "spec_vocabulary": {s["key"]: s["label"] for s in specs},
        "has_result": True,
    }


_SPECS = [
    {"key": "thickness", "label": "Thickness", "value": "0.8 mm"},
    {"key": "board_thickness", "label": "Drainer board / countertop thickness", "value": "18 mm"},
    {"key": "material", "label": "Material", "value": "Stainless steel"},
]


class TestProjectionDropsHiddenKeys:
    def test_projection_no_attribute_specs_line_omits_hidden_keys(self):
        """AC-15: no attribute asked - the compact Specs line names every
        populated key EXCEPT the hidden ones."""
        envelope = _product_envelope(_SPECS)
        ctx = {"semantic_input": {}, "access": {"hidden_spec_keys": ["thickness"]}}

        out = fetch.output_structurer(envelope, ctx)

        specs_field = next(f for f in out["answers"][0]["fields"] if f["label"] == "Specs")
        assert "Thickness" not in specs_field["value"]
        assert "Material: Stainless steel" in specs_field["value"]
        assert "Drainer board / countertop thickness: 18 mm" in specs_field["value"]

    def test_projection_hidden_key_removed_from_vocabulary_so_no_hit(self):
        """AC-15: a hidden key is removed from the matching vocabulary too, so an
        ask naming it directly cannot resolve to a hit - it falls to the
        `spec_hidden:` miss path instead (AC-16), never renders the field."""
        envelope = _product_envelope(_SPECS)
        ctx = {
            "semantic_input": {"requested_attributes": ["thickness"]},
            "access": {"hidden_spec_keys": ["thickness"]},
        }

        out = fetch.output_structurer(envelope, ctx)

        fields = out["answers"][0]["fields"]
        assert not any(f.get("label") == "Thickness" for f in fields)
        assert not any((f.get("key") or "").startswith("spec:thickness") for f in fields)

    def test_projection_visible_key_unchanged_when_other_key_hidden(self):
        """A word naming a visible key behaves exactly as today, whatever else on
        the product is hidden."""
        envelope = _product_envelope(_SPECS)
        ctx = {
            "semantic_input": {"requested_attributes": ["material"]},
            "access": {"hidden_spec_keys": ["thickness"]},
        }

        out = fetch.output_structurer(envelope, ctx)

        fields = out["answers"][0]["fields"]
        assert any(f["label"] == "Material" and f["value"] == "Stainless steel" for f in fields)
        assert "not available" not in out["response"]
        assert "not recorded" not in out["response"]


class TestHiddenKeyDirectAsk:
    def test_projection_direct_ask_on_hidden_key_renders_not_available_once_without_codes(self):
        """AC-16: one `spec_hidden:<key>` miss, "<label>: not available" - never
        "not recorded", and with no product code listed (the codes-capped
        `spec_miss:` shape stays for a genuinely absent key)."""
        envelope = _product_envelope(_SPECS)
        ctx = {
            "semantic_input": {"requested_attributes": ["thickness"]},
            "access": {"hidden_spec_keys": ["thickness"]},
        }

        out = fetch.output_structurer(envelope, ctx)

        assert "*Thickness:* not available" in out["response"]
        assert "not recorded" not in out["response"]
        assert "not available for" not in out["response"]
        assert out["response"].count("not available") == 1

    def test_projection_asked_word_shared_with_hidden_key_still_renders_the_visible_key(self):
        """Code review: "thickness" token-contains BOTH the hidden `thickness`
        key (exact match) and the visible `board_thickness` key's label
        ("Drainer board / countertop thickness" - "thickness" is one of its
        tokens), the same containment rule an ordinary hit uses. The hidden
        match must not short-circuit the visible one - the reply needs the
        board thickness VALUE and the "Thickness: not available" miss, not one
        instead of the other."""
        envelope = _product_envelope(_SPECS)
        ctx = {
            "semantic_input": {"requested_attributes": ["thickness"]},
            "access": {"hidden_spec_keys": ["thickness"]},
        }

        out = fetch.output_structurer(envelope, ctx)

        fields = out["answers"][0]["fields"]
        assert any(
            f["label"] == "Drainer board / countertop thickness" and f["value"] == "18 mm"
            for f in fields
        )
        assert "*Thickness:* not available" in out["response"]
        assert out["response"].count("not available") == 1


class TestTurnTraceSpecVisibility:
    def test_turn_trace_has_spec_visibility_hidden_and_dropped(self):
        """AC-17: the turn trace gains a `spec_visibility` event beside `reveals`,
        naming the keys hidden for this contact and the keys the envelope
        actually carried and dropped."""
        t = trace_mod.TurnTrace()
        payload = {
            "_exit_kind": "continue",
            "gate": {
                "compatible_entities": [
                    {"uuid": str(uuid.uuid4()), "entity_type": "product", "code": "SRTKS8825"}
                ]
            },
            "ctx": {
                "parse": {"output": {"domain_hint": "master_products"}},
                "access": {"hidden_spec_keys": ["thickness"]},
            },
        }
        envelope = _product_envelope(_SPECS)
        services = FetchServices(mcp_call=lambda name, args: json.dumps(envelope))

        business.run_fetch(payload, services=services, dry_run=False, trace=t)

        events = [e for e in t.events if e["kind"] == "spec_visibility"]
        assert len(events) == 1
        assert events[0]["hidden"] == ["thickness"]
        assert "thickness" in events[0]["dropped"]

    def test_turn_trace_dropped_lists_only_keys_actually_removed(self):
        """Code review: `dropped` must name keys the ENVELOPE actually carried
        and removed, not merely keys the vocabulary knows about - a product
        with no `spec:thickness` field (never populated on this product) has
        nothing to drop even though `thickness` sits in `spec_vocabulary` (the
        registry-wide map) and in the contact's hidden set."""
        t = trace_mod.TurnTrace()
        payload = {
            "_exit_kind": "continue",
            "gate": {
                "compatible_entities": [
                    {"uuid": str(uuid.uuid4()), "entity_type": "product", "code": "SRTKS8825"}
                ]
            },
            "ctx": {
                "parse": {"output": {"domain_hint": "master_products"}},
                "access": {"hidden_spec_keys": ["thickness"]},
            },
        }
        # `material` is populated on this product; `thickness` is known to the
        # vocabulary (the registry carries it) but this particular product has
        # no `spec:thickness` field at all.
        envelope = _product_envelope(
            [{"key": "material", "label": "Material", "value": "Stainless steel"}]
        )
        envelope["spec_vocabulary"]["thickness"] = "Thickness"
        services = FetchServices(mcp_call=lambda name, args: json.dumps(envelope))

        business.run_fetch(payload, services=services, dry_run=False, trace=t)

        events = [e for e in t.events if e["kind"] == "spec_visibility"]
        assert len(events) == 1
        assert events[0]["hidden"] == ["thickness"]
        assert events[0]["dropped"] == []


# --------------------------------------------------------------------- end to end


def _leaked_spec_envelope() -> dict:
    """Two products, base fields plus `spec:class` / `spec:trap_type` /
    `spec:seat_material` - copied from the measured turn's own `looked_up` trace
    event (console turn 8c432988-f0fc-4cad-a7a6-4a92207cd8d5, `chatbot.turns`)."""

    def _item(code: str, trap_note: str) -> dict:
        return {
            "title": code,
            "fields": [
                {"label": "Product Code", "value": code},
                {
                    "label": "Description",
                    "value": f"SORENTO ONE PIECE TWISTER FLUSH WC ({trap_note}). {code}",
                },
                {"label": "List Price", "value": "MYR 1260.00"},
                {"label": "Dimensions", "value": "Not defined"},
                {"key": "spec:class", "label": "Product class", "value": "Water Closet"},
                {"key": "spec:trap_type", "label": "Trap", "value": "s_trap"},
                {"key": "spec:seat_material", "label": "Seat cover material", "value": "pp"},
            ],
            "flags": {
                "discontinued": False,
                "expired": False,
                "expiring_soon": False,
                "unallocated": False,
                "partially_allocated": False,
            },
        }

    return {
        "result_type": "products",
        "intro": "Here are the matching products.",
        "items": [_item("SRTWC286-SH", "S-TRAP 250MM"), _item("SRTWC286-SH-200", "S-TRAP 200MM")],
        "spec_vocabulary": {
            "class": "Product class",
            "trap_type": "Trap",
            "seat_material": "Seat cover material",
        },
        "has_result": True,
    }


class TestEndToEndRenderedAnswerHonoursHiddenSpecs:
    """Measured defect (console turn 8c432988-f0fc-4cad-a7a6-4a92207cd8d5,
    `chatbot.turns`, shared DB): `access.hidden_spec_keys` named every registry
    key and `spec_visibility.dropped` correctly listed 10 keys the projection
    removed, yet `response` (the text that landed in `chatbot.turns.response`)
    printed every `*Label:* value` spec line for all 10 products anyway - the
    projection ran on one object and the answer was rendered from another.

    Drives the REAL business lane end to end - `run_until_exit` / `run_fetch` /
    `complete_answer`, including the real `engine.complete_turn` tail - the way
    `tests/chatbot/test_s6c_engine_paths.py` does: only the MCP call, the parser
    and `check_access` are stubbed, so `select_tool`, `tool_filter`,
    `output_structurer` and the whole answer/render stage run unmocked. The
    assertion is on `result.reply["text"]` - the SAME string `chatbot.turns.
    response` carries - not on the envelope `output_structurer` builds (already
    proven correct in isolation by `TestProjectionDropsHiddenKeys` above)."""

    _HIDDEN_SPEC_KEYS = ["class", "seat_material", "trap_type"]

    @staticmethod
    def _resolve_bundle() -> ResolveGateServices:
        def _resolve_entity(body: dict) -> dict:
            return {
                "tokens": ["SRTWC8517"],
                "resolutions": [
                    {
                        "raw": "SRTWC8517",
                        "matches": [
                            {
                                "uuid": "22222222-2222-2222-2222-222222222222",
                                "entity_type": "product",
                                "canonical_code": "SRTWC8517",
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

    @staticmethod
    def _answer_services() -> AnswerServices:
        def _mcp_probe(name: str, args: dict) -> Any:
            return {"answers": [], "has_result": False}

        def _family_fetch(query: str) -> Any:
            return {"data": []}

        return AnswerServices(mcp_probe=_mcp_probe, family_fetch=_family_fetch)

    def _wire(self, session_factory, monkeypatch) -> None:
        set_chatbot_switches(session_factory, business_lane=True)
        db = session_factory()
        setting = db.query(SystemSetting).first()
        setting.chatbot_completed_lanes = ["business_query"]
        db.commit()

        monkeypatch.setattr(
            engine_mod.business_services,
            "production_services",
            lambda db, *, space_id=None: self._resolve_bundle(),
        )

        def _mcp_call(name: str, args: dict) -> Any:
            assert name == "crm_master_products_list", name
            return json.dumps(_leaked_spec_envelope())

        monkeypatch.setattr(
            engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=_mcp_call)
        )
        monkeypatch.setattr(
            engine_mod.business_services,
            "answer_services_for",
            lambda session_factory: self._answer_services(),
        )
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda db, *, agent_code, contact_id, space_id: {
                "allowed": True,
                "decision": "allow",
                "agent_name": "General Enquiries",
                "attributes": None,
                "all_attributes_allowed": None,
                "hidden_spec_keys": self._HIDDEN_SPEC_KEYS,
            },
        )
        monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")

    # The VALUES only, not the labels: a hidden key's label legitimately still
    # appears in the safe "<label>: not available" miss line (AC-16) - it is the
    # customer's stored VALUE that must never reach the reply.
    _LEAKED_STRINGS = ["Water Closet", "s_trap", "pp"]

    def _assert_no_leak(self, result) -> None:
        assert result.status == "done", result.error
        text_out = result.reply.get("text")
        assert isinstance(text_out, str) and text_out
        for needle in self._LEAKED_STRINGS:
            assert needle not in text_out, (needle, text_out)
        items = (
            result.item.get("result", {}).get("rows", {}).get("answers")
            if isinstance(result.item, dict)
            else None
        )
        for item in items or []:
            for f in item.get("fields") or []:
                key = f.get("key") if isinstance(f, dict) else None
                assert not (isinstance(key, str) and key.startswith("spec:")), f

    def test_hidden_specs_do_not_reach_the_rendered_answer_when_an_attribute_is_asked(
        self, session_factory, seeded, stub_parser, monkeypatch
    ) -> None:
        """`requested_attributes: ["class"]`, not the literal `["spec"]` the brief
        named: `class` is one of the HIDDEN keys and matches `spec_vocabulary`
        exactly, so this actually exercises `_project_product_specs`'s asked
        branch against a hidden key (AC-16's "not available" line). The literal
        word "spec" matches no registry key or label at all (exact-match and
        containment both miss), so it can only ever produce an ordinary "not
        recorded" line with ZERO spec fields carried either way - a vacuous
        pass regardless of whether the leak exists, verified by hand before
        this was written.

        20 Sep 2026: was `xfail(strict=True)` ("the engine silently ignores
        requested_attributes and composes the generic full product-info card") - now
        genuinely PASSES, a side effect of R-d (`turn/compose.py` reusing the fetch
        lane's own per-attribute `lane_text`, which already carried the attribute-scoped
        "not available" line; the engine just never surfaced it before R-d). Confirmed
        real, not a wrong-reason green: re-ran with `--runxfail` before removing the
        marker and both assertions in `_assert_no_leak` plus the "not available" line
        genuinely hold, not just "does not raise"."""
        self._wire(session_factory, monkeypatch)
        stub_parser(
            _parser_output(
                requested_attributes=["class"],
                user_goal="checking specs for a product",
            )
        )

        result = engine_mod.run_turn(
            _envelope(), session_factory=session_factory
        )

        self._assert_no_leak(result)
        # AC-16: the hidden key still gets its safe miss line - the label may
        # appear, the customer's stored VALUE (checked by `_assert_no_leak`)
        # must not.
        assert "*Product class:* not available" in result.reply["text"]

    def test_hidden_specs_do_not_reach_the_rendered_answer_with_no_attribute_asked(
        self, session_factory, seeded, stub_parser, monkeypatch
    ) -> None:
        """The mirror case: no attribute asked (the "Specs:" summary line path) -
        same assertion, same rendered string."""
        self._wire(session_factory, monkeypatch)
        stub_parser(_parser_output(requested_attributes=[]))

        result = engine_mod.run_turn(
            _envelope(), session_factory=session_factory
        )

        self._assert_no_leak(result)


# --------------------------------------------------------- the id on the wire


class TestResolveBodyContactIdIsAString:
    """Production regression from #874 (measured 14 Sep, every `business_query`
    turn): the lane sent `ctx.contact.id` into the resolve body raw, and that id
    is the Respond.io contact id, which arrives from the webhook as a JSON
    INTEGER. The new `ResolveReferenceRequest.contact_id: str | None` rejected
    it, so the whole lane threw before any resolution:

        ValidationError: 1 validation error for ResolveReferenceRequest
        contact_id  Input should be a valid string
        [type=string_type, input_value=900000008, input_type=int]

    Every sibling read of the same id in `resolve_gate.py` already wraps it in
    `jsc.js_string(...)` (the access-types call, the semantic-input builder);
    `_contact_id_from_ctx` is the one that forgot. The schema pin covers the
    external callers too - n8n posts the same integer straight at the route.
    """

    @staticmethod
    def _ctx_with_int_contact_id() -> dict[str, Any]:
        """The measured production shape: `contact.id` an int, copied otherwise
        from `TestEntityPinsBody._ctx` in
        `tests/chatbot/test_s6a_gate_dry_run_and_seams.py`."""
        return {
            "contact": {"id": 900000008},
            "text": {"message": {"message": {"text": "ZZT-1"}}},
            "access": {"hidden_spec_keys": ["thickness"]},
            "parse": {
                "output": {
                    "match_mode": "or",
                    "entities": [
                        {
                            "hint": "product",
                            "raw": "ZZT-1",
                            "canonical_code": "ZZT-1",
                            "current_message": True,
                        }
                    ],
                }
            },
        }

    def test_lane_body_stringifies_an_integer_respond_io_contact_id(self) -> None:
        from app.api.v1.system.references import ResolveReferenceRequest
        from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

        body = resolve_entity_body(self._ctx_with_int_contact_id(), space_id="364817")

        assert body["contact_id"] == "900000008"
        # The body the lane hands `services.resolve_entity` must survive the
        # route's own validation - this is the exact call that threw in prod.
        assert ResolveReferenceRequest(**body).contact_id == "900000008"

    def test_resolve_request_coerces_an_integer_contact_id_and_leaves_the_rest(self) -> None:
        """The schema pin: any caller sending the Respond.io id as a number (n8n
        posts the webhook value straight through) gets it coerced, while a
        string id and an absent id are untouched."""
        from app.api.v1.system.references import ResolveReferenceRequest

        assert ResolveReferenceRequest(query="x", contact_id=900000008).contact_id == "900000008"
        assert ResolveReferenceRequest(query="x", contact_id=None).contact_id is None
        assert ResolveReferenceRequest(query="x", contact_id="abc").contact_id == "abc"
        assert ResolveReferenceRequest(query="x").contact_id is None
