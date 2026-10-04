"""PARSER-PER-AUDIENCE, owner ruling (a): the tagged parser version is built at migration
time FROM the template the `production` label points at, so it keeps every #1405
`{{variable}}`, carries the #1470 LOW STOCK REPORT FILTERS block once, and the registry
list variables drop the rows a contact's missing grants gate.

Red tests, written before the coder. Interfaces assumed (see the lane report):
  * `ppa_0001_tagged_parser.apply(bind) -> int | None` and `.remove(bind)` (existing names),
    now reading the production-labelled template instead of the static tagged file.
  * `parser.resolve_config(db, current_date=..., grants=...)` renders the list variables
    for that audience.
  * `PromptGate.words: tuple[str, ...]` on each `PROMPT_GATES` row: the domain words that
    name that gate's domain.

Postgres only. The migration tests use the rolled-back `pg_session`; the render tests use
the blank-schema `session_factory` and seed their own registry rows.

No em or en dashes.
"""
from __future__ import annotations

import importlib.util
import logging
import pathlib
import re
import uuid

import pytest
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip
from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services import ai_prompt_registry, chatbot_prompt_vars as pv
from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot.prompt_gates import PROMPT_GATES, render_for_audience
from app.services.chatbot_parser_prompt import LOW_STOCK_FILTERS_ADDENDUM
from tests._pg_fixture import blank_session as pg_session
from tests.chatbot.test_parser_audience_render import ALL_GRANTS

KEY = "chatbot_semantic_parser"
BACKEND = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.prod-20261001.txt"
LOW_BLOCK = LOW_STOCK_FILTERS_ADDENDUM.strip("\n")
VAR_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
C, P, S, L = ALL_GRANTS


@pytest.fixture(autouse=True)
def _fresh_cache():
    pv.clear_cache()
    ai_prompt_registry.bust_cache()
    yield
    pv.clear_cache()
    ai_prompt_registry.bust_cache()


def _migration():
    path = BACKEND / "alembic" / "versions" / "ppa_0001_tagged_parser.py"
    spec = importlib.util.spec_from_file_location("_ppa_tv", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production_template() -> str:
    """The owner's text the way pdyn_0003 leaves it: every registry list span swapped for
    its `{{variable}}` (forced, so the test does not depend on the local registry data),
    plus the ACCOUNT-LEDGER block. It carries NO low stock filters block."""
    text_ = SNAPSHOT.read_text(encoding="utf-8")
    for name, pattern in pv._IDENTICAL_CANDIDATES:
        m = re.search(pattern, text_, flags=re.S)
        if m:
            text_ = text_[: m.start("list")] + "{{" + name + "}}" + text_[m.end("list") :]
    text_ = pv.with_account_block(text_)
    assert "{{domains}}" in text_ and "{{domain_words}}" in text_
    assert LOW_BLOCK not in text_
    return text_


def _put_version(db, template: str) -> AIPromptVersion:
    top = db.execute(text("SELECT COALESCE(max(version), 0) FROM ai_prompt_versions WHERE name = :n"), {"n": KEY}).scalar()
    row = AIPromptVersion(id=str(uuid.uuid4()), name=KEY, version=int(top) + 1, template=template,
                          commit_message="t", config_json={})
    db.add(row)
    db.flush()
    return row


def _point_production_at(db, row: AIPromptVersion) -> None:
    updated = db.execute(
        text("UPDATE ai_prompt_labels SET version_id = :v WHERE name = :n AND label = 'production'"),
        {"v": row.id, "n": KEY},
    ).rowcount
    if not updated:
        db.add(AIPromptLabel(name=KEY, label="production", version_id=row.id))
    db.flush()


def _labels(db) -> list[tuple]:
    return sorted(
        db.execute(text("SELECT label, version_id::text FROM ai_prompt_labels WHERE name = :n"), {"n": KEY}).all()
    )


def _count(db) -> int:
    return db.execute(text("SELECT count(*) FROM ai_prompt_versions WHERE name = :n"), {"n": KEY}).scalar()


def _seed_and_apply(db, template: str):
    mod = _migration()
    mod.remove(db.connection())
    prod = _put_version(db, template)
    _point_production_at(db, prod)
    labels = _labels(db)
    version = mod.apply(db.connection())
    return mod, prod, labels, version


# --------------------------------------------------------------------------- #
# The migration builds from the production-labelled template
# --------------------------------------------------------------------------- #


def test_the_published_text_keeps_every_variable_and_carries_the_low_stock_block_once():
    """The owner's required test: BOTH a #1405 variable and the low-stock block."""
    source = _production_template()
    with pg_session() as db:
        _, _, labels, version = _seed_and_apply(db, source)
        assert version is not None, "apply must publish a tagged version from the production template"
        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == version).one()
        assert "{{domains}}" in row.template and "{{domain_words}}" in row.template
        assert LOW_BLOCK in row.template
        assert row.template.count("LOW STOCK REPORT FILTERS") == 1
        assert row.template.count(LOW_BLOCK) == 1
        assert _labels(db) == labels, "no label may move"


def test_every_source_variable_survives():
    source = _production_template()
    wanted = set(VAR_RE.findall(source))
    assert len(wanted) >= 3, wanted
    with pg_session() as db:
        _, _, _, version = _seed_and_apply(db, source)
        assert version is not None
        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == version).one()
        assert wanted <= set(VAR_RE.findall(row.template)), wanted - set(VAR_RE.findall(row.template))


def test_markers_are_inserted_and_the_full_audience_render_keeps_the_source_lines():
    source = _production_template()
    with pg_session() as db:
        _, _, _, version = _seed_and_apply(db, source)
        assert version is not None
        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == version).one()
        tags = set(re.findall(r"\{\{#only (\w+)\}\}", row.template))
        assert tags == {g.tag for g in PROMPT_GATES}, tags
        full = render_for_audience(row.template, list(ALL_GRANTS))
        assert "{{#only" not in full and "{{/only}}" not in full
        got = set(full.splitlines())
        assert [ln for ln in source.splitlines() if ln not in got] == [], "a source line was lost"
        bare = render_for_audience(row.template, [])
        assert "LOW STOCK REPORT FILTERS" not in bare
        assert "== LOW STOCK REPORT" not in bare and "== SALES REPORT" not in bare
        assert "{{domains}}" in bare


def test_second_run_writes_nothing():
    source = _production_template()
    with pg_session() as db:
        mod, _, labels, first = _seed_and_apply(db, source)
        assert first is not None
        before = _count(db)
        assert mod.apply(db.connection()) is None
        assert _count(db) == before
        assert _labels(db) == labels


def test_a_template_with_no_anchors_writes_nothing_and_logs(caplog):
    with pg_session() as db:
        with caplog.at_level(logging.WARNING):
            mod, _, labels, version = _seed_and_apply(db, "HEAD {{current_date}} {{domains}} {{domain_words}}\nnothing gated here\n")
        assert version is None
        assert caplog.records, "fail closed must log"
        assert _labels(db) == labels
        assert db.execute(
            text("SELECT count(*) FROM ai_prompt_versions WHERE name = :n AND template LIKE '%{{#only%'"), {"n": KEY}
        ).scalar() == 0


@pytest.mark.parametrize(
    "title",
    ["PURCHASE ORDERS", "SPO LAST RECEIPT", "LAST PURCHASE COST", "LOW STOCK REPORT", "SALES REPORT", "SALES ANALYSIS"],
)
def test_one_missing_gated_anchor_writes_nothing(title):
    source = _production_template()
    assert f"== {title}" in source
    broken = source.replace(f"== {title}", f"== RENAMED {title}")
    with pg_session() as db:
        _, _, labels, version = _seed_and_apply(db, broken)
        assert version is None
        assert _labels(db) == labels


# --------------------------------------------------------------------------- #
# Registry list variables render per audience
# --------------------------------------------------------------------------- #

DOMAINS = [
    ("purchase_cost", [], ["purchase cost"]),
    ("purchase_order", [], ["PO"]),
    ("spo_allocation", [], ["SPO"]),
    ("sales", [], ["sales report"]),
    ("inventory", ["check_stock", "low_stock_report"], ["stock"]),
    ("order", ["check_order"], ["delivery"]),
]
STATUSES = ["outstanding", "sales_report", "sales_analysis", "top_selling"]
WORDS = ["stock", "PO", "purchase cost", "delivery"]
LIST_TEMPLATE = (
    "HEAD {{current_date}}\nDOMAINS: {{domains}}\nWORDS: {{domain_words}}\n"
    "STATUS: {{status_values}}\nDETAIL:\n{{domains_detail}}\nTAIL\n"
)


def _seed_registry(db) -> None:
    for i, (name, intents, switch) in enumerate(DOMAINS):
        db.execute(
            text(
                "INSERT INTO chatbot_domains (id, name, label, intents, switch_words, sort_order) "
                "VALUES (gen_random_uuid(), :n, :n, :i, :w, :o)"
            ),
            {"n": name, "i": intents, "w": switch, "o": i},
        )
    for i, value in enumerate(STATUSES):
        db.execute(
            text(
                "INSERT INTO chatbot_status_words (id, domain, value, label, prompt_lists, sort_order) "
                "VALUES (gen_random_uuid(), 'order', :v, :v, ARRAY['status_values'], :o)"
            ),
            {"v": value, "o": i},
        )
    for i, word in enumerate(WORDS):
        db.execute(
            text("INSERT INTO chatbot_domain_words (id, word, sort_order) VALUES (gen_random_uuid(), :w, :o)"),
            {"w": word, "o": i},
        )
    db.commit()


def _config_prompt(session_factory, monkeypatch, grants) -> str:
    from tests.chatbot.test_parser_audience_wiring import _publish

    monkeypatch.setattr("app.services.llm_provider.resolve_api_key", lambda cfg, provider: "sk-test")
    db = session_factory()
    if not db.execute(text("SELECT 1 FROM chatbot_domains LIMIT 1")).first():
        _seed_registry(db)
    _publish(session_factory, LIST_TEMPLATE)
    pv.clear_cache()
    cfg = parser_mod.resolve_config(session_factory(), current_date="D", grants=grants)
    return cfg.system_prompt


def _has(prompt: str, word: str) -> bool:
    return re.search(r"(?<![A-Za-z_])" + re.escape(word) + r"(?![A-Za-z_])", prompt) is not None


def test_no_grants_keeps_every_gated_registry_row(session_factory, monkeypatch):
    # Owner ruling 5 Oct 2026 (was test_no_grants_drops_every_gated_registry_row, which
    # asserted every gated row ABSENT): the list variables keep the classification
    # vocabulary for a contact with no grant.
    out = _config_prompt(session_factory, monkeypatch, [])
    for gated in ("purchase_cost", "purchase_order", "spo_allocation", "sales", "sales_report",
                  "sales_analysis", "top_selling", "PO", "purchase cost"):
        assert _has(out, gated), (gated, out)
    for kept in ("inventory", "order", "stock", "delivery", "outstanding", "check_stock"):
        assert _has(out, kept), (kept, out)
    assert "{{" not in out


@pytest.mark.parametrize("grant", [C, P, S, L], ids=["cost", "placed", "sales_report", "low_stock"])
def test_one_grant_renders_the_same_lists_as_no_grant(session_factory, monkeypatch, grant):
    # Owner ruling 5 Oct 2026 (was test_one_grant_restores_only_its_rows, which asserted
    # the OTHER grants' rows absent): no per-audience row filtering, so one grant renders
    # the same list lines as none. `{{domains_detail}}` (after "DETAIL:") may differ.
    bare = _config_prompt(session_factory, monkeypatch, [])
    out = _config_prompt(session_factory, monkeypatch, [grant])
    assert out.split("DETAIL:")[0] == bare.split("DETAIL:")[0]


def test_all_four_grants_is_exactly_todays_render(session_factory, monkeypatch):
    out = _config_prompt(session_factory, monkeypatch, list(ALL_GRANTS))
    plain, _ = ai_prompt_registry.render(session_factory(), KEY, current_date="D")
    assert out == render_for_audience(plain, list(ALL_GRANTS))
    for word in ("sales_report", "low_stock_report", "PO", "purchase cost", "spo_allocation"):
        assert _has(out, word), word


def test_every_domain_word_naming_a_gated_domain_is_mapped():
    mapped = {}
    for gate in PROMPT_GATES:
        for word in gate.words:
            mapped[word] = gate.domain
    assert mapped.get("PO") == "purchase_order", mapped
    assert mapped.get("purchase cost") == "purchase_cost", mapped
    assert mapped.get("SPO") == "spo_allocation", mapped
