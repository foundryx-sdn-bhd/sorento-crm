"""The projects module's audit entity types are pinned to the PRE-move table names.

``audit_service._audit_entity_type`` derives ``audit_log.entity_type`` from
``__tablename__`` unless the model overrides it. Migration 354 renamed 34 tables, so
without an override two things break at once and neither raises:

* every audit row already written for `project_leads` is orphaned, because new rows arrive
  as `leads` and nothing joins the two;
* the module starts writing `entity_type='brands'`, `'purchase_orders'`,
  `'sales_orders'`, which core `app.models.product` and `app.models.procurement` already
  use, so a "who changed this brand" query returns another module's history.

Both failures are silent - the write succeeds, the report is wrong - which is exactly the
kind of thing that has to be pinned by a test rather than by a convention. Two services
also hardcode the old strings (`project_lead_service.LEAD_AUDIT_ENTITY_TYPE`,
`project_task_service`'s `AuditLog.entity_type == "project_tasks"` filter), and they are
correct only while this holds.

The pre-move names come from migration 354's own table list, so the migration and the
models cannot drift apart on the question of what a table used to be called.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app import models  # noqa: F401  register every model
from app.database import Base
from app.services.audit_service import _audit_entity_type

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "354_projects_schema_move.py"
)


def _pre_move_names() -> dict[str, str]:
    """new bare name -> the name the table carried before migration 354."""
    spec = importlib.util.spec_from_file_location("m354_names", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {new: old for old, new in module.TABLES}


def _mapped_classes():
    return [
        mapper.class_
        for mapper in Base.registry.mappers
        if getattr(mapper.class_, "__table__", None) is not None
    ]


# Born after migration 354, so no pre-move name exists: these models chose their entity
# type at birth and are pinned by their own test below, not by the rename contract.
BORN_AFTER_THE_MOVE = frozenset({
    "so_supply_decisions",
    # PLAN-so-book-diff-replanning.md (migration 29d85dc3ccc3), same reason as above.
    "planning_change_batches",
    "planning_change_rows",
    # PLAN-scm-cs-planning-uat.md (migration 421_order_inquiry_links): one inquiry row's
    # quantity placed across several documents.
    "order_inquiry_links",
    # OI-RESERVE-RECEIVED (migration oirr_0001_released_links): born into `projects`.
    "order_inquiry_released_links",
    # PLAN-set-quote-line-14oct.md, migration sql_0001_set_quote_line (#1682): one product
    # set priced into a series, born straight into `projects` beside `series_products`.
    "series_sets",
    # PLAN-series-filters-15oct.md, migration sf_0001_series_brands (#1697): the brands a
    # series picks, born straight into `projects` beside `series_categories`.
    "series_brands",
    # PLAN-oi-links-autocount-truth-24sep.md (migration oisl_0001_suggested_links, issue
    # #1215): a guess the cascade walk made, never a placement - born straight into
    # `projects` the same way its sibling `order_inquiry_links` was.
    "order_inquiry_suggested_links",
    # PLAN-scm-fulfilment-feedback.md: the planning module's own move-stock artifact.
    "stock_transfers",
    # PLAN-scm-fulfilment-feedback-2sep.md S4, migration 461: born straight into `projects`
    # the same way `so_supply_decisions` was.
    "so_supply_decision_drafts",
    # PLAN-oi-header-list-detail.md S1, migration 523_oi_monthly_no_raises: born straight
    # into `projects` the same way.
    "order_inquiry_raises",
    # PLAN-oi-request-cs-reserve.md S1, migration oirs_0001_reserve_requests: born straight
    # into `projects` the same way.
    "order_inquiry_reserve_requests",
    "order_inquiry_reserve_request_rows",
    # PLAN-oi-request-cs-reserve.md section 6c, migration oirs_0002_reserve_round2: born
    # straight into `projects` the same way.
    "order_inquiry_reserve_events",
    # PLAN-stock-debt-snapshot-15oct.md, migration sds_0001_stock_debt_snapshots (#1705):
    # born straight into `projects` the same way.
    "stock_debt_snapshots",
    "stock_debt_snapshot_rows",
    # SR-CORE (#1718, migration sr_0001_sample_requests): born straight into `projects`.
    "sample_requests",
    "sample_request_lines",
})


def _projects_schema_classes():
    """Every mapped class in the `projects` schema, whenever it was born."""
    return [cls for cls in _mapped_classes() if cls.__table__.schema == "projects"]


def _projects_classes():
    return [
        cls
        for cls in _projects_schema_classes()
        if cls.__tablename__ not in BORN_AFTER_THE_MOVE
    ]


def test_a_model_born_after_the_move_keeps_the_prefix_convention_it_was_born_with():
    """`so_supply_decisions` never wrote a pre-move audit row, so nothing constrains it
    historically; it adopted its siblings' `project_` prefix and this pins that choice.

    `planning_change_batches` / `planning_change_rows` (PLAN-so-book-diff-replanning.md),
    `order_inquiry_links` and `stock_transfers` are the same case: born straight into
    `projects` by their own migration, so they pin the same convention rather than the
    rename contract.
    """
    from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow
    from app.models.project_so import (
        OrderInquiryLink,
        OrderInquiryReserveEvent,
        OrderInquiryReserveRequest,
        OrderInquiryReserveRequestRow,
        OrderInquirySuggestedLink,
        SOSupplyDecision,
        SOSupplyDecisionDraft,
    )
    from app.models.stock_transfer import StockTransfer

    assert _audit_entity_type(SOSupplyDecision) == "project_so_supply_decisions"
    assert _audit_entity_type(PlanningChangeBatch) == "project_planning_change_batches"
    assert _audit_entity_type(PlanningChangeRow) == "project_planning_change_rows"
    assert _audit_entity_type(OrderInquiryLink) == "project_order_inquiry_links"
    assert (
        _audit_entity_type(OrderInquirySuggestedLink)
        == "project_order_inquiry_suggested_links"
    )
    assert _audit_entity_type(StockTransfer) == "project_stock_transfers"
    assert (
        _audit_entity_type(SOSupplyDecisionDraft)
        == "project_so_supply_decision_drafts"
    )
    # PLAN-oi-request-cs-reserve.md S1: the same convention, pinned at birth.
    assert (
        _audit_entity_type(OrderInquiryReserveRequest)
        == "project_order_inquiry_reserve_requests"
    )
    assert (
        _audit_entity_type(OrderInquiryReserveRequestRow)
        == "project_order_inquiry_reserve_request_rows"
    )
    # PLAN-oi-request-cs-reserve.md section 6c: the same convention, pinned at birth.
    assert (
        _audit_entity_type(OrderInquiryReserveEvent)
        == "project_order_inquiry_reserve_events"
    )
    # PLAN-stock-debt-snapshot-15oct.md: the same convention, pinned at birth.
    from app.models.stock_debt_snapshot import StockDebtSnapshot, StockDebtSnapshotRow

    assert _audit_entity_type(StockDebtSnapshot) == "project_stock_debt_snapshots"
    assert _audit_entity_type(StockDebtSnapshotRow) == "project_stock_debt_snapshot_rows"
    # SR-CORE (#1718): the same convention, pinned at birth.
    from app.models.sample_request import SampleRequest

    assert _audit_entity_type(SampleRequest) == "project_sample_requests"


def test_every_renamed_model_pins_its_pre_move_audit_entity_type():
    pre_move = _pre_move_names()
    checked = 0

    for cls in _projects_classes():
        name = cls.__tablename__
        old = pre_move[name]
        if old == name:
            continue  # never carried the prefix; its entity type did not change
        assert _audit_entity_type(cls) == old, (
            f"{cls.__name__} writes audit rows as {_audit_entity_type(cls)!r}; "
            f"they were written as {old!r} before the schema move"
        )
        checked += 1

    assert checked == 34, f"expected 34 renamed models, pinned {checked}"


def test_models_that_kept_their_name_keep_their_audit_entity_type():
    """The other 13 changed schema only, so their entity type must not have moved either."""
    pre_move = _pre_move_names()
    checked = 0

    for cls in _projects_classes():
        name = cls.__tablename__
        if pre_move[name] != name:
            continue
        assert _audit_entity_type(cls) == name
        checked += 1

    assert checked == 13


def test_no_projects_entity_type_collides_with_any_other_model():
    """Seven bare names now exist in both schemas. The audit trail must not conflate them.

    Every non-projects model counts, not just the default-schema ones: `audit_log.entity_type`
    is one flat namespace with no schema in it, so an `scm` or `dealer_kit` model that audits
    as `brands` conflates just as badly as a `public` one would.
    """
    others = {
        _audit_entity_type(cls): cls.__name__
        for cls in _mapped_classes()
        if cls.__table__.schema != "projects"
    }

    for cls in _projects_schema_classes():
        entity_type = _audit_entity_type(cls)
        assert entity_type not in others, (
            f"{cls.__name__} audits as {entity_type!r}, which {others[entity_type]} "
            f"already uses"
        )


def test_the_two_services_that_hardcode_a_pre_move_entity_type_still_agree():
    """Both read `audit_log` by a literal string, so a changed pin silently returns nothing.

    `project_lead_service.LEAD_AUDIT_ENTITY_TYPE` and the `"project_tasks"` filter in
    `project_task_service` are the reason the docstring above says the two services "are
    correct only while this holds" - this is what makes that a test rather than a claim.
    The task side is proved by reading: a row written under the pinned entity type is what
    `task_history` returns, and one written under the bare table name is not.
    """
    import uuid

    from app.models.audit import AuditLog
    from app.models.projects import ProjectLead, ProjectTask
    from app.services.project_lead_service import LEAD_AUDIT_ENTITY_TYPE
    from app.services.project_task_service import task_history
    from tests._pg_fixture import blank_session

    assert LEAD_AUDIT_ENTITY_TYPE == _audit_entity_type(ProjectLead) == "project_leads"
    assert _audit_entity_type(ProjectTask) == "project_tasks"

    with blank_session() as db:
        task_id = str(uuid.uuid4())
        db.add(
            AuditLog(
                entity_type=_audit_entity_type(ProjectTask),
                entity_id=task_id,
                action="CREATE",
                new_values={"name": "ZZT pinned task"},
            )
        )
        db.add(
            AuditLog(
                entity_type=ProjectTask.__tablename__,
                entity_id=task_id,
                action="CREATE",
                new_values={"name": "ZZT bare-name task"},
            )
        )
        db.flush()

        history = task_history(db, task_id)

    assert [(row["action"], row["to_value"]) for row in history] == [
        ("created", "ZZT pinned task")
    ]
