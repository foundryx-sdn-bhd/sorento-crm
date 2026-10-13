"""The `analytics_connector` schema: read-only views over the chat tables (card section 5.2).

One source for the view bodies. The migration `acc_0001_connector` calls `ensure_views`, and
`scripts/bootstrap_env.py` calls it too (a bootstrapped database never runs migration bodies).
A later change to a view means a new migration that calls `ensure_views` again.

Times are Malaysia wall clock. OTP sign-in codes are masked here with the same pattern as
`app.services.otp_redaction.OTP_TEXT_RE`; a test feeds both the same fixtures. No view carries
a phone number, `session_vars`, `portal_slug`, a turn `envelope`/`trace`/`response` blob, or
`chat_histories.result`/`state_trace`.

The two cost views (`llm_calls`, `turn_costs`) are created by ``ensure_cost_views``.
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
       COALESCE(mc.message_count, 0) AS message_count,
       rc.chatbot_profile -> 'facts' AS profile_facts
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
       {_mask('t.error')} AS error,
       t.attempt,
       {_mask("COALESCE(t.envelope #>> '{message,message,message,text}', t.envelope #>> '{message,message,message,attachment,description}')")} AS inbound_text,
       {_mask("t.response #>> '{reply,text}'")} AS reply_text
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

# CHAT-TURN-COST (plan documentation/plans/analytics/PLAN-chat-turn-cost-13oct.md). One row per
# CHATBOT `ai_assistant_usage_logs` row, priced from `llm_model_prices`. No `user_id`, no
# conversation id. Chatbot features only: the CRM shows staff-side AI spend (assistant, extraction,
# drafts) only to `system.ai_assistant_settings.view`, which the connector's chat gate is not.
# Anthropic reports cache reads OUTSIDE `prompt_tokens` (`llm_provider.py`), OpenAI and Gemini
# inside it, so the uncached part is computed per provider. A model with no price row is NULL
# (unpriced), never 0. A missing cached price bills cached tokens at the input price. Anthropic
# cache WRITES (`cache_creation_input_tokens`, 1.25x input) are not captured: nothing in `app/`
# sets `cache_control` today, so they are zero; capture them when prompt caching is turned on.
_CACHED = "COALESCE(u.cached_tokens, 0)"
LLM_CALLS_V = f"""
CREATE OR REPLACE VIEW {SCHEMA}.llm_calls AS
SELECT u.id::text AS id,
       {_myt_naive('u.created_at')} AS created_at,
       u.feature,
       u.chatbot_turn_id,
       u.contact_id,
       rc.name AS contact_name,
       u.provider,
       u.model,
       u.prompt_tokens,
       {_CACHED} AS cached_tokens,
       u.completion_tokens,
       u.total_tokens,
       u.response_time_ms,
       u.was_answered,
       CASE WHEN p.model IS NULL THEN NULL ELSE round((
              (CASE WHEN lower(btrim(u.provider)) = 'anthropic' THEN u.prompt_tokens
                    ELSE GREATEST(u.prompt_tokens - {_CACHED}, 0) END) * p.input_usd_per_1m
            + {_CACHED} * COALESCE(p.cached_input_usd_per_1m, p.input_usd_per_1m)
            + u.completion_tokens * p.output_usd_per_1m
       ) / 1000000, 6) END AS cost_usd,
       (p.model IS NOT NULL) AS priced
  FROM ai_assistant_usage_logs u
  LEFT JOIN respond_contacts rc ON rc.id = u.contact_id
  LEFT JOIN llm_model_prices p ON p.model = u.model
 WHERE u.feature LIKE 'chatbot%'
"""

# One row per live turn. The calls are summed per turn in a LATERAL over the
# `chatbot_turn_id` index: measured on 100k synthetic turns, a newest-first page took 71 ms this
# way against 2.9 s as a grouped hash join, and a group-by over every turn 1.2 s against 1.4 s
# (plan, "Performance"). Category, outcome, topic,
# episode and prompt version come from the turn's CA-1 fact row (`chatbot.chat_ask_facts`, one per
# closed live turn, `ask_seq` 1), never from the trace: a trace is tens of KB per turn and reading
# it per row would not fit the connector's 10 s statement timeout. A turn with no fact yet (still
# open, superseded by a retry, or not swept) has those columns empty.
TURN_COSTS_V = f"""
CREATE OR REPLACE VIEW {SCHEMA}.turn_costs AS
SELECT t.id::text AS id,
       {_myt_tz('t.created_at')} AS created_at,
       rc.id AS contact_id,
       t.contact_respond_id,
       rc.name AS contact_name,
       cu.customer_name,
       f.episode_id::text AS episode_id,
       {_mask("COALESCE(t.envelope #>> '{message,message,message,text}', t.envelope #>> '{message,message,message,attachment,description}')")} AS inbound_text,
       {_mask("t.response #>> '{reply,text}'")} AS reply_text,
       t.status,
       t.stage,
       t.branch_kind,
       f.domain,
       f.intent,
       f.topic_code,
       tp.label AS topic_label,
       f.outcome,
       f.prompt_version,
       calls.model,
       COALESCE(calls.llm_calls, 0) AS llm_calls,
       COALESCE(calls.prompt_tokens, 0) AS prompt_tokens,
       COALESCE(calls.cached_tokens, 0) AS cached_tokens,
       COALESCE(calls.completion_tokens, 0) AS completion_tokens,
       COALESCE(calls.total_tokens, 0) AS total_tokens,
       COALESCE(calls.llm_ms, 0) AS llm_ms,
       (extract(epoch FROM (t.finished_at - COALESCE(t.started_at, t.created_at))) * 1000)::bigint AS latency_ms,
       CASE WHEN calls.chatbot_turn_id IS NULL THEN 0::numeric
            WHEN calls.priced THEN calls.cost_usd END AS cost_usd,
       COALESCE(calls.priced, true) AS priced
  FROM chatbot.turns t
  LEFT JOIN LATERAL (
        SELECT r.id, r.name FROM respond_contacts r
         WHERE r.respond_io_id = t.contact_respond_id ORDER BY r.created_at ASC LIMIT 1
  ) rc ON true
  LEFT JOIN chatbot.chat_ask_facts f ON f.turn_id = t.id AND f.ask_seq = 1
  LEFT JOIN customers cu ON cu.id = f.customer_id
  LEFT JOIN chatbot.chat_topics tp ON tp.code = f.topic_code
  LEFT JOIN LATERAL (
        SELECT lc.chatbot_turn_id,
               count(*) AS llm_calls,
               string_agg(DISTINCT lc.model, ', ' ORDER BY lc.model) AS model,
               sum(lc.prompt_tokens) AS prompt_tokens,
               sum(lc.cached_tokens) AS cached_tokens,
               sum(lc.completion_tokens) AS completion_tokens,
               sum(lc.total_tokens) AS total_tokens,
               sum(lc.response_time_ms) AS llm_ms,
               sum(lc.cost_usd) AS cost_usd,
               bool_and(lc.priced) AS priced
          FROM {SCHEMA}.llm_calls lc
         WHERE lc.chatbot_turn_id = t.id::text
         GROUP BY lc.chatbot_turn_id
  ) calls ON true
 WHERE NOT t.is_test
"""

COST_VIEWS = (LLM_CALLS_V, TURN_COSTS_V)

_ROLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ensure_views(conn: Any) -> None:
    """Create the schema and (re)create the four views. ``conn`` is a Session or a Connection."""
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    for ddl in VIEWS:
        conn.execute(text(ddl))


def ensure_cost_views(conn: Any) -> None:
    """(Re)create the two cost views (CHAT-TURN-COST). Separate from ``ensure_views`` because
    they need `llm_model_prices`, `ai_assistant_usage_logs.cached_tokens` and
    `chat_ask_facts.prompt_version`, which exist only
    from migration `ctc_0001_turn_costs` on, and `acc_0001_connector` still calls ``ensure_views``."""
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    for ddl in COST_VIEWS:
        conn.execute(text(ddl))


def grant_read_role(conn: Any, role: str = RO_ROLE) -> None:
    """Give ``role`` SELECT on the views and nothing else (card section 5.1)."""
    if not _ROLE_RE.match(role):
        raise ValueError(f"unsafe role name: {role!r}")
    conn.execute(text(f'GRANT USAGE ON SCHEMA {SCHEMA} TO "{role}"'))
    conn.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO "{role}"'))
    conn.execute(text(f"ALTER ROLE \"{role}\" SET statement_timeout = '10s'"))
    conn.execute(text(f'ALTER ROLE "{role}" SET default_transaction_read_only = on'))
