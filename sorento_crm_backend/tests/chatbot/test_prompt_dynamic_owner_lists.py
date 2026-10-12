"""PROMPT-DYNAMIC, owner answers of 2 Oct 2026 (PLAN-prompt-dynamic-30sep, "Owner answers"):

- answer 2 (D-B2): the domain words are a curated list in the DB (`chatbot_domain_words`),
  seeded with the owner's 19 words in his order, rendered in his exact layout;
- answer 4 (D-B4): the status lists keep his subsets through a row tag
  (`chatbot_status_words.prompt_lists`): `{{statuses}}` and `{{status_values}}` give
  `outstanding` and `delivered` only, and the new `{{status_field_values}}` gives line 778,
  `outstanding|delivered|sales_report`.

Every list below must reproduce the owner's production file byte for byte.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip
from app.services import ai_prompt_registry, chatbot_prompt_vars as pv
from tests._pg_fixture import pg_session
from tests.chatbot.test_prompt_dynamic_prod_snapshot import REFERENCE_KINDS

BACKEND = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.prod-20261001.txt"


def _migration():
    path = BACKEND / "alembic" / "versions" / "pdyn_0004_prompt_lists.py"
    spec = importlib.util.spec_from_file_location("_t_pdyn_0004", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _fresh_cache():
    pv.clear_cache()
    yield
    pv.clear_cache()


def _owner(start: str, end: str) -> str:
    src = SNAPSHOT.read_text(encoding="utf-8")
    s = src.index(start)
    return src[s : src.index(end, s)].rstrip("\n")


def _seeded(db) -> None:
    _migration().apply(db.connection())
    db.flush()
    pv.clear_cache()


# `serial_ddl`: each test that applies pdyn_0004 or pdyn_0006 runs ALTER TABLE / CREATE TABLE / DROP on the SHARED real schema, which
# takes ACCESS EXCLUSIVE locks and can deadlock with other xdist workers (CI shard 1 saw it
# on the crew SQL tests). It leaves the pool and runs in the `-p no:xdist` serial_ddl step.
@pytest.mark.serial_ddl
def test_domain_words_render_the_owner_curated_list_from_the_table():
    with pg_session() as db:
        _seeded(db)
        assert pv.render_value(db, "domain_words") == _owner("stock, incoming, ETA", " - in any language")


@pytest.mark.serial_ddl
def test_statuses_render_only_the_rows_tagged_for_the_bullets():
    with pg_session() as db:
        _seeded(db)
        out = pv.render_value(db, "statuses")
        assert '"outstanding"' in out and '"delivered"' in out
        assert "so_outstanding" not in out and "sales_report" not in out


@pytest.mark.serial_ddl
def test_status_values_and_status_field_values_are_the_owner_subsets():
    with pg_session() as db:
        _seeded(db)
        assert pv.render_value(db, "status_values") == "outstanding|delivered"
        assert pv.render_value(db, "status_field_values") == "outstanding|delivered|sales_report"
        assert pv.render_value(db, "order_status_values").startswith("outstanding|delivered|so_outstanding")


@pytest.mark.serial_ddl
def test_with_the_seeds_the_owner_file_swaps_these_lists_and_still_renders_byte_identical():
    source = SNAPSHOT.read_text(encoding="utf-8")
    with pg_session() as db:
        _seeded(db)
        # The 1 Oct file's entity-kind block predates the five REF-PROBES kinds (#1569).
        db.execute(text("DELETE FROM chatbot_entity_kinds WHERE kind = ANY(:k)"), {"k": REFERENCE_KINDS})
        pv.clear_cache()
        # The statuses bullets match only when the rows carry the owner's words (the
        # sandbox may hold a hand-test word on a sales row, which is untagged anyway).
        template, report = pv.identical_wording_layer(source, db)
        replaced = {(r["variable"], r["line"]) for r in report if r["action"] == "replaced"}
        assert {
            ("domain_words", 87),
            ("statuses", 643),
            ("status_values", 770),
            ("status_field_values", 778),
            ("order_status_values", 821),
            ("teams", 773),
            ("entity_kinds_detail", 1677),
        } <= replaced
        values = {
            n: pv.render_value(db, n) for n in ai_prompt_registry.extract_tokens(template) & set(pv.VARIABLE_NAMES)
        }
        assert ai_prompt_registry._substitute(template, values) == source


@pytest.mark.serial_ddl
def test_the_migration_is_idempotent_and_never_overrides_an_owner_edit():
    mod = _migration()
    with pg_session() as db:
        mod.apply(db.connection())
        db.execute(text("UPDATE chatbot_status_words SET prompt_lists = '{statuses}' WHERE value = 'delivered'"))
        db.execute(text("UPDATE chatbot_domain_words SET sort_order = 999 WHERE word = 'GRN'"))
        count = db.execute(text("SELECT count(*) FROM chatbot_domain_words")).scalar()
        mod.apply(db.connection())
        assert db.execute(text("SELECT prompt_lists FROM chatbot_status_words WHERE value = 'delivered'")).scalar() == [
            "statuses"
        ]
        assert db.execute(text("SELECT sort_order FROM chatbot_domain_words WHERE word = 'GRN'")).scalar() == 999
        assert db.execute(text("SELECT count(*) FROM chatbot_domain_words")).scalar() == count


def test_the_seed_words_are_the_owner_words_in_his_order():
    owner = _owner("stock, incoming, ETA", " - in any language")
    assert list(_migration().DOMAIN_WORDS) == [w.strip() for w in owner.replace("\n", " ").split(",")]


# --------------------------------------------------------------------------- #
# Owner answer 6 (2 Oct 2026), access levels: APPROVED - order the active access types
# as his text lists them (`pdyn_0005_access_level_order`). Agents and the master_products
# narrowing are on hold.
# --------------------------------------------------------------------------- #

OWNER_ACCESS_LEVELS = [
    "Sorento Dealer", "Mocha Dealer", "Mocha Office", "Cabana Dealer", "Cabana Office", "End User", "Sorento Office",
]


def _order_migration():
    path = BACKEND / "alembic" / "versions" / "pdyn_0005_access_level_order.py"
    spec = importlib.util.spec_from_file_location("_t_pdyn_0005", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_access_level_order_step_renders_the_owner_list_and_inserts_nothing():
    with pg_session() as db:
        db.execute(text("UPDATE contact_access_types SET is_active = false"))
        for i, name in enumerate(reversed(OWNER_ACCESS_LEVELS)):
            db.execute(
                text(
                    "INSERT INTO contact_access_types (code, name, is_active, sort_order, keywords, created_at, updated_at) "
                    "VALUES (:c, :n, true, :s, '[]', now(), now())"
                ),
                {"c": f"zzt{i}", "n": name, "s": None},
            )
        db.flush()
        count = db.execute(text("SELECT count(*) FROM contact_access_types")).scalar()
        _order_migration().apply(db.connection())
        pv.clear_cache()
        assert pv.render_value(db, "access_levels") == _owner('["Sorento Dealer"', "\n")
        assert db.execute(text("SELECT count(*) FROM contact_access_types")).scalar() == count


def _put_levels(db, orders: dict[str, int | None]) -> None:
    db.execute(text("UPDATE contact_access_types SET is_active = false"))
    for i, name in enumerate(OWNER_ACCESS_LEVELS):
        db.execute(
            text(
                "INSERT INTO contact_access_types (code, name, is_active, sort_order, keywords, created_at, updated_at) "
                "VALUES (:c, :n, true, :s, '[]', now(), now())"
            ),
            {"c": f"zzt{i}", "n": name, "s": orders[name]},
        )
    db.flush()


def _orders(db) -> dict[str, int | None]:
    rows = db.execute(text("SELECT name, sort_order FROM contact_access_types WHERE code LIKE 'zzt%'")).all()
    return {n: s for n, s in rows}


def test_the_access_level_order_step_never_overwrites_a_set_sort_order():
    with pg_session() as db:
        original = {name: 100 + i for i, name in enumerate(reversed(OWNER_ACCESS_LEVELS))}
        _put_levels(db, original)
        _order_migration().apply(db.connection())
        assert _orders(db) == original


def test_the_access_level_order_step_fills_only_the_null_sort_orders():
    with pg_session() as db:
        original: dict[str, int | None] = {
            name: (None if i % 2 == 0 else 100 + i) for i, name in enumerate(reversed(OWNER_ACCESS_LEVELS))
        }
        _put_levels(db, original)
        _order_migration().apply(db.connection())
        after = _orders(db)
        for name, before in original.items():
            if before is not None:
                assert after[name] == before, name
            else:
                assert after[name] is not None, name
                assert after[name] == OWNER_ACCESS_LEVELS.index(name) + 1, name


def test_the_access_level_order_step_leaves_inactive_rows_alone():
    with pg_session() as db:
        db.execute(
            text(
                "INSERT INTO contact_access_types (code, name, is_active, sort_order, keywords, created_at, updated_at) "
                "VALUES ('zztoff', 'Sorento Dealer', false, 77, '[]', now(), now())"
            )
        )
        _order_migration().apply(db.connection())
        assert db.execute(text("SELECT sort_order FROM contact_access_types WHERE code = 'zztoff'")).scalar() == 77


# --------------------------------------------------------------------------- #
# Owner Q-B (2 Oct 2026) = (b): `access_agents.in_parser_prompt`, seeded for his five
# agents; `{{agents}}` renders the flagged active rows only (`pdyn_0006_agents_in_prompt`).
# Kept simple: lane ACCESS-MODEL will later turn agents into role presets.
# --------------------------------------------------------------------------- #

OWNER_AGENTS = ["general_enquiries", "order_enquiries", "incoming_stock_enquiries", "marketing_form", "it_support"]


def _agents_migration():
    path = BACKEND / "alembic" / "versions" / "pdyn_0006_agents_in_prompt.py"
    spec = importlib.util.spec_from_file_location("_t_pdyn_0006", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _put_agents(db, codes: list[str], active: bool = True) -> None:
    for code in codes:
        db.execute(
            text(
                "INSERT INTO access_agents (id, code, name, is_active, assign_to_new_internal_contacts, "
                "synced_to_excel, created_at, updated_at) VALUES (gen_random_uuid(), :c, :c, :a, false, false, now(), now()) "
                "ON CONFLICT (code) DO UPDATE SET is_active = :a"
            ),
            {"c": code, "a": active},
        )


@pytest.mark.serial_ddl
def test_agents_render_the_flagged_rows_only_and_give_the_owner_line():
    mod = _agents_migration()
    with pg_session() as db:
        mod.apply(db.connection())
        db.execute(text("UPDATE access_agents SET in_parser_prompt = false"))
        _put_agents(db, OWNER_AGENTS + ["ideation", "complaint", "purchase_request"])
        mod.apply(db.connection())
        pv.clear_cache()
        assert pv.render_value(db, "agents") == "|".join(OWNER_AGENTS)
        owner_line = _owner('"suggested_agent": "', "\n")
        assert '"suggested_agent": "' + pv.render_value(db, "agents") + '"' in owner_line


@pytest.mark.serial_ddl
def test_an_inactive_flagged_agent_stays_out():
    mod = _agents_migration()
    with pg_session() as db:
        mod.apply(db.connection())
        db.execute(text("UPDATE access_agents SET in_parser_prompt = false"))
        _put_agents(db, OWNER_AGENTS)
        mod.apply(db.connection())
        db.execute(text("UPDATE access_agents SET is_active = false WHERE code = 'it_support'"))
        pv.clear_cache()
        assert "it_support" not in pv.render_value(db, "agents").split("|")


@pytest.mark.serial_ddl
def test_the_agents_seed_never_overrides_an_owner_edit():
    mod = _agents_migration()
    with pg_session() as db:
        mod.apply(db.connection())
        _put_agents(db, OWNER_AGENTS)
        db.execute(text("UPDATE access_agents SET in_parser_prompt = false"))
        db.execute(text("UPDATE access_agents SET in_parser_prompt = true WHERE code = 'order_enquiries'"))
        mod.apply(db.connection())
        flagged = set(db.execute(text("SELECT code FROM access_agents WHERE in_parser_prompt")).scalars())
        assert flagged == {"order_enquiries"}
