"""WA-MSG-TRIM (owner, 3 Oct 2026): every chatbot contact gets the COMPACT stock view.

Since 1 Oct 2026 Meta charges every outgoing WhatsApp message, and the detailed view (one
row per location) is what pushes stock answers past the chunk limit into extra messages.
The data migration flips every `detailed` stock visibility row to `compact`: the global
default, access-type rows and contact overrides alike. `availability` is the separate
dealer mode and is never touched. The downgrade restores exactly the rows it flipped.

Plan: `documentation/plans/chatbot/PLAN-wa-msg-trim.md`.
"""
from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import text

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402,F401

from app.models.access import StockVisibilityPolicy
from app.models.base import set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.stock_visibility import default_policy, resolve_policy

from tests._pg_fixture import blank_session, unique_code
from tests.test_stock_visibility_policy import (
    _access_type,
    _contact,
    _load_migration,
    _policy_row,
    _tag,
)

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "wa_trim_0001_stock_compact.py"
)


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


def _run(db, fn_name: str) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _load_migration(_MIGRATION, f"wa_trim_0001_{fn_name}")
    context = MigrationContext.configure(db.connection())
    with Operations.context(context):
        getattr(module, fn_name)()
    db.expire_all()


def _seed(db):
    default = _policy_row(db, mode="detailed")
    end_user = _access_type(db, unique_code("end_user")[:50], "End user")
    dealer = _access_type(db, unique_code("dealer")[:50], "Dealer")
    end_user_row = _policy_row(db, mode="detailed", access_type=end_user)
    dealer_row = _policy_row(db, mode="availability", access_type=dealer)
    detailed_contact = _contact(db)
    detailed_row = _policy_row(db, mode="detailed", contact=detailed_contact)
    availability_row = _policy_row(db, mode="availability", contact=_contact(db))
    compact_row = _policy_row(db, mode="compact", contact=_contact(db))
    return {
        "default": default,
        "end_user": end_user_row,
        "dealer": dealer_row,
        "detailed": detailed_row,
        "availability": availability_row,
        "compact": compact_row,
        "detailed_contact": detailed_contact,
        "end_user_type": end_user,
    }


def _modes(db, rows: dict) -> dict[str, str]:
    return {
        key: db.get(StockVisibilityPolicy, row.id).mode
        for key, row in rows.items()
        if isinstance(row, StockVisibilityPolicy)
    }


def test_upgrade_turns_every_detailed_row_compact_and_leaves_availability(db):
    rows = _seed(db)

    _run(db, "upgrade")

    assert _modes(db, rows) == {
        "default": "compact",
        "end_user": "compact",
        "dealer": "availability",
        "detailed": "compact",
        "availability": "availability",
        "compact": "compact",
    }
    assert default_policy(db).mode == "compact"
    assert resolve_policy(db, contact_id=rows["detailed_contact"].id).mode == "compact"


def test_contact_on_a_formerly_detailed_access_type_reads_compact(db):
    rows = _seed(db)
    tagged = _contact(db)
    _tag(db, tagged, rows["end_user_type"])

    _run(db, "upgrade")

    assert resolve_policy(db, contact_id=tagged.id).mode == "compact"


def test_upgrade_is_idempotent(db):
    rows = _seed(db)

    _run(db, "upgrade")
    _run(db, "upgrade")

    assert set(_modes(db, rows).values()) == {"compact", "availability"}
    backed_up = db.execute(text("SELECT count(*) FROM stock_visibility_wa_trim_backup")).scalar()
    assert backed_up == 3


def test_downgrade_restores_exactly_the_rows_it_flipped(db):
    rows = _seed(db)

    _run(db, "upgrade")
    _run(db, "downgrade")

    assert _modes(db, rows) == {
        "default": "detailed",
        "end_user": "detailed",
        "dealer": "availability",
        "detailed": "detailed",
        "availability": "availability",
        "compact": "compact",
    }
    gone = db.execute(text("SELECT to_regclass('stock_visibility_wa_trim_backup')")).scalar()
    assert gone is None
