"""CHAT-LANGUAGE slice 1 (stock), AC-CL12 and the resolve half of AC-CL04: `label_catalog.resolve`.

Postgres only, on a blank schema (`tests._pg_fixture.blank_session`), like
`tests/test_translation_service.py`. The blank schema is empty, but every count is still scoped
to `source_lang='en'`, `target_lang='ms'` and the catalog's own source texts, so the test stays
correct on a shared database.

`app.services.chatbot.label_catalog` does not exist yet, so every test is red on import.
"""
from __future__ import annotations

import pytest

from app.models.translation_memory import TranslationMemory
from app.services.chatbot import label_catalog
from tests._pg_fixture import blank_session


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _rows(db, lang: str = "ms") -> list[TranslationMemory]:
    return (
        db.query(TranslationMemory)
        .filter(
            TranslationMemory.source_lang == "en",
            TranslationMemory.target_lang == lang,
            TranslationMemory.source_text.in_(list(label_catalog.LABELS)),
        )
        .all()
    )


def _by_source(db, lang: str = "ms") -> dict[str, TranslationMemory]:
    return {r.source_text: r for r in _rows(db, lang)}


def _add(db, source_text: str, target_text: str, source: str, lang: str = "ms") -> None:
    db.add(
        TranslationMemory(
            source_text=source_text,
            source_lang="en",
            target_lang=lang,
            target_text=target_text,
            source=source,
        )
    )
    db.flush()


def test_ac_cl12_first_call_inserts_one_ai_row_per_catalog_entry(db):
    assert _rows(db) == []
    loc = label_catalog.resolve(db, "ms")
    rows = _by_source(db)
    assert set(rows) == set(label_catalog.LABELS)
    assert len(_rows(db)) == len(label_catalog.LABELS)
    for english, row in rows.items():
        assert row.source == "ai", english
        assert row.target_text == label_catalog.LABELS[english]["ms"], english
    # The localizer works off those rows.
    assert loc.label({"key": "product_code", "label": "Product Code", "value": "X"}) == "Kod Produk"
    assert loc.text("no stock at the moment, ETA 15/10/2026.") == (
        "tiada stok buat masa ini, ETA 15/10/2026."
    )


def test_ac_cl12_second_call_inserts_nothing(db):
    label_catalog.resolve(db, "ms")
    first = {r.id: (r.target_text, r.source) for r in _rows(db)}
    label_catalog.resolve(db, "ms")
    second = {r.id: (r.target_text, r.source) for r in _rows(db)}
    assert first == second
    assert len(second) == len(label_catalog.LABELS)


def test_ac_cl12_a_manual_row_wins_and_is_unchanged_afterwards(db):
    _add(db, "Total", "JUMLAH-BETUL", "manual")
    loc = label_catalog.resolve(db, "ms")

    assert loc.label({"label": "Total", "value": 1}) == "JUMLAH-BETUL"
    assert loc.label({"label": "Product Code", "value": "X"}) == "Kod Produk"

    rows = _by_source(db)
    assert rows["Total"].source == "manual"
    assert rows["Total"].target_text == "JUMLAH-BETUL"
    # Every other entry got its default; the manual one was not duplicated.
    assert len(_rows(db)) == len(label_catalog.LABELS)

    # And it still wins on the next turn.
    again = label_catalog.resolve(db, "ms")
    assert again.label({"label": "Total", "value": 1}) == "JUMLAH-BETUL"
    assert _by_source(db)["Total"].target_text == "JUMLAH-BETUL"


def test_ac_cl12_an_ai_row_edited_by_staff_text_is_read_not_overwritten(db):
    _add(db, "Warehouse", "Stor Barang", "ai")
    loc = label_catalog.resolve(db, "ms")
    assert loc.label({"key": "warehouse", "label": "Warehouse", "value": "BUKIT RAJA"}) == "Stor Barang"
    assert _by_source(db)["Warehouse"].target_text == "Stor Barang"
    assert _by_source(db)["Warehouse"].source == "ai"


def test_ac_cl12_languages_are_independent(db):
    label_catalog.resolve(db, "ms")
    assert _rows(db, "zh") == []
    zh = label_catalog.resolve(db, "zh")
    assert len(_rows(db, "zh")) == len(label_catalog.LABELS)
    assert len(_rows(db, "ms")) == len(label_catalog.LABELS)
    assert zh.label({"label": "Total", "value": 1}) == "总数"


def test_ac_cl12_en_reads_nothing_and_returns_identity(db, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("resolve(db, 'en') must not touch the database")

    monkeypatch.setattr(db, "query", boom)
    monkeypatch.setattr(db, "execute", boom)
    assert label_catalog.resolve(db, "en") is label_catalog.IDENTITY
    monkeypatch.undo()
    assert _rows(db, "ms") == [] and _rows(db, "zh") == []
    assert (
        db.query(TranslationMemory)
        .filter(TranslationMemory.source_lang == "en", TranslationMemory.target_lang == "en")
        .count()
        == 0
    )


def test_ac_cl12_an_unknown_language_reads_nothing_and_returns_identity(db, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("resolve(db, 'fr') must not touch the database")

    monkeypatch.setattr(db, "query", boom)
    monkeypatch.setattr(db, "execute", boom)
    assert label_catalog.resolve(db, "fr") is label_catalog.IDENTITY


def test_ac_cl04_resolve_ignores_a_memory_row_that_drops_a_token(db):
    """A staff edit that loses `{eta}` is rejected at read time: the default stands in, and
    the row itself is left as staff wrote it (never overwritten, never deleted)."""
    _add(db, "no stock at the moment, ETA {eta}.", "tiada stok buat masa ini.", "manual")
    loc = label_catalog.resolve(db, "ms")

    assert loc.text("no stock at the moment, ETA 15/10/2026.") == (
        "tiada stok buat masa ini, ETA 15/10/2026."
    )
    row = _by_source(db)["no stock at the moment, ETA {eta}."]
    assert row.source == "manual"
    assert row.target_text == "tiada stok buat masa ini."


def test_ac_cl04_resolve_ignores_a_memory_row_that_adds_a_token(db):
    _add(db, "Total", "Jumlah {extra}", "manual")
    loc = label_catalog.resolve(db, "ms")
    assert loc.label({"label": "Total", "value": 1}) == "Jumlah"


# --------------------------------------------------------------------------- #
# Fix round 1, item 1: the seed rides a SEPARATE short session, never the turn's
# --------------------------------------------------------------------------- #


def _scratch_cleanup(engine) -> None:
    from sqlalchemy.orm import Session

    with Session(bind=engine) as cleanup:
        cleanup.query(TranslationMemory).filter(
            TranslationMemory.source_lang == "en",
            TranslationMemory.target_lang == "ms",
            TranslationMemory.source_text.in_(list(label_catalog.LABELS)),
        ).delete(synchronize_session=False)
        cleanup.commit()


def test_item1_seeded_defaults_are_visible_from_a_second_session():
    """The turn's own session may never commit before the reply; the seed must persist anyway."""
    from sqlalchemy.orm import Session

    from tests._pg_fixture import blank_schema_engine

    scoped = blank_schema_engine()
    _scratch_cleanup(scoped)
    try:
        with Session(bind=scoped) as turn_session:
            # An uncommitted write of the TURN: seeding on this session would commit it.
            turn_session.add(
                TranslationMemory(
                    source_text="ZZT turn marker",
                    source_lang="en",
                    target_lang="xx",
                    target_text="marker",
                    source="ai",
                )
            )
            turn_session.flush()
            label_catalog.resolve(turn_session, "ms")
            turn_session.rollback()  # the turn never committed anything itself
        with Session(bind=scoped) as other:
            assert (
                other.query(TranslationMemory)
                .filter(TranslationMemory.source_text == "ZZT turn marker")
                .count()
                == 0
            ), "the seed committed the turn's own transaction"
            rows = (
                other.query(TranslationMemory)
                .filter(
                    TranslationMemory.source_lang == "en",
                    TranslationMemory.target_lang == "ms",
                    TranslationMemory.source_text.in_(list(label_catalog.LABELS)),
                )
                .all()
            )
            assert len(rows) == len(label_catalog.LABELS)
            assert {r.source for r in rows} == {"ai"}
    finally:
        _scratch_cleanup(scoped)


def test_item1_dry_run_reads_but_writes_nothing():
    from sqlalchemy.orm import Session

    from tests._pg_fixture import blank_schema_engine

    scoped = blank_schema_engine()
    _scratch_cleanup(scoped)
    try:
        with Session(bind=scoped) as turn_session:
            loc = label_catalog.resolve(turn_session, "ms", dry_run=True)
            # The code defaults still serve the reply.
            assert loc.label({"label": "Total", "value": 1}) == "Jumlah"
        with Session(bind=scoped) as other:
            assert (
                other.query(TranslationMemory)
                .filter(
                    TranslationMemory.target_lang == "ms",
                    TranslationMemory.source_text.in_(list(label_catalog.LABELS)),
                )
                .count()
                == 0
            )
    finally:
        _scratch_cleanup(scoped)


def test_item1_a_failing_select_leaves_the_session_usable(db, monkeypatch):
    from sqlalchemy import text

    def bad_query(*args, **kwargs):
        db.execute(text("SELECT * FROM table_that_does_not_exist_zz"))

    monkeypatch.setattr(db, "query", bad_query)
    loc = label_catalog.resolve(db, "ms")
    monkeypatch.undo()
    # The turn's transaction was not aborted: a follow-up query works.
    assert db.execute(text("SELECT 1")).scalar() == 1
    assert loc.label({"label": "Total", "value": 1}) == "Jumlah"
