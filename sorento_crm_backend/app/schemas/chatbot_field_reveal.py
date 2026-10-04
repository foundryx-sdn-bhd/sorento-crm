"""Response/request shapes for the chatbot field-reveal admin API (Slice C)."""
from __future__ import annotations

from pydantic import BaseModel


class FieldRevealKey(BaseModel):
    key: str
    label: str
    # Prompt blocks this key also removes from the parser prompt (`PROMPT_GATES`).
    prompt_blocks: list[str] = []


class FieldRevealKeysResponse(BaseModel):
    items: list[FieldRevealKey]


class ContactFieldRevealsResponse(BaseModel):
    granted: list[str]


class ContactFieldRevealsUpdate(BaseModel):
    granted: list[str]
