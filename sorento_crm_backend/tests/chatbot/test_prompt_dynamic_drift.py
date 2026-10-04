"""PROMPT-DYNAMIC R4 drift test + the wording-layer migration (UAC AC-PD-3, AC-PD-6,
AC-PD-7).

Fails when:
- a list the parser receives diverges from the registry it is rendered from;
- a version published at or after the wording layer carries a registry list as literal
  text again (a code-constant republish would do exactly that), beyond the lists the
  wording layer itself reported it had to keep literal;
- a publish path rebuilds or promotes over the owner's wording.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import uuid

import pytest
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip
from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services import ai_prompt_registry, chatbot_prompt_vars as pv
from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT
from tests._pg_fixture import pg_session

KEY = "chatbot_semantic_parser"
VERSIONS = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions"
ACCESS_LEVELS = [
    "Sorento Dealer", "Mocha Dealer", "Mocha Office", "Cabana Dealer", "Cabana Office",
    "End User", "Sorento Office",
]


def _load(filename: str):
    spec = importlib.util.spec_from_file_location(f"_t_{filename}", VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _fresh_cache():
    pv.clear_cache()
    ai_prompt_registry.bust_cache()
    yield
    pv.clear_cache()
    ai_prompt_registry.bust_cache()


def _seed_access_levels(db) -> None:
    db.execute(text("DELETE FROM contact_access_types WHERE code LIKE 'zzt%'"))
    have = set(db.execute(text("SELECT name FROM contact_access_types WHERE is_active")).scalars())
    for i, name in enumerate(n for n in ACCESS_LEVELS if n not in have):
        db.execute(
            text(
                "INSERT INTO contact_access_types (code, name, is_active, sort_order, keywords, created_at, updated_at) "
                "VALUES (:c, :n, true, :s, '[]', now(), now())"
            ),
            {"c": f"zzt{i}", "n": name, "s": 100 + i},
        )


def _production(db) -> AIPromptVersion:
    return (
        db.query(AIPromptVersion)
        .join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
        .filter(AIPromptLabel.name == KEY, AIPromptLabel.label == "production")
        .one()
    )


def _wording_layer_rows(db) -> list[AIPromptVersion]:
    return [
        r
        for r in db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).order_by(AIPromptVersion.version)
        if pv.is_wording_layer(r.template)
    ]


def _render(db, template: str) -> str:
    row = AIPromptVersion(id=str(uuid.uuid4()), name=KEY, version=90000 + uuid.uuid4().int % 9999,
                          template=template, commit_message="t", config_json={})
    db.add(row)
    db.flush()
    out, _ = ai_prompt_registry.render(db, KEY, current_date="TODAY", override_version_id=row.id)
    return out


# --------------------------------------------------------------------------- #
# The migration (AC-PD-6)
# --------------------------------------------------------------------------- #


def test_the_wording_layer_is_published_unlabelled_from_production():
    with pg_session() as db:
        rows = _wording_layer_rows(db)
        assert rows, "pdyn_0002_wording_layer published no version"
        first = rows[0]
        # Published with no label (the owner promotes). The migration run itself, and that it
        # leaves `production` where it was, is `test_prompt_dynamic_review_round::test_b1_*`.
        assert (first.config_json or {}).get("from_version") is not None
        report = (first.config_json or {}).get("wording_layer_report") or []
        assert any("{{domains}}" in line for line in report)
        assert any("{{statuses}}" in line for line in report)


def test_the_migration_is_idempotent():
    mod = _load("pdyn_0002_wording_layer.py")
    with pg_session() as db:
        before = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count()
        assert mod.apply(db.connection()) is None
        assert db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count() == before


def test_wording_layer_keeps_the_owner_text_verbatim_outside_the_lists():
    with pg_session() as db:
        _seed_access_levels(db)
        owner = _production(db).template.replace(
            "== MESSAGE TYPE ==", "== MESSAGE TYPE ==\nOWNER EDIT: keep this line exactly."
        )
        out, _report = pv.wording_layer(owner, db)
        assert "OWNER EDIT: keep this line exactly." in out
        # Outside the policy blocks (which become their live variables), the only lines
        # that change are the ones holding a replaced list.
        from app.services.chatbot_parser_prompt import BLOCKS_BEGIN

        body = owner.split(BLOCKS_BEGIN, 1)[0]
        changed = [line for line in body.splitlines() if line not in out]
        list_markers = ("ONE of:", '"order_status":', '"status":', '"suggested_team":',
                        '"suggested_agent":', '"hint": "product|', "The full set is now:",
                        '["Sorento Dealer"', "STATUS word -", '  - "', "    \"", "price, spec", "stock, incoming, ETA")
        unexpected = [line for line in changed if not any(m in line for m in list_markers)
                      and not line.startswith("    ")]
        assert unexpected == [], unexpected


# --------------------------------------------------------------------------- #
# Drift (AC-PD-7): what the parser receives equals the registries
# --------------------------------------------------------------------------- #


def test_every_rendered_list_equals_its_registry():
    with pg_session() as db:
        _seed_access_levels(db)
        wording, report = pv.wording_layer(_production(db).template, db)
        assert pv.literal_lists(wording) == [], report
        out = _render(db, wording)

        def one(pattern: str) -> str:
            m = re.search(pattern, out)
            assert m, pattern
            return m.group(1)

        v = lambda name: pv.render_value(db, name)  # noqa: E731
        assert one(r"domain_hint = ONE of: (.*?) \| null") == v("domains")
        assert one(r'"order_status": "(.*?)\|null') == v("status_values")
        assert one(r'"status": "(.*?)\|null') == v("status_field_values")  # its own list (review 3, S1)
        assert one(r'"suggested_team": "(.*?)"') == v("teams")
        assert one(r'"suggested_agent": "(.*?)"') == v("agents")
        assert one(r'"hint": "([a-z_|]+)"') == v("entity_kinds")
        assert one(r"drawn\s+ONLY from:\s*\n(\[.*?\])") == v("access_levels")
        assert v("statuses") in out
        assert v("domains_detail") in out
        for name in pv.VARIABLE_NAMES:
            assert "{{" + name + "}}" not in out


def test_the_rendered_lists_follow_a_registry_change_without_a_publish():
    with pg_session() as db:
        wording, _ = pv.wording_layer(_production(db).template, db)
        before = _render(db, wording)
        db.execute(text("UPDATE chatbot_status_words SET trigger_words = trigger_words || '{zzt drift word}'::text[] WHERE value = 'outstanding'"))
        pv.clear_cache()
        after = _render(db, wording)
        assert '"zzt drift word"' not in before and '"zzt drift word"' in after


def test_no_version_after_the_wording_layer_carries_a_literal_registry_list():
    with pg_session() as db:
        rows = _wording_layer_rows(db)
        assert rows
        first = rows[0]
        kept = {
            line.split(":", 1)[0]
            for line in (first.config_json or {}).get("wording_layer_report") or []
            if "kept literal" in line
        }
        allowed = {"access_levels"} if any(k.startswith("access levels") for k in kept) else set()
        later = (
            db.query(AIPromptVersion)
            .filter(AIPromptVersion.name == KEY, AIPromptVersion.version >= first.version)
            .all()
        )
        for row in later:
            # The owner's production snapshot (`pdyn_0003_prod_identical`) keeps a list
            # literal wherever the registry does not reproduce his text, and says so.
            own = {
                r["variable"]
                for r in (row.config_json or {}).get("identical_report") or []
                if str(r.get("action", "")).startswith("kept literal")
            }
            if "*" in own:  # the proof failed and the owner's text went in verbatim
                continue
            assert set(pv.literal_lists(row.template)) <= allowed | own, (
                f"v{row.version} carries registry list(s) as literal text: "
                f"{sorted(set(pv.literal_lists(row.template)) - allowed)}. Publish wording "
                f"changes with chatbot_prompt_vars.publish_wording_edit, never the code constant."
            )


def test_the_code_constant_lists_are_all_recognised_by_the_transform():
    """A new hand list shape in `SEMANTIC_PARSER_PROMPT` that the transform does not know
    would survive into the wording layer as literal text."""
    with pg_session() as db:
        _seed_access_levels(db)
        wording, report = pv.wording_layer(SEMANTIC_PARSER_PROMPT, db)
        assert pv.literal_lists(wording) == [], report


# --------------------------------------------------------------------------- #
# Publish paths never overwrite or promote over the wording (AC-PD-3, R3)
# --------------------------------------------------------------------------- #


def test_s4_publish_and_s12_promote_stand_down_once_the_wording_layer_exists():
    s12 = _load("chatbot_rearch_s12.py")
    with pg_session() as db:
        count = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count()
        production = _production(db).id
        s12.republish_and_promote(db.connection())
        db.expire_all()
        assert db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count() == count
        assert _production(db).id == production


def test_publish_wording_edit_applies_one_edit_to_production_unlabelled():
    with pg_session() as db:
        prod = _production(db)
        version = pv.publish_wording_edit(
            db, old="== MESSAGE TYPE ==", new="== MESSAGE TYPE (edited) ==", message="t"
        )
        assert version is not None
        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == version).one()
        assert row.template == prod.template.replace("== MESSAGE TYPE ==", "== MESSAGE TYPE (edited) ==", 1)
        assert _production(db).id == prod.id
        assert pv.publish_wording_edit(db, old="NOT IN THE TEXT zzt", new="x", message="t") is None


def test_the_stale_banner_is_quiet_for_a_wording_layer_production():
    from app.api.v1.system.chatbot import prompt_blocks_status

    with pg_session() as db:
        layer = _wording_layer_rows(db)[0]
        db.query(AIPromptLabel).filter(AIPromptLabel.name == KEY, AIPromptLabel.label == "production").update(
            {"version_id": layer.id}
        )
        db.flush()
        db.execute(text("UPDATE chatbot_domains SET switch_words = switch_words || '{zzt}'::text[] WHERE name = 'order'"))
        assert prompt_blocks_status(current_user={}, db=db).stale is False


# `serial_ddl`: this test (drops chatbot_status_words) runs ALTER TABLE / CREATE TABLE / DROP on the SHARED real schema, which
# takes ACCESS EXCLUSIVE locks and can deadlock with other xdist workers (CI shard 1 saw it
# on the crew SQL tests). It leaves the pool and runs in the `-p no:xdist` serial_ddl step.
@pytest.mark.serial_ddl
def test_the_seed_works_on_a_table_built_from_the_model():
    """CI (run 36797594354): `scripts.bootstrap_env` builds `chatbot_status_words` with
    `create_all` from the ORM model, whose `id` default is Python-side only, so the
    migration's `CREATE TABLE IF NOT EXISTS` is skipped and its seed INSERT must name the
    id itself or it fails with `null value in column "id"`."""
    from app.models.chatbot_policy import ChatbotStatusWord

    mod = _load("pdyn_0001_status_words_sales.py")
    with pg_session() as db:
        conn = db.connection()
        conn.execute(text("DROP TABLE chatbot_status_words"))
        ChatbotStatusWord.__table__.create(conn)
        mod.apply(conn)
        assert conn.execute(text("SELECT count(*) FROM chatbot_status_words")).scalar() == 8
