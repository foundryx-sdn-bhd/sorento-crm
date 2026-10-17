"""Central registry for list-query resources and shared metadata."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional

from app.models.certificate import Certificate
from app.models.marketing import Promotion
from app.models.order import Order
from app.models.product import Product
from app.models.procurement import Supplier
from app.models.stock_ask import StockAsk
from app.models.tickets import Ticket
from app.models.workflow_forms import WorkflowFormDefinition, WorkflowSubmission
from app.schemas.certificate import CertificateResponse
from app.schemas.marketing import PromotionResponse
from app.schemas.order import OrderResponse
from app.schemas.product import ProductResponse
from app.schemas.procurement import SupplierResponse
from app.schemas.tickets import TicketResponse
from app.schemas.workflow_forms import WorkflowFormDefinitionOut, WorkflowSubmissionOut


Serializer = Callable[[Iterable[Any]], list[Any]]


def _serialize_passthrough(rows: Iterable[Any]) -> list[Any]:
    return list(rows)


def _serialize_orders(rows: Iterable[Any]) -> list[Any]:
    return [OrderResponse.model_validate(x) for x in rows]


def _serialize_products(rows: Iterable[Any]) -> list[Any]:
    return [ProductResponse.model_validate(x) for x in rows]


def _serialize_suppliers(rows: Iterable[Any]) -> list[Any]:
    return [SupplierResponse.model_validate(x) for x in rows]


def _serialize_promotions(rows: Iterable[Any]) -> list[Any]:
    return [PromotionResponse.model_validate(x) for x in rows]


def _serialize_workflow_definitions(rows: Iterable[Any]) -> list[Any]:
    return [WorkflowFormDefinitionOut.model_validate(x) for x in rows]


def _serialize_workflow_submissions(rows: Iterable[Any]) -> list[Any]:
    return [WorkflowSubmissionOut.model_validate(x) for x in rows]


def _serialize_tickets(rows: Iterable[Any]) -> list[Any]:
    return [TicketResponse.model_validate(x) for x in rows]


def _serialize_stock_asks(rows: Iterable[Any]) -> list[Any]:
    """Stock asks name their contact, customer and product, so the serializer needs the
    row's own session to look the names up."""
    rows = list(rows)
    if not rows:
        return []
    from sqlalchemy.orm import object_session

    from app.services.stock_ask_service import serialize

    return serialize(object_session(rows[0]), rows)


def _serialize_certificates(rows: Iterable[Any]) -> list[Any]:
    """Certificates carry DERIVED validity, so a plain ``model_validate`` is wrong.

    ``validity_state`` / ``is_expired`` / ``days_until_expiry`` are computed off
    the CURRENT revision at read time and are not columns - validating the ORM
    row straight into the schema would fail on the required ``validity_state``.
    The session is taken off the rows themselves because the serializer contract
    has nowhere to pass one.
    """
    from sqlalchemy.orm import object_session

    rows = list(rows)
    if not rows:
        return []
    db = object_session(rows[0])
    if db is None:  # pragma: no cover - detached rows are not a real path
        return [CertificateResponse.model_validate(x) for x in rows]

    from app.services.certificate_service import CertificateService

    return CertificateService(db).serialize_many(rows)


@dataclass(frozen=True)
class ListQueryResourceAdapter:
    resource_key: str
    view_slug: str
    export_slug: str
    serializer: Serializer
    model: Optional[type[Any]] = None
    compile_prefix: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None


ADAPTERS: Dict[str, ListQueryResourceAdapter] = {
    "orders": ListQueryResourceAdapter(
        resource_key="orders",
        view_slug="order_management.orders.view",
        export_slug="order_management.orders.export",
        serializer=_serialize_orders,
        model=Order,
        compile_prefix="order",
        display_name="Orders",
        description="Order list filter/export metadata",
    ),
    "products": ListQueryResourceAdapter(
        resource_key="products",
        view_slug="master_data.products.view",
        export_slug="master_data.products.export",
        serializer=_serialize_products,
        model=Product,
        compile_prefix="product",
        display_name="Products",
        description="Product list filter/export metadata",
    ),
    "suppliers": ListQueryResourceAdapter(
        resource_key="suppliers",
        view_slug="procurement.suppliers.view",
        export_slug="procurement.suppliers.export",
        serializer=_serialize_suppliers,
        model=Supplier,
        compile_prefix="supplier",
        display_name="Suppliers",
        description="Supplier list filter/export metadata",
    ),
    "promotions": ListQueryResourceAdapter(
        resource_key="promotions",
        view_slug="marketing.promotions.view",
        # No dedicated export slug in current permission seed; keep same as view for now.
        export_slug="marketing.promotions.view",
        serializer=_serialize_promotions,
        model=Promotion,
        compile_prefix="promotion",
        display_name="Promotions",
        description="Promotion list filter/export metadata",
    ),
    "workflow_form_definitions": ListQueryResourceAdapter(
        resource_key="workflow_form_definitions",
        view_slug="workflow_forms.definitions.view",
        export_slug="workflow_forms.definitions.export",
        serializer=_serialize_workflow_definitions,
        model=WorkflowFormDefinition,
        compile_prefix="def",
        display_name="Workflow Form Definitions",
        description="Workflow form definition list-query metadata",
    ),
    "workflow_form_submissions": ListQueryResourceAdapter(
        resource_key="workflow_form_submissions",
        view_slug="workflow_forms.submissions.view",
        export_slug="workflow_forms.submissions.export",
        serializer=_serialize_workflow_submissions,
        model=WorkflowSubmission,
        compile_prefix="sub",
        display_name="Workflow Form Submissions",
        description="Workflow form submission list-query metadata",
    ),
    "certificates": ListQueryResourceAdapter(
        resource_key="certificates",
        view_slug="master_data.certificates.view",
        export_slug="master_data.certificates.export",
        serializer=_serialize_certificates,
        model=Certificate,
        compile_prefix="certificate",
        display_name="Certificates",
        description="Certificate register list filter/export metadata",
    ),
    "tickets": ListQueryResourceAdapter(
        resource_key="tickets",
        view_slug="tickets.tickets.view",
        export_slug="tickets.tickets.export",
        serializer=_serialize_tickets,
        model=Ticket,
        compile_prefix="ticket",
        display_name="Tickets",
        description="Ticket list filter/export metadata",
    ),
    # AC-BILLING S2: the finance Billing Documents list. No model/compile prefix: its export
    # columns are the list row's own keys (see `billing_document_query_service.export_rows`),
    # so there is no metadata to sync.
    "finance_billing_documents": ListQueryResourceAdapter(
        resource_key="finance_billing_documents",
        view_slug="finance.billing_documents.view",
        export_slug="finance.billing_documents.export",
        serializer=_serialize_passthrough,
        display_name="Billing Documents",
        description="Finance billing document list export",
    ),
    # Chatbot stock ask v2 S5: the customer detail page's Asks tab (column config is keyed
    # by `order_management.customers.view::stock_asks`). The plan builds no export, so the
    # export slug is one no role is granted (`_crud` seeds view/add/edit/delete only).
    "stock_asks": ListQueryResourceAdapter(
        resource_key="stock_asks",
        view_slug="order_management.customers.view",
        export_slug="order_management.customers.export",
        serializer=_serialize_stock_asks,
        model=StockAsk,
        compile_prefix="stock_ask",
        display_name="Stock Asks",
        description="Chatbot stock asks per customer",
    ),
}


def get_adapter(resource_key: str) -> Optional[ListQueryResourceAdapter]:
    return ADAPTERS.get((resource_key or "").strip())


def require_adapter(resource_key: str) -> ListQueryResourceAdapter:
    adapter = get_adapter(resource_key)
    if not adapter:
        raise ValueError(f"Unsupported resource: {resource_key}")
    return adapter


def list_registered_resource_keys() -> list[str]:
    return list(ADAPTERS.keys())
