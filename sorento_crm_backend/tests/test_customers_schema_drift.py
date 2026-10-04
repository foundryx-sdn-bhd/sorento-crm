"""`customers`: the model and the database must agree about every varchar width.

This table has now drifted from its model three times, and each time it cost a debugging
session rather than a review comment:

1. The unique index. `app/models/order.py` still declared the pre-305 global
   `uq_customers_code_name_lower` while the live index was
   `uq_customers_company_code_name_lower`, so a cross-company test failed on the test schema
   and passed in production (UAC AC-2.5).
2. and 3. Three column widths. The model declared `phone_number` String(50), `industry`
   String(120) and `website` String(500); the database held `varchar(20)`, `varchar(100)` and
   `varchar(255)`. A real 4,196-row AutoCount export carries 58 phone numbers longer than 20
   characters (`016-000 0503 (MR.CHAEH)`), so the importer's over-length pre-check - which read
   the MODEL - passed every one of them, promised 0 failures, and Postgres then refused all 58
   with `StringDataRightTruncation`. Migration 355 widened the columns.

A migration only resets the clock. This file is the part that keeps it: the model's declared
lengths are compared against the lengths the database actually has, so the next divergence is
a failing test instead of a preview that lies.

**On a `create_all` database the first test is a tautology, and that is expected.** CI builds
its schema from the ORM models (`scripts/bootstrap_env.py`), so there the two cannot disagree.
The drift only exists where migrations built the schema: the dev and production databases.
The test is still the right shape - it fails there, which is where it matters - and
`test_the_guard_notices_a_narrowed_column` proves the comparison is not vacuous by narrowing a
column for real and watching the guard report it.
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect as sa_inspect, text

from app.database import engine
from app.models.order import Customer
from app.services import customer_import_service as svc

from ._pg_fixture import blank_session, pg_session

#: Every varchar column on the model, and the width it declares.
MODEL_LENGTHS: dict[str, int] = dict(svc._MODEL_LENGTHS)

#: The three widths migration 355 realigned, as the model has always declared them. Named
#: rather than merely covered by the sweep, because these are the ones a real export hit.
REALIGNED = {"phone_number": 50, "industry": 120, "website": 500}


def _drift(limits: dict[str, int]) -> dict[str, tuple[int, int]]:
    """column -> (what the model declares, what the database has), for every disagreement.

    Columns the database does not report are ignored: a model column with no table column at
    all is a different failure, and a missing-migration test that also fires on an unrelated
    new column reads as noise.
    """
    return {
        name: (declared, limits[name])
        for name, declared in sorted(MODEL_LENGTHS.items())
        if name in limits and limits[name] != declared
    }


def _explain(drift: dict[str, tuple[int, int]]) -> str:
    lines = [
        f"  {name}: model String({declared}), database varchar({actual})"
        for name, (declared, actual) in drift.items()
    ]
    return (
        "customers has drifted from its model:\n"
        + "\n".join(lines)
        + "\n\nThe model is the intent. Add a migration that alters the column to match it "
        "(see 355_customers_length_drift), do not narrow the model to match the database - "
        "the importer reads its length limits from the database, so a value the model allows "
        "and the column refuses fails at apply time after a clean preview."
    )


def test_every_customers_varchar_matches_the_model():
    """The sweep. Fails on a migration-built database the moment a width diverges."""
    if not sa_inspect(engine).has_table(Customer.__tablename__):
        pytest.skip("no customers table on this database")

    with pg_session() as session:
        drift = _drift(svc.column_limits(session))

    assert drift == {}, _explain(drift)


def test_the_three_columns_a_real_export_hit_are_wide_enough():
    """The named regression: 58 rows of a real export died on `phone_number varchar(20)`."""
    if not sa_inspect(engine).has_table(Customer.__tablename__):
        pytest.skip("no customers table on this database")

    with pg_session() as session:
        limits = svc.column_limits(session)

    for name, expected in REALIGNED.items():
        assert MODEL_LENGTHS[name] == expected, f"the model moved {name} without this test"
        assert limits[name] == expected, (
            f"customers.{name} is varchar({limits[name]}) and the model declares "
            f"String({expected}): migration 355 has not run on this database"
        )


def test_the_guard_notices_a_narrowed_column():
    """The anti-vacuity check, and the proof that the importer reads the SCHEMA.

    `column_limits` is the same function the over-length pre-check calls, so narrowing a
    column here and seeing the new width come back is exactly the evidence that a preview run
    against a drifted database would name the rows the database is about to refuse.

    Runs on the scratch schema inside a rolled-back transaction: Postgres DDL is
    transactional, so the narrowed column exists for this test and for nothing else.
    """
    with blank_session() as session:
        session.execute(
            text("ALTER TABLE customers ALTER COLUMN industry TYPE character varying(11)")
        )

        limits = svc.column_limits(session)

        assert limits["industry"] == 11, (
            "the limits came from the model, not the database - the whole point of "
            "column_limits is that it reads the schema"
        )
        assert _drift(limits) == {"industry": (MODEL_LENGTHS["industry"], 11)}
