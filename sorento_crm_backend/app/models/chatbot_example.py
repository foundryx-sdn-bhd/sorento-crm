"""`chatbot_examples`: worked parser examples retrieved by nearest embedding (PROMPT-DIET).

Rows with a null embedding are skipped by retrieval until `scripts/prompt_diet_seed_examples.py
--embed` fills them. Mirrors migration `pdiet_0001_examples`.
"""
from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Column, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.database import Base


class ChatbotExample(Base):
    __tablename__ = "chatbot_examples"
    __audit_skip__ = "retrieval examples for the parser prompt, loaded by script"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    area = Column(Text, nullable=False)
    message = Column(Text, nullable=False)
    context = Column(Text, nullable=True)
    expected_output = Column(JSONB, nullable=False)
    embedding = Column(Vector(1536), nullable=True)
    source = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=False), nullable=False, server_default=func.now())
