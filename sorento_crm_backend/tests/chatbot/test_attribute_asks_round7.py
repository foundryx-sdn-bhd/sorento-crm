"""Owner hand test of rounds 4 to 6 on PR #833 (27 Sep 2026 10:37 to 10:45 MYT, console
:3084, contact Mr Loo). Fix round 7, items 1 to 7 (F1 to F7 of the round 7 brief).

The owner's words (verbatim): "for #833, check out my recent converations, first i don't
know why it say it did not underatnd eta? is it becuase it thought it is spec? the result
is okay, but I need it to be more structured, so it follows the structure of normlaly how
we ask eta to the chatbot, like product code, container number, eta, <other fields>,
cincoming quantity..., then you see when i ask for s trap bowl price 8840, why it went
rogue? again when i ask p trap, all these questions are from a salesperson named Leena who
always ask this kind of question, again when i ask about close couple wc in p trap, it can
give, but I need it to beheave just like how we ask stock, i don't need the exra
description, just behave like normal stock ask, and why suddenly at the bottom say could
not find close couple wc? and you said other brnads have stock, so i tried cabana, but you
sid cabana got 3 only, why suddenly when i say cabana you say 1136 have stock, then wehn i
show, it is just pure cabana item not fitlered? pelase be more human and natural in this,
then you see when i ask about cert, you give me photos, and again, I need the output to
behave like normal product attachment ask, the structure"

The asks come from a salesperson (Leena) who types a product code, a descriptor and a
field, so every case below uses her exact message. Every turn runs `engine.run_turn` with
the real resolver, class vocabulary, spec registry and brands table; the parser verdict is
stubbed as the owner's turn traces read it, and the MCP tools are stubbed in their real
presenter shapes (`sorento_crm_mcp.presenters`).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from tests.chatbot.test_attribute_asks_round3 import (  # noqa: F401 - fixtures used by name
    _Chat,
    _ask,
    _bare,
    _display,
    _entity,
    world as r3world,
)
from tests.chatbot.test_attribute_asks_round4 import (  # noqa: F401 - fixtures used by name
    _header,
    _prod_like_resolve_entity,
    world as r4world,
)
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_lane_require import _certificate_for, _stock_for
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category, _incoming_for

# --------------------------------------------------------------------------- #
# World                                                                         #
# --------------------------------------------------------------------------- #


def _make(db, *, code: str, name: str, description: str, brand, category_id: str, uom_id: str):
    from app.models.product import Product
    from app.services.product_spec_derivation import derive_for_code

    row = Product(
        id=str(uuid.uuid4()),
        product_code=code,
        product_name=name,
        description=description,
        category_id=category_id,
        base_uom_id=uom_id,
        brand_id=brand.id,
        list_price=100,
        is_active=True,
    )
    db.add(row)
    db.flush()
    derive_for_code(db, code)
    return row


def _photo_for(db, *, product_id: str) -> None:
    """A Product Photos attachment on the product: the rows the owner got for "cert"."""
    from app.models.product import ProductAttachment
    from app.models.resources import Attachment, AttachmentType

    kind = db.query(AttachmentType).filter(AttachmentType.type_name == "Product Photos").first()
    if kind is None:
        kind = AttachmentType(id=str(uuid.uuid4()), type_name="Product Photos", allowed_extensions="jpg,png")
        db.add(kind)
        db.flush()
    att = Attachment(
        id=str(uuid.uuid4()),
        original_filename=f"photo-{uuid.uuid4().hex[:6]}.jpg",
        stored_filename=f"photo-{uuid.uuid4().hex[:6]}.jpg",
        file_path=f"zz/photos/{uuid.uuid4().hex}.jpg",
        attachment_type_id=kind.id,
    )
    db.add(att)
    db.flush()
    db.add(ProductAttachment(id=str(uuid.uuid4()), product_id=product_id, attachment_id=att.id))
    db.flush()


@pytest.fixture()
def world(r4world):
    """Round 4's world (Sorento, Mocha, Cabana; P trap and S trap water closets, basins,
    taps, tubs) plus the products the owner's seven messages name:

      - SRTKS65502 and SRTKS65502-BL, two Sorento kitchen sinks with incoming (item 1);
      - the 8840 family: SRTWC8840-SC (seat cover), SRTWC8840-LID, and the X-prefixed
        bowls SRTWCX8840-P (P trap) and SRTWCX8840-S (S trap) (items 2 to 4);
      - the 7604 family: SRTWC7604-FT, SRTWC7604-SC and SRTWCX7604-P-RL-NEW (item 4);
      - close coupled P trap water closets with stock: Sorento 4, Cabana 3, Mocha 2, and
        20 more Cabana products with stock in other classes, so "cabana" alone is a much
        larger set than the 3 the "Other brands" line counted (items 5 and 6);
      - Sorento kitchen taps, two wall mounted, each with a certificate AND a product
        photo (item 7).
    """
    db = r4world["db"]
    sorento, mocha, cabana = r4world["sorento"], r4world["mocha"], r4world["cabana"]
    uom = r4world["srt_tubs"][0].base_uom_id
    wh = r4world["warehouse"]
    wc = _class_category(db, "WC")
    ks = _class_category(db, "KS")
    ft = _class_category(db, "FT")
    ba = _class_category(db, "BA")

    def make(code, name, description, brand=sorento, category=wc):
        return _make(db, code=code, name=name, description=description, brand=brand, category_id=category, uom_id=uom)

    sinks = [
        make("SRTKS65502", "Sorento Kitchen Sink 65502", "SRTKS65502 STAINLESS STEEL KITCHEN SINK", category=ks),
        make("SRTKS65502-BL", "Sorento Kitchen Sink 65502 Black", "SRTKS65502-BL BLACK KITCHEN SINK", category=ks),
    ]
    for p in sinks:
        _incoming_for(db, product_id=p.id)

    f8840 = {
        "SRTWC8840-SC": make("SRTWC8840-SC", "Sorento 8840 Seat Cover", "SRTWC8840-SC SEAT COVER FOR WATER CLOSET"),
        "SRTWC8840-LID": make("SRTWC8840-LID", "Sorento 8840 Lid", "SRTWC8840-LID CISTERN LID FOR WATER CLOSET"),
        "SRTWCX8840-P": make("SRTWCX8840-P", "Sorento 8840 Bowl P Trap", "SRTWCX8840-P WATER CLOSET BOWL ONLY P-TRAP"),
        "SRTWCX8840-S": make("SRTWCX8840-S", "Sorento 8840 Bowl S Trap", "SRTWCX8840-S WATER CLOSET BOWL ONLY S-TRAP"),
    }
    f7604 = {
        "SRTWC7604-FT": make("SRTWC7604-FT", "Sorento 7604 Fitting", "SRTWC7604-FT FITTING SET FOR WATER CLOSET"),
        "SRTWC7604-SC": make("SRTWC7604-SC", "Sorento 7604 Seat Cover", "SRTWC7604-SC SEAT COVER FOR WATER CLOSET"),
        "SRTWCX7604-P-RL-NEW": make(
            "SRTWCX7604-P-RL-NEW", "Sorento 7604 Pedestal P Trap", "SRTWCX7604-P-RL-NEW PEDESTAL WATER CLOSET P-TRAP"
        ),
    }
    close_couple = {
        "sorento": [
            make(f"SRTWCC{i}", f"Sorento Close Coupled WC {i}", f"SRTWCC{i} CLOSE COUPLED P-TRAP WATER CLOSET")
            for i in range(4)
        ],
        "cabana": [
            make(f"CBWCC{i}", f"Cabana Close Coupled WC {i}", f"CBWCC{i} CLOSE COUPLED P-TRAP WATER CLOSET", brand=cabana)
            for i in range(3)
        ],
        "mocha": [
            make(f"MWCC{i}", f"Mocha Close Coupled WC {i}", f"MWCC{i} CLOSE COUPLED P-TRAP WATER CLOSET", brand=mocha)
            for i in range(2)
        ],
    }
    cabana_other = [
        make(f"CBBA{i:02d}", f"Cabana Towel Bar {i}", f"CBBA{i:02d} TOWEL BAR", brand=cabana, category=ba) for i in range(20)
    ]
    for p in [*close_couple["sorento"], *close_couple["cabana"], *close_couple["mocha"], *cabana_other]:
        _stock_for(db, product_id=p.id, warehouse_id=wh.id)

    kitchen_taps = [
        make("SRTKT101", "Sorento Kitchen Tap 101", "SRTKT101 CHROME KITCHEN TAP", category=ft),
        make("SRTKT102", "Sorento Kitchen Tap 102", "SRTKT102 CHROME KITCHEN TAP", category=ft),
    ]
    wall_taps = [
        make("SRTKT201", "Sorento Wall Kitchen Tap 201", "SRTKT201 WALL MOUNTED KITCHEN TAP", category=ft),
        make("SRTKT202", "Sorento Wall Kitchen Tap 202", "SRTKT202 WALL MOUNTED KITCHEN TAP", category=ft),
    ]
    for p in kitchen_taps + wall_taps:
        _certificate_for(db, product_id=p.id, valid_until=date.today() + timedelta(days=365))
        _photo_for(db, product_id=p.id)
    db.commit()
    return {
        **r4world,
        "sinks": sinks,
        "f8840": f8840,
        "f7604": f7604,
        "close_couple": close_couple,
        "cabana_other": cabana_other,
        "kitchen_taps": kitchen_taps,
        "wall_taps": wall_taps,
        "every": r4world["every"]
        + sinks
        + list(f8840.values())
        + list(f7604.values())
        + [p for rows in close_couple.values() for p in rows]
        + cabana_other
        + kitchen_taps
        + wall_taps,
    }


# --------------------------------------------------------------------------- #
# Tools, in their real presenter shapes                                         #
# --------------------------------------------------------------------------- #


def _incoming_tool(db):
    """`presenters._incoming_by_product`: one row per open shipment line, the packing list
    attached per shipment."""
    from app.models.procurement import InboundShipment, InboundShipmentLine
    from app.models.product import Product

    def call(args: dict[str, Any]) -> str:
        ids = list(args.get("product_ids") or [])
        rows = (
            db.query(Product, InboundShipment, InboundShipmentLine)
            .join(InboundShipmentLine, InboundShipmentLine.product_id == Product.id)
            .join(InboundShipment, InboundShipment.id == InboundShipmentLine.shipment_id)
            .filter(Product.id.in_(ids))
            .order_by(Product.product_code)
            .all()
            if ids
            else []
        )
        items, attachments = [], []
        for p, s, line in rows:
            items.append(
                {
                    "title": p.product_code,
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": p.product_code},
                        {"key": "product_name", "label": "Product Name", "value": p.product_name},
                        {"key": "shipping_container_number", "label": "Shipment Container", "value": f"CONT{s.shipment_number[-6:]}"},
                        {"key": "estimated_arrival_date", "label": "Estimated Arrival Date", "value": "2026-10-01"},
                        {"key": "batch_number", "label": "Batch", "value": "B1"},
                        {"key": "remaining_incoming_quantity", "label": "Incoming Quantity", "value": int(line.quantity_shipped)},
                        {"key": "warehouse_allocations", "label": "Warehouse Allocations", "value": "BRW 10"},
                    ],
                    "flags": {},
                }
            )
            attachments.append({"url": f"https://files.example/{s.shipment_number}.pdf", "filename": f"Packing list {s.shipment_number}.pdf"})
        return json.dumps(
            {
                "result_type": "incoming_stock",
                "intro": "Incoming stock found for the requested products.",
                "items": items,
                "attachments": attachments,
                "has_result": bool(items),
            }
        )

    return call


def _attachments_tool(db):
    """`presenters._product_attachments` over the seeded rows: EVERY attachment a product
    has (photos and certificates), narrowed only by the filters the call carries - so a
    certificate ask that forgets to narrow gets the photos, as the owner's did."""
    from app.models.certificate import Certificate, CertificateProduct, CertificateRevision
    from app.models.product import Product, ProductAttachment
    from app.models.resources import Attachment, AttachmentType

    def call(args: dict[str, Any]) -> str:
        ids = list(args.get("product_ids") or [])
        type_ids = set(args.get("attachment_type_ids") or [])
        cert_ids = set(args.get("certificate_ids") or [])
        items: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        products = db.query(Product).filter(Product.id.in_(ids)).order_by(Product.product_code).all() if ids else []
        for p in products:
            photos = (
                db.query(Attachment, AttachmentType)
                .join(ProductAttachment, ProductAttachment.attachment_id == Attachment.id)
                .join(AttachmentType, AttachmentType.id == Attachment.attachment_type_id)
                .filter(ProductAttachment.product_id == p.id)
                .all()
            )
            if not cert_ids:
                for att, kind in photos:
                    if type_ids and kind.id not in type_ids:
                        continue
                    items.append(
                        {
                            "title": p.product_code,
                            "fields": [
                                {"label": "Product Code", "value": p.product_code},
                                {"label": "Product Name", "value": p.product_name},
                                {"label": "Attachment Type", "value": kind.type_name},
                                {"label": "File Name", "value": att.original_filename},
                            ],
                            "flags": {},
                        }
                    )
                    attachments.append({"url": f"https://files.example/{att.stored_filename}", "filename": att.original_filename})
            certs = (
                db.query(Certificate, CertificateRevision)
                .join(CertificateProduct, CertificateProduct.certificate_id == Certificate.id)
                .outerjoin(CertificateRevision, CertificateRevision.id == Certificate.current_revision_id)
                .filter(CertificateProduct.product_id == p.id)
                .all()
            )
            if type_ids and not cert_ids and not _certification_type_ids(db) & type_ids:
                certs = []
            for cert, rev in certs:
                if cert_ids and cert.id not in cert_ids:
                    continue
                valid_until = rev.valid_until if rev is not None else None
                expired = bool(valid_until and valid_until < date.today())
                items.append(
                    {
                        "title": p.product_code,
                        "fields": [
                            {"label": "Product Code", "value": p.product_code},
                            {"label": "Product Name", "value": p.product_name},
                            {"label": "Attachment Type", "value": "Certification"},
                            {"label": "File Name", "value": f"{cert.certificate_number}.pdf"},
                            {"label": "Certificate Number", "value": cert.certificate_number},
                            {"label": "Valid Until", "value": valid_until.isoformat() if valid_until else None},
                            {"label": "Validity", "value": "Expired" if expired else "Valid"},
                        ],
                        "flags": {"expired": expired},
                    }
                )
                attachments.append({"url": f"https://files.example/{cert.certificate_number}.pdf", "filename": f"{cert.certificate_number}.pdf"})
        return json.dumps(
            {
                "result_type": "product_attachments",
                "intro": "Here are the product attachments.",
                "items": items,
                "attachments": attachments,
                "has_result": bool(items),
            }
        )

    return call


def _certification_type_ids(db) -> set[str]:
    from app.models.resources import AttachmentType

    return {t.id for t in db.query(AttachmentType).filter(AttachmentType.is_certificate.is_(True)).all()}


def _product_tool(db):
    """`presenters._products` for a price ask: code, name, description, list price."""
    from app.models.product import Product

    def call(args: dict[str, Any]) -> str:
        ids = list(args.get("product_ids") or [])
        rows = db.query(Product).filter(Product.id.in_(ids)).order_by(Product.product_code).all() if ids else []
        items = [
            {
                "title": p.product_code,
                "fields": [
                    {"key": "product_code", "label": "Product Code", "value": p.product_code},
                    {"key": "description", "label": "Description", "value": p.description},
                    {"key": "list_price", "label": "List Price", "value": f"MYR {float(p.list_price or 0):.2f}"},
                ],
                "flags": {},
            }
            for p in rows
        ]
        return json.dumps(
            {"result_type": "products", "intro": "Here are the matching products.", "items": items, "has_result": bool(items)}
        )

    return call


def _tools(world, calls: list[dict[str, Any]]):
    from tests.chatbot.test_attribute_asks_round3 import _stock_tool

    db = world["db"]
    stock = _stock_tool(db, world["warehouse"].warehouse_code)
    attachments = _attachments_tool(db)
    incoming = _incoming_tool(db)
    products = _product_tool(db)

    def fake_call_tool(name: str, args: dict[str, Any]) -> str:
        calls.append({"name": name, "args": dict(args)})
        if name == "crm_master_product_attachments_list":
            return attachments(args)
        if "incoming" in name or "shipment" in name:
            return incoming(args)
        if "stock" in name or "inventory" in name:
            return stock(args)
        return products(args)

    return fake_call_tool


class _Leena(_Chat):
    """Round 3's chat (every reply scanned for snake_case) over this round's tools."""

    def __init__(self, session_factory, monkeypatch, stub_parser, stub_access, world):
        import tests.chatbot.test_attribute_asks_round3 as round3

        monkeypatch.setattr(round3, "_s4_real_resolve_entity", _prod_like_resolve_entity)
        monkeypatch.setattr(round3, "_tools", _tools)
        super().__init__(session_factory, monkeypatch, stub_parser, stub_access, world)


@pytest.fixture()
def chat(session_factory, monkeypatch, stub_parser, stub_access, world):
    return _Leena(session_factory, monkeypatch, stub_parser, stub_access, world)


# --------------------------------------------------------------------------- #
# The parser's readings, as the owner's turn traces show them                   #
# --------------------------------------------------------------------------- #


def _code_ask(goal: str, *entities: dict[str, Any], attrs: tuple[str, ...] = (), **overrides: Any) -> dict[str, Any]:
    from tests.chatbot.test_engine import _parser_output

    base = dict(
        intent_hint="check_product",
        domain_hint="master_products",
        match_mode="and",
        user_goal=goal,
        requested_attributes=list(attrs),
        entities=list(entities),
    )
    base.update(overrides)
    return _parser_output(**base)


def _eta_ask(goal: str, raw: str, attrs: tuple[str, ...]) -> dict[str, Any]:
    return _code_ask(goal, _entity(raw, "product"), attrs=attrs, intent_hint="check_incoming", domain_hint="incoming")


def _brand_word(world, key: str) -> str:
    """The brand as the salesperson types it: its displayed name, lower case."""
    return _display(world[key].brand_name).lower()


def _brand_pick(world, key: str) -> dict[str, Any]:
    """"cabana" after "Other brands with stock: ...": the parser keeps the stock intent
    and reads the word as a brand, as the owner's trace shows (it answered a stock set of
    every Cabana product)."""
    return _code_ask(
        _brand_word(world, key),
        _entity(_brand_word(world, key), "brand"),
        attrs=("stock",),
        intent_hint="check_stock",
        domain_hint="inventory",
    )


# The owner's messages, verbatim, with the parser reading each one ------------ #

M1 = "65502 eta"
M2 = "I need srtwc8840 s trap bowl only price"
M3 = "Srtwc8840 bowl only price"
M4A = "Srtwc7604 p trap price"
M4B = "Srtwc8840 p Trap"
M5 = "Can you suggest close couple wc available stock in p trap"
M7A = "any kitchen tap has cert"
M7B = "any WALL MOUNTED KITCHEN TAP has cert"


def _v1(attrs: tuple[str, ...] = ("incoming",)) -> dict[str, Any]:
    return _eta_ask(M1, "65502", attrs)


def _v2() -> dict[str, Any]:
    return _code_ask(M2, _entity("srtwc8840", "product"), _entity("s trap bowl", "category"), attrs=("price",))


def _v3() -> dict[str, Any]:
    return _code_ask(M3, _entity("Srtwc8840", "product"), attrs=("price",))


def _v4a() -> dict[str, Any]:
    return _code_ask(M4A, _entity("Srtwc7604 p trap", "product"), attrs=("price",))


def _v4b() -> dict[str, Any]:
    return _code_ask(M4B, _entity("Srtwc8840 p Trap", "product"))


def _v5() -> dict[str, Any]:
    return _ask("stock", "close couple wc", M5, extra=[_entity("p trap", "spec")])


def _v7a() -> dict[str, Any]:
    return _ask("cert", "kitchen tap", M7A)


def _v7b() -> dict[str, Any]:
    return _ask("cert", "WALL MOUNTED KITCHEN TAP", M7B)


# Reply readers ---------------------------------------------------------------- #

_ROW_START = re.compile(r"^(\d+)\. ")


def _full_rows(text: str) -> list[list[str]]:
    """The numbered rows of a normal (full field) answer: each row's lines, the first
    starting "N. "."""
    rows: list[list[str]] = []
    for block in text.split("\n\n"):
        lines = block.strip().splitlines()
        if lines and _ROW_START.match(lines[0]):
            rows.append(lines)
    return rows


def _labels(row: list[str]) -> list[str]:
    out = []
    for i, line in enumerate(row):
        line = _ROW_START.sub("", line) if i == 0 else line
        m = re.match(r"^\*([^*:]+):\*", line)
        if m:
            out.append(m.group(1))
    return out


def _codes_listed(text: str, world) -> set[str]:
    return {
        p.product_code
        for p in world["every"]
        if re.search(rf"(?<![\w-]){re.escape(p.product_code)}(?![\w-])", text)
    }


#: Internal phrasing that must never reach a reply (F7).
_INTERNAL = re.compile(
    r"described set|qualifies nothing|predicate|require key|spec[_ ]search|unrecognized|scope term|\bNone\b|\bnull\b",
    re.IGNORECASE,
)


def _human(text: str) -> None:
    assert not _INTERNAL.search(text), text


# --------------------------------------------------------------------------- #
# F1 (item 1): an ETA ask never misreads its own word; rows keep the ETA shape  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("attrs", [("incoming",), (), ("estimated_arrival_date",)], ids=["incoming", "none", "eta-key"])
def test_f1_an_eta_ask_never_says_it_did_not_understand_eta(chat, world, attrs):
    text = chat.say(M1, _v1(attrs))
    assert "did not understand" not in text, text
    assert '"eta"' not in text.lower(), text
    assert "not recorded yet" not in text, text
    assert _codes_listed(text, world) == {"SRTKS65502", "SRTKS65502-BL"}, text
    # Moved in fix round 13 on PR #833 (code first, owner 28 Sep 2026: "if found by product
    # code -> forward asking"). "65502" matches SRTKS65502 and SRTKS65502-BL by code, so
    # it is a forward ask like "wc286 incoming", and the incoming forward ask over more
    # than one code is the pick list (`gate.REQUIRE_SPECIFIC_DOMAINS`). Before: the digit
    # token was not code-shaped, so the counted set listed both in the ETA row shape.
    # The has/no incoming stamps are not asserted: this file's stubs do not answer the
    # picker probe, so `_human` (which bans "None") is not run on the pick list.
    assert "Which product do you mean? Please choose:" in text, text
    assert "have stock" not in text.lower(), text


@pytest.mark.parametrize("word", ["eta", "arriving", "shipment"])
def test_f1_no_word_that_chose_the_incoming_domain_is_said_back_on_a_class_ask(chat, world, word):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count."""
    message = f"any kitchen sink {word}"
    text = chat.say(message, _ask("incoming", "kitchen sink", message, requested_attributes=["incoming"]))
    assert "did not understand" not in text, text
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): no brand named means every brand.
    assert "Here's what you want: kitchen sinks with incoming stock (2)" in _header(text), text


def test_f1_each_row_of_a_multi_product_eta_answer_has_the_single_product_structure(chat, world):
    single = chat.say("SRTKS65502 eta", _eta_ask("SRTKS65502 eta", "SRTKS65502", ("incoming",)))
    [one] = _full_rows(single)
    expected = _labels(one)
    assert expected[:1] == ["Product Code"] and "Shipment Container" in expected and "Incoming Quantity" in expected, single
    assert "I have attached the file(s) below." in single, single
    # The multi half moved in fix round 13 on PR #833 (code first, owner 28 Sep 2026: "if
    # found by product
    # code -> forward asking"). "65502" matches SRTKS65502 and SRTKS65502-BL by code, so
    # it is a forward ask like "wc286 incoming", and the incoming forward ask over more
    # than one code is the pick list (`gate.REQUIRE_SPECIFIC_DOMAINS`). Before: the digit
    # token was not code-shaped, so the counted set listed both in the ETA row shape.
    multi = chat.say(M1, _v1())
    assert "Which product do you mean? Please choose:" in multi, multi
    assert _codes_listed(multi, world) == {"SRTKS65502", "SRTKS65502-BL"}, multi


# --------------------------------------------------------------------------- #
# F2 (items 2 and 3): a code's descriptor words pick its variants               #
# --------------------------------------------------------------------------- #


def test_f2_a_code_with_s_trap_bowl_lists_that_codes_s_trap_bowl_never_a_category_set(chat, world):
    text = chat.say(M2, _v2())
    assert _codes_listed(text, world) == {"SRTWCX8840-S"}, text
    assert "*List Price:*" in text, text
    assert "have stock" not in text and "*Product type:*" not in text, text
    _human(text)


def test_f2_bowl_only_keeps_the_codes_bowls_and_drops_its_seat_cover_and_lid(chat, world):
    text = chat.say(M3, _v3())
    assert _codes_listed(text, world) == {"SRTWCX8840-P", "SRTWCX8840-S"}, text
    assert "*List Price:*" in text, text


def test_f2_seat_cover_picks_the_seat_cover(chat, world):
    message = "Srtwc8840 seat cover price"
    text = chat.say(message, _code_ask(message, _entity("Srtwc8840", "product"), attrs=("price",)))
    assert _codes_listed(text, world) == {"SRTWC8840-SC"}, text


def test_f2_a_stock_ask_with_a_code_and_a_descriptor_never_opens_a_set(chat, world):
    message = "srtwc8840 s trap bowl stock"
    verdict = _code_ask(
        message,
        _entity("srtwc8840", "product"),
        _entity("s trap bowl", "category"),
        attrs=("stock",),
        intent_hint="check_stock",
        domain_hint="inventory",
    )
    text = chat.say(message, verdict)
    assert _codes_listed(text, world) == {"SRTWCX8840-S"}, text
    assert not re.search(r"\d+ \w+ (?:has|have) stock\.", text), text


# --------------------------------------------------------------------------- #
# F3 (item 4): code first, then the descriptor                                  #
# --------------------------------------------------------------------------- #


def test_f3_srtwc7604_p_trap_resolves_the_x_prefixed_p_trap_variant(chat, world):
    text = chat.say(M4A, _v4a())
    assert _codes_listed(text, world) == {"SRTWCX7604-P-RL-NEW"}, text
    assert "Couldn't find" not in text, text
    assert "*List Price:*" in text, text


def test_f3_srtwc8840_p_trap_resolves_the_p_trap_bowl(chat, world):
    text = chat.say(M4B, _v4b())
    assert _codes_listed(text, world) == {"SRTWCX8840-P"}, text
    assert "Couldn't find" not in text, text


def test_f3_a_descriptor_both_bowls_carry_offers_only_those(chat, world):
    message = "Srtwc8840 trap price"
    text = chat.say(message, _code_ask(message, _entity("Srtwc8840 trap", "product"), attrs=("price",)))
    assert _codes_listed(text, world) == {"SRTWCX8840-P", "SRTWCX8840-S"}, text


def test_f3_did_you_mean_prefers_the_variants_that_match_the_descriptor(world):
    """No variant is both an S trap bowl and a seat cover: the ones that match the most of
    what was said are offered, never the lid."""
    from app.services.product_code_family import pick_variants

    codes, terms = pick_variants(world["db"], "SRTWC8840", ["S", "TRAP", "SEAT", "COVER"])
    assert set(codes) == {"SRTWCX8840-S", "SRTWC8840-SC"}, codes
    assert terms == ["s trap".upper(), "seat cover".upper()], terms


def test_f3_a_word_no_variant_carries_picks_nothing(world):
    from app.services.product_code_family import narrow_code_tokens

    assert narrow_code_tokens(world["db"], query="SRTWC8840-SC price", tokens=["SRTWC8840SC"], allowed_types=["product"]) is None
    assert narrow_code_tokens(world["db"], query="65502 eta", tokens=["65502"], allowed_types=["product"]) is None


# --------------------------------------------------------------------------- #
# F4 (item 5, item 7 tail): the stock header, and no miss for a placed phrase   #
# --------------------------------------------------------------------------- #


def test_f4_a_described_stock_set_keeps_the_normal_header(chat, world):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set (brand, trap and
    product type all still named, inside the phrase) and its count."""
    text = chat.say(M5, _v5())
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): no brand named means every brand: all 9.
    assert _header(text) == [
        "Here's what you want: P trap close coupled water closets with stock (9)",
    ], text
    assert not re.search(r"^\*Type:\*", text, re.MULTILINE), text
    assert "*Brand:*" not in text and "*Product type:*" not in text and "*Trap:*" not in text, text


@pytest.mark.parametrize("message,verdict", [(M5, _v5), (M7B, _v7b)], ids=["close-couple-wc", "wall-mounted-kitchen-tap"])
def test_f4_a_phrase_read_into_the_header_is_never_said_to_be_missing(chat, world, message, verdict):
    text = chat.say(message, verdict())
    assert "could not find" not in text.lower(), text


# --------------------------------------------------------------------------- #
# F5 (item 6): a brand named from the offer narrows the same set                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("reading", ["stock-intent", "brand-only"])
def test_f5_a_brand_from_the_offer_narrows_the_same_set(chat, world, reading):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set."""
    first = chat.say(M5, _v5())
    cabana = _display(world["cabana"].brand_name)
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): the set is every brand and no "Other brands" line
    # is said; a brand named next still narrows the same set.
    assert "Other brands" not in first and "(9)" in first.splitlines()[0], first
    word = _brand_word(world, "cabana")
    verdict = _brand_pick(world, "cabana") if reading == "stock-intent" else _bare(entities=[_entity(word, "brand")])
    text = chat.say(word, verdict)
    assert _header(text) == [
        f"Here's what you want: {cabana} P trap close coupled water closets with stock (3)",
    ], text
    assert _codes_listed(text, world) == {p.product_code for p in world["close_couple"]["cabana"]}, text
    assert "Other brands" not in text, text
    _human(text)


def test_f5_a_brand_the_offer_did_not_name_is_a_new_question(chat, world):
    chat.say(M5, _v5())
    word = _brand_word(world, "sorento")
    text = chat.say(word, _brand_pick(world, "sorento"))
    assert "3 water closets have stock." not in text, text


# --------------------------------------------------------------------------- #
# F6 (item 7): a certificate ask lists certificates, in the attachment shape    #
# --------------------------------------------------------------------------- #


_CERT_LABELS = ["Product Code", "Product Name", "Attachment Type", "File Name", "Certificate Number", "Valid Until", "Validity"]


@pytest.mark.parametrize("message,verdict,codes", [(M7A, _v7a, 4), (M7B, _v7b, 2)], ids=["kitchen-tap", "wall-mounted"])
def test_f6_a_cert_ask_lists_certificates_only_in_the_attachment_structure(chat, world, message, verdict, codes):
    text = chat.say(message, verdict())
    assert "Product Photos" not in text, text
    rows = _full_rows(text)
    assert len(rows) == codes, text
    for row in rows:
        assert _labels(row) == _CERT_LABELS, row
        assert "*Attachment Type:* Certification" in row, row
    assert "I have attached the file(s) below." in text, text
    [call] = [c for c in chat.calls if c["name"] == "crm_master_product_attachments_list"]
    assert call["args"].get("certificate_ids"), call


def test_f6_an_expired_certificate_carries_the_expiry_flag(chat, world):
    from tests.chatbot.test_lane_require import _certificate_for as cert

    tap = world["kitchen_taps"][0]
    cert(world["db"], product_id=tap.id, valid_until=date.today() - timedelta(days=5))
    world["db"].commit()
    text = chat.say(M7A, _v7a())
    assert "*Validity:* Expired" in text and "*(EXPIRED)*" in text, text


# --------------------------------------------------------------------------- #
# F7: plain words only                                                         #
# --------------------------------------------------------------------------- #


def test_f7_a_set_that_qualifies_nothing_says_so_in_plain_words(chat, world):
    message = "any bathtub has cert"
    text = chat.say(message, _ask("cert", "bathtub", message))
    assert text.strip(), text
    _human(text)


def test_f7_the_lane_reasons_never_read_as_internal_phrases():
    """The one internal reason a zero set records is never a reply; the reply comes from
    the miss lane. Pinned so a later change that prints `error` cannot print this."""
    import inspect

    from app.services.chatbot.turn import compose

    assert "qualifies nothing" not in inspect.getsource(compose)


# --------------------------------------------------------------------------- #
# The owner's seven messages, replayed in order                                 #
# --------------------------------------------------------------------------- #


def test_replay_the_owners_seven_messages(chat, world):
    """Items 1 to 7 as one conversation, with the follow-ups "cabana", "50" and "10"."""
    sorento, cabana = _display(world["sorento"].brand_name), _display(world["cabana"].brand_name)
    cabana_rows = {p.product_code for p in world["close_couple"]["cabana"]}
    turns = [
        (M1, _v1()),
        (M2, _v2()),
        (M3, _v3()),
        (M4A, _v4a()),
        (M4B, _v4b()),
        (M5, _v5()),
        (_brand_word(world, "cabana"), _brand_pick(world, "cabana")),
        ("50", _bare(top_n=50)),
        ("10", _bare(top_n=10)),
        (M7A, _v7a()),
        (M7B, _v7b()),
    ]
    replies = [chat.say(text, verdict) for text, verdict in turns]
    for reply in replies[1:]:
        _human(reply)
    one, two, three, four_a, four_b, five, six, fifty, ten, seven_a, seven_b = replies

    # 1: no "did not understand". Moved in fix round 13 on PR #833 (code first): "65502"
    # matches two codes, so it is the forward incoming pick list, as "wc286 incoming" is;
    # it was the counted set in the ETA row shape. See
    # `test_f1_an_eta_ask_never_says_it_did_not_understand_eta`.
    assert "did not understand" not in one and "not recorded yet" not in one, one
    assert "Which product do you mean? Please choose:" in one, one
    assert _codes_listed(one, world) == {"SRTKS65502", "SRTKS65502-BL"}, one
    # 2 and 3: the code's variants, in the price structure.
    assert _codes_listed(two, world) == {"SRTWCX8840-S"} and "*List Price:*" in two, two
    assert _codes_listed(three, world) == {"SRTWCX8840-P", "SRTWCX8840-S"}, three
    # 4: code then descriptor.
    assert _codes_listed(four_a, world) == {"SRTWCX7604-P-RL-NEW"}, four_a
    assert _codes_listed(four_b, world) == {"SRTWCX8840-P"}, four_b
    # 5: the normal stock header; no miss tail. Amended to the round 8 ruling on PR #833
    # (owner retest of round 7, 27 Sep 2026): the header is the product-code answer's own
    # intro naming the described set and its count.
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): no brand named means every
    # brand, and no "Other brands" line.
    assert _header(five) == ["Here's what you want: P trap close coupled water closets with stock (9)"], five
    assert "could not find" not in five.lower(), five
    # 6: the brand narrows the same set, and the counts agree.
    assert "Other brands" not in five, five
    assert _header(six) == [f"Here's what you want: {cabana} P trap close coupled water closets with stock (3)"], six
    assert _codes_listed(six, world) == cabana_rows, six
    # "50" and "10" stay on the same three products.
    for reply in (fifty, ten):
        assert _codes_listed(reply, world) <= cabana_rows, reply
        # Amended to fix round 9 on PR #833: the intro names its leg once.
        assert "1,1" not in reply and reply.startswith("Here's what you want: "), reply
    # 7: certificates only, in the attachment structure; no miss tail.
    for reply in (seven_a, seven_b):
        assert "Product Photos" not in reply, reply
        assert all(_labels(r) == _CERT_LABELS for r in _full_rows(reply)) and _full_rows(reply), reply
    assert "could not find" not in seven_b.lower(), seven_b


def test_f3_words_every_variant_carries_leave_the_code_alone(world):
    """"water closet" is in every 8840 description: it picks nothing, so the code is not
    swapped for its whole family (X siblings included)."""
    from app.services.product_code_family import narrow_code_tokens

    assert narrow_code_tokens(world["db"], query="SRTWC8840 water closet", tokens=["SRTWC8840"], allowed_types=["product"]) is None
