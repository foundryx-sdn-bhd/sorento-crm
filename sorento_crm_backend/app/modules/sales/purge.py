"""Delete every row the `sales` module owns, when it is uninstalled with purge.

Discovered by ``app.modules.runtime.discovery.discover_module_purge_handlers`` because it is
``app/modules/sales/purge.py`` and exposes ``purge(db)``; same shape and rules as
``app/modules/projects/purge.py``: module-owned tables only (everything declared in
``app/models/sales.py``, all in the ``sales`` schema), children first, core rows never touched
(a purge never deletes a ``public.sales_agents`` row), and never ``DROP SCHEMA``.
``tests/test_sales_module_purge_invariants.py`` pins the list against the models and against
``sorento_crm_frontend/modules/sales/purge_tables.json``. Edit all three together.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Type

from sqlalchemy.orm import Session

from app.models.sales import (
    SalesOpportunity,
    SalesOpportunityLine,
    SalesPointRatio,
    SalesTarget,
    SalesTargetAccount,
    SalesTargetCommissionTier,
    SalesTargetPeriod,
    SalesTargetScope,
    SalesTargetTierRate,
    SalesTeam,
    SalesTeamMember,
)

logger = logging.getLogger(__name__)

#: Every module-owned table, children first.
PURGE_ORDER: List[Type] = [
    SalesOpportunityLine,
    SalesOpportunity,
    SalesTargetTierRate,
    SalesTargetCommissionTier,
    SalesTargetAccount,
    SalesTargetScope,
    SalesTargetPeriod,
    SalesTarget,
    SalesPointRatio,
    SalesTeamMember,
    SalesTeam,
]


def purge(db: Session) -> Dict[str, int]:
    """Empty every module-owned table. Returns ``{schema.table: rows_deleted}``; the caller commits."""
    counts: Dict[str, int] = {}
    for model in PURGE_ORDER:
        label = model.__table__.fullname
        counts[label] = db.query(model).delete(synchronize_session=False)
        logger.info("Purge %s: deleted %s rows", label, counts[label])
    return counts
