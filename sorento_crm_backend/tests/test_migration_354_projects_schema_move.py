"""Migration 354 has to be a no-op on a fresh database and do the work on an old one.

The projects module's 47 tables move from the default schema into `projects`, and the 34
that carried the `project_` prefix drop it on the way (ADR-0011). A database built by
`create_all` from the post-move models arrives with the tables already there and already
named; the shared development database arrives with all 47 sitting in `public` under their
old names. One revision serves both starting schemas, and every step is guarded on "the
source is there and the destination is not".

That guard is the whole revision, and it is the kind of thing never exercised until a
deploy: CI takes the no-op path, so a bug in the moving path ships, and a developer whose
database takes the moving path never sees the no-op path break. Both are run here.

The revision is imported and executed against a scratch schema pair rather than asserted
about the live database, per the pattern in CLAUDE.md and in
`test_migration_353_order_inquiry_rename.py`. Its `TARGET_SCHEMA` is rebound to the
fixture's scratch projects schema first: the module resolves its SOURCE schema from
`current_schema()` for the same reason, so neither end of the move can reach past the
scratch schemas into the real database and move production tables.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.database import Base
from app.models.base import set_company_scope
from app.models.project_so import OrderInquiry, ProjectSalesOrder
from app.models.projects import Project, ProjectLead
from tests._pg_fixture import blank_schema_engine, blank_session, unique_code

SORENTO = "00000000-0000-0000-0000-000000000001"

# Tables that declare the `projects` schema but were CREATED after the move, so revision
# 354 has never heard of them: they have no pre-move name, no derived-index rename, and
# both of 354's directions leave them exactly where their own migration put them.
BORN_AFTER_THE_MOVE = frozenset({
    "so_supply_decisions",
    # OI-LINE-SPLIT (migration ols_0001_so_line_splits): the delivery splits staff plan for one
    # SO line, born straight into `projects` beside `sales_order_lines`.
    "so_line_splits",
    # PLAN-so-book-diff-replanning.md: born straight into `projects` by their own migration
    # (29d85dc3ccc3), same as `so_supply_decisions` above.
    "planning_change_batches",
    "planning_change_rows",
    # PLAN-scm-cs-planning-uat.md, migration 421_order_inquiry_links: one inquiry row's
    # quantity placed across several documents.
    "order_inquiry_links",
    # OI-RESERVE-RECEIVED (migration oirr_0001_released_links): born into `projects`.
    "order_inquiry_released_links",
    # PLAN-scm-fulfilment-feedback.md: the planning module's own move-stock artifact.
    "stock_transfers",
    # PLAN-scm-fulfilment-feedback-2sep.md S4, migration 461: saved-but-not-yet-confirmed
    # board decisions, born straight into `projects` the same way `so_supply_decisions` was.
    "so_supply_decision_drafts",
    # PLAN-oi-header-list-detail.md S1, migration 523_oi_monthly_no_raises: one raise/
    # reconfirm history row per header, born straight into `projects` the same way.
    "order_inquiry_raises",
    # PLAN-oi-request-cs-reserve.md S1, migration oirs_0001_reserve_requests: one "request
    # CS to reserve" ask per inquiry plus its per-row answers, born straight into
    # `projects` the same way.
    "order_inquiry_reserve_requests",
    "order_inquiry_reserve_request_rows",
    # PLAN-oi-request-cs-reserve.md section 6c, migration oirs_0002_reserve_round2: one
    # history row per reserve/unreserve on a reserve request row (F3), born straight into
    # `projects` the same way.
    "order_inquiry_reserve_events",
    # PLAN-oi-links-autocount-truth-24sep.md (migration oisl_0001_suggested_links, issue
    # #1215): a guess the cascade walk made, never a placement, born straight into
    # `projects` the same way as its sibling `order_inquiry_links`.
    "order_inquiry_suggested_links",
    # PLAN-set-quote-line-14oct.md, migration sql_0001_set_quote_line (#1682): one product
    # set priced into a series, born straight into `projects` beside `series_products`.
    "series_sets",
    # PLAN-series-filters-15oct.md, migration sf_0001_series_brands (#1697): the brands a
    # series picks, born straight into `projects` beside `series_categories`.
    "series_brands",
    # PLAN-stock-debt-snapshot-15oct.md, migration sds_0001_stock_debt_snapshots (#1705): a
    # frozen Stock Debt view and its rows, born straight into `projects` (ADR-0011).
    "stock_debt_snapshots",
    "stock_debt_snapshot_rows",
    # SR-CORE (#1718, migration sr_0001_sample_requests): born straight into `projects`.
    "sample_requests",
    "sample_request_lines",
})

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "354_projects_schema_move.py"
)


def _scratch_projects_schema() -> str:
    """The scratch stand-in for the real `projects` schema, from the fixture's own map."""
    options = blank_schema_engine().get_execution_options()
    return options["schema_translate_map"]["projects"]


def _scratch_default_schema() -> str:
    options = blank_schema_engine().get_execution_options()
    return options["schema_translate_map"][None]


@pytest.fixture
def db():
    """A scratch schema pair built by ``create_all``, so it carries the POST-move shape.

    Everything here is DDL, and Postgres runs DDL inside the transaction the fixture rolls
    back, so both scratch schemas are unchanged afterwards.
    """
    with blank_session() as session:
        yield session


def _module():
    spec = importlib.util.spec_from_file_location("m354", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Point the revision at the scratch projects schema. Without this the ALTERs would
    # name the REAL `projects` schema, and the test would move production tables.
    module.TARGET_SCHEMA = _scratch_projects_schema()
    return module


def _run(db, direction: str) -> None:
    module = _module()
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        getattr(module, direction)()


def _schemas() -> list[str]:
    return [_scratch_default_schema(), _scratch_projects_schema()]


def _tables(db) -> set[str]:
    """Every module table, as `schema.table`, across BOTH scratch schemas.

    Filtering on `current_schema()` alone - which is what the 353 test does - would see an
    empty set after the move and pass regardless of what the revision did.

    In the default schema only the PRE-move names count. Seven of the post-move bare names
    (`brands`, `purchase_orders`, `sales_orders` and friends) are also CORE tables sitting
    right there, and counting those would make the assertion drift with core rather than
    with this revision. The pre-move names carry the `project_` prefix precisely so they
    cannot be confused, and the 13 that never carried it collide with nothing.
    """
    projects_schema = _scratch_projects_schema()
    old_names = sorted(old for old, _ in _module().TABLES)
    rows = db.execute(
        text(
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE schemaname = :projects "
            "   OR (schemaname = :default AND tablename = ANY(:old_names))"
        ),
        {
            "projects": projects_schema,
            "default": _scratch_default_schema(),
            "old_names": old_names,
        },
    )
    return {f"{row[0]}.{row[1]}" for row in rows}


def _scope_params() -> dict:
    return {
        "projects": _scratch_projects_schema(),
        "default": _scratch_default_schema(),
        "old_names": sorted(old for old, _ in _module().TABLES),
    }


#: Same scoping as ``_tables``: module tables only, never the core table that happens to
#: share a bare name. Including core's `brands_pkey` here would make the round-trip
#: expectation below rewrite a core index name.
_MODULE_TABLE_SCOPE = (
    "(n.nspname = :projects OR (n.nspname = :default AND c.relname = ANY(:old_names)))"
)


def _indexes(db) -> set[str]:
    rows = db.execute(
        text(
            "SELECT n.nspname, ic.relname FROM pg_index i "
            "JOIN pg_class ic ON ic.oid = i.indexrelid "
            "JOIN pg_class c ON c.oid = i.indrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE {_MODULE_TABLE_SCOPE}"
        ),
        _scope_params(),
    )
    return {f"{row[0]}.{row[1]}" for row in rows}


def _constraints(db) -> set[str]:
    rows = db.execute(
        text(
            "SELECT n.nspname, c.relname, con.conname FROM pg_constraint con "
            "JOIN pg_class c ON c.oid = con.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE {_MODULE_TABLE_SCOPE}"
        ),
        _scope_params(),
    )
    return {f"{row[0]}.{row[1]}.{row[2]}" for row in rows}


def _snapshot(db):
    return (_tables(db), _indexes(db), _constraints(db))


def _metadata_index_names() -> set[str]:
    """Every index name ``Base.metadata`` declares for the 47 moved tables.

    This is what ``create_all`` writes, and therefore what a bootstrapped CI or
    disaster-recovery database carries. A migrated database has to end up with the same
    set, or every autogenerate run afterwards reports the difference as drift.
    """
    from app import models  # noqa: F401  register every model

    return {
        index.name
        for table in Base.metadata.tables.values()
        if table.schema == "projects"
        for index in table.indexes
    }


def _derived_index_map_from_metadata() -> set[tuple[str, str]]:
    """Rebuild the revision's index-rename map from the models.

    SQLAlchemy names an unnamed index `ix_%(column_0_label)s`, and a schema-qualified
    table folds its SCHEMA into that label - so declaring `schema="projects"` silently
    renamed `ix_project_leads_company_id` to `ix_projects_leads_company_id` in the metadata
    while the database kept the old name. An index the model names explicitly
    (`ix_project_parties_name`) is unaffected and must NOT be renamed, and the two are
    indistinguishable in the catalog: both are a single-column index called
    `ix_<pre-move table>_<column>`. So the map is a constant inside the revision, and this
    rebuilds it from the only source that can tell the two apart.
    """
    from app import models  # noqa: F401  register every model

    old_by_new = {new: old for old, new in _module().TABLES}
    pairs: set[tuple[str, str]] = set()
    for table in Base.metadata.tables.values():
        if table.schema != "projects" or table.name in BORN_AFTER_THE_MOVE:
            continue
        old = old_by_new[table.name]
        for index in table.indexes:
            columns = list(index.columns)
            if len(columns) != 1:
                continue  # a multi-column index is never convention-named
            if index.name != f"ix_{columns[0]._ddl_label}":
                continue  # named by hand in the model; the move did not touch it
            pairs.add((f"ix_{old}_{columns[0].name}", index.name))
    return pairs


# --------------------------------------------------------------------------- #
# the mapping itself
# --------------------------------------------------------------------------- #

def test_the_mapping_covers_exactly_the_models_that_declare_the_projects_schema():
    """The revision's table list and the model files must agree, table for table.

    Two independent sources of the same fact, which is the point: a model moved into the
    schema without a line in the revision would never reach a migrated database, and a line
    in the revision with no model behind it would move a table nothing reads.
    """
    module = _module()
    from app import models  # noqa: F401  register every model

    declared = {
        table.name
        for table in Base.metadata.tables.values()
        if table.schema == "projects"
    } - BORN_AFTER_THE_MOVE

    assert len(module.TABLES) == 47
    assert {new for _, new in module.TABLES} == declared
    assert len({old for old, _ in module.TABLES}) == 47


# --------------------------------------------------------------------------- #
# the fresh database - CI, and every new install
# --------------------------------------------------------------------------- #

def test_upgrade_changes_nothing_when_the_tables_are_already_in_the_projects_schema(db):
    """A schema built from the current models is already moved. 354 must not raise."""
    target = _scratch_projects_schema()
    before = _snapshot(db)

    assert {name for name in before[0] if name.startswith(f"{target}.")}, (
        "the scratch schema pair is not the post-move one"
    )

    _run(db, "upgrade")

    assert _snapshot(db) == before


def test_upgrade_is_repeatable(db):
    """Twice is the same as once. A guard that only holds on the first pass is not a guard."""
    _run(db, "upgrade")
    before = _snapshot(db)

    _run(db, "upgrade")

    assert _snapshot(db) == before


# --------------------------------------------------------------------------- #
# the database that predates the move - the shared development one
# --------------------------------------------------------------------------- #

def test_upgrade_moves_and_renames_when_the_tables_are_in_the_default_schema(db):
    """Build the pre-354 schema with the revision's own downgrade, then move it back."""
    module = _module()
    default = _scratch_default_schema()
    target = _scratch_projects_schema()
    expected = _snapshot(db)

    _run(db, "downgrade")

    after_down = _tables(db)
    # A table born after the move stays in the projects schema through 354's downgrade:
    # the revision's TABLES list has never heard of it.
    stayed = {f"{target}.{name}" for name in BORN_AFTER_THE_MOVE}
    assert after_down == {f"{default}.{old}" for old, _ in module.TABLES} | stayed, (
        "downgrade did not produce the pre-move schema"
    )
    assert not [
        name
        for name in after_down
        if name.startswith(f"{target}.") and name not in stayed
    ]

    _run(db, "upgrade")

    # EXACT, not modulo anything. Both directions rename the derived index and constraint
    # names, so a create_all schema taken down and brought back is the same catalog it
    # started as - which is the whole point: a bootstrapped database and a migrated one
    # must not disagree about a single identifier.
    assert _snapshot(db) == expected


def test_downgrade_moves_a_colliding_table_back_beside_its_core_namesake(db):
    """`projects.brands` has to land next to CORE `brands` without either losing its key.

    Index names are unique per SCHEMA. A schema built by create_all calls the module key
    `brands_pkey`, and CORE `brands` already owns that name in the default schema, so a
    plain SET SCHEMA is refused outright - which is what this revision did before the
    downgrade learned to restore derived names. Five of the seven colliding tables hit it.
    """
    default = _scratch_default_schema()

    _run(db, "downgrade")

    names = {name.rpartition(".")[2] for name in _indexes(db)}
    assert "project_brands_pkey" in names, "the module key did not come back prefixed"
    assert f"{default}.project_brands" in _tables(db)

    # Core's own key is untouched and still where it was.
    assert (
        db.execute(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace WHERE n.nspname = :s AND c.relname = 'brands_pkey'"
            ),
            {"s": default},
        ).scalar()
        == 1
    )


def test_an_index_the_model_names_by_hand_is_left_alone(db):
    """Only convention-derived names move. A hand-written one is already agreed on.

    `ix_project_parties_name` and `uq_projects_company_developer_title` are spelled out in
    `__table_args__`, so the metadata and the catalog have always said the same thing about
    them and there is nothing to unify. Pinned because the pre-move shape of a
    CONVENTION-derived name is `ix_<pre-move table>_<column>`, which is exactly the shape
    of these two - a rename rule inferred from the catalog rather than from the models
    would take them with it.
    """
    target = _scratch_projects_schema()

    assert f"{target}.ix_project_parties_name" in _indexes(db)
    assert f"{target}.uq_projects_company_developer_title" in _indexes(db)

    _run(db, "downgrade")
    _run(db, "upgrade")

    assert f"{target}.ix_project_parties_name" in _indexes(db)
    assert f"{target}.uq_projects_company_developer_title" in _indexes(db)


# --------------------------------------------------------------------------- #
# the derived names - the reason a bootstrapped database and a migrated one agree
# --------------------------------------------------------------------------- #

def test_the_index_rename_map_is_the_one_the_models_derive():
    """The constant in the revision, regenerated from `Base.metadata`.

    The map has to be a constant (the catalog cannot distinguish a convention-derived name
    from a hand-written one), so this is the guard that keeps it honest: add a column with
    `index=True` to a projects model, or drop one, and this fails until the revision is
    updated to match.
    """
    module = _module()

    assert set(module.DERIVED_INDEXES) == _derived_index_map_from_metadata()
    assert len(module.DERIVED_INDEXES) == len(set(module.DERIVED_INDEXES))


def test_no_renamed_identifier_exceeds_the_postgres_limit():
    """Postgres truncates an identifier past 63 bytes, silently, at both ends of a rename.

    A truncated name would neither match the metadata nor round-trip, and the failure would
    only ever show on a real database. Cheaper to assert here.
    """
    module = _module()

    too_long = [
        name
        for pair in module.DERIVED_INDEXES
        for name in pair
        if len(name.encode("utf-8")) > 63
    ]
    assert not too_long, too_long


def test_a_migrated_database_ends_with_the_names_create_all_writes(db):
    """The MUST-FIX: bootstrapped and migrated databases must agree, identifier for
    identifier.

    `scripts/bootstrap_env.py` + `create_all` is how CI and a disaster-recovery instance
    are built; `alembic upgrade head` is how production and every developer machine got
    there. Alembic compares indexes BY NAME, so one convention-derived name that differs
    between the two is permanent autogenerate churn on one of them.

    The scratch pair here is a create_all schema, so `downgrade()` builds the pre-354
    database and `upgrade()` has to land back on exactly the create_all names.
    """
    target = _scratch_projects_schema()
    declared = _metadata_index_names()

    _run(db, "downgrade")

    after_down = {name.rpartition(".")[2] for name in _indexes(db)}
    assert "ix_project_leads_company_id" in after_down, (
        "the pre-354 database is the one that carries the unqualified derived name"
    )
    assert "ix_projects_leads_company_id" not in after_down

    _run(db, "upgrade")

    present = {
        name.rpartition(".")[2]
        for name in _indexes(db)
        if name.startswith(f"{target}.")
    }
    assert declared <= present, sorted(declared - present)


def test_a_postgres_default_constraint_name_follows_the_table(db):
    """`project_brands_pkey` becomes `brands_pkey`, which is what create_all calls it.

    Postgres derives a primary key name from the table name at CREATE time and never
    revisits it, so a migrated database keeps `project_brands_pkey` inside the `projects`
    schema while a bootstrapped one says `brands_pkey`. `\\d` disagreeing between the two
    is the readable half of the same problem the index names cause for autogenerate.
    """
    target = _scratch_projects_schema()

    _run(db, "downgrade")
    assert f"{target}.project_brands_pkey" not in _indexes(db)  # it moved out with its table

    _run(db, "upgrade")

    names = {name.rpartition(".")[2] for name in _indexes(db) if name.startswith(f"{target}.")}
    assert "brands_pkey" in names
    assert "project_brands_pkey" not in names


# --------------------------------------------------------------------------- #
# the rows that name a table rather than living in one
# --------------------------------------------------------------------------- #

def _seed_binding(db, table_name: str) -> str:
    """One lookup set with one binding on ``table_name``.status. Returns the binding id."""
    default = _scratch_default_schema()
    set_id = str(uuid4())
    binding_id = str(uuid4())
    db.execute(
        text(
            f'INSERT INTO "{default}"."lookup_sets" (id, set_key, name, is_active) '
            "VALUES (:id, :key, :name, true)"
        ),
        {"id": set_id, "key": f"zzt_{uuid4().hex[:12]}", "name": "ZZT Schema Move"},
    )
    db.execute(
        text(
            f'INSERT INTO "{default}"."lookup_bindings" (id, set_id, table_name, column_name) '
            "VALUES (:id, :set_id, :table_name, 'status')"
        ),
        {"id": binding_id, "set_id": set_id, "table_name": table_name},
    )
    return binding_id


def _binding_table(db, binding_id: str) -> str:
    default = _scratch_default_schema()
    return db.execute(
        text(f'SELECT table_name FROM "{default}"."lookup_bindings" WHERE id = :id'),
        {"id": binding_id},
    ).scalar()


def test_a_binding_on_a_moved_table_is_repointed_at_the_qualified_name(db):
    """`lookup_bindings.table_name` is a table name stored as data, so 354 has to move it.

    A binding is keyed by the SCHEMA-QUALIFIED name (`app/services/lookup_eligibility.py`),
    because the bare one stopped identifying a table when seven of them started existing
    twice. Left as `project_purchase_orders` the binding matches nothing at all; left as
    `purchase_orders` it would police core's table instead.
    """
    target = _scratch_projects_schema()
    module = _module()
    moved = _seed_binding(db, "project_purchase_orders")
    core = _seed_binding(db, "purchase_orders")

    _run(db, "upgrade")

    assert _binding_table(db, moved) == f"{target}.purchase_orders"
    # The core binding shares the post-move BARE name and must not be touched.
    assert _binding_table(db, core) == "purchase_orders"

    _run(db, "downgrade")

    assert _binding_table(db, moved) == "project_purchase_orders"
    assert _binding_table(db, core) == "purchase_orders"
    assert len(module.TABLES) == 47  # the rewrite covers the same list as the move


def test_a_binding_on_an_unprefixed_moved_table_gains_the_schema(db):
    """The 13 that kept their bare name still change key: `so_amendments` is now
    `projects.so_amendments`, and nothing else in the database is called that."""
    target = _scratch_projects_schema()
    binding = _seed_binding(db, "so_amendments")

    _run(db, "upgrade")

    assert _binding_table(db, binding) == f"{target}.so_amendments"


def test_rows_travel_with_the_tables(db):
    """A move, not a re-create: the row in the old table is the row in the new one.

    `ALTER TABLE ... SET SCHEMA` is metadata-only, which is the whole argument for doing
    this on a database that already holds project rows. Asserted rather than assumed.
    """
    set_company_scope(db, frozenset({SORENTO}))
    default = _scratch_default_schema()
    target = _scratch_projects_schema()

    lead = ProjectLead(
        lead_code=unique_code("lead"),
        title="ZZT Schema Move Lead",
        normalised_title=unique_code("zzt schema move"),
    )
    db.add(lead)
    db.flush()
    project = Project(
        project_code=unique_code("proj"),
        title="ZZT Schema Move Tower",
        normalised_title=unique_code("zzt schema move tower"),
        lead_id=lead.id,
    )
    db.add(project)
    db.flush()
    sales_order = ProjectSalesOrder(
        project_id=project.id, provisional_ref=unique_code("SO")
    )
    db.add(sales_order)
    db.flush()
    inquiry = OrderInquiry(project_sales_order_id=sales_order.id)
    db.add(inquiry)
    db.flush()
    inquiry_id = str(inquiry.id)

    # Detach the identity map before the tables move out from under it.
    db.expunge_all()

    _run(db, "downgrade")
    assert (
        db.execute(
            text(
                f'SELECT count(*) FROM "{default}"."project_order_inquiries" WHERE id = :i'
            ),
            {"i": inquiry_id},
        ).scalar()
        == 1
    ), "the row did not travel with the table on the way down"

    _run(db, "upgrade")

    assert (
        db.execute(
            text(f'SELECT count(*) FROM "{target}"."order_inquiries" WHERE id = :i'),
            {"i": inquiry_id},
        ).scalar()
        == 1
    )
