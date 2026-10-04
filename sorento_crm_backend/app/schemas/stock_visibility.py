"""Admin API shapes for the stock visibility policy.

One body for every tier (`{effective, override}`), so the same card serves the
contact page, the access-type admin and the settings default without three
response shapes to keep in step. The full contract, request and response, is
documented at the top of `sorento_crm_frontend/services/stockVisibilityService.ts`.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class StockVisibilityWarehouse(BaseModel):
    """A warehouse RESOLVED, not a bare id: the card renders `CODE - name`, and a
    UUID must never reach the UI."""

    id: str
    code: str
    name: Optional[str] = None
    company_name: Optional[str] = None


class StockVisibilityPolicyOut(BaseModel):
    mode: Literal["detailed", "compact", "availability"]
    #: null = every active warehouse; [] = none at all.
    warehouses: Optional[List[StockVisibilityWarehouse]] = None
    #: `warehouses`'s sibling: null = no exclusion, [] = every active warehouse
    #: (the opposite of what [] means on `warehouses`), a list = every active
    #: warehouse except these. The two are never both non-empty-list on one
    #: tier's own row, but a merged access-type policy can carry both.
    excluded_warehouses: Optional[List[StockVisibilityWarehouse]] = None
    #: Withhold the locations holding none of the product. See the model docstring:
    #: `detailed` drops the row, `compact` the location line, `availability` is
    #: unaffected, and a negative quantity is never hidden.
    hide_zero_locations: bool = False
    source: Literal["contact", "access_type", "default"]
    #: The access type's NAME when `source` is `access_type`, so the badge can
    #: read "Access type: Dealer". A name, never the code.
    source_label: Optional[str] = None


class StockVisibilityPolicyResponse(BaseModel):
    effective: StockVisibilityPolicyOut
    #: The row stored AT the requested tier. null = this tier inherits.
    override: Optional[StockVisibilityPolicyOut] = None


class StockVisibilityInput(BaseModel):
    """Upsert body. `warehouse_ids` is REPLACED wholesale, never merged - merging
    would make removing a location impossible from the card."""

    mode: Literal["detailed", "compact", "availability"]
    #: REQUIRED, and nullable rather than defaulted: a PUT replaces the whole row,
    #: so a body that simply omitted the key used to widen the policy to every
    #: location - the one edit an admin can make without meaning to.
    #: `max_length` bounds the list itself, not one id - a caller cannot pad the
    #: body past what any real warehouse master could ever need.
    warehouse_ids: Optional[List[str]] = Field(
        ..., description="null = every active warehouse; [] = none.", max_length=500
    )
    #: REQUIRED and nullable, same reasoning as `warehouse_ids`: a PUT replaces
    #: the whole row, so an omitted key must not silently widen a stored
    #: exclusion by accident. Never both non-null with `warehouse_ids` on the
    #: same body - 422 "Pick locations to include or to exclude, not both."
    excluded_warehouse_ids: Optional[List[str]] = Field(
        ...,
        description=(
            "null = no exclusion; [] = every active warehouse; a list = every "
            "active warehouse except these."
        ),
        max_length=500,
    )
    #: Defaulted rather than required, unlike `warehouse_ids`: omitting THAT one
    #: widened a policy to every location, while omitting this one only ever
    #: shows more than was hidden, so the literal reading is also the safe one.
    #: The card sends it on every Save.
    hide_zero_locations: bool = Field(
        False,
        description="Hide the locations holding none of the product. Negatives stay visible.",
    )
