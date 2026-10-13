"""PV2-CORE: the INDEX and publish_v2 (CONTRACT-PV2-13oct.md section 2)."""
from __future__ import annotations

import hashlib
import re
import sys
import types
import uuid

import pytest
from sqlalchemy import text

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot.pv2 import index as pv2_index
from app.services.chatbot.pv2.schema import validate
from app.services.product_spec_registry import seed_spec_registry
from tests.chatbot.pv2.conftest import minimal_ask, minimal_output

SECTIONS = [
    "## DOMAINS", "## ENTITY KINDS", "## BRANDS", "## ATTACHMENT TYPES (key = name | aka)",
    "## PRODUCT CLASSES", "## SPEC ATTRIBUTES", "## REPORTS", "## TEAMS",
]


def _company(db) -> str:
    cid = str(uuid.uuid4())
    db.execute(
        text("insert into companies (id, code, name) values (:id, :code, :name)"),
        {"id": cid, "code": f"C{cid[:6]}", "name": f"Co {cid[:6]}"},
    )
    return cid


def _brand(db, cid, code, name, active=True):
    db.execute(
        text(
            "insert into brands (id, company_id, brand_code, brand_name, is_active) "
            "values (:id, :c, :code, :name, :a)"
        ),
        {"id": str(uuid.uuid4()), "c": cid, "code": code, "name": name, "a": active},
    )


def _att_type(db, name, code=None, synonyms=()):
    import json

    db.execute(
        text(
            "insert into attachment_types (id, code, type_name, allowed_extensions, max_file_size_mb, search_synonyms) "
            "values (:id, :code, :name, 'pdf', 10, cast(:syn as jsonb))"
        ),
        {"id": str(uuid.uuid4()), "code": code, "name": name, "syn": json.dumps(list(synonyms))},
    )


def _category(db, cid, code, class_label, synonyms=()):
    import json

    db.execute(
        text(
            "insert into product_categories (id, company_id, category_code, category_name, class_label, search_synonyms) "
            "values (:id, :c, :code, :code, :cl, cast(:syn as jsonb))"
        ),
        {"id": str(uuid.uuid4()), "c": cid, "code": code, "cl": class_label, "syn": json.dumps(list(synonyms))},
    )


def _product_spec(db, cid, cls, ptype):
    import json

    pid = str(uuid.uuid4())
    cat = db.execute(text("select id from product_categories where company_id = :c limit 1"), {"c": cid}).scalar()
    uom = db.execute(text("select id from units_of_measure limit 1")).scalar()
    if uom is None:
        uom = str(uuid.uuid4())
        db.execute(
            text("insert into units_of_measure (id, uom_code, uom_name) values (:id, 'ZZTU', 'Unit')"),
            {"id": uom},
        )
    db.execute(
        text(
            "insert into products (id, company_id, product_code, product_name, category_id, base_uom_id, list_price) "
            "values (:id, :c, :code, :code, :cat, :uom, 0)"
        ),
        {"id": pid, "c": cid, "code": f"ZZT{pid[:8]}", "cat": cat, "uom": uom},
    )
    db.execute(
        text("insert into product_specifications (id, product_id, values) values (:id, :p, cast(:v as jsonb))"),
        {
            "id": str(uuid.uuid4()), "p": pid,
            "v": json.dumps({"class": {"value": cls}, "product_type": {"value": ptype}}),
        },
    )


@pytest.fixture()
def db(session_factory):
    s = session_factory()
    seed_spec_registry(s)
    cid = _company(s)
    cid2 = _company(s)
    _brand(s, cid, "SORENTO", "Sorento")
    _brand(s, cid2, "SORENTO", "Sorento")  # same brand in a second company: listed once
    _brand(s, cid, "MOCHA", "Mocha")
    _brand(s, cid, "OLDCO", "Old Co", active=False)
    _att_type(s, "Technical Specifications", "technical_specifications", ["TD", "CAD", "drawing", "图纸"])
    _att_type(s, "Product Photos", "product_photos", ["photo", "gambar"])
    _att_type(s, "Installation Guide", None, ["manual"])
    _category(s, cid, "SRT-WC", "Water Closet", ["toilet", "马桶"])
    _category(s, cid, "SRT-KS", "Kitchen Sink", ["sinki"])
    _product_spec(s, cid, "Water Closet", "close_coupled")
    _product_spec(s, cid, "Water Closet", "close_coupled")
    _product_spec(s, cid, "Kitchen Sink", "close_coupled")  # minority: not its primary class
    _product_spec(s, cid, "Kitchen Sink", "undermount_sink")
    s.flush()
    return s


def test_sections_in_fixed_order_no_ids_no_dates_no_counts(db):
    body, sha = pv2_index.render_index(db)
    heads = [line for line in body.splitlines() if line.startswith("## ")]
    assert heads == SECTIONS
    assert sha == hashlib.sha1(body.encode("utf-8")).hexdigest()
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", body)
    assert not re.search(r"\d{4}-\d{2}-\d{2}", body)
    assert not re.search(r"\(\d+\)|\b\d+ (rows|products|items)\b", body)


def test_render_is_deterministic(db):
    assert pv2_index.render_index(db) == pv2_index.render_index(db)


def test_domains_supported_only(db):
    body, _ = pv2_index.render_index(db)
    keys = pv2_index.parse_index_keys(body)
    assert "inventory" in keys.domains and "sales" in keys.domains
    assert "goods_receive" not in keys.domains  # supported = false in the seed policy


def test_brands_active_deduped(db):
    body, _ = pv2_index.render_index(db)
    section = pv2_index.section_lines(body, "## BRANDS")
    assert section == ["MOCHA = Mocha", "SORENTO = Sorento"]


def test_attachment_types_key_and_aka(db):
    body, _ = pv2_index.render_index(db)
    lines = pv2_index.section_lines(body, "## ATTACHMENT TYPES (key = name | aka)")
    assert "technical_specifications = Technical Specifications | aka TD, CAD, drawing, 图纸" in lines
    assert "installation_guide = Installation Guide | aka manual" in lines
    keys = pv2_index.parse_index_keys(body)
    assert {"technical_specifications", "product_photos", "installation_guide"} <= keys.attachment_types


def test_product_type_listed_only_under_primary_class(db):
    body, _ = pv2_index.render_index(db)
    lines = pv2_index.section_lines(body, "## PRODUCT CLASSES")
    wc = next(line for line in lines if line.startswith("water_closet = Water Closet"))
    ks = next(line for line in lines if line.startswith("kitchen_sink = Kitchen Sink"))
    assert "close_coupled" in wc and "close_coupled" not in ks
    assert "undermount_sink" in ks
    assert "toilet" in wc and "马桶" in wc
    keys = pv2_index.parse_index_keys(body)
    assert {"water_closet", "kitchen_sink"} <= keys.entity_keys["product_class"]
    assert "close_coupled" in keys.entity_keys["product_type"]
    # a seeded type with no product on file still gets a key (listed without a class)
    assert "angle_valve" in keys.entity_keys["product_type"]


def test_spec_attributes(db):
    body, _ = pv2_index.render_index(db)
    lines = pv2_index.section_lines(body, "## SPEC ATTRIBUTES")
    assert any(line.startswith("dim_length = Length") and "mm" in line for line in lines)
    trap = next(line for line in lines if line.startswith("trap_type = "))
    assert "p_trap" in trap and "s_trap" in trap
    keys = pv2_index.parse_index_keys(body)
    assert "class" not in keys.spec_attributes and "product_type" not in keys.spec_attributes
    assert keys.spec_values["trap_type"] == frozenset({"s_trap", "p_trap"})


def test_index_keys_feed_validate(db):
    body, _ = pv2_index.render_index(db)
    keys = pv2_index.parse_index_keys(body)
    raw = minimal_output(minimal_ask(
        domain="product_attachment", attachment_type="technical_specifications",
        entities=[{"kind": "brand", "key": "SORENTO", "phrase": None, "op": "set", "refers_to_held": False},
                  {"kind": "brand", "key": "OLDCO", "phrase": "oldco", "op": "add", "refers_to_held": False}],
    ))
    out, dropped = validate(raw, keys)
    assert [e.key for e in out.asks[0].entities] == ["SORENTO", None]
    assert [d.value for d in dropped] == ["OLDCO"]


def test_teams_listed(db):
    body, _ = pv2_index.render_index(db)
    assert "warehouse" in pv2_index.parse_index_keys(body).teams


def test_index_under_cap_with_seed_registry(db):
    body, _ = pv2_index.render_index(db)
    assert len(body) <= pv2_index.INDEX_CAP_CHARS


def test_alias_clash_omitted_and_reported(db):
    _att_type(db, "Product Drawing", "product_drawing", ["drawing"])
    db.flush()
    body, _ = pv2_index.render_index(db)
    lines = pv2_index.section_lines(body, "## ATTACHMENT TYPES (key = name | aka)")
    assert not any("drawing" in line.split("|", 1)[-1] for line in lines if "| aka" in line)
    clashes = pv2_index.alias_clashes(db)
    assert ("attachment_type", "drawing", ("product_drawing", "technical_specifications")) in clashes


def test_alias_precedence_prints_only_under_winner(db):
    _att_type(db, "Product Drawing", "product_drawing", ["drawing"])
    db.execute(text(
        "insert into chatbot_alias_precedence (id, kind, alias_norm, winner_key) "
        "values (:id, 'attachment_type', 'drawing', 'technical_specifications')"
    ), {"id": str(uuid.uuid4())})
    db.flush()
    body, _ = pv2_index.render_index(db)
    lines = pv2_index.section_lines(body, "## ATTACHMENT TYPES (key = name | aka)")
    td = next(line for line in lines if line.startswith("technical_specifications"))
    pd = next(line for line in lines if line.startswith("product_drawing"))
    assert "drawing" in td.split("| aka", 1)[1] and "| aka" not in pd
    assert pv2_index.alias_clashes(db) == []


def test_reports_section_from_report_measures(db, monkeypatch):
    fake = types.ModuleType("app.services.chatbot.pv2.report_measures")
    fake.index_lines = lambda: [
        "dataset sales = Sales", "measure amount = Sales amount", "measure qty = Quantity",
        "dimension customer = Customer", "basis delivered = Delivered",
    ]
    monkeypatch.setitem(sys.modules, "app.services.chatbot.pv2.report_measures", fake)
    body, _ = pv2_index.render_index(db)
    assert pv2_index.section_lines(body, "## REPORTS")[0] == "dataset sales = Sales"
    keys = pv2_index.parse_index_keys(body)
    assert keys.report_datasets == {"sales"} and keys.report_measures == {"amount", "qty"}
    assert keys.report_dimensions == {"customer"} and keys.report_bases == {"delivered"}


# --------------------------------------------------------------------------- publish_v2


def test_publish_v2_writes_immutable_version_and_staging_label(db):
    vid = pv2_index.publish_v2(db, "HEAD TEXT")
    row = db.get(AIPromptVersion, vid)
    body, sha = pv2_index.render_index(db)
    assert row.name == "chatbot_semantic_parser_v2"
    assert row.template == "HEAD TEXT\n\n" + body
    assert row.variables == []
    assert row.config_json["index_sha"] == sha
    labels = {
        r.label for r in db.query(AIPromptLabel).filter(AIPromptLabel.name == "chatbot_semantic_parser_v2")
    }
    assert labels == {"staging"}
    vid2 = pv2_index.publish_v2(db, "HEAD TEXT 2")
    assert db.get(AIPromptVersion, vid2).version == row.version + 1


def test_publish_v2_refuses_variables_oversize_and_clashes(db):
    with pytest.raises(pv2_index.PublishRefused):
        pv2_index.publish_v2(db, "today is {{current_date}}")
    with pytest.raises(pv2_index.PublishRefused):
        pv2_index.publish_v2(db, "x" * (pv2_index.HEAD_CAP_CHARS + 1))
    _att_type(db, "Product Drawing", "product_drawing", ["drawing"])
    db.flush()
    with pytest.raises(pv2_index.PublishRefused, match="drawing"):
        pv2_index.publish_v2(db, "HEAD")


def test_pinned_keys_parse_back_from_published_template(db):
    vid = pv2_index.publish_v2(db, "HEAD\n## not a section\nmore head")
    row = db.get(AIPromptVersion, vid)
    keys = pv2_index.parse_index_keys(row.template)
    body, _ = pv2_index.render_index(db)
    assert keys == pv2_index.parse_index_keys(body)
    assert pv2_index.index_of(row.template) == body
