"""Per-contact delta-sync state for the local-first thread (lane CHAT-LOCAL-FIRST, R2/R4).

One row per (channel, contact_id), the same key ``chat_histories`` scopes a thread by.
It holds only what the message rows cannot say for themselves:

- ``newest_synced_message_id``: the Respond-side watermark the delta read continues
  from. Advanced ONLY by a Respond read (``sync_newer``, or the live page that fills an
  empty thread), never by a row n8n, the webhook or a CRM send wrote: a message one of
  those lanes missed is still behind the watermark, so the next read recovers it, and
  rows they wrote after it are re-read and have their media / sender filled in. NULL =
  never synced; the first read is the newest page with no cursor.
- ``oldest_reached``: the scroll-back walk hit the start of the Respond thread, so no
  older delta read is ever made for this contact again.
- ``last_synced_at``: when the last delta read ran. The thread's 10 s poll schedules
  nothing while this is fresh (``chat_thread_sync_service.SYNC_MIN_INTERVAL_SECONDS``).
- ``last_activity_at``: when the contact last had a message land, from any lane. The
  reconcile tick selects on it, so Respond load follows message activity, not viewers.
- ``last_error`` / ``last_error_at``: the last delta read failure, for diagnosis.

The oldest stored message id is deliberately NOT here: a scroll-back continues from
whatever is oldest in ``chat_histories`` (``chat_thread_sync_service.oldest_stored``).
"""
import uuid

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class ChatThreadSyncState(Base):
    __tablename__ = "chat_thread_sync_state"
    __audit_skip__ = "sync bookkeeping, not a business record"

    # server_default: chat_thread_sync_service inserts with raw SQL and never passes an id.
    id = Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        server_default=text("gen_random_uuid()"),
    )
    channel = Column(String(32), nullable=False)
    contact_id = Column(String(128), nullable=False)
    newest_synced_message_id = Column(String(64), nullable=True)
    oldest_reached = Column(Boolean, nullable=False, server_default=text("false"), default=False)
    last_activity_at = Column(DateTime(timezone=False), nullable=True)
    last_synced_at = Column(DateTime(timezone=False), nullable=True)
    last_error = Column(Text, nullable=True)
    last_error_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("channel", "contact_id", name="uq_chat_thread_sync_state_contact"),
        Index("ix_chat_thread_sync_state_activity", "last_activity_at"),
    )
