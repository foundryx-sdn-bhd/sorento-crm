"""The blank-schema fallback carries one kind row per entity hint, the same rows the
REF-PROBES migration seeds (release fix 12 Oct 2026, PR #1598).

`test_rearch_s0_entity_kinds_seed.py` reads the migrated table, so it cannot see the fallback
`policy.load_policy` uses on a schema no migration seeded. The migration keeps a frozen copy
of its rows (a migration's data must not move with the app), so the two copies are pinned
equal here. Pure Python: no database.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.services.chatbot.contracts import ENTITY_HINTS
from app.services.chatbot.turn import policy_rows

_MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "rkind_0003_reference_kind_rows.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("_rkind_0003_reference_kind_rows", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_fallback_has_exactly_one_row_per_entity_hint() -> None:
    kinds = [row["kind"] for row in policy_rows.DEFAULT_KIND_ROWS]
    assert sorted(kinds) == sorted(ENTITY_HINTS), kinds


def test_the_fallback_reference_rows_equal_the_migration_rows() -> None:
    fallback = {
        (row["kind"], row["label"], row["resolver_source"], row["did_you_mean"], row["default_narrowing"])
        for row in policy_rows.REFERENCE_KIND_ROWS
    }
    migrated = {
        (kind, label, source, False, "not_applicable") for kind, label, source in _migration().KIND_ROWS
    }
    assert fallback == migrated


def test_the_migration_revision_fits_alembic_version() -> None:
    module = _migration()
    assert module.revision == "rkind_0003_reference_kind_rows" and len(module.revision) <= 32
    assert module.down_revision == "rkind_0002_parser_ref_kinds_v56"
