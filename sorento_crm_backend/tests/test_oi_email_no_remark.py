"""EMAIL-SO-CHANGES: the order inquiry handover email without the REMARK column
(`documentation/plans/scm/PLAN-email-so-line-changes-12oct.md`, owner rules 12 Oct 2026).

Owner: "nice and sweet". REMARK is removed entirely; QTY / NEW QTY and DELIVERY / NEW DATE say
what moved, the headline lists the actions (owner Q1 = numbers + headline only, no per-line
label). A product change is a column pair like the other two: ITEM CODE (before) and NEW ITEM
CODE (after), shown only when some line in the email changed its product (owner, 12 Oct).
Attachments print as one line under the table (Q2).
"""
from __future__ import annotations

import json
import re

import sqlalchemy as sa

from tests._pg_fixture import blank_session

from .test_email_layout_migrations import _run, _seed
from .test_oi_email_print import (
    CODE,
    _authored,
    _cells,
    _ctx,
    _custom_text,
    _handover_row,
    _line,
    _line_header_widths,
    _load,
)

REVISION = "oihr_0006_no_remark_column"
PRIOR = "oihr_0005_print_one_page"


def _seeded_through_0005(db) -> dict:
    mods = _seed(db)
    _run(mods["eml_0002_seed_layouts"], db)
    _run(_load("oihr_0004_wide_line_table"), db)
    _run(_load(PRIOR), db)
    return _handover_row(db)


def _render(ctx):
    from app.models.email_template import EmailTemplate
    from app.services.email_template_service import EmailTemplateService

    with blank_session() as db:
        _run(_load(REVISION), db)
        template = db.query(EmailTemplate).filter(EmailTemplate.code == CODE).one()
        return EmailTemplateService(db).render(template, ctx)


def _line_table(out) -> list[list[str]]:
    tables = re.findall(r"<table.*?</table>", _authored(out["body_html"]), re.S)
    assert len(tables) == 2, tables
    return _cells(tables[1])


ORDER = [{"so_number": "SO400236", "so_date": "18/09/2026", "customer": "UNIBASE", "project": "FARMFIELD / JB"}]


# --------------------------------------------------------------------------- #
# Migration                                                                    #
# --------------------------------------------------------------------------- #


def test_migration_is_the_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    module = _load(REVISION)
    assert module.revision == REVISION and len(REVISION) <= 32
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = list(script.get_heads())
    assert len(heads) == 1, heads
    assert REVISION in {r.revision for r in script.walk_revisions(base="base", head=heads[0])}


def test_upgrade_converts_0005_document_in_place_idempotent_and_reversible():
    module = _load(REVISION)
    with blank_session() as db:
        before = _seeded_through_0005(db)
        assert "REMARK" in before["body_html"], "sanity: oihr_0005 ran"

        _run(module, db)
        after = _handover_row(db)
        assert after["layout_json"] == module._LAYOUT
        assert after["layout_json"]["width"] == "print"
        assert after["body_html"] == _custom_text(after["layout_json"]), "D3 mirror"
        assert "REMARK" not in after["body_html"]
        assert "REMARK" not in after["body_text"]
        assert "line.remark" not in after["body_html"] + after["body_text"]

        _run(module, db)
        assert _handover_row(db) == after, "idempotent"

        _run(module, db, "downgrade")
        assert _handover_row(db) == before, "downgrade restores oihr_0005 byte for byte"


def test_admin_edited_document_is_left_alone():
    module = _load(REVISION)
    with blank_session() as db:
        before = _seeded_through_0005(db)
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
        assert row["layout_json"] == module._LAYOUT
        assert row["body_html"] == _custom_text(row["layout_json"])


# --------------------------------------------------------------------------- #
# Rendered through the real service                                            #
# --------------------------------------------------------------------------- #


def test_srtwb246_reads_was_now_with_no_remark_column():
    """The owner's SO400236 shape after the fix: both lines print their date move."""
    lines = [
        _line("SO400236", "BRW-BB", "CWB242", "2", "02/11/2026", "DELAY",
              {"delivery_date": "01/10/2026"}),
        _line("SO400236", "BRW-BB", "SRTWB246", "1", "02/11/2026", "ORDER",
              {"delivery_date": "01/10/2026"}),
    ]
    out = _render(_ctx(ORDER, lines))
    assert _line_table(out) == [
        ["LOCATION", "ITEM CODE", "QTY", "DELIVERY", "NEW DATE"],
        ["BRW-BB", "CWB242", "2", "01/10/2026", "02/11/2026"],
        ["BRW-BB", "SRTWB246", "1", "01/10/2026", "02/11/2026"],
    ]
    assert "DELAY" not in _authored(out["body_html"]), "no per-line label anywhere (Q1)"
    text = out["body_text"]
    assert "LOCATION | ITEM CODE | QTY | DELIVERY | NEW DATE\n" in text, text
    assert "BRW-BB | SRTWB246 | 1 | 01/10/2026 | 02/11/2026\n" in text, text
    assert "REMARK" not in text and "ORDER\n" not in text.split("\n", 1)[1], text


def test_cancel_reads_new_qty_zero_and_a_new_line_has_no_new_cells():
    lines = [
        _line("SO400236", "BRW-BB", "SRTWT6808", "0", "20/10/2026", "CANCEL ALL", {"qty": "280"}),
        _line("SO400236", "BRW-BB", "CWB242", "3", "02/11/2026", "ORDER"),
    ]
    assert _line_table(_render(_ctx(ORDER, lines))) == [
        ["LOCATION", "ITEM CODE", "QTY", "NEW QTY", "DELIVERY"],
        ["BRW-BB", "SRTWT6808", "280", "0", "20/10/2026"],
        ["BRW-BB", "CWB242", "3", "", "02/11/2026"],
    ]


def test_qty_and_date_both_move_on_one_line():
    lines = [
        _line("SO400236", "KL-WH", "SRTWT6808", "436", "15/10/2026", "ADVANCE, ORDER 264",
              {"qty": "172", "delivery_date": "30/10/2026"}),
    ]
    assert _line_table(_render(_ctx(ORDER, lines))) == [
        ["LOCATION", "ITEM CODE", "QTY", "NEW QTY", "DELIVERY", "NEW DATE"],
        ["KL-WH", "SRTWT6808", "172", "436", "30/10/2026", "15/10/2026"],
    ]


def test_product_change_prints_item_code_and_new_item_code():
    lines = [
        _line("SO400236", "KL-WH", "SRT382-6-DIY", "12", "06/11/2026", "ORDER, NEW ITEM CODE",
              {"item_code": "SRT382-6"}),
        _line("SO400236", "KL-WH", "CWB242", "3", "06/11/2026", "ORDER"),
    ]
    out = _render(_ctx(ORDER, lines))
    assert _line_table(out) == [
        ["LOCATION", "ITEM CODE", "NEW ITEM CODE", "QTY", "DELIVERY"],
        ["KL-WH", "SRT382-6", "SRT382-6-DIY", "12", "06/11/2026"],
        ["KL-WH", "CWB242", "", "3", "06/11/2026"],
    ]
    assert "(was" not in out["body_html"]
    text = out["body_text"]
    assert "LOCATION | ITEM CODE | NEW ITEM CODE | QTY | DELIVERY\n" in text, text
    assert "KL-WH | SRT382-6 | SRT382-6-DIY | 12 | 06/11/2026\n" in text, text
    assert "KL-WH | CWB242 |  | 3 | 06/11/2026\n" in text, text


def test_no_product_change_no_new_item_code_column():
    out = _render(_ctx(ORDER, [_line("SO400236", "BRW-BB", "CWB242", "3", "02/11/2026", "ORDER")]))
    assert "NEW ITEM CODE" not in out["body_html"] and "NEW ITEM CODE" not in out["body_text"]


def test_attachments_print_as_one_line_under_the_table():
    attached = dict(_line("SO400236", "BRW-BB", "CWB242", "3", "02/11/2026", "ORDER"))
    attached["attachments"] = [
        {"name": "drawing.pdf", "attached": True, "url": "https://crm.test/a"},
        {"name": "big.dwg", "attached": False, "url": "https://crm.test/b"},
    ]
    out = _render(_ctx(ORDER, [attached]))
    html = _authored(out["body_html"])
    tail = html.rsplit("</table>", 1)[1]
    assert "Attachments: CWB242: drawing.pdf, " in tail, tail
    assert re.search(
        r'<a href="https://crm\.test/b"[^>]*>big\.dwg</a> \(not attached, too large for email\)',
        tail,
    ), tail
    assert "Attachments" not in html.rsplit("</table>", 1)[0], "never inside a cell"
    assert "Attachments: CWB242: drawing.pdf, big.dwg (https://crm.test/b)" in out["body_text"]


def test_no_attachments_no_attachments_line():
    out = _render(_ctx(ORDER, [_line("SO400236", "BRW-BB", "CWB242", "3", "02/11/2026", "ORDER")]))
    assert "Attachments" not in out["body_html"] and "Attachments" not in out["body_text"]


def test_two_orders_keep_their_group_rows():
    orders = ORDER + [{"so_number": "SO402761", "so_date": "19/08/2026", "customer": "C", "project": "T"}]
    lines = [
        _line("SO400236", "BRW-BB", "CWB242", "3", "02/11/2026", "ORDER"),
        _line("SO402761", "PG-WH", "SRTBS2201", "8", "09/10/2026", "CANCEL 4", {"qty": "12"}),
    ]
    out = _render(_ctx(orders, lines))
    assert _line_table(out) == [
        ["LOCATION", "ITEM CODE", "QTY", "NEW QTY", "DELIVERY"],
        ["S/O NO SO400236"],
        ["BRW-BB", "CWB242", "3", "", "02/11/2026"],
        ["S/O NO SO402761"],
        ["PG-WH", "SRTBS2201", "12", "8", "09/10/2026"],
    ]
    assert 'colspan="5"' in out["body_html"]


def test_line_table_columns_share_the_width_in_every_variant():
    """All eight column variants (each NEW column optional): integer shares summing to 100,
    ITEM CODE never narrower than any other column."""
    from itertools import product as combos

    for item, qty, date in combos((False, True), repeat=3):
        was = {}
        if item:
            was["item_code"] = "OLD-1"
        if qty:
            was["qty"] = "24"
        if date:
            was["delivery_date"] = "06/11/2026"
        lines = [_line("SO400236", "KL-WH", "X", "30", "13/11/2026", "R", was or None)]
        widths = _line_header_widths(_render(_ctx(ORDER, lines)))
        headers = ["LOCATION", "ITEM CODE"] + (["NEW ITEM CODE"] if item else []) + ["QTY"]
        headers += (["NEW QTY"] if qty else []) + ["DELIVERY"] + (["NEW DATE"] if date else [])
        assert [name for name, _ in widths] == headers, (was, widths)
        values = dict(widths)
        assert sum(values.values()) == 100, (was, widths)
        assert values["ITEM CODE"] == max(values.values()), (was, widths)
