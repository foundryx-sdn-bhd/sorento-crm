"""Master data API routes."""
from fastapi import APIRouter
from app.api.v1.master_data import (
    products,
    brands,
    categories,
    certificates,
    units_of_measure,
    countries,
    products_select,
    product_find,
    product_attachments,
    lookup_sets,
    lookup_eligibility,
    field_linkage,
    ai_extract_field,
    respond_contacts_select,
    spec_registry,
    product_specifications,
    product_sets,
    product_companions,
    product_alternatives,
    product_combos,
    product_compatibility,
    sales_agents,
)

router = APIRouter()

router.include_router(products_select.router, prefix="/products", tags=["products"])
# CHAT-FIND: `/products/find` before `products.router`, whose `/{product_id}` would read it as an id.
router.include_router(product_find.router, prefix="/products", tags=["products"])
router.include_router(product_alternatives.router, prefix="/products", tags=["products"])
router.include_router(products.router, prefix="/products", tags=["products"])
router.include_router(brands.router, prefix="/brands", tags=["brands"])
router.include_router(categories.router, prefix="/product-categories", tags=["product-categories"])
router.include_router(spec_registry.router, prefix="/spec-registry", tags=["spec-registry"])
router.include_router(
    product_specifications.router,
    prefix="/product-specifications",
    tags=["product-specifications"],
)
router.include_router(units_of_measure.router, prefix="/units-of-measure", tags=["units-of-measure"])
router.include_router(countries.router, prefix="/countries", tags=["countries"])
router.include_router(product_attachments.router, prefix="/product-attachments", tags=["product-attachments"])
router.include_router(certificates.router, prefix="/certificates", tags=["certificates"])
router.include_router(product_sets.router, prefix="/product-sets", tags=["product-sets"])
router.include_router(
    product_companions.router,
    prefix="/product-companion-rules",
    tags=["product-companion-rules"],
)
# NO prefix: the combo routes live under three path roots (`/products/{id}/combos`,
# `/product-combos/...`, `/product-combo-parts/...`), so there is nothing common to
# hang one on. Registered here beside product_companions, the #779 precedent this
# slice copies.
router.include_router(product_combos.router, tags=["product-combos"])
# NO prefix either: `/products/{id}/compatibility` and `/product-compatibility/...`.
router.include_router(product_compatibility.router, tags=["product-compatibility"])
router.include_router(sales_agents.router, prefix="/sales-agents", tags=["sales-agents"])
router.include_router(lookup_sets.router, prefix="/lookup-sets", tags=["lookup-sets"])
router.include_router(lookup_eligibility.router, prefix="/lookup-eligibility", tags=["lookup-eligibility"])
router.include_router(
    field_linkage.router,
    prefix="/field-linkage-schema",
    tags=["field-linkage-schema"],
)
router.include_router(
    ai_extract_field.router,
    prefix="/ai-extract-field",
    tags=["ai-extract-field"],
)
router.include_router(
    respond_contacts_select.router,
    prefix="/respond-contacts",
    tags=["respond-contacts"],
)
