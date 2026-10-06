"""A Postgres session that rolls back, for tests of real database behaviour.

sqlite is the wrong substrate for anything transactional here. It does not
share Postgres's SAVEPOINT semantics, its constraint enforcement differs, and
`schema="scm"` models cannot be created on it at all -- so a sqlite test can
pass while proving nothing about production, and break the moment an unrelated
module registers a global Session listener.

Everything runs inside one outer transaction that is rolled back at teardown, so
`begin_nested()` becomes a real nested savepoint and no test data survives --
verified against the shared local database, which holds real records.

**Scope your assertions.** The tables are not empty. Counting all rows in
`warehouses` will pick up production data, so filter by whatever prefix the test
creates. `unique_code()` exists to make that easy and collision-free.
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.database import Base, engine

# Prefix reserved for test-created ROWS. Distinctive enough to filter on and to
# recognise if one ever escapes a rollback.
TEST_PREFIX = "ZZT"

# Prefix reserved for the scratch SCHEMAS this module creates. Deliberately NOT
# ``zzt_``, which is what it used to be, and the difference is the whole point.
#
# Every checkout of this repository shares one local Postgres, and several are
# checked out at once (git worktrees, one per feature). The suite's session-start
# sweep in tests/conftest.py drops leftover scratch schemas, and until recently it
# dropped EVERY schema matching ``zzt_%`` unconditionally -- including one that a
# suite running in a sibling checkout was in the middle of using. That version is
# still on main and therefore still live in those checkouts, so it is not enough
# for this one to sweep politely: it has to be UNMATCHABLE by an impolite sweeper
# it cannot change.
#
# The cost of the old behaviour was real. The victim's run reported dozens of
# ``relation "zzt_blank_....lookup_bindings" does not exist`` errors at fixture
# setup -- Postgres words a dropped SCHEMA exactly like a missing TABLE -- with no
# connection to any code anybody had touched, and the tests that had already run
# passed, so it read as "the change I am holding broke this suite" rather than
# "another process deleted my database". Hours went into bisecting an innocent
# diff. A one-word prefix is a cheap price for never paying that again.
SCRATCH_SCHEMA_PREFIX = "zzs"


# ``alpha=True`` maps each hex digit onto a letter (0->g ... 9->p), so the suffix keeps its
# 32 bits of entropy but never contains a digit.
_HEX_DIGITS_TO_LETTERS = str.maketrans("0123456789", "ghijklmnop")


def unique_code(stem: str = "", *, alpha: bool = False) -> str:
    """A collision-free code for a test-created record.

    ``alpha=True`` gives a suffix with no digits. Ask for it when the code is searched by
    substring under a test that also sends a digit-only token: eight random hex chars can
    spell "65502" (or any other run of digits a test asserts an exact result set for), and
    the app matching it is correct behaviour, so the test data has to stay out of the way.
    """
    suffix = uuid.uuid4().hex[:8]
    if alpha:
        suffix = suffix.translate(_HEX_DIGITS_TO_LETTERS)
    return f"{TEST_PREFIX}-{stem}-{suffix}" if stem else f"{TEST_PREFIX}-{suffix}"


@contextmanager
def pg_session(autoflush: bool = True) -> Session:
    """Yield a session whose work is discarded at the end.

    ``autoflush=False`` reproduces how the application actually runs: ``SessionLocal`` is
    built with ``autoflush=False``, so a row that a service has only ``db.add``ed is NOT
    visible to the next SELECT that service makes. A test session that autoflushes writes
    that row out before the read and therefore hides a whole class of defect - the "does
    this already exist?" guard that sits in front of a unique index passes under the test
    and collides at flush in production. Ask for it wherever the behaviour under test is a
    get-or-create.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=autoflush)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


_BLANK = {}


def blank_schema_engine():
    """An Engine over a blank copy of the **entire** schema, built once per run.

    This is the replacement for ``create_engine("sqlite:///:memory:")`` plus
    ``Base.metadata.create_all(engine)``, which was the dominant fixture shape
    in this suite. It gives the same thing that pattern was reaching for -- an
    empty database with every table present -- without sqlite's differences in
    typing, constraint enforcement and transaction semantics.

    Every table on ``Base.metadata`` emits in well under a second, so the cost is
    paid once for the whole session rather than per test. Data isolation is the
    caller's job: use ``blank_session()``, which discards writes.

    ``scm``, ``dealer_kit``, ``chatbot``, ``sales``, ``finance`` and ``projects`` are translated alongside the
    default schema, so those models -- which could not be created on sqlite at all -- are
    included. Any future module that declares its own schema must be added here
    too, or ``create_all`` fails on the first table it cannot place.
    """
    if "engine" not in _BLANK:
        from app import models  # noqa: F401  register every model's table

        # `app.models` does not reach the lookup and audit models, and this schema is
        # emitted ONCE from whatever is registered at that moment. So the table set
        # depended on which test file ran first: a file importing only app.models built
        # a schema with no `lookup_bindings`, and a later file importing app.main --
        # which registers the lookup-validation listener that queries it on every
        # flush -- then died with UndefinedTable. Every suite passed alone while the
        # whole run failed, which reads as flakiness rather than as an ordering bug.
        _globally_required_tables()

        # The PID is IN THE NAME so the orphan sweep can tell a schema whose
        # owner is still running from one a killed run abandoned. Belt to the
        # prefix's braces: the prefix hides this schema from a sibling checkout's
        # sweeper, the PID stops OUR sweeper dropping a schema belonging to a
        # second run of this same checkout. See
        # tests/conftest.py::_sweep_orphan_scratch_schemas.
        name = f"{SCRATCH_SCHEMA_PREFIX}_blank_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        admin = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        admin.exec_driver_sql(f'CREATE SCHEMA "{name}"')
        admin.exec_driver_sql(f'CREATE SCHEMA "{name}_scm"')
        admin.exec_driver_sql(f'CREATE SCHEMA "{name}_dealer_kit"')
        admin.exec_driver_sql(f'CREATE SCHEMA "{name}_projects"')
        admin.exec_driver_sql(f'CREATE SCHEMA "{name}_chatbot"')
        admin.exec_driver_sql(f'CREATE SCHEMA "{name}_sales"')
        admin.exec_driver_sql(f'CREATE SCHEMA "{name}_finance"')
        admin.close()

        scoped = engine.execution_options(
            schema_translate_map={
                None: name,
                "scm": f"{name}_scm",
                "dealer_kit": f"{name}_dealer_kit",
                "projects": f"{name}_projects",
                "chatbot": f"{name}_chatbot",
                "sales": f"{name}_sales",
                "finance": f"{name}_finance",
            }
        )
        # AUTOCOMMIT, so each CREATE TABLE / ADD CONSTRAINT / CREATE INDEX commits
        # and RELEASES ITS LOCKS as it goes.
        #
        # Building the whole model inside one transaction holds a lock on every
        # object at once - well over a thousand - and Postgres sizes its shared
        # lock table from `max_locks_per_transaction` (64 by default). One worker
        # fits; four xdist workers each building their own scratch schema at the
        # same time do not, and the loser dies mid-DDL with "out of shared memory"
        # in whatever test happened to be first. It reads as an unrelated flake -
        # the traceback names a random file's fixture - and it got worse every
        # time the model grew a table.
        with scoped.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            # The Sorento company row is seeded by the ``after_create`` DDL event on
            # the companies table (tests/conftest.py), which fires here during
            # create_all - so every owned insert's auto-stamped company_id resolves
            # against the FK. Nothing to seed by hand.
            Base.metadata.create_all(connection, checkfirst=False)
            # AI-ALLOWED-GATE (owner ruling 5 Oct 2026): in production a new contact is
            # blocked until a human ticks "AI replies allowed". The suite's ~150 contact
            # seeds predate the flag and expect an answering contact, so the SCRATCH
            # schema's default is flipped to allowed here, in one place, instead of
            # touching every seed. Tests about the production default assert it through
            # the migration's own SQL or the ORM default, never through this schema.
            connection.exec_driver_sql(
                f'ALTER TABLE "{name}".respond_contacts ALTER COLUMN chatbot_allowed SET DEFAULT true'
            )

        _BLANK["engine"] = scoped
        _BLANK["name"] = name
    return _BLANK["engine"]


def drop_blank_schema():
    """Tear down the shared blank schema. Called from the session fixture."""
    name = _BLANK.pop("name", None)
    _BLANK.pop("engine", None)
    if name:
        admin = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        admin.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        admin.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}_scm" CASCADE')
        admin.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}_dealer_kit" CASCADE')
        admin.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}_projects" CASCADE')
        admin.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}_chatbot" CASCADE')
        admin.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}_sales" CASCADE')
        admin.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}_finance" CASCADE')
        admin.close()


@contextmanager
def blank_session() -> Session:
    """A session over the blank schema whose writes are all discarded.

    ``join_transaction_mode="create_savepoint"`` is what makes this work with
    tests that call ``commit()``: the session's commits land on a savepoint
    inside the outer transaction, so they are visible to the test and to any
    code under it, and the outer rollback still discards everything. Without it
    a committing test would leak rows into the next one.
    """
    connection = blank_schema_engine().connect()
    transaction = connection.begin()

    # schema_translate_map only rewrites ORM/Table constructs. A route issuing
    # raw text() SQL -- and several do -- resolves its table names through
    # search_path instead, which would send those writes to the REAL public
    # tables while the test read an empty scratch schema and saw nothing.
    #
    # That failure is silent and writes live data, so pin search_path too. SET
    # LOCAL scopes it to this transaction, which is discarded below.
    #
    # Every module schema is listed, or raw SQL naming one of its tables resolves
    # against nothing.
    #
    # ORDER MATTERS, and the projects schema must come LAST before `public`. The default
    # schema stays first so `current_schema()` is still `{name}` (migration 354 reads it
    # to find where the tables are). The projects schema trails the module schemas
    # because seven of its bare names -- brands, purchase_orders, purchase_order_lines,
    # sales_orders, sales_order_lines, quotations, quotation_lines -- also exist as CORE
    # tables, and unqualified raw SQL naming one of those means the core one. Put
    # `{name}_projects` earlier and seven core tables silently repoint at the module's.
    # `scm` and `dealer_kit` share no bare name with core, so their position is free.
    # `sales` does (`teams`, `team_members`), and like projects it trails the default schema,
    # so unqualified raw SQL naming either still means core's Users & Access table. `finance`
    # shares no bare name with core; it sits beside `sales`.
    #
    # `public` is LAST, deliberately trailing every scratch schema: every table on
    # `Base.metadata` was just created in one of the schemas above, so a real table name
    # always resolves there first and this adds no exposure for tables. What it DOES add
    # is Postgres EXTENSION objects that live only in `public` and are never part of
    # `Base.metadata` at all - `pg_trgm`'s `similarity()` chief among them, which several
    # resolver probes call by bare name. Without `public` on the path those calls raise
    # `UndefinedFunction`, not "found nothing", which is a worse failure than the
    # exposure this trades for it (measured: `tests/chatbot/test_rearch_s0_*.py` and
    # `test_rearch_s2_*.py` pass unchanged with `public` appended here).
    name = _BLANK["name"]
    connection.exec_driver_sql(
        f'SET LOCAL search_path TO "{name}", "{name}_scm", "{name}_dealer_kit", '
        f'"{name}_chatbot", "{name}_sales", "{name}_finance", "{name}_projects", "public"'
    )

    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _globally_required_tables():
    """Tables the app's global SQLAlchemy listeners query on every flush.

    Importing ``app.main`` -- which the TestClient suites must do -- registers
    audit and lookup-validation listeners. Those fire on any flush and query
    these tables regardless of what the test is doing, so a scratch schema
    without them fails with UndefinedTable on the first insert.
    """
    from app.models.audit import AuditLog
    from app.models.lookup import LookupBinding, LookupOption, LookupSet

    return [
        LookupBinding.__table__,
        LookupOption.__table__,
        LookupSet.__table__,
        AuditLog.__table__,
    ]


def _with_dependencies(tables):
    """Close the set over foreign keys, in dependency order.

    Everything reachable is included, whatever schema it declares. It used to skip
    ``table.schema is not None`` because the translate map only redirected the default
    schema, and that skip became a trap the moment the projects module moved into its
    own schema: ``complaints`` and ``purchase_requests`` carry a foreign key to
    ``projects.projects``, so a caller passing either would have been handed a schema
    whose FK target was silently missing. ``pg_empty_schema`` now translates all three
    schemas, so nothing needs skipping.
    """
    seen: dict[str, object] = {}

    def visit(table):
        if table.key in seen:
            return
        seen[table.key] = table
        for fk in table.foreign_keys:
            visit(fk.column.table)

    for table in tables:
        visit(table)

    return list(seen.values())


@contextmanager
def pg_empty_schema(tables) -> Session:
    """A session over an *empty* set of the given tables, still on Postgres.

    Some behaviour is only meaningful against a blank slate -- seeding, chiefly:
    "creates one integration per entry" cannot be asserted on the real database,
    where migration 297 already created them and the seed is a no-op.

    sqlite was the old way to get that blank slate, at the cost of testing
    against a schema with different types, no enforced foreign keys, and no
    JSONB. Instead this creates the tables in a throwaway Postgres schema via
    ``schema_translate_map``, so the DDL is the real DDL and only the data is
    empty. The schema is dropped afterwards.

    ``tables`` is an explicit list because emitting the full metadata wholesale is
    what ``blank_schema_engine`` is for. Whatever those tables reference is pulled in
    for you -- Postgres validates FK targets at DDL time where sqlite did not, so an
    incomplete list fails loudly here instead of silently not enforcing.

    Every module schema is translated (``scm``, ``projects``, ``chatbot``, ``sales`` and ``finance``
    alongside the default),
    mirroring ``blank_schema_engine``: a model that declares a schema must land in a
    scratch copy of it, never in the REAL ``projects`` schema, and a caller reaching
    one through a foreign key must not have it quietly dropped.
    """
    tables = _with_dependencies(list(tables) + _globally_required_tables())
    name = f"{SCRATCH_SCHEMA_PREFIX}_scratch_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    admin = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    admin.exec_driver_sql(f'CREATE SCHEMA "{name}"')
    admin.exec_driver_sql(f'CREATE SCHEMA "{name}_scm"')
    admin.exec_driver_sql(f'CREATE SCHEMA "{name}_projects"')
    admin.exec_driver_sql(f'CREATE SCHEMA "{name}_chatbot"')
    admin.exec_driver_sql(f'CREATE SCHEMA "{name}_sales"')
    admin.exec_driver_sql(f'CREATE SCHEMA "{name}_finance"')
    admin.close()

    scoped = engine.execution_options(
        schema_translate_map={
            None: name,
            "scm": f"{name}_scm",
            "projects": f"{name}_projects",
            "chatbot": f"{name}_chatbot",
            "sales": f"{name}_sales",
            "finance": f"{name}_finance",
        }
    )
    connection = scoped.connect()
    try:
        # create_all rather than per-table create(): it sorts by dependency and
        # breaks FK cycles with a follow-up ALTER, which a naive loop cannot.
        Base.metadata.create_all(connection, tables=tables, checkfirst=True)
        # Same scratch-schema default flip as `blank_schema_engine` (AI-ALLOWED-GATE).
        if "respond_contacts" in {t.name for t in tables}:
            connection.exec_driver_sql(
                f'ALTER TABLE "{name}".respond_contacts ALTER COLUMN chatbot_allowed SET DEFAULT true'
            )
        connection.commit()

        session = Session(bind=connection)
        try:
            yield session
        finally:
            session.close()
    finally:
        connection.close()
        cleanup = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        cleanup.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        cleanup.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}_scm" CASCADE')
        cleanup.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}_projects" CASCADE')
        cleanup.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}_chatbot" CASCADE')
        cleanup.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}_sales" CASCADE')
        cleanup.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}_finance" CASCADE')
        cleanup.close()
