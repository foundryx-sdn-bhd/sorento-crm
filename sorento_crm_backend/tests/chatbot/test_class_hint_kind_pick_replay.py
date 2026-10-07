"""CLASS-HINT-KIND-PICK console replay table (merge gate item 1, crew 6 Oct 2026).

Runs the nine sentences of the card through the REAL engine and the REAL resolver over a
seeded blank schema and prints one markdown row per sentence: the sentence, the parser
verdict used, the reply and the open question. The parser is NOT here (no LLM in tests),
so each verdict is hand-written as the production parser emits it for that sentence; the
crew's live dry run on the Mac is the parser-side check of the same set.

Opt-in: `CLASS_HINT_REPLAY=1 pytest tests/chatbot/test_class_hint_kind_pick_replay.py -s`.
Run on origin/main (worktree, this file and `test_class_hint_kind_pick.py` copied in) for the
BEFORE column and on the lane branch for the AFTER column. The table is also written to
`CLASS_HINT_REPLAY_OUT` when set.
"""
from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.models.inventory import Stock
from app.models.product import Brand, Product, ProductAttachment, ProductCategory
from app.models.resources import Attachment, AttachmentType
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.product_class_signal import backfill_category_signals
from app.services.product_spec_derivation import derive_for_code
from app.services.product_spec_registry import seed_spec_registry
from tests._pg_fixture import unique_code
from tests.chatbot.test_class_hint_kind_pick import (
    PROMO_DESCRIPTIONS,
    STOCK_TOOL,
    _class_entity,
    _focus_brand_words,
    _said,
    _seed_world,
    _thickness_entity,
    _turn,
)
from tests.chatbot.test_engine import _parser_output, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_outstanding_lane import _seed_contact as _seed_business_contact
from tests.chatbot.test_r3_pending_end_to_end import _session_of

pytestmark = pytest.mark.skipif(
    os.environ.get("CLASS_HINT_REPLAY") != "1",
    reason="console replay table; opt in with CLASS_HINT_REPLAY=1",
)

ATTACHMENT_TOOLS = {"crm_resource_attachments_list", "crm_resource_attachments_catalogue"}
CABANA_CATALOGUE = "@ CABANA CATALOG 2025 (RESEARCHABLE).pdf"
ROGUE_CODE = "SRTWC8318-RL-250"
ORDER_HINTED_CODE = "ZZT7445"
# The owner's product-page screenshot (chatbot.turns 0a405823): the code on the page.
SCREENSHOT_CODE = "SRTWC8383-RL-GY"


def _entity(raw: str, hint: str, **extra: Any) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": extra.pop("canonical_code", None), "current_message": True, "confident": True, **extra}


def _seed_replay_world(session_factory) -> dict[str, Any]:
    """`_seed_world(cabana=True)` (the card's dev shape) plus what the other sentences
    need: Brand Sorento with two Sorento wash basins, a Tap class with a Cabana basin tap
    and a Sorento water tap, the ROGUE product ZZWC8318-RL-250 in stock, a product coded
    ZZT7445 for the AC-1527 control, and the Cabana catalogue PDF (Direct Access type)
    for the r11 control."""
    world = _seed_world(session_factory, cabana=True)
    db = session_factory()
    cabana_id = world["brand_id"]
    sorento = Brand(id=str(uuid.uuid4()), brand_code="SRT", brand_name="Sorento", is_active=True, company_id=DEFAULT_COMPANY_ID)
    tap_cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("TP")[:50], category_name="ZZT Tap",
        class_label="Tap", search_synonyms=["tap", "water tap", "basin tap", "faucet"], company_id=DEFAULT_COMPANY_ID,
    )
    wc_cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("WC")[:50], category_name="ZZT Water Closet",
        class_label="Water Closet", search_synonyms=["water closet", "wc"], company_id=DEFAULT_COMPANY_ID,
    )
    db.add_all([sorento, tap_cat, wc_cat])
    db.flush()
    backfill_category_signals(db)
    seed_spec_registry(db)
    basin_cat_id = world["category_id"]
    uom_id = world["uom_id"]
    wh_id = world["warehouse_id"]
    stock_by_id: dict[str, str] = {pid: code for code, pid in world["cabana_basins"].items()}

    def product(code: str, description: str, *, brand_id: str | None, category_id: str, qty: int | None) -> Product:
        row = Product(
            id=str(uuid.uuid4()), product_code=code, product_name=code, description=description,
            category_id=category_id, base_uom_id=uom_id, brand_id=brand_id,
            list_price=Decimal("1.00"), is_active=True, company_id=DEFAULT_COMPANY_ID,
        )
        db.add(row)
        db.flush()
        derive_for_code(db, code)
        if qty is not None:
            db.add(Stock(id=str(uuid.uuid4()), product_id=row.id, warehouse_id=wh_id, quantity_on_hand=qty, company_id=DEFAULT_COMPANY_ID))
            stock_by_id[row.id] = code
        return row

    product(unique_code("ZZSRTB", alpha=True), "SORENTO WHITE WASH BASIN", brand_id=sorento.id, category_id=basin_cat_id, qty=3)
    product(unique_code("ZZSRTC", alpha=True), "SORENTO GREY WASH BASIN", brand_id=sorento.id, category_id=basin_cat_id, qty=0)
    taps = [
        product(unique_code("ZZCBT", alpha=True), "CABANA CHROME BASIN TAP", brand_id=cabana_id, category_id=tap_cat.id, qty=4),
        product(unique_code("ZZSRTT", alpha=True), "SORENTO BRASS WATER TAP", brand_id=sorento.id, category_id=tap_cat.id, qty=2),
        product(ORDER_HINTED_CODE, "SORENTO BASIN MIXER TAP", brand_id=sorento.id, category_id=tap_cat.id, qty=1),
    ]
    product(ROGUE_CODE, "SORENTO S-TRAP 250MM ONE PIECE WC", brand_id=sorento.id, category_id=wc_cat.id, qty=7)
    # Owner evidence (console dry run, chatbot.turns 0a405823, 6 Oct 23:58 MYT): a product-page
    # SCREENSHOT of this water closet under `product_attachment`; the parser read the brand as
    # {raw Sorento, hint brand, canonical_code Sorento} and main minted the four-way kind pick.
    gold_wc = product(SCREENSHOT_CODE, "SORENTO GOLD WATER CLOSET", brand_id=sorento.id, category_id=wc_cat.id, qty=2)
    # Owner evidence (console on the #1514 copy, prompt v58): "which tap has technical
    # drawing" (a 298 set) then "sorentooooo?". Every tap carries a technical drawing.
    drawing_type = AttachmentType(type_name="Technical Drawing", code="technical_drawing", allowed_extensions="pdf", is_direct_access=False)
    db.add(drawing_type)
    db.flush()
    for tap in taps:
        drawing = Attachment(
            attachment_type_id=drawing_type.id, original_filename=f"{tap.product_code} technical drawing.pdf",
            stored_filename=f"zzt-td-{tap.product_code}.pdf", file_path=f"https://example.test/zzt-td-{tap.product_code}.pdf",
            company_id=DEFAULT_COMPANY_ID,
        )
        db.add(drawing)
        db.flush()
        db.add(ProductAttachment(id=str(uuid.uuid4()), product_id=tap.id, attachment_id=drawing.id, company_id=DEFAULT_COMPANY_ID))

    direct_access = AttachmentType(type_name="Direct Access", code=None, allowed_extensions="pdf", is_direct_access=True)
    db.add(direct_access)
    db.flush()
    catalogue = Attachment(
        attachment_type_id=direct_access.id, original_filename=CABANA_CATALOGUE,
        stored_filename=f"zzt-{CABANA_CATALOGUE}", file_path=f"https://example.test/zzt-{CABANA_CATALOGUE}",
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(catalogue)
    db.commit()
    world["stock_by_id"] = stock_by_id
    world["attachments"] = {catalogue.id: CABANA_CATALOGUE}
    image_type = AttachmentType(type_name="Product Image", code="product_image", allowed_extensions="jpg", is_direct_access=False)
    db.add(image_type)
    db.flush()
    image = Attachment(
        attachment_type_id=image_type.id, original_filename=f"{SCREENSHOT_CODE} product image.jpg",
        stored_filename=f"zzt-img-{SCREENSHOT_CODE}.jpg", file_path=f"https://example.test/zzt-img-{SCREENSHOT_CODE}.jpg",
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(image)
    db.flush()
    db.add(ProductAttachment(id=str(uuid.uuid4()), product_id=gold_wc.id, attachment_id=image.id, company_id=DEFAULT_COMPANY_ID))
    db.commit()
    # `{product id: (code, filename)}` for the product attachments tool stub: the taps' technical
    # drawings and the gold water closet's product image.
    world["drawings"] = {tap.id: (tap.product_code, f"{tap.product_code} technical drawing.pdf") for tap in taps}
    world["drawings"][gold_wc.id] = (SCREENSHOT_CODE, f"{SCREENSHOT_CODE} product image.jpg")
    return world


def _tool_stub(monkeypatch, world: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The MCP boundary, answering from the ids each call carries."""
    from app.services.ai_assistant_service import MCPRuntimeClient

    calls: list[tuple[str, dict[str, Any]]] = []
    stock_by_id = world["stock_by_id"]
    attachments = world["attachments"]
    drawings = world.get("drawings") or {}

    def fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        calls.append((name, dict(arguments)))
        if name == STOCK_TOOL:
            rows = [
                {"flags": {}, "title": stock_by_id[pid], "fields": [
                    {"key": "product_code", "label": "Product Code", "value": stock_by_id[pid]},
                    {"key": "total_on_hand", "label": "Total", "value": 5},
                ]}
                for pid in (arguments.get("product_ids") or []) if pid in stock_by_id
            ]
            return json.dumps({"items": rows, "has_result": bool(rows), "attachments": [], "action_links": [],
                               "result_type": "stock_compact",
                               "intro": "Stock summary for the requested products." if rows else "No matching results found."})
        if name == "crm_master_product_attachments_list":
            ids = [pid for pid in (arguments.get("product_ids") or []) if pid in drawings]
            items = [{"title": drawings[pid][0], "flags": {}, "fields": [
                {"key": "product_code", "label": "Product Code", "value": drawings[pid][0]},
                {"key": "original_filename", "label": "File Name", "value": drawings[pid][1]},
            ]} for pid in ids]
            return json.dumps({"result_type": "product_attachments", "intro": "Here are the attachments." if items else "No matching results found.",
                               "items": items, "has_result": bool(items), "attachments": [], "action_links": []})
        if name == "crm_master_products_list":
            ids = list(arguments.get("product_ids") or [])
            items = [{"title": f"product {pid}", "flags": {}, "fields": [{"key": "product_code", "label": "Product Code", "value": f"product {pid}"}]} for pid in ids]
            return json.dumps({"result_type": "products", "intro": "Here are the matching products." if items else "No matching results found.",
                               "items": items, "has_result": bool(items), "attachments": [], "action_links": []})
        if name in ATTACHMENT_TOOLS:
            wanted = [a for a in (arguments.get("attachment_ids") or []) if a in attachments]
            files = [{"id": a, "original_filename": attachments[a], "company_name": "Sorento",
                      "url": f"https://cdn.example.test/{a}.pdf", "content_type": "application/pdf"} for a in wanted]
            return json.dumps({"result_type": "resource_attachments", "intro": "I have attached the file(s) below." if files else "No matching results found.",
                               "items": [{"fields": [{"key": "original_filename", "label": "File Name", "value": attachments[a]}]} for a in wanted],
                               "attachments": files, "has_result": bool(files)})
        return json.dumps({"items": [], "has_result": False})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)
    return calls


SENTENCES: list[tuple[str, dict[str, Any]] | list[tuple[str, dict[str, Any]]]] = [
    ("any basin with thickness 1.0mm", dict(intent_hint="check_product", domain_hint="master_products", match_mode="and",
                                            entities=[_class_entity(), _thickness_entity()])),
    ("basin", dict(intent_hint="check_product", domain_hint="master_products", match_mode="and", entities=[_class_entity()])),
    ("cabana stock", dict(intent_hint="check_stock", domain_hint="inventory", match_mode="and", entities=[_entity("cabana", "product")])),
    ("cabana basin tap", dict(intent_hint="check_product", domain_hint="master_products", match_mode="and",
                              entities=[_entity("cabana", "brand", canonical_code="Cabana"), _entity("basin tap", "category")])),
    ("SORENTO SRTWC8318", dict(intent_hint="check_stock", domain_hint="inventory", match_mode="and",
                               entities=[_entity("SORENTO", "brand", canonical_code="Sorento"), _entity("SRTWC8318", "product")])),
    ("water tap", dict(intent_hint="check_product", domain_hint="master_products", match_mode="and", entities=[_entity("water tap", "category")])),
    ("sorento basin", dict(intent_hint="check_product", domain_hint="master_products", match_mode="and",
                           entities=[_entity("sorento", "brand", canonical_code="Sorento"), _entity("basin", "category")])),
    # Owner clarification: brand identity is the parser's canonical_code (the Known brands
    # name), never the raw word. A misspelling and a staff shorthand the parser mapped.
    ("Sorrento basin", dict(intent_hint="check_product", domain_hint="master_products", match_mode="and",
                            entities=[_entity("Sorrento", "brand", canonical_code="Sorento"), _entity("basin", "category")])),
    ("srt basin", dict(intent_hint="check_product", domain_hint="master_products", match_mode="and",
                       entities=[_entity("srt", "brand", canonical_code="Sorento"), _entity("basin", "category")])),
    ("SRT SRTWC8318", dict(intent_hint="check_stock", domain_hint="inventory", match_mode="and",
                           entities=[_entity("SRT", "brand", canonical_code="Sorento"), _entity("SRTWC8318", "product")])),
    ("cabana catalog", dict(intent_hint="get_resource_attachment", domain_hint="resource_attachment", domain_in_message=True, match_mode="and",
                            entities=[_entity("cabana", "brand", canonical_code="Cabana"), _entity("catalog", "attachment", canonical_code="catalogue")],
                            routing={"suggested_team": "purchasing", "suggested_agent": "general_enquiries", "team_source": None})),
    (f"{ORDER_HINTED_CODE} stock", dict(intent_hint="check_stock", domain_hint="inventory", match_mode="and",
                                       entities=[_entity(ORDER_HINTED_CODE, "order")])),
    # Owner evidence (console on the #1514 copy, prompt v58, contact Mr Loo): after "which tap
    # has technical drawing" (298 set) the owner typed "sorentooooo?"; the parser mapped it to
    # brand Sorento, the kind pick fired (customer / promotion / form / transporter) and ~200
    # codes were dumped above the question (the dump is ROGUE class C, #1506). A two-turn case:
    # the table row is the SECOND turn.
    [
        ("which tap has technical drawing", dict(
            intent_hint="check_product_attachment", domain_hint="product_attachment", match_mode="and",
            entities=[_entity("tap", "category"), _entity("technical drawing", "attachment_type", canonical_code="technical drawing")],
        )),
        ("sorentooooo?", dict(
            intent_hint="check_product_attachment", domain_hint="product_attachment", match_mode="and",
            entity_op="replace_combine", entities=[_entity("sorentooooo", "brand", canonical_code="Sorento")],
        )),
    ],
    # Owner evidence (console dry run, chatbot.turns 0a405823, 6 Oct 23:58 MYT): a product-page
    # SCREENSHOT of SORENTO GOLD WATER CLOSET SRTWC8383-RL-GY under `product_attachment`. The
    # parser (v58) read {raw Sorento, hint brand, confident, canonical_code Sorento}; on main the
    # resolver had no brand kind, the all-types rerun re-typed the word by name substring and
    # reconcile minted "Which one do you mean? Sorento (customer / promotion / form /
    # transporter)". The verdict below is the text the image read yields, hand-written.
    (f"[screenshot] SORENTO GOLD WATER CLOSET {SCREENSHOT_CODE}", dict(
        intent_hint="check_product_attachment", domain_hint="product_attachment", match_mode="and",
        entities=[_entity("Sorento", "brand", canonical_code="Sorento"), _entity(SCREENSHOT_CODE, "product"),
                  _entity("product image", "attachment_type", canonical_code="product image")],
    )),
]


def _reset_session(session_factory) -> None:
    from sqlalchemy import text as sql_text

    db = session_factory()
    db.execute(sql_text("UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb)"), {"sv": json.dumps({"variables": {}})})
    db.commit()


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " / ")


def test_replay_table(session_factory, stub_parser, stub_access, monkeypatch) -> None:
    _seed_business_contact(session_factory, variables={})
    world = _seed_replay_world(session_factory)
    calls = _tool_stub(monkeypatch, world)
    stub_access()
    rows: list[str] = ["| # | sentence | parser verdict (hand-written) | reply | open question | focus brands | tool calls |",
                       "|---|---|---|---|---|---|---|"]
    for i, case in enumerate(SENTENCES, start=1):
        turns = case if isinstance(case, list) else [case]
        prior = turns[:-1]
        text, verdict = turns[-1]
        for j, (ptext, pverdict) in enumerate(prior):
            _turn(session_factory, stub_parser, _parser_output(**pverdict), text=ptext, msg_id=f"ZZT-replay-{i}-{j}")
        calls.clear()
        result = _turn(session_factory, stub_parser, _parser_output(**verdict), text=text, msg_id=f"ZZT-replay-{i}")
        if prior:
            text = " -> ".join([p[0] for p in prior] + [text])
        if result.status == "done":
            # The reply text alone: `actions` repeats it as the sent message.
            said = (result.reply or {}).get("text") or _said(result)
        else:
            said = f"STATUS {result.status}: {result.error}"
        open_q = (_session_of(session_factory).get("open_question") or {}).get("kind")
        brands = ",".join(_focus_brand_words(session_factory))
        ents = "; ".join(f"{e['raw']} ({e['hint']}" + (f", canonical {e['canonical_code']}" if e.get("canonical_code") else "") + ")" for e in verdict["entities"])
        tools = "; ".join(
            f"{name}({', '.join(f'{k}={len(v) if isinstance(v, list) else v}' for k, v in args.items() if k in ('product_ids', 'attachment_ids', 'brand_ids', 'product_codes'))})"
            for name, args in calls
        )
        rows.append(
            f"| {i} | {_cell(text)} | {verdict['domain_hint']} / {verdict['intent_hint']}: {_cell(ents)} | "
            f"{_cell(said)} | {open_q or '-'} | {brands or '-'} | {_cell(tools) or '-'} |"
        )
        _reset_session(session_factory)
    table = "\n".join(rows)
    print("\n" + table + "\n")
    out = os.environ.get("CLASS_HINT_REPLAY_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(table + "\n")
