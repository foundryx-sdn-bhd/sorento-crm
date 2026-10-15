"""A frozen Stock Debt board (STOCK-DEBT-SNAPSHOT).

`StockDebtSnapshot` is the header: the filters it was taken under, the month axis, the
totals. `StockDebtSnapshotRow` is one product: the `StockDebtRow` as served, its drill lines
(every bucket, with the order inquiry rows linked at that moment) and the decision someone
took on it. Both are written once and never recomputed; only the decision fields change.

Plan: documentation/plans/scm/PLAN-stock-debt-snapshot-15oct.md.
"""
import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.database import Base
from app.models.base import CompanyScopedMixin


def _uuid_str() -> str:
    return str(uuid.uuid4())


class StockDebtSnapshot(Base, CompanyScopedMixin):
    __tablename__ = "stock_debt_snapshots"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    name = Column(String(120), nullable=False)
    # The StockDebtExportIn fields except `split`, plus the human labels (AC-6).
    filters = Column(JSONB, nullable=False)
    months = Column(JSONB, nullable=False)
    tba_month = Column(String(10), nullable=True)
    totals = Column(JSONB, nullable=False)
    row_count = Column(Integer, nullable=False, default=0, server_default="0")
    created_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_stock_debt_snapshots_company_created", "company_id", created_at.desc()),
    )


class StockDebtSnapshotRow(Base, CompanyScopedMixin):
    __tablename__ = "stock_debt_snapshot_rows"
    __audit_skip__ = (
        "bulk insert of up to 5,000 rows per save; the decision fields carry their own who/when"
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    snapshot_id = Column(
        UUID(as_uuid=False),
        ForeignKey("stock_debt_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    position = Column(Integer, nullable=False)
    product_id = Column(
        UUID(as_uuid=False), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    product_code = Column(String(180), nullable=False)
    product_name = Column(Text, nullable=True)
    # The StockDebtRow as served. Mapped to the column named "row".
    row_data = Column("row", JSONB, nullable=False)
    # AC-4 / AC-5: every demand line of every bucket, each with its `oi_rows`.
    lines = Column(JSONB, nullable=False)
    decision = Column(String(16), nullable=True)
    decision_warehouse_id = Column(
        UUID(as_uuid=False), ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True
    )
    decision_note = Column(Text, nullable=True)
    decided_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at = Column(DateTime(timezone=False), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "decision IS NULL OR decision IN ('reserve', 'buy', 'ignore')",
            name="ck_stock_debt_snapshot_rows_decision",
        ),
        Index("ix_stock_debt_snapshot_rows_snapshot_position", "snapshot_id", "position"),
        Index("ix_stock_debt_snapshot_rows_snapshot_decision", "snapshot_id", "decision"),
    )
