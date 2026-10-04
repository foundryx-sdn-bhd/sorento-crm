"""The customer importer: the upsert key, the company partition, and what it may not touch.

Every test seeds its own chain under a `ZZT` marker and asserts only about rows it created,
so it says the same thing on the prod-copy database and on CI's empty one.

Three things here are worth stating plainly, because getting any of them wrong is silent:

* **The key is (company, code, name).** `301-S007` carries 225 distinct debtor names in real
  data, so code alone would collapse them onto one row.
* **The same code+name legitimately exists under two companies** - 884 pairs are held by both
  Sorento and Mocha today - so a cross-company insert must not read as a collision.
* **A blank cell never clears a populated field.** A sparse export wiping curated columns is
  the way this importer would destroy real data.
"""
from __future__ import annotations

import uuid
from io import BytesIO

import openpyxl
import pytest

from app.models.base import set_company_scope
from app.models.company import Company
from app.models.order import Customer, CustomerAlias
from app.services import customer_import_service as svc
from app.services import import_outcome_codes as oc
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.import_outcome import ImportOutcome

from ._pg_fixture import blank_session, unique_code

MOCHA_ID = "00000000-0000-0000-0000-000000000002"

HEADERS = [
    "Debtor Code",
    "Debtor Name",
    "Email",
    "Phone No",
    "Mobile No",
    "Registered Name",
    "Market Segment",
]


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


def _workbook(rows: list[list], headers: list[str] | None = None, title_lines: int = 1) -> bytes:
    """A listing with `title_lines` junk rows above the header, as AutoCount emits."""
    wb = openpyxl.Workbook()
    sheet = wb.active
    for i in range(title_lines):
        sheet.append([f"CUSTOMER LISTING {i}"])
    sheet.append(headers or HEADERS)
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _aliases(session) -> None:
    """Seed the `customer` doc type's aliases the way migration 353 does.

    The alias rows are migration-body data, so a create_all schema has none of them and
    every column would report unmapped. Replayed from the migration itself rather than
    restated, so the test cannot drift from what ships.
    """
    import importlib.util
    from pathlib import Path

    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    spec = importlib.util.spec_from_file_location(
        "_customer_aliases_353", versions / "353_customer_import_aliases.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.seed(session.connection())


#: Alias rows migration 353 deliberately does NOT ship, one per field the importer must
#: never write. Seeded only by the guard tests below, and the reason they are not
#: vacuous: without them the hostile headers resolve to nothing, so the test proves only
#: that an UNKNOWN column is ignored - which stays true even if every guard is deleted.
#: With them the header genuinely names the field, and what protects the row is the
#: READABLE_FIELDS / UPDATABLE_FIELDS / NEVER_WRITTEN_FIELDS split under test.
HOSTILE_ALIASES = (
    ("notes", "Notes"),
    ("is_active", "Active"),
    ("account_owner_user_id", "Account Owner"),
    ("company_id", "Company Id"),
    ("id", "Row Id"),
    ("created_at", "Created At"),
    ("created_by", "Created By"),
    ("billing_address", "Billing Address"),
    ("customer_code", "Key Code"),
    ("customer_name", "Key Name"),
)


def _hostile_aliases(session) -> None:
    """Give every protected field a header that really does resolve to it."""
    from sqlalchemy import text

    for field, alias in HOSTILE_ALIASES:
        session.execute(
            text(
                """
                INSERT INTO import_field_alias (doc_type, field, alias, locale)
                VALUES ('customer', :f, :a, NULL)
                ON CONFLICT (doc_type, field, alias) WHERE supplier_id IS NULL DO NOTHING
                """
            ),
            {"f": field, "a": alias},
        )
    session.flush()


def _assert_headers_resolve(session, *headers: str) -> None:
    """The anti-vacuity check: these headers are known, not merely unrecognised."""
    from app.services.import_alias_service import AliasResolver

    resolver = AliasResolver.for_doc_type(session, "customer")
    expected = dict((alias, field) for field, alias in HOSTILE_ALIASES)
    for header in headers:
        assert resolver.field_for_header(header) == expected[header], (
            f"{header!r} does not resolve, so this test would pass with no guard at all"
        )


def _apply(session, data: bytes, *, actor: str | None = None) -> tuple[dict, ImportOutcome]:
    outcome = ImportOutcome(None, persist=False)
    result = svc.apply(session, data, outcome, actor=actor)
    session.flush()
    return result, outcome


def _held(session, code: str) -> list[Customer]:
    return (
        session.query(Customer)
        .filter(Customer.customer_code == code)
        .order_by(Customer.customer_name)
        .all()
    )


# --------------------------------------------------------------------- creating


def test_a_new_row_is_created_and_stamped_with_the_scoped_company():
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]

        result, _outcome = _apply(
            session,
            _workbook([[code, "Alpha Trading", "a@example.com", "03-111", "012-1", None, None]]),
        )

        assert result["created"] == 1
        assert (result["updated"], result["unchanged"], result["failed"]) == (0, 0, 0)
        rows = _held(session, code)
        assert len(rows) == 1
        assert rows[0].company_id == DEFAULT_COMPANY_ID, "company comes from the job scope"
        assert rows[0].email == "a@example.com"
        assert rows[0].is_active is True
        assert rows[0].customer_type == "company"


def test_company_id_is_never_read_from_a_file_column():
    """AC-1.3: a spreadsheet must not be able to write into another company's book.

    The `Company Id` header resolves to the `company_id` field here - asserted, not
    assumed - so the row is held in the scoped company by the write-side guard rather
    than by the column having gone unrecognised.
    """
    with blank_session() as session:
        _aliases(session)
        _hostile_aliases(session)
        _assert_headers_resolve(session, "Company Id", "Row Id", "Created At", "Created By")
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        session.add(Company(id=MOCHA_ID, name="Mocha", code=unique_code("M")[:20]))
        session.flush()
        code = unique_code("C")[:50]
        planted_id, planted_author = str(uuid.uuid4()), str(uuid.uuid4())
        importing_user = str(uuid.uuid4())

        result, _outcome = _apply(
            session,
            _workbook(
                [[code, "Alpha Trading", MOCHA_ID, planted_id, "2001-01-01", planted_author]],
                headers=[
                    "Debtor Code",
                    "Debtor Name",
                    "Company Id",
                    "Row Id",
                    "Created At",
                    "Created By",
                ],
            ),
            actor=importing_user,
        )

        assert result["created"] == 1
        rows = _held(session, code)
        assert len(rows) == 1
        assert rows[0].company_id == DEFAULT_COMPANY_ID, "the job's scope, not the file"
        assert rows[0].id != planted_id, "identity is ours, never the file's"
        assert rows[0].created_by == importing_user, "provenance is the caller, not a cell"
        assert rows[0].created_at is not None and rows[0].created_at.year > 2001


def test_the_same_code_and_name_may_exist_under_two_companies():
    """AC-2.4. 884 code+name pairs are held by BOTH Sorento and Mocha today, so the
    unique index must carry `company_id` - which is what the model declares now."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, None)
        session.add(Company(id=MOCHA_ID, name="Mocha", code=unique_code("M")[:20]))
        session.flush()

        code = unique_code("C")[:50]
        book = _workbook([[code, "CASH (SRT) - AISAH SHAMSUDIN", None, None, None, None, None]])

        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        first, _ = _apply(session, book)
        set_company_scope(session, frozenset({MOCHA_ID}))
        second, _ = _apply(session, book)

        assert (first["created"], second["created"]) == (1, 1)
        set_company_scope(session, None)
        rows = _held(session, code)
        assert len(rows) == 2, "one row per company, not a collision"
        assert {r.company_id for r in rows} == {DEFAULT_COMPANY_ID, MOCHA_ID}


# --------------------------------------------------------------------- updating


def test_a_changed_value_updates_and_leaves_the_key_alone():
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]
        _apply(session, _workbook([[code, "Alpha", "old@example.com", None, None, None, None]]))

        result, _outcome = _apply(
            session, _workbook([[code, "Alpha", "new@example.com", None, None, None, None]])
        )

        assert (result["updated"], result["created"]) == (1, 0)
        rows = _held(session, code)
        assert len(rows) == 1
        assert rows[0].email == "new@example.com"
        assert rows[0].customer_code == code and rows[0].customer_name == "Alpha"


def test_an_identical_row_is_unchanged_and_writes_nothing():
    """AC-3.3: only rows whose values actually moved count as updated."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]
        book = _workbook([[code, "Alpha", "a@example.com", "03-111", None, None, None]])
        _apply(session, book)
        before = _held(session, code)[0].updated_at

        result, outcome = _apply(session, book)

        assert result["unchanged"] == 1
        assert result["updated"] == 0
        assert _held(session, code)[0].updated_at == before, "no write at all"
        assert outcome.count_of(oc.UNCHANGED) == 1


def test_a_blank_cell_never_clears_a_populated_field():
    """AC-3.2: the likeliest way a customer importer destroys real data."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]
        _apply(
            session,
            _workbook([[code, "Alpha", "keep@example.com", "03-111", "012-1", "Alpha Sdn Bhd", None]]),
        )

        # A sparse re-export: the contact columns are simply not filled in.
        result, _outcome = _apply(
            session, _workbook([[code, "Alpha", None, None, None, None, None]])
        )

        held = _held(session, code)[0]
        assert result["unchanged"] == 1, "a file that says less says nothing"
        assert held.email == "keep@example.com"
        assert held.phone_number == "03-111"
        assert held.mobile_number == "012-1"
        assert held.registered_name == "Alpha Sdn Bhd"


def test_curated_fields_survive_a_re_import_that_names_them():
    """AC-3: account owner, notes and the active flag belong to a person, not a file.

    Every hostile header here genuinely resolves to the field it names (asserted before
    the import runs), so the row is protected by the write-side guard and not by a column
    that failed to map. The re-import must therefore read `unchanged` and write nothing.
    """
    with blank_session() as session:
        _aliases(session)
        _hostile_aliases(session)
        _assert_headers_resolve(
            session, "Account Owner", "Notes", "Active", "Billing Address", "Key Name"
        )
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]
        _apply(session, _workbook([[code, "Alpha", "a@example.com", None, None, None, None]]))

        held = _held(session, code)[0]
        held.account_owner_user_id = None  # no user row to reference; assert it is not SET
        held.notes = "Called about the 2026 range"
        held.is_active = False
        session.flush()
        original_id, created_at = held.id, held.created_at

        result, outcome = _apply(
            session,
            _workbook(
                [
                    [
                        code,
                        "Alpha",
                        "a@example.com",
                        None,
                        None,
                        None,
                        None,
                        str(uuid.uuid4()),  # Account Owner
                        "wiped",  # Notes
                        "Yes",  # Active
                        '{"line1": "nowhere"}',  # Billing Address
                        "Renamed Alpha",  # Key Name: a second header for customer_name
                    ]
                ],
                headers=HEADERS
                + ["Account Owner", "Notes", "Active", "Billing Address", "Key Name"],
            ),
        )

        assert result["unchanged"] == 1, "a file that only names protected fields says nothing"
        assert (result["updated"], result["created"], result["failed"]) == (0, 0, 0)
        assert outcome.count_of(oc.UNCHANGED) == 1
        held = _held(session, code)[0]
        assert held.notes == "Called about the 2026 range"
        assert held.is_active is False, "absence or a file column is not a reactivation"
        assert held.account_owner_user_id is None
        assert held.billing_address is None, "AC-3.4: out of scope, not silently written"
        assert held.customer_name == "Alpha", "AC-1.4: the importer never renames"
        assert (held.id, held.created_at) == (original_id, created_at)


def test_market_segment_is_filled_when_blank_and_never_replaced():
    """AC-3: it decides SCM demand class, so overwriting one re-prioritises live orders."""
    from app.models.access import MarketSegment

    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        retail, project = unique_code("RT")[:50], unique_code("PJ")[:50]
        session.add(MarketSegment(id=str(uuid.uuid4()), code=retail, name="Retail"))
        session.add(MarketSegment(id=str(uuid.uuid4()), code=project, name="Project"))
        session.flush()

        blank_code, curated_code = unique_code("C")[:50], unique_code("C")[:50]
        _apply(
            session,
            _workbook(
                [
                    [blank_code, "Blank", None, None, None, None, None],
                    [curated_code, "Curated", None, None, None, None, project],
                ]
            ),
        )
        assert _held(session, blank_code)[0].market_segment_code is None
        assert _held(session, curated_code)[0].market_segment_code == project

        _apply(
            session,
            _workbook(
                [
                    [blank_code, "Blank", None, None, None, None, retail],
                    [curated_code, "Curated", None, None, None, None, retail],
                ]
            ),
        )

        assert _held(session, blank_code)[0].market_segment_code == retail, "filled"
        assert _held(session, curated_code)[0].market_segment_code == project, "kept"


def test_an_unrecognised_market_segment_is_reported_and_never_costs_the_row():
    """The column is a foreign key: an unknown code would fail the whole customer."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]

        result, _outcome = _apply(
            session, _workbook([[code, "Alpha", None, None, None, None, "NOT-A-SEGMENT"]])
        )

        assert result["created"] == 1
        assert result["unknown_market_segments"] == ["NOT-A-SEGMENT"]
        assert _held(session, code)[0].market_segment_code is None


def test_a_dropped_market_segment_names_the_rows_it_happened_on():
    """A file-level list no screen renders is not a trace: 40 customers could land with
    no segment under a job reporting "40 created, no warnings", and the segment decides
    SCM demand class and fulfilment priority (decision D3)."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        clean, dropped, second = (unique_code("C")[:50] for _ in range(3))

        result, outcome = _apply(
            session,
            _workbook(
                [
                    [clean, "Clean", None, None, None, None, None],
                    [dropped, "Dropped", None, None, None, None, "NOT-A-SEGMENT"],
                    [second, "Also dropped", None, None, None, None, "ALSO-NOT-ONE"],
                ]
            ),
        )

        assert result["created"] == 3, "an optional column never costs a whole customer"
        assert result["unknown_market_segment_rows"] == 2
        assert result["unknown_market_segments"] == ["ALSO-NOT-ONE", "NOT-A-SEGMENT"]
        # Per ROW, not just per file: exactly the two rows, riding on a success outcome.
        assert outcome.count_of(oc.MARKET_SEGMENT_NOT_RECOGNISED) == 2
        assert outcome.count_of(oc.CREATED) == 1
        assert outcome.failed == 0 and outcome.skipped == 0
        assert outcome.successful == 3


def test_a_dropped_market_segment_is_traced_on_an_unchanged_row_too():
    """The row states nothing new about the customer, but it did name a segment that
    went nowhere; a re-import must not lose that."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]
        _apply(session, _workbook([[code, "Alpha", "a@example.com", None, None, None, None]]))

        result, outcome = _apply(
            session,
            _workbook([[code, "Alpha", "a@example.com", None, None, None, "NOT-A-SEGMENT"]]),
        )

        assert result["unchanged"] == 1
        assert outcome.count_of(oc.MARKET_SEGMENT_NOT_RECOGNISED) == 1
        assert outcome.count_of(oc.UNCHANGED) == 0, "one code per row; the segment is it"
        assert _held(session, code)[0].market_segment_code is None


# ------------------------------------------------------------- customer type


def test_no_shipped_alias_maps_a_debtor_type_column():
    """Decision D1: a real AutoCount listing's `Debtor Type` carries Trade / Cash /
    Local. Mapping it would rewrite the discriminator the app branches on with another
    system's vocabulary, so 353 ships no alias for it and the column reads unmapped."""
    from app.services.import_alias_service import AliasResolver

    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        resolver = AliasResolver.for_doc_type(session, "customer")
        assert resolver.field_for_header("Debtor Type") is None
        assert resolver.field_for_header("Customer Type") is None
        code = unique_code("C")[:50]

        result, _outcome = _apply(
            session,
            _workbook(
                [[code, "Alpha", "Cash"]],
                headers=["Debtor Code", "Debtor Name", "Debtor Type"],
            ),
        )

        assert result["unmapped_headers"] == ["Debtor Type"]
        assert _held(session, code)[0].customer_type == "company"


def test_customer_type_is_written_on_insert_only():
    """Decision D1: set once, never moved by a re-import. Even where an admin has added
    an alias deliberately, a later file cannot flip an existing customer's type."""
    from sqlalchemy import text

    with blank_session() as session:
        _aliases(session)
        session.execute(
            text(
                "INSERT INTO import_field_alias (doc_type, field, alias, locale) "
                "VALUES ('customer', 'customer_type', 'Debtor Type', NULL) "
                "ON CONFLICT (doc_type, field, alias) WHERE supplier_id IS NULL DO NOTHING"
            )
        )
        session.flush()
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]

        _apply(
            session,
            _workbook(
                [[code, "Alpha", "individual"]],
                headers=["Debtor Code", "Debtor Name", "Debtor Type"],
            ),
        )
        assert _held(session, code)[0].customer_type == "individual", "insert reads the file"

        result, _outcome = _apply(
            session,
            _workbook(
                [[code, "Alpha", "Cash"]],
                headers=["Debtor Code", "Debtor Name", "Debtor Type"],
            ),
        )

        assert result["unchanged"] == 1, "the type is not a change the file may make"
        assert result["updated"] == 0
        assert _held(session, code)[0].customer_type == "individual"


# --------------------------------------------- one customer per debtor code (DEBTOR-CODE-UNIQUE)


def test_a_new_name_on_a_held_code_updates_that_customer_and_keeps_the_name_as_alias():
    """Owner, 4 Oct 2026: one customer per debtor code. The held name stays; the file's
    name is an alias so search still finds it."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]
        _apply(session, _workbook([[code, "CASH (SRT) - ABDUL RAUF", None, None, None, None, None]]))

        result, outcome = _apply(
            session, _workbook([[code, "CASH (SRT) - AIMAN", None, None, None, None, None]])
        )

        assert result["created"] == 0
        assert result["needs_review"] == 0
        assert outcome.failed == 0 and outcome.skipped == 0
        held = _held(session, code)
        assert [c.customer_name for c in held] == ["CASH (SRT) - ABDUL RAUF"]
        aliases = session.query(CustomerAlias).filter(CustomerAlias.customer_id == held[0].id).all()
        assert [a.alias_name for a in aliases] == ["CASH (SRT) - AIMAN"]


def test_a_code_twice_in_one_file_is_one_customer():
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]
        result, outcome = _apply(
            session,
            _workbook(
                [
                    [code, "Deluxe Home Center (KTN)", None, None, None, None, None],
                    [code, "Deluxe Home Center AC (I)", None, None, None, None, None],
                ]
            ),
        )

        assert result["created"] == 1
        assert result["skipped"] == 1
        assert outcome.count_of(oc.DUPLICATE_IN_FILE) == 1
        held = _held(session, code)
        assert [c.customer_name for c in held] == ["Deluxe Home Center (KTN)"]
        aliases = session.query(CustomerAlias).filter(CustomerAlias.customer_id == held[0].id).all()
        assert [a.alias_name for a in aliases] == ["Deluxe Home Center AC (I)"]


# ------------------------------------------------------------- partial success


def test_a_row_without_a_name_is_skipped_and_the_rest_of_the_file_imports():
    """AC-5.6: one bad row never fails the file."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        good, nameless = unique_code("C")[:50], unique_code("C")[:50]

        result, outcome = _apply(
            session,
            _workbook(
                [
                    [good, "Alpha", None, None, None, None, None],
                    [nameless, None, None, None, None, None, None],
                    [None, "No code at all", None, None, None, None, None],
                ]
            ),
        )

        assert result["created"] == 1
        assert result["skipped"] == 2
        assert outcome.count_of(oc.MISSING_REQUIRED_FIELD) == 2
        assert len(_held(session, good)) == 1
        assert _held(session, nameless) == []
        assert {p["reason"] for p in result["problems"]} == {
            "no customer name",
            "no customer code",
        }


def test_a_mixed_file_counts_every_row_once():
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        keep, move, add = (unique_code("C")[:50] for _ in range(3))
        _apply(
            session,
            _workbook(
                [
                    [keep, "Keep", "keep@example.com", None, None, None, None],
                    [move, "Move", "before@example.com", None, None, None, None],
                ]
            ),
        )

        result, outcome = _apply(
            session,
            _workbook(
                [
                    [keep, "Keep", "keep@example.com", None, None, None, None],
                    [move, "Move", "after@example.com", None, None, None, None],
                    [add, "Add", None, None, None, None, None],
                    [None, None, None, None, None, None, None],  # spacing, not a row
                    [unique_code("C")[:50], None, None, None, None, None, None],
                ]
            ),
        )

        assert result["total_rows"] == 4, "the blank line is not a data row"
        assert (result["created"], result["updated"], result["unchanged"], result["skipped"]) == (
            1,
            1,
            1,
            1,
        )
        assert outcome.processed == 4
        assert outcome.successful == 3
        assert _held(session, move)[0].email == "after@example.com"


def test_the_same_key_twice_in_one_file_is_skipped_the_second_time():
    """Otherwise the preview counts two creates and the import writes one.

    Its own code: `DUPLICATE_LINE`'s shared label reads "Identical line already exists on
    this order", and a customer job has no order (AC-6.2).
    """
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]

        result, outcome = _apply(
            session,
            _workbook(
                [
                    [code, "Alpha", "first@example.com", None, None, None, None],
                    [code, "alpha ", "second@example.com", None, None, None, None],
                ]
            ),
        )

        assert (result["created"], result["skipped"]) == (1, 1)
        assert outcome.count_of(oc.DUPLICATE_IN_FILE) == 1
        assert outcome.count_of(oc.DUPLICATE_LINE) == 0, "not the order-line code"
        assert oc.label_for(oc.DUPLICATE_IN_FILE) == "The same row appears earlier in this file"
        assert len(_held(session, code)) == 1


def test_a_value_longer_than_its_column_fails_the_row_not_the_file():
    """An over-length varchar aborts the whole Postgres transaction if it reaches the DB."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        good, long_code = unique_code("C")[:50], "X" * 60

        result, outcome = _apply(
            session,
            _workbook(
                [
                    [good, "Alpha", None, None, None, None, None],
                    [long_code, "Too long", None, None, None, None, None],
                ]
            ),
        )

        assert result["created"] == 1
        assert result["failed"] == 1
        assert outcome.count_of(oc.ROW_ERROR) == 1
        assert len(_held(session, good)) == 1


# ------------------------------------------------------------------- unreadable


def test_a_file_with_no_customer_code_column_is_refused_whole():
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))

        result, _outcome = _apply(
            session,
            _workbook([["something", "else"]], headers=["Colour", "Size"]),
        )

        assert result["readable"] is False
        assert result["missing_columns"] == ["customer_code", "customer_name"]
        assert result["created"] == 0


def test_unrecognised_headers_are_reported_by_name():
    """AC-4.3: an unmapped column is the first sign a client's export changed."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]

        result, _outcome = _apply(
            session,
            _workbook(
                [[code, "Alpha", "SALESMAN A", 3]],
                headers=["Debtor Code", "Debtor Name", "Salesman", "Credit Term"],
            ),
        )

        assert result["created"] == 1
        assert result["unmapped_headers"] == ["Salesman", "Credit Term"]


# ---------------------------------------------------------------------- preview


def test_the_preview_writes_nothing_and_agrees_with_the_import():
    """Test and Confirm must never disagree about the same file."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        held, new = unique_code("C")[:50], unique_code("C")[:50]
        _apply(session, _workbook([[held, "Held", "old@example.com", None, None, None, None]]))

        book = _workbook(
            [
                [held, "Held", "new@example.com", None, None, None, None],
                [new, "New", None, None, None, None, None],
                [None, "No code", None, None, None, None, None],
            ]
        )
        preview = svc.preview(session, book)

        assert (preview["created"], preview["updated"], preview["skipped"]) == (1, 1, 1)
        assert _held(session, new) == [], "the preview wrote nothing"
        assert _held(session, held)[0].email == "old@example.com"

        applied, _outcome = _apply(session, book)
        for key in ("created", "updated", "unchanged", "skipped", "failed", "needs_review"):
            assert applied[key] == preview[key], key


def test_the_preview_answers_for_the_scoped_company_only():
    """A preview reading every company reports "would update" on rows the import inserts."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, None)
        session.add(Company(id=MOCHA_ID, name="Mocha", code=unique_code("M")[:20]))
        session.flush()

        code = unique_code("C")[:50]
        book = _workbook([[code, "Shared", "a@example.com", None, None, None, None]])
        set_company_scope(session, frozenset({MOCHA_ID}))
        _apply(session, book)

        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        preview = svc.preview(session, book)

        assert preview["created"] == 1, "Mocha's row is not Sorento's row"
        assert preview["unchanged"] == 0


# ------------------------------------------------------------ the reader itself


def test_the_header_is_found_below_the_export_title_lines():
    """AutoCount exports carry title rows above the table, so row 1 is not the header."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]

        result, _outcome = _apply(
            session,
            _workbook([[code, "Alpha", None, None, None, None, None]], title_lines=4),
        )

        assert result["readable"] is True
        assert result["created"] == 1
        assert result["problems"] == [], "the title lines are not reported as bad rows"


def test_a_numeric_phone_cell_is_not_read_as_a_float():
    """Excel types a bare phone number as a number; `60123456789.0` is not a phone number."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]

        _apply(
            session,
            _workbook([[code, "Alpha", None, 60123456789, None, None, None]]),
        )

        assert _held(session, code)[0].phone_number == "60123456789"


def test_the_report_footer_under_the_table_is_not_a_row():
    """Row 13 of the committed `e2e/fixtures/debtor-listing.xlsx`: AutoCount prints
    `*** END OF REPORT ***` in the debtor-NAME column, so a guard on "both cells blank"
    counts the last line of every real export as a data row and reports it as bad."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]

        result, outcome = _apply(
            session,
            _workbook(
                [
                    [code, "Alpha", None, None, None, None, None],
                    [None, None, None, None, None, None, None],  # spacing
                    [None, "*** END OF REPORT ***", None, None, None, None, None],
                    [None, "Page 1 of 1", None, None, None, None, None],
                    ["-----", None, None, None, None, None, None],
                ]
            ),
        )

        assert result["created"] == 1
        assert result["total_rows"] == 1, "furniture under the table is not data"
        assert result["problems"] == []
        assert outcome.skipped == 0 and outcome.failed == 0


def test_a_customer_actually_called_total_is_still_reported():
    """The footer guard matches the WHOLE cell, so a real debtor name that happens to
    start with a footer word is still named rather than silently disappearing."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))

        result, _outcome = _apply(
            session,
            _workbook([[None, "Total Home Solutions", None, None, None, None, None]]),
        )

        assert result["total_rows"] == 1
        assert result["problems"] == [{"row": 3, "reason": "no customer code"}]


def test_the_row_total_is_published_before_the_first_write():
    """S4: `total_rows` otherwise first appears when the job completes, so the upload
    drawer reads 0/0 for the whole run, which looks stuck."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        first, second = unique_code("C")[:50], unique_code("C")[:50]
        seen: list[int] = []
        outcome = ImportOutcome(None, persist=False)

        def _report(total: int) -> None:
            # Called BEFORE any row is written: nothing is held yet at this point.
            seen.append(total)
            assert _held(session, first) == []

        svc.apply(
            session,
            _workbook(
                [
                    [first, "One", None, None, None, None, None],
                    [second, "Two", None, None, None, None, None],
                ]
            ),
            outcome,
            on_total_rows=_report,
        )
        session.flush()

        assert seen == [2]
        assert len(_held(session, first)) == 1


def test_a_reporting_failure_never_costs_the_import():
    """Progress is observability: it must not be able to fail a 900-row file."""
    with blank_session() as session:
        _aliases(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        code = unique_code("C")[:50]
        outcome = ImportOutcome(None, persist=False)

        def _boom(_total: int) -> None:
            raise RuntimeError("the job row went away")

        result = svc.apply(
            session,
            _workbook([[code, "Alpha", None, None, None, None, None]]),
            outcome,
            on_total_rows=_boom,
        )
        session.flush()

        assert result["created"] == 1
        assert len(_held(session, code)) == 1


# -------------------------------------------------- what the dialog is handed


def test_the_validation_shape_makes_row_problems_warnings_not_errors():
    """AC-5.6: a file with three bad rows out of 900 imports 897, so the bad rows must be
    an acknowledgement, not a block. `valid` false would stop the whole import."""
    from app.tasks.import_tasks import _customer_import_shape

    shaped = _customer_import_shape(
        {
            "readable": True,
            "missing_columns": [],
            "unmapped_headers": ["SALESMAN"],
            "problems": [{"row": 14, "reason": "no customer name"}],
            "total_rows": 900,
            "created": 612,
            "updated": 240,
            "unchanged": 45,
            "skipped": 3,
            "failed": 0,
            "needs_review": 2,
            "unknown_market_segments": ["RETAIL-X"],
            "unknown_market_segment_rows": 40,
        }
    )

    assert shaped["valid"] is True
    assert shaped["errors"] == []
    assert "Row 14: no customer name" in shaped["warnings"]
    assert "Column not recognised: SALESMAN" in shaped["warnings"]
    assert any("RETAIL-X" in w for w in shaped["warnings"])
    assert any("close to one already" in w for w in shaped["warnings"])
    # How MANY customers land without a segment is the part that matters: it decides
    # SCM demand class, and the spelling alone does not say how much of the book moved.
    assert any("40 row(s) import with no market segment" in w for w in shaped["warnings"])
    assert shaped["summary"]["would_create"] == 612
    assert shaped["summary"]["would_skip"] == 3
    assert shaped["summary"]["needs_review"] == 2


def test_an_unreadable_file_is_invalid_with_the_missing_column_named():
    from app.tasks.import_tasks import _customer_import_shape

    shaped = _customer_import_shape(
        {
            "readable": False,
            "missing_columns": ["customer_code", "customer_name"],
            "unmapped_headers": [],
            "problems": [],
            "total_rows": 0,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "failed": 0,
            "needs_review": 0,
        }
    )

    assert shaped["valid"] is False
    assert shaped["errors"] == ["The file has no customer code, customer name column."]
    assert shaped["warnings"] == []
