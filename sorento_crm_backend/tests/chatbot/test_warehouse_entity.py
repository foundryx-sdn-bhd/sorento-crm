"""AC-1 / AC-2 (chatbot-warehouse-entity-and-last-in): warehouse survives the gate for
`inventory` and `spo_allocation`, and the entity-ids transformer turns a resolved warehouse
into `warehouse_ids`.

`documentation/plans/chatbot/PLAN-chatbot-warehouse-entity-and-last-in.md`;
`chatbot-warehouse-entity-and-last-in-acceptance-criteria.md` AC-1, AC-2.
"""
from __future__ import annotations

from typing import Any

from app.services.chatbot.lanes.business import fetch
from app.services.chatbot.lanes.business.gate import run_gate
from tests.chatbot.conftest import validating_resolve_entity

PRODUCT_UUID = "11111111-1111-1111-1111-111111111111"
WAREHOUSE_UUID = "22222222-2222-2222-2222-222222222222"
CUSTOMER_UUID = "33333333-3333-3333-3333-333333333333"


def _match(uuid: str, entity_type: str, code: str) -> dict[str, Any]:
    return {"uuid": uuid, "entity_type": entity_type, "canonical_code": code}


def _resolver(*matches: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolutions": [{"token": m["canonical_code"], "resolved": True, "matches": [m]} for m in matches],
    }


class TestGateKeepsWarehouse:
    def test_inventory_keeps_product_and_warehouse(self) -> None:
        resolver = _resolver(
            _match(PRODUCT_UUID, "product", "SRT62-GM"),
            _match(WAREHOUSE_UUID, "warehouse", "BRW"),
        )
        out = run_gate(
            dict(resolver),
            parser={"domain_hint": "inventory", "entities": []},
            resolver=resolver,
        )
        types = {e["entity_type"] for e in out["compatible_entities"]}
        assert types == {"product", "warehouse"}

    def test_spo_allocation_keeps_product_and_warehouse_and_drops_customer(self) -> None:
        resolver = _resolver(
            _match(PRODUCT_UUID, "product", "SRT62-GM"),
            _match(WAREHOUSE_UUID, "warehouse", "BRW"),
            _match(CUSTOMER_UUID, "customer", "ABC SDN BHD"),
        )
        out = run_gate(
            dict(resolver),
            parser={"domain_hint": "spo_allocation", "entities": []},
            resolver=resolver,
        )
        types = {e["entity_type"] for e in out["compatible_entities"]}
        assert types == {"product", "warehouse"}
        assert "customer" not in types


class TestZeroEntitySpoAllocationAsksInsteadOfFanningOut:
    """Follow-up ruling (8 Sep 2026, chatbot-warehouse-entity-and-last-in): with
    one-row-per-product semantics, an unscoped "last in" (no product, no warehouse - no
    entity at all) would otherwise fan out to a row for every product in the table. The
    gate must refuse it, and the miss lane must render a clarification instead of the
    turn ever reaching fetch."""

    def _empty_gate(self) -> dict[str, Any]:
        resolver: dict[str, Any] = {"resolutions": []}
        return run_gate(
            dict(resolver),
            parser={"domain_hint": "spo_allocation", "entities": []},
            resolver=resolver,
        )

    def test_gate_fails_with_no_entities(self) -> None:
        out = self._empty_gate()
        assert out["gate_passed"] is False
        assert "requires a scoping entity" in out["gate_reason"]

    def test_the_miss_branch_fires_not_the_continue_branch(self) -> None:
        """`if3_miss` is what `resolve_gate.run` checks BEFORE it would ever reach
        fetch - True here means the turn takes the clarification exit, not the
        continue-to-fetch one."""
        from app.services.chatbot.lanes.business.resolve_gate import if3_miss

        gate = self._empty_gate()
        ctx_resolved_ctx = {"gate": gate}
        assert if3_miss(ctx_resolved_ctx, parser={"domain_hint": "spo_allocation", "entities": []}) is True

    def test_a_clarification_is_rendered(self) -> None:
        from app.services.chatbot.lanes.business.answer import not_found_error_message

        parser = {
            "domain_hint": "spo_allocation",
            "entities": [],
            "routing": {"suggested_team": "procurement"},
            "access_levels": [],
        }
        resolved: dict[str, Any] = {"by_entity_type": {}, "tokens": [], "unresolved_tokens": []}
        gate = self._empty_gate()

        out = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        assert out.get("is_clarification") is True
        message = (out.get("escalate_message") or "").strip()
        assert message != ""
        # The scope word is the CUSTOMER's word for the thing, never the domain key
        # (`_HUMAN_SCOPE`'s own rule: printing an internal type asks the customer to speak
        # our schema).
        assert "SPO line" in message, message
        assert "spo_allocation" not in message, message
        # `crm_procurement_spo_allocations_last_receipt_list` takes no date parameter at
        # all (`fetch.DATE_PARAMS`), so offering a date range asks for a filter nothing
        # downstream could apply.
        assert "date range" not in message, message
        # Two options are "A or B", never "A, or B" - an Oxford comma on a two-item list
        # is the tell that a list helper was written for three (review S5, 8 Sep 2026).
        assert "a product code or warehouse" in message, message


class TestTransformerEmitsWarehouseIds:
    def test_warehouse_entity_becomes_warehouse_ids(self) -> None:
        trigger = {
            "entities": [
                {"entity_type": "product", "uuid": PRODUCT_UUID, "code": "SRT62-GM"},
                {"entity_type": "warehouse", "uuid": WAREHOUSE_UUID, "code": "BRW"},
            ],
            "tool": "crm_procurement_spo_allocations_last_receipt_list",
            "semantic_input": {"contact_id": "1", "space_id": "s"},
        }
        out = fetch.entity_ids_transformer(trigger)
        assert out["warehouse_ids"] == [WAREHOUSE_UUID]
        assert out["product_ids"] == [PRODUCT_UUID]


# --------------------------------------------------------------------------- #
# AC-11: a product and a warehouse in ONE AND-mode resolve call keep BOTH.
# --------------------------------------------------------------------------- #
# Measured defect, browser AC-10, 8 Sep 2026: "last in for SRT62-GM to brw" resolved the
# warehouse and LOST the product, so the fetch args carried `warehouse_ids` and no
# `product_ids`. Cause, measured against the local prod copy `sorento_ai_automation_0907`:
# `_and_probe_warehouse` became exact-code PER TOKEN in this lane, so it answered "brw"
# with BRW even though the sibling token "SRT62-GM" is not a warehouse at all. That
# non-empty intersection is what suppressed the resolve route's own AND -> OR degrade
# (`_resolve_input`: "AND-mode produced zero intersection; switched to OR-mode under the
# whitelist"), and the OR pass is what resolves a product and a warehouse SEPARATELY.
#
# 161 of the 166 multi-hint AND turns in the corpus already ride that degrade; these five
# were the only ones that did not.


def _seed_world(session_factory):
    """One company with two products, two warehouses and a customer.

    Named ZZT so the row is obvious in a shared database, and every id is returned as a
    string because the gate and the transformer both compare uuids as text.
    """
    from app.models.company import Company
    from app.models.inventory import Warehouse
    from app.models.order import Customer
    from app.models.product import Product, ProductCategory, UnitOfMeasure
    from tests._pg_fixture import unique_code

    db = session_factory()
    company = Company(name=unique_code("ZZTWH"), code=unique_code("ZZTWH")[:50])
    db.add(company)
    db.flush()
    category = ProductCategory(
        category_code=unique_code("CAT")[:50],
        category_name="ZZT warehouse category",
        company_id=company.id,
    )
    uom = UnitOfMeasure(uom_code=unique_code("UOM")[:20], uom_name="Each", company_id=company.id)
    db.add_all([category, uom])
    db.flush()

    out: dict[str, str] = {"company": str(company.id)}
    for code in ("SRT62-GM", "SRTWC286-SH"):
        product = Product(
            product_code=code,
            product_name=f"ZZT {code}",
            category_id=category.id,
            base_uom_id=uom.id,
            list_price=10,
            is_active=True,
            company_id=company.id,
        )
        db.add(product)
        db.flush()
        out[code] = str(product.id)
    for code in ("BRW", "BRW-IB"):
        warehouse = Warehouse(
            warehouse_code=code, warehouse_name=f"{code} warehouse", company_id=company.id
        )
        db.add(warehouse)
        db.flush()
        out[code] = str(warehouse.id)
    customer = Customer(
        customer_code="300-Z001",
        customer_name="ZZT WAREHOUSE TRADING SDN BHD",
        is_active=True,
        company_id=company.id,
    )
    db.add(customer)
    db.flush()
    out["customer"] = str(customer.id)
    db.commit()
    return out


def _ctx(text: str, entities: list[dict[str, Any]], domain: str) -> dict[str, Any]:
    """`build-ctx`'s inner ctx for one AND-mode business turn."""
    return {
        "text": {"message": {"message": {"text": text}}},
        "contact": {"id": "900000008"},
        "parse": {
            "output": {
                "message_type": "business_query",
                "intent_hint": "check_stock",
                "domain_hint": domain,
                "match_mode": "and",
                "access_levels": [],
                "entities": entities,
            }
        },
    }


def _services(db: Any, calls: list[dict[str, Any]]):
    """The lane's OWN resolver binding, counted.

    `spec_fallback` / `understand_phrase` are forced off - they are the only two flags in
    the body that reach an LLM provider, and no test here may. Every other key is the
    lane's own (`resolve_entity_body`), so the call this counts is the call production
    makes.
    """
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
    from app.config import settings
    from app.services.chatbot.lanes.business.services import ResolveGateServices

    def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        calls.append(body)
        payload = {**body, "spec_fallback": False, "understand_phrase": False}
        principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
        return resolve_reference_post(
            ResolveReferenceRequest(**payload), current_user=principal, db=db
        )

    return ResolveGateServices(
        access_types=lambda **_: [],
        resolve_entity=validating_resolve_entity(resolve_entity),
        probe=lambda **_: None,
    )


def _run_lane(session_factory, world, ctx):
    """`resolve_gate.run` over the real resolver, then the real gate. Returns
    `(compatible_entities, resolver_call_bodies)`."""
    from app.models.base import set_company_scope
    from app.services.chatbot.lanes.business import resolve_gate

    db = session_factory()
    set_company_scope(db, frozenset({world["company"]}))
    calls: list[dict[str, Any]] = []
    out = resolve_gate.run(
        ctx, "resolve", {}, services=_services(db, calls), space_id="364817"
    )
    return (out["gate"] or {}).get("compatible_entities") or [], calls


def _args_for(entities: list[dict[str, Any]], tool: str) -> dict[str, Any]:
    return fetch.entity_ids_transformer(
        {
            "entities": entities,
            "tool": tool,
            "semantic_input": {"contact_id": "900000008", "space_id": "364817"},
        }
    )


class TestProductAndWarehouseResolveTogether:
    """AC-11. Two hints, one AND-mode call, both entities survive to the fetch args."""

    def test_spo_allocation_keeps_the_product_and_the_warehouse(self, session_factory) -> None:
        world = _seed_world(session_factory)
        entities, calls = _run_lane(
            session_factory,
            world,
            _ctx(
                "last in for SRT62-GM to brw",
                [{"raw": "SRT62-GM", "hint": "product"}, {"raw": "brw", "hint": "warehouse"}],
                "spo_allocation",
            ),
        )
        by_type = {e["entity_type"]: str(e["uuid"]) for e in entities}
        assert by_type.get("product") == world["SRT62-GM"], entities
        assert by_type.get("warehouse") == world["BRW"], entities

        args = _args_for(entities, "crm_procurement_spo_allocations_last_receipt_list")
        assert args["product_ids"] == [world["SRT62-GM"]]
        assert args["warehouse_ids"] == [world["BRW"]]
        assert len(calls) == 1, f"the lane must still make ONE resolve call: {calls!r}"

    def test_inventory_keeps_the_product_and_the_warehouse(self, session_factory) -> None:
        world = _seed_world(session_factory)
        entities, calls = _run_lane(
            session_factory,
            world,
            _ctx(
                "stock for SRT62-GM in brw ib",
                [
                    {"raw": "SRT62-GM", "hint": "product"},
                    {"raw": "brw ib", "hint": "warehouse"},
                ],
                "inventory",
            ),
        )
        by_type = {e["entity_type"]: str(e["uuid"]) for e in entities}
        assert by_type.get("product") == world["SRT62-GM"], entities
        assert by_type.get("warehouse") == world["BRW-IB"], entities

        args = _args_for(entities, "crm_inventory_stock_balance_list")
        assert args["product_ids"] == [world["SRT62-GM"]]
        assert args["warehouse_ids"] == [world["BRW-IB"]]
        assert len(calls) == 1, f"the lane must still make ONE resolve call: {calls!r}"

    def test_order_keeps_the_product_and_the_customer(self, session_factory) -> None:
        """The same defect shape on a different pair - proof the fix is not warehouse-only."""
        world = _seed_world(session_factory)
        entities, calls = _run_lane(
            session_factory,
            world,
            _ctx(
                "delivery for SRT62-GM to zzt warehouse trading",
                [
                    {"raw": "SRT62-GM", "hint": "product"},
                    {"raw": "300-Z001", "hint": "customer"},
                ],
                "order",
            ),
        )
        by_type = {e["entity_type"]: str(e["uuid"]) for e in entities}
        assert by_type.get("product") == world["SRT62-GM"], entities
        assert by_type.get("customer") == world["customer"], entities

        args = _args_for(entities, "crm_order_management_orders_list")
        assert args["product_ids"] == [world["SRT62-GM"]]
        assert args["customer_ids"] == [world["customer"]]
        assert len(calls) == 1, f"the lane must still make ONE resolve call: {calls!r}"

    def test_one_hint_two_tokens_still_makes_exactly_one_call(self, session_factory) -> None:
        """The unchanged path, pinned: every entity shares a hint, so nothing about the
        request or the response shape may move."""
        world = _seed_world(session_factory)
        entities, calls = _run_lane(
            session_factory,
            world,
            _ctx(
                "last in for SRT62-GM and SRTWC286-SH",
                [
                    {"raw": "SRT62-GM", "hint": "product"},
                    {"raw": "SRTWC286-SH", "hint": "product"},
                ],
                "spo_allocation",
            ),
        )
        assert len(calls) == 1, f"the lane must still make ONE resolve call: {calls!r}"
        # CHATBOT-QUEUE-FIX (crew browser pass, prod turn f0a2): two product codes are
        # two products, so they resolve OR. An AND intersection folded "SRTSWT3001"
        # onto SRTSWT3001-GM and dropped it (tests/test_resolve_two_product_codes_and_mode.py);
        # for this pair the intersection was empty and the route degraded to OR anyway.
        assert calls[0]["match_mode"] == "or"
        assert calls[0]["allowed_entity_types"] == ["product", "product"]
        assert calls[0]["tokens"] == ["SRT62GM", "SRTWC286SH"]
        assert entities is not None


# --------------------------------------------------------------------------- #
# `TestAWarehouseIsNotADocumentFilter` (review S1, 8 Sep 2026, live exec 11818957) -
# REMOVED here (AC-1592) and ported to `test_rearch_port_warehouse_not_a_document_
# filter.py` as a RED finding, not a straight port. The old `output_exchange`'s
# post-process step dropped a `warehouse` entity before it could wrongly filter a
# document-list tool (`crm_resource_attachments_list` has no warehouse parameter);
# grepped the whole `app/services/chatbot/` tree for `broaden_dropped` this session -
# zero hits anywhere, including `lanes/business/fetch.py` and `gate.py`. Probed
# `gate.run_gate` directly with a `resource_attachment` domain + a warehouse entity:
# `gate_reason` reads "domain 'resource_attachment' not in matrix; passing through
# unscoped" and `compatible_entities` still carries the warehouse - the gate does not
# reproduce the drop either. The live-incident fix this class proved appears to have
# no equivalent anywhere in the new architecture; not the tester's fix to make.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# AC-11 (PLAN-po-spo-warehouse-29sep.md W1): the gate keeps a warehouse on a PO ask.
# --------------------------------------------------------------------------- #


class TestGateKeepsWarehouseOnPurchaseOrder:
    """Supersedes the "`purchase_order` stays as is" line of the earlier plan: a PO ask
    used to pass every entity through unscoped (no `ALLOWED` row for the domain)."""

    def _gate(self, *matches: dict[str, Any]) -> dict[str, Any]:
        resolver = _resolver(*matches)
        return run_gate(
            dict(resolver),
            parser={"domain_hint": "purchase_order", "entities": []},
            resolver=resolver,
        )

    def test_keeps_product_and_warehouse_and_drops_a_customer(self) -> None:
        out = self._gate(
            _match(PRODUCT_UUID, "product", "SRT79-SS"),
            _match(WAREHOUSE_UUID, "warehouse", "BRW"),
            _match(CUSTOMER_UUID, "customer", "ABC SDN BHD"),
        )
        types = {e["entity_type"] for e in out["compatible_entities"]}
        assert types == {"product", "warehouse"}, out.get("gate_reason")

    def test_a_zero_entity_ask_passes_as_a_broad_query(self) -> None:
        out = self._gate()
        assert out["gate_passed"] is True, out.get("gate_reason")
        assert "permits broad query" in out["gate_reason"], out["gate_reason"]

    def test_the_matrix_rows_exist(self) -> None:
        from app.services.chatbot.lanes.business.gate import ALLOWED, ALLOWS_EMPTY

        assert set(ALLOWED.get("purchase_order") or []) == {
            "product", "warehouse", "category", "brand",
        }
        assert ALLOWS_EMPTY.get("purchase_order") is True

    def test_the_miss_line_has_a_scope_word(self) -> None:
        from app.services.chatbot.lanes.business.answer import _SCOPE_WORD

        assert _SCOPE_WORD.get("purchase_order") == "purchase order"


class TestACustomerOnlyPurchaseOrderAskIsAsked:
    """Reviewer item 6 on PR #1373, pinned as a deliberate change: before the
    `purchase_order` gate row a customer token carried from an order ask rode into the PO
    tool (which has no customer filter) and the whole open book came back as if it were
    hanlim's. Now the gate refuses it and the miss line asks for a product code or
    warehouse, with the customer's word for the domain."""

    def test_the_gate_fails_and_names_the_scope_word(self) -> None:
        from app.services.chatbot.lanes.business.answer import _SCOPE_WORD, not_found_error_message

        resolver = _resolver(_match(CUSTOMER_UUID, "customer", "HANLIM"))
        gate = run_gate(
            dict(resolver),
            parser={"domain_hint": "purchase_order", "entities": []},
            resolver=resolver,
        )
        assert gate["gate_passed"] is False
        assert "incompatible with 'purchase_order'" in gate["gate_reason"]
        assert _SCOPE_WORD["purchase_order"] == "purchase order"
        parser = {
            "domain_hint": "purchase_order",
            "entities": [],
            "routing": {"suggested_team": "purchasing"},
            "access_levels": [],
        }
        resolved = {"by_entity_type": {}, "tokens": [], "unresolved_tokens": []}
        out = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        message = (out.get("escalate_message") or "").strip()
        assert "purchase order" in message, message
        assert "purchase_order" not in message, message
