"""CONTACT-TAGS schemas: the tag catalog, per-contact tagging and the bulk tag preview/apply."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_BULK_CONTACTS = 500


class ContactTagRef(BaseModel):
    id: str
    name: str


def _clean_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError("Tag name is required.")
    return value


class ContactTagCreate(BaseModel):
    name: Annotated[str, Field(max_length=100)]
    description: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0
    settings: Optional[dict] = None

    _trim = field_validator("name")(_clean_name)


class ContactTagUpdate(BaseModel):
    name: Optional[Annotated[str, Field(max_length=100)]] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    settings: Optional[dict] = None

    _trim = field_validator("name")(_clean_name)


class ContactTagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: Optional[str] = None
    is_active: bool
    sort_order: int
    settings: dict = {}
    contact_count: int = 0
    differs_count: int = 0
    created_at: datetime
    updated_at: datetime


class ContactTagsSet(BaseModel):
    tag_ids: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=1000)


class ContactTagsSetResponse(BaseModel):
    tags: list[ContactTagRef]


class BulkTagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_ids: list[Annotated[str, Field(max_length=64)]] = Field(..., min_length=1, max_length=2000)
    add_tag_ids: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=1000)
    remove_tag_ids: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=1000)
    dry_run: bool = True

    @field_validator("contact_ids")
    @classmethod
    def _at_most_500(cls, ids: list[str]) -> list[str]:
        if len(dict.fromkeys(ids)) > MAX_BULK_CONTACTS:
            raise ValueError(f"At most {MAX_BULK_CONTACTS} contacts per request.")
        return ids

    @model_validator(mode="after")
    def _disjoint(self):
        if set(self.add_tag_ids) & set(self.remove_tag_ids):
            raise ValueError("A tag cannot be both added and removed.")
        return self


class BulkTagRow(BaseModel):
    contact_id: str
    label: str
    status: Literal["changed", "unchanged", "failed"]
    tags_before: list[str]
    tags_after: list[str]
    error: Optional[str] = None


class BulkTagCounts(BaseModel):
    changed: int = 0
    unchanged: int = 0
    failed: int = 0


class BulkTagResult(BaseModel):
    dry_run: bool
    results: list[BulkTagRow]
    counts: BulkTagCounts


class TagSettingsConflict(BaseModel):
    name: str
    value: object = None


class TagSettingsItem(BaseModel):
    section: str
    key: str
    label: str
    current: object = None
    expected: object = None
    decided_by: list[str] = []
    conflict: bool = False
    conflict_tags: list[TagSettingsConflict] = []
    differs: bool = False


class ContactTagSettingsResponse(BaseModel):
    status: Literal["matches", "differs", "none"]
    tags: list[ContactTagRef]
    items: list[TagSettingsItem]
