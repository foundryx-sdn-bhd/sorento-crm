"""Company-scope enforcement tests (multi-company data isolation).

Security core - Group H of UAC-multi-company-isolation.md:
 - AC-H1 leak test: UNSET -> 0 rows; single-company scope -> only that company;
    None -> all companies (across a representative set of owned models).
 - AC-H2 new-table guard: every model with a company_id column is a
    CompanyScopedMixin subclass (except the M2M grant/membership tables).
 - AC-H3 four-state predicate: UNSET/empty -> false(), None -> no predicate,
    frozenset -> IN (...); empty never renders `.in_([])`.
 - AC-H5 attachments: shared (null) rows always visible; owned rows scoped.
 - AC-D4: a system write (scope None/UNSET) to an owned table without a
    company_id is rejected (never inserts a null-company owned row).

Runs against the local Postgres dev DB (a prod-copy) inside a nested transaction
that is ALWAYS rolled back, so no real data is created. All test rows carry a
``zzscope-<suffix>`` / ``ZZSCOPE`` marker and are scoped by the two throwaway test
company ids - no unscoped DELETE (the DB is the developer's real data).
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import false as sa_false
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.database import SessionLocal, Base
from app.models.base import CompanyScopedMixin
from app.models.company import Company
from app.models.dealer_kit import Page as DealerKitPage
from app.models.product import Brand, ProductCategory
from app.models.inventory import Warehouse
from app.models.marketing import CampaignType
from app.models.order import Transporter
from app.models.resources import Attachment, AttachmentDirectory
from app.services.company_scope import (
    UNSET,
    build_company_predicate,
    company_scope,
    register_company_scope_listeners,
)
from app.services.error_handler import AppException

# The scope listeners are normally installed at import time by app.main / the
# worker path; install them explicitly so this file is self-contained.
register_company_scope_listeners()


@pytest.fixture(autouse=True)
def _strict_owned_insert():
    """This module asserts the REAL enforcement (fail-closed rejection + auto-stamp),
    so it opts out of the conftest ``_TEST_LEAVE_NULL_OWNED_INSERT`` legacy escape
    (which lets owned inserts leave company_id null). Restore it afterwards so the
    rest of the suite keeps its pre-isolation behaviour."""
    import app.services.company_scope as _cs

    prev = _cs._TEST_LEAVE_NULL_OWNED_INSERT
    _cs._TEST_LEAVE_NULL_OWNED_INSERT = False
    try:
        yield
    finally:
        _cs._TEST_LEAVE_NULL_OWNED_INSERT = prev

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_LIVE_DB_TESTS") == "1",
    reason="SKIP_LIVE_DB_TESTS=1",
)

# Owned models exercised in the leak test - one per model file, none with
# __audit_track__ (keeps the flush free of audit side effects).
REPRESENTATIVE = [
    (Brand, "brand_code", dict(brand_name="ZZSCOPE brand")),
    (Warehouse, "warehouse_code", dict(warehouse_name="ZZSCOPE wh")),
    (CampaignType, "type_code", dict(type_name="ZZSCOPE ct")),
    (ProductCategory, "category_code", dict(category_name="ZZSCOPE cat")),
    # Dealer Kit lives in its own Postgres schema. It is in this list because the
    # do_orm_execute filter matching across a schema boundary is a distinct thing
    # from matching inside public, and only a real query proves it.
    (DealerKitPage, "slug", dict(name="ZZSCOPE dk page")),
]


def _compile(expr) -> str:
    if expr is None:
        return "<none>"
    return str(expr.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.fixture()
def db() -> Session:
    session = SessionLocal()
    session.begin_nested()  # SAVEPOINT rolled back at teardown
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def data(db: Session):
    """Two throwaway companies (A, B) + one owned row of each representative model
    per company, plus attachments (company A / company B / shared-null)."""
    suffix = uuid.uuid4().hex[:8]
    company_a = Company(id=str(uuid.uuid4()), name=f"ZZSCOPE A {suffix}", code=f"ZA{suffix}")
    company_b = Company(id=str(uuid.uuid4()), name=f"ZZSCOPE B {suffix}", code=f"ZB{suffix}")
    db.add_all([company_a, company_b])
    db.flush()
    a, b = company_a.id, company_b.id

    for model, code_field, extra in REPRESENTATIVE:
        for company_id in (a, b):
            kwargs = dict(extra)
            uniq = f"ZZSCOPE-{code_field}-{company_id[:6]}-{suffix}"
            kwargs[code_field] = uniq
            # Some name columns carry their own UNIQUE constraint (e.g.
            # campaign_types.type_name); make every *_name unique per row.
            for k in list(kwargs):
                if k.endswith("_name"):
                    kwargs[k] = f"{kwargs[k]} {uniq}"
            kwargs["company_id"] = company_id
            if model is Transporter:  # unused here but kept generic
                kwargs["normalized_name"] = uniq.lower()
            db.add(model(**kwargs))
    # AttachmentDirectory (owned) for A and B
    db.add(AttachmentDirectory(name=f"ZZSCOPE dir {suffix}", company_id=a))
    db.add(AttachmentDirectory(name=f"ZZSCOPE dir {suffix}", company_id=b))

    # Attachments: A / B / shared(null).
    fname = f"zzscope-{suffix}"
    for tag, company_id in (("a", a), ("b", b), ("shared", None)):
        att = Attachment(
            original_filename=f"{fname}-{tag}.pdf",
            stored_filename=f"{fname}-{tag}.pdf",
            file_path=f"/zzscope/{fname}-{tag}.pdf",
            company_id=company_id,
        )
        db.add(att)
    db.flush()
    return {"a": a, "b": b, "suffix": suffix, "fname": fname}


# --- AC-H1 leak test -----------------------------------------------------------
@pytest.mark.parametrize("model,code_field", [(m, f) for m, f, _ in REPRESENTATIVE])
def test_unset_scope_returns_zero_rows(db, data, model, code_field):
    ids = [data["a"], data["b"]]
    with company_scope(db, UNSET):
        count = db.query(model).filter(model.company_id.in_(ids)).count()
    assert count == 0, f"{model.__name__} leaked under UNSET (fail-closed expected)"


@pytest.mark.parametrize("model,code_field", [(m, f) for m, f, _ in REPRESENTATIVE])
def test_single_company_scope_returns_only_that_company(db, data, model, code_field):
    ids = [data["a"], data["b"]]
    with company_scope(db, frozenset({data["a"]})):
        rows = db.query(model).filter(model.company_id.in_(ids)).all()
    assert len(rows) == 1
    assert rows[0].company_id == data["a"]


@pytest.mark.parametrize("model,code_field", [(m, f) for m, f, _ in REPRESENTATIVE])
def test_none_scope_returns_all_companies(db, data, model, code_field):
    ids = [data["a"], data["b"]]
    with company_scope(db, None):
        count = db.query(model).filter(model.company_id.in_(ids)).count()
    assert count == 2


def test_empty_scope_returns_zero_rows(db, data):
    ids = [data["a"], data["b"]]
    with company_scope(db, frozenset()):
        count = db.query(Brand).filter(Brand.company_id.in_(ids)).count()
    assert count == 0


# --- M2 statement-cache cross-scope isolation ---------------------------------
def test_back_to_back_single_scopes_do_not_leak_via_statement_cache(db, data):
    """Same session/engine, two single-company scopes of IDENTICAL cardinality
    (``IN (:one)``) executed back-to-back. The scope predicate is injected as a
    CONCRETE clause (not a lambda), so each scope's company id is part of the
    statement-cache key and the second query must NOT reuse the first scope's
    baked value. Guards the with_loader_criteria/statement-cache same-cardinality
    leak that a lambda predicate would silently cause."""
    ids = [data["a"], data["b"]]
    # Run twice interleaved to exercise a warmed cache (A, B, A, B).
    for _ in range(2):
        with company_scope(db, frozenset({data["a"]})):
            rows_a = db.query(Brand).filter(Brand.company_id.in_(ids)).all()
        with company_scope(db, frozenset({data["b"]})):
            rows_b = db.query(Brand).filter(Brand.company_id.in_(ids)).all()
        assert {r.company_id for r in rows_a} == {data["a"]}, "scope B leaked into A's read"
        assert {r.company_id for r in rows_b} == {data["b"]}, "scope A leaked into B's read"


# --- AC-H5 attachments (shared) ------------------------------------------------
def _att_query(db, fname):
    return db.query(Attachment).filter(Attachment.original_filename.like(f"{fname}-%"))


def test_attachments_unset_shows_only_shared(db, data):
    with company_scope(db, UNSET):
        rows = _att_query(db, data["fname"]).all()
    assert {r.company_id for r in rows} == {None}


def test_attachments_single_company_shows_owned_plus_shared(db, data):
    with company_scope(db, frozenset({data["a"]})):
        rows = _att_query(db, data["fname"]).all()
    got = {r.company_id for r in rows}
    assert got == {data["a"], None}
    assert data["b"] not in got


def test_attachments_none_shows_all(db, data):
    with company_scope(db, None):
        rows = _att_query(db, data["fname"]).all()
    assert {r.company_id for r in rows} == {data["a"], data["b"], None}


# --- AC-H3 four-state predicate ------------------------------------------------
def test_predicate_none_adds_nothing():
    assert build_company_predicate(Brand, None) is None


def test_predicate_unset_is_false_for_owned():
    sql = _compile(build_company_predicate(Brand, UNSET)).lower()
    assert "false" in sql
    assert "in ()" not in sql


def test_predicate_empty_frozenset_is_false_not_empty_in():
    sql = _compile(build_company_predicate(Brand, frozenset())).lower()
    assert "false" in sql
    assert "in ()" not in sql  # never the .in_([]) footgun


def test_predicate_frozenset_is_in_clause():
    cid = str(uuid.uuid4())
    sql = _compile(build_company_predicate(Brand, frozenset({cid}))).lower()
    assert "company_id in" in sql
    # UUID literals render dash-stripped by the pg dialect; compare normalized.
    assert cid.replace("-", "") in sql.replace("-", "")


def test_predicate_attachment_shared_null_or_in():
    cid = str(uuid.uuid4())
    sql = _compile(build_company_predicate(Attachment, frozenset({cid}))).lower()
    assert "is null" in sql
    assert "company_id in" in sql


def test_predicate_attachment_unset_is_null_only():
    sql = _compile(build_company_predicate(Attachment, UNSET)).lower()
    assert "is null" in sql
    assert "false" not in sql  # shared rows still visible under fail-closed


# --- AC-H2 new-table guard -----------------------------------------------------
# Tables that legitimately carry a company_id column WITHOUT being owned/partitioned:
# - M2M grant/membership tables (shared bucket).
# - Infra/pipeline tables that snapshot a company_id but are NOT auto-filtered by
#     the ORM listener: import_jobs (enqueue snapshot, worker re-establishes scope);
#     embedding_documents / embedding_chunks (vector search filters company_id
#     manually - the pipeline processes all companies).
# - Admin-listing tables with a MANUAL company filter (admin_listing_company_filter),
#     deliberately NOT owned mixins so their other consumers (portal / public /
#     workflow / worker / global audit listener) keep working under any scope - 
#     only the staff listing is scoped: forms, import_logs, audit_logs.
_COMPANY_ID_ALLOWLIST = {
    "user_companies",
    "respond_contact_companies",
    "import_jobs",
    "embedding_documents",
    "embedding_chunks",
    "forms",
    "import_logs",
    "audit_logs",
    # The audit trail's own failure ledger (best-effort capture, owner ruling 28 Sep 2026):
    # written by the global audit listener for whichever company's write lost its trail,
    # counted company-wide on the system health page, like audit_logs.
    "audit_trail_gaps",
    # Conversation / form SLA trackers carry the company that decides WHICH escalation
    # ladder the tracker climbs, not who may read the row. Scoping it would break the
    # two readers that have no company: the overdue scan (a scheduler tick with no
    # principal) and the cross-company admin SLA listing. Isolation is enforced where
    # it matters instead - the teams the tracker escalates into ARE scoped.
    "conversation_sla_tracking",
    # SLA policies are company-owned data, but deliberately filtered by hand
    # (`sla_service.list_policies` reads the active scope) instead of through the
    # mixin. The auto-filter reaches every SLAPolicy load anywhere in the app,
    # including the ~160 tests and background readers that hold a policy id with no
    # company context at all, and turns each one into an empty result.
    "sla_policies",
    # Numbering rules key their counter by (company_id, doc_type) with NULL meaning the
    # legacy/global rule (migration 327). Deliberately NOT the mixin: the resolver names
    # the company explicitly on every claim, and the auto-filter would hide the NULL
    # fallback row from every scoped session - a fresh company would then mint no numbers
    # at all instead of inheriting the default rule.
    "document_numbering_rules",
    # Container sizes are per-tenant OR global: a row with a NULL company is a default
    # this system ships for everyone, and a tenant row overrides it (hence the unique
    # index on coalesce(company_id, nil)). The mixin's auto-filter would drop every
    # NULL-company row, leaving a loading plan with no container to pack into, so
    # `loading_plan_service.container_sizes` reads them unfiltered on purpose.
    # KNOWN GAP, deliberately left for its own slice rather than changed during a merge:
    # unfiltered also means one tenant can read another's custom size. The fix is
    # `company_id IS NULL OR company_id = <scope>` in that one reader, not the mixin.
    "container_size",
    # The salesperson master is per-company OR shared, and in the captain's own files it is
    # shared: the same agent codes sell for both companies, so partitioning the master would
    # duplicate every agent and split one person's demand class across two rows. A NULL
    # company therefore means "everyone's", and the mixin's auto-filter drops NULL-company
    # rows - which here would hide the entire master and leave every imported order with no
    # agent to classify from. Same shape and same known gap as `container_size`: the day a
    # tenant needs its own agent, the fix is `company_id IS NULL OR company_id = <scope>` in
    # `sales_agent_service`, not the mixin. That fix is now WRITEABLE rather than aspirational:
    # migration 356 replaced the global unique on the code with one on
    # `(coalesce(company_id, nil), sales_agent)`, exactly `container_size`'s index, so a tenant
    # row can coexist with the shared row it overrides.
    "sales_agents",
    # A user's product-discontinued (company, brand) scopes: a preference about
    # which company's catalogue they want to hear about, not company-owned data.
    # The scheduled fan-out runs under the task's own company scope, so an owned
    # table here would be filtered by it and would silently drop recipients from
    # a narrowed run - the whole point of the row is to say "notify me about
    # company X" from a session that is scoped to company Y.
    "user_product_discontinued_scopes",
    # A scoped reference's company_id is per-company; a SHARED master's
    # (sales_agents) is deliberately NULL - the mixin's auto-filter would hide
    # every shared-type row from any scoped session, and its auto-stamp would
    # reject the NULL-company insert `link()` writes for one on purpose
    # (BL-056, autocount-brands-ingest). `IntegrationReferenceService` scopes
    # both reads and writes explicitly instead.
    "integration_references",
}


def test_every_company_id_table_is_registered():
    offenders = []
    owned = []
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        table = mapper.local_table.name
        has_company_id = "company_id" in mapper.local_table.columns
        is_owned = isinstance(cls, type) and issubclass(cls, CompanyScopedMixin)
        if is_owned:
            owned.append(table)
        if has_company_id and not is_owned and table not in _COMPANY_ID_ALLOWLIST:
            offenders.append(table)
    assert not offenders, (
        f"Tables have a company_id column but are not CompanyScopedMixin subclasses "
        f"(add the mixin or allowlist them): {offenders}"
    )
    # The count is a tripwire, not a fact about the schema: it forces a human to look
    # whenever an owned table appears, because an unpartitioned one leaks across
    # companies silently. Update it deliberately, never to "make the test pass".
    #
    # It stays a COUNT, not a list: enumerating the owned tables in a comment is how the
    # number and the prose drifted apart twice already. The rule is what matters. A table
    # is owned when the row is a fact about ONE company that cannot be derived from
    # something already scoped:
    # - Planning artefacts own their company because a run, a recommendation, a budget or
    #     an exception batch is a company's own decision queue, and an exception's warehouse
    #     is nullable (supply in transit names no location to filter through).
    # - Fulfilment rows (loading plans, supplier notices, supplier inventory, allocations)
    #     own it for the same reason: they are that company's shipment, not a place.
    # - The project-sales domain owns its company the same way: a project, a lead, a
    #     quotation, an intake PO/SO and their versions are that company's pipeline.
    # - Certificate children are deliberately NOT owned: they are only reachable through
    #     the certificate, which is scoped, so a second filter is redundant surface (SEC-2a).
    #     Same reasoning for the project children that inherit their partition through their
    #     parent (projects.series_categories, projects.brands, projects.collaborators,
    #     projects.takeover_requests).
    # - `access_agents` is NOT owned: one agent routes both brands through two ladders.
    # Derived instead of owned: demand_stat / item_classification / the views (via the
    # warehouse join), supplier_performance (via suppliers), market signals (facts about
    # the world, not about us).
    # The Dealer Kit adds 9 owned tables under the same rule: a page, its editions and
    # tiles, its assets, collections, bundles, a flyer reading, a selection and the
    # contact -> customer link are each a fact about ONE company's catalogue.
    # `selection_line` is deliberately NOT owned: it hangs off a scoped parent, so
    # scoping it too would filter it twice and add nothing.
    # `promotion_types` is owned but SHARED (`__company_shared__`, like attachments):
    # the five kinds the migration seeds carry no company and have to stay visible
    # under every scope, while a company that adds a type of its own keeps it. Owned
    # without the shared flag would have hidden the seeds from every logged-in user
    # while an API-key caller still saw them.
    # `countries` is owned but SHARED for the same reason: the 249-row ISO seed
    # (`510_countries`) carries no company and has to stay visible to every scoped
    # user, exactly like `promotion_types`'s migration-seeded kinds.
    # `product_spec_flyer_batches` is owned: a proposal batch is ONE company's pass over
    # its own flyer, and it copies the company off the reading it was started from. Its
    # proposal rows are not owned - they hang off the scoped batch, so scoping them too
    # would filter them twice (the `selection_line` rule above).
    # Onboarding adds 3 owned tables under the same rule: a request is ONE company's
    # intake batch (the requester is invited into that company, and the reviewer works
    # a queue of her own company's requests), its people are that batch's named staff,
    # and its templates are that company's own bundles of access. `onboarding_people`
    # is owned rather than derived through its request because every per-person review
    # write loads the person BY ID (`get_person`), so that id-keyed load is worth
    # scoping in its own right, and the mixin also stamps the company at insert.
    # Note what this does NOT replace: a cross-company write is already refused because
    # each caller pairs that load with the scoped `get_request` (`_request_owning`,
    # `_assert_reviewer_writable`), and the mixin cannot catch a SAME-company write
    # aimed at another request's person, which is exactly what those ownership checks
    # are for. Both are load-bearing; removing either is a real hole.
    # Front planning adds 2 owned tables between its stages. `so_supply_decisions` is a
    # fact about ONE company's project pipeline (the revision CS confirmed against a
    # Project SO, loaded by id on every read of the sheet it backs), and
    # `order_summary_location_allocation` is a planning artefact for the same reason
    # `reorder_recommendation` and `recommendation_override` beside it are: it is that
    # company's decision, read by the PO worklist, and the mixin stamps the company at
    # insert so a split written under one scope can never be read under another.
    # The proforma slice adds 2 more under the same rule: an invoice is ONE company's
    # document of record from ONE of its suppliers, and its lines are that document's own
    # charges. The lines are owned rather than derived because the write path deletes and
    # reinserts them BY invoice id, and the mixin also stamps the company at insert.
    #
    # The number is the union of five lineages at this merge: main's 68 (67 plus the flyer
    # proposal batch above), plus the 41 the project-sales branch brought (audited table by
    # table at its own merge), plus `so_supply_decisions`, plus
    # `order_summary_location_allocation`, plus `proforma_invoice` and
    # `proforma_invoice_line`.
    #
    # Every branch that has reached this line so far arrived with a number that was right
    # about its own lineage and wrong about the union, so measure rather than add up. Stage
    # 1C branched before the flyer proposal batch landed and counted 67+41+1 = 109; main
    # counted 68+41 = 109; Stage 2 branched before Stage 1C and counted its OWN copy of
    # `so_supply_decisions` plus `order_summary_location_allocation` as 68+41+2 = 111; the
    # proforma branch counted 68+41+2 = 111 for a different pair. 111 was only coincidentally
    # the merged answer at Stage 2, because `so_supply_decisions` exists once and not twice
    # (Stage 1C owns the model, Stage 2's duplicate declaration is gone - see
    # `app/models/project_so.py`). The value below is measured against the merged model set.
    #
    # PLAN-so-book-diff-replanning.md adds 2: `planning_change_batches` is ONE company's
    # reaction to its own re-uploaded SO book (born from that company's upload, listed on
    # its own worklist), and `planning_change_rows` is that batch's own line-by-line record -
    # owned rather than derived through the batch because every row write and read is BY
    # ROW, keyed straight off `batch_id`/`row_id`, the same reason `plan_exception` and
    # `so_amendments` are owned beside their own parents rather than left to a join.
    #
    # PLAN-scm-proforma-to-spo.md adds 2 more link tables: `proforma_invoice_shipment_link`
    # (migration 405) records where a proforma invoice line's goods actually went - the draft
    # inbound shipment line the "convert" action made for it, or why it made none - and
    # `shipment_line_spo_link` (migration 406) records the next hop, where a shipment line's
    # demand actually went - the SPO line "Create SPO" made for it, or why it made none. Both
    # are owned rather than derived through their parent because the write path is idempotent
    # BY the link row (a second convert / second "Create SPO" is refused by finding an
    # existing row), the same shape as `so_amendments` beside `plan_exception`.
    #
    # PLAN-scm-reorder-decision-to-autocount.md adds 1: `plan_row_decision` (migration 408) is
    # the buyer's CURRENT decision on one recommendation row - buy / use stock / use an
    # existing PO / skip, or a mixture - kept CURRENT rather than append-only. Owned because it
    # is that company's own decision queue, the same reason `recommendation_override` beside
    # it is owned.
    #
    # PLAN-product-sets.md adds 2: `product_sets` is the flyer code that names an assembly
    # (`SRTWC8608-RL` = pedestal + cistern + seat cover). Owned because every product code in
    # the catalogue exists once under Sorento and once under Mocha, so a set naming those
    # products belongs to exactly one of them - the unique index is `(company_id, set_code)`
    # for the same reason. `product_set_members` is deliberately NOT owned: it reaches its
    # scope through its parent set, the way `certificate_products` reaches it through
    # `Certificate`. `product_set_proposal_batches` is owned for the identical reason: a
    # proposal batch is one company's own pass over its own catalogue, deriving candidate
    # sets from product codes that exist once under Sorento and once under Mocha, so the
    # batch belongs to exactly one of them - the same fact `product_sets` is owned for.
    # `product_set_proposals`, the candidate rows inside a batch, are deliberately NOT owned
    # for the same reason `product_set_members` is not: each one is reachable only through
    # its parent batch, which is already scoped, so a second filter would drop it twice.
    #
    # PLAN-scm-cs-planning-uat.md adds 1 and PLAN-scm-fulfilment-feedback.md adds 1.
    # `order_inquiry_links` (migration 421) is one placement of part of an inquiry row's
    # quantity on one document line. Owned rather than derived through the row because the
    # unlink path loads the link BY ID and the netting read joins it straight off `row_id`,
    # the same reason `so_amendments` is owned beside its own parent; the row's own company
    # is what the migration copies onto it. `stock_transfers` (migration 419) is one
    # company's move of its own stock between its own warehouses, loaded by id on every
    # approve and cancel, and `(company_id, transfer_no)` is what makes the number unique -
    # a transfer table scoped through a warehouse would filter on a place instead of on the
    # company whose goods moved.
    # 123 since scm.supplier_product_code_alias (R16, the supplier's own spelling of a
    # product code, remembered per company).
    #
    # PLAN-price-tag-request.md adds 2 more. `price_tag_requests` is a salesperson's
    # request for printed tags naming ONE company's products at ONE company's prices -
    # loaded by id on claim, design and every status transition, and its doc number
    # (`PT-YYYYMM-NNNN`) is unique per company the same way an SO/PO number is.
    # `price_tag_request_lines` stays unscoped: it is reachable only through its
    # request, which already carries the partition (the `selection_line` rule).
    # `dealer_kit.tag_template` is owned for the same reason `dealer_kit.page` is:
    # a reusable tag layout is one company's own catalogue asset, loaded by id when a
    # request line is dropped onto the designer.
    #
    # PLAN-scm-reorder-oi-feedback-1sep.md S4 (PR #489 review round) adds 1: `saved_views`
    # is a saved filter/sort/column view of a listing, and a shared/published one can name
    # another company's suppliers, products or warehouses inside its filter blob - owned
    # the same reason `price_tag_requests` is, loaded by id on publish/set-default/delete.
    #
    # PLAN-scm-fulfilment-feedback-2sep.md S4 adds 1: `so_supply_decision_drafts` is one
    # planner's SAVED-but-not-yet-confirmed decision on one line of one company's sales
    # order, loaded by (company, sales order, line, item code) on every save/undo and
    # stamped by the mixin at insert - owned the same reason `so_supply_decisions` is.
    #
    # PLAN-price-tag-ux-r3.md S4 adds 1: `dealer_kit.tag_size_preset` is owned for the
    # same reason `tag_template` is - a saved, named tag size is one company's own
    # catalogue shortcut, offered under the designer's Tag Size dropdown and editable at
    # `/dealer-kit/tag-sizes`, with a `(company_id, name)` unique constraint so a name is
    # only unique within that company's own set of saved sizes.
    #
    # PLAN-ingest-parity-standardisation.md S2 (D22) adds 1: `order_inquiry_conflicts` is
    # a record of ONE company's ESB push overriding the warehouse its own Order Inquiry
    # sheet had set on its own sales order line. It is read standalone by
    # `GET /scm/order-inquiry/conflicts` (paged list, not reached through the parent
    # sales order), so it is owned outright rather than inheriting the partition through
    # `sales_orders` - a standalone read of a child-only row would otherwise be the one
    # place the parent's filter does not apply.
    #
    # PLAN-scm-supplied-with-companions.md adds 1: `product_companion_rules` names a
    # COMPANION product (CKSW015) and its supplier scope, and every product code in the
    # catalogue exists once under Sorento and once under Mocha - a rule naming those
    # products belongs to exactly one of them, the same fact `product_sets` is owned
    # for, and the create path resolves both the companion and every host id through a
    # company-scoped query. `product_companion_rule_hosts` is deliberately NOT owned: it
    # reaches its scope through its parent rule, the way `product_set_members` reaches
    # it through `product_sets`, so scoping it too would filter it twice.
    # PLAN-scm-supplier-documents-pi-first.md adds 1: `scm.proforma_invoice_packing_line`
    # is one row of the SUPPLIER's own packing list, filed against the invoice that prices
    # it. Owned for the same reason `proforma_invoice_line` beside it is owned, and not
    # left to its parent: `replace_packing_rows` deletes and reinserts the whole set BY
    # invoice id (a re-upload is a correction), every dismiss / undo / match loads the row
    # BY ID off a route that names only the invoice and the row, and `rebind_packing_rows`
    # reaches rows across invoices by supplier and item code - three id-keyed paths that
    # never pass through a scoped parent query. The mixin also stamps the company at
    # insert, which is what keeps a row written under one scope unreadable under another.
    #
    # PLAN-price-tag-r9-review-loop.md adds 1: `price_tag_review_comments` is owned
    # because `create_comments` stamps the request's company onto the row at insert,
    # the CRM `PATCH .../review-comments/{id}` and the Done toggle load a row BY ID off
    # a route that names only the request and the row, and the portal's list must never
    # surface another company's pins even when a line id is guessed.
    # PLAN-oi-header-list-detail.md adds 1: `order_inquiry_raises` is company-owned, not
    # shared (security review 21 Sep 2026).
    # PLAN-oi-request-cs-reserve.md adds 2: `order_inquiry_reserve_requests` is ONE
    # company's ask to its own CS to commit stock, and `order_inquiry_reserve_request_rows`
    # is that ask's own per-row answer. The rows are owned rather than derived through the
    # request because Eling's reserve call and the cancel path both load the row BY ID off
    # a route that names only the request (`reserve()` takes `request_row_id`s), and the
    # mixin stamps the company at insert so a row written under one scope is unreadable
    # under another.
    # PLAN-oi-request-cs-reserve.md section 6c (review round 2) adds 1:
    # `order_inquiry_reserve_events` is one company's own history of a reserve line
    # (F3) - the reserve/unreserve route both load and write it BY the reserve request
    # row's id, never through a scoped parent query, so it is owned outright like its
    # sibling reserve tables above.
    # PLAN-oi-links-autocount-truth-24sep.md (issue #1215) adds 1:
    # `order_inquiry_suggested_links` is one company's own guess the cascade walk made
    # against its own order inquiry row, written and trimmed by
    # `ProjectOrderInquiryService._write_suggested_links` off the scoped row - owned the
    # same reason `order_inquiry_links` beside it is owned.
    # PLAN-sales-targets-opportunities-26sep.md (S6) adds 2: `sales.teams` and
    # `sales.team_members` are one company's own teams and its agents' dated places in
    # them; the team routes load a team BY ID, so the mixin's filter is what hides another
    # company's team (UAC S6-8).
    # PLAN-cost-price-supplier-26sep.md (#1288, Lane A) adds 3: `product_supplier_costs`
    # is one company's own dated cost list for its own product-supplier link,
    # `cost_price_change_sets` is one company's upload of a supplier price list, and
    # `supplier_price_links` is one company's link for a supplier (plan 4.1, 4.3, 4.5).
    # The routes load a set, a line's set and a cost row BY ID, so the mixin's filter is
    # what hides another company's prices; `cost_price_change_lines` reaches its scope
    # through its set and is deliberately not owned.
    # PLAN-sales-targets-opportunities-26sep.md (S1) adds 3: `sales.targets`,
    # `sales.target_periods` and `sales.target_scope` are one company's own targets, their
    # per-period figures and their product scope rows; every target route loads BY ID, so the
    # mixin's filter is what hides another company's target (UAC S1-13).
    # PLAN-sales-targets-opportunities-26sep.md (S2, section 16) adds 2:
    # `sales.opportunities` is one company's own pipeline entry, logged by its agent in the
    # portal or by its staff in the CRM, and `sales.opportunity_lines` are that entry's
    # products. The lines are owned rather than derived through the opportunity because
    # PATCH replaces the set by opportunity id and the mixin stamps the company at insert,
    # the same reason `order_inquiry_reserve_request_rows` is owned beside its request.
    # Both sit in the sales module's PURGE_ORDER and `purge_tables.json`.
    # The S1 fix round 3 (PR #1297) adds 1: `sales.target_commission_tiers` are one target's
    # own tiers, read and replaced only through that scoped target (plan 3.3).
    # PLAN-finance-billing-documents-27sep.md (S0, #1309) adds 2: `finance.billing_documents`
    # and `finance.billing_document_lines` are one company's own AutoCount billing documents,
    # pushed under that company's anchor; the read-back loads a document BY ID.
    # PLAN-chatbot-stock-ask-v2-24sep.md (S5) adds 1: `stock_asks` is one company's own
    # record of the chatbot's stock answers; the PATCH routes load an ask BY ID, so the
    # mixin's filter is what hides another company's ask (AC-SA505).
    # Merge of the lanes: main's 148 + 3 (cost price, #1288) = 151, + 1 (stock_asks) = 152,
    # + 1 (#1354 S2): `branches`, the AutoCount branch table = 153.
    # CUSTOMER-GROUP adds 1: `customer_groups` is one company's own grouping of its ledgers,
    # unique per (company, name) = 154.
    # + 1 (ITEM-TYPE-CRM): `item_types`, AutoCount ItemType reference data = 155.
    expected_owned = 155
    assert len(owned) == expected_owned, (
        f"expected {expected_owned} owned tables, found {len(owned)}: {sorted(owned)}"
    )


# --- AC-D4 system write rejected (UNSET/empty only) ---------------------------
# NOTE: ``None`` is NOT in this list - a None-scope (system / all-companies, the
# n8n no-contact backward-compat path) owned-write now stamps the incumbent
# company (Sorento) rather than rejecting; see the dedicated test below. Only a
# genuinely-ambiguous scope (UNSET = resolver never ran / unresolved contact,
# or an empty frozenset) is rejected.
@pytest.mark.parametrize("scope", [UNSET, frozenset()])
def test_system_write_without_company_is_rejected(scope):
    session = SessionLocal()
    session.begin_nested()
    try:
        with company_scope(session, scope):
            session.add(Brand(brand_code=f"ZZSCOPE-d4-{uuid.uuid4().hex[:8]}", brand_name="d4"))
            with pytest.raises(AppException) as exc:
                session.flush()
        assert exc.value.status_code == 400
    finally:
        session.rollback()


def test_none_scope_owned_write_stamps_incumbent_company():
    """A None-scope (system/all-companies) owned write must NOT reject - it stamps
    the incumbent (Sorento) so n8n no-contact write flows keep working."""
    from app.services.company_scope import DEFAULT_COMPANY_ID

    session = SessionLocal()
    session.begin_nested()
    try:
        with company_scope(session, None):
            b = Brand(brand_code=f"ZZSCOPE-none-{uuid.uuid4().hex[:8]}", brand_name="none")
            session.add(b)
            session.flush()
            assert str(b.company_id) == DEFAULT_COMPANY_ID
    finally:
        session.rollback()
        session.close()


def test_single_company_scope_auto_stamps_on_insert():
    session = SessionLocal()
    session.begin_nested()
    try:
        suffix = uuid.uuid4().hex[:8]
        company = Company(id=str(uuid.uuid4()), name=f"ZZSCOPE stamp {suffix}", code=f"ZS{suffix}")
        session.add(company)
        session.flush()
        with company_scope(session, frozenset({company.id})):
            brand = Brand(brand_code=f"ZZSCOPE-stamp-{suffix}", brand_name="stamp")
            session.add(brand)
            session.flush()
        assert brand.company_id == company.id  # auto-stamped from single-company scope
    finally:
        session.rollback()
        session.close()
