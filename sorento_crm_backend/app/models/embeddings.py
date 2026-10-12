"""Embedding pipeline models for pgvector-backed RAG retrieval."""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from app.database import Base
import uuid


class EmbeddingQueue(Base):
    __tablename__ = "embedding_queue"
    __audit_skip__ = "embedding work queue"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_type = Column(String(64), nullable=False)
    source_id = Column(String(128), nullable=False)
    event_type = Column(String(100), nullable=False)
    event_version = Column(Integer, nullable=False, default=1)
    source_updated_at = Column(DateTime(timezone=False), nullable=True)
    source_hash = Column(String(64), nullable=True)
    payload = Column(JSON, nullable=False, server_default="{}")
    status = Column(String(32), nullable=False, default="pending")
    retry_count = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime(timezone=False), nullable=False, server_default=func.now())
    last_error = Column(Text, nullable=True)
    correlation_id = Column(UUID(as_uuid=False), nullable=True)
    rq_job_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=False), nullable=False, server_default=func.now())
    processed_at = Column(DateTime(timezone=False), nullable=True)

    __table_args__ = (
        Index("ix_embedding_queue_status_available_at", "status", "available_at"),
        Index("ix_embedding_queue_source", "source_type", "source_id"),
        Index("ix_embedding_queue_correlation_id", "correlation_id"),
    )


class EmbeddingDocument(Base):
    __tablename__ = "embedding_documents"
    __audit_skip__ = "derived embeddings, rebuilt from the source rows"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_type = Column(String(64), nullable=False)
    source_id = Column(String(128), nullable=False)
    source_key = Column(String(128), nullable=True)
    title = Column(String(255), nullable=True)
    body_text = Column(Text, nullable=False)
    metadata_json = Column("metadata", JSON, nullable=False, server_default="{}")
    visibility_scope = Column(String(64), nullable=True)
    source_hash = Column(String(64), nullable=False)
    source_updated_at = Column(DateTime(timezone=False), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    # Company of the source entity (multi-company isolation). Nullable: shared /
    # company-less knowledge (mcp_tool, form, schema_doc) stays NULL and is visible
    # under every scope. A conversation_frame carries its contact's company, but a
    # company-scoped search never returns a frame (one contact's private conversation). NOT a CompanyScopedMixin - the vector
    # search filters on this column manually (the pipeline processes all companies).
    company_id = Column(UUID(as_uuid=False), nullable=True, index=True)
    created_at = Column(DateTime(timezone=False), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    chunks = relationship("EmbeddingChunk", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_embedding_documents_source", "source_type", "source_id"),
        Index("ix_embedding_documents_source_hash", "source_type", "source_id", "source_hash"),
        Index("uq_embedding_documents_source", "source_type", "source_id", unique=True),
    )


class EmbeddingChunk(Base):
    __tablename__ = "embedding_chunks"
    __audit_skip__ = "derived embeddings, rebuilt from the source rows"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(
        UUID(as_uuid=False),
        ForeignKey("embedding_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type = Column(String(64), nullable=False)
    source_id = Column(String(128), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    chunk_hash = Column(String(64), nullable=False)
    embedding = Column(Vector(1536), nullable=False)
    model_name = Column(String(128), nullable=False)
    model_version = Column(String(64), nullable=False)
    embedding_provider = Column(String(64), nullable=False)
    source_hash = Column(String(64), nullable=False)
    metadata_json = Column("metadata", JSON, nullable=False, server_default="{}")
    # Company of the source entity (multi-company isolation) - mirrors the parent
    # document; the vector search filters on this column. See EmbeddingDocument.
    company_id = Column(UUID(as_uuid=False), nullable=True, index=True)
    is_current = Column(Boolean, nullable=False, default=True)
    embedded_at = Column(DateTime(timezone=False), nullable=False, server_default=func.now())
    superseded_at = Column(DateTime(timezone=False), nullable=True)

    document = relationship("EmbeddingDocument", back_populates="chunks")

    __table_args__ = (
        Index("ix_embedding_chunks_source_current", "source_type", "source_id", "is_current"),
        Index("ix_embedding_chunks_model_current", "model_name", "model_version", "is_current"),
        Index(
            "uq_embedding_chunks_current",
            "source_type",
            "source_id",
            "model_name",
            "model_version",
            "chunk_index",
            unique=True,
            postgresql_where=(is_current.is_(True)),
        ),
    )
