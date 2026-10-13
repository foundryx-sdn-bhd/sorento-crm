"""PV2-RESOLVE unknown-words log (chatbot_unrecognised_phrases). No LLM, no network."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.chatbot.pv2.resolve import log_unrecognised

from tests.chatbot.pv2 import _resolve_seed as S


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def rows(db):
    return db.execute(
        text("SELECT phrase, kind, seen_count, domain, contact_id, turn_id FROM chatbot_unrecognised_phrases ORDER BY phrase, kind")
    ).all()


def test_first_insert_has_seen_count_one_and_normalised_phrase(db):
    n = log_unrecognised(db, [("  Foo   BAR ", "brand")], domain="stock", contact_id="c1", turn_id="t1")
    assert n == 1
    (row,) = rows(db)
    assert row.phrase == "foo bar"
    assert row.kind == "brand"
    assert row.seen_count == 1
    assert (row.domain, row.contact_id, row.turn_id) == ("stock", "c1", "t1")


def test_same_phrase_different_case_and_spacing_bumps_same_row(db):
    log_unrecognised(db, [("foo bar", "brand")])
    log_unrecognised(db, [("FOO    Bar", "brand")])
    (row,) = rows(db)
    assert row.seen_count == 2


def test_same_phrase_different_kind_is_a_separate_row(db):
    log_unrecognised(db, [("gambar", "attachment_type"), ("gambar", "brand")])
    assert [(r.kind, r.seen_count) for r in rows(db)] == [("attachment_type", 1), ("brand", 1)]


def test_empty_list_writes_nothing(db):
    assert log_unrecognised(db, []) == 0
    assert rows(db) == []


def test_a_failed_log_write_never_breaks_the_callers_transaction(db):
    from sqlalchemy import text as _text

    from app.services.chatbot.pv2.resolve import log_unrecognised

    # Make the write itself fail. Dropping the scratch table does not: the blank schema's
    # search_path ends in `public` (tests/_pg_fixture.py, for extension functions), so the
    # insert fell through to a `public` copy of the table wherever one exists (a database
    # built by `scripts.bootstrap_env`, as CI's is) and the "failed" write succeeded. A
    # trigger on the scratch table fails it in place; the whole transaction rolls back.
    with db.begin_nested():
        db.execute(_text(
            "CREATE FUNCTION zz_fail_log_write() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'log write fails'; END $$"
        ))
        db.execute(_text(
            "CREATE TRIGGER zz_fail_log_write BEFORE INSERT OR UPDATE ON chatbot_unrecognised_phrases "
            "FOR EACH ROW EXECUTE FUNCTION zz_fail_log_write()"
        ))
    assert log_unrecognised(db, [("gambar", "attachment_type")]) == 0
    assert db.execute(_text("SELECT 1")).scalar() == 1
