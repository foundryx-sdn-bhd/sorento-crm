"""OI-EMAIL-PRINT: the order inquiry handover email prints on one A4 portrait page with one
line per cell (`documentation/plans/scm/PLAN-oi-email-print-09oct.md`, AC-P1 to AC-P12).

Owner + end user, 9 Oct (SO418869): REMARK carried the internal planning note "Planning keeps
this Buy: ..." and wrapped over a dozen lines, SO DATE / S/O NO repeated on every line, and the
900px card was wider than an A4 portrait page.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa

from tests._pg_fixture import blank_session

from .test_email_layout_migrations import _row, _run, _seed

REVISION = "oihr_0005_print_one_page"
PRIOR = "oihr_0004_wide_line_table"
CODE = "order_inquiry_handover" + "_default"

PLANNING_NOTE = (
    "Planning keeps this Buy: 140 landed for this line on SPO-2026/09-0036 stay linked "
    "to this line; adjust the linkage if the Buy replaces them"
)


def _load(revision: str):
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    paths = list(versions.glob(f"{revision}*.py"))
    assert paths, f"no alembic migration named {revision} under alembic/versions/"
    spec = importlib.util.spec_from_file_location(f"zzt_{revision}", paths[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _remark_row(**kwargs) -> SimpleNamespace:
    base = dict(
        verb=None, qty=None, delivery_date=None, item_code=None,
        cited_document=None, note=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# AC-P1 to AC-P3: REMARK never carries the row's note                          #
# --------------------------------------------------------------------------- #


def test_planning_note_never_reaches_remark():
    """AC-P1: the SO418869 photo."""
    from app.services.project_order_inquiry_service import IV_ORDER, handover_remark

    assert handover_remark("raised", _remark_row(verb=IV_ORDER, note=PLANNING_NOTE), None) == "ORDER"


def test_no_internal_note_reaches_remark():
    """AC-P2: every writer of `row.note` that reaches a raised row stays out of REMARK, while
    the verb's own structured facts still print."""
    from app.services.project_order_inquiry_service import (
        IV_ORDER,
        IV_ORDER_BACK,
        IV_RESERVE_AND_ORDER,
        handover_remark,
    )

    notes = [
        "Replaces 20 used; SPO-2026/08-0012 received 02 Sep 2026 into KL-WH; Was item OLD-1",
        "Order-back: SO401990 line 3 lent 10 to SO402761 line 2",
        "Was 10, now 8",
        "No previous delivery date",
        "Also cited on the form: 202511-S0022",
        "Confirmed by WhatsApp",
    ]
    for note in notes:
        assert handover_remark("raised", _remark_row(verb=IV_ORDER, note=note), None) == "ORDER", note
        assert (
            handover_remark("raised", _remark_row(verb=IV_RESERVE_AND_ORDER, note=note), None)
            == "RESERVE & ORDER"
        ), note
        assert (
            handover_remark(
                "raised",
                _remark_row(verb=IV_ORDER_BACK, cited_document="SPO-2026/09-0102", note=note),
                None,
            )
            == "ORDER BACK SPO-2026/09-0102"
        ), note
    # The structured product change still prints, the note beside it does not.
    assert handover_remark(
        "raised",
        _remark_row(verb=IV_ORDER, item_code="NEW-1", note="Replaces 4 used; Was item OLD-1"),
        {"item_code": "OLD-1"},
    ) == "ORDER, CHANGE ITEM CODE TO NEW-1 (WAS OLD-1)"


def test_change_so_names_the_order_it_moves_to():
    """AC-P3 (Q1 rec b): the target order is the instruction, read off the fixed
    `Moved to <SO>` stamp `_change_note` writes; no stamp, bare verb."""
    from app.services.project_order_inquiry_service import IV_CHANGE_SO, handover_remark

    assert handover_remark(
        "raised", _remark_row(verb=IV_CHANGE_SO, note="Moved to SO418870"), None
    ) == "CHANGE SO NO TO SO418870"
    assert handover_remark(
        "raised",
        _remark_row(verb=IV_CHANGE_SO, note="Linked to 202607-S0031 (x); Moved to SO9"),
        None,
    ) == "CHANGE SO NO TO SO9"
    assert handover_remark("raised", _remark_row(verb=IV_CHANGE_SO), None) == "CHANGE SO NO"
    assert handover_remark(
        "raised", _remark_row(verb=IV_CHANGE_SO, note="Superseded by revision 3"),
        {"so_number": "SO1", "customer": "C", "project": "P"},
    ) == "CHANGE SO NO"


# --------------------------------------------------------------------------- #
# AC-P4: the order table carries the SO date                                   #
# --------------------------------------------------------------------------- #


def _pending(so_number: str, so_date: str, pso: str) -> dict:
    return {
        "pso_id": pso, "so_number": so_number, "customer": "CUSTOMER A", "project": "PROJECT E",
        "stock_location": "JB-WH", "verb_keys": ("ORDER",), "order_inquiry_id": "oi-1",
        "line": {"so_date": so_date, "so_number": so_number, "item_code": "X", "remark": "ORDER"},
        "actor": {"name": "CS", "email": "cs@example.com"},
    }


def test_orders_carry_the_so_date_once():
    from app.services.project_order_inquiry_service import _build_handover_context

    context, _ = _build_handover_context(
        [
            _pending("SO418869", "12/09/2026", "pso-1"),
            _pending("SO418869", "12/09/2026", "pso-1"),
            _pending("SO418870", "13/09/2026", "pso-2"),
        ]
    )
    orders = context["handover"]["orders"]
    assert [(o["so_number"], o["so_date"]) for o in orders] == [
        ("SO418869", "12/09/2026"),
        ("SO418870", "13/09/2026"),
    ], orders


# --------------------------------------------------------------------------- #
# AC-P11: the migration                                                        #
# --------------------------------------------------------------------------- #


def _handover_row(db) -> dict:
    row = _row(db, CODE)
    assert row is not None, "no handover template row"
    layout = row[2]
    if isinstance(layout, str):
        layout = json.loads(layout)
    return {"body_html": row[0], "body_text": row[1], "layout_json": layout}


def _custom_text(layout: dict) -> str:
    return "\n".join(
        b["html"] for b in layout["blocks"] if b["type"] == "custom_text" and b.get("html")
    )


def _seeded_through_0004(db) -> dict:
    mods = _seed(db)
    _run(mods["eml_0002_seed_layouts"], db)
    _run(_load(PRIOR), db)
    return _handover_row(db)


def test_migration_is_the_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    module = _load(REVISION)
    assert module.revision == REVISION and len(REVISION) <= 32
    heads = list(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
    assert heads == [REVISION], heads


def test_upgrade_converts_0004_document_in_place_idempotent_and_reversible():
    module = _load(REVISION)
    with blank_session() as db:
        before = _seeded_through_0004(db)
        assert before["layout_json"]["width"] == "wide", "sanity: oihr_0004 ran"

        _run(module, db)
        after = _handover_row(db)
        layout = after["layout_json"]
        assert layout["width"] == "print"
        assert [b["type"] for b in layout["blocks"]] == [
            b["type"] for b in before["layout_json"]["blocks"]
        ]
        authored = _custom_text(layout)
        assert after["body_html"] == authored, "body_html mirrors the custom text (D3)"
        assert "SO DATE | S/O NO" not in after["body_text"]
        assert "LOCATION | ITEM CODE | QTY |" in after["body_text"]
        assert "order.so_date" in after["body_text"]

        _run(module, db)
        assert _handover_row(db) == after, "idempotent"

        _run(module, db, "downgrade")
        assert _handover_row(db) == before, "downgrade restores oihr_0004 byte for byte"


def test_admin_edited_document_is_left_alone():
    module = _load(REVISION)
    with blank_session() as db:
        before = _seeded_through_0004(db)
        edited = json.loads(json.dumps(before["layout_json"]))
        edited["blocks"][1]["text"] = "Admin headline {{ handover.headline }}"
        db.execute(
            sa.text("UPDATE email_templates SET layout_json = CAST(:l AS jsonb) WHERE code = :c"),
            {"l": json.dumps(edited), "c": CODE},
        )
        _run(module, db)
        row = _handover_row(db)
        assert row["layout_json"] == edited
        assert row["body_text"] == before["body_text"]


def test_upgrade_inserts_when_the_row_is_absent():
    module = _load(REVISION)
    with blank_session() as db:
        _run(module, db)
        row = _handover_row(db)
        assert row["layout_json"]["width"] == "print"
        assert row["body_html"] == _custom_text(row["layout_json"])


# --------------------------------------------------------------------------- #
# AC-P4 to AC-P8, AC-P12: rendered through the real service                    #
# --------------------------------------------------------------------------- #


def _line(so, loc, code, qty, dd, remark, was=None):
    return {
        "so_date": "12/09/2026", "so_number": so, "location": loc, "item_code": code,
        "qty": qty, "delivery_date": dd, "remark": remark, "was": was,
    }


def _ctx(orders, lines):
    return {
        "handover": {
            "subject_scope": orders[0]["so_number"], "headline": "ORDER, DELAY",
            "orders": orders, "lines": lines, "line_count": len(lines),
            "link": "https://crm.test/project-sales/order-inquiries?query=x",
        },
        "actor": {"name": "CS", "email": "cs@example.com"},
        "today": "09/10/2026",
    }


def _render(ctx):
    from app.models.email_template import EmailTemplate
    from app.services.email_template_service import EmailTemplateService

    with blank_session() as db:
        _run(_load(REVISION), db)
        template = db.query(EmailTemplate).filter(EmailTemplate.code == CODE).one()
        return EmailTemplateService(db).render(template, ctx)


def _authored(html: str) -> str:
    from tests._email_body import authored_html

    return authored_html(html)


def _cells(table: str) -> list[list[str]]:
    rows = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
        cells = re.findall(r"<t[dh](?:\s[^>]*)?>(.*?)</t[dh]>", row_html, re.S)
        rows.append([re.sub(r"<[^>]+>", "", c).strip() for c in cells])
    return rows


def test_one_order_renders_print_width_header_so_date_and_no_repeated_columns():
    orders = [{"so_number": "SO418869", "so_date": "12/09/2026", "customer": "CUSTOMER A", "project": "PROJECT E"}]
    lines = [
        _line("SO418869", "JB-WH", "WC-TB2140-WHT", "140", "30/10/2026", "ORDER"),
        _line("SO418869", "JB-WH", "WC-BS3311-CHR", "24", "13/11/2026", "DELAY",
              {"delivery_date": "30/10/2026"}),
    ]
    out = _render(_ctx(orders, lines))
    html = out["body_html"]
    assert 'width="672"' in html and "max-width:672px" in html, "AC-P8: print card"
    assert "@page { size: A4 portrait" in html and "@media print" in html, "AC-P8: print CSS"
    tables = re.findall(r"<table.*?</table>", _authored(html), re.S)
    assert len(tables) == 2
    head, body = _cells(tables[0]), _cells(tables[1])
    assert head == [
        ["S/O NO", "SO DATE", "CUSTOMER", "PROJECT"],
        ["SO418869", "12/09/2026", "CUSTOMER A", "PROJECT E"],
    ], head
    assert body == [
        ["LOCATION", "ITEM CODE", "QTY", "DELIVERY", "NEW DATE", "REMARK"],
        ["JB-WH", "WC-TB2140-WHT", "140", "30/10/2026", "", "ORDER"],
        ["JB-WH", "WC-BS3311-CHR", "24", "30/10/2026", "13/11/2026", "DELAY"],
    ], body
    # AC-P7: nothing wraps but REMARK (the last cell of every row).
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", tables[1], re.S):
        opens = re.findall(r"<t[dh](?:\s[^>]*)?>", row_html)
        for tag in opens[:-1]:
            assert "white-space:nowrap" in tag, tag
    assert "white-space:nowrap" not in re.findall(r"<td[^>]*>", tables[1])[-1]
    for tag in re.findall(r"<th(?:\s[^>]*)?>", tables[1]):
        assert "white-space:nowrap" in tag, tag
    assert "S/O NO SO418869" not in html, "AC-P6: one order, no group row"
    assert out["subject"] == "OI: SO418869"
    text = out["body_text"]
    assert "SO418869 - 12/09/2026 - CUSTOMER A - PROJECT E" in text, text
    assert "LOCATION | ITEM CODE | QTY | DELIVERY | NEW DATE | REMARK" in text, text
    assert "JB-WH | WC-BS3311-CHR | 24 | 30/10/2026 | 13/11/2026 | DELAY" in text, text


def test_two_orders_group_their_lines_under_one_row_each():
    orders = [
        {"so_number": "SO402757", "so_date": "18/08/2026", "customer": "B", "project": "R"},
        {"so_number": "SO402761", "so_date": "19/08/2026", "customer": "C", "project": "T"},
    ]
    lines = [
        _line("SO402757", "KL-WH", "SRTWT6808", "436", "25/09/2026", "ADVANCE, ORDER 64",
              {"qty": "372", "delivery_date": "01/10/2026"}),
        _line("SO402757", "KL-WH", "SRTWT6808", "16", "25/09/2026", "ORDER BACK SPO-2026/08-0061"),
        _line("SO402761", "PG-WH", "SRTBS2201", "8", "09/10/2026", "CANCEL BALANCE 4 NOS", {"qty": "12"}),
    ]
    out = _render(_ctx(orders, lines))
    tables = re.findall(r"<table.*?</table>", _authored(out["body_html"]), re.S)
    body = _cells(tables[1])
    assert body == [
        ["LOCATION", "ITEM CODE", "QTY", "NEW QTY", "DELIVERY", "NEW DATE", "REMARK"],
        ["S/O NO SO402757"],
        ["KL-WH", "SRTWT6808", "372", "436", "01/10/2026", "25/09/2026", "ADVANCE, ORDER 64"],
        ["KL-WH", "SRTWT6808", "16", "", "25/09/2026", "", "ORDER BACK SPO-2026/08-0061"],
        ["S/O NO SO402761"],
        ["PG-WH", "SRTBS2201", "12", "8", "09/10/2026", "", "CANCEL BALANCE 4 NOS"],
    ], body
    assert 'colspan="7"' in tables[1]
    assert "S/O NO SO402757\nKL-WH | SRTWT6808 | 372 | 436 |" in out["body_text"], out["body_text"]


def test_other_widths_unchanged():
    """AC-P12: the standard and wide cards are what they were."""
    from app.services.email_layout import _CARD_WIDTH, _SIDE_GUTTER

    assert (_CARD_WIDTH["standard"], _SIDE_GUTTER["standard"]) == (600, 40)
    assert (_CARD_WIDTH["wide"], _SIDE_GUTTER["wide"]) == (900, 24)
    assert (_CARD_WIDTH["print"], _SIDE_GUTTER["print"]) == (672, 12)
