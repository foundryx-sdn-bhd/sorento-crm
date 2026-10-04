"""AutoCount pushes a sales order's Note as raw RTF - this makes it plain text.

  a  `strip_rtf` on the real RTF sample matches AutoCount's control byte for byte
  b  plain text passes through unchanged apart from `.strip()`
  c  `None`/blank in, `None` out
  d  cp1252 hex escapes and `\\{ \\} \\\\` unescape correctly
  e  `CanonicalSalesOrder.internal_note` is plain the moment the payload parses
  f  the real ingest entry point stores the plain text, not the RTF
  g  the backfill migration helper cleans a landed row and leaves a plain one alone
  h  a control word `striprtf` cannot parse never raises - the raw value comes back
  i  a leading space before the `{\\rtf` wrapper still gets recognised and stripped
  j  3+ blank lines mid-note collapse to one blank line (AutoCount's trailing-`\\par` runs)
  k  trailing spaces on a line are dropped even when the line is not the note's last one

Substrate: (a)-(e) are pure functions/schemas, no DB. (f) reuses
`tests.test_ingest_documents`'s Postgres-backed `env` fixture (a scratch schema) since
it exercises the real ingest endpoint end to end. (g) is raw SQL against the migration's
own helper, so it uses `pg_session` against the REAL database instead (rolled back) -
same reasoning as `test_migration_450_spec_rules_backfill.py`: raw SQL does not resolve
through the scratch schema's schema-translate map, so a scratch copy would prove nothing.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.schemas.canonical_documents import CanonicalSalesOrder
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.utils.rtf import strip_rtf

from ._pg_fixture import pg_session, unique_code
from .test_ingest_documents import INGEST_SO, _ref, _so_record, env  # noqa: F401

# The exact bytes AutoCount pushed for one real delivery-address note.
_SAMPLE_RTF = (
    r"{\rtf1\ansi\ansicpg1252\deff0\deflang1033{\fonttbl{\f0\fnil\fcharset0 Microsoft YaHei;}} "
    r"{\colortbl ;\red0\green0\blue0;} \viewkind4\uc1\pard\cf1\b\f0\fs20 DELIVERY ADDRESS\par "
    r"A-25-07 MAYA ARA RESIDENCES\par 1 JALAN PJU 1A/1, ARA DAMANSARA\par "
    r"47301 PETALING JAYA, SELANGOR\par \par PIC: 013-000 0549 - FAD\cf0\fs20\par }"
)
_SAMPLE_PLAIN = (
    "DELIVERY ADDRESS\n"
    "A-25-07 MAYA ARA RESIDENCES\n"
    "1 JALAN PJU 1A/1, ARA DAMANSARA\n"
    "47301 PETALING JAYA, SELANGOR\n"
    "\n"
    "PIC: 013-000 0549 - FAD"
)

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "510_strip_rtf_so_notes.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("zzt_migration_510", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestStripRtf:
    def test_a_the_real_sample_matches_exactly(self):
        assert strip_rtf(_SAMPLE_RTF) == _SAMPLE_PLAIN

    def test_b_plain_text_passes_through_stripped_only(self):
        assert strip_rtf("  Just a note, no RTF at all.  ") == "Just a note, no RTF at all."

    def test_c_none_and_blank_are_none(self):
        assert strip_rtf(None) is None
        assert strip_rtf("") is None
        assert strip_rtf("   ") is None

    def test_d_hex_escapes_and_braces_unescape(self):
        # \'e9 is cp1252's e-acute; \{ \} \\ are RTF's own escapes for literal
        # brace/backslash characters, distinct from the control-word backslash.
        sample = r"{\rtf1\ansi\ansicpg1252\deff0 Caf\'e9 \{brace\} back\\slash\par}"
        assert strip_rtf(sample) == "Café {brace} back\\slash"

    def test_h_a_control_word_striprtf_cannot_parse_never_raises(self):
        # striprtf 0.0.33 raises TypeError on a truncated \uc control word - a push
        # or a migration row that hits this must not become a permanent failure.
        sample = r"{\rtf1\ansi text \uc}"
        assert strip_rtf(sample) == sample

    def test_i_a_leading_space_before_the_wrapper_still_strips(self):
        assert strip_rtf(" " + _SAMPLE_RTF) == _SAMPLE_PLAIN

    def test_j_three_or_more_blank_lines_mid_note_collapse_to_one(self):
        # Real data: the ASTER GREEN note ends in five \par in a row. Mid-note here
        # (not at the very end) so the collapse, not the overall strip(), is what
        # is under test.
        sample = r"{\rtf1\ansi\ansicpg1252\deff0 LINE ONE\par\par\par\par LINE TWO\par}"
        assert strip_rtf(sample) == "LINE ONE\n\nLINE TWO"

    def test_k_trailing_spaces_on_a_non_last_line_are_dropped(self):
        sample = r"{\rtf1\ansi\ansicpg1252\deff0 LINE ONE   \par LINE TWO\par}"
        assert strip_rtf(sample) == "LINE ONE\nLINE TWO"


class TestCanonicalSalesOrderNote:
    def test_e_the_schema_stores_plain_text(self):
        so = CanonicalSalesOrder(
            source_ref=_ref("SO"),
            so_number=unique_code("SO"),
            status="open",
            internal_note=_SAMPLE_RTF,
        )
        assert so.internal_note == _SAMPLE_PLAIN


class TestCanonicalSalesOrderNoteLengthCap:
    """Security review, SPL-B1: an unbounded `internal_note` reaches `label_from_note` on
    every push (a batch of up to 1000, synchronous). Capped to 8000 characters - AFTER the
    RTF wrapper is stripped, not before it, since a font table alone routinely runs the
    RAW byte count several times past the PLAIN text it wraps.
    """

    def test_an_rtf_note_under_8000_chars_of_plain_text_but_over_8000_as_raw_rtf_lands(self):
        # A font table is metadata, never rendered text - padding it is a realistic way
        # for AutoCount's own RTF control to produce a raw payload many times longer
        # than what a person actually typed.
        padding = "".join(
            f"{{\\f{i}\\fnil\\fcharset0 PaddingFontName{i};}}" for i in range(1, 400)
        )
        padded_rtf = (
            r"{\rtf1\ansi\ansicpg1252\deff0\deflang1033"
            r"{\fonttbl{\f0\fnil\fcharset0 Microsoft YaHei;}" + padding + "} "
            r"{\colortbl ;\red0\green0\blue0;} \viewkind4\uc1\pard\cf1\b\f0\fs20 "
            r"PROJECT: SHORT\cf0\fs20\par }"
        )
        assert len(padded_rtf) > 8000

        so = CanonicalSalesOrder(
            source_ref=_ref("SO"), so_number=unique_code("SO"), status="open",
            internal_note=padded_rtf,
        )

        assert so.internal_note == "PROJECT: SHORT"

    def test_a_plain_note_over_8000_characters_is_truncated_not_rejected(self):
        so = CanonicalSalesOrder(
            source_ref=_ref("SO"), so_number=unique_code("SO"), status="open",
            internal_note="A" * 9000,
        )

        assert so.internal_note == "A" * 8000

    def test_a_non_string_internal_note_still_gets_pydantics_own_type_error(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CanonicalSalesOrder(
                source_ref=_ref("SO"), so_number=unique_code("SO"), status="open",
                internal_note=12345,
            )


class TestIngestStoresPlainText:
    def test_f_the_ingest_endpoint_stores_plain_text_not_rtf(self, env):
        record = _so_record(env, internal_note=_SAMPLE_RTF)
        res = env.post(INGEST_SO, [record])
        assert res.status_code == 200, res.text

        header = env.header("sales_orders", record["source_ref"])
        assert header is not None
        assert header["internal_note"] == _SAMPLE_PLAIN


@pytest.fixture
def db():
    with pg_session() as session:
        yield session


def _insert_so(db, *, internal_note: str) -> tuple[str, object]:
    so_id = str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO sales_orders (id, company_id, so_number, status, internal_note)
            VALUES (:id, :company_id, :so_number, 'open', :note)
            """
        ),
        {
            "id": so_id,
            "company_id": DEFAULT_COMPANY_ID,
            "so_number": unique_code("ZZTRTF"),
            "note": internal_note,
        },
    )
    updated_at = db.execute(
        text("SELECT updated_at FROM sales_orders WHERE id = :id"), {"id": so_id}
    ).scalar()
    return so_id, updated_at


class TestBackfillHelper:
    def test_g_a_landed_rtf_row_is_cleaned_and_a_plain_one_is_untouched(self, db):
        rtf_id, _ = _insert_so(db, internal_note=_SAMPLE_RTF)
        plain_id, plain_updated_at = _insert_so(db, internal_note="Already plain.")
        db.flush()

        module = _migration_module()
        touched = module.strip_rtf_notes(db.connection())

        # >=, not ==: this runs against the shared local database (rolled back at
        # teardown), which can already hold other landed RTF-wrapped notes nobody
        # has backfilled yet - a hard `== 1` couples the test to how clean that
        # ambient data happens to be today.
        assert touched >= 1

        rtf_row = db.execute(
            text("SELECT internal_note FROM sales_orders WHERE id = :id"), {"id": rtf_id}
        ).mappings().first()
        assert rtf_row["internal_note"] == _SAMPLE_PLAIN

        plain_row = db.execute(
            text("SELECT internal_note, updated_at FROM sales_orders WHERE id = :id"),
            {"id": plain_id},
        ).mappings().first()
        assert plain_row["internal_note"] == "Already plain."
        assert plain_row["updated_at"] == plain_updated_at
