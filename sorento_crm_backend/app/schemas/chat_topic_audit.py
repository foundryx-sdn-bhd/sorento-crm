"""Response and request shapes for the topic audit (chat analytics CA-2).

Every field the screen reads is declared: `response_model` silently drops one that is not.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


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


class AuditResponse(BaseModel):
    version: int
    versions: list[int]
    sample_size: int
    reviewed: int
    rates: AuditRates
    targets: AuditTargets
    rows: list[AuditRow]


class DrawRequest(BaseModel):
    version: int


class DrawResponse(BaseModel):
    drawn: int


class VerdictRequest(BaseModel):
    verdict: Literal["right", "wrong_topic", "wrong_subject"]
    corrected_topic: Optional[str] = None
    products_verdict: Optional[Literal["right", "wrong"]] = None
