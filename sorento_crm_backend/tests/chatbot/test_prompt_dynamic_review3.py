"""PROMPT-DYNAMIC reviewer pass 3 (2 Oct 2026, owner-answers round).

B1: `alembic upgrade head` on a database that predates this lane must not crash. The
wording-layer migrations (pdyn_0002, pdyn_0003) call the renderers, which now read
`chatbot_status_words.prompt_lists` and `chatbot_domain_words`, so `pdyn_0004_prompt_lists`
must run before them IN ALEMBIC ORDER (bootstrap_env hid this by calling it first).

S1: the wording-layer version (pdyn_0002) must lose no value: each pipe list goes to the
variable that renders that line's own list.

S2: the `domain_words` source is the curated table, which no admin page edits yet.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip
from app.services import ai_prompt_registry, chatbot_prompt_vars as pv
from tests._pg_fixture import pg_session

BACKEND = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.prod-20261001.txt"
KEY = "chatbot_semantic_parser"


@pytest.fixture(autouse=True)
def _fresh_cache():
    pv.clear_cache()
    ai_prompt_registry.bust_cache()
    yield
    pv.clear_cache()
    ai_prompt_registry.bust_cache()


def _pdyn_in_upgrade_order() -> list[str]:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    order = [rev.revision for rev in script.walk_revisions("base", "heads")][::-1]
    return [r for r in order if r.startswith("pdyn_")]


def _load(revision: str):
    path = BACKEND / "alembic" / "versions" / f"{revision}.py"
    spec = importlib.util.spec_from_file_location(f"_t_{revision}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# `serial_ddl`: this test (drops and re-adds the lane's columns and table) runs ALTER TABLE / CREATE TABLE / DROP on the SHARED real schema, which
# takes ACCESS EXCLUSIVE locks and can deadlock with other xdist workers (CI shard 1 saw it
# on the crew SQL tests). It leaves the pool and runs in the `-p no:xdist` serial_ddl step.
@pytest.mark.serial_ddl
def test_the_lane_migrations_run_in_alembic_order_on_a_database_without_the_new_column_or_table():
    order = _pdyn_in_upgrade_order()
    assert order.index("pdyn_0004_prompt_lists") < order.index("pdyn_0002_wording_layer")
    assert order.index("pdyn_0006_agents_in_prompt") < order.index("pdyn_0002_wording_layer")
    with pg_session() as db:
        conn = db.connection()
        conn.execute(text("ALTER TABLE chatbot_status_words DROP COLUMN prompt_lists"))
        conn.execute(text("DROP TABLE chatbot_domain_words"))
        conn.execute(text("ALTER TABLE access_agents DROP COLUMN IF EXISTS in_parser_prompt"))
        # Neither wording-layer version exists yet, and production is the owner's text.
        # A wording-layer version, as `pv.is_wording_layer` reads one: the add-on variables
        # (`pv.ADDON_VARIABLES`) ride on the code constant, production included.
        skip = "|".join(["current_date", *sorted(pv.ADDON_VARIABLES)])
        tokened = f"SELECT id FROM ai_prompt_versions WHERE name = :n AND template ~ '[{{][{{](?!({skip})[}}])'"
        conn.execute(text(f"DELETE FROM ai_prompt_labels WHERE version_id IN ({tokened})"), {"n": KEY})
        conn.execute(text(f"DELETE FROM ai_prompt_versions WHERE id IN ({tokened})"), {"n": KEY})
        vid = conn.execute(
            text(
                "INSERT INTO ai_prompt_versions (id, name, version, type, template, variables, created_at) "
                "VALUES (gen_random_uuid(), :n, (SELECT COALESCE(max(version), 0) + 1 FROM ai_prompt_versions WHERE name = :n), "
                "'text', :t, '[\"current_date\"]', now()) RETURNING id"
            ),
            {"n": KEY, "t": SNAPSHOT.read_text(encoding="utf-8")},
        ).scalar()
        conn.execute(
            text("UPDATE ai_prompt_labels SET version_id = :v WHERE name = :n AND label = 'production'"),
            {"v": vid, "n": KEY},
        )
        for revision in order:
            pv.clear_cache()
            _load(revision).apply(conn)
        row = conn.execute(
            text("SELECT config_json FROM ai_prompt_versions WHERE name = :n AND config_json ? 'prod_snapshot_sha256'"),
            {"n": KEY},
        ).scalar()
        assert row is not None, "pdyn_0003 published nothing"
        replaced = {r["variable"] for r in row["identical_report"] if r["action"] == "replaced"}
        assert {"statuses", "status_values", "status_field_values", "order_status_values", "domain_words"} <= replaced


def _values_on(rendered: str, pattern: str) -> set[str]:
    m = re.search(pattern, rendered)
    assert m, pattern
    return set(m.group(1).split("|"))


@pytest.mark.parametrize(
    "pattern",
    [
        r'"order_status": "([a-z_|]+?)\|null',
        r'"status": "([a-z_|]+?)\|null',
        r"The full set is now: ([a-z_|]+?)\|null",
    ],
)
def test_the_wording_layer_loses_no_value_of_the_owner_lists(pattern):
    source = SNAPSHOT.read_text(encoding="utf-8")
    with pg_session() as db:
        template, _report = pv.wording_layer(source, db)
        values = {n: pv.render_value(db, n) for n in ai_prompt_registry.extract_tokens(template) & set(pv.VARIABLE_NAMES)}
        rendered = ai_prompt_registry._substitute(template, values)
        assert _values_on(source, pattern) <= _values_on(rendered, pattern)


def test_the_domain_words_source_is_the_curated_list_with_no_page_yet():
    var = pv.VARIABLES["domain_words"]
    assert var.href == ""
    assert "chatbot_domain_words" in var.tables
    assert "Domain words" in var.source

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
pytestmark = pytest.mark.parser_prompt_rows
