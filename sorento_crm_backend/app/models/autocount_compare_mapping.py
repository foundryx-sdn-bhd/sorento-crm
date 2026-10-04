"""How the AutoCount pull's "Compare with my Excel" tab reads a checker's workbook
(PLAN-do-compare-mapping.md). One row per workbook kind: the sheet to read and the column
mapping (`[{excel_header, transform, field}]`) saved as one JSON set, since the rows are only
ever read and written together. A global table: no company_id, no `CompanyScopedMixin`."""
from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class AutocountCompareMapping(Base):
    __tablename__ = "autocount_compare_mappings"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind = Column(String(40), nullable=False, unique=True)
    sheet_name = Column(String(100), nullable=False)
    columns = Column(JSONB, nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
