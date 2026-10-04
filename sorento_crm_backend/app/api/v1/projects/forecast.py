"""Forecast and dashboard API (S5a, UAC Group I).

One composite read for the dashboard plus the three pieces separately, because the pipeline
page wants only the headline numbers and should not pay for the salesperson breakdown.

Every literal segment (`/forecast`, `/dashboard`) sits under `/reports/`, which keeps it
clear of `/projects/{project_id}` -- the shadowing trap `test_route_shadowing` watches for.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.projects._common import acting_company_id
from app.database import get_db
from app.dependencies import require_permission, require_permission_with_api_key
from app.schemas.projects import (
    ConversionResponse,
    ProjectDashboardResponse,
    ProjectForecastResponse,
)
from app.services import project_forecast_service as svc
from app.services.error_handler import handle_internal_error

logger = logging.getLogger(__name__)

router = APIRouter()

VIEW = "projects.projects.view"
# The dashboard is management reporting, not a salesperson's own pipeline, so it reads the
# same permission the forecast module is gated on rather than the per-project view right.
REPORT_VIEW = "projects.reports.view"


@router.get("/reports/forecast", response_model=ProjectForecastResponse)
async def project_forecast(
    # API-key readable (AC-K1): `crm_project_forecast`. Conversion and the composite
    # dashboard stay JWT-only -- no tool asks for them, and an API-key surface should only be
    # as wide as something actually needs.
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """AC-I1. Pipeline, Weighted and Committed, never blended."""
    from fastapi import HTTPException, status

    from app.models.base import get_brand_scope
    from app.services.contact_brand_scope import brand_scope_allows_tool

    if not brand_scope_allows_tool("crm_project_forecast", get_brand_scope(db)):
        # A brand-scoped contact gets the route's own not-granted answer.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Permission required: {VIEW}")
    try:
        return svc.forecast(db, company_id=acting_company_id(db))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/reports/conversion", response_model=ConversionResponse)
async def project_conversion(
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """AC-I5. Rolled from quotation outcomes, so a partial win is not a full win."""
    try:
        return svc.conversion(db, company_id=acting_company_id(db))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/reports/dashboard", response_model=ProjectDashboardResponse)
async def project_dashboard(
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """AC-I4 in one read, because the dashboard shows it on one screen.

    `delivery_lag_months` rides along so the page can SAY which assumption produced the year
    buckets. A forecast whose assumption is invisible is a forecast people argue with.
    """
    try:
        from app.services import sponsorship_link_service as links

        company_id = acting_company_id(db)
        return {
            "forecast": svc.forecast(db, company_id=company_id),
            "conversion": svc.conversion(db, company_id=company_id),
            "loss_reasons": svc.loss_reason_counts(db, company_id=company_id),
            "by_salesperson": svc.by_salesperson(db, company_id=company_id),
            "sponsorship": links.sponsorship_conversion(db, company_id=company_id),
            "delivery_lag_months": svc.delivery_lag_months(db),
        }
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))
