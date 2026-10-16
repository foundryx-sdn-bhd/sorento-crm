"""S14-S16: the price-floor approval gate (PLAN-quotation-approval-and-revision-request).

Written BEFORE the implementation. Every test here is red until the gate exists.

The one that matters most is the FIRST one, and it is worth saying why: the whole feature is
invisible to the ordinary quotation. Price floors already flag a line, but nothing enforced
them, so the change lands on a path every salesperson uses every day. A careless gate that
stamps a status on create, or counts "no floor configured" as a breach, would put an approval
step in front of quotations nobody ever meant to gate - and that failure looks like the feature
working. So it is pinned first and pinned hard: a quotation with no below-floor line writes no
status at all and issues exactly as it did before.

The rest state the gate itself:

- **Below floor and not approved cannot be issued** (DoD 2), refused with a code the screen
  can render rather than a sentence it has to pattern-match.
- **Approved issues, and issuing spends the approval** - the document lands on `issued`, so the
  NEXT revision that dips below the floor has to be approved again rather than riding on a
  decision a manager made about different prices.
- **Reject demands a reason** and writes it where the salesperson will read it.
- **The graph is the authority**: an illegal move is refused server-side, and the two rungs a
  manager owns (approved, rejected) are refused on the generic move route so they cannot be
  reached without the permission and the reason that belong to them.

Postgres only, via ``blank_session``. Every row carries the ``zzt-qapprove`` marker so nothing
here can touch the real data the dev database holds.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.numbering import DocumentNumberingRule
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.projects import PriceFloorRule
from app.models.status import Status
from app.models.user import User
from app.services import project_seed_service
from app.services.error_handler import AppException

from ._pg_fixture import blank_session

MARKER = "zzt-qapprove"

LIST_PRICE = "300.00"
# The client's own example: the lowest a line may go is half of list.
FLOOR_PERCENT = "50.00"
ABOVE_FLOOR = "200.00"  # 300 x 50% = 150, so 200 clears it
BELOW_FLOOR = "120.00"  # under 150, so it needs a manager

APPROVE = "projects.quotations.approve"
EVERYTHING = {
    "projects.projects.view",
    "projects.projects.edit",
    "projects.projects.delete",
    "projects.projects.manage",
    APPROVE,
}
NO_APPROVE = EVERYTHING - {APPROVE}


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return str(db.execute(text("select id from companies where code = 'SRT'")).scalar())


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _numbering_rule(db, company_id: str) -> None:
    rule = (
        db.query(DocumentNumberingRule)
        .filter(DocumentNumberingRule.doc_type == "project_quotation")
        .first()
    )
    if rule is None:
        rule = DocumentNumberingRule(id=_uid(), doc_type="project_quotation")
        if hasattr(DocumentNumberingRule, "company_id"):
            rule.company_id = company_id
        db.add(rule)
    rule.enabled = True
    rule.prefix_template = f"{MARKER}/Q/"
    rule.number_digits = 4
    rule.next_value = 1
    rule.start_value = 1
    rule.reset_policy = "none"
    rule.last_reset_key = None
    db.flush()


def _product(db) -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name="Piece")
    db.add(uom)
    category = ProductCategory(
        id=_uid(),
        category_code=f"ZZT-{_uid()[:8]}",
        category_name=f"{MARKER} Sanitary Ware",
    )
    db.add(category)
    db.flush()
    row = Product(
        id=_uid(),
        product_code=f"ZZT-{_uid()[:8]}",
        product_name=f"{MARKER} WC Suite",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal(LIST_PRICE),
    )
    db.add(row)
    db.flush()
    return row


def _floor(db, company_id: str) -> None:
    """A system-level percent-of-list rule: the client's "50% of the list price"."""
    db.add(
        PriceFloorRule(
            id=_uid(),
            company_id=company_id,
            mode="percent",
            value=Decimal(FLOOR_PERCENT),
        )
    )
    db.flush()


def _setup(db, *, with_floor: bool = True):
    """A project with one quotation document and one scope, priced by the caller."""
    from app.services import project_quotation_document_service as qdocs
    from app.services.project_service import register_project

    company_id = _sorento(db)
    project_seed_service.run(db, company_id=company_id)
    _numbering_rule(db, company_id)
    owner = _user(db, f"{MARKER} Baser")
    product = _product(db)
    if with_floor:
        _floor(db, company_id)
    project = register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=None,
        title=f"{MARKER} Cabana Elmina {_uid()[:12]}",
    )
    document = qdocs.create_document(db, project=project, actor_user_id=owner)
    scope = qdocs.add_scope(
        db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
    )
    return company_id, owner, product, project, document, scope


def _price(db, scope, product, owner, unit_price: str):
    from app.services import project_quotation_service as quotes

    return quotes.upsert_line(
        db,
        version=quotes.current_version(db, scope.id),
        actor_user_id=owner,
        payload={"product_id": product.id, "unit_price": unit_price, "quantity": 2},
    )


def _sign(db, document, owner) -> None:
    from app.services import project_quotation_document_service as qdocs

    qdocs.sign_as_sorento(
        db,
        document=document,
        actor_user_id=owner,
        payload={
            "signer_name": f"{MARKER} Baser",
            "mode": "draw",
            "image_data_uri": "data:image/png;base64,zzt",
        },
    )


def _status(db, key: str) -> Status:
    row = (
        db.query(Status)
        .filter(
            Status.entity_type == "quotation",
            Status.scope_id.is_(None),
            Status.key == key,
        )
        .first()
    )
    assert row is not None, f"the quotation graph has no '{key}' rung"
    return row


# --------------------------------------------------------------------- DoD item 1


def test_a_quotation_with_no_below_floor_line_never_touches_the_approval_graph():
    """THE regression this whole slice is most likely to cause.

    Price floors are configured per company and per product, and the ordinary quotation clears
    them. If the gate stamped a status on create, or treated "no rule reaches this product" as
    a breach, every salesperson would suddenly need a manager to send a perfectly normal
    quotation - and it would read as the feature working rather than as a bug. So: no status
    row, no approval needed, and the Issue call goes through exactly as it did before the gate
    existed.
    """
    from app.services import project_quotation_approval_service as approval
    from app.services import project_quotation_document_service as qdocs

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        line = _price(db, scope, product, owner, ABOVE_FLOOR)

        # The floor was resolved and applied; the line simply clears it.
        assert line.floor_value_applied == Decimal("150.00")
        assert line.is_below_floor is False

        assert approval.below_floor_line_count(db, document) == 0
        assert approval.requires_approval(db, document) is False
        assert document.approval_status_id is None

        _sign(db, document, owner)
        record = qdocs.issue(db, document=document, actor_user_id=owner)
        assert record.issue_no == 1

        # Still nothing on the graph. Issuing a quotation nobody had to approve must not
        # quietly enrol it in an approval lifecycle it will then be stuck in.
        db.refresh(document)
        assert document.approval_status_id is None
        assert approval.current_key(db, document) is None


def test_a_company_with_no_floor_rules_at_all_is_never_gated():
    """The commonest install on day one. `resolve_floor` answers None when no rule reaches the
    line, which is a real answer and not a failure - inventing a floor of zero would flag
    nothing while looking like it had checked, and inventing one of infinity would gate
    everything."""
    from app.services import project_quotation_approval_service as approval
    from app.services import project_quotation_document_service as qdocs

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db, with_floor=False)
        line = _price(db, scope, product, owner, "1.00")

        assert line.floor_value_applied is None
        assert line.is_below_floor is False
        assert approval.requires_approval(db, document) is False

        _sign(db, document, owner)
        assert qdocs.issue(db, document=document, actor_user_id=owner).issue_no == 1


# --------------------------------------------------------------------- DoD item 2


def test_a_below_floor_quotation_is_refused_at_issue_with_a_code_the_screen_can_render():
    """The gate is at ISSUE, not at Sign: the internal signature is readiness, not dispatch, so
    a person may sign a quotation at any price and still be stopped from sending it. The refusal
    carries a code because the screen has to name the reason AND offer the way to ask for
    approval; a client pattern-matching on English would break the first time the wording moved.
    """
    from app.services import project_quotation_approval_service as approval
    from app.services import project_quotation_document_service as qdocs

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        line = _price(db, scope, product, owner, BELOW_FLOOR)
        assert line.is_below_floor is True

        assert approval.below_floor_line_count(db, document) == 1
        assert approval.requires_approval(db, document) is True

        # Signing is still allowed at any price.
        _sign(db, document, owner)

        with pytest.raises(AppException) as refused:
            qdocs.issue(db, document=document, actor_user_id=owner)
        assert refused.value.status_code == 422
        assert refused.value.detail["code"] == "quotation_below_floor_pending_approval"

        # Nothing was written: a refused issue must not leave half a revision behind.
        assert qdocs.current_issue(db, document) is None


def test_the_salesperson_asks_for_approval_a_manager_approves_and_the_issue_then_proceeds():
    """The whole journey in one test, because the value is in the sequence rather than in any
    one step. A quotation that has never needed a manager has no status; asking for one is what
    puts it on the graph, at `pending_approval`, from the initial rung it was implicitly sitting
    at. Approval is what lets the issue through."""
    from app.services import project_quotation_approval_service as approval
    from app.services import project_quotation_document_service as qdocs

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)
        _sign(db, document, owner)

        manager = _user(db, f"{MARKER} Manager")

        approval.move(
            db,
            document=document,
            to_status_id=_status(db, "pending_approval").id,
            actor_user_id=owner,
        )
        assert approval.current_key(db, document) == "pending_approval"

        # Still refused while it waits.
        with pytest.raises(AppException) as waiting:
            qdocs.issue(db, document=document, actor_user_id=owner)
        assert waiting.value.detail["code"] == "quotation_below_floor_pending_approval"

        approval.approve(
            db, document=document, actor_user_id=manager, permissions=EVERYTHING
        )
        assert approval.current_key(db, document) == "approved"

        record = qdocs.issue(db, document=document, actor_user_id=owner)
        assert record.issue_no == 1


def test_issuing_spends_the_approval_so_the_next_below_floor_revision_needs_a_new_one():
    """A manager approved THOSE prices. Leaving the document on `approved` for good would let
    the next revision drop another line under the floor and go out on a decision nobody made
    about it, which is the gate quietly disabling itself after one use."""
    from app.services import project_quotation_approval_service as approval
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)
        _sign(db, document, owner)
        manager = _user(db, f"{MARKER} Manager")

        approval.move(
            db,
            document=document,
            to_status_id=_status(db, "pending_approval").id,
            actor_user_id=owner,
        )
        approval.approve(
            db, document=document, actor_user_id=manager, permissions=EVERYTHING
        )
        qdocs.issue(db, document=document, actor_user_id=owner)

        assert approval.current_key(db, document) == "issued"

        # A revision, priced under the floor again.
        version = quotes.revise(db, quotation=scope, actor_user_id=owner)
        quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "100.00", "quantity": 2},
        )
        _sign(db, document, owner)

        with pytest.raises(AppException) as refused:
            qdocs.issue(db, document=document, actor_user_id=owner)
        assert refused.value.detail["code"] == "quotation_below_floor_pending_approval"

        # And the way back on is the same one: ask again from where it stands.
        approval.move(
            db,
            document=document,
            to_status_id=_status(db, "pending_approval").id,
            actor_user_id=owner,
        )
        approval.approve(
            db, document=document, actor_user_id=manager, permissions=EVERYTHING
        )
        assert qdocs.issue(db, document=document, actor_user_id=owner).issue_no == 2


# --------------------------------------------------------------------- S16: reject


def test_rejecting_demands_a_reason_and_keeps_it_where_the_salesperson_will_read_it():
    """"Rejected" with no reason leaves the salesperson guessing which line to move, which is
    the conversation this gate exists to make explicit. The reason travels with the document
    rather than living only in an activity row, because the block on the quotation screen is
    where it has to be read."""
    from app.services import project_quotation_approval_service as approval

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)
        manager = _user(db, f"{MARKER} Manager")
        approval.move(
            db,
            document=document,
            to_status_id=_status(db, "pending_approval").id,
            actor_user_id=owner,
        )

        for empty in ("", "   ", None):
            with pytest.raises(AppException) as blank:
                approval.reject(
                    db,
                    document=document,
                    actor_user_id=manager,
                    reason=empty,
                    permissions=EVERYTHING,
                )
            assert blank.value.status_code == 422
            assert blank.value.detail["code"] == "quotation_reject_reason_required"

        # The refusal changed nothing.
        assert approval.current_key(db, document) == "pending_approval"

        approval.reject(
            db,
            document=document,
            actor_user_id=manager,
            reason="  The WC suite has to come back to at least RM 240.  ",
            permissions=EVERYTHING,
        )
        assert approval.current_key(db, document) == "rejected"
        assert (
            document.approval_rejected_reason
            == "The WC suite has to come back to at least RM 240."
        )


def _rejected(db, *, reason: str = "Too low."):
    """A below-floor quotation that a manager has sent back. Returns the parts a test needs."""
    from app.services import project_quotation_approval_service as approval

    _, owner, product, project, document, scope = _setup(db)
    _price(db, scope, product, owner, BELOW_FLOOR)
    manager = _user(db, f"{MARKER} Manager")
    approval.move(
        db,
        document=document,
        to_status_id=_status(db, "pending_approval").id,
        actor_user_id=owner,
    )
    approval.reject(
        db,
        document=document,
        actor_user_id=manager,
        reason=reason,
        permissions=EVERYTHING,
    )
    return owner, manager, product, project, document, scope


def test_c2_a_rejected_quotation_has_no_back_to_draft_step():
    """C2. Rejected -> Draft used to be the salesperson's own move; PR/SF have no such step
    (the creator edits and presses Submit again), so the move is now refused for anyone and
    the rung and the manager's reason are left exactly as they were."""
    from app.services import project_quotation_approval_service as approval

    with blank_session() as db:
        owner, manager, _, _, document, _ = _rejected(db)

        for actor in (owner, manager):
            with pytest.raises(AppException) as refused:
                approval.move(
                    db,
                    document=document,
                    to_status_id=_status(db, "draft").id,
                    actor_user_id=actor,
                )
            assert refused.value.status_code == 422
            assert refused.value.detail["code"] == "quotation_status_not_self_serve"

        assert approval.current_key(db, document) == "rejected"
        assert document.approval_rejected_reason == "Too low."


def test_c1_a_rejected_quotation_resubmits_straight_to_pending_in_one_press():
    """C1. The salesperson re-prices and presses Send for approval from Rejected. The service
    walks the two existing edges (rejected -> draft -> pending) itself, clears the reason, and
    writes exactly ONE quotation_approval_requested row for the press."""
    from app.models.activities import ActivityEvent
    from app.services import project_quotation_approval_service as approval

    with blank_session() as db:
        owner, _, _, project, document, _ = _rejected(db)

        def _requested() -> int:
            return (
                db.query(ActivityEvent)
                .filter(
                    ActivityEvent.entity_type == "project",
                    ActivityEvent.entity_id == str(project.id),
                    ActivityEvent.system_template == "quotation_approval_requested",
                )
                .count()
            )

        assert _requested() == 1  # the first press, before the rejection

        approval.move(
            db,
            document=document,
            to_status_id=_status(db, "pending_approval").id,
            actor_user_id=owner,
        )

        assert approval.current_key(db, document) == "pending_approval"
        assert document.approval_rejected_reason is None
        assert _requested() == 2  # exactly one new row for the resubmit


def test_approve_and_reject_are_refused_without_the_grant():
    """The permission is the whole access control on this decision - there is no team-tier
    resolution behind it - so a salesperson holding every project grant except this one must not
    be able to approve their own discount."""
    from app.services import project_quotation_approval_service as approval

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)
        approval.move(
            db,
            document=document,
            to_status_id=_status(db, "pending_approval").id,
            actor_user_id=owner,
        )

        with pytest.raises(AppException) as approving:
            approval.approve(
                db, document=document, actor_user_id=owner, permissions=NO_APPROVE
            )
        assert approving.value.status_code == 403

        with pytest.raises(AppException) as rejecting:
            approval.reject(
                db,
                document=document,
                actor_user_id=owner,
                reason="Nope.",
                permissions=NO_APPROVE,
            )
        assert rejecting.value.status_code == 403

        assert approval.current_key(db, document) == "pending_approval"


# --------------------------------------------------------------- S14: the graph


def test_the_seeded_graph_is_the_exact_set_the_plan_names_and_the_seed_never_argues_twice():
    """The graph is configuration, so it is seeded rather than coded - but a seeder that
    re-asserted itself would bring back a rung an admin deliberately deleted on every restart,
    and nobody would connect the reappearance to a deploy. Same wholesale guard the project
    funnel already uses."""
    from app.models.status import StatusTransition

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)

        rungs = {
            row.key: row
            for row in db.query(Status).filter(
                Status.entity_type == "quotation", Status.scope_id.is_(None)
            )
        }
        assert set(rungs) == {
            "draft",
            "rejected",
            "pending_approval",
            "approved",
            "issued",
        }
        assert rungs["draft"].is_initial is True
        assert [key for key, row in rungs.items() if row.is_initial] == ["draft"]
        # Nothing is terminal: a document at `issued` is revised and issued again, and a
        # rejected one is re-priced and asked again. A terminal rung would strand both.
        assert not any(row.is_terminal for row in rungs.values())

        by_id = {row.id: key for key, row in rungs.items()}
        edges = {
            (by_id[edge.from_status_id], by_id[edge.to_status_id])
            for edge in db.query(StatusTransition).filter(
                StatusTransition.entity_type == "quotation",
                StatusTransition.scope_id.is_(None),
            )
        }
        assert edges == {
            ("draft", "pending_approval"),
            ("pending_approval", "approved"),
            ("pending_approval", "rejected"),
            ("rejected", "draft"),
            ("approved", "issued"),
            ("issued", "pending_approval"),
        }

        # Idempotent: a second boot adds nothing.
        before = db.query(Status).filter(Status.entity_type == "quotation").count()
        project_seed_service.run(db, company_id=company_id)
        assert db.query(Status).filter(Status.entity_type == "quotation").count() == before


def test_an_illegal_move_is_refused_by_the_engine_and_the_managers_rungs_are_not_self_serve():
    """The status engine is the authority on which move is legal, not the client. And the two
    rungs a manager owns are refused on the generic move route even though the edges exist:
    reaching `approved` without the permission, or `rejected` without a reason, through a route
    that asks for neither would make both rules decorative."""
    from app.services import project_quotation_approval_service as approval

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)

        # draft -> approved is not an edge at all.
        with pytest.raises(AppException) as illegal:
            approval.move(
                db,
                document=document,
                to_status_id=_status(db, "approved").id,
                actor_user_id=owner,
            )
        assert illegal.value.status_code == 422

        approval.move(
            db,
            document=document,
            to_status_id=_status(db, "pending_approval").id,
            actor_user_id=owner,
        )
        # pending_approval -> approved IS an edge, and it is still refused here: approving is
        # its own act with its own permission.
        for key in ("approved", "rejected"):
            with pytest.raises(AppException) as owned:
                approval.move(
                    db,
                    document=document,
                    to_status_id=_status(db, key).id,
                    actor_user_id=owner,
                )
            assert owned.value.status_code == 422
            assert owned.value.detail["code"] == "quotation_status_not_self_serve"


def test_the_approval_position_reaches_the_frontend_through_the_document_serializer():
    """`serialize_document` is a MANUAL dict, so a new column that is not added to it never
    reaches the screen however correctly it is stored - this repo has been bitten by exactly
    that on `get_user` and on `system_settings`. The block on the quotation page reads every
    one of these fields, so all of them are pinned here."""
    from app.services import project_quotation_approval_service as approval
    from app.services import project_quotation_document_service as qdocs

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)

        payload = qdocs.serialize_document(db, document)
        assert payload["requires_approval"] is True
        assert payload["below_floor_line_count"] == 1
        assert payload["approval_status_id"] is None
        assert payload["approval_status_key"] is None
        assert payload["approval_status_label"] is None
        assert payload["approval_rejected_reason"] is None

        manager = _user(db, f"{MARKER} Manager")
        approval.move(
            db,
            document=document,
            to_status_id=_status(db, "pending_approval").id,
            actor_user_id=owner,
        )
        approval.reject(
            db,
            document=document,
            actor_user_id=manager,
            reason="Too low.",
            permissions=EVERYTHING,
        )

        payload = qdocs.serialize_document(db, document)
        assert payload["approval_status_key"] == "rejected"
        assert payload["approval_status_label"] == _status(db, "rejected").label
        assert payload["approval_status_id"] == _status(db, "rejected").id
        assert payload["approval_rejected_reason"] == "Too low."


def test_every_approval_decision_is_written_to_the_project_activity_feed():
    """A discount below the floor is exactly the decision somebody asks about six months later.
    Who asked, who decided, and why it was sent back all have to be recoverable from the
    project's own history rather than from a manager's memory."""
    from app.models.activities import ActivityEvent
    from app.services import project_quotation_approval_service as approval

    with blank_session() as db:
        _, owner, product, project, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)
        manager = _user(db, f"{MARKER} Manager")

        approval.move(
            db,
            document=document,
            to_status_id=_status(db, "pending_approval").id,
            actor_user_id=owner,
        )
        approval.reject(
            db,
            document=document,
            actor_user_id=manager,
            reason="Too low.",
            permissions=EVERYTHING,
        )
        # C1: resubmit straight from Rejected, no back-to-draft step.
        approval.move(
            db,
            document=document,
            to_status_id=_status(db, "pending_approval").id,
            actor_user_id=owner,
        )
        approval.approve(
            db, document=document, actor_user_id=manager, permissions=EVERYTHING
        )

        rows = (
            db.query(ActivityEvent)
            .filter(
                ActivityEvent.entity_type == "project",
                ActivityEvent.entity_id == str(project.id),
            )
            .all()
        )
        templates = [row.system_template for row in rows]
        assert templates.count("quotation_approval_requested") == 2
        assert templates.count("quotation_approval_rejected") == 1
        assert templates.count("quotation_approval_granted") == 1

        rejected = next(
            row for row in rows if row.system_template == "quotation_approval_rejected"
        )
        assert rejected.actor_id == manager
        assert "Too low." in (rejected.body_text or "")
