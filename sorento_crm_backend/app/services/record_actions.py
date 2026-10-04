"""The record actions a reader can take back while the window is open (D7, S6).

Deleting a product, deleting or re-statusing a delivery order, trashing a user: each
one used to open a confirmation dialog, and now parks itself on the server for a few
seconds instead. `/api/v1/pending-actions` is the only route that reaches these, and
it looks the action up here.

Two rules, both load-bearing:

* **`execute` calls the EXISTING service method, unchanged.** The same code the
  immediate route called still does the work; the pending action changed WHEN it runs,
  never what it does. Inlining logic here would let a deferred delete drift from the
  one the API still exposes, and the drift would be invisible until the two disagreed
  in production.
* **`permission` names the slug the route enforces before parking anything.** The check
  happens at the CLICK, not at the commit, because a refusal ten seconds later has no
  button left to report itself on.

`entity_type` is the frontend's word for the record (`product`, `order`, `user`), and
the payload carries `entity_id` plus whatever the method needs beyond it - the route
puts both there, so `execute` never has to reach for the action row.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.services.form_action_grace import WINDOW_DESTRUCTIVE, WINDOW_REVERSIBLE
from app.services.form_action_registry import FormAction, register

logger = logging.getLogger(__name__)

#: A record action authorised by OWNERSHIP rather than by a role grant.
#:
#: `permission` is what tells `/pending-actions` this is a record action at all, and for
#: every other one it is the slug the immediate route enforces. A notification has no
#: such slug: the bell is in the topbar for every signed-in user, the route checks
#: nothing, and the handler is scoped to the requester, so the only row it can reach is
#: the reader's own. Inventing a permission for it would mean a grant sweep across every
#: role for a rule the query already enforces.
OWN_RECORD = "@own"


def _entity_id(payload: dict) -> str:
    return str(payload["entity_id"])


# --------------------------------------------------------------------------------------
# Handlers - one line each, straight onto the service method the route already calls.
# --------------------------------------------------------------------------------------


def _delete_product(db: Session, payload: dict):
    from app.services.product_service import ProductService

    return ProductService(db).delete_product(_entity_id(payload))


def _delete_order(db: Session, payload: dict):
    from app.services.order_service import OrderService

    return OrderService(db).delete_order(_entity_id(payload))


def _delete_product_companion_rule(db: Session, payload: dict):
    from app.services.product_companion_service import ProductCompanionService

    return ProductCompanionService(db).delete(_entity_id(payload))


def _delete_product_combo(db: Session, payload: dict):
    from app.services.product_combo_service import ProductComboService

    return ProductComboService(db).delete(_entity_id(payload))


def _delete_product_combo_part(db: Session, payload: dict):
    from app.services.product_combo_service import ProductComboService

    return ProductComboService(db).delete_part(_entity_id(payload))


def _delete_price_tag_request_tag(db: Session, payload: dict):
    from app.models.price_tag import PriceTagRequestTag
    from app.services.price_tag_request_service import PriceTagRequestService

    tag = (
        db.query(PriceTagRequestTag)
        .filter(PriceTagRequestTag.id == _entity_id(payload))
        .first()
    )
    if tag is None:
        return None
    return PriceTagRequestService.delete_tag(db, tag)


def _set_order_status(db: Session, payload: dict):
    from app.schemas.order import OrderUpdate
    from app.services.order_service import OrderService

    return OrderService(db).update_order(
        _entity_id(payload),
        OrderUpdate(order_status_id=str(payload["order_status_id"])),
        payload.get("requested_by_id"),
    )


def _delete_chatbot_domain(db: Session, payload: dict):
    """The same two lines `DELETE /system/chatbot/domains/{id}` runs.

    There is no `ChatbotDomainService` to call: the route builds the query itself, so
    there is nothing to delegate to and inventing a service for one delete would be more
    machinery than the rule it is meant to protect. Resolved by id only - the route also
    accepts a NAME, for a caller holding one, and a parked action always carries the id.
    """
    from app.api.v1.system.chatbot_config import refuse_if_last_domain
    from app.models.chatbot_policy import ChatbotDomain

    row = (
        db.query(ChatbotDomain).filter(ChatbotDomain.id == _entity_id(payload)).first()
    )
    if row is None:
        return None
    # The same rule the route enforces, imported rather than repeated: the loader falls
    # back to the frozen seed when it reads no domains, so an emptied table gives the
    # operator fourteen domains back on the next turn and a screen showing none.
    refuse_if_last_domain(db, row)
    db.delete(row)
    return None


def _delete_user(db: Session, payload: dict):
    from app.services.user_service import UserService

    # The trash the Users list restores from, which is what DELETE /users/{id} has
    # always done. The deferred path does not get to redefine the verb.
    return UserService(db).delete_user(_entity_id(payload))


def _unlink_user_contact(db: Session, payload: dict):
    from app.audit_context import AuditActor, actor_scope
    from app.services.user_service import UserService

    # The exact unlink branch of `PUT /users/{id}` (S3 1.3/1.4) - one
    # implementation, called from both places.
    #
    # This executor runs from whichever request or background sweep happens to
    # commit the deferred window, not from the click that started it - without
    # this, the audit row would name that sweep's actor (or nobody) instead of
    # the owner who actually asked for the unlink. `requested_by_id` is stamped
    # onto the payload at park time (`app/api/v1/system/pending_actions.py`).
    requested_by_id = payload.get("requested_by_id")
    if requested_by_id:
        with actor_scope(
            AuditActor(
                actor_type="user",
                user_id=str(requested_by_id),
                real_user_id=str(requested_by_id),
            ),
            db=db,
        ):
            return UserService(db).unlink_contact(_entity_id(payload))
    return UserService(db).unlink_contact(_entity_id(payload))


# --------------------------------------------------------------------------------------
# Registrations. `<entity>.<verb>`, the same keys the frontend's action sets name.
# --------------------------------------------------------------------------------------

def _delete_sales_team(db: Session, payload: dict):
    from app.services.sales import team_service

    return team_service.delete_team_by_id(db, _entity_id(payload))


register(
    FormAction(
        key="sales_team.delete",
        entity_types=("sales_team",),
        execute=_delete_sales_team,
        window=WINDOW_DESTRUCTIVE,
        permission="sales.teams.delete",
        label="Delete sales team",
    )
)


def _delete_sales_opportunity(db: Session, payload: dict):
    from app.services.sales import opportunity_service

    return opportunity_service.delete_opportunity_by_id(db, _entity_id(payload))


def _delete_sales_target(db: Session, payload: dict):
    from app.services.sales import target_service

    return target_service.delete_target_by_id(db, _entity_id(payload))


register(
    FormAction(
        key="sales_opportunity.delete",
        entity_types=("sales_opportunity",),
        execute=_delete_sales_opportunity,
        window=WINDOW_DESTRUCTIVE,
        permission="sales.opportunities.delete",
        label="Delete sales opportunity",
    )
)

register(
    FormAction(
        key="sales_target.delete",
        entity_types=("sales_target",),
        execute=_delete_sales_target,
        window=WINDOW_DESTRUCTIVE,
        permission="sales.targets.delete",
        label="Delete target",
    )
)

register(
    FormAction(
        key="product.delete",
        entity_types=("product",),
        execute=_delete_product,
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.products.delete",
        label="Delete product",
    )
)

register(
    FormAction(
        key="chatbot_domain.delete",
        entity_types=("chatbot_domain",),
        execute=_delete_chatbot_domain,
        window=WINDOW_DESTRUCTIVE,
        permission="system.chatbot_config.manage",
        label="Delete chatbot domain",
    )
)

register(
    FormAction(
        key="order.delete",
        entity_types=("order",),
        execute=_delete_order,
        window=WINDOW_DESTRUCTIVE,
        permission="order_management.orders.delete",
        label="Delete delivery order",
    )
)

register(
    FormAction(
        key="order.set_status",
        entity_types=("order",),
        execute=_set_order_status,
        window=WINDOW_REVERSIBLE,
        permission="order_management.orders.edit",
        label="Change status",
    )
)

register(
    FormAction(
        key="product_companion_rule.delete",
        entity_types=("product_companion_rule",),
        execute=_delete_product_companion_rule,
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.products.edit",
        label="Delete rule",
    )
)

# The two halves of AC-S1-5. A combo and a part are both deleted from the product
# page's own Combos section, so both take the products edit slug and the destructive
# window - there is nothing to un-delete once it lapses.
register(
    FormAction(
        key="product_combo.delete",
        entity_types=("product_combo",),
        execute=_delete_product_combo,
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.products.edit",
        label="Delete combo",
    )
)

register(
    FormAction(
        key="product_combo_part.delete",
        entity_types=("product_combo_part",),
        execute=_delete_product_combo_part,
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.products.edit",
        label="Remove part",
    )
)

# AC-S3-6: removing a tag is a destructive action like any other, so it takes
# the grace window rather than a dialog. The service refuses the line's LAST tag
# with a 422 at commit time, which is the same answer the button already
# prevents by disabling itself.
register(
    FormAction(
        key="price_tag_request_tag.delete",
        entity_types=("price_tag_request_tag",),
        execute=_delete_price_tag_request_tag,
        window=WINDOW_DESTRUCTIVE,
        permission="dealer_kit.price_tag_requests.process",
        label="Remove tag",
    )
)

register(
    FormAction(
        key="user.delete",
        entity_types=("user",),
        execute=_delete_user,
        window=WINDOW_DESTRUCTIVE,
        permission="user_management.users.delete",
        label="Trash user",
    )
)

register(
    FormAction(
        key="user.unlink_contact",
        entity_types=("user",),
        execute=_unlink_user_contact,
        window=WINDOW_REVERSIBLE,
        permission="user_management.users.edit",
        label="Unlink WhatsApp contact",
    )
)


# --------------------------------------------------------------------------------------
# S6b - the sweep of what still confirmed in a dialog.
#
# Same two rules as above: `execute` is one line onto the service method the immediate
# route already calls, and `permission` is the slug that route (or the screen the action
# lives on) enforces. A key ending `.delete` takes the destructive window; a detach that
# can be made again by re-adding the row takes the reversible one.
# --------------------------------------------------------------------------------------


def _unlink_product_supplier(db: Session, payload: dict):
    from app.services.procurement_service import ProductSupplierService

    return ProductSupplierService(db).delete_product_supplier(_entity_id(payload))


register(
    FormAction(
        key="product_supplier.unlink",
        entity_types=("product_supplier",),
        execute=_unlink_product_supplier,
        # Reversible: the supplier is still there, and re-adding the row restores the
        # link. The terms typed on it are not restored, which is why it is deferred at
        # all rather than applied on the click.
        window=WINDOW_REVERSIBLE,
        # The section is part of a product's edit form, and that is the grant it is
        # rendered behind.
        permission="master_data.products.edit",
        label="Remove supplier",
    )
)


def _unlink_order_inquiry_row(db: Session, payload: dict):
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    return ProjectOrderInquiryService(db).unplace(
        _entity_id(payload), actor_user_id=payload.get("requested_by_id")
    )


register(
    FormAction(
        key="order_inquiry_row.unlink",
        entity_types=("order_inquiry_row",),
        execute=_unlink_order_inquiry_row,
        # Reversible: the document is still there, and re-linking the row restores the
        # placement (AC-DP-06 - "Unlink is a deferred pending action, never a confirm
        # dialog"). `link_id` is left unset so every link on the row goes, matching
        # what "Unlink selected" means on the worklist today.
        window=WINDOW_REVERSIBLE,
        permission="projects.order_inquiry.action",
        label="Unlink",
    )
)


def _cancel_order_inquiry_reserve_request(db: Session, payload: dict):
    from app.services.order_inquiry_reserve_service import OrderInquiryReserveService
    from app.services.user_service import UserPermissionService

    actor_id = payload.get("requested_by_id")
    # SF-1 (review round): the requester or CS may cancel - recomputed HERE, at commit
    # time, rather than trusted off whatever was true the moment the countdown started,
    # since a role change during the window is the actor's CURRENT standing to act on.
    actor_can_reserve = bool(actor_id) and UserPermissionService(db).check_user_has_permission(
        str(actor_id), "projects.order_inquiries.reserve"
    )
    return OrderInquiryReserveService(db).cancel_request(
        request_id=_entity_id(payload),
        actor_user_id=actor_id,
        actor_can_reserve=actor_can_reserve,
    )


register(
    FormAction(
        key="order_inquiry_reserve_request.cancel",
        entity_types=("order_inquiry_reserve_request",),
        execute=_cancel_order_inquiry_reserve_request,
        # Reversible (PLAN-oi-request-cs-reserve.md 3.2): a cancel while nothing has been
        # reserved yet takes nothing back except the ask itself, and the countdown gives
        # a misclick a few seconds to catch itself. No email either way (R9's ONE email
        # is the request itself; a cancel is silent).
        window=WINDOW_REVERSIBLE,
        # SF-4 (review round): the declared slug the generic registry contract checks
        # (`test_record_actions_s6b.py`) - the requester's own grant. A RESERVE-only
        # holder (Eling, who never raises a request) ALSO needs to start this countdown
        # on a request that is not hers; `_ANY_OF_PERMISSIONS` in `pending_actions.py`
        # widens the actual PARK-time gate to either grant, since `FormAction.permission`
        # carries one slug only - the ownership question itself (whose request this is)
        # stays inside `cancel_request` above, never at the park gate.
        permission="projects.order_inquiries.acknowledge",
        label="Cancel request",
    )
)


# `order_inquiry_reserve_row.unreserve` (round 2, F5) retired round 4
# (`PLAN-oi-request-cs-reserve.md` 6e.1, AC-RS-79): the per-row `.../unreserve` route
# it deferred to is gone, superseded by "Amend reserve" through the commit endpoint's
# own `amendments` list, which needs no countdown - the decision is not committed
# until CS clicks `Reserve` on the Lines grid.


def _actor(db: Session, payload: dict) -> dict:
    """The click's actor, in the shape a service expects `current_user` to be.

    The row is read back rather than passed through, because a service may re-check a
    grant against the id AND write the email into provenance ("cleared by ...").
    A dict with only an id would leave that reading "a person" ten seconds after
    somebody with a name pressed the button.
    """
    uid = payload.get("requested_by_id")
    if not uid:
        return {"id": None}
    from app.models.user import User

    row = db.query(User).filter(User.id == str(uid)).first()
    return {
        "id": str(uid),
        "email": getattr(row, "email", None),
        "name": getattr(row, "name", None),
    }


# ----- Resources (attachments/folders) --------------------------------------------------
#
# `Set company…` (PLAN-shared-brand-attachments.md R4/R22). Reversible - the twin links
# it maintains are just as reversible as the company itself, so it takes the short window
# like `order.set_status`. `permission=OWN_RECORD` mirrors the route it defers to
# (`POST .../bulk-company`), which is guarded the same as `PUT /attachments/{id}` - no
# permission slug of its own, just a signed-in caller (R13); `AttachmentCompanyService`
# still checks the target company against the actor's own grants (AC-B6).


def _set_attachment_company(db: Session, payload: dict):
    from app.services.attachment_company_service import AttachmentCompanyService

    return AttachmentCompanyService(db).apply(
        attachment_ids=[_entity_id(payload)],
        company_id=payload.get("company_id"),
        actor_id=payload.get("requested_by_id"),
    )


def _set_directory_company(db: Session, payload: dict):
    from app.services.attachment_company_service import AttachmentCompanyService

    return AttachmentCompanyService(db).apply(
        directory_ids=[_entity_id(payload)],
        company_id=payload.get("company_id"),
        actor_id=payload.get("requested_by_id"),
    )


register(
    FormAction(
        key="attachment.set_company",
        entity_types=("attachment",),
        execute=_set_attachment_company,
        window=WINDOW_REVERSIBLE,
        permission=OWN_RECORD,
        label="Set company",
    )
)

register(
    FormAction(
        key="attachment_directory.set_company",
        entity_types=("attachment_directory",),
        execute=_set_directory_company,
        window=WINDOW_REVERSIBLE,
        permission=OWN_RECORD,
        label="Set company",
    )
)


# ----- Master data ---------------------------------------------------------------------


def _delete_uom(db: Session, payload: dict):
    from app.services.product_service import UnitOfMeasureService

    return UnitOfMeasureService(db).delete_uom(_entity_id(payload))


def _delete_country(db: Session, payload: dict):
    from app.services.country_service import CountryService

    return CountryService(db).delete_country(_entity_id(payload))


def _delete_brand(db: Session, payload: dict):
    from app.services.product_service import BrandService

    return BrandService(db).delete_brand(_entity_id(payload))


def _delete_product_category(db: Session, payload: dict):
    from app.services.product_service import ProductCategoryService

    return ProductCategoryService(db).delete_category(_entity_id(payload))


def _delete_product_set(db: Session, payload: dict):
    from app.services.product_set_service import ProductSetService

    return ProductSetService(db).delete(_entity_id(payload))


def _delete_certificate(db: Session, payload: dict):
    from app.services.certificate_service import CertificateService

    return CertificateService(db).delete_certificate(_entity_id(payload))


def _delete_lookup_option(db: Session, payload: dict):
    from app.services.lookup_option_service import LookupOptionService

    return LookupOptionService(db).delete(_entity_id(payload))


def _unlink_lookup_binding(db: Session, payload: dict):
    from app.services.lookup_binding_service import LookupBindingService

    return LookupBindingService(db).delete(_entity_id(payload))


register(
    FormAction(
        key="uom.delete",
        entity_types=("uom",),
        execute=_delete_uom,
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.units_of_measure.delete",
        label="Delete unit of measure",
    )
)

register(
    FormAction(
        key="country.delete",
        entity_types=("country",),
        execute=_delete_country,
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.countries.delete",
        label="Delete country",
    )
)

register(
    FormAction(
        key="brand.delete",
        entity_types=("brand",),
        execute=_delete_brand,
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.brands.delete",
        label="Delete brand",
    )
)

register(
    FormAction(
        key="product_category.delete",
        entity_types=("product_category",),
        execute=_delete_product_category,
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.product_categories.delete",
        label="Delete category",
    )
)

register(
    FormAction(
        key="product_set.delete",
        entity_types=("product_set",),
        execute=_delete_product_set,
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.product_sets.delete",
        label="Delete product set",
    )
)

register(
    FormAction(
        key="certificate.delete",
        entity_types=("certificate",),
        execute=_delete_certificate,
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.certificates.delete",
        label="Delete certificate",
    )
)

register(
    FormAction(
        key="lookup_option.delete",
        entity_types=("lookup_option",),
        execute=_delete_lookup_option,
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.lookup_sets.edit",
        label="Delete option",
    )
)

register(
    FormAction(
        key="lookup_binding.unlink",
        entity_types=("lookup_binding",),
        execute=_unlink_lookup_binding,
        # Reversible: the column keeps its data and the binding can be made again.
        window=WINDOW_REVERSIBLE,
        permission="master_data.lookup_sets.edit",
        label="Remove binding",
    )
)


# ----- Marketing -----------------------------------------------------------------------


def _delete_campaign(db: Session, payload: dict):
    from app.services.marketing_service import MarketingCampaignService

    return MarketingCampaignService(db).delete_campaign(_entity_id(payload))


def _delete_promotion_type(db: Session, payload: dict):
    from app.services.marketing_service import PromotionTypeService

    return PromotionTypeService(db).delete_promotion_type(_entity_id(payload))


def _delete_promotion_group(db: Session, payload: dict):
    from app.services.marketing_service import PromotionService

    return PromotionService(db).delete_promotion_group(
        str(payload["promotion_id"]), _entity_id(payload)
    )


def _unlink_promotion_product(db: Session, payload: dict):
    from app.services.marketing_service import PromotionProductService

    return PromotionProductService(db).delete_promotion_product(
        str(payload["promotion_id"]), _entity_id(payload)
    )


register(
    FormAction(
        key="campaign.delete",
        entity_types=("campaign",),
        execute=_delete_campaign,
        window=WINDOW_DESTRUCTIVE,
        permission="marketing.campaigns.delete",
        label="Delete campaign",
    )
)

register(
    FormAction(
        key="promotion_type.delete",
        entity_types=("promotion_type",),
        execute=_delete_promotion_type,
        window=WINDOW_DESTRUCTIVE,
        permission="marketing.promotion_types.delete",
        label="Delete promotion type",
    )
)

register(
    FormAction(
        key="promotion_group.delete",
        entity_types=("promotion_group",),
        execute=_delete_promotion_group,
        window=WINDOW_DESTRUCTIVE,
        permission="marketing.promotions.edit",
        label="Delete group",
    )
)

register(
    FormAction(
        key="promotion_product.unlink",
        entity_types=("promotion_product",),
        execute=_unlink_promotion_product,
        window=WINDOW_REVERSIBLE,
        permission="marketing.promotion_products.delete",
        label="Remove product line",
    )
)


# ----- Inventory, integrations, tickets, workflow forms ---------------------------------


def _delete_warehouse(db: Session, payload: dict):
    from app.services.inventory_service import WarehouseService

    return WarehouseService(db).delete_warehouse(_entity_id(payload))


def _delete_spo_document(db: Session, payload: dict):
    from app.services.procurement_service import SPOAllocationService

    # `entity_id` is the SPO NUMBER, not a uuid - `spo_allocations` has no document
    # row of its own (PLAN-spo-investigation-grid.md), so the "document" this deletes
    # is every line sharing that number, and only the caller's own company's lines
    # (see `delete_document`'s docstring for why the company scoping lives there).
    return SPOAllocationService(db).delete_document(_entity_id(payload))


def _delete_integration(db: Session, payload: dict):
    from app.services.integration_admin_service import IntegrationAdminService

    service = IntegrationAdminService(db)
    service.delete(service.get(_entity_id(payload)))
    db.commit()


def _revoke_integration_key(db: Session, payload: dict):
    from app.services.integration_admin_service import IntegrationAdminService

    service = IntegrationAdminService(db)
    service.revoke_key(
        service.get(str(payload["integration_id"])), _entity_id(payload)
    )
    db.commit()


def _delete_ticket(db: Session, payload: dict):
    from app.services.tickets_service import delete_ticket

    return delete_ticket(db, ticket_id=_entity_id(payload), current_user=_actor(db, payload))


def _cancel_ticket_draft(db: Session, payload: dict):
    from app.services.tickets_service import cancel_ticket_draft

    return cancel_ticket_draft(
        db, ticket_id=_entity_id(payload), current_user=_actor(db, payload)
    )


def _delete_workflow_definition(db: Session, payload: dict):
    from app.services.workflow_forms_service import WorkflowFormsService

    return WorkflowFormsService(db).delete_definition(_entity_id(payload))


def _delete_workflow_submission(db: Session, payload: dict):
    from app.services.workflow_forms_service import WorkflowFormsService

    return WorkflowFormsService(db).delete_submission(_entity_id(payload))


register(
    FormAction(
        key="warehouse.delete",
        entity_types=("warehouse",),
        execute=_delete_warehouse,
        window=WINDOW_DESTRUCTIVE,
        permission="inventory.warehouses.delete",
        label="Delete warehouse",
    )
)

register(
    FormAction(
        key="spo_document.delete",
        entity_types=("spo_document",),
        execute=_delete_spo_document,
        window=WINDOW_DESTRUCTIVE,
        permission="procurement.spo_allocations.delete",
        label="Delete SPO document",
    )
)

register(
    FormAction(
        key="integration.delete",
        entity_types=("integration",),
        execute=_delete_integration,
        window=WINDOW_DESTRUCTIVE,
        permission="integration.integrations.delete",
        label="Delete integration",
    )
)

register(
    FormAction(
        key="integration_key.revoke",
        entity_types=("integration_key",),
        execute=_revoke_integration_key,
        # A revoked key can never be un-revoked - the plaintext is gone - so this
        # takes the long window even though its verb is not `delete`.
        window=WINDOW_DESTRUCTIVE,
        permission="integration.integrations.manage_keys",
        label="Revoke API key",
    )
)

register(
    FormAction(
        key="ticket.delete",
        entity_types=("ticket",),
        execute=_delete_ticket,
        window=WINDOW_DESTRUCTIVE,
        permission="tickets.tickets.delete",
        label="Delete ticket",
    )
)

register(
    FormAction(
        key="ticket.cancel_draft",
        entity_types=("ticket",),
        execute=_cancel_ticket_draft,
        # Discarding a draft hard-deletes it, so it takes the long window even
        # though the button on screen says Cancel rather than Delete.
        window=WINDOW_DESTRUCTIVE,
        permission="tickets.tickets.edit",
        label="Discard draft",
    )
)

register(
    FormAction(
        key="workflow_definition.delete",
        entity_types=("workflow_definition",),
        execute=_delete_workflow_definition,
        window=WINDOW_DESTRUCTIVE,
        permission="workflow_forms.definitions.delete",
        label="Delete workflow form",
    )
)

register(
    FormAction(
        key="workflow_submission.delete",
        entity_types=("workflow_submission",),
        execute=_delete_workflow_submission,
        window=WINDOW_DESTRUCTIVE,
        permission="workflow_forms.submissions.delete",
        label="Delete submission",
    )
)


# ----- SLA, system, user management -----------------------------------------------------


def _delete_sla_tracking(db: Session, payload: dict):
    from app.services.sla_service import ConversationSLATrackingService

    return ConversationSLATrackingService(db).delete_tracking(_entity_id(payload))


def _delete_message_snippet(db: Session, payload: dict):
    from app.services.message_snippet_service import MessageSnippetService

    return MessageSnippetService(db).delete_snippet(_entity_id(payload))


def _delete_automation(db: Session, payload: dict):
    from app.services.automation_service import AutomationService

    return AutomationService(db).delete(_entity_id(payload))


def _delete_email_template(db: Session, payload: dict):
    from app.services.email_template_service import EmailTemplateService

    return EmailTemplateService(db).delete(_entity_id(payload))


def _delete_role(db: Session, payload: dict):
    from app.services.user_service import UserRoleService

    return UserRoleService(db).delete_role(_entity_id(payload))


def _delete_team(db: Session, payload: dict):
    from app.services.user_service import TeamService

    return TeamService(db).delete_team(_entity_id(payload))


def _delete_access_agent(db: Session, payload: dict):
    from app.services.user_service import AccessAgentService

    return AccessAgentService(db).delete_agent(_entity_id(payload))


def _clear_product_spec_value(db: Session, payload: dict):
    from app.models.product import Product
    from app.services.error_handler import handle_not_found, handle_validation_error
    from app.services.product_spec_write import apply_spec_values

    # The record is one product's value for one key, so the entity id has to name BOTH
    # (`<product id>:<spec key>`). Parked against the bare spec key it was a globally
    # shared id: two people clearing `width` on two different products collided on the
    # one-pending-action-per-record index, one of them got a 409 for a record nobody
    # else had touched, and each then read the other's outcome as their own.
    product_id, _, spec_key = _entity_id(payload).partition(":")
    if not product_id or not spec_key:
        raise handle_validation_error(
            "A specification value is addressed as '<product id>:<spec key>'."
        )
    # `spec.values` may only be written through this one service (hard-fail rule), so
    # the handler goes through it exactly as the route does.
    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        raise handle_not_found("Product", product_id)
    mode = "absent" if payload.get("mode") == "absent" else "revert"
    return apply_spec_values(
        db,
        product.product_code,
        [{"spec_key": spec_key, "op": mode}],
        actor=_actor(db, payload),
    )


def _remove_stock_visibility_policy(db: Session, payload: dict):
    from app.services.stock_visibility import delete_policy

    # The scope is the entity: a contact override or an access-type policy. The kind
    # travels in the payload because the two are different columns, not different ids.
    scope_kind = str(payload.get("scope_kind") or "")
    if scope_kind == "contact":
        return delete_policy(db, contact_id=_entity_id(payload))
    return delete_policy(db, access_type_code=_entity_id(payload))


def _unlink_contact_customer(db: Session, payload: dict):
    from app.services.contact_customer_service import unlink_by_link_id

    return unlink_by_link_id(db, _entity_id(payload))


def _unassign_customer_sales_agent(db: Session, payload: dict):
    from app.services.order_service import CustomerService

    return CustomerService(db).unassign_sales_agent(
        _entity_id(payload), payload.get("sales_agent_id")
    )


def _delete_customer_group(db: Session, payload: dict):
    from app.services.customer_group_service import CustomerGroupService

    return CustomerGroupService(db).delete_group(_entity_id(payload))


def _remove_customer_from_group(db: Session, payload: dict):
    from app.services.customer_group_service import CustomerGroupService

    return CustomerGroupService(db).remove_customer(
        _entity_id(payload), payload.get("customer_group_id")
    )


def _remove_spec_visibility_policy(db: Session, payload: dict):
    from app.services.error_handler import handle_not_found, handle_validation_error
    from app.services.field_access import resolve_contact_id
    from app.services.spec_visibility import delete_policy

    # The scope is the entity: a contact override or a market-segment policy. The
    # kind travels in the payload because the two are different columns, not
    # different ids - same convention as `_remove_stock_visibility_policy`. The
    # default tier has no DELETE route and is refused here the same way.
    scope_kind = str(payload.get("scope_kind") or "")
    if scope_kind == "contact":
        # N1 (code review): resolved the SAME way the DELETE route resolves it -
        # `entity_id` may be a Respond.io id rather than `respond_contacts.id`,
        # and `delete_policy`'s own lookup is an exact-equality filter on the
        # column, so an unresolved Respond.io id would match no row and return
        # False - a SILENT no-op for anything but the internal id form.
        #
        # `raise_through=True` (SF-3, security re-verify): every handler's
        # contract (`test_every_handler_resolves_its_service_import`) proves
        # its lazy imports are correctly named by breaking the session and
        # asserting the break itself surfaces - `resolve_contact_id`'s default
        # fail-closed swallow would turn that into an ordinary 404 instead,
        # hiding a renamed import exactly as it would hide a real DB failure.
        resolved = resolve_contact_id(
            db, _entity_id(payload), payload.get("space_id"), raise_through=True
        )
        if not resolved:
            raise handle_not_found("Contact", _entity_id(payload))
        return delete_policy(db, contact_id=resolved)
    if scope_kind == "segment":
        return delete_policy(db, segment_code=_entity_id(payload))
    raise handle_validation_error("The default spec visibility policy cannot be removed.")


def _remove_signin_background(db: Session, payload: dict):
    from app.services.signin_background import clear_signin_background

    return clear_signin_background(db)


def _delete_market_segment(db: Session, payload: dict):
    from app.services.market_segment_service import MarketSegmentService

    # Keyed by CODE, not a uuid - that is what the table's primary key is and what
    # `DELETE /market-segments/{code}` takes.
    return MarketSegmentService(db).delete_segment(_entity_id(payload))


def _delete_onboarding_request(db: Session, payload: dict):
    from app.services import onboarding_service

    # No service method to call: `DELETE /onboarding-requests/{id}` is these three
    # lines and no rule of its own (`get_request` already carries the 404 and the
    # company-scope filter), so there is nothing here that could drift from it.
    request = onboarding_service.get_request(db, _entity_id(payload))
    db.delete(request)
    db.commit()


register(
    FormAction(
        key="sla_tracking.delete",
        entity_types=("sla_tracking",),
        execute=_delete_sla_tracking,
        window=WINDOW_DESTRUCTIVE,
        permission="sla_management.conversation_sla_tracking.delete",
        label="Delete tracking record",
    )
)

register(
    FormAction(
        key="message_snippet.delete",
        entity_types=("message_snippet",),
        execute=_delete_message_snippet,
        window=WINDOW_DESTRUCTIVE,
        permission="sla_management.message_snippets.delete",
        label="Delete snippet",
    )
)

register(
    FormAction(
        key="automation.delete",
        entity_types=("automation",),
        execute=_delete_automation,
        window=WINDOW_DESTRUCTIVE,
        permission="automation.automations.delete",
        label="Delete automation",
    )
)

register(
    FormAction(
        key="email_template.delete",
        entity_types=("email_template",),
        execute=_delete_email_template,
        window=WINDOW_DESTRUCTIVE,
        permission="email_templates.templates.delete",
        label="Delete email template",
    )
)

register(
    FormAction(
        key="role.delete",
        entity_types=("role",),
        execute=_delete_role,
        window=WINDOW_DESTRUCTIVE,
        permission="user_management.roles.delete",
        label="Delete role",
    )
)

register(
    FormAction(
        key="team.delete",
        entity_types=("team",),
        execute=_delete_team,
        window=WINDOW_DESTRUCTIVE,
        permission="user_management.teams.delete",
        label="Delete team",
    )
)

register(
    FormAction(
        key="access_agent.delete",
        entity_types=("access_agent",),
        execute=_delete_access_agent,
        window=WINDOW_DESTRUCTIVE,
        permission="user_management.access_agents.delete",
        label="Delete access agent",
    )
)

register(
    FormAction(
        key="product_spec_value.clear",
        # The record is the VALUE on one product, so the entity id names both halves of
        # it: `<product id>:<spec key>`. The spec key alone is shared by every product
        # that holds it, and the engine's one-pending-action-per-record index would then
        # treat two products as one record.
        entity_types=("product_spec_value",),
        execute=_clear_product_spec_value,
        # `revert` hands the key back to derivation and `absent` writes a tombstone.
        # The tombstone is the irreversible one, so both take the long window rather
        # than the screen having to pick a different countdown per menu item.
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.products.edit",
        label="Clear specification",
    )
)

def _unverify_spec_verification(db: Session, payload: dict):
    from app.services import product_spec_verification

    # Keyed by the PRODUCT CODE, not a uuid - `product_spec_verification.unverify_code`
    # takes the code, and the verification ledger has no row of its own to key on
    # (`ProductSpecVerification` is append-only and never re-pointed).
    return product_spec_verification.unverify_code(
        db, _entity_id(payload), actor=_actor(db, payload)
    )


register(
    FormAction(
        key="spec_verification.unverify",
        entity_types=("spec_verification",),
        execute=_unverify_spec_verification,
        # Reversible: withdrawing a stamp is not a delete - Verify can be pressed again
        # on the same row afterwards, and the ledger row itself is never removed, only
        # marked withdrawn (D8).
        window=WINDOW_REVERSIBLE,
        permission="master_data.products.edit",
        label="Unverify",
    )
)


def _delete_spec_key(db: Session, payload: dict):
    from app.services.product_spec_registry import delete_registry_key

    # Keyed by the spec key ITSELF, not a uuid - `delete_registry_key` takes it and is
    # the same refusal `DELETE /spec-registry/{spec_key}` runs (D.6, PLAN-spec-
    # workbench-redesign.md): a seed-sourced key is refused, never deleted.
    return delete_registry_key(db, _entity_id(payload))


register(
    FormAction(
        key="spec_key.delete",
        entity_types=("spec_key",),
        execute=_delete_spec_key,
        window=WINDOW_DESTRUCTIVE,
        permission="master_data.spec_registry.delete",
        label="Delete specification",
    )
)

def _remove_spec_rule(db: Session, payload: dict):
    from app.services.product_spec_registry import remove_rule

    # Keyed by the spec key: the rule has no id of its own, so the payload carries the
    # builder it is (#1286, D8: removing a rule is the way back from a mistake).
    return remove_rule(db, _entity_id(payload), payload.get("builder") or {})


def _remove_spec_value(db: Session, payload: dict):
    from app.services.product_spec_registry import remove_value

    return remove_value(db, _entity_id(payload), payload.get("value"))


def _remove_spec_word(db: Session, payload: dict):
    from app.services.product_spec_registry import remove_word

    return remove_word(db, _entity_id(payload), payload.get("value"), payload.get("word"))


# Reversible, all three: a rule, a choice or a word can be added back on the same
# screen, and a shipped choice or word is only suppressed (D13). Parked against the
# spec key; the entity type names the thing being removed as well, so a key's prefix is
# one of its entity types like every other record action.
register(
    FormAction(
        key="spec_rule.remove",
        entity_types=("spec_key", "spec_rule"),
        execute=_remove_spec_rule,
        window=WINDOW_REVERSIBLE,
        permission="master_data.spec_registry.edit",
        label="Remove rule",
    )
)

register(
    FormAction(
        key="spec_value.remove",
        entity_types=("spec_key", "spec_value"),
        execute=_remove_spec_value,
        window=WINDOW_REVERSIBLE,
        permission="master_data.spec_registry.edit",
        label="Remove choice",
    )
)

register(
    FormAction(
        key="spec_word.remove",
        entity_types=("spec_key", "spec_word"),
        execute=_remove_spec_word,
        window=WINDOW_REVERSIBLE,
        permission="master_data.spec_registry.edit",
        label="Remove word",
    )
)

register(
    FormAction(
        key="stock_visibility_policy.remove",
        entity_types=("stock_visibility_policy",),
        execute=_remove_stock_visibility_policy,
        # Reversible: the tier falls back to the policy above it and the card can
        # write the override again from what is still on screen.
        window=WINDOW_REVERSIBLE,
        permission="inventory.stock.edit",
        label="Remove stock visibility",
    )
)

register(
    FormAction(
        key="spec_visibility_policy.remove",
        entity_types=("spec_visibility_policy",),
        execute=_remove_spec_visibility_policy,
        # Reversible: the tier falls back to the policy above it and the card can
        # write the override again from what is still on screen.
        window=WINDOW_REVERSIBLE,
        permission="user_management.contacts.edit",
        label="Remove spec visibility",
    )
)

register(
    FormAction(
        key="contact_customer_link.unlink",
        # Parked on the LINK row, not the contact or the customer: the pair is what goes.
        entity_types=("contact_customer_link",),
        execute=_unlink_contact_customer,
        # Reversible: both ends survive and the link can be made again in one pick.
        window=WINDOW_REVERSIBLE,
        permission="user_management.contacts.edit",
        label="Unlink customer",
    )
)

register(
    FormAction(
        key="customer.unassign_sales_agent",
        entity_types=("customer",),
        execute=_unassign_customer_sales_agent,
        # Reversible: the agent can be assigned again from the same tab.
        window=WINDOW_REVERSIBLE,
        permission="master_data.sales_agents.edit",
        label="Unassign customer",
    )
)

register(
    FormAction(
        key="customer_group.delete",
        entity_types=("customer_group",),
        execute=_delete_customer_group,
        # Hard delete: the ledgers keep existing with no group (FK ON DELETE SET NULL).
        window=WINDOW_DESTRUCTIVE,
        permission="order_management.customers.delete",
        label="Delete customer group",
    )
)

register(
    FormAction(
        key="customer.remove_from_group",
        entity_types=("customer",),
        execute=_remove_customer_from_group,
        # Reversible: the ledger can be added back from the group page.
        window=WINDOW_REVERSIBLE,
        permission="order_management.customers.edit",
        label="Remove from group",
    )
)

register(
    FormAction(
        key="signin_background.remove",
        # A singleton setting, not a row: the frontend parks it against the constant
        # `signin-background`, because there is one of it and the reader never sees an id.
        entity_types=("signin_background",),
        execute=_remove_signin_background,
        # Reversible: the sign-in page falls back to its designed default and the admin
        # still holds the file they uploaded, so putting it back is one drop away.
        window=WINDOW_REVERSIBLE,
        permission="user_management.settings.edit",
        label="Remove sign-in background",
    )
)

register(
    FormAction(
        key="market_segment.delete",
        entity_types=("market_segment",),
        execute=_delete_market_segment,
        window=WINDOW_DESTRUCTIVE,
        # `.manage`, not `.view`: a read grant authorising a hard delete was wrong
        # in principle, even though the immediate route it replaced had no slug
        # at all (issue #402).
        permission="user_management.reference_data.manage",
        label="Delete market segment",
    )
)

register(
    FormAction(
        key="onboarding_request.delete",
        entity_types=("onboarding_request",),
        execute=_delete_onboarding_request,
        window=WINDOW_DESTRUCTIVE,
        permission="user_management.onboarding.delete",
        label="Delete onboarding request",
    )
)


def _delete_translation(db: Session, payload: dict):
    from app.services import translation_service

    return translation_service.delete_memory(db, _entity_id(payload))


register(
    FormAction(
        key="translation_memory.delete",
        entity_types=("translation_memory",),
        execute=_delete_translation,
        window=WINDOW_DESTRUCTIVE,
        permission="system.translations.edit",
        label="Delete translation",
    )
)


# ----- Dealer kit -----------------------------------------------------------------------
#
# One slug for all five, `dealer_kit.page.edit`, because that is the single grant every
# one of these DELETE routes is behind today.


def _delete_dk_bundle(db: Session, payload: dict):
    from app.services.dealer_kit import bundle_service

    return bundle_service.delete_bundle(db, _entity_id(payload))


def _delete_dk_collection(db: Session, payload: dict):
    from app.services.dealer_kit import collection_service

    return collection_service.delete_collection(db, _entity_id(payload))


def _delete_dk_page(db: Session, payload: dict):
    from app.services.dealer_kit import page_service

    return page_service.delete_page(db, _entity_id(payload))


def _delete_dk_flyer_reading(db: Session, payload: dict):
    from app.services.dealer_kit import flyer_reading_service

    return flyer_reading_service.delete_reading(db, _entity_id(payload))


def _delete_dk_tile_template(db: Session, payload: dict):
    from app.services.dealer_kit import tile_template_service

    return tile_template_service.delete_template(db, _entity_id(payload))


for _key, _entity, _fn, _label in (
    ("dk_bundle.delete", "dk_bundle", _delete_dk_bundle, "Delete bundle"),
    ("dk_collection.delete", "dk_collection", _delete_dk_collection, "Delete collection"),
    ("dk_page.delete", "dk_page", _delete_dk_page, "Delete page"),
    (
        "dk_flyer_reading.delete",
        "dk_flyer_reading",
        _delete_dk_flyer_reading,
        "Delete flyer reading",
    ),
    (
        "dk_tile_design.delete",
        "dk_tile_design",
        _delete_dk_tile_template,
        "Delete tile design",
    ),
):
    register(
        FormAction(
            key=_key,
            entity_types=(_entity,),
            execute=_fn,
            window=WINDOW_DESTRUCTIVE,
            permission="dealer_kit.page.edit",
            label=_label,
        )
    )


def _bulk_delete_tag_templates(db: Session, payload: dict):
    """Delete a SELECTION of templates atomically (D26, S11).

    One handler, one call, over the whole list of ids - not
    `useDeferredBulkAction`'s one-pending-action-per-row shape, because the
    batch has to refuse together (AC-S11-2): a foreign or missing id must stop
    every row in the selection, not just its own. `entity_id` here is a
    client-generated token naming the CLICK, not a real template - there is
    no single record a multi-row delete is "about", and the ids that matter
    travel in the payload instead.
    """
    from app.services.dealer_kit import tag_template_service

    ids = payload.get("template_ids") or []
    return tag_template_service.bulk_delete(
        db,
        [str(i) for i in ids],
        requested_by_id=payload.get("requested_by_id"),
    )


def _delete_tag_template(db: Session, payload: dict):
    from app.services.dealer_kit import tag_template_service

    return tag_template_service.delete_template(
        db, _entity_id(payload), requested_by_id=payload.get("requested_by_id")
    )


for _key, _fn, _label in (
    ("tag_template.delete", _delete_tag_template, "Delete tag template"),
    ("tag_template.bulk_delete", _bulk_delete_tag_templates, "Delete tag templates"),
):
    register(
        FormAction(
            key=_key,
            entity_types=("tag_template",),
            execute=_fn,
            window=WINDOW_DESTRUCTIVE,
            permission="dealer_kit.tag_templates.manage",
            label=_label,
        )
    )


def _delete_tag_size_preset(db: Session, payload: dict):
    """Both callers - the `/dealer-kit/tag-sizes` listing's row menu and the
    request designer's Tag Size control (the saved-size `x`) - park through
    this ONE key, same as `tag_template.delete`."""
    from app.services.dealer_kit import tag_size_service

    return tag_size_service.delete_preset(db, _entity_id(payload))


register(
    FormAction(
        key="tag_size_preset.delete",
        entity_types=("tag_size_preset",),
        execute=_delete_tag_size_preset,
        window=WINDOW_DESTRUCTIVE,
        # D9: the tag template slugs, no new grant.
        permission="dealer_kit.tag_templates.manage",
        label="Delete tag size",
    )
)


def _delete_dealer_kit_asset(db: Session, payload: dict):
    """A library asset: artwork or a brand font (PLAN-brand-font-manage.md).

    Same guard the immediate `DELETE /dealer-kit/assets/{id}` route uses -
    ``delete_asset_guarded`` refuses (``FONT_IN_USE`` / ``ASSET_IN_USE``) while
    anything still names it, which the frontend only learns once the window
    lapses and the commit fails (AC-10): the countdown disappears and the
    toast carries the reason, same as any other guarded deferred delete.
    """
    from app.services.dealer_kit import asset_service

    return asset_service.delete_asset_guarded(db, _entity_id(payload))


register(
    FormAction(
        key="dealer_kit_asset.delete",
        entity_types=("dealer_kit_asset",),
        execute=_delete_dealer_kit_asset,
        window=WINDOW_DESTRUCTIVE,
        permission="dealer_kit.library.manage",
        label="Delete asset",
    )
)


def _undo_flyer_code_adopt(db: Session, payload: dict):
    """"This printed code is NOT that product" - undoing an adoption (D7, S1).

    There is no row of its own to key a pending action on: the thing being
    detached is one entry inside a reading's `code_overrides` JSONB map, not a
    record with an id. So the entity id names BOTH (`<reading id>:<printed
    code>`), the same shape `_clear_product_spec_value` above uses for a
    product's spec value - and like that one, this reads BOTH out of the id
    rather than trusting separate payload fields, which is what
    `test_every_handler_resolves_its_service_import` drives every handler
    with (no `reading_id`/`printed_code` keys, only a bare `entity_id`).

    A reading id is a fixed 36-char UUID, so this SLICES at that width rather
    than splitting on the first ':' - a printed code never contains one, but
    slicing also never raises on an entity id that has no colon at all (the
    harness's bare-uuid stand-in), where a `str.split(":", 1)` unpack would
    raise `ValueError` before ever reaching `db` and fail that test for the
    wrong reason.

    Same service call the DELETE route makes (`flyer_reading_service.
    unadopt_code`) - the deferred path changes WHEN this runs, never what it
    does.
    """
    from app.services.dealer_kit import flyer_reading_service

    entity_id = _entity_id(payload)
    reading_id, printed_code = entity_id[:36], entity_id[37:]
    record = flyer_reading_service.get_reading(db, reading_id)
    return flyer_reading_service.unadopt_code(db, record, printed_code=printed_code)


register(
    FormAction(
        key="flyer_code_adoption.undo",
        entity_types=("flyer_code_adoption",),
        execute=_undo_flyer_code_adopt,
        # Reversible, not destructive: re-adopting the same code afterwards
        # writes the same key back (AC-A.4), and nothing already applied to
        # the product is touched either way the window resolves (R2).
        window=WINDOW_REVERSIBLE,
        # `master_data.products.edit` only - the direct DELETE route also
        # requires `dealer_kit.page.view`, and this generic route checks ONE
        # slug. Narrowing that pair to its write half is a deliberate,
        # reviewed call for this action: `products.edit` is the permission
        # that actually authorises the write (which product a code means),
        # `page.view` scopes which readings a caller may look at, and this
        # path is reached from the button on a reading the caller is already
        # looking at, gated by the page's own view permission before the
        # button ever renders.
        permission="master_data.products.edit",
        label="Undo code adoption",
    )
)


# ----- SCM ------------------------------------------------------------------------------


def _scm_actor(db: Session, payload: dict) -> Optional[str]:
    """The caller's human NAME, which is what SCM writes into provenance."""
    who = _actor(db, payload)
    return who.get("name") or who.get("email") or None


def _delete_loading_plan(db: Session, payload: dict):
    from app.services.scm import loading_plan_service

    return loading_plan_service.delete_record(db, _entity_id(payload))


def _capture_loading_plan_delete(db: Session, payload: dict) -> dict:
    """Refuse a sent plan's delete at PARK time, not ten seconds later (AC-A7).

    Mirrors `_capture_loading_plan_cancel`: `capture` runs before anything is
    persisted, on both the deferred and the immediate path, so it doubles as the
    premise check `refuse_if_sent` already owns for the immediate DELETE route.
    """
    from app.services.scm import loading_plan_service

    loading_plan_service.refuse_if_sent(db, _loading_plan_or_404(db, payload))
    return {}


def _loading_plan_or_404(db: Session, payload: dict):
    from app.models.scm import LoadingPlan
    from app.services.error_handler import handle_not_found

    plan_id = _entity_id(payload)
    plan = db.query(LoadingPlan).filter(LoadingPlan.id == plan_id).first()
    if plan is None:
        raise handle_not_found("Loading plan", plan_id)
    return plan


def _capture_loading_plan_cancel(db: Session, payload: dict) -> dict:
    """Refuse an already-cancelled plan at PARK time, not ten seconds later.

    `capture` is the one hook `dispatch` runs before anything is persisted, on both
    the deferred and the immediate path, so it doubles as the premise check here: it
    snapshots nothing (a record action is never undone) and instead raises the same
    409 `refuse_if_cancelled` raises for the immediate route (AC-A7).
    """
    from app.services.scm import loading_plan_service

    loading_plan_service.refuse_if_cancelled(_loading_plan_or_404(db, payload))
    return {}


def _cancel_loading_plan(db: Session, payload: dict):
    from app.services.scm import loading_plan_service

    out = loading_plan_service.cancel_record(
        db, _loading_plan_or_404(db, payload), actor=_scm_actor(db, payload)
    )
    db.commit()
    return out


def _delete_market_topic(db: Session, payload: dict):
    from app.services.scm import market_research_service

    return market_research_service.delete_topic(db, _entity_id(payload))


def _delete_container_size(db: Session, payload: dict):
    from app.models.scm import ContainerSize
    from app.services.error_handler import handle_not_found

    # No service method to call: `DELETE /container-sizes/{id}` is these three lines and
    # no rule of its own, so there is nothing here that could drift from it.
    row = db.query(ContainerSize).filter(ContainerSize.id == _entity_id(payload)).first()
    if row is None:
        raise handle_not_found("Container size", _entity_id(payload))
    db.delete(row)
    db.commit()


def _delete_currency_rate(db: Session, payload: dict):
    from app.services.scm import currency_rate_service

    # Keyed by CURRENCY, not a uuid - that is what the route takes too.
    return currency_rate_service.delete_rate(db, _entity_id(payload))


def _delete_reorder_policy(db: Session, payload: dict):
    from app.services.scm import policy_service

    return policy_service.delete_policy(db, _entity_id(payload))


def _delete_scm_sales_order(db: Session, payload: dict):
    from app.services.scm.sales_order_service import SalesOrderService

    return SalesOrderService(db).delete(_entity_id(payload))


def _delete_proforma_invoice(db: Session, payload: dict):
    from app.services.scm import proforma_invoice_service

    proforma_invoice_service.delete(db, _entity_id(payload))
    db.commit()


def _forget_supplier_code_alias(db: Session, payload: dict):
    from app.services.scm import supplier_code_alias_service

    out = supplier_code_alias_service.delete(
        db, _entity_id(payload), actor=_scm_actor(db, payload)
    )
    db.commit()
    return out


register(
    FormAction(
        key="loading_plan.delete",
        entity_types=("loading_plan",),
        execute=_delete_loading_plan,
        capture=_capture_loading_plan_delete,
        window=WINDOW_DESTRUCTIVE,
        permission="scm.reorder.run",
        label="Delete loading plan",
    )
)

register(
    FormAction(
        key="loading_plan.cancel",
        entity_types=("loading_plan",),
        execute=_cancel_loading_plan,
        capture=_capture_loading_plan_cancel,
        window=WINDOW_REVERSIBLE,
        permission="scm.reorder.run",
        label="Cancel loading plan",
    )
)

register(
    FormAction(
        key="market_topic.delete",
        entity_types=("market_topic",),
        execute=_delete_market_topic,
        window=WINDOW_DESTRUCTIVE,
        permission="scm.policy.manage",
        label="Delete research topic",
    )
)

register(
    FormAction(
        key="container_size.delete",
        entity_types=("container_size",),
        execute=_delete_container_size,
        window=WINDOW_DESTRUCTIVE,
        permission="scm.reorder.run",
        label="Delete container size",
    )
)

register(
    FormAction(
        key="currency_rate.delete",
        entity_types=("currency_rate",),
        execute=_delete_currency_rate,
        window=WINDOW_DESTRUCTIVE,
        permission="scm.config.manage",
        label="Remove exchange rate",
    )
)

register(
    FormAction(
        key="reorder_policy.delete",
        entity_types=("reorder_policy",),
        execute=_delete_reorder_policy,
        window=WINDOW_DESTRUCTIVE,
        permission="scm.policy.manage",
        label="Delete policy",
    )
)

register(
    FormAction(
        key="scm_sales_order.delete",
        entity_types=("scm_sales_order",),
        execute=_delete_scm_sales_order,
        window=WINDOW_DESTRUCTIVE,
        permission="scm.reorder.run",
        label="Delete sales order",
    )
)

register(
    FormAction(
        key="proforma_invoice.delete",
        entity_types=("proforma_invoice",),
        execute=_delete_proforma_invoice,
        window=WINDOW_DESTRUCTIVE,
        permission="scm.proforma_invoice.upload",
        label="Delete proforma invoice",
    )
)

register(
    FormAction(
        key="supplier_code_alias.forget",
        entity_types=("supplier_code_alias",),
        execute=_forget_supplier_code_alias,
        # Reversible: the rows go back to whatever the ladder says now, and the ruling
        # can be made again from the same screen.
        window=WINDOW_REVERSIBLE,
        permission="scm.reorder.run",
        label="Forget supplier-code match",
    )
)


def _dismiss_packing_line(db: Session, payload: dict):
    from app.services.scm import proforma_invoice_packing_service

    # `invoice_id` scopes the row, exactly as the route does: a row id under ANOTHER
    # invoice must not be reachable off a guess.
    out = proforma_invoice_packing_service.dismiss_packing_line(
        db,
        str(payload.get("invoice_id") or ""),
        _entity_id(payload),
        actor=_scm_actor(db, payload),
    )
    db.commit()
    return {"id": str(out.id), "match_state": out.match_state}


register(
    FormAction(
        key="proforma_invoice_packing_line.dismiss",
        entity_types=("proforma_invoice_packing_line",),
        execute=_dismiss_packing_line,
        # Reversible: the row stays on the invoice, and Undo dismiss is on its own gear
        # afterwards. Same window and same reasoning as `supplier_code_alias.forget`,
        # which is the ruling this one writes.
        window=WINDOW_REVERSIBLE,
        permission="scm.proforma_invoice.upload",
        label="Dismiss packing row",
    )
)


def _forget_import_field_alias(db: Session, payload: dict):
    from app.models.import_alias import ImportFieldAlias
    from app.services.error_handler import handle_not_found

    alias_id = _entity_id(payload)
    row = db.query(ImportFieldAlias).filter(ImportFieldAlias.id == alias_id).first()
    if row is None:
        raise handle_not_found("Import column mapping", alias_id)
    db.delete(row)
    db.commit()
    return {"deleted": 1}


register(
    FormAction(
        key="import_field_alias.forget",
        entity_types=("import_field_alias",),
        execute=_forget_import_field_alias,
        # Reversible, and named `forget` rather than `delete` for the same reason
        # `supplier_code_alias.forget` is: what goes is a REMEMBERED spelling, re-added
        # from the same page in one line if it turns out to have been wanted.
        window=WINDOW_REVERSIBLE,
        permission="system.import_field_aliases.edit",
        label="Remove import column mapping",
    )
)


def _delete_shipment_line_photo(db: Session, payload: dict):
    from app.services.scm import shipment_line_photos

    # `shipment_id`/`line_id` scope the delete (review round 1, item 1) - the route
    # puts both there, same as `entity_id`; without them a photo id under another
    # shipment would be reachable off a guess.
    return shipment_line_photos.delete_photo(
        db,
        str(payload.get("shipment_id") or ""),
        str(payload.get("line_id") or ""),
        _entity_id(payload),
    )


register(
    FormAction(
        key="shipment_line_photo.delete",
        entity_types=("shipment_line_photo",),
        execute=_delete_shipment_line_photo,
        # Destructive: the file itself is removed (link, attachment row and object),
        # same as every other file delete in this registry - re-adding it means
        # uploading it again, not undoing a link.
        window=WINDOW_DESTRUCTIVE,
        permission="scm.reorder.run",
        label="Delete shipment line photo",
    )
)


def _delete_sales_order_line_attachment(db: Session, payload: dict):
    from app.services import so_line_attachments

    # `line_id` scopes the delete (mirrors `shipment_line_photo.delete`'s own rule) -
    # without it a link id under another line would be reachable off a guess.
    return so_line_attachments.delete(
        db,
        str(payload.get("line_id") or ""),
        _entity_id(payload),
        payload.get("requested_by_id"),
    )


register(
    FormAction(
        key="sales_order_line_attachment.delete",
        entity_types=("sales_order_line_attachment",),
        execute=_delete_sales_order_line_attachment,
        # Destructive: the file itself is removed (link, attachment row and object) -
        # re-adding it means uploading it again, not undoing a link (Q8).
        window=WINDOW_DESTRUCTIVE,
        permission="projects.projects.edit",
        label="Delete attachment",
    )
)


def _delete_notification(db: Session, payload: dict):
    from app.services.error_handler import handle_not_found
    from app.services.notification_service import NotificationService

    # Scoped to the requester, exactly as DELETE /notifications/{id} is: the row this
    # can reach is the reader's own, which is what stands in for a permission slug.
    notification_id = _entity_id(payload)
    requested_by_id = payload.get("requested_by_id")
    if not requested_by_id:
        raise handle_not_found("Notification", notification_id)
    if not NotificationService(db).delete(notification_id, str(requested_by_id)):
        raise handle_not_found("Notification", notification_id)
    return {"message": "Deleted"}


register(
    FormAction(
        key="notification.delete",
        entity_types=("notification",),
        execute=_delete_notification,
        # Reversible: the notification is a copy of something that happened elsewhere,
        # and the reader can Clear the whole list beside this button anyway. Five
        # seconds is the window this needs - enough that no click is final on its own.
        window=WINDOW_REVERSIBLE,
        permission=OWN_RECORD,
        label="Delete notification",
    )
)


def _delete_saved_view(db: Session, payload: dict):
    from app.services.saved_views_service import SavedViewsService

    # `SavedViewsService.delete` already answers 404 (via `handle_not_found`) for a view
    # that is not the requester's own, so ownership IS the check - the same shape
    # `notification.delete` above uses.
    SavedViewsService(db).delete(_entity_id(payload), str(payload.get("requested_by_id") or ""))
    return {"message": "Deleted"}


register(
    FormAction(
        key="saved_view.delete",
        entity_types=("saved_view",),
        execute=_delete_saved_view,
        # Destructive (the suite's own default for every `.delete`, `test_record_actions_s6b
        # .SHORT_WINDOW_DELETES` is a narrow allowlist): unlike a notification, a saved view
        # can carry real, non-trivial filter/sort/column work a reader built up over time,
        # so ten seconds to catch a stray click is worth more than the five-second window.
        window=WINDOW_DESTRUCTIVE,
        permission=OWN_RECORD,
        label="Delete view",
    )
)


def _void_price_tag_request(db: Session, payload: dict):
    from app.services.price_tag_request_service import (
        PriceTagRequestService,
        STATUS_VOID,
    )

    return PriceTagRequestService.transition_status(
        db,
        _entity_id(payload),
        STATUS_VOID,
        user_id=str(payload.get("requested_by_id") or "") or None,
    )


register(
    FormAction(
        key="price_tag_request.void",
        entity_types=("price_tag_request",),
        execute=_void_price_tag_request,
        # Destructive, and not a `.delete`: void is the end of the line for a
        # request - no transition leaves it - so the grace window IS the way
        # back, exactly as it is for a delete.
        window=WINDOW_DESTRUCTIVE,
        permission="dealer_kit.price_tag_requests.process",
        label="Void request",
    )
)


def _undo_confirm(db: Session, payload: dict):
    """The board's gear entry, "Undo <SO> confirm (rev N)" (S2, #979).

    `entity_id` is the planning record (`project_sales_order_id`), and `decision_id` in
    the payload is the decision the pending action was created against - a Confirm
    written during the countdown makes a DIFFERENT decision the newest by the time the
    window lapses, and `undo_last_confirm` refuses `superseded` rather than undoing the
    wrong revision (AC-UC-28).

    `mode` (AC-R2-34, S5, `PLAN-scm-oi-handover-r2-undo.md`) routes to `reconstruct_
    undo` instead when the gear entry was a reconstructed one - re-checked here
    against the CURRENT active decision's own journal state (a park-time 409/403
    already refused a mismatch or a non-admin caller; this is the same guard run
    again at commit, the same belt-and-braces `undo_last_confirm`'s own `superseded`
    check already is for the journal path).

    B2 (review round 1): EXECUTE re-runs BOTH checks the journal path's own
    `undo_last_confirm` already runs at commit and the reconstructed path was
    missing entirely - the role gate (a countdown can span a demotion) and
    `_assert_actor_can_undo` (Confirm's own per-project authorisation, over every
    order the reconstruct touches, donor included) - not only the park-time gate.
    Park already refused a non-admin/wrong-role caller before a window ever
    started, so this is belt-and-braces for the countdown itself, exactly the same
    reasoning `undo_last_confirm`'s own re-checks already document.
    """
    from app.services.project_supply_service import ProjectSupplyService
    from app.services.project_supply_undo_service import _decision_is_journalled, undo_last_confirm

    order = ProjectSupplyService(db).get_order(_entity_id(payload))
    mode = payload.get("mode") or "journal"

    if mode == "reconstructed":
        from app.models.project_so import DECISION_ACTIVE, SOSupplyDecision
        from app.services.error_handler import AppException
        from app.services.project_supply_undo_reconstruct_service import reconstruct_undo
        from app.services.project_supply_undo_service import (
            _assert_actor_can_undo,
            touched_project_sales_order_ids,
        )
        from app.services.user_service import UserPermissionService

        actor_id = payload.get("requested_by_id")
        role_slugs = (
            UserPermissionService(db).get_user_role_slugs(actor_id) if actor_id else set()
        )
        if not (role_slugs & {"superadmin", "admin"}):
            raise AppException(
                status_code=403,
                message="Only an admin can run a reconstructed undo.",
                code="FORBIDDEN",
            )

        decision = (
            db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id == order.id,
                SOSupplyDecision.state == DECISION_ACTIVE,
            )
            .first()
        )
        if decision is None or _decision_is_journalled(decision):
            raise AppException(
                status_code=409,
                message="This confirm carries a journal; use the journal undo.",
                code="mode_mismatch",
            )
        expected_decision_id = payload.get("decision_id")
        if expected_decision_id and str(decision.id) != str(expected_decision_id):
            raise AppException(
                status_code=409,
                message="A newer confirm has already replaced this one.",
                code="superseded",
            )
        _assert_actor_can_undo(
            db, actor_id, touched_project_sales_order_ids(db, decision)
        )
        return reconstruct_undo(db, order, decision, actor_user_id=actor_id)

    return undo_last_confirm(
        db,
        order,
        actor_user_id=payload.get("requested_by_id"),
        expected_decision_id=payload.get("decision_id"),
    )


def _discard_cost_price_change_set(db: Session, payload: dict):
    from app.services.procurement.cost_price_change_service import discard

    return discard(db, _entity_id(payload))


register(
    FormAction(
        key="cost_price_change_set.discard",
        entity_types=("cost_price_change_set",),
        execute=_discard_cost_price_change_set,
        # A Draft set has nothing live changed yet - there is no way back once the
        # window lapses, same as any other hard delete (#1288, AC-S1-23).
        window=WINDOW_DESTRUCTIVE,
        permission="procurement.cost_price_changes.upload",
        label="Discard price change",
    )
)


def _delete_product_supplier_cost(db: Session, payload: dict):
    from app.models.cost_price import ProductSupplierCost
    from app.services.error_handler import handle_not_found
    from app.services.procurement.supplier_cost_service import delete_cost

    cost_id = _entity_id(payload)
    row = db.query(ProductSupplierCost).filter(ProductSupplierCost.id == cost_id).first()
    if row is None:
        raise handle_not_found("Cost row", cost_id)
    return delete_cost(
        db, str(row.product_supplier_id), cost_id,
        {"id": payload.get("requested_by_id")},
    )


register(
    FormAction(
        key="product_supplier_cost.delete",
        entity_types=("product_supplier_cost",),
        execute=_delete_product_supplier_cost,
        window=WINDOW_DESTRUCTIVE,
        # The section lives on the supplier's Prices tab / product's Suppliers tab,
        # both gated by the product-suppliers edit slug (#1288, contract 2.3).
        permission="procurement.product_suppliers.edit",
        label="Delete cost row",
    )
)


register(
    FormAction(
        key="project_sales_order.undo_confirm",
        entity_types=("project_sales_order",),
        execute=_undo_confirm,
        # Reversible: the previous revision comes back exactly as it was (R2), not a
        # hard delete with nothing left to catch.
        window=WINDOW_REVERSIBLE,
        # Confirm's own slug (`fulfilment_planning.py:73`) - undoing it takes the same
        # grant as doing it.
        permission="projects.projects.edit",
        label="Undo confirm",
    )
)


def _delete_contact_chatbot_fact(db: Session, payload: dict):
    from app.services.contact_service import ContactService
    from app.services.error_handler import handle_unprocessable

    # Chatbot memory lane A (contract section 5): `entity_id` is `"<contactId>:<key>"`
    # - one fact of one contact, so a composite id rather than a payload key, the same
    # shape `ContactAccessAgentsTable.tsx` already builds for this action.
    # N1 (security review 26 Sep 2026): a composite id missing its `:` is 422, the
    # same as an unknown key - not a 404 against whatever the whole string happens
    # to look like as a contact id.
    entity_id = _entity_id(payload)
    contact_id, sep, key = entity_id.partition(":")
    if not sep or not key:
        raise handle_unprocessable(f"{entity_id!r} is not a valid contact fact id.")
    return ContactService(db).delete_contact_fact(contact_id, key)


register(
    FormAction(
        key="contact_chatbot_fact.delete",
        entity_types=("contact_chatbot_fact",),
        execute=_delete_contact_chatbot_fact,
        # Hard delete (contract section 4): a learned or said fact leaves a
        # tombstone, a staff fact leaves nothing - either way there is no
        # "undo" a reversible window could restore, so this is the destructive one.
        window=WINDOW_DESTRUCTIVE,
        permission="user_management.contacts.edit",
        label="Delete fact",
    )
)


# --------------------------------------------------------------------------------------
# Ideas (IDEATION-IN-CRM): the ss call is made as the user who started the action, because
# the ss comment and idea rules (own-or-moderator, scope) are decided per embed principal.
# --------------------------------------------------------------------------------------


def _requester(db: Session, payload: dict) -> dict:
    from app.services.ideation_gateway_service import user_for_requester

    return user_for_requester(db, str(payload["requested_by_id"]))


def _archive_idea(db: Session, payload: dict):
    from app.services.ideation_gateway_service import call_ss, require_uuid, ss_path

    user = _requester(db, payload)
    idea_id = require_uuid(_entity_id(payload), "idea")
    return call_ss(db, user, "POST", ss_path("embed", "ideas", idea_id, "status"), json={"status": "archived"})


def _delete_idea(db: Session, payload: dict):
    from app.services.ideation_gateway_service import call_ss, require_uuid, ss_path

    user = _requester(db, payload)
    idea_id = require_uuid(_entity_id(payload), "idea")
    return call_ss(db, user, "DELETE", ss_path("embed", "ideas", idea_id))


def _delete_idea_comment(db: Session, payload: dict):
    from app.services.ideation_gateway_service import call_ss, require_uuid, ss_path

    user = _requester(db, payload)
    idea_id = require_uuid(payload.get("idea_id"), "idea")
    comment_id = require_uuid(_entity_id(payload), "comment")
    return call_ss(db, user, "DELETE", ss_path("embed", "ideas", idea_id, "comments", comment_id))


register(
    FormAction(
        key="idea.archive",
        entity_types=("idea",),
        execute=_archive_idea,
        window=WINDOW_REVERSIBLE,
        permission="ideation.ideas.manage",
        label="Archive idea",
    )
)

register(
    FormAction(
        key="idea.delete",
        entity_types=("idea",),
        execute=_delete_idea,
        window=WINDOW_DESTRUCTIVE,
        permission="ideation.ideas.manage",
        label="Delete idea",
    )
)

register(
    FormAction(
        key="idea_comment.delete",
        entity_types=("idea_comment",),
        execute=_delete_idea_comment,
        window=WINDOW_DESTRUCTIVE,
        # Any viewer may park the delete of a comment; ss decides whether it is theirs to delete.
        permission="ideation.board.view",
        label="Delete comment",
    )
)
