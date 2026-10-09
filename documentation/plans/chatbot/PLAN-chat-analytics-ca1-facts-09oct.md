# PLAN: Chat analytics CA-1, facts from traces + Chat overview

Status: Build (behaviour card posted on PR #1546, building on its recommendations). Track: full
(migration + new dataset + new permission).

Design (binding): `documentation/plans/chatbot/DESIGN-chat-analytics-09oct.md` (PR #1545, head
25d09f0e), section 8 lane CA-1. UAC: `chat-analytics-ca1-facts-acceptance-criteria.md`.

## Scope

Facts from live traces only (5 Sep on). No LLM, no topics (CA-2), no backfill of pre-trace
history (CA-4), no bot dataset (CA-3). The owner sees one new report, "Chat overview", on the
existing Reports screen.

## Contract (what the tests pin)

### Tables (schema `chatbot`, migration `chat_facts_0001_tables`, additive only)

`chatbot.chat_ask_facts`
- `id` uuid pk; `source` text not null (`trace`); `turn_id` uuid not null; `ask_seq` smallint
  not null default 1; unique (`turn_id`, `ask_seq`).
- `contact_respond_id` text not null; `contact_id` text null; `customer_id` uuid null;
  `company_id` text null; `audience` text not null (`dealer` / `staff` / `unknown`); `contact_type` text not null
  (`dealer` / `office` / `not_allowed` / `unknown`, snapshot, rules under "Edge rules").
- `asked_at` timestamptz not null; `channel` text not null default `whatsapp`.
- `domain` text null; `intent` text null; `outcome` text not null; `failure_reason` text null;
  `branch_kind` text null; `tools` text[] not null default `{}`; `rows_offered` int null;
  `reply_ms` int null; `is_media` bool not null default false; `phantom_dropped` bool not null
  default false.
- `episode_id` uuid null.
- `topic_code` text null; `taxonomy_version` smallint null; `label_model` text null;
  `label_prompt_version` text null; `labelled_at` timestamptz null; `label_confidence` real null.
- `created_at` timestamptz default now().
- Indexes: `asked_at`, `contact_respond_id`, `outcome`.

`chatbot.chat_ask_items`
- `id` uuid pk; `ask_id` uuid fk -> chat_ask_facts.id on delete cascade; `kind` text
  (`product` / `brand`); `ref_id` text (the CRM id); `role` text (`named` / `shown`);
  `resolved_by` text (`parser` / `reply`). Unique (`ask_id`, `kind`, `ref_id`, `role`).
  Index `ref_id`.

`chatbot.chat_episode_facts`
- `frame_id` uuid pk (the `conversation_frames.id`, no FK: the frame is trimmed, this row stays);
  `contact_respond_id` text; `opened_at`, `closed_at` timestamptz; `domain`, `intent` text null;
  `turn_count` int; `ask_count` int (non-noise turns); `failed_turns` int; `last_outcome` text
  null; `handover` bool; `close_reason` text null; `resolved` bool null; `asked_again` bool null
  (both null until CA-2).

No column holds customer words, an episode summary or `last_user_message`.

### Code

- `app/models/chat_facts.py`: `ChatAskFact`, `ChatAskItem`, `ChatEpisodeFact` (schema `chatbot`,
  `__audit_skip__`).
- `app/services/chatbot/analytics/outcome.py`: pure, no DB.
  - `turn_outcome(turn: dict) -> tuple[str, str | None]` (outcome, failure_reason). `turn` is the
    `memory._turn_to_digest_dict` shape plus `status`. Rules: section "Outcome rules" below.
  - `FAILED_OUTCOMES = ("technical", "cannot_do", "missed")`.
  - `flatten(turn: dict) -> dict`: the fact fields derivable from the trace alone (domain,
    intent, outcome, failure_reason, branch_kind, tools, rows_offered, phantom_dropped,
    named_codes {product: [...], brand: [...]}, shown_codes [...]).
- `app/services/chatbot/analytics/facts.py`: DB side.
  - `record_turn(db, turn_id) -> bool`: writes the fact row (+ items) for one closed live turn;
    `ON CONFLICT DO NOTHING`; returns False for a test / console / not-closed / unknown turn.
    Commits its own work.
  - `sweep(db, *, since: datetime, until: datetime | None = None) -> int`: every closed live turn
    in range with no fact row; returns rows written.
  - `snapshot_episode(db, frame_id, turn_rows) -> None`: writes `chat_episode_facts` and sets
    `chat_ask_facts.episode_id` for the frame's turns; does NOT commit (the caller's
    transaction).
- Hook 1: `engine._close_turn` (`engine.py:1503`) calls `facts.record_turn` after its commit for
  `done`/`failed`, inside try/except (a failure logs and never fails the turn).
- Hook 2: `memory.write_episode_for_reset` calls `facts.snapshot_episode` after the frame insert
  and BEFORE `_trim_to_newest`, same transaction, wrapped in a SAVEPOINT so an analytics failure
  never loses the frame.
- Sweep: `scripts/chat_facts_sweep.py --since 2026-09-05` (one-time load, idempotent) and a
  nightly scheduler job over the last 3 days. `--rebuild` reloads a date range after a rule change.
- Report: `app/services/reports/datasets/chat_asks.py` (ask grain) and `chat_ask_items.py`
  (item grain), `app/services/reports/definitions/chat_overview.py` (key `chat_overview`,
  title "Chat overview", module `chatbot`, permission `chatbot.analytics.view`, scope `none`
  justified in the docstring, `datasets_by` on the `count_by` param). Count by Products switches
  to one row per ask and product; choosing Product as the summary row and Named / Shown as the
  measures shows each product by its code. The default layout stays outcome by week (one
  default view per report).
- Permission `chatbot.analytics.view` in `app/rbac/permission_registry.py` and seeded by the
  migration onto every role holding `system.chat_history.view`.

### Outcome rules (first match wins)

1. `technical` (failed): turn `status = failed`.
2. `noise`: `branch_kind = low_signal`.
3. `refused_policy`: plan lane `escalation_barred`.
4. `handover` / `off_topic`: `branch_kind = out_of_scope`. The lane always hands the thread to
   staff, so the split is on structured fields only. `handover` when the customer asked for a
   person (`verdict.escalation.is_escalation_confirmation`, `verdict.message_type = escalation`,
   `answer_pending_accept` in the apply event's `rules_fired`, or `escalation.named_teams`) or
   the ask names a business (`verdict.domain_hint`, `plan.domains` or `verdict.intent_hint`).
   Otherwise `off_topic`: counted as an ask, not noise, not failed.
5. `refused_policy`: branch `access_denied` / `ai_not_allowed` / `stock_denied`; or a
   `domain_grant` event with `skipped = not_granted`; or `plan.denied` non-empty with no
   `plan.fetch`.
6. `cannot_do` (failed): branch `not_supported`.
7. `clarified`: branch `clarify_menu` / `demand_qty` / `escalate_offer` / `offer_hold`; or
   `plan.ask` set; or a `required_ask` event.
8. `missed` (failed): `looked_up.facts.missed` non-empty; or a `crossdomain` event with
   `answered` null.
9. `answered_empty`: a `looked_up` stage, nothing missed, `facts.sections == 0`.
10. `answered`: everything else.

`phantom_answer` events set `phantom_dropped`, not an outcome (behaviour card Q2).

### Edge rules
- Test turns (`is_test`) and `ingress = console` never become fact rows.
- Retries: one fact per respond `message_id`, the latest `attempt` (an older attempt's row is
  deleted when a later attempt is recorded).
- Items: a named entity becomes an item only when its `canonical_code` equals a product code
  (`products.code`) or brand code exactly (case-insensitive equality); shown rows from
  `replied.raw.reply.result_set[].code` the same way. No match, no row.
- Contact type, first match: `office` when a user is linked; `dealer` when the contact has any
  `respond_contact_customers` link; `not_allowed` when `respond_contacts.chatbot_allowed` is
  false; `office` otherwise; `unknown` when there is no `respond_contacts` row. The report
  filters on it (default: dealer, not allowed, unknown) and shows it as a dimension; audience
  stays as a dimension only.
- Audience: `staff` when a user is linked (`users.respond_contact_id`), else `dealer` when the
  contact has a customer link, else `unknown`.

## Open (owner)
Behaviour card questions Q1-Q5 on PR #1546; this plan follows the recommendations until
answered.
