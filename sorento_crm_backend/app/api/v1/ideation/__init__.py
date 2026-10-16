"""Ideas gateway: CRM routes over the ss embed API (IDEATION-IN-CRM)."""
from fastapi import APIRouter

from app.api.v1.ideation import ideas, portal

router = APIRouter()
router.include_router(ideas.router)
router.include_router(portal.router, prefix="/portal")
