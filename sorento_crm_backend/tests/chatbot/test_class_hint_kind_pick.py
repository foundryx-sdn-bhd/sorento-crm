"""CLASS-HINT-KIND-PICK (owner, 6 Oct 2026, "unacceptable"): a confident category word is
never re-typed to a promotion or a customer by name match.

Dev dry run (chatbot.turns 4d18caff, contact 437264483): "any basin with thickness 1.0mm"
-> parser `basin` hinted `category`, confident, `1.0mm` a grounded thickness spec, domain
master_products -> "Which one do you mean? 1. basin (promotion) 2. basin (customer)".

Mechanism (origin/main 9fc96e29a, card `documentation/plans/chatbot/CARD-class-hint-kind-
pick-06oct.md`): the primary pass pins `category` to product under a product domain and
finds no code; `references.py:2267-2343` reruns the zero-hit token against every kind,
where `ILIKE '%basin%'` hits promotion descriptions (PDF filenames) and a customer name;
`turn_runtime.resolve_kinds` counts the kinds, `turn/reconcile.py` mints the pick. And
because the token then counts as resolved, `references.py:3090` skips the spec/class
search that already knows "basin" as Wash Basin. The re-type armed the question AND hid
the answer.

Seam (crew ruling on the card, Q2 = S3): one optional request field,
`hint_locked_tokens`, built by `resolve_gate.resolve_entity_body` from the parser's own
`hint` + `confident` (never from the message text) and read at the ONE place the rerun
collects its tokens. Absent field = byte-identical response for every other caller.

Q1 (reach), owner verbatim: "why is cabana asked either a promotion or attachment? that
is absolutely wrong, cabana is a brand." So: a word the catalogue knows as a brand (exact,
case-insensitive on `brands.brand_code` / `brand_name`, never the spelling tier) resolves
as brand whatever the parser hinted; a confident `category` / `product_type` / `product` /
`brand` hint keeps its kind; promotion, customer, attachment, transporter and form are
never assigned by name substring to such a token. The D2 control in
`test_rearch_r11_catalogue.py` is superseded and rewritten there on the real resolver.

Harness: the REAL engine and the REAL resolver route in process (the same
`business_services._resolve_entity` binding production uses, spec_fallback left ON), over
rows seeded on the blank schema in the dev shape (two promotion PDFs and one customer
whose names contain "basin", one Wash Basin class with two products); only the MCP
network boundary is doubled (`MCPRuntimeClient.call_tool`), exactly as
`test_rearch_r12_handpass12_c.py`'s S-ENGINE group does.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.models.inventory import Stock, Warehouse
from app.models.marketing import Promotion
from app.models.order import Customer
from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure
from app.models.resources import Attachment, AttachmentType
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.product_class_signal import backfill_category_signals
from app.services.product_spec_derivation import derive_for_code
from app.services.product_spec_registry import seed_spec_registry
from tests._pg_fixture import unique_code
from tests.chatbot.test_engine import _envelope, _parser_output, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_outstanding_lane import _seed_contact as _seed_business_contact
from tests.chatbot.test_r3_pending_end_to_end import _session_of

KIND_PICK_QUESTION = "Which one do you mean?"
PRODUCTS_TOOL = "crm_master_products_list"
STOCK_TOOL = "crm_inventory_stock_balance_list"
LOCK_FIELD = "hint_locked_tokens"

# The dev rows' shape: promotion descriptions are PDF filenames carrying the class word
# (31 on dev per the crew scout, two here), one customer whose name carries it.
PROMO_DESCRIPTIONS = ("6. Wash Basin.pdf", "CABANA BASIN TAP PROMO_09092026 END USER.pdf")
CUSTOMER_NAME = "ZZT BASIN TRADING SDN BHD"


# --------------------------------------------------------------------------- #
# Seeding: the dev shape on the blank schema.
# --------------------------------------------------------------------------- #


def _seed_world(session_factory, *, cabana: bool = False) -> dict[str, Any]:
    """Two Wash Basin products (class derived off the real description rule), two
    promotions and one customer whose NAMES contain "basin", plus the spec registry
    and the category class signal the spec search reads. Returns the ids.

    `cabana=True` adds the D2 shape the owner ruled on: a live Brand row Cabana, two
    Cabana wash basins with stock, and an attachment PDF whose filename carries
    "cabana" beside the promotion PDF that already does."""
    db = session_factory()
    brand = None
    warehouse = None
    if cabana:
        brand = Brand(id=str(uuid.uuid4()), brand_code="CABANA", brand_name="Cabana", is_active=True, company_id=DEFAULT_COMPANY_ID)
        warehouse = Warehouse(
            id=str(uuid.uuid4()), warehouse_code=unique_code("WH")[:20], warehouse_name="ZZT WH", company_id=DEFAULT_COMPANY_ID
        )
        db.add_all([brand, warehouse])
        db.flush()
    category = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=unique_code("WB")[:50],
        category_name="ZZT Wash Basin",
        class_label="Wash Basin",
        search_synonyms=["wash basin", "basin"],
        company_id=DEFAULT_COMPANY_ID,
    )
    uom = UnitOfMeasure(
        id=str(uuid.uuid4()), uom_code=unique_code("U")[:20], uom_name="Each", company_id=DEFAULT_COMPANY_ID
    )
    db.add_all([category, uom])
    db.flush()
    backfill_category_signals(db)
    seed_spec_registry(db)

    basins: dict[str, str] = {}
    for suffix, description in (("A", "SORENTO WHITE WASH BASIN"), ("B", "SORENTO GREY WASH BASIN")):
        # Alpha-only suffix: the resolver's substring tiers must never find a digit of
        # the code inside "1.0mm" or the other way round.
        code = unique_code(f"ZZBSN{suffix}", alpha=True)
        product = Product(
            id=str(uuid.uuid4()),
            product_code=code,
            product_name=code,
            description=description,
            category_id=category.id,
            base_uom_id=uom.id,
            list_price=Decimal("1.00"),
            is_active=True,
            company_id=DEFAULT_COMPANY_ID,
        )
        db.add(product)
        db.flush()
        derive_for_code(db, code)
        basins[code] = product.id

    cabana_basins: dict[str, str] = {}
    if cabana:
        assert brand is not None and warehouse is not None
        for suffix, description in (("A", "CABANA WHITE WASH BASIN"), ("B", "CABANA GREY WASH BASIN")):
            code = unique_code(f"ZZCBN{suffix}", alpha=True)
            product = Product(
                id=str(uuid.uuid4()),
                product_code=code,
                product_name=code,
                description=description,
                category_id=category.id,
                base_uom_id=uom.id,
                brand_id=brand.id,
                list_price=Decimal("1.00"),
                is_active=True,
                company_id=DEFAULT_COMPANY_ID,
            )
            db.add(product)
            db.flush()
            derive_for_code(db, code)
            db.add(
                Stock(
                    id=str(uuid.uuid4()), product_id=product.id, warehouse_id=warehouse.id,
                    quantity_on_hand=5, company_id=DEFAULT_COMPANY_ID,
                )
            )
            cabana_basins[code] = product.id
        promo_type = AttachmentType(type_name="ZZT Promotion", code=None, allowed_extensions="pdf", is_direct_access=False)
        db.add(promo_type)
        db.flush()
        db.add(
            Attachment(
                attachment_type_id=promo_type.id,
                original_filename=PROMO_DESCRIPTIONS[1],
                stored_filename=f"zzt-{PROMO_DESCRIPTIONS[1]}",
                file_path=f"https://example.test/zzt-{PROMO_DESCRIPTIONS[1]}",
                company_id=DEFAULT_COMPANY_ID,
            )
        )
        db.flush()

    promotion_ids: list[str] = []
    for description in PROMO_DESCRIPTIONS:
        promo = Promotion(id=str(uuid.uuid4()), description=description, is_active=True)
        db.add(promo)
        db.flush()
        promotion_ids.append(promo.id)

    customer_code = unique_code("BSN", alpha=True)[:50]
    customer = Customer(
        id=str(uuid.uuid4()),
        customer_code=customer_code,
        customer_name=CUSTOMER_NAME,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(customer)
    db.commit()
    # Ids, not rows: a plain test session has no company scope set, so a later ORM read of
    # a company-scoped table on it returns nothing (fail-closed by design).
    return {
        "basins": basins,
        "cabana_basins": cabana_basins,
        "promotions": promotion_ids,
        "customer": customer.id,
        "customer_code": customer_code,
        "category_id": category.id,
        "uom_id": uom.id,
        "brand_id": brand.id if brand is not None else None,
        "warehouse_id": warehouse.id if warehouse is not None else None,
    }


def _seed_digit_coded_cabana_product(session_factory, world: dict[str, Any], code: str) -> str:
    """One more Cabana product, coded with digits, on the `cabana=True` world."""
    db = session_factory()
    product = Product(
        id=str(uuid.uuid4()),
        product_code=code,
        product_name=code,
        description="CABANA S-TRAP 250MM ONE PIECE WC",
        category_id=world["category_id"],
        base_uom_id=world["uom_id"],
        brand_id=world["brand_id"],
        list_price=Decimal("1.00"),
        is_active=True,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(product)
    db.flush()
    derive_for_code(db, code)
    db.add(
        Stock(
            id=str(uuid.uuid4()), product_id=product.id, warehouse_id=world["warehouse_id"],
            quantity_on_hand=5, company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.commit()
    return code


def _capturing_call_tool(
    monkeypatch, *, stock_by_id: dict[str, str] | None = None
) -> list[tuple[str, dict[str, Any]]]:
    """Only the network boundary is doubled. The products and stock tools answer from the
    ids they are sent, so a wrong id shows up as a wrong reply, never a canned one.
    `stock_by_id` is `{product uuid: code}` for the rows the stock tool should hold."""
    from app.services.ai_assistant_service import MCPRuntimeClient

    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        calls.append((name, dict(arguments)))
        if name == STOCK_TOOL:
            wanted = [pid for pid in (arguments.get("product_ids") or []) if pid in (stock_by_id or {})]
            rows = [
                {
                    "flags": {},
                    "title": stock_by_id[pid],
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": stock_by_id[pid]},
                        {"key": "total_on_hand", "label": "Total", "value": 5},
                    ],
                }
                for pid in wanted
            ]
            return json.dumps(
                {
                    "items": rows,
                    "has_result": bool(rows),
                    "attachments": [],
                    "action_links": [],
                    "result_type": "stock_compact",
                    "intro": "Stock summary for the requested products." if rows else "No matching results found.",
                }
            )
        if name == PRODUCTS_TOOL:
            ids = list(arguments.get("product_ids") or [])
            items = [
                {
                    "title": f"product {pid}",
                    "flags": {},
                    "fields": [{"key": "product_code", "label": "Product Code", "value": f"product {pid}"}],
                }
                for pid in ids
            ]
            return json.dumps(
                {
                    "result_type": "products",
                    "intro": "Here are the matching products." if items else "No matching results found.",
                    "items": items,
                    "has_result": bool(items),
                    "attachments": [],
                    "action_links": [],
                }
            )
        return json.dumps({"items": [], "has_result": False})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)
    return calls


def _recording_resolver(monkeypatch) -> list[dict[str, Any]]:
    """Production's own `resolve_entity` binding, with each (body, response) pair kept, so
    a test can read what the engine SENT and what the resolver ANSWERED without a
    second resolver."""
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.lanes.business import services as business_services

    exchanges: list[dict[str, Any]] = []
    real_production_services = business_services.production_services

    def recording_production_services(db, *, space_id=None):
        bundle = real_production_services(db, space_id=space_id)
        inner = bundle.resolve_entity

        def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            response = inner(body)
            exchanges.append({"body": dict(body), "response": response})
            return response

        return bundle.__class__(
            access_types=bundle.access_types, resolve_entity=resolve_entity, probe=bundle.probe
        )

    monkeypatch.setattr(engine_mod.business_services, "production_services", recording_production_services)
    return exchanges


def _class_entity(raw: str = "basin", *, confident: bool = True) -> dict[str, Any]:
    return {"raw": raw, "hint": "category", "canonical_code": None, "current_message": True, "confident": confident}


def _thickness_entity() -> dict[str, Any]:
    """The owner's recorded spec entity: `{raw "1.0mm", hint specification, spec_key
    thickness, spec_value 1}`."""
    return {
        "raw": "1.0mm",
        "hint": "specification",
        "canonical_code": None,
        "current_message": True,
        "confident": True,
        "spec_key": "thickness",
        "spec_value": 1,
        "spec_unit": "mm",
    }


def _recorded_verdict(entities: list[dict[str, Any]], *, user_goal: str) -> dict[str, Any]:
    return _parser_output(
        intent_hint="check_product",
        domain_hint="master_products",
        match_mode="and",
        user_goal=user_goal,
        entities=entities,
    )


def _turn(session_factory, stub_parser, verdict: dict[str, Any], *, text: str, msg_id: str):
    from app.services.chatbot import engine as engine_mod

    stub_parser(verdict)
    envelope = _envelope()
    envelope.message["message"]["messageId"] = msg_id
    envelope.message["message"]["message"]["text"] = text
    return engine_mod.run_turn(envelope, session_factory=session_factory)


def _said(result) -> str:
    return "\n".join(
        [((result.reply or {}).get("text") or "")]
        + [a.get("text") or "" for a in (result.actions or []) if isinstance(a, dict)]
    )


def _assert_no_kind_pick(session_factory, result, said: str) -> None:
    assert result.status == "done", result.error
    assert KIND_PICK_QUESTION not in said, said
    for kind in ("promotion", "customer", "attachment", "transporter", "form"):
        assert f"({kind})" not in said, said
    open_question = _session_of(session_factory).get("open_question") or {}
    assert open_question.get("kind") != "kind_pick", open_question


def _focus_brand_words(session_factory) -> list[str]:
    """`focus.brands` holds plain code strings (`turn/apply._CODE_ONLY_FIELDS`), the
    typed word when the parser gave no canonical code."""
    focus = _session_of(session_factory).get("focus") or {}
    return [
        str(b.get("raw") or b.get("code") or "" if isinstance(b, dict) else b).lower()
        for b in (focus.get("brands") or [])
    ]


# --------------------------------------------------------------------------- #
# The owner's turn, through the real engine and the real resolver.
# --------------------------------------------------------------------------- #


class TestAConfidentCategoryWordIsNeverReTyped:
    def test_the_recorded_basin_turn_never_asks_a_kind_pick(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """The owner's dev turn verbatim: "any basin with thickness 1.0mm". Never
        "Which one do you mean? 1. basin (promotion) 2. basin (customer)"."""
        _seed_business_contact(session_factory, variables={})
        _seed_world(session_factory)
        _capturing_call_tool(monkeypatch)
        stub_access()
        result = _turn(
            session_factory, stub_parser,
            _recorded_verdict([_class_entity(), _thickness_entity()], user_goal="finding a basin with thickness 1.0mm"),
            text="any basin with thickness 1.0mm", msg_id="ZZT-chkp-recorded",
        )
        _assert_no_kind_pick(session_factory, result, _said(result))

    def test_a_bare_class_word_never_asks_a_kind_pick(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """"basin" alone: the same 2 + 1 name hits on the rerun, the same two-way pick
        today. Never."""
        _seed_business_contact(session_factory, variables={})
        _seed_world(session_factory)
        _capturing_call_tool(monkeypatch)
        stub_access()
        result = _turn(
            session_factory, stub_parser,
            _recorded_verdict([_class_entity()], user_goal="looking for basins"),
            text="basin", msg_id="ZZT-chkp-bare",
        )
        _assert_no_kind_pick(session_factory, result, _said(result))

    def test_the_class_word_stays_unresolved_so_the_spec_tier_answers_it(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """The other half of the defect: the rerun's promotion rows made "basin" count as
        resolved, which SKIPPED the spec/class search (`references.py:3090`,
        `_every_caller_token_resolved`). With the word kept as the parser's class, the
        resolver's own answer carries no promotion or customer match under "basin" and
        the Wash Basin products reach the products tool. No promotion id ever does."""
        _seed_business_contact(session_factory, variables={})
        world = _seed_world(session_factory)
        calls = _capturing_call_tool(monkeypatch)
        exchanges = _recording_resolver(monkeypatch)
        stub_access()
        result = _turn(
            session_factory, stub_parser,
            _recorded_verdict([_class_entity()], user_goal="looking for basins"),
            text="basin", msg_id="ZZT-chkp-spec",
        )
        assert result.status == "done", result.error
        assert exchanges, "the resolver must be asked at all"
        response = exchanges[-1]["response"]
        basin_resolutions = [
            r for r in (response.get("resolutions") or []) if str(r.get("token") or "").lower() == "basin"
        ]
        kinds = sorted(
            {str(m.get("entity_type")) for r in basin_resolutions for m in (r.get("matches") or [])}
        )
        assert "promotion" not in kinds and "customer" not in kinds, (kinds, response.get("fallback_applied"))
        tiers = {str(m.get("match_tier")) for r in basin_resolutions for m in (r.get("matches") or [])}
        assert tiers <= {"spec_search"}, tiers
        product_calls = [args for name, args in calls if name == PRODUCTS_TOOL]
        assert product_calls, f"the products tool must run, no catalogue dump and no miss without a fetch: {calls}"
        sent_ids = set(product_calls[0].get("product_ids") or [])
        assert sent_ids == set(world["basins"].values()), (sent_ids, world)
        assert not sent_ids & set(world["promotions"]), (sent_ids, world["promotions"])


class TestABrandWordIsTheBrandWhateverTheParserHinted:
    """Owner Q1 verbatim: "cabana is a brand". The D2 shape (`test_rearch_r11_catalogue.py`,
    "cabana stock" hinted `product`, a promotion PDF and an attachment PDF named cabana,
    no product coded cabana) through the REAL resolver over a live Brand row Cabana."""

    def _verdict(self, hint: str) -> dict[str, Any]:
        return _parser_output(
            intent_hint="check_stock",
            domain_hint="inventory",
            match_mode="and",
            user_goal="checking cabana stock",
            entities=[{"raw": "cabana", "hint": hint, "canonical_code": None, "current_message": True, "confident": True}],
        )

    @pytest.mark.parametrize("hint", ["product", "brand", "customer"])
    def test_cabana_stock_is_the_brand_scoped_stock_answer(
        self, hint, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """Whatever the parser hinted the word as, the reply is the existing brand-scoped
        counted set (card addendum item 1, measured), never "Which one do you mean?",
        and the word lands on `focus.brands`."""
        _seed_business_contact(session_factory, variables={})
        world = _seed_world(session_factory, cabana=True)
        stock_by_id = {pid: code for code, pid in world["cabana_basins"].items()}
        calls = _capturing_call_tool(monkeypatch, stock_by_id=stock_by_id)
        stub_access()
        result = _turn(session_factory, stub_parser, self._verdict(hint), text="cabana stock", msg_id=f"ZZT-chkp-cabana-{hint}")
        said = _said(result)
        _assert_no_kind_pick(session_factory, result, said)
        assert said.startswith("Here's what you want: Cabana wash basins with stock (2)"), said
        for code in world["cabana_basins"]:
            assert code in said, (code, said)
        stock_calls = [args for name, args in calls if name == STOCK_TOOL]
        assert stock_calls, calls
        assert set(stock_calls[0].get("product_ids") or []) == set(world["cabana_basins"].values()), (stock_calls, world)
        assert "cabana" in _focus_brand_words(session_factory), _session_of(session_factory).get("focus")

    def test_a_word_no_brand_knows_still_walks_todays_path(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """Control: with NO Brand row Cabana the exact read finds nothing; the confident
        `product` hint is still locked (Q1), so there is still no kind pick, and the word
        is not on `focus.brands` (nothing in the catalogue says it is one)."""
        _seed_business_contact(session_factory, variables={})
        _seed_world(session_factory)
        _capturing_call_tool(monkeypatch)
        stub_access()
        result = _turn(session_factory, stub_parser, self._verdict("product"), text="cabana stock", msg_id="ZZT-chkp-cabana-none")
        _assert_no_kind_pick(session_factory, result, _said(result))
        assert "cabana" not in _focus_brand_words(session_factory), _session_of(session_factory).get("focus")


# --------------------------------------------------------------------------- #
# The seam: what the engine sends, read off the parser's own hint + confident.
# --------------------------------------------------------------------------- #


def _ctx(entities: list[dict[str, Any]], *, text: str = "any basin with thickness 1.0mm") -> dict[str, Any]:
    return {
        "text": {"message": {"message": {"text": text}}},
        "contact": {"id": "1"},
        "parse": {
            "output": {
                "message_type": "business_query",
                "intent_hint": "check_product",
                "domain_hint": "master_products",
                "match_mode": "and",
                "access_levels": [],
                "entities": entities,
            }
        },
    }


class TestTheResolveBodyLocksTheParsersClassHints:
    def test_a_confident_class_hint_is_sent_as_a_locked_token(self) -> None:
        from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

        body = resolve_entity_body(_ctx([_class_entity(), _thickness_entity()]))
        assert body["fallback_to_all_types"] is True, "the rerun stays on for every OTHER token"
        assert body.get(LOCK_FIELD) == ["basin"], body

    def test_product_type_is_a_class_hint_too(self) -> None:
        from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

        entity = {**_class_entity("wash basin"), "hint": "product_type"}
        body = resolve_entity_body(_ctx([entity], text="which wash basin"))
        assert body.get(LOCK_FIELD) == ["wash basin"], body

    def test_a_non_confident_class_hint_is_not_locked(self) -> None:
        """Card Q3 = (a): the parser said it was unsure, so today's rerun applies."""
        from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

        body = resolve_entity_body(_ctx([_class_entity(confident=False)]))
        assert LOCK_FIELD not in body, body

    def test_a_body_with_no_lockable_hint_is_byte_identical_to_today(self) -> None:
        """An `order` hint is not locked ("7445" hinted order and found as a product code is
        the re-type AC-1527 exists for), so the key never appears and every other caller
        shape is unchanged."""
        from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

        order = {"raw": "7445", "hint": "order", "canonical_code": None, "current_message": True, "confident": True}
        body = resolve_entity_body(_ctx([order], text="7445"))
        assert LOCK_FIELD not in body, body

    def test_confident_product_and_brand_hints_are_locked_too(self) -> None:
        """Owner Q1: a confident `product` or `brand` hint keeps its kind as well."""
        from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

        product = {"raw": "cabana", "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
        brand = {"raw": "Sorento", "hint": "brand", "canonical_code": None, "current_message": True, "confident": True}
        body = resolve_entity_body(_ctx([_class_entity(), product, brand], text="cabana basin sorento"))
        assert body.get(LOCK_FIELD) == ["basin", "cabana", "Sorento"], body

    def test_an_entity_the_caller_decided_is_locked_whatever_its_hint(self) -> None:
        """`locked_entity_ids` is `turn_runtime.resolve_kinds`'s exact live-brand read: an
        entity in it is locked even under an `order` hint or `confident: false`."""
        from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

        unsure = {"raw": "cabana", "hint": "order", "canonical_code": None, "current_message": True, "confident": False}
        body = resolve_entity_body(_ctx([unsure], text="cabana"), locked_entity_ids=frozenset({id(unsure)}))
        assert body.get(LOCK_FIELD) == ["cabana"], body


class TestOnlyTheExactBrandTierDecidesABrandWord:
    """Owner Q1 + addendum item 3: the catalogue's brand read is exact on name or code,
    case-insensitive, and NEVER the spelling-distance tier #1504 measured reaching nine
    more domains."""

    BRANDS = [
        {"id": "b1", "brand_code": "CAB", "brand_name": "Cabana", "company_id": None, "is_active": True},
        {"id": "b2", "brand_code": "SRT", "brand_name": "Sorento", "company_id": None, "is_active": True},
    ]

    @pytest.mark.parametrize("word, expected", [
        ("cabana", ["b1"]), ("CABANA", ["b1"]), ("  Cabana ", ["b1"]), ("cab", ["b1"]), ("srt", ["b2"]),
        ("Sorrento", []), ("sorneto", []), ("cabana basin", []), ("basin", []), ("", []),
    ])
    def test_exact_brand_rows(self, word, expected) -> None:
        from app.services.chatbot.turn_runtime import exact_brand_rows

        assert [r["id"] for r in exact_brand_rows(self.BRANDS, word)] == expected

    def test_the_spelling_tier_still_belongs_to_brand_rows_for_word_alone(self) -> None:
        """`brand_rows_for_word` (the order/sales intercept's read) keeps its wider tiers:
        the same misspelling it accepted yesterday it accepts today, and the exact read
        does not."""
        from app.services.chatbot.turn_runtime import brand_rows_for_word, exact_brand_rows

        assert [r["id"] for r in brand_rows_for_word(self.BRANDS, "Sorrento")] == ["b2"]
        assert exact_brand_rows(self.BRANDS, "Sorrento") == []


# --------------------------------------------------------------------------- #
# The route: the one place the rerun collects its tokens.
# --------------------------------------------------------------------------- #


def _route_call(db, body: dict[str, Any]) -> dict[str, Any]:
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post

    safe = {"spec_fallback": False, "understand_phrase": False, **body}
    return resolve_reference_post(ResolveReferenceRequest(**safe), current_user={"id": None}, db=db)


def _basin_body(**extra: Any) -> dict[str, Any]:
    return {
        "query": "any basin with thickness 1.0mm",
        "tokens": ["basin"],
        "match_mode": "and",
        "allowed_entity_types": ["category"],
        "domain": "master_products",
        "fallback_to_all_types": True,
        "exact_match": True,
        **extra,
    }


def _kinds_under(result: dict[str, Any], token: str) -> set[str]:
    out: set[str] = set()
    for r in result.get("resolutions") or []:
        if str(r.get("token") or "").lower() == token:
            out |= {str(m.get("entity_type")) for m in (r.get("matches") or [])}
    for m in result.get("intersection") or []:
        out.add(str(m.get("entity_type")))
    return out


class TestTheRerunLeavesALockedTokenAlone:
    def test_control_without_the_field_the_rerun_re_types_the_class_word_today(self, session_factory) -> None:
        """GREEN today: the seed reproduces dev. The same body minus the new field still
        answers "basin" with the promotion and the customer rows, so the three tests
        below are red for the right reason."""
        _seed_world(session_factory)
        result = _route_call(session_factory(), _basin_body())
        kinds = _kinds_under(result, "basin")
        assert {"promotion", "customer"} <= kinds, (kinds, result.get("unresolved_tokens"))
        assert result.get("fallback_applied") is True, result.keys()

    def test_a_locked_token_is_left_out_of_the_rerun(self, session_factory) -> None:
        _seed_world(session_factory)
        result = _route_call(session_factory(), _basin_body(**{LOCK_FIELD: ["basin"]}))
        kinds = _kinds_under(result, "basin")
        assert not kinds & {"promotion", "customer"}, kinds
        assert [t.lower() for t in result.get("unresolved_tokens") or []] == ["basin"], result.get("unresolved_tokens")
        assert not result.get("fallback_applied"), result.get("fallback_types_found")

    def test_a_locked_token_beside_a_code_keeps_the_codes_own_rerun(self, session_factory) -> None:
        """Per token, not per request (why S1 was not taken): a customer code the parser
        mis-hinted `product` beside the locked class word is still re-typed to the one
        kind that hit (AC-1527), while "basin" is not."""
        world = _seed_world(session_factory)
        db = session_factory()
        customer_code = world["customer_code"]
        body = _basin_body(
            query=f"basin for {customer_code}",
            tokens=["basin", customer_code],
            match_mode="or",
            allowed_entity_types=["category", "product"],
            **{LOCK_FIELD: ["basin"]},
        )
        result = _route_call(db, body)
        assert not _kinds_under(result, "basin") & {"promotion", "customer"}, result
        assert "customer" in _kinds_under(result, customer_code.lower()), result
        assert result.get("fallback_applied") is True, result.keys()

    def test_a_locked_brand_word_loses_its_primary_pass_promotion_fan_out(self, session_factory) -> None:
        """Under inventory a `brand` hint fans out to the PROMOTION probe in the primary
        pass (`entity_resolver._ENTITY_TYPE_EXPANSIONS`), so "cabana" met "CABANA BASIN
        TAP PROMO" before any rerun. Locked, the word keeps no promotion and stays
        unresolved for the HAS / spec branch."""
        _seed_world(session_factory)
        body = _basin_body(
            query="cabana stock", tokens=["cabana"], allowed_entity_types=["brand"], domain="inventory",
            **{LOCK_FIELD: ["cabana"]},
        )
        result = _route_call(session_factory(), body)
        assert not _kinds_under(result, "cabana") & {"promotion", "customer", "attachment"}, result
        assert [t.lower() for t in result.get("unresolved_tokens") or []] == ["cabana"], result.get("unresolved_tokens")

    def test_a_lock_entry_meets_the_routes_stopword_trimmed_echo(self, session_factory) -> None:
        """Reviewer S1: the route trims filler words off a multi-word token before resolving
        ("basin items" is echoed as "basin"). The lock entry takes the same trim, or the
        rerun re-types the word behind the lock's back."""
        _seed_world(session_factory)
        body = _basin_body(
            query="any basin items", tokens=["basin items"], allowed_entity_types=["category"],
            **{LOCK_FIELD: ["basin items"]},
        )
        result = _route_call(session_factory(), body)
        kinds = set()
        for r in result.get("resolutions") or []:
            kinds |= {str(m.get("entity_type")) for m in (r.get("matches") or [])}
        assert not kinds & {"promotion", "customer"}, result
        assert not result.get("fallback_applied"), result.get("fallback_types_found")

    def test_resource_attachment_keeps_its_own_kind_for_a_locked_brand_word(self, session_factory) -> None:
        """AC-12 through the route: "cabana catalog" under resource_attachment narrows to the
        Cabana catalogue by filename coverage, and that attachment row IS the answer, never
        a re-type of the brand word. Pins the r11 (a-d) shape; the K7 kill guard for the
        own-kind exception is `test_order_keeps_the_resolvers_customer_reading_for_a_
        locked_word` below (measured: red with the exception removed, this one stays green
        because the attachment-coverage path returns before the strip)."""
        _seed_world(session_factory, cabana=True)
        db = session_factory()
        direct_access = AttachmentType(type_name="ZZT Direct Access", code=None, allowed_extensions="pdf", is_direct_access=True)
        db.add(direct_access)
        db.flush()
        catalogue = Attachment(
            attachment_type_id=direct_access.id, original_filename="@ CABANA CATALOG 2025 (RESEARCHABLE).pdf",
            stored_filename="zzt-cabana-catalog.pdf", file_path="https://example.test/zzt-cabana-catalog.pdf",
            company_id=DEFAULT_COMPANY_ID,
        )
        db.add(catalogue)
        db.commit()
        body = _basin_body(
            query="cabana catalog", tokens=["cabana", "catalog"], match_mode="and",
            allowed_entity_types=["brand", "attachment"], domain="resource_attachment",
            **{LOCK_FIELD: ["cabana"]},
        )
        result = _route_call(db, body)
        rows = list(result.get("intersection") or []) + [
            m for r in result.get("resolutions") or [] for m in (r.get("matches") or [])
        ]
        assert any(m.get("entity_type") == "attachment" and m.get("uuid") == catalogue.id for m in rows), result

    def test_order_keeps_the_resolvers_customer_reading_for_a_locked_word(self, session_factory) -> None:
        """Crew ruling Q4 (out of scope) and #1504's control: under ORDER the resolver's own
        expansion reads a brand or category word as the customer it names; customer and
        transporter are the order domain's own kinds, kept like promotion under promotion."""
        world = _seed_world(session_factory)
        db = session_factory()
        body = _basin_body(
            query="basin trading orders", tokens=["basin trading"], match_mode="or",
            allowed_entity_types=["brand"], domain="order", **{LOCK_FIELD: ["basin trading"]},
        )
        result = _route_call(db, body)
        kinds = set()
        for r in result.get("resolutions") or []:
            kinds |= {str(m.get("entity_type")) for m in (r.get("matches") or [])}
        assert "customer" in kinds, (kinds, world["customer"], result)

    def test_the_request_domains_own_kind_is_kept(self, session_factory) -> None:
        """"cabana promo" under the PROMOTION domain is answered by the promotions whose
        description carries the brand: that match is the question's answer, never a
        re-type, so the lock leaves it."""
        _seed_world(session_factory)
        body = _basin_body(
            query="cabana promo", tokens=["cabana"], allowed_entity_types=["brand"], domain="promotion",
            **{LOCK_FIELD: ["cabana"]},
        )
        result = _route_call(session_factory(), body)
        assert "promotion" in _kinds_under(result, "cabana"), result

    def test_a_brand_word_beside_a_matched_code_opens_no_spec_path(self, session_factory) -> None:
        """AC-11, measured on the replay: locked and unresolved, "SORENTO" beside a matched
        code opened the spec fallback, which bound the brand and emitted its whole
        catalogue beside the one code asked. A brand word scopes the code: no spec rows,
        the code's own match alone."""
        world = _seed_world(session_factory, cabana=True)
        # A code WITH digits: `_code_matched` reads only a digit-bearing token as a code
        # (a word, however coded, describes a set). The alpha-only seeds above are for
        # the "basin" / "1.0mm" shapes and would not count here.
        code = _seed_digit_coded_cabana_product(session_factory, world, "ZZCB7318-RL")
        body = _basin_body(
            query=f"CABANA {code}", tokens=["CABANA", code], match_mode="and",
            allowed_entity_types=["brand", "product"], domain="inventory", spec_fallback=True,
            **{LOCK_FIELD: ["CABANA", code]},
        )
        result = _route_call(session_factory(), body)
        matches = [m for r in result.get("resolutions") or [] for m in (r.get("matches") or [])]
        matches += list(result.get("intersection") or [])
        assert "spec_search" not in {m.get("match_tier") for m in matches}, result
        assert code.lower() in {str(m.get("canonical_code") or "").lower() for m in matches}, result

    def test_without_the_field_a_brand_word_beside_a_code_still_opens_the_spec_path_today(self, session_factory) -> None:
        """Byte-identical contract for n8n / MCP (security review, finding 1): the brand
        guard is gated on the field, so a caller that sends none keeps today's spec path
        for the same request."""
        world = _seed_world(session_factory, cabana=True)
        # A brand with NO promotion or customer named after it, so the word is unresolved
        # today and today's path is the spec fallback (with "cabana" the promotion rows
        # resolve the word first and no fallback runs either way).
        db = session_factory()
        mocha = Brand(id=str(uuid.uuid4()), brand_code="MOCHA", brand_name="Mocha", is_active=True, company_id=DEFAULT_COMPANY_ID)
        db.add(mocha)
        db.commit()
        code = _seed_digit_coded_cabana_product(session_factory, {**world, "brand_id": mocha.id}, "ZZMC7319-RL")
        body = _basin_body(
            query=f"MOCHA {code}", tokens=["MOCHA", code], match_mode="and",
            allowed_entity_types=["brand", "product"], domain="inventory", spec_fallback=True,
        )
        result = _route_call(session_factory(), body)
        matches = [m for r in result.get("resolutions") or [] for m in (r.get("matches") or [])]
        matches += list(result.get("intersection") or [])
        assert "spec_search" in {m.get("match_tier") for m in matches}, result
        locked = _route_call(session_factory(), {**body, LOCK_FIELD: ["MOCHA", code]})
        locked_matches = [m for r in locked.get("resolutions") or [] for m in (r.get("matches") or [])]
        locked_matches += list(locked.get("intersection") or [])
        assert "spec_search" not in {m.get("match_tier") for m in locked_matches}, locked

    def test_a_description_beside_a_matched_code_still_opens_the_spec_path(self, session_factory) -> None:
        """Control (`test_resolve_brand_routing.py` pins the same): the guard is for a BRAND
        word only; an unanswered class description beside a code still reaches the spec
        fallback."""
        world = _seed_world(session_factory, cabana=True)
        code = next(iter(world["cabana_basins"]))
        body = _basin_body(
            query=f"{code} wash basin", tokens=[code, "wash basin"], match_mode="or",
            allowed_entity_types=["product", "category"], domain="inventory", spec_fallback=True,
            **{LOCK_FIELD: ["wash basin"]},
        )
        result = _route_call(session_factory(), body)
        tiers = {m.get("match_tier") for r in result.get("resolutions") or [] for m in (r.get("matches") or [])}
        assert "spec_search" in tiers, result

    def test_the_field_is_optional_and_absent_means_today(self, session_factory) -> None:
        """Byte-identical for n8n / MCP: a body without the field and a body with an
        empty list answer exactly alike."""
        _seed_world(session_factory)
        db = session_factory()
        without = _route_call(db, _basin_body())
        empty = _route_call(db, _basin_body(**{LOCK_FIELD: []}))
        # `elapsed_ms` is the one key that is a clock, not an answer.
        assert {k: v for k, v in without.items() if k != "elapsed_ms"} == {
            k: v for k, v in empty.items() if k != "elapsed_ms"
        }
