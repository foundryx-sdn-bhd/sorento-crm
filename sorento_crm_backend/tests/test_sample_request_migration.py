"""SR-CORE schema and permissions, red-first (AC-SR-70).

The scratch schema is built from ``Base.metadata`` (tests/_pg_fixture.py), so the table and
column checks prove the MODELS declare them; the migration file checks prove the revision
exists, grants the three slugs and sits on a single alembic head. Permission ROWS are not
asserted against the scratch schema: it holds no migration-seeded data.
"""
from __future__ import annotations

import pathlib

from sqlalchemy import text

from ._pg_fixture import blank_session

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
SLUGS = (
    "projects.sample_requests.view",
    "projects.sample_requests.create",
    "projects.sample_requests.process",
)


def _columns(db, table: str) -> set[str]:
    rows = db.execute(
        text(
            "select column_name from information_schema.columns "
            "where table_name = :t and table_schema = any(current_schemas(false))"
        ),
        {"t": table},
    )
    return {r[0] for r in rows}


def test_ac_sr_70_sample_request_tables_exist():
    """AC-SR-70: both tables exist with the card's columns."""
    with blank_session() as db:
        header = _columns(db, "sample_requests")
        lines = _columns(db, "sample_request_lines")
    assert {
        "id", "company_id", "request_no", "status_id", "project_id", "project_title",
        "customer_id", "customer_name", "salesperson_id", "source",
        "contact_id", "space_id", "request_date", "delivery_date",
        "scope_note", "ship_to_name", "ship_to_address", "site_contact_name",
        "site_contact_phone", "site_contact_note", "delivery_mode", "loan_value", "loan_unit",
        "goods_from", "purpose", "board_mounting", "board_label", "consignment_no", "po_no",
        "submitted_at", "confirmed_at", "delivered_at", "return_due", "cancelled_at",
        "cancel_reason",
    } <= header
    assert {"id", "request_id", "line_no", "product_id", "qty", "remark"} <= lines


def test_ac_sr_70_settings_columns_exist():
    """AC-SR-70 / AC-SR-20: the project switch is a column on system_settings."""
    with blank_session() as db:
        cols = _columns(db, "system_settings")
    assert "sample_request_require_project" in cols


def test_ac_sr_70_settings_columns_default_false():
    """AC-SR-20: OFF on deploy (a fresh settings row reads False)."""
    from app.models.user import SystemSetting

    with blank_session() as db:
        row = SystemSetting(id="00000000-0000-0000-0000-000000000070")
        db.add(row)
        db.flush()
        db.refresh(row)
        assert row.sample_request_require_project is False


def test_ac_sr_70_a_migration_creates_the_tables_and_grants_the_slugs():
    """AC-SR-70: one additive revision names the tables and all three permission slugs."""
    hits = [
        f for f in VERSIONS.glob("*.py")
        if "sample_requests" in f.read_text() and "sample_request_lines" in f.read_text()
    ]
    assert len(hits) == 1, [f.name for f in hits]
    body = hits[0].read_text()
    for slug in SLUGS:
        assert slug in body
    assert "sample_request_require_project" in body


def test_ac_sr_70_revision_id_fits_and_alembic_has_one_head():
    """AC-SR-70: revision id <= 32 chars and a single head."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = VERSIONS.parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, heads
    mine = [
        r for r in script.walk_revisions()
        if "sample_requests" in pathlib.Path(r.path).read_text()
        and "sample_request_lines" in pathlib.Path(r.path).read_text()
    ]
    assert len(mine) == 1
    assert len(mine[0].revision) <= 32
    assert mine[0].down_revision


# ---------------------------------------------------------------- form rebuild (16 Oct)


def _sr_migration_text() -> str:
    hits = [
        f for f in VERSIONS.glob("*.py")
        if "sample_requests" in f.read_text() and "sample_request_lines" in f.read_text()
    ]
    assert len(hits) == 1, [f.name for f in hits]
    return hits[0].read_text()


def test_ac_sr_03_20_removed_columns_are_gone_from_the_models():
    """Owner 16 Oct: no quotation, no alternative product, no immediate tick (models)."""
    with blank_session() as db:
        header = _columns(db, "sample_requests")
        lines = _columns(db, "sample_request_lines")
        settings = _columns(db, "system_settings")
    assert "quotation_version_id" not in header
    assert "deliver_immediately" not in header
    assert "alt_product_id" not in lines
    assert "sample_request_require_quotation" not in settings
    assert "sample_request_require_project" in settings


def test_ac_sr_70_edited_migration_no_longer_creates_the_removed_columns():
    """AC-SR-70: sr_0001 is edited in place, so its text names none of the three columns
    nor the quotation setting."""
    body = _sr_migration_text()
    for removed in (
        "quotation_version_id",
        "deliver_immediately",
        "alt_product_id",
        "sample_request_require_quotation",
    ):
        assert removed not in body, removed
    assert "sample_request_require_project" in body
