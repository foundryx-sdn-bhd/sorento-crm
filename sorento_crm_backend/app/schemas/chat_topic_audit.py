"""Response and request shapes for the topic audit (chat analytics CA-2).

Every field the screen reads is declared: `response_model` silently drops one that is not.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, model_validator


class AuditRates(BaseModel):
    subject: Optional[float] = None
    topic: Optional[float] = None
    product: Optional[float] = None


class AuditTargets(BaseModel):
    subject: float
    topic: float
    product: float


class AuditRow(BaseModel):
    id: str
    asked_at: datetime
    message: str
    subject: str
    subject_label: str
    topic: str
    topic_label: str
    confidence: Optional[float] = None
    products: list[str]
    verdict: Optional[str] = None
    corrected_topic: Optional[str] = None
    corrected_topic_label: Optional[str] = None
    products_verdict: Optional[str] = None
    reviewed_by_name: Optional[str] = None


class AuditTopic(BaseModel):
    """One active taxonomy row, for the corrected-topic pickers (a leaf has a parent)."""

    code: str
    label: str
    parent_code: Optional[str] = None


class AuditResponse(BaseModel):
    version: int
    versions: list[int]
    sample_size: int
    reviewed: int
    rates: AuditRates
    targets: AuditTargets
    topics: list[AuditTopic]
    rows: list[AuditRow]


class DrawRequest(BaseModel):
    version: int


class DrawResponse(BaseModel):
    drawn: int


class VerdictRequest(BaseModel):
    """A verdict on the topic, on the products, or both (a row's products can be judged
    before its topic is)."""

    verdict: Optional[Literal["right", "wrong_topic", "wrong_subject"]] = None
    corrected_topic: Optional[str] = None
    products_verdict: Optional[Literal["right", "wrong"]] = None

    @model_validator(mode="after")
    def _says_something(self) -> "VerdictRequest":
        if self.verdict is None and self.products_verdict is None:
            raise ValueError("Give a verdict, a products verdict, or both.")
        return self
