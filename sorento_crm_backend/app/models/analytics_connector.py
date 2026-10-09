"""OAuth state for the claude.ai analytics connector (CLAUDE-CONNECTOR, card section 3).

Three small tables: the dynamically registered clients, the pending authorize requests (they
double as the single-use code store), and the grants that hold the rotating refresh token.
Codes and refresh tokens are kept as SHA-256 hashes only. The access token is an ordinary
``user_sessions`` row (``auth_method='claude_connector'``), referenced by ``current_session_id``.

String primary keys on purpose: ``client_id`` and the request id arrive from the open internet,
and a non-uuid value must read as "not found", never as a cast error.
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


class AnalyticsOAuthClient(Base):
    __tablename__ = "analytics_oauth_clients"
    __audit_skip__ = "oauth client registry; lifecycle events are written by the connector service"

    id = Column(String(64), primary_key=True, default=_new_id)  # the public client_id
    client_name = Column(String(200), nullable=True)
    redirect_uris = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)


class AnalyticsOAuthRequest(Base):
    __tablename__ = "analytics_oauth_requests"
    __audit_skip__ = "short-lived authorize request and code store"

    id = Column(String(64), primary_key=True, default=_new_id)
    client_id = Column(String(64), ForeignKey("analytics_oauth_clients.id", ondelete="CASCADE"), nullable=False)
    redirect_uri = Column(Text, nullable=False)
    code_challenge = Column(String(128), nullable=False)
    state = Column(Text, nullable=True)
    scope = Column(String(100), nullable=False)
    resource = Column(Text, nullable=False)
    # pending -> approved | denied -> used
    status = Column(String(12), nullable=False, default="pending")
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    code_hash = Column(String(64), nullable=True, index=True)
    code_expires_at = Column(DateTime(timezone=False), nullable=True)
    expires_at = Column(DateTime(timezone=False), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)


class AnalyticsConnectorGrant(Base):
    __tablename__ = "analytics_connector_grants"
    __audit_skip__ = "oauth grant; lifecycle events are written by the connector service"

    id = Column(String(64), primary_key=True, default=_new_id)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(String(64), ForeignKey("analytics_oauth_clients.id", ondelete="CASCADE"), nullable=False)
    refresh_token_hash = Column(String(64), nullable=False, index=True)
    previous_refresh_hash = Column(String(64), nullable=True, index=True)
    # The grant's live access token. Not a foreign key: user_sessions.id is a varchar column.
    current_session_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    last_used_at = Column(DateTime(timezone=False), nullable=True)
    revoked_at = Column(DateTime(timezone=False), nullable=True)
    expires_at = Column(DateTime(timezone=False), nullable=False)

    __table_args__ = (Index("ix_analytics_connector_grants_user_id", "user_id"),)
