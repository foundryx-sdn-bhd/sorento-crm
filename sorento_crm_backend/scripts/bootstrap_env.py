"""Provision an empty Postgres database into a runnable Sorento CRM schema.

This is the reproducible environment bootstrap: it is what CI uses to build a
test database, and what a brand-new environment (or a disaster-recovery restore)
needs before the app can start.

WHY THIS EXISTS RATHER THAN `alembic upgrade head`
--------------------------------------------------
The migration chain cannot build the schema from zero. Its first revision
(``001_add_is_responded_to_sla_tracking``) *alters* ``conversation_sla_tracking``,
and no migration ever creates that table - the original database was built by
SQLAlchemy ``create_all`` and every migration since has only ALTERed it. Running
``alembic upgrade head`` against an empty database therefore dies at revision
``008`` with "relation conversation_sla_tracking does not exist".

So the ORM models are the source of truth for the schema, exactly as they were
originally. This script makes that explicit and repeatable instead of implicit
and lost. Once the schema exists it is stamped at head so future migrations
apply normally.

Squashing the historical migrations into a real baseline revision would let
``alembic upgrade head`` work from zero and is the better long-term fix, but it
requires re-stamping existing databases - a separate, coordinated change.

USAGE
-----
    DATABASE_URL=postgresql://localhost/sorento_ci python -m scripts.bootstrap_env

Idempotent: safe to re-run against an already-bootstrapped database.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("bootstrap")


def _require_db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        log.error("DATABASE_URL is not set")
        sys.exit(1)
    return url


def create_schema() -> None:
    """Create every table declared by the ORM models, plus the module schemas.

    The `CREATE SCHEMA` step is not optional decoration. ``create_all`` emits
    ``CREATE TABLE scm.x`` / ``CREATE TABLE projects.x`` and never creates the schema
    itself, so a from-zero database (CI, disaster recovery) dies here with
    ``schema "projects" does not exist`` - a failure that never reproduces on a
    developer machine, where migration 273 or 354 already made the schema.
    """
    import app.models  # noqa: F401 - registers every model on Base.metadata
    from sqlalchemy import text

    from app.database import Base, engine

    with engine.begin() as conn:
        # Every Postgres schema any model declares, READ OFF THE MODELS.
        #
        # `create_all` creates TABLES, never the schema that holds them, so a
        # model naming one Postgres has never heard of aborts the whole
        # bootstrap with "schema does not exist" - and bootstrap is the ONLY way
        # this database is built from zero, in CI and anywhere else. That is how
        # `dealer_kit` broke it: the list here said `scm` and nobody remembered
        # to add the second one, and `projects` (ADR-0011) is the third.
        #
        # Derived rather than listed for exactly that reason. A hand-maintained
        # list is a step somebody has to remember at the moment they are thinking
        # about something else, and it fails in the one environment nobody runs
        # by hand. Base.metadata already knows the answer.
        for schema in sorted(
            {table.schema for table in Base.metadata.tables.values() if table.schema}
        ):
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        # pgvector / trigram are used by the embedding + entity-resolver paths.
        for ext in ("vector", "pg_trgm"):
            try:
                conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
            except Exception as exc:  # noqa: BLE001
                log.warning("extension %s unavailable: %s", ext, exc)

    Base.metadata.create_all(engine)
    log.info("schema created from ORM models")


def create_views() -> None:
    """Create the `scm` reporting views.

    The DDL lives in migration 274 as module-level constants; it is imported
    from there rather than duplicated so the migration stays the single source
    of truth for the view definitions.
    """
    import importlib.util
    from pathlib import Path

    from sqlalchemy import text

    from app.database import engine

    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"

    def _load(filename: str):
        spec = importlib.util.spec_from_file_location(
            f"_scm_views_{filename}", versions / filename
        )
        module = importlib.util.module_from_spec(spec)
        # The migrations import `alembic.op` only inside functions, so loading one
        # for its module-level constants is safe.
        spec.loader.exec_module(module)
        return module

    base = _load("274_scm_m0_views_reg.py")

    ordered = [
        base._ON_ORDER_V,
        base._COMMITTED_V,
        base._CONSUMPTION_V,
        base._RECEIPT_LEAD_V,
        base._NET_POSITION_V,
    ]

    # A later revision may redefine a view. Because this function executes DDL instead
    # of running migration bodies, those redefinitions have to be replayed here too or a
    # freshly built database silently keeps the original definition while every migrated
    # database has the new one. Appended in revision order so the last write wins.
    ordered.extend(_load("311_scm_purchasing_base.py")._REDEFINED_VIEWS)
    # 327 re-emits scm.on_order_v from 311's own constant (311's DDL was edited in place
    # after it had already been stamped somewhere, so databases at 311 can hold the old
    # definition). Replayed here too, in revision order, rather than assumed identical.
    ordered.extend(_load("327_scm_coverage_config.py")._REDEFINED_VIEWS)
    # 337 redefines on_order_v to read the SPO allocation (a purchase order is an order
    # placed, not stock incoming) and adds po_ordered_v under the old body's honest name.
    _m337 = _load("337_scm_on_order_from_spo.py")
    ordered.extend([_m337.ON_ORDER_FROM_SPO, _m337.PO_ORDERED_V])
    # 420 moved every SPO document into `spo_allocations` and redefines on_order_v to match:
    # a LEFT JOIN to the shipment (a shipping order with no container booked yet is still
    # stock on its way, and 337's inner join dropped every one of them) plus
    # `warehouse_id IS NOT NULL`. Its body is a function of the bind rather than a constant,
    # so it is emitted inside the connection below instead of joining `ordered`. Without this
    # replay a bootstrapped database keeps 337's body while every migrated database holds
    # 420's, and the two disagree by the whole unshipped book - which is how CI read 90 units
    # on order where the dev database read 115.
    _m420 = _load("420_spo_docs_in_allocations.py")
    # 501 adds `warehouse_segment` to `consumption_v` (S7, PLAN-reorder-feedback-9sep.md) -
    # the level suggestion's retail-only filter reads it. Appended last, after every other
    # replay above, since it is the latest revision in the chain to touch a view here.
    ordered.extend(_load("501_consumption_v_wh_segment.py")._REDEFINED_VIEWS)
    with engine.begin() as conn:
        for ddl in ordered:
            # The migration's DDL uses bare CREATE VIEW; make re-runs idempotent.
            conn.execute(text(ddl.replace("CREATE VIEW", "CREATE OR REPLACE VIEW", 1)))
        conn.execute(text(_m420.on_order_from_spo_documents(conn)))
    log.info("scm views created (%d)", len(ordered) + 1)
    _fix_committed_v()


def _fix_committed_v() -> None:
    """Bring ``scm.committed_v`` current past migration 337.

    ``create_views()`` above only replays the view DDL embedded in migrations 274, 311,
    327 and 337 - the ones that ship their body as a module-level constant. Migrations
    340 and 346 redefine ``scm.committed_v`` again (the ``qty_required`` /
    ``purchasing_status`` / ``demand_class`` / ``demand_origin`` rules) but inline their
    ``op.execute(...)`` DDL instead of exporting a constant, so a bootstrapped database
    silently keeps the 337 body: every committed-demand figure it computes is wrong.

    Rather than add a fifth migration to the replay list (and a sixth, a seventh, every
    time the view is touched again), this imports the CURRENT view body from
    ``app.services.scm.demand.COMMITTED_V_SQL`` - the single source of truth the demand
    service itself relies on - and lays it down with ``CREATE OR REPLACE``. That module
    is edited whenever the view changes, so this call always reflects the latest rule
    with no migration bookkeeping required. Verified by asserting the newest rule's
    marker actually appears in the resulting view definition: ``ack_state`` (migration
    428, the order-inquiry handshake; ``demand_origin`` was the marker until migration
    426 dropped the sheet leg). Move the marker when the view gains a newer rule.
    """
    from sqlalchemy import text

    from app.database import engine
    from app.services.scm.demand import COMMITTED_V_SQL

    with engine.begin() as conn:
        conn.execute(text(COMMITTED_V_SQL))
    with engine.connect() as conn:
        definition = conn.execute(text(
            "SELECT definition FROM pg_views WHERE schemaname = 'scm' "
            "AND viewname = 'committed_v'"
        )).scalar()
    if not definition or "ack_state" not in definition:
        raise SystemExit(
            "bootstrap: scm.committed_v fix did not take effect (no ack_state rule "
            "in the view definition)."
        )
    log.info("scm.committed_v is current (ack_state rule present)")


def apply_migration_only_ddl() -> None:
    """Lay down schema that only ever existed inside a migration body, never in a model.

    `create_schema()` builds tables from the ORM models via `create_all`, so anything a
    migration added without a matching model column/table is silently absent on a
    bootstrapped database, and the affected endpoint 500s at read time. Known cases,
    found by running the real app against a from-zero database:

    * migration 351 (`scm.reorder_policy.margin_floor_pct`, `.dead_turnover_months`) -
      no model column for either.
    * migration 352 (`scm.product_lifecycle_decision`) - the table has no ORM model at
      all; it is read/written through raw SQL only.
    * `product_suppliers.min_order_quantity` / `.is_primary` - these predate the
      migration chain entirely (part of the original hand-built schema, before 273
      introduced `moq` / `is_primary_supplier` as the modelled successors) and no
      migration ever creates them either, so there is no revision to replay. They are
      still read by `app.services.scm.reorder_level_service.supplier_constraints` via
      `COALESCE(ps.moq, ps.min_order_quantity)` / `ORDER BY ps.is_primary`, so a
      bootstrapped database needs the columns even though nothing ever writes them.
    * migration 306 (`ALTER COLUMN company_id SET DEFAULT <Sorento id>` on every owned
      table) - the model intentionally stays `nullable=True` at the ORM level (see
      `CompanyScopedMixin`'s docstring), so `create_all` never adds this DEFAULT. Without
      it, a raw `INSERT` that omits `company_id` (scripts, seeds, and plenty of test
      fixtures) lands NULL instead of Sorento, and the session-scoped company predicate
      (`app.services.company_scope_sql.company_sql_predicate`) then filters the row out
      of every read - silent, empty results rather than an error, which is worse to
      diagnose than a constraint violation.

    Each step is idempotent (skipped once its marker column/table already exists), so
    reruns against an already-bootstrapped database are safe.
    """
    import importlib.util
    from pathlib import Path

    from sqlalchemy import text

    import alembic.op as op_module
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.database import engine

    markers = {
        "351_scm_product_health_thresholds": (
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'scm' AND table_name = 'reorder_policy' "
            "AND column_name = 'margin_floor_pct'"
        ),
        "306_company_id_default_sorento": (
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'products' "
            "AND column_name = 'company_id' AND column_default IS NOT NULL"
        ),
        "352_scm_product_lifecycle_decision": (
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'scm' AND table_name = 'product_lifecycle_decision'"
        ),
    }
    versions_dir = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    for name, marker_sql in markers.items():
        with engine.connect() as conn:
            present = conn.execute(text(marker_sql)).scalar()
        if present:
            log.info("%s: already applied, skipping", name)
            continue
        spec = importlib.util.spec_from_file_location(name, versions_dir / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with engine.begin() as conn:
            op_module._proxy = Operations(MigrationContext.configure(conn))
            module.upgrade()
        log.info("%s: applied", name)

    with engine.begin() as conn:
        conn.execute(text(
            'ALTER TABLE product_suppliers ADD COLUMN IF NOT EXISTS '
            '"min_order_quantity" INTEGER'
        ))
        conn.execute(text(
            'ALTER TABLE product_suppliers ADD COLUMN IF NOT EXISTS '
            '"is_primary" BOOLEAN'
        ))
    log.info("product_suppliers: legacy min_order_quantity/is_primary columns present")

    # scm.order_link_claim's per-company uniqueness (migration 335) is a functional/
    # expression index (`coalesce(company_id, ...)`, `coalesce(item_code, '')`) with no
    # `Index(...)` counterpart in the model's `__table_args__`, so `create_all` never lays
    # it down. Without it two different companies claiming the SAME (so_number, po_number)
    # pairing silently succeed as one company (no isolation) instead of two independent
    # rows, AND the same company can claim the same pairing twice (no duplicate guard).
    # `CREATE UNIQUE INDEX IF NOT EXISTS` is naturally idempotent - no marker needed.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_scm_order_link_claim_identity "
            "ON scm.order_link_claim "
            "(coalesce(company_id, '00000000-0000-0000-0000-000000000000'::uuid), "
            "so_number, po_number, coalesce(item_code, ''))"
        ))
    log.info("scm.order_link_claim: per-company uniqueness index present")


def seed_scm_module_data() -> None:
    """Replay migration 311's DATA seeds, which create_all cannot produce.

    `create_all` builds tables from the ORM and knows nothing about rows, so reference data
    seeded inside a migration body does not exist on a freshly built database. Two of
    311's seeds are load-bearing rather than cosmetic:

    * `import_field_alias` - with no aliases the import channel resolves no column headers,
      so every uploaded file reports every column as unmapped.
    * `scm.priority_policy` - with no active policy nothing can be ranked for fulfilment.

    Calls the migration's own seeding functions rather than restating the data, so the two
    paths cannot drift. Both are idempotent.
    """
    import importlib.util
    from pathlib import Path

    from app.database import engine

    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    spec = importlib.util.spec_from_file_location(
        "_scm_seed_311", versions / "311_scm_purchasing_base.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 338 adds the AutoCount detail-listing wording, including the `qty_delivered` /
    # `qty_remaining` columns that stop `Qty` being read as the outstanding figure. A later
    # migration adding aliases has to be replayed here too, or a create_all database resolves
    # the old wording only and the client's real export is rejected on it.
    spec_338 = importlib.util.spec_from_file_location(
        "_scm_seed_338", versions / "338_scm_autocount_so_detail_aliases.py"
    )
    module_338 = importlib.util.module_from_spec(spec_338)
    spec_338.loader.exec_module(module_338)

    # 347 adds the `reorder_level` upload doc-type's header aliases (item_code, location,
    # reorder_level, reorder_qty). Its `scm.reorder_level.reorder_qty` column is already an
    # ORM column (create_all produces it), but the alias seed is migration-body-only, same
    # gap as 311/338 - without it every reorder-level upload reports every column
    # unmapped. `_ALIASES` is (field, alias) with `doc_type='reorder_level'` fixed, per the
    # migration's own INSERT; replayed verbatim (`ON CONFLICT ... DO NOTHING`, idempotent).
    from sqlalchemy import text as _text

    spec_347 = importlib.util.spec_from_file_location(
        "_scm_seed_347", versions / "347_scm_reorder_level_upload.py"
    )
    module_347 = importlib.util.module_from_spec(spec_347)
    spec_347.loader.exec_module(module_347)

    # 357 adds the last seven columns of the captain's real outstanding-SO export, including
    # `Agent` - which the demand classification reads. Same create_all gap as 311/338/347:
    # without the replay a bootstrapped database resolves neither the agent (so every order
    # it could classify is reported unclassifiable instead) nor the six ignored columns (so
    # every upload reports them as unrecognised).
    spec_357 = importlib.util.spec_from_file_location(
        "_scm_seed_357", versions / "357_scm_so_agent_aliases.py"
    )
    module_357 = importlib.util.module_from_spec(spec_357)
    spec_357.loader.exec_module(module_357)

    # 358 adds the `po_spo_history` doc type: the 27 headers of the captain's PO + SPO
    # export. Same create_all gap again, and this one decides whether the file is READ at
    # all - the purchase-history channel tells the two shapes apart by resolving `Doc No`,
    # `Item Code` and `Qty` out of one header row, so with no aliases a structured export
    # falls through to the banded reader and is rejected as "not a Purchase Order Listing".
    spec_358 = importlib.util.spec_from_file_location(
        "_scm_seed_358", versions / "358_scm_po_spo_history_aliases.py"
    )
    module_358 = importlib.util.module_from_spec(spec_358)
    spec_358.loader.exec_module(module_358)

    # TWO revisions are numbered 375, from lanes that were open at the same time, and they
    # seed different things. They are named after what they seed rather than after the
    # number, because `module_375` would silently mean whichever block was written last.
    #
    # The proforma one adds the `proforma_invoice` doc type: both real invoice shapes
    # (Jinbaichuan's 19-column pre-loading list and Kailu's seven-column proforma). Same
    # create_all gap as 311/338/347/357/358, and this one decides whether a proforma is READ
    # at all - the header is recognised by resolving item code, quantity and unit price on
    # one row, so with no aliases every upload is refused as "the file does not name the
    # columns".
    spec_proforma = importlib.util.spec_from_file_location(
        "_scm_seed_375_proforma", versions / "375_scm_proforma_invoice.py"
    )
    module_proforma = importlib.util.module_from_spec(spec_proforma)
    spec_proforma.loader.exec_module(module_proforma)

    # The Kailu one adds the bare packing-list spellings the captain's Kailu list uses
    # (`型号`, `货名`, `体积`, `牌子/LOGO` plus nine columns resolved only so they stop
    # reporting as unrecognised). Same create_all gap again, and like 358 it decides whether
    # the file is read at all - without them the reader finds no item_code column and the
    # preview rejects the file as having no item_code or qty.
    spec_kailu = importlib.util.spec_from_file_location(
        "_scm_seed_375_kailu", versions / "375_kailu_packing_list_aliases.py"
    )
    module_kailu = importlib.util.module_from_spec(spec_kailu)
    spec_kailu.loader.exec_module(module_kailu)

    # 415 adds the rest of the purchase book's money line (`DISCOUNT` / `TOTAL (INC)` on the
    # `outstanding_po` doc type). Same create_all gap as 311/338/347/357/358: migration 414's
    # `discount` / `line_total` columns ARE ORM columns, so create_all produces them, but
    # nothing resolves a header to either without the alias row - so on a bootstrapped
    # database the columns exist and every upload leaves them empty for ever.
    spec_415 = importlib.util.spec_from_file_location(
        "_scm_seed_415", versions / "415_po_money_aliases.py"
    )
    module_415 = importlib.util.module_from_spec(spec_415)
    spec_415.loader.exec_module(module_415)

    # 428 adds the volume spellings to the `proforma_invoice` doc type (`体积(cbm)` /
    # `总体积(cbm)`). Same create_all gap as every one above: migration 428's `cbm_per_unit`
    # / `cbm_total` columns ARE ORM columns, so create_all produces them, but no header
    # resolves to either without the alias row - the container fill would read "unmeasured"
    # on a document that states the volume on every line.
    spec_428 = importlib.util.spec_from_file_location(
        "_scm_seed_428", versions / "428_scm_pi_cbm_adjust_revision.py"
    )
    module_428 = importlib.util.module_from_spec(spec_428)
    spec_428.loader.exec_module(module_428)
    # 435 / 436 add the weight (`净重(kg)` / `毛重(kg)` / N.W. / G.W.) and measurement
    # (材质 / 装箱数 / 外箱尺寸, L / W / H) spellings to the `proforma_invoice` doc type. Same
    # gap again: the columns are ORM columns, the header aliases are rows, and without them
    # a CI database reads every weight and carton size as absent.
    spec_435 = importlib.util.spec_from_file_location(
        "_scm_seed_435", versions / "435_proforma_line_weights.py"
    )
    module_435 = importlib.util.module_from_spec(spec_435)
    spec_435.loader.exec_module(module_435)
    spec_436 = importlib.util.spec_from_file_location(
        "_scm_seed_436", versions / "436_packing_list_workbook_fields.py"
    )
    module_436 = importlib.util.module_from_spec(spec_436)
    spec_436.loader.exec_module(module_436)
    # 459 adds `FromSODocList` to the `outstanding_po` doc type. Same create_all gap as
    # every alias block above, and this one decides whether a re-uploaded purchase book can
    # seed any DEDICATION at all: G12's project-bin lock refuses an unattributed line to the
    # automatic pass, and this column is where the attribution comes from.
    spec_459 = importlib.util.spec_from_file_location(
        "_scm_seed_459", versions / "459_outstanding_po_from_so_alias.py"
    )
    module_459 = importlib.util.module_from_spec(spec_459)
    spec_459.loader.exec_module(module_459)

    # 440 seeds the `inbound_shipment_draft` numbering rule (`PL-{YYMM}-{NNN}`). Nothing in
    # the ORM produces a `document_numbering_rules` ROW, and without it every packing list
    # created by a convert - or by `/new` without a number - is refused for having no series
    # to draw from.
    spec_440 = importlib.util.spec_from_file_location(
        "_scm_seed_440", versions / "440_seed_inbound_shipment_draft_numbering.py"
    )
    module_440 = importlib.util.module_from_spec(spec_440)
    spec_440.loader.exec_module(module_440)

    # 483 adds the Jiexia spellings (`箱号` / `封签号` / `客户` / `INVOICE NO.` / bare `日期` /
    # `客户型号` / `洁厦型号` / `JIEXIA MODEL` / `单价` / `总金额` / `CARTONS` / `CBM`, weights)
    # to BOTH `packing_list` and `proforma_invoice`. Same create_all gap as every alias
    # migration above: `seal_no` and `consignee` are new columns nothing derives a header
    # for without this row, and the container/seal split (`箱号:X / 封签号:Y`) is what makes
    # the Jiexia packing list read as two containers instead of one.
    spec_483 = importlib.util.spec_from_file_location(
        "_scm_seed_483", versions / "483_supplier_doc_aliases.py"
    )
    module_483 = importlib.util.module_from_spec(spec_483)
    spec_483.loader.exec_module(module_483)

    # 506 adds the Jinbaichuan spellings (`Customer Name 客户名` / `客户名` / `Customer Name`
    # for `consignee`, `封条号` for `seal_no`) to BOTH `packing_list` and `proforma_invoice`.
    # Same create_all gap as every alias migration above: without this row a combined
    # Jinbaichuan-style header ("封条号：OOLLGZ7182") resolves no label at all, so the
    # container value it follows never gets split off (test_pi_header_cells_and_convert.py).
    spec_506 = importlib.util.spec_from_file_location(
        "_scm_seed_506", versions / "506_scm_pi_consignee_ref.py"
    )
    module_506 = importlib.util.module_from_spec(spec_506)
    spec_506.loader.exec_module(module_506)

    # `ifa_supplier_word_col` adds the `supplier_inventory_word` doc type: the D7 word list
    # (`SORENTO` -> `SRT`, `连体马桶` -> `WC`, ...) a bare stock-list model number composes
    # through. Same create_all gap as every alias migration above - without the replay a
    # bootstrapped database resolves no words at all, so every bare 型号 falls back to the
    # raw-join key and waits in the Supplier codes picker instead of binding on the first
    # upload.
    spec_ifa_word = importlib.util.spec_from_file_location(
        "_scm_seed_ifa_word", versions / "ifa_supplier_word_col.py"
    )
    module_ifa_word = importlib.util.module_from_spec(spec_ifa_word)
    spec_ifa_word.loader.exec_module(module_ifa_word)

    # `ifa_bare_container_seal` adds the bare `柜号` (container_no) / `封条` (seal_no) shared
    # aliases DAFUYUAN and NEW YANGGANG's combined-file headers need to split past their first
    # pair. Same create_all gap as every alias migration above; its own unique partial index
    # (`WHERE supplier_id IS NULL`) makes the replay idempotent.
    spec_ifa_bare = importlib.util.spec_from_file_location(
        "_scm_seed_ifa_bare", versions / "ifa_bare_container_seal.py"
    )
    module_ifa_bare = importlib.util.module_from_spec(spec_ifa_bare)
    spec_ifa_bare.loader.exec_module(module_ifa_bare)

    with engine.begin() as conn:
        aliases = module.seed_import_field_aliases(conn)
        policies = module.seed_priority_policy(conn)
        aliases += module_338.seed(conn)
        aliases += module_357.seed(conn)
        aliases += module_358.seed(conn)
        aliases += module_proforma.seed(conn)
        aliases += module_kailu.seed(conn)
        aliases += module_415.seed(conn)
        aliases += module_428.seed(conn)
        aliases += module_435.seed(conn)
        aliases += module_436.seed(conn)
        aliases += module_459.seed(conn)
        aliases += module_483.seed(conn)
        module_506.seed(conn)
        aliases += module_ifa_word.seed_supplier_word_rows(conn)
        module_ifa_bare.seed(conn)
        module_440.seed_inbound_shipment_draft_rule(conn)
        for field, alias in module_347._ALIASES:
            conn.execute(_text(
                "INSERT INTO import_field_alias (doc_type, field, alias, locale) "
                "VALUES ('reorder_level', :f, :a, NULL) "
                "ON CONFLICT (doc_type, field, alias) WHERE supplier_id IS NULL DO NOTHING"
            ), {"f": field, "a": alias})
            aliases += 1
    log.info("scm module data seeded -> aliases=%d priority_policy=%d", aliases, policies)


def seed_fulfilment_planning_flags() -> None:
    """Replay migration 443's warehouse seed, which create_all cannot produce.

    Same create_all gap as 311/338/347: `create_all` builds the COLUMN from the ORM and
    knows nothing about rows, so the one-off "turn the flag on for the active
    `-BB/-IB/-IR/-NTC/-AM` bins" UPDATE does not happen on a freshly built database. Without
    it every warehouse reads `fulfilment_planning = false`, which is "nothing is planned
    against anything" - the ladder proposes Buy for every line and the board's verdict is
    `Outside fulfilment planning` on all of them.

    Calls the migration's own function rather than restating the predicate, so the two paths
    cannot drift.

    Runs ONLY on the FIRST bootstrap of a database, which is the same guard the migration
    states with its `add_column` branch: the seed is a one-off STARTING POSITION and the
    flag is configuration an admin edits on the Warehouses screen from then on. Bootstrap
    re-runs on every deploy, so replaying it unconditionally would turn back on every bin
    somebody had deliberately turned off.

    "First bootstrap" is read off `alembic_version`, which `stamp_head()` writes at the end
    of this same run: absent or empty means this database has never been bootstrapped, and
    that is the only moment the column can have been created by the `create_all` above.
    """
    import importlib.util
    from pathlib import Path

    from sqlalchemy import text

    from app.database import engine

    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    spec = importlib.util.spec_from_file_location(
        "_seed_443", versions / "443_fulfilment_planning_flag_tba_date.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with engine.begin() as conn:
        # Two statements, not one CASE: Postgres plans a subquery even on the branch it
        # will not take, so a single expression referencing `alembic_version` fails
        # outright on the fresh database this guard exists to recognise.
        has_version_table = conn.execute(
            text("SELECT to_regclass('alembic_version') IS NOT NULL")
        ).scalar()
        stamped = (
            conn.execute(text("SELECT count(*) FROM alembic_version")).scalar()
            if has_version_table
            else 0
        )
        if stamped:
            log.info("fulfilment planning flags left as configured -> seed skipped")
            return
        flagged = module.seed_fulfilment_planning_flags(conn)
    log.info("fulfilment planning flags seeded -> warehouses=%d", flagged)


def seed_customer_import_aliases() -> None:
    """Replay migration 353's `customer` header aliases (same create_all gap as 311/338/347).

    The customer importer resolves every column through `import_field_alias`, so on a
    bootstrapped database with no `customer` rows the first upload reports every column
    unmapped and reads as a broken importer rather than as missing seed data. The
    migration's own `seed()` is called so the two paths cannot drift; it is idempotent.
    """
    import importlib.util
    from pathlib import Path

    from app.database import engine

    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    spec = importlib.util.spec_from_file_location(
        "_customer_alias_seed_353", versions / "353_customer_import_aliases.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with engine.begin() as conn:
        inserted = module.seed(conn)
    log.info("customer import aliases seeded -> %d", inserted)


def seed_products_list_query_fields() -> None:
    """The `products` list-query filter catalog, minimally replayed for bootstrap.

    `list_query_resources` / `list_query_fields` are ENTIRELY migration-seeded data
    (101, 128, 503, prod_discontinued_at_flt, ...) with no seed function of their own
    anywhere in this script, so a bootstrapped database carries both tables completely
    empty - not just missing `exclude_from_planning`/`discontinued_at`, missing the
    `products` resource row itself. Only what `test_product_exclude_from_planning.py`
    and `test_product_discontinued_at_list.py` pin is replayed here: a bare `products`
    resource (101's own shape), the `exclude_from_planning` field (503) and the
    `discontinued_at` field (prod_discontinued_at_flt), each via its own `seed()` so
    the two paths can never drift. The ~40 other master fields 128 seeds are NOT
    replayed - nothing in the suite currently reads them off a bootstrapped database, and
    the full catalog belongs in its own seed pass the day something does.
    """
    import importlib.util
    import uuid
    from pathlib import Path

    from sqlalchemy import text

    from app.database import engine

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM list_query_resources WHERE resource_key = 'products'")
        ).fetchone()
        if not row:
            conn.execute(
                text(
                    "INSERT INTO list_query_resources "
                    "(id, resource_key, display_name, description, is_active) "
                    "VALUES (CAST(:id AS uuid), 'products', 'Products', "
                    "'Product master list', true)"
                ),
                {"id": str(uuid.uuid4())},
            )

        versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
        spec = importlib.util.spec_from_file_location(
            "_products_flt_seed_503", versions / "503_product_exclude_planning_flt.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        inserted = module.seed(conn)

        spec_disc = importlib.util.spec_from_file_location(
            "_products_flt_seed_discontinued_at", versions / "prod_discontinued_at_flt.py"
        )
        module_disc = importlib.util.module_from_spec(spec_disc)
        spec_disc.loader.exec_module(module_disc)
        inserted_disc = module_disc.seed(conn)
    log.info(
        "products list-query field catalog seeded -> exclude_from_planning: %s, discontinued_at: %s",
        "added" if inserted else "already present",
        "added" if inserted_disc else "already present",
    )


def seed_chatbot_policy() -> None:
    """Replay the chatbot turn re-architecture's migration-body seeds (AC-1501,
    AC-1502, AC-1550, AC-1594).

    `chatbot_rearch_s0` seeds `chatbot_domains` / `chatbot_entity_kinds`,
    `chatbot_rearch_s4` publishes the first `chatbot_semantic_parser` version with the
    policy blocks rendered from those two tables, and `chatbot_rearch_s6d` /
    `chatbot_rearch_s6e` / `chatbot_rearch_s7` / `chatbot_rearch_s8` / `chatbot_rearch_
    s11` / `chatbot_rearch_s12` update six domains' narrowing and `chatbot_rearch_s9`
    appends `crm_sales_report` (and `chatbot_top_selling_tool` appends
    `crm_top_selling_report`) to the order domain's tools. `chatbot_rearch_s12` then
    republishes the parser version over its own narrowing changes and moves the
    `production` label onto it (owner ruling 21 Sep 2026: the deploy ships the config,
    nothing is promoted by hand). All nine are migration-BODY work: `create_all` gives a
    bootstrapped database the TABLES and COLUMNS (every one is a plain model default)
    but none of the rows these migrations INSERT/UPDATE, so a fresh CI database has
    empty policy tables and no published parser version at all.

    Each migration's own function is imported and called directly, in the same order
    the real migration chain applies them (s0 -> s4 -> s6d -> s6e -> s7 -> s8 -> s9 ->
    s11 -> s12 - s4's publish reads whatever `chatbot_domains` holds at the moment it
    runs, same as a real `alembic upgrade head` replay would, and s12's republish is
    therefore the one that ends up labelled), so the two paths can never
    drift and bootstrap produces the identical row set a genuinely migrated database
    has. Every step is idempotent (see each migration's own docstring); safe to call
    twice.
    """
    import importlib.util
    import sys
    from pathlib import Path

    from app.database import engine

    alembic_dir = Path(__file__).resolve().parent.parent / "alembic"
    versions = alembic_dir / "versions"
    # `chatbot_rearch_s0.seed_domains_and_kinds` does `from _chatbot_policy_seed import
    # ...` by bare module name - `_chatbot_policy_seed.py` lives in `alembic/`, not
    # `alembic/versions/`. Real `alembic upgrade` resolves it because `ScriptDirectory`
    # puts its own `script_location` (`alembic/`) on `sys.path` (measured:
    # `ScriptDirectory.from_config(cfg).get_revision(...)` leaves
    # `.../alembic` at `sys.path[0]`); loading the migration module directly (below)
    # does not, so it is added here too.
    if str(alembic_dir) not in sys.path:
        sys.path.insert(0, str(alembic_dir))

    def _load(name: str, filename: str):
        spec = importlib.util.spec_from_file_location(name, versions / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    s0 = _load("_chatbot_rearch_s0", "chatbot_rearch_s0.py")
    s4 = _load("_chatbot_rearch_s4", "chatbot_rearch_s4.py")
    s6d = _load("_chatbot_rearch_s6d", "chatbot_rearch_s6d.py")
    s6e = _load("_chatbot_rearch_s6e", "chatbot_rearch_s6e.py")
    s7 = _load("_chatbot_rearch_s7", "chatbot_rearch_s7.py")
    s8 = _load("_chatbot_rearch_s8", "chatbot_rearch_s8.py")
    s9 = _load("_chatbot_rearch_s9", "chatbot_rearch_s9.py")
    s11 = _load("_chatbot_rearch_s11", "chatbot_rearch_s11.py")
    s12 = _load("_chatbot_rearch_s12", "chatbot_rearch_s12.py")
    # Fix round 8 on PR #833: the `specification` entity kind, BEFORE the republish so
    # the published blocks carry its entity-kind line.
    spk = _load("_spk_0001_specification_kind", "spk_0001_specification_kind.py")
    top_selling = _load("_chatbot_top_selling_tool", "chatbot_top_selling_tool.py")
    sales_s1 = _load("_sales_s1_reports_module", "sales_s1_reports_module.py")
    pdyn1 = _load("_pdyn_0001_status_words_sales", "pdyn_0001_status_words_sales.py")
    pdyn2 = _load("_pdyn_0002_wording_layer", "pdyn_0002_wording_layer.py")
    pdyn3 = _load("_pdyn_0003_prod_identical", "pdyn_0003_prod_identical.py")
    pdyn4 = _load("_pdyn_0004_prompt_lists", "pdyn_0004_prompt_lists.py")
    pdyn5 = _load("_pdyn_0005_access_level_order", "pdyn_0005_access_level_order.py")
    pdyn6 = _load("_pdyn_0006_agents_in_prompt", "pdyn_0006_agents_in_prompt.py")

    with engine.begin() as conn:
        domains_inserted, kinds_inserted = s0.seed_domains_and_kinds(conn)
    with engine.begin() as conn:
        kinds_inserted += int(spk.insert_kind(conn))
    with engine.begin() as conn:
        s4.publish_policy_blocks(conn)
    with engine.begin() as conn:
        s6d.apply_narrowing(conn)
    with engine.begin() as conn:
        s6e.apply_narrowing(conn)
    with engine.begin() as conn:
        s7.apply_narrowing(conn)
    with engine.begin() as conn:
        s8.apply_narrowing(conn)
    with engine.begin() as conn:
        s9.apply_tools(conn)
    with engine.begin() as conn:
        top_selling.apply_tools(conn)
        sales_s1.apply_tools(conn)
    with engine.begin() as conn:
        s11.apply_narrowing(conn)
    with engine.begin() as conn:
        s12.apply_narrowing(conn)
    # After every narrowing step: the `sales` row copies the `order` row's narrowing.
    with engine.begin() as conn:
        pdyn1.apply(conn)
    # The owner's prompt-list tags and his curated domain words (answers 2 and 4).
    with engine.begin() as conn:
        pdyn4.apply(conn)
        pdyn5.apply(conn)
        pdyn6.apply(conn)
    # LAST, and after every narrowing step: it renders the blocks from the tables as
    # they now stand and leaves `production` on that version.
    with engine.begin() as conn:
        s12.republish_and_promote(conn)
    # After the republish: the wording layer is built from the version it promoted, and
    # published unlabelled (the owner promotes).
    with engine.begin() as conn:
        pdyn2.apply(conn)
    # The owner's production text of 1 Oct 2026, unlabelled, variables only where the
    # tables reproduce it exactly.
    with engine.begin() as conn:
        pdyn3.apply(conn)
    log.info(
        "chatbot policy seeded -> domains=%d kinds=%d (narrowing + first prompt "
        "version applied)",
        domains_inserted,
        kinds_inserted,
    )


def _seed_default_company() -> None:
    """Idempotently insert the fixed Sorento company row (mirrors migration 302).

    Needed because bootstrap builds the schema from the models and only *stamps*
    alembic at head - migration 302's data seed is never executed. Without this
    row the ``*_company_id_fkey`` constraints reject every owned insert once the
    scope layer auto-stamps the incumbent company.
    """
    from sqlalchemy import text

    from app.database import engine

    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        conn.execute(
            text(
                "INSERT INTO companies (id, name, code, is_active) "
                "VALUES ('00000000-0000-0000-0000-000000000001', 'Sorento', 'SRT', true) "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
        log.info("seeded default company (Sorento)")
    except Exception as exc:  # noqa: BLE001
        log.warning("default-company seed failed (continuing): %s", exc)
    finally:
        conn.close()


def seed_reference_data() -> None:
    """Run the app's own startup seeders.

    These are the same functions ``app.main`` invokes on boot, called directly so
    a database is fully seeded without having to start a server. Each is
    idempotent and independently guarded: one failing must not abort the rest.
    """
    from app.database import SessionLocal

    # Multi-company: the schema comes from create_all + `alembic stamp head`, so
    # migration 302's Sorento seed never runs here. Every owned table has a NOT
    # NULL company_id FK -> companies, and the test suite / seeders auto-stamp the
    # incumbent Sorento company, so that row MUST exist first or every owned insert
    # violates the FK. Seed it idempotently (fixed id, mirrors migration 302).
    _seed_default_company()

    steps = [
        # Roles + order statuses first: later seeders and RBAC grants depend on them.
        ("reference data", "app.services.reference_seed", "run"),
        ("rbac permissions", "app.rbac.permission_registry", "sync_permissions"),
        # Re-run so freshly synced permissions reach the admin roles.
        ("admin permission grants", "app.services.reference_seed", "run"),
        ("email event configs", "app.services.email_event_registry", "seed_event_configs"),
        ("mcp tool catalog", "app.services.mcp_tool_registry_service", "sync_catalog"),
        ("it support bootstrap", "app.services.it_support_bootstrap", "run"),
        ("record action bootstrap", "app.services.record_action_bootstrap", "run"),
    ]
    for label, module_path, func_name in steps:
        db = SessionLocal()
        try:
            module = __import__(module_path, fromlist=[func_name])
            result = getattr(module, func_name)(db)
            db.commit()
            log.info("seeded %s -> %s", label, result)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            log.warning("seeder %r failed (continuing): %s", label, exc)
        finally:
            db.close()


def seed_n8n_sales_reports_grant() -> None:
    """Replay `selfref_0001_n8n_sales_view`'s grant (CHATBOT-SELFREF-SCOPE B1).

    `sales.reports.view` on the `integration_n8n` role is a migration-body seed, and a
    create_all database never runs it, so the chatbot's `crm_sales_analysis` call would
    be 403 on a fresh install exactly as it was in production. Calls the migration's own
    function rather than restating the SQL (the 311 precedent); idempotent, and a database
    without the role yet grants nothing and fails nothing.
    """
    import importlib.util
    from pathlib import Path

    from app.database import engine

    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    spec = importlib.util.spec_from_file_location(
        "_selfref_0001_grant", versions / "selfref_0001_n8n_sales_view.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        module.grant_sales_reports_view(conn)
    log.info("integration_n8n: sales.reports.view granted")


# This project names revisions descriptively ("313_purchase_request_pic") rather
# than with alembic's 12-char hashes, and plenty of them run past alembic's own
# ``version_num VARCHAR(32)``. The long-lived databases were widened at some point
# (production is varchar(255)), so nobody noticed - but a FRESH database lets
# alembic create the table at 32 and the stamp then dies:
#
#   StringDataRightTruncation: value too long for type character varying(32)
#   [SQL: INSERT INTO alembic_version (version_num) VALUES ('...')]
#
# Create the table at the real width BEFORE alembic can create it at 32, and widen
# an existing narrow one. Keeps CI, disaster recovery and a genuine incremental
# ``upgrade head`` all consistent with production.
ALEMBIC_VERSION_NUM_WIDTH = 255


def ensure_alembic_version_width() -> None:
    """Make ``alembic_version.version_num`` wide enough for our revision ids."""
    from sqlalchemy import text

    from app.database import engine

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version ("
                f"version_num VARCHAR({ALEMBIC_VERSION_NUM_WIDTH}) NOT NULL "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY)"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE alembic_version ALTER COLUMN version_num "
                f"TYPE VARCHAR({ALEMBIC_VERSION_NUM_WIDTH})"
            )
        )
    log.info("alembic_version.version_num at varchar(%s)", ALEMBIC_VERSION_NUM_WIDTH)


def stamp_head() -> None:
    """Mark the database as being at the latest revision.

    The schema came from the models, which already reflect every migration, so
    the historical revisions must not be replayed.
    """
    from alembic import command
    from alembic.config import Config

    from pathlib import Path

    ensure_alembic_version_width()

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    command.stamp(cfg, "head")
    log.info("alembic stamped at head")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Create schema + views but skip reference-data seeding.",
    )
    args = parser.parse_args()

    url = _require_db_url()
    log.info("bootstrapping %s", url.rsplit("@", 1)[-1])

    create_schema()
    apply_migration_only_ddl()
    create_views()
    if not args.skip_seed:
        seed_reference_data()
        seed_n8n_sales_reports_grant()
        seed_scm_module_data()
        seed_fulfilment_planning_flags()
        seed_customer_import_aliases()
        seed_products_list_query_fields()
        seed_chatbot_policy()
    stamp_head()
    log.info("bootstrap complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
