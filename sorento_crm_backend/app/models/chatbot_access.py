"""The chatbot access model: roles -> domains -> fields, one registry (lane ACCESS-MODEL).

`chatbot_domain_fields` is the field/report registry, `chatbot_roles` the dynamic role list
(seed rows only, the owner creates and deletes the rest), `contact_chatbot_roles` which roles
a contact holds, and `contact_access_overrides` the per-contact plus/minus rows. Nothing here
grants access by itself: `app.services.chatbot.access_tree.effective_access` is the one reader.

Declared as models so `Base.metadata.create_all` builds them for every blank-schema test
fixture; the migration `acm_0001_access_model` is what SEEDS them on a real database.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class ChatbotDomainField(Base):
    """One tickable field (kind `field`) or report (kind `report`) of a chatbot domain.

    `key` keeps today's reveal-key strings (`inventory.sellable`, `incoming_stock.<field>`...)
    so the enforcement seams that read `ctx.access.attributes` do not move.
    """

    __tablename__ = "chatbot_domain_fields"
    __company_shared__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    domain_name = Column(
        Text,
        ForeignKey("chatbot_domains.name", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
    )
    key = Column(Text, nullable=False, unique=True)
    label = Column(Text, nullable=False)
    kind = Column(Text, nullable=False, server_default="field")
    prompt_tag = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        CheckConstraint("kind IN ('field', 'report')", name="ck_chatbot_domain_fields_kind"),
        Index("ix_chatbot_domain_fields_domain", "domain_name"),
    )


class ChatbotRole(Base):
    """A role: a set of domain and field ticks plus the `sees_all_customers` flag."""

    __tablename__ = "chatbot_roles"
    __audit_track__ = True
    # A migration-seeded reference table: without this its own seeds are invisible to
    # company-scoped users (LESSONS-LEARNT).
    __company_shared__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    #: Slug of the name at create time, immutable.
    code = Column(Text, nullable=False, unique=True)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    sees_all_customers = Column(Boolean, nullable=False, server_default=text("false"))
    #: dealer / office / end_user / NULL. A contact's tiers are the set over its roles.
    audience_tier = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=False), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_by = Column(Text, nullable=True)
    updated_by = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "audience_tier IS NULL OR audience_tier IN ('dealer', 'office', 'end_user')",
            name="ck_chatbot_roles_audience_tier",
        ),
    )


class ChatbotRoleDomain(Base):
    __tablename__ = "chatbot_role_domains"
    __company_shared__ = True

    role_id = Column(
        UUID(as_uuid=False), ForeignKey("chatbot_roles.id", ondelete="CASCADE"), primary_key=True
    )
    domain_name = Column(
        Text,
        ForeignKey("chatbot_domains.name", onupdate="CASCADE", ondelete="CASCADE"),
        primary_key=True,
    )


class ChatbotRoleField(Base):
    __tablename__ = "chatbot_role_fields"
    __company_shared__ = True

    role_id = Column(
        UUID(as_uuid=False), ForeignKey("chatbot_roles.id", ondelete="CASCADE"), primary_key=True
    )
    field_key = Column(
        Text,
        ForeignKey("chatbot_domain_fields.key", onupdate="CASCADE", ondelete="CASCADE"),
        primary_key=True,
    )


class ContactChatbotRole(Base):
    """A contact holds a role. RESTRICT on the role: a role someone holds cannot be deleted."""

    __tablename__ = "contact_chatbot_roles"

    contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="CASCADE"), primary_key=True
    )
    role_id = Column(
        UUID(as_uuid=False), ForeignKey("chatbot_roles.id", ondelete="RESTRICT"), primary_key=True
    )


class ContactAccessOverride(Base):
    """A per-contact plus (`granted`) or minus row, beating every role.

    `field_key` NULL = a domain override; otherwise a field override under `domain_name`.
    """

    __tablename__ = "contact_access_overrides"
    __audit_track__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="CASCADE"), nullable=False
    )
    domain_name = Column(
        Text,
        ForeignKey("chatbot_domains.name", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
    )
    field_key = Column(
        Text,
        ForeignKey("chatbot_domain_fields.key", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
    )
    granted = Column(Boolean, nullable=False)
    created_at = Column(DateTime(timezone=False), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    updated_by = Column(Text, nullable=True)

    # Two partial uniques, not one constraint: NULLs are distinct in Postgres (pattern of
    # `agent_field_access`, models/access.py).
    __table_args__ = (
        Index("ix_contact_access_overrides_contact", "contact_id"),
        Index(
            "uq_contact_access_overrides_domain",
            "contact_id",
            "domain_name",
            unique=True,
            postgresql_where=text("field_key IS NULL"),
        ),
        Index(
            "uq_contact_access_overrides_field",
            "contact_id",
            "field_key",
            unique=True,
            postgresql_where=text("field_key IS NOT NULL"),
        ),
    )
