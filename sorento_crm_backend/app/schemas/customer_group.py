"""Customer group schemas."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class CustomerGroupWrite(BaseModel):
    """Create / rename body. Blank is refused by the service (422), after trimming."""

    name: str


class CustomerGroupResponse(BaseModel):
    id: str
    name: str
    ledger_count: int = 0
    # Sorted distinct `customers.account_level` of the members; none when no member is numbered.
    account_levels: List[int] = []
    # The one person behind the group's ledger agents; None when none is assigned or they differ.
    sales_agent_label: Optional[str] = None
    sales_agent_mixed: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CustomerGroupSelect(BaseModel):
    id: str
    name: str
    ledger_count: int = 0


class CustomerGroupCustomersAssign(BaseModel):
    # Capped so one request cannot lock thousands of rows or build a huge IN list.
    customer_ids: List[str] = Field(max_length=500)
