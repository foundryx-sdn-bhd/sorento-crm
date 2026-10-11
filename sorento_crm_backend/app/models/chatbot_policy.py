"""The chatbot's policy tables: `chatbot_domains` and `chatbot_entity_kinds` (AC-1501,
AC-1502).

Declared as models so `Base.metadata.create_all` builds them for every blank-schema test
fixture - the migration (`chatbot_rearch_s0`) is what SEEDS them on a real database, and
`app.services.chatbot.turn.policy.load_policy` falls back to
`turn/policy_rows.py`'s rows when the table is present but empty, which is exactly the
create_all case. Both columns sets mirror that migration exactly.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.sql import func
from sqlalchemy import DateTime

from app.database import Base


class ChatbotDomain(Base):
    """One routing domain: its intents, its tools, its narrowing policy, its team.

    Audited (``__audit_track__``) like ``stock_visibility_policies``, for the same
    reason: one row decides what every future turn does - which tool is called, whether
    the bot asks before answering, where an escalation lands - so "who changed this, and
    from what" has to be answerable.
    """

    __tablename__ = "chatbot_domains"
    __audit_track__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(Text, nullable=False, unique=True)
    label = Column(Text, nullable=False)
    intents = Column(ARRAY(Text), nullable=False, server_default="{}")
    tools = Column(ARRAY(Text), nullable=False, server_default="{}")
    primary_tool = Column(Text, nullable=True)
    escalation_team_code = Column(Text, nullable=True)
    switch_words = Column(ARRAY(Text), nullable=False, server_default="{}")
    narrowing = Column(JSONB, nullable=False, server_default="{}")
    takes_date_filter = Column(Boolean, nullable=False, server_default="false")
    reveal_key = Column(Text, nullable=True)
    supported = Column(Boolean, nullable=False, server_default="true")
    ladder = Column(ARRAY(Text), nullable=False, server_default="{}")
    sort_order = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=False), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatbotEntityKind(Base):
    """One entity kind: what resolves it, and how it narrows by default.

    Audited for the same reason as ``ChatbotDomain``.
    """

    __tablename__ = "chatbot_entity_kinds"
    __audit_track__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind = Column(Text, nullable=False, unique=True)
    label = Column(Text, nullable=False)
    resolver_source = Column(Text, nullable=False)
    did_you_mean = Column(Boolean, nullable=False, server_default="true")
    default_narrowing = Column(Text, nullable=False)
    family_grouping = Column(Text, nullable=True)
    base_property_words = Column(JSONB, nullable=False, server_default="{}")
    # Owner ruling 20 Sep 2026 (PLAN-chatbot-answer-half-reattach.md "Roster cap"):
    # "configurable, actually I prefer 10" - the ceiling on any roster this kind is
    # asked in (the gate's customer/product picker arms, the did-you-mean list).
    # PICKER-NO-CAP (owner, 2 Oct 2026): starts at the S3 ceiling, 50, so no picker is
    # visibly cut (migration `picker_no_cap_0001`).
    roster_cap = Column(Integer, nullable=False, server_default="50")
    sort_order = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=False), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatbotStatusWord(Base):
    """One status value a domain's asks can carry, and the customer words that set it
    (PLAN-prompt-dynamic-30sep D6).

    The parser prompt's status bullets and its `status` / `order_status` value list are
    rendered from these rows at request time (`app/services/chatbot_prompt_vars.py`), so a
    word added here reaches the next turn without a prompt publish. Audited for the same
    reason as ``ChatbotDomain``: one row changes what every future turn is told.
    """

    __tablename__ = "chatbot_status_words"
    __audit_track__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # `chatbot_domains.name`, not its id: the prompt, the parser output and every lane
    # speak the name, and a renamed domain is a new domain everywhere else too.
    domain = Column(Text, nullable=False)
    value = Column(Text, nullable=False, unique=True)
    label = Column(Text, nullable=False)
    trigger_words = Column(ARRAY(Text), nullable=False, server_default="{}")
    sort_order = Column(Integer, nullable=False, server_default="0")
    # The parser prompt lists this row is in (owner answer 4, 2 Oct 2026; migration
    # `pdyn_0004_prompt_lists`): `{{statuses}}`, `{{status_values}}` and
    # `{{status_field_values}}` each render only the rows tagged with their own name.
    prompt_lists = Column(ARRAY(Text), nullable=False, server_default="{}")
    created_at = Column(DateTime(timezone=False), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatbotDomainWord(Base):
    """One word of the parser prompt's DOMAIN IN MESSAGE list (owner answer 2, 2 Oct 2026;
    migration `pdyn_0004_prompt_lists`). A curated list, in `sort_order`: it is the owner's
    own choice of words, not the union of the domains' switch words."""

    __tablename__ = "chatbot_domain_words"
    __audit_track__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    word = Column(Text, nullable=False, unique=True)
    sort_order = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=False), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatbotClarifyPolicy(Base):
    """One question kind the chatbot can ask, and what it does instead (CLARIFY-POLICY S2,
    AC-110..AC-116, owner rule R1 11 Oct 2026): `ask`, `default`, `collapse`, or for the
    carried subject `ask`, `carry`, `drop`. Seeded by migration `clp_0001_clarify_policy` from
    `clarify_policy.CLARIFY_POLICY_ROWS`; `clarify_policy.load_clarify_policy` falls back to that
    list when the table is empty (the create_all case). `mode` must be one of the row's own
    `allowed_modes` (CHECK). Not company scoped: one policy for the chatbot."""

    __tablename__ = "chatbot_clarify_policy"
    __audit_track__ = True
    __table_args__ = (
        CheckConstraint("mode = ANY(allowed_modes)", name="ck_chatbot_clarify_policy_mode_allowed"),
    )

    ask_kind = Column(Text, primary_key=True)
    label = Column(Text, nullable=False)
    mode = Column(Text, nullable=False)
    allowed_modes = Column(ARRAY(Text), nullable=False)
    default_note = Column(Text, nullable=False, server_default="")
    repeat_limit = Column(Integer, nullable=False, server_default="2")
    updated_at = Column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    updated_by = Column(Text, nullable=True)
