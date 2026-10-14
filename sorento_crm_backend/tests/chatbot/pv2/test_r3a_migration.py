"""PV2 redesign R3a red tests (UAC R3a-1, R3a-2 migration half): `pv2_0004_kind_registry`.

The migration adds the R3a columns to `chatbot_entity_kinds` and `chatbot_domains` (ADD COLUMN IF NOT EXISTS, server
defaults), seeds them by kind / domain name (idempotent), and its downgrade drops them. The data it writes is compared
with the literals in `_r3a_expect` (the HEAD values of the retired constants). Runs on the blank schema, rolled back;
no database is created. Every row is seeded here from the OLD columns only, so the migration must add the rest.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text

from tests.chatbot.pv2 import _r3a_expect as X
from tests._pg_fixture import blank_session

_BACKEND = Path(__file__).resolve().parents[3]
_ALEMBIC_DIR = _BACKEND / "alembic"
_FILE = _ALEMBIC_DIR / "versions" / "pv2_0004_kind_registry.py"


def _load():
    assert _FILE.exists(), f"{_FILE.name} does not exist yet (R3a-1)"
    if str(_ALEMBIC_DIR) not in sys.path:
        sys.path.insert(0, str(_ALEMBIC_DIR))
    spec = importlib.util.spec_from_file_location("_zzt_pv2_0004_kind_registry", _FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepare(db) -> None:
    """The stale shape: only the old columns, one row per seeded kind and domain."""
    from app.services.chatbot.turn import policy_rows

    db.execute(text("DELETE FROM chatbot_entity_kinds"))
    db.execute(text("DELETE FROM chatbot_domains"))
    for table, cols in (("chatbot_entity_kinds", X.NEW_KIND_FIELDS), ("chatbot_domains", X.NEW_DOMAIN_FIELDS)):
        for col in cols:
            db.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}"))
    for i, r in enumerate(policy_rows.DEFAULT_KIND_ROWS):
        db.execute(
            text(
                "INSERT INTO chatbot_entity_kinds (id, kind, label, resolver_source, did_you_mean, "
                "default_narrowing, family_grouping, base_property_words, roster_cap, sort_order) VALUES "
                "(gen_random_uuid(), :k, :l, :rs, :d, :n, :f, CAST(:b AS jsonb), :c, :s)"
            ),
            dict(k=r["kind"], l=r["label"], rs=r["resolver_source"], d=r["did_you_mean"], n=r["default_narrowing"],
                 f=r["family_grouping"], b=json.dumps(r["base_property_words"]), c=r["roster_cap"], s=i),
        )
    for i, r in enumerate(policy_rows.DEFAULT_DOMAIN_ROWS):
        db.execute(
            text(
                "INSERT INTO chatbot_domains (id, name, label, intents, tools, switch_words, narrowing, supported, "
                "ladder, sort_order) VALUES (gen_random_uuid(), :n, :l, CAST(:i AS text[]), CAST(:t AS text[]), "
                "CAST(:sw AS text[]), CAST(:nw AS jsonb), :su, CAST(:la AS text[]), :s)"
            ),
            dict(n=r["name"], l=r["label"], i=list(r["intents"]), t=list(r["tools"]), sw=list(r["switch_words"]),
                 nw=json.dumps(r["narrowing"]), su=r["supported"], la=list(r["ladder"]), s=i),
        )


def _read(db) -> tuple[dict[str, dict], dict[str, dict]]:
    kinds = {
        r["kind"]: dict(r)
        for r in db.execute(
            text("SELECT kind, " + ", ".join(X.NEW_KIND_FIELDS) + " FROM chatbot_entity_kinds")
        ).mappings()
    }
    domains = {
        r["name"]: dict(r)
        for r in db.execute(
            text("SELECT name, " + ", ".join(X.NEW_DOMAIN_FIELDS) + " FROM chatbot_domains")
        ).mappings()
    }
    return kinds, domains


def _columns(db, table: str) -> set[str]:
    return {
        r[0]
        for r in db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = :t"
            ),
            {"t": table},
        )
    }


def test_r3a1_the_migration_exists_and_chains_onto_the_label_reasoning_head():
    module = _load()
    assert module.revision == "pv2_0004_kind_registry"
    assert module.down_revision == "pv2_0003_label_reasoning"
    assert callable(getattr(module, "apply", None)), "the module needs apply(bind) (bootstrap_env calls it)"


def test_r3a1_alembic_has_a_single_head_and_it_is_the_new_migration():
    cfg = Config(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    assert list(ScriptDirectory.from_config(cfg).get_heads()) == ["pv2_0004_kind_registry"]


def test_r3a2_apply_writes_every_kind_and_domain_value_the_retired_constants_held():
    module = _load()
    with blank_session() as db:
        _prepare(db)
        module.apply(db.connection())
        kinds, domains = _read(db)
    assert set(kinds) == set(X.KINDS) and set(domains) == set(X.DOMAINS), "apply adds no row and drops none"
    for kind in X.KINDS:
        for name, want in X.expected_kind(kind).items():
            assert X.norm(name, kinds[kind][name]) == want, f"kind {kind} column {name}"
    for dom in X.DOMAINS:
        for name, want in X.expected_domain(dom).items():
            assert X.norm(name, domains[dom][name]) == want, f"domain {dom} column {name}"


def test_r3a1_apply_is_idempotent_run_twice_gives_the_same_rows():
    module = _load()
    with blank_session() as db:
        _prepare(db)
        module.apply(db.connection())
        first = _read(db)
        module.apply(db.connection())
        second = _read(db)
    assert first == second
    assert len(second[0]) == len(X.KINDS) and len(second[1]) == len(X.DOMAINS)


def test_r3a1_apply_keeps_a_staff_edited_row_value_it_did_not_seed_over_a_second_run():
    """The seed is by name and idempotent: a second run restores nothing it did not own beyond the seeded
    columns, and never touches the old columns (a staff-edited label stays)."""
    module = _load()
    with blank_session() as db:
        _prepare(db)
        module.apply(db.connection())
        db.execute(text("UPDATE chatbot_entity_kinds SET label = 'Edited by staff' WHERE kind = 'customer'"))
        module.apply(db.connection())
        label = db.execute(text("SELECT label FROM chatbot_entity_kinds WHERE kind = 'customer'")).scalar()
    assert label == "Edited by staff"


def test_r3a1_upgrade_adds_the_columns_and_downgrade_drops_them():
    module = _load()
    with blank_session() as db:
        _prepare(db)
        assert not set(X.NEW_KIND_FIELDS) & _columns(db, "chatbot_entity_kinds")
        assert not set(X.NEW_DOMAIN_FIELDS) & _columns(db, "chatbot_domains")
        context = MigrationContext.configure(connection=db.connection())
        with Operations.context(context):
            module.upgrade()
        assert set(X.NEW_KIND_FIELDS) <= _columns(db, "chatbot_entity_kinds")
        assert set(X.NEW_DOMAIN_FIELDS) <= _columns(db, "chatbot_domains")
        kinds, domains = _read(db)
        assert X.norm("default_domain", kinds["inbound_shipment"]["default_domain"]) == "incoming"
        assert domains["inventory"]["reads_quantity"] is True
        with Operations.context(context):
            module.downgrade()
        assert not set(X.NEW_KIND_FIELDS) & _columns(db, "chatbot_entity_kinds")
        assert not set(X.NEW_DOMAIN_FIELDS) & _columns(db, "chatbot_domains")


@pytest.mark.parametrize("table, cols", [
    ("chatbot_entity_kinds", X.NEW_KIND_FIELDS), ("chatbot_domains", X.NEW_DOMAIN_FIELDS),
])
def test_r3a1_a_row_inserted_without_the_new_columns_takes_the_server_defaults(table, cols):
    """ADD COLUMN with server defaults: a row staff create later (no registry values) is valid and reads as 'none'."""
    module = _load()
    with blank_session() as db:
        _prepare(db)
        context = MigrationContext.configure(connection=db.connection())
        with Operations.context(context):
            module.upgrade()
        if table == "chatbot_entity_kinds":
            db.execute(text(
                "INSERT INTO chatbot_entity_kinds (id, kind, label, resolver_source, default_narrowing) "
                "VALUES (gen_random_uuid(), 'zzt_new_kind', 'New', 'x', 'optional_filter')"))
            row: dict[str, Any] = dict(db.execute(text(
                "SELECT " + ", ".join(cols) + " FROM chatbot_entity_kinds WHERE kind = 'zzt_new_kind'"
            )).mappings().one())
        else:
            db.execute(text(
                "INSERT INTO chatbot_domains (id, name, label) VALUES (gen_random_uuid(), 'zzt_new_domain', 'New')"))
            row = dict(db.execute(text(
                "SELECT " + ", ".join(cols) + " FROM chatbot_domains WHERE name = 'zzt_new_domain'"
            )).mappings().one())
    for name, value in row.items():
        assert X.norm(name, value) in ({}, (), frozenset(), None, False), f"{name} default is {value!r}"
