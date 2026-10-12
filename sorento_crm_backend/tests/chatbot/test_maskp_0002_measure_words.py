"""MULTI-ASK-PLAN2 (crew decision, owner agrees, 12 Oct 2026): the sub-ask measure vocabulary
is DATA, rows of the existing glossary `chatbot_status_words` tagged `sub_ask_measures`, which
the parser prompt reads through the prompt-vars variable `{{sub_ask_measures}}`. No code
matches these words; only the parser reads them.

Postgres only, blank schema (the migration is insert-only, no DDL).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

from app.services import chatbot_prompt_vars as pv
from app.services.chatbot_parser_prompt import SUB_ASKS_ADDENDUM

from tests._pg_fixture import blank_session

BACKEND = Path(__file__).resolve().parents[2]
MEASURES = (
    "received_qty", "last_receipt", "incoming", "stock", "supplier",
    "last_cost", "sold_total", "sold_by", "other",
)


def _migration():
    path = BACKEND / "alembic" / "versions" / "maskp_0002_measure_words.py"
    spec = importlib.util.spec_from_file_location("_maskp_0002_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _fresh_cache():
    pv.clear_cache()
    yield
    pv.clear_cache()


def _rows(db) -> dict[str, dict]:
    return {
        r["value"]: dict(r)
        for r in db.execute(
            text(
                "SELECT domain, value, label, trigger_words, prompt_lists FROM chatbot_status_words "
                "WHERE 'sub_ask_measures' = ANY(prompt_lists)"
            )
        ).mappings()
    }


def _seeded(db):
    _migration().apply(db.connection())
    db.flush()
    pv.clear_cache()


def test_revision_chains_onto_maskp_0001():
    m = _migration()
    assert m.revision == "maskp_0002_measure_words"
    assert m.down_revision == "maskp_0001_parser_sub_asks"
    assert len(m.revision) <= 32


def test_every_measure_the_runner_reads_is_a_tagged_row():
    with blank_session() as db:
        _seeded(db)
        rows = _rows(db)
        assert sorted(rows) == sorted(MEASURES)
        # `order_status_values` renders every domain "order" row into ORDER_STATUS lists.
        assert all(r["domain"] != "order" for r in rows.values())
        assert all(r["prompt_lists"] == ["sub_ask_measures"] for r in rows.values())


def test_the_vocabulary_r7_carried_in_the_prompt_is_now_data():
    with blank_session() as db:
        _seeded(db)
        rows = _rows(db)
        assert {"进仓库", "入仓", "收货"} <= set(rows["received_qty"]["trigger_words"])
        assert {"jual", "jualan", "terjual"} <= set(rows["sold_total"]["trigger_words"])
        assert "never goods still on the way" in rows["received_qty"]["label"]
        assert "SPO" in rows["last_receipt"]["label"] and "delivery date" in rows["last_receipt"]["label"]
        assert "GRN" not in rows["last_receipt"]["label"]


def test_the_variable_renders_the_tagged_rows_as_a_glossary():
    with blank_session() as db:
        _seeded(db)
        rendered = pv.render_value(db, "sub_ask_measures")
        assert rendered.splitlines()[0].startswith('  - "received_qty"')
        assert '"进仓库"' in rendered and '"jual"' in rendered
        assert "Domain" not in rendered
        for other_list in ("statuses", "status_values", "status_field_values", "order_status_values"):
            assert "received_qty" not in pv.render_value(db, other_list), other_list


def test_an_owner_edit_survives_a_rerun():
    m = _migration()
    with blank_session() as db:
        _seeded(db)
        db.execute(text("UPDATE chatbot_status_words SET trigger_words = '{own}' WHERE value = 'stock'"))
        _seeded(db)
        assert _rows(db)["stock"]["trigger_words"] == ["own"]
        assert len(_rows(db)) == len(m.MEASURE_WORDS)


def test_the_addendum_reads_the_measures_from_the_variable_and_carries_no_word_list():
    assert "{{sub_ask_measures}}" in SUB_ASKS_ADDENDUM
    assert not any("㐀" <= ch <= "鿿" for ch in SUB_ASKS_ADDENDUM)


def test_the_owner_can_tag_a_row_for_the_list_on_the_status_words_page():
    from app.api.v1.system.chatbot_config import STATUS_PROMPT_LISTS

    assert "sub_ask_measures" in STATUS_PROMPT_LISTS


def test_measure_words_never_leak_into_the_domain_words_fallback():
    with blank_session() as db:
        _seeded(db)
        db.execute(text("DELETE FROM chatbot_domain_words"))
        pv.clear_cache()
        assert "进仓库" not in pv.render_value(db, "domain_words")


def test_a_value_the_owner_already_uses_is_named_never_silently_skipped(caplog):
    """Review S2: an existing untagged `stock` row keeps the owner's row, and the seed says
    that measure is missing from {{sub_ask_measures}}."""
    import logging

    m = _migration()
    with blank_session() as db:
        db.execute(
            text(
                "INSERT INTO chatbot_status_words (id, domain, value, label, trigger_words, sort_order, prompt_lists) "
                "VALUES (gen_random_uuid(), 'inventory', 'stock', 'owner row', '{}', 0, '{}')"
            )
        )
        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            missing = m.apply(db.connection())
        assert missing == ["stock"]
        assert "stock" in caplog.text and "sub_ask_measures" in caplog.text
        assert _rows(db).get("stock") is None


# ---- crew live check, 13 Oct 2026 (v90 = prod v52 + the addendum, NO glossary rows on dev):
# the measure list rendered empty and the parser filed supplier as other, 入库 as last_receipt.


MEASURE_VALUES = (
    "received_qty", "last_receipt", "incoming", "stock", "supplier",
    "last_cost", "sold_total", "sold_by", "other",
)


def test_the_addendum_defines_every_measure_inline_even_with_no_glossary_rows():
    rendered = SUB_ASKS_ADDENDUM.replace("{{sub_ask_measures}}", "")
    for value in MEASURE_VALUES:
        assert f'"{value}":' in rendered, value


def test_a_quantity_received_is_received_qty_and_a_date_is_last_receipt():
    text = " ".join(SUB_ASKS_ADDENDUM.split())
    received = text[text.index('"received_qty":') : text.index('"last_receipt":')]
    last = text[text.index('"last_receipt":') : text.index('"incoming":')]
    assert "HOW MANY" in received and "quantity" in received
    assert "WHEN" in last and "date" in last


def test_a_count_and_its_breakdown_are_two_asks():
    text = " ".join(SUB_ASKS_ADDENDUM.split())
    assert "A count AND its breakdown are two things" in text
    assert "sold_total then sold_by" in text


def test_the_glossary_renders_the_words_only():
    with blank_session() as db:
        _seeded(db)
        rendered = pv.render_value(db, "sub_ask_measures")
        first = rendered.splitlines()[0]
        assert first.startswith('    "received_qty": ') and "never goods" not in first
