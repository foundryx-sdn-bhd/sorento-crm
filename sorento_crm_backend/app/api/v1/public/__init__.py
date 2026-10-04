"""Public (unauthenticated) API routes."""
from fastapi import APIRouter
from app.api.v1.public import (
    ai_extract,
    approval,
    branding,
    catalogue,
    dealer_kit_fonts,
    geo,
    onboarding,
    portal,
    portal_conversations,
    portal_customer_asks,
    portal_ideas,
    portal_price_tag,
    portal_sales_opportunity,
    print as print_route,
    quotation_sign,
    supplier_request,
    ticket_drafts,
    view,
)

router = APIRouter()
router.include_router(approval.router, prefix="/approval", tags=["public-approval"])
router.include_router(view.router, prefix="/view", tags=["public-view"])
# BEFORE portal.router, and that ordering is the fix, not a preference (D49).
# Both are mounted at /portal and Starlette serves the FIRST route whose path
# matches, and portal.py declares POST/PUT/GET/DELETE `/submissions/{kind}...`.
# Mounted the other way round, every price tag write is answered by the generic
# handler: it used to be a 422 about `body.fields` - a key belonging to another
# form's schema, which is what the salesperson saw when Submit "did nothing" -
# and now that `price_tag_request` is out of SUPPORTED_TYPES it would be a flat
# 400. This router declares only literal price tag paths, so going first
# captures exactly the requests meant for it and leaves the legacy kinds alone.
router.include_router(
    portal_price_tag.router, prefix="/portal", tags=["public-portal-price-tag"]
)
# Customer idea track page (IDEATION-IN-CRM): literal `/portal/ideas/...` paths, mounted before
# the generic portal router for the same reason.
router.include_router(portal_ideas.router, prefix="/portal", tags=["public-portal-ideas"])
# Chatbot stock ask v2 S6: literal `/customer-asks` paths only, mounted before the
# generic portal router for the same reason as the price tag router above.
router.include_router(
    portal_customer_asks.router, prefix="/portal", tags=["public-portal-customer-asks"]
)
# SALES-CONVO: literal `/conversations` paths only, mounted before the generic portal router
# for the same reason.
router.include_router(
    portal_conversations.router, prefix="/portal", tags=["public-portal-conversations"]
)
# Same reasoning as portal_price_tag above (D49): declares only literal
# `/sales-opportunities...` paths, mounted before the generic handler so it captures
# exactly the requests meant for it.
router.include_router(
    portal_sales_opportunity.router, prefix="/portal", tags=["public-portal-sales-opportunity"]
)
router.include_router(portal.router, prefix="/portal", tags=["public-portal"])
router.include_router(
    quotation_sign.router, prefix="/quotation-sign", tags=["public-quotation-sign"]
)
# Onboarding intake, gated by the per-request token: /api/v1/public/onboarding/*
router.include_router(
    onboarding.router, prefix="/onboarding", tags=["public-onboarding"]
)
router.include_router(ai_extract.router, prefix="/portal", tags=["public-portal-ai-extract"])
router.include_router(
    ticket_drafts.router, prefix="/ticket-drafts", tags=["public-ticket-drafts"]
)
# The signature pad asks this while a customer is still signing, so it has to be reachable
# without a session exactly like the counter-sign page above it.
router.include_router(geo.router, prefix="/geo", tags=["public-geo"])
# Published catalogue pages: /api/v1/public/c/{company_code}/{slug}
router.include_router(catalogue.router, prefix="/c", tags=["public-catalogue"])
# The supplier's own view of a container request: /api/v1/public/supplier-request/{token}
router.include_router(
    supplier_request.router, prefix="/supplier-request", tags=["public-supplier-request"]
)
# The sign-in page's background, asked for before anyone has a session:
# /api/v1/public/branding
router.include_router(branding.router, prefix="/branding", tags=["public-branding"])
# Render payload for the PDF worker: /api/v1/public/print/{download_id}?token=
router.include_router(print_route.router, prefix="/print", tags=["public-print"])
# Font bytes, same-origin so FontFace.load() has no CORS to satisfy:
# /api/v1/public/dealer-kit/fonts/{asset_id}
router.include_router(
    dealer_kit_fonts.router, prefix="/dealer-kit/fonts", tags=["public-dealer-kit-fonts"]
)
