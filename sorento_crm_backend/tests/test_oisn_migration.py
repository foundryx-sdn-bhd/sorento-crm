"""AC-SN-32: migration `oisn_0001_staged_notify` (down_revision `ac_consign_0001_doc_type`).

Adds `parked_at` / `notice_parked_at` / `notice_parked_by` / `notified_qty`, and rewrites the
two reserve templates' subject + layout_json in place ONLY while they still equal the seeded
values (the state after the real seed chain + `eml_0002_seed_layouts`); an admin-edited
template is left alone; downgrade restores the seeded values and drops the columns.

The migration does not exist yet: every test fails on the missing revision, the correct red.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from tests._pg_fixture import blank_session
from tests.test_email_layout_migrations import RESERVE_CODES, _run, _scripts, _seed

REVISION = "oisn_0001_staged_notify"
COLUMNS = {
    "order_inquiry_reserve_requests": {"parked_at"},
    "order_inquiry_reserve_request_rows": {"notice_parked_at", "notice_parked_by", "notified_qty"},
}


def _mig():
    scripts = _scripts()
    assert REVISION in scripts, f"migration {REVISION} does not exist yet"
    return scripts[REVISION]


def _seeded(db):
    mods = _seed(db)
    _run(mods["eml_0002_seed_layouts"], db)
    return {code: _tpl(db, code) for code in RESERVE_CODES}


def _tpl(db, code):
    return tuple(
        db.execute(
            sa.text("SELECT subject, layout_json FROM email_templates WHERE code = :c"), {"c": code}
        ).one()
    )


def _columns(db):
    found = {}
    for table in COLUMNS:
        found[table] = {
            name
            for (name,) in db.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :t AND table_schema = ANY (current_schemas(false))"
                ),
                {"t": table},
            )
        }
    return found


def test_AC_SN_32_revision_and_parent():
    mig = _mig()
    assert mig.down_revision == "ac_consign_0001_doc_type"
    assert len(REVISION) <= 32


def test_AC_SN_32_upgrade_adds_columns_and_updates_seeded_templates():
    mig = _mig()
    with blank_session() as db:
        seeded = _seeded(db)
        _run(mig, db, "downgrade")  # blank schema is built from the models: start without them
        assert not any(_columns(db)[t] & cols for t, cols in COLUMNS.items())
        _run(mig, db)
        present = _columns(db)
        for table, cols in COLUMNS.items():
            assert cols <= present[table], (table, present[table])
        for code in RESERVE_CODES:
            subject, layout = _tpl(db, code)
            assert subject != seeded[code][0], f"{code}: subject must gain the bulk branch"
            assert layout != seeded[code][1], f"{code}: layout must gain the bulk branch"
            assert "reserve.requests" in subject + json.dumps(layout)


def test_AC_SN_32_single_oi_render_is_unchanged():
    from app.models.email_template import EmailTemplate
    from app.services.email_template_service import EmailTemplateService

    from tests.test_order_inquiry_reserve import _reserve_fixture_context

    mig = _mig()
    with blank_session() as db:
        _seeded(db)
        context = _reserve_fixture_context()

        def render():
            out = {}
            for code in RESERVE_CODES:
                template = db.query(EmailTemplate).filter(EmailTemplate.code == code).one()
                out[code] = EmailTemplateService(db).render(template, context)
            return out

        before = render()
        _run(mig, db)
        db.expire_all()
        after = render()
        for code in RESERVE_CODES:
            assert after[code]["subject"] == before[code]["subject"], code
            assert after[code]["body_html"] == before[code]["body_html"], code


def test_AC_SN_32_admin_edited_template_is_left_alone():
    mig = _mig()
    requested, reserved = RESERVE_CODES
    with blank_session() as db:
        seeded = _seeded(db)
        db.execute(
            sa.text("UPDATE email_templates SET subject = 'Admin subject' WHERE code = :c"),
            {"c": requested},
        )
        db.execute(
            sa.text(
                "UPDATE email_templates SET layout_json = layout_json || CAST(:x AS jsonb) "
                "WHERE code = :c"
            ),
            {"x": json.dumps({"admin_note": "edited"}), "c": reserved},
        )
        edited_reserved = _tpl(db, reserved)
        _run(mig, db)
        assert _tpl(db, requested)[0] == "Admin subject"
        assert _tpl(db, reserved) == edited_reserved, "an admin-edited layout is left as is"

    with blank_session() as db:
        seeded = _seeded(db)
        db.execute(
            sa.text("UPDATE email_templates SET subject = 'Admin subject' WHERE code = :c"),
            {"c": requested},
        )
        _run(mig, db)
        assert _tpl(db, reserved)[0] != seeded[reserved][0], "the untouched template is updated"


def test_AC_SN_32_downgrade_restores_seeded_values_and_drops_columns():
    mig = _mig()
    with blank_session() as db:
        seeded = _seeded(db)
        _run(mig, db)
        _run(mig, db, "downgrade")
        for code in RESERVE_CODES:
            assert _tpl(db, code) == seeded[code], f"{code}: downgrade restores the seeded values"
        present = _columns(db)
        for table, cols in COLUMNS.items():
            assert not (cols & present[table]), f"{table} still has {cols & present[table]}"


def test_fix_backfill_notified_qty_for_answered_rows_then_amend_cycle(monkeypatch):
    """Nit 3: answered rows get `notified_qty = qty_reserved` at upgrade; NULL stays NULL."""
    from decimal import Decimal

    from tests.test_oi_staged_notify import (
        SUMMARY_URL,
        _automations,
        _commit,
        _ok,
        _oi_row,
        _new_oi,
        _request,
        _reserve,
        _rr,
    )
    from tests.test_order_inquiry_reserve import (
        RESERVER_PERMISSIONS,
        _api,
        _as,
        _captured_dispatches,
        _register,
    )
    from tests.test_order_inquiry_worklist import _user

    mig = _mig()
    gen = _api(RESERVER_PERMISSIONS)
    client, world = next(gen)
    try:
        _automations(world)
        _register()
        _captured_dispatches(monkeypatch)
        who = _user(world.db, "zzt-oisn-raiser")
        inquiry = _new_oi(world, who)
        answered, open_row = _oi_row(world, inquiry, qty="200"), _oi_row(world, inquiry, qty="30")
        _request(client, world, inquiry, [(answered, "200"), (open_row, "30")], park=False)
        _commit(world, inquiry, reserves=[_reserve(world, answered, "200")])
        world.db.commit()

        # Pre-upgrade state: the column does not exist, so nothing was ever stamped.
        _run(mig, world.db, "downgrade")
        _run(mig, world.db)
        world.db.commit()

        assert _rr(world, answered).notified_qty == Decimal("200")
        assert _rr(world, open_row).notified_qty is None

        _commit(
            world, inquiry,
            amendments=[{"row_id": answered.id, "qty_reserved": "150", "reason": "short"}],
            park_notice=True,
        )
        _commit(
            world, inquiry,
            amendments=[{"row_id": answered.id, "qty_reserved": "200"}],
            park_notice=True,
        )
        with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
            assert _ok(cs.get(SUMMARY_URL))["my_parked_notices"] == 0
    finally:
        gen.close()


def test_fix_bulk_plain_text_pluralises_more_count():
    """Nit 6: body_text says 'inquiry' for 1 more, 'inquiries' for 2."""
    from app.models.email_template import EmailTemplate
    from app.services.email_template_service import EmailTemplateService

    mig = _mig()
    with blank_session() as db:
        _seeded(db)
        _run(mig, db)
        db.expire_all()

        def text(code, more):
            template = db.query(EmailTemplate).filter(EmailTemplate.code == code).one()
            ctx = {
                "reserve": {
                    "requests": [
                        {"inquiry_no": "OI-1", "so_number": "SO1", "customer": "C", "line_count": 1}
                    ],
                    "oi_count": 1 + more,
                    "line_count": 1 + more,
                    "more_count": more,
                    "link": "https://crm.example.com/x?reserve=needs_reserve",
                },
                "requester": {"name": "Joey", "email": "j@x.test"},
                "actor": {"name": "Eling", "email": "e@x.test"},
                "today": "14/10/2026",
            }
            return EmailTemplateService(db).render(template, ctx)["body_text"]

        for code in RESERVE_CODES:
            assert "and 1 more order inquiry." in text(code, 1), code
            assert "and 2 more order inquiries." in text(code, 2), code
