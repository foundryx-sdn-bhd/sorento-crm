"""`oihr_0004_wide_line_table`: the handover template ships on the wide shell with a
line table whose dates and quantities never wrap (EMAIL-HANDOVER-QTY, PR #1392,
AC-11 to AC-13, `PLAN-oi-handover-qty-change-30sep.md` S3).

Replays the real seed chain (212 -> soatt_0001, then eml_0002's block document) on a
blank scratch schema first, the way `tests/test_email_layout_migrations.py` does, so the
in-place update and the byte-for-byte downgrade are checked against the row a real
database carries, not a fixture copy.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from tests._pg_fixture import blank_session

from .test_email_layout_migrations import _row, _run, _scripts, _seed
from .test_order_inquiry_handover_automation import _table_rows

REVISION = "oihr_0004_wide_line_table"
CODE = "order_inquiry_handover" + "_default"

LINE_HEADERS = [
    "SO DATE", "S/O NO", "LOCATION", "ITEM CODE", "QTY", "QTY CHANGE TO",
    "DELIVERY DATE", "DELIVERY DATE CHANGE TO", "REMARK",
]


def _find_migration_path() -> Path | None:
    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    for path in versions_dir.glob(f"{REVISION}*.py"):
        return path
    return None


def _load_migration():
    path = _find_migration_path()
    assert path is not None, (
        f"no alembic migration named {REVISION} under alembic/versions/ - the coder must "
        "add it (PLAN-oi-handover-qty-change-30sep.md S3)"
    )
    spec = importlib.util.spec_from_file_location("zzt_oihr_0004", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _handover_row(db) -> dict:
    row = _row(db, CODE)
    assert row is not None, "no handover template row"
    layout = row[2]
    if isinstance(layout, str):
        layout = json.loads(layout)
    return {"body_html": row[0], "body_text": row[1], "layout_json": layout}


def _custom_text_html(layout: dict) -> str:
    return "\n".join(b["html"] for b in layout["blocks"] if b["type"] == "custom_text" and b.get("html"))


def _line_table(html: str) -> str:
    """The second `<table>` of the authored body: the line table (the first is the S/O
    summary)."""
    tables = re.findall(r"<table.*?</table>", html, re.S)
    assert len(tables) == 2, f"expected the S/O table and the line table, got {len(tables)}"
    return tables[1]


# --------------------------------------------------------------------------- #
# AC-15 (housekeeping): the revision exists, is short enough, and sits on the head chain #
# --------------------------------------------------------------------------- #


def test_migration_exists_and_is_on_the_single_head_chain():
    module = _load_migration()
    assert module.revision == REVISION
    assert len(module.revision) <= 32
    sd = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = list(sd.get_heads())
    assert len(heads) == 1, heads
    ancestry = {r.revision for r in sd.walk_revisions(base="base", head=heads[0])}
    assert REVISION in ancestry
    assert "eml_0002_seed_layouts" in ancestry, "the wide document builds on eml_0002's"


# --------------------------------------------------------------------------- #
# AC-11: in place, wide, nowrap, mirror, text untouched, idempotent            #
# --------------------------------------------------------------------------- #


def test_upgrade_updates_the_seeded_document_in_place_and_is_idempotent():
    module = _load_migration()
    with blank_session() as db:
        mods = _seed(db)
        _run(mods["eml_0002_seed_layouts"], db)
        before = _handover_row(db)
        assert before["layout_json"] is not None, "sanity: eml_0002 converted the seeded body"
        assert before["layout_json"].get("width") in (None, "standard")

        _run(module, db)
        after = _handover_row(db)
        layout = after["layout_json"]
        assert layout["width"] == "wide", layout.get("width")
        assert [b["type"] for b in layout["blocks"]] == [
            b["type"] for b in before["layout_json"]["blocks"]
        ], "block order is kept"
        assert layout["version"] == 1

        authored = _custom_text_html(layout)
        table = _line_table(authored)
        ths = re.findall(r"<th(?:\s[^>]*)?>", table)
        assert len(ths) == 9, ths
        for th in ths[:-1]:
            assert re.search(r"width:\s*\d+px", th), f"every column but REMARK has a width: {th}"
        assert not re.search(r"width:\s*\d+px", ths[-1]), f"REMARK takes the slack: {ths[-1]}"
        tds = re.findall(r"<td(?:\s[^>]*)?>", table)
        # Nine cells per row in the template source (the two CHANGE TO cells are wrapped
        # in `{% if cols.* %}`); every cell but the last, REMARK, refuses to wrap.
        assert len(tds) == 9, tds
        for td in tds[:-1]:
            assert "white-space:nowrap" in td, td
        assert "white-space:nowrap" not in tds[-1], tds[-1]
        # The Jinja that decides the two CHANGE TO columns is untouched.
        for token in (
            "{% if cols.qty %}", "{% if cols.delivery_date %}",
            "{% if line.was and line.was.qty %}", "{% if line.attachments %}",
        ):
            assert token in authored, token

        assert after["body_html"] == authored, "body_html mirrors the custom text blocks (D3)"
        assert after["body_text"] == before["body_text"], "the hand-tuned text part is untouched"
        assert db.execute(
            sa.text("SELECT count(*) FROM email_templates WHERE code = :c"), {"c": CODE}
        ).scalar() == 1

        _run(module, db)
        assert _handover_row(db) == after, "idempotent: the second run changes nothing"

        _run(module, db, "downgrade")
        restored = _handover_row(db)
        assert restored["layout_json"] == before["layout_json"], "downgrade restores the document"
        assert restored["body_html"] == before["body_html"], "downgrade restores the body byte for byte"
        assert restored["body_text"] == before["body_text"]


# --------------------------------------------------------------------------- #
# AC-12: a database with no row at all gets one in the new shape               #
# --------------------------------------------------------------------------- #


def test_upgrade_inserts_when_the_row_is_absent():
    module = _load_migration()
    with blank_session() as db:
        _run(module, db)
        row = _handover_row(db)
        assert row["layout_json"]["width"] == "wide"
        assert row["body_html"] == _custom_text_html(row["layout_json"])
        assert "SO DATE | S/O NO | LOCATION" in row["body_text"]
        subject = db.execute(
            sa.text("SELECT subject FROM email_templates WHERE code = :c"), {"c": CODE}
        ).scalar()
        assert subject == "OI: {{ handover.subject_scope }}"


# --------------------------------------------------------------------------- #
# AC-13: rendered through the real service, 900 wide, cells in order           #
# --------------------------------------------------------------------------- #


def test_migrated_template_renders_wide_with_the_line_total_cells():
    from app.models.email_template import EmailTemplate
    from app.services.email_layout import has_layout
    from app.services.email_template_service import EmailTemplateService

    module = _load_migration()
    line = {
        "so_date": "15/09/2026", "so_number": "SO402757", "location": "SRT-MAIN",
        "item_code": "SRTWT6808", "qty": "436",
        "delivery_date": "25/09/2026", "remark": "ADVANCE, ORDER 264",
        "was": {"qty": "172", "delivery_date": "01/10/2026"},
    }
    ctx = {
        "handover": {
            "subject_scope": "SRT-MAIN @ SO402757",
            "verbs": ["ORDER", "ADVANCE"],
            "headline": "ORDER, ADVANCE",
            "orders": [{"so_number": "SO402757", "customer": "C", "project": "P"}],
            "lines": [line],
            "line_count": 1,
            "link": "https://crm.test/project-sales/order-inquiries?query=SO402757",
        },
        "actor": {"name": "Eling", "email": "person12@example.com"},
        "today": "30/09/2026",
    }
    with blank_session() as db:
        _run(module, db)
        template = db.query(EmailTemplate).filter(EmailTemplate.code == CODE).one()
        out = EmailTemplateService(db).render(template, ctx)
        html = out["body_html"]
        assert has_layout(html)
        assert 'width="900"' in html and "max-width:900px" in html
        headers = [
            re.sub(r"<[^>]+>", "", h).strip()
            for h in re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)
        ][3:]
        assert headers == LINE_HEADERS, headers
        rows = _table_rows(html)
        assert [
            "15/09/2026", "SO402757", "SRT-MAIN", "SRTWT6808", "172", "436",
            "01/10/2026", "25/09/2026", "ADVANCE, ORDER 264",
        ] in rows, rows
        assert out["subject"] == "OI: SRT-MAIN @ SO402757"
        assert "SO DATE | S/O NO | LOCATION" in out["body_text"]
        assert "172 | 436" in out["body_text"] or "| 172 | 436 |" in out["body_text"], out["body_text"]


# --------------------------------------------------------------------------- #
# Review round 1 (should-fix 3): the template is admin-editable since #1349,  #
# so a document an admin arranged, or a row eml_0002 left NULL because its    #
# body had been edited, is left alone by both directions.                     #
# --------------------------------------------------------------------------- #


def test_admin_arranged_document_is_left_alone_both_ways():
    module = _load_migration()
    with blank_session() as db:
        mods = _seed(db)
        _run(mods["eml_0002_seed_layouts"], db)
        edited = json.loads(json.dumps(_handover_row(db)["layout_json"]))
        edited["blocks"].insert(1, {"type": "intro", "text": "Hi purchasing", "align": "left"})
        db.execute(
            sa.text("UPDATE email_templates SET layout_json = CAST(:l AS jsonb), body_html = :b WHERE code = :c"),
            {"l": json.dumps(edited), "b": "<p>admin</p>", "c": CODE},
        )
        before = _handover_row(db)

        _run(module, db)
        assert _handover_row(db) == before, "upgrade must not overwrite an admin's document"
        _run(module, db, "downgrade")
        assert _handover_row(db) == before, "downgrade must not touch a document it never wrote"


def test_row_left_null_by_eml_0002_is_left_alone():
    module = _load_migration()
    with blank_session() as db:
        mods = _seed(db)
        db.execute(
            sa.text("UPDATE email_templates SET body_html = body_html || '<p>admin</p>' WHERE code = :c"),
            {"c": CODE},
        )
        _run(mods["eml_0002_seed_layouts"], db)
        before = _handover_row(db)
        assert before["layout_json"] is None, "sanity: eml_0002 skipped the edited body"

        _run(module, db)
        assert _handover_row(db) == before
        _run(module, db, "downgrade")
        assert _handover_row(db) == before


def test_seeded_document_resaved_by_the_editor_is_still_converted_and_a_skip_is_logged(caplog):
    """Review round 1 (should-fix): the block editor's Save re-dumps an unchanged
    arrangement with `width: "standard"` once the field exists; that row is still ours
    to convert. And a row this migration cannot convert says so in the log."""
    import logging

    module = _load_migration()
    with blank_session() as db:
        mods = _seed(db)
        _run(mods["eml_0002_seed_layouts"], db)
        resaved = json.loads(json.dumps(_handover_row(db)["layout_json"]))
        resaved["width"] = "standard"
        db.execute(
            sa.text("UPDATE email_templates SET layout_json = CAST(:l AS jsonb) WHERE code = :c"),
            {"l": json.dumps(resaved), "c": CODE},
        )
        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            _run(module, db)
        assert _handover_row(db)["layout_json"]["width"] == "wide"
        assert "left untouched" not in caplog.text

        edited = json.loads(json.dumps(_handover_row(db)["layout_json"]))
        edited["blocks"].insert(1, {"type": "intro", "text": "Hi", "align": "left"})
        db.execute(
            sa.text("UPDATE email_templates SET layout_json = CAST(:l AS jsonb) WHERE code = :c"),
            {"l": json.dumps(edited), "c": CODE},
        )
        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            _run(module, db, "downgrade")
        assert "oihr_0004 downgrade" in caplog.text and "left untouched" in caplog.text
        assert _handover_row(db)["layout_json"] == edited
