"""The `analytics_connector` schema: read-only views over the chat tables (card section 5.2).

One source for the view bodies. The migration `acc_0001_connector` calls `ensure_views`, and
`scripts/bootstrap_env.py` calls it too (a bootstrapped database never runs migration bodies).
A later change to a view means a new migration that calls `ensure_views` again.

Times are Malaysia wall clock. OTP sign-in codes are masked here with the same pattern as
`app.services.otp_redaction.OTP_TEXT_RE`; a test feeds both the same fixtures. No view carries
a phone number, `session_vars`, `portal_slug`, a turn `envelope`/`trace`/`response` blob, or
`chat_histories.result`/`state_trace`.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

SCHEMA = "analytics_connector"
RO_ROLE = "sorento_analytics_ro"

# `{col}` is the expression to mask. The capture group keeps the keyword text, like the Python
# rule. A capturing group instead of `(?:`: SQLAlchemy's text() would read `(?:sign` as a bind.
OTP_MASK_SQL = (
    "regexp_replace({col}, '((sign-in|verification) code is\\s+)\\d{{4,8}}', '\\1******', 'gi')"
)


def _mask(col: str) -> str:
    return OTP_MASK_SQL.format(col=col)


def _myt_naive(col: str) -> str:
    """A naive-UTC `timestamp` column as Malaysia wall clock."""
    return f"(({col}) AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kuala_Lumpur'"


def _myt_tz(col: str) -> str:
    """A `timestamptz` column as Malaysia wall clock."""
    return f"(({col}) AT TIME ZONE 'Asia/Kuala_Lumpur')"


_CONTACT_JOINS = """
LEFT JOIN LATERAL (
    SELECT c.customer_code, c.customer_name
      FROM respond_contact_customers rcc
      JOIN customers c ON c.id = rcc.customer_id
     WHERE rcc.contact_id = rc.id
     ORDER BY rcc.is_primary DESC, rcc.created_at ASC
     LIMIT 1
) cu ON true
LEFT JOIN LATERAL (
    SELECT string_agg(co.name, ', ' ORDER BY co.name) AS companies
      FROM respond_contact_companies rcco
      JOIN companies co ON co.id = rcco.company_id
     WHERE rcco.respond_contact_id = rc.id
) cm ON true
"""

MESSAGES_V = f"""
CREATE OR REPLACE VIEW {SCHEMA}.messages AS
SELECT ch.id,
       {_myt_naive('ch.sent_at')} AS sent_at,
       ch.type AS direction,
       ch.channel,
       rc.id AS contact_id,
       ch.contact_id AS contact_respond_id,
       rc.name AS contact_name,
       cu.customer_code,
       cu.customer_name,
       cm.companies,
       {_mask('ch.message')} AS text,
       {_mask('ch.reply_to_message')} AS reply_to_text,
       ch.chatbot_turn_id,
       ch.delivery_status
  FROM chat_histories ch
  LEFT JOIN LATERAL (
        SELECT r.id, r.name FROM respond_contacts r
         WHERE r.respond_io_id = ch.contact_id ORDER BY r.created_at ASC LIMIT 1
  ) rc ON true
  {_CONTACT_JOINS}
"""

CONTACTS_V = f"""
CREATE OR REPLACE VIEW {SCHEMA}.contacts AS
SELECT rc.id,
       rc.respond_io_id,
       rc.name,
       rc.first_name,
       rc.last_name,
       cu.customer_code,
       cu.customer_name,
       cm.companies,
       {_myt_naive('rc.created_at')} AS created_at,
       rc.chatbot_allowed,
       rc.chatbot_stock_allowed,
       rc.outbound_enabled,
       rc.escalation_allowed,
       rc.chatbot_memory_level,
       rc.chatbot_profile ->> 'tier' AS profile_tier,
       rc.chatbot_profile ->> 'language' AS profile_language,
       {_myt_naive('mc.first_message_at')} AS first_message_at,
       {_myt_naive('mc.last_message_at')} AS last_message_at,
       COALESCE(mc.message_count, 0) AS message_count
  FROM respond_contacts rc
  {_CONTACT_JOINS}
  LEFT JOIN LATERAL (
        SELECT min(h.sent_at) AS first_message_at, max(h.sent_at) AS last_message_at,
               count(*) AS message_count
          FROM chat_histories h WHERE h.contact_id = rc.respond_io_id
  ) mc ON true
"""

BOT_TURNS_V = f"""
CREATE OR REPLACE VIEW {SCHEMA}.bot_turns AS
SELECT t.id::text AS id,
       rc.id AS contact_id,
       t.contact_respond_id,
       rc.name AS contact_name,
       {_myt_tz('t.created_at')} AS created_at,
       {_myt_tz('t.finished_at')} AS finished_at,
       (extract(epoch FROM (t.finished_at - COALESCE(t.started_at, t.created_at))) * 1000)::bigint AS latency_ms,
       t.status,
       t.stage,
       t.branch_kind,
       t.error,
       t.attempt,
       {_mask("COALESCE(t.envelope #>> '{message,message,message,text}', t.envelope #>> '{message,message,message,attachment,description}')")} AS inbound_text,
       t.response #>> '{{reply,text}}' AS reply_text
  FROM chatbot.turns t
  LEFT JOIN LATERAL (
        SELECT r.id, r.name FROM respond_contacts r
         WHERE r.respond_io_id = t.contact_respond_id ORDER BY r.created_at ASC LIMIT 1
  ) rc ON true
 WHERE NOT t.is_test
"""

EPISODES_V = f"""
CREATE OR REPLACE VIEW {SCHEMA}.episodes AS
SELECT cf.id::text AS id,
       rc.id AS contact_id,
       rc.name AS contact_name,
       cf.domain,
       cf.intent,
       cf.status,
       cf.close_reason,
       {_myt_naive('cf.started_at')} AS started_at,
       {_myt_naive('cf.last_activity_at')} AS last_activity_at,
       {_myt_naive('cf.closed_at')} AS closed_at,
       COALESCE(cardinality(cf.turn_ids), 0) AS turn_count,
       array_to_string(cf.tools_used, ', ') AS tools_used,
       {_mask('cf.summary')} AS summary,
       {_mask('cf.last_user_message')} AS last_user_message,
       {_mask('cf.last_assistant_summary')} AS last_assistant_summary
  FROM conversation_frames cf
  LEFT JOIN respond_contacts rc ON rc.id = cf.contact_respond_id
 WHERE NOT cf.is_test
"""

VIEWS = (MESSAGES_V, CONTACTS_V, BOT_TURNS_V, EPISODES_V)

_ROLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ensure_views(conn: Any) -> None:
    """Create the schema and (re)create the four views. ``conn`` is a Session or a Connection."""
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    for ddl in VIEWS:
        conn.execute(text(ddl))


def grant_read_role(conn: Any, role: str = RO_ROLE) -> None:
    """Give ``role`` SELECT on the views and nothing else (card section 5.1)."""
    if not _ROLE_RE.match(role):
        raise ValueError(f"unsafe role name: {role!r}")
    conn.execute(text(f'GRANT USAGE ON SCHEMA {SCHEMA} TO "{role}"'))
    conn.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO "{role}"'))
    conn.execute(text(f"ALTER ROLE \"{role}\" SET statement_timeout = '10s'"))
    conn.execute(text(f'ALTER ROLE "{role}" SET default_transaction_read_only = on'))
