"""Finance module API routes, mounted at `/finance` behind the `finance` module guard.

Billing documents (AutoCount invoices, cash sales, credit and debit notes) and the customer's
AR total, read only (plan PLAN-finance-billing-documents-27sep S2, card CARD-ac-billing-17oct).
The documents themselves arrive through the external ingest door, never here."""
from fastapi import APIRouter

from app.api.v1.finance import billing_documents

router = APIRouter()
router.include_router(
    billing_documents.router, prefix="/billing-documents", tags=["finance-billing-documents"]
)
router.include_router(
    billing_documents.customers_router, prefix="/customers", tags=["finance-billing-documents"]
)
