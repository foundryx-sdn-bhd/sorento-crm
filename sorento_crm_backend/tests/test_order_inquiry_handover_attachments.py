"""Line attachments on the OI handover email (#1312, PLAN-oi-line-attachments-27sep.md).

Contract: `documentation/plans/scm/oi-line-attachments-27sep-acceptance-criteria.md`,
AC-E1/AC-E3/AC-E4/AC-E5/AC-E7.

TEST-FIRST: written before `_build_handover_context` grows its second
`attachments_by_line` argument, so a red here is a `TypeError` (still takes one
positional argument), a missing `HANDOVER_ATTACHMENT_CAP_BYTES` constant, a missing
`soatt_0001_so_line_attachments` migration, or the template missing the "Attachments:"
print after that migration is applied - never an import typo.

E1/E4/E5 are pure (no DB): `_build_handover_context` takes plain dicts, the same shape
`test_order_inquiry_handover_automation.py::test_subject_ignores_blank_locations` already
exercises against the one-argument form. E3/E7 need the real template chain (r1 seed -> r2
layout -> r3 location column -> this lane's soatt migration), on a blank scratch schema,
reusing that file's own `_r2_template`/`_run_upgrade`/`_handover_context` helpers.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import text as sa_text

from app.services.automation_triggers import build_order_inquiry_link
from app.services.project_order_inquiry_service import _build_handover_context

from ._pg_fixture import blank_session
from .test_order_inquiry_handover_automation import (
    _handover_context,
    _r2_template,
    _run_upgrade,
)


def _pending(
    *,
    so_number: str = "SO423136",
    line_no: int = 3,
    item_code: str = "SRTWCY8605-PJ",
    core_line_id: str = "core-1",
    row_id: str = "row-1",
    order_inquiry_id: str = "oi-1",
) -> dict:
    """The shape `ProjectOrderInquiryService._record_handover` queues - `core_line_id`
    is the new key this lane adds beside `line_no`/`row_id`/`order_inquiry_id`."""
    return {
        "pso_id": "pso-1",
        "so_number": so_number,
        "customer": "BUIMACO",
        "project": "TUJU",
        "stock_location": None,
        "verb_keys": (),
        "line": {"item_code": item_code, "so_number": so_number},
        "order_inquiry_id": order_inquiry_id,
        "row_id": row_id,
        "core_line_id": core_line_id,
        "line_no": line_no,
        "actor": {"name": "Eling", "email": "person12@example.com"},
    }


# --------------------------------------------------------------------------- #
# AC-E1                                                                       #
# --------------------------------------------------------------------------- #


def test_handover_context_carries_line_attachments():
    pending = [_pending()]
    attachments_by_line = {
        "core-1": [
            {"filename": "a.png", "size_bytes": 100, "storage_provider": "r2", "storage_key": "key-a"},
            {"filename": "b.pdf", "size_bytes": 200, "storage_provider": "r2", "storage_key": "key-b"},
        ],
    }

    context, _source_id = _build_handover_context(pending, attachments_by_line)

    line = context["handover"]["lines"][0]
    assert line["attachments"] == [
        {"name": "SO423136-L3-a.png", "attached": True, "url": None},
        {"name": "SO423136-L3-b.pdf", "attached": True, "url": None},
    ]
    assert context["email_attachments"] == [
        {"filename": "SO423136-L3-a.png", "storage_provider": "r2", "storage_key": "key-a", "optional": True},
        {"filename": "SO423136-L3-b.pdf", "storage_provider": "r2", "storage_key": "key-b", "optional": True},
    ]


# --------------------------------------------------------------------------- #
# AC-E4                                                                       #
# --------------------------------------------------------------------------- #


def test_handover_size_cap_links_overflow():
    """AC-E4, extended by fix round 1 should-fix 5: a file too big to fit must not
    also sink every SMALLER file queued after it - 10 MB attaches, 10 MB overflows
    (20 MB > the 15 MB cap), and the 1 MB right after it still fits (11 MB total) and
    attaches too. Only counted toward the running total when actually attached."""
    ten_mb = 10 * 1024 * 1024
    one_mb = 1 * 1024 * 1024
    pending = [_pending(row_id="row-9", order_inquiry_id="oi-9")]
    attachments_by_line = {
        "core-1": [
            {"filename": "first.png", "size_bytes": ten_mb, "storage_provider": "r2", "storage_key": "k1"},
            {"filename": "second.png", "size_bytes": ten_mb, "storage_provider": "r2", "storage_key": "k2"},
            {"filename": "third.png", "size_bytes": one_mb, "storage_provider": "r2", "storage_key": "k3"},
        ],
    }

    context, _source_id = _build_handover_context(pending, attachments_by_line)

    expected_overflow_url = build_order_inquiry_link("oi-9") + "?row=row-9"
    line = context["handover"]["lines"][0]
    assert line["attachments"] == [
        {"name": "SO423136-L3-first.png", "attached": True, "url": None},
        {"name": "SO423136-L3-second.png", "attached": False, "url": expected_overflow_url},
        {"name": "SO423136-L3-third.png", "attached": True, "url": None},
    ]
    assert context["email_attachments"] == [
        {"filename": "SO423136-L3-first.png", "storage_provider": "r2", "storage_key": "k1", "optional": True},
        {"filename": "SO423136-L3-third.png", "storage_provider": "r2", "storage_key": "k3", "optional": True},
    ]


def test_handover_same_core_line_twice_attaches_once():
    """Fix round 1, captain's should-fix 6: two OI rows raised against the SAME SO
    line (a split raise) share one `core_line_id` - its files must attach to the
    email ONCE, not once per row, while BOTH rows still print the names."""
    pending = [
        _pending(row_id="row-1"),
        _pending(row_id="row-2"),
    ]
    attachments_by_line = {
        "core-1": [
            {"filename": "a.png", "size_bytes": 100, "storage_provider": "r2", "storage_key": "key-a"},
        ],
    }

    context, _source_id = _build_handover_context(pending, attachments_by_line)

    assert context["email_attachments"] == [
        {"filename": "SO423136-L3-a.png", "storage_provider": "r2", "storage_key": "key-a", "optional": True},
    ], "the file must attach exactly once, not once per row sharing the core line"
    lines = context["handover"]["lines"]
    assert len(lines) == 2
    for line in lines:
        assert line["attachments"] == [
            {"name": "SO423136-L3-a.png", "attached": True, "url": None},
        ], "both rows still print the name, even though only one MIME part exists"


# --------------------------------------------------------------------------- #
# AC-E5                                                                       #
# --------------------------------------------------------------------------- #


def test_handover_without_files_unchanged():
    pending = [_pending()]

    context, _source_id = _build_handover_context(pending, {})

    line = context["handover"]["lines"][0]
    assert not line.get("attachments"), "a line with no files must carry no attachments to print"
    assert "email_attachments" not in context


# --------------------------------------------------------------------------- #
# AC-E3 - the r2+r3+soatt template chain                                      #
# --------------------------------------------------------------------------- #


def _find_soatt_migration_path() -> Path | None:
    versions_dir = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    for path in versions_dir.glob("soatt_0001_so_line_attachments*.py"):
        return path
    return None


def _load_soatt_migration():
    path = _find_soatt_migration_path()
    assert path is not None, (
        "no alembic migration named soatt_0001_so_line_attachments was found under "
        "alembic/versions/ - the coder must add it (PLAN-oi-line-attachments-27sep.md "
        "3.1, AC-E7), down_revision chained onto main's current single head "
        "(sales_s1_reports_module)."
    )
    spec = importlib.util.spec_from_file_location("zzt_soatt_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _attachment_line(**over) -> dict:
    base = dict(
        so_date="01/09/2026", so_number="SO423136", item_code="SRTWCY8605-PJ",
        qty="10", delivery_date="01/09/2026", remark="ORDER", was=None,
    )
    base.update(over)
    return base


def test_handover_template_prints_attachments():
    from app.services.email_template_service import EmailTemplateService

    with_files = _attachment_line(
        attachments=[
            {"name": "SO423136-L3-a.png", "attached": True, "url": None},
            {"name": "SO423136-L3-b.pdf", "attached": True, "url": None},
        ],
    )
    without_files = _attachment_line(item_code="CSH2072", attachments=[])

    with blank_session() as db:
        template = _r2_template(db)
        _run_upgrade(_load_soatt_migration(), db)
        db.refresh(template)
        rendered = EmailTemplateService(db).render(
            template, _handover_context([with_files, without_files])
        )

    assert "Attachments: SO423136-L3-a.png, SO423136-L3-b.pdf" in rendered["body_html"]
    assert "Attachments: SO423136-L3-a.png, SO423136-L3-b.pdf" in rendered["body_text"]
    # The second line carries no files - nothing extra prints for it (AC-E5's other half).
    assert rendered["body_html"].count("Attachments:") == 1
    assert rendered["body_text"].count("Attachments:") == 1


# --------------------------------------------------------------------------- #
# AC-E7 - migration idempotency, single head                                  #
# --------------------------------------------------------------------------- #


def test_migration_idempotent_single_head():
    module = _load_soatt_migration()
    # Exactly one head, and this lane's migration is in its chain. Never the head's
    # name: every later migration PR (or a merge revision) moves the head, and a
    # pinned name turns this test red on a graph that is still correct.
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1, heads
    chain = {rev.revision for rev in script.walk_revisions(base="base", head=heads[0])}
    assert module.revision in chain, (module.revision, heads)

    with blank_session() as db:
        template = _r2_template(db)
        _run_upgrade(module, db)

        type_count = db.execute(
            sa_text("SELECT count(*) FROM attachment_types WHERE code = 'so_line_attachment'")
        ).scalar()
        assert type_count == 1

        db.refresh(template)
        once = template.body_html
        assert once.count("Attachments:") == 1

        _run_upgrade(module, db)  # replay - must not duplicate the type row or the marker

        type_count_again = db.execute(
            sa_text("SELECT count(*) FROM attachment_types WHERE code = 'so_line_attachment'")
        ).scalar()
        assert type_count_again == 1

        db.refresh(template)
        assert template.body_html == once
        assert template.body_html.count("Attachments:") == 1

        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        ctx = MigrationContext.configure(db.connection())
        with Operations.context(ctx):
            module.downgrade()

        db.refresh(template)
        assert "Attachments:" not in template.body_html
        type_count_down = db.execute(
            sa_text("SELECT count(*) FROM attachment_types WHERE code = 'so_line_attachment'")
        ).scalar()
        assert type_count_down == 0
