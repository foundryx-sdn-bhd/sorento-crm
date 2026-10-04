"""Three live seams whose PRODUCTION shape the ported n8n nodes cannot read.

Follow-up to #700. Every fix in that PR is bypassed on production because the test that
proved it stubbed the seam with the shape the n8n NODE produced, not the shape the CRM
SERVICE produces. A stub that hand-builds the upstream node's JSON proves the port can
read n8n; it proves nothing about the CRM.

So every test here crosses the real seam:

* **H** - `services._mcp_call` builds `MCPRuntimeClient` and returns whatever
  `call_tool` returns, and `call_tool` returns a **string** (`"\\n".join(chunks)` or
  `json.dumps(result)`, `app/services/ai_assistant_service.py`). The fetch path parses it
  (`fetch.parse_mcp_content`); the four probe seams did not, so `answer.run_crossdomain`'s
  `probe_result if isinstance(probe_result, dict) else {}` dropped every probe answer and
  `crossdomain_render` degraded to `no_envelope`. The probe ALSO sent
  `sub-get-results`' workflowInputs (`entities` / `semantic_input` / `user_prompt`) as the
  MCP tool arguments, which the MCP server ignores: MEASURED against the local MCP on
  6 Sep 2026, `crm_incoming_stock_list` answers those arguments with
  `{"data": [], "total": 0, "page": 1, "limit": null}` - the raw list shape, with no
  `items` / `answers` / `has_result`, so it degrades to `no_envelope` even once it is
  parsed. The same call with `entity_ids_transformer`'s arguments answers with the render
  envelope. So the test asserts BOTH halves at the client: the arguments that arrive and
  the string that comes back.
* **B** - `services._family_fetch` returns `ProductService.list_products(...)`, whose
  `data` is a list of `Product` ORM objects. `jsc.get(row, "product_code")` returns the
  default on anything that is not a dict, so `sibling_transform` found no siblings and the
  D3 family offer was skipped. Exercised against the REAL `ProductService` on the
  Postgres fixture.
* **I** - the resolver reaches one customer by `customer_code` and another by
  `debtor_name`, both as `canonical_code`, on two different `customers` rows. The gate
  keys by uuid, so both survive, and `_axis_labelled_subject` printed the internal debtor
  code. Exercised against the REAL resolver route, the REAL gate and the REAL
  `output_structurer`.

Postgres only (`tests/chatbot/conftest.py`'s `session_factory` over
`tests/_pg_fixture.py`'s blank schema). No LLM, no network, no n8n.
"""
from __future__ import annotations

import json
from typing import Any

from app.models.company import Company
from app.models.order import Customer
from app.models.product import Product, ProductCategory, UnitOfMeasure
from tests._pg_fixture import unique_code

# --------------------------------------------------------------------------- #
# The production shapes, verbatim.
# --------------------------------------------------------------------------- #

# What `crm_incoming_stock_list` really answers a filtered probe with when nothing is
# incoming. Captured from the local MCP server on 6 Sep 2026 with
# `entity_ids_transformer`'s own arguments; the `field_access.denied` list is trimmed to
# one entry (the renderer never reads it) and nothing else is changed. The seam hands
# this to the lane as a STRING, which is the whole point of the test.
INCOMING_NONE_ENVELOPE: dict[str, Any] = {
    "result_type": "incoming_stock",
    "intro": "No matching results found.",
    "items": [],
    "attachments": [],
    "action_links": [],
    "last_updated_at": None,
    "has_result": False,
    "field_access": {
        "denied": [
            {
                "field": "eta_delay_date",
                "agent_code": "incoming_stock_enquiries",
                "outcome": "field_not_allowed",
                "reason": (
                    "This contact holds the agent, but this field is not allowed on it. "
                    "Tick the field on the agent, or add a per-contact override."
                ),
                "label": "ETA Delay",
            }
        ]
    },
}

# What the MCP answers the n8n workflowInputs shape with. MEASURED, same session: the
# server ignores `entities` / `semantic_input` / `user_prompt`, so the tool falls back to
# its raw unfiltered list serialiser. No `items`, no `answers`, no `has_result`.
UNFILTERED_LIST_ANSWER: dict[str, Any] = {"data": [], "total": 0, "page": 1, "limit": None}


def _stock_row(*, company: str, code: str, warehouse: str, qty: Any) -> dict[str, Any]:
    """One `crm_inventory_stock_balance_list` presenter row, keys and labels as shipped
    (`nodes/sub-get-results/output-structurer/gr-15158457.json` and friends)."""
    return {
        "title": code,
        "fields": [
            {"key": "company_name", "label": "Company", "value": company},
            {"key": "product_code", "label": "Product Code", "value": code},
            {"key": "warehouse", "label": "Warehouse", "value": warehouse},
            {"key": "quantity_on_hand", "label": "Quantity On Hand", "value": qty},
        ],
        "flags": {
            "discontinued": False,
            "expired": False,
            "expiring_soon": False,
            "unallocated": False,
            "partially_allocated": False,
        },
    }


# The owner's turn of 6 Sep 2026: "STOCK STATUS / MWT5727SS-CR / MHS1028 / MSK11A-QT".
# Two companies, three codes, two of them with stock rows and MSK11A-QT with none.
STOCK_ENVELOPE: dict[str, Any] = {
    "result_type": "stock",
    "intro": "Stock details found for the requested products.",
    "items": [
        _stock_row(company="Sorento", code="MWT5727SS-CR", warehouse="WH1", qty=378),
        _stock_row(company="Mocha", code="MWT5727SS-CR", warehouse="WH3 (PSM)", qty=2),
        _stock_row(company="Sorento", code="MHS1028", warehouse="WH1", qty=1),
    ],
    "attachments": [],
    "action_links": [],
    "last_updated_at": "2026-09-06T09:30:00",
    "has_result": True,
    "lookup_companies": [{"name": "Sorento"}, {"name": "Mocha"}],
}

PARSER: dict[str, Any] = {
    "message_type": "business_query",
    "intent_hint": "check_stock",
    "domain_hint": "inventory",
    "user_goal": "stock status for three codes",
    "access_levels": ["Dealer"],
    "is_active": True,
}

RESOLVED: dict[str, Any] = {
    "resolutions": [
        {
            "token": "MWT5727SS-CR",
            "matches": [
                {
                    "entity_type": "product",
                    "canonical_code": "MWT5727SS-CR",
                    "uuid": "6a43b9ec-9806-4bcb-9732-eb106bbf2b69",
                    "match_tier": "exact",
                }
            ],
        },
        {
            "token": "MHS1028",
            "matches": [
                {
                    "entity_type": "product",
                    "canonical_code": "MHS1028",
                    "uuid": "98f3418b-6d7a-4e33-bdf2-317ea933c3f8",
                    "match_tier": "exact",
                }
            ],
        },
        {
            "token": "MSK11A-QT",
            "matches": [
                {
                    "entity_type": "product",
                    "canonical_code": "MSK11A-QT",
                    "uuid": "ef3f2fd2-254a-4ab5-a7d0-6d9139a67d67",
                    "match_tier": "exact",
                }
            ],
        },
    ]
}


class _RecordingMcpClient:
    """A stand-in for `MCPRuntimeClient` that returns what the real one returns: a STRING.

    Constructed exactly as `services._mcp_call` constructs it, so the seam under test is
    the production one and only the socket is replaced.
    """

    calls: list[tuple[str, dict[str, Any]]] = []
    answer: str = ""

    def __init__(self, url: str, timeout_seconds: int | None = None) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def call_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        type(self).calls.append((tool_name, args))
        return type(self).answer


def _wire_mcp(monkeypatch: Any, answer: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Replace the MCP client with one that answers `answer` as a JSON STRING."""
    from app.services import ai_assistant_service

    _RecordingMcpClient.calls = []
    _RecordingMcpClient.answer = json.dumps(answer)
    monkeypatch.setattr(ai_assistant_service, "MCPRuntimeClient", _RecordingMcpClient)
    return _RecordingMcpClient.calls


# --------------------------------------------------------------------------- #
# H - the cross-domain probe result is a STRING, and its arguments are tool arguments.
# --------------------------------------------------------------------------- #


class TestCrossDomainProbeCrossesTheRealSeam:
    def test_a_code_with_neither_stock_nor_incoming_is_named_with_an_escalation_offer(
        self, monkeypatch
    ) -> None:
        """The whole answer half of the owner's three-code turn, on the production seam.

        Red before the fix for two independent reasons, either of which alone is enough
        to lose the sentence: the seam returns a string that `run_crossdomain` throws
        away, and the arguments it sends are not the tool's.
        """
        from app.services.chatbot.lanes.business import answer as answer_mod
        from app.services.chatbot.lanes.business import fetch as fetch_mod
        from app.services.chatbot.lanes.business import services as services_mod

        calls = _wire_mcp(monkeypatch, INCOMING_NONE_ENVELOPE)

        trigger = {
            "tool": "crm_inventory_stock_balance_list",
            "entities": [
                {"uuid": m["matches"][0]["uuid"], "entity_type": "product", "code": r_code}
                for r_code, m in (
                    (r["token"], r) for r in RESOLVED["resolutions"]
                )
            ],
            "semantic_input": {"contact_id": "900000008", "space_id": "364817"},
            "contact_id": "900000008",
        }
        structured = fetch_mod.output_structurer(STOCK_ENVELOPE, trigger)
        validated = answer_mod.validator(structured, semantic_parser=PARSER)

        services = services_mod.production_answer_services(None)
        out = answer_mod.run_crossdomain(
            validated,
            parser=PARSER,
            resolved=RESOLVED,
            session_block={"session_vars": {"variables": {}}},
            entities_names=None,
            services=services,
            contact_id="900000008",
            space_id="364817",
        )

        assert calls, "the cross-domain probe never reached the MCP client"
        name, args = calls[0]
        assert name == "crm_incoming_stock_list"
        assert "product_ids" in args, (
            "the probe sent sub-get-results' workflowInputs as the MCP tool arguments; "
            "the server ignores them and answers with the unfiltered list shape - "
            f"got {sorted(args)}"
        )
        assert "semantic_input" not in args and "user_prompt" not in args, (
            f"n8n workflowInputs leaked into the MCP tool arguments: {sorted(args)}"
        )

        block = ((out.get("render") or {}).get("_xdBlock") or {}).get("block") or ""
        assert "No stock and no incoming for MSK11A-QT" in block, (
            "the probe answered as a string and the render degraded to no_envelope, so "
            f"the code with nothing on either side went unmentioned: {block!r}"
        )
        # The block states what is absent and nothing more: the escalate offer is written
        # ONCE by `tail/compose.crossdomain_compose` from `block["team"]` (8 Sep 2026,
        # turns 0184d84d / 5f73ddb0 / 90a1637a carried it twice).
        assert "escalate" not in block.lower(), (
            f"the cross-domain block must not carry the offer itself: {block!r}"
        )

    def test_the_unfiltered_list_shape_is_not_read_as_an_envelope(self, monkeypatch) -> None:
        """The measurement that makes the argument fix load-bearing rather than tidy.

        `{"data": [], "total": 0, ...}` is what the MCP answers the workflowInputs shape
        with. Parsed or not, it carries no `items` / `answers` / `has_result`, so the
        renderer degrades - which is why parsing the string alone would not have fixed
        the turn.
        """
        from app.services.chatbot.lanes.business.answer import crossdomain_render

        out = crossdomain_render(
            UNFILTERED_LIST_ANSWER,
            zeroset={
                "origin_domain": "inventory",
                "team": "warehouse",
                "returned_codes": ["MWT5727SS-CR"],
                "missing": [
                    {
                        "code": "MSK11A-QT",
                        "_n": "MSK11A-QT",
                        "uuid": "ef3f2fd2-254a-4ab5-a7d0-6d9139a67d67",
                    }
                ],
            },
            validator={"has_result": True},
        )
        assert out["_xdBlock"]["degraded"] is True
        assert out["_xdBlock"]["reason"] == "no_envelope"


# --------------------------------------------------------------------------- #
# B - family-fetch returns ORM rows.
# --------------------------------------------------------------------------- #


def _seed_family(session_factory: Any, codes: list[str]) -> str:
    """One company, one category, one uom, one product per code. Returns the company id."""
    db = session_factory()
    company = Company(name=unique_code("FAM"), code=unique_code("FAM")[:50])
    db.add(company)
    db.flush()
    category = ProductCategory(
        category_code=unique_code("CAT")[:50],
        category_name="ZZT family category",
        company_id=company.id,
    )
    uom = UnitOfMeasure(uom_code=unique_code("UOM")[:20], uom_name="Each", company_id=company.id)
    db.add_all([category, uom])
    db.flush()
    for code in codes:
        db.add(
            Product(
                product_code=code,
                product_name=f"ZZT {code}",
                category_id=category.id,
                base_uom_id=uom.id,
                list_price=10,
                is_active=True,
                company_id=company.id,
            )
        )
    db.commit()
    return str(company.id)


class TestFamilyFetchCrossesTheRealProductService:
    def test_the_sibling_family_offer_lists_the_variant_the_products_service_returned(
        self, session_factory, monkeypatch
    ) -> None:
        """The owner's "Pls check eta srtwb1542" turn, on the production family seam.

        `answer_services_for` binds the REAL `ProductService.list_products`, so the rows
        the miss lane reads are whatever that service returns - ORM objects today. The
        expected reply is the shape the live capture
        `nodes/sub-miss-suggest-live/miss-suggest-result/ms-15193683.json` records:
        "Related products: 1. X - no incoming, ...".
        """
        from app.services.chatbot.engine import _scoped_factory
        from app.services.chatbot.lanes.business.miss_suggest import run_miss_lane
        from app.services.chatbot.lanes.business.services import answer_services_for

        company_id = _seed_family(session_factory, ["SRTWB1542", "SRTWB1542-MG"])
        # The SAME wrapper `engine.run_turn` puts round the factory (H56), so the family
        # read is scoped exactly as it is in production - the seam under test opens its
        # own session from this factory.
        scoped_factory = _scoped_factory(session_factory, frozenset({company_id}))
        _wire_mcp(monkeypatch, INCOMING_NONE_ENVELOPE)

        parser = {
            "message_type": "business_query",
            "intent_hint": "check_incoming",
            "domain_hint": "incoming",
            "access_levels": [],
        }
        gate = {
            "gate_passed": False,
            "gate_reason": "no incoming rows",
            "gate_debug": {"domain": "incoming"},
            "require_specific": False,
            "company_team": "purchasing",
            "compatible_entities": [
                {"entity_type": "product", "code": "SRTWB1542", "uuid": None},
            ],
        }
        not_found_item = {
            "escalate_message": "No incoming stock (ETA) found for SRTWB1542.",
            "is_clarification": False,
        }

        offer = run_miss_lane(
            not_found_item,
            parser=parser,
            resolved={"resolutions": []},
            gate=gate,
            services=answer_services_for(scoped_factory),
            build_result={"has_result": False},
            contact_id="900000008",
            space_id="364817",
            execution_id="zzt-family",
        )

        response = str(offer.get("suggest_response") or "")
        assert offer.get("suggest_offer") is True, (
            "the D3 family offer was skipped - `family_fetch` returned ORM rows and "
            f"`sibling_transform` read no product_code off them: {offer!r}"
        )
        assert "Related products:" in response, response
        assert "SRTWB1542-MG - no incoming" in response, response
        assert offer.get("suggest_quick_reply"), (
            f"the family offer carries no quick replies: {offer.get('suggest_quick_reply')!r}"
        )


# --------------------------------------------------------------------------- #
# I - a customer labelled by its internal code.
# --------------------------------------------------------------------------- #


def _seed_hanlim(session_factory: Any) -> str:
    """Two `customers` rows for one trading name, plus the product the turn names.

    The second row is the alias account the debtor master really carries
    ("... [A/C II]"), on its OWN uuid and its OWN code - which is what makes the gate's
    per-uuid dedupe keep both and the code-vs-name prefix collapse impossible.
    """
    db = session_factory()
    company = Company(name=unique_code("HAN"), code=unique_code("HAN")[:50])
    db.add(company)
    db.flush()
    db.add_all(
        [
            Customer(
                customer_code="300-H070",
                customer_name="HANLIM TRADING SDN BHD",
                is_active=True,
                company_id=company.id,
            ),
            Customer(
                customer_code="300-H118",
                customer_name="HANLIM TRADING SDN BHD [A/C II]",
                is_active=True,
                company_id=company.id,
            ),
        ]
    )
    category = ProductCategory(
        category_code=unique_code("CAT")[:50],
        category_name="ZZT hanlim category",
        company_id=company.id,
    )
    uom = UnitOfMeasure(uom_code=unique_code("UOM")[:20], uom_name="Each", company_id=company.id)
    db.add_all([category, uom])
    db.flush()
    db.add(
        Product(
            product_code="RPACC",
            product_name="ZZT RPACC",
            category_id=category.id,
            base_uom_id=uom.id,
            list_price=10,
            is_active=True,
            company_id=company.id,
        )
    )
    db.commit()
    return str(company.id)


# The owner's turn of 6 Sep 2026, as `build-ctx` hands it to `resolve-entity`.
_HANLIM_TEXT = "delivery to hanlim customer, product rpacc"
_HANLIM_CTX: dict[str, Any] = {
    "text": {"message": {"message": {"text": _HANLIM_TEXT}}},
    "parse": {
        "output": {
            "message_type": "business_query",
            "intent_hint": "check_order",
            "domain_hint": "orders",
            "match_mode": "or",
            "access_levels": [],
            "entities": [
                {"raw": "hanlim", "hint": "customer"},
                {"raw": "rpacc", "hint": "product"},
            ],
        }
    },
}


def _real_resolve(db: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    """The lane's OWN body (`resolve_gate.resolve_entity_body`) through the lane's OWN
    binding (`services._resolve_entity`), minus the two LLM-reaching flags - the
    spec-search fallback calls a provider, and no test here may.
    """
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
    from app.config import settings
    from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

    body = {**resolve_entity_body(ctx), "spec_fallback": False, "understand_phrase": False}
    principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
    return resolve_reference_post(
        ResolveReferenceRequest(**body), current_user=principal, db=db
    )


class TestACustomerIsLabelledByItsName:
    def test_the_not_found_line_names_the_customer_never_its_debtor_code(
        self, session_factory
    ) -> None:
        """"delivery to hanlim customer, product rpacc", through the real resolver,
        the real gate and the real `output_structurer`."""
        from app.models.base import set_company_scope
        from app.services.chatbot.lanes.business import fetch as fetch_mod
        from app.services.chatbot.lanes.business.gate import run_gate

        company_id = _seed_hanlim(session_factory)
        db = session_factory()
        set_company_scope(db, frozenset({company_id}))
        resolved = _real_resolve(db, _HANLIM_CTX)

        customers = [
            m
            for r in (resolved.get("resolutions") or [])
            for m in (r.get("matches") or [])
            if m.get("entity_type") == "customer"
        ]
        assert len(customers) >= 2, (
            "the fixture is meant to produce TWO customer matches on two uuids - that is "
            f"what the gate's per-uuid dedupe keeps: {customers!r}"
        )

        parser = {
            "message_type": "business_query",
            "intent_hint": "check_order",
            "domain_hint": "orders",
        }
        gated = run_gate({}, parser=parser, resolver=resolved)
        entities = gated.get("compatible_entities") or []

        trigger = {
            "tool": "crm_order_management_orders_list",
            "entities": entities,
            "semantic_input": {"contact_id": "900000008", "space_id": "364817"},
            "contact_id": "900000008",
        }
        envelope = {
            "result_type": "orders",
            "intro": "No matching results found.",
            "items": [],
            "attachments": [],
            "action_links": [],
            "last_updated_at": None,
            "has_result": False,
            "lookup_companies": [{"name": "Sorento"}, {"name": "Mocha"}],
        }
        out = fetch_mod.output_structurer(envelope, trigger)
        response = str(out.get("response") or "")

        assert "customer HANLIM TRADING SDN BHD, product RPACC" in response, (
            "the axis-labelled subject must name the customer the way the customer said "
            f"it: {response!r}"
        )
        assert "300-H070" not in response and "300-H118" not in response, (
            f"an internal debtor code reached the customer: {response!r}"
        )
        assert "[A/C II]" not in response, (
            "the alias account is the SAME customer and must be printed once: "
            f"{response!r}"
        )
