# Monitoring enhancement - test report

Keyed to `monitoring-enhancement-acceptance-criteria.md`. Updated per slice.

| Slice | Status |
|-------|--------|
| **S2 - scheduled task overdue** | **PASS** (2026-07-20) |
| **S4 - WhatsApp latency SLA (CRM side)** | **PASS** (2026-07-20). n8n half handed off - see `n8n-contract-handoff.md`. Inert until it lands. |
| **S5 - chat history admin UI** | **PASS** (2026-07-20) |
| S1 - health dashboard | not started |
| S3 - api_call_log | not started |
| S6 - timezone | not started |

---

## S2 - scheduled task overdue

Branch `worktree-observability-monitoring`. Backend `pytest`, frontend `vitest`,
browser verification via Playwright MCP against a dev server on `:3002`
(the user's own `:3000` was left untouched - it serves the main checkout).

### Results

| Id | Verdict | Evidence |
|----|---------|----------|
| OBS-S2-01 | PASS | `test_due_at_is_last_run_plus_interval`, `test_due_at_ignores_next_run_at_entirely` - the second poisons `next_run_at` with a value a year out and asserts `due_at` does not move. |
| OBS-S2-02 | PASS | `test_never_run_with_past_start_at_is_due_at_start_at`, `test_never_run_without_start_at_falls_back_to_created_at`, `test_never_run_task_is_reported_overdue`. |
| OBS-S2-03 | PASS | `test_future_start_at_not_overdue`. |
| OBS-S2-04 | PASS | `test_grace_is_percentage_clamped_60s_to_30min` (4 params covering both clamp ends), plus the boundary pair: `== due_at + grace` not overdue, `+1s` overdue. |
| OBS-S2-05 | PASS | `test_per_task_grace_percent_overrides_global`, `test_task_without_override_uses_global`, `test_invalid_per_task_grace_falls_back_to_global`. |
| OBS-S2-06 | PASS | `test_health_and_watchdog_report_identical_overdue_sets` drives both surfaces off one helper. Both inline queries deleted, not patched. |
| OBS-S2-07 | PASS | `test_disabled_task_never_overdue`. |
| OBS-S2-08 | PASS | `test_scheduled_tasks_overdue_detail_is_itemized` asserts key, interval phrasing, MYT last-run, lateness, and deep link. Rendered body in the simulation output below. |
| OBS-S2-09 | PASS | `test_late_by_measured_from_due_at` - 33m since last run on a 10m interval reports `23m`, i.e. measured from `due_at`, not from `due_at + grace`. |
| OBS-S2-10 | PASS | Pre-existing `health_alert_state` de-dup tests pass unchanged (fire once / suppress in cooldown / re-fire after / recovery notice). |
| OBS-S2-11 | PASS | Live prod-copy DB: old rules flagged `takeover_request_commit`, new rule reports zero. See below. |
| OBS-S2-12 | PASS | "Grace period (%)" renders with the effective value as placeholder and the computed grace inline ("Currently 2m 30s"); blank = use global. |
| OBS-S2-13 | PASS | Playwright via sidebar → set 50 → save → reload: field shows 50, server-computed grace updates 2m 30s → 5m, DB shows `{"grace_percent": 50}`. Zero console errors. |
| OBS-S2-14 | PASS | Zod rejects non-numeric/NaN with a message and blocks submit; `mapFormToUpdateBody.test.ts` covers the value mapping including `0` as a real value. |

### Live-data validation (OBS-S2-11)

Against the local prod-copy DB, 12 enabled tasks:

```
OLD card rule    : 1 overdue -> ['takeover_request_commit']
OLD watchdog rule: 1 overdue -> ['takeover_request_commit']
NEW shared rule  : 0 overdue -> []
```

`takeover_request_commit` runs every 15 seconds and was being reported overdue while
sub-second late. That is the standing false-alarm mechanism, reproduced and removed.

### State simulation (`scripts/simulate_overdue_states.py`)

Drives one task through every lateness state inside a rolled-back transaction:

```
task      : system_health_watchdog        interval: every 10 minutes
grace     : 150s  (25% of interval, clamped)

scenario                              ran ago   card   alert  agree
healthy - ran just now                  0.0m      0      no   ok
late, inside grace                     11.2m      0      no   ok
exactly at due + grace (boundary)      12.5m      0      no   ok
overdue - past grace                   14.0m      1     YES   ok
badly overdue                         200.0m      1     YES   ok
```

Resulting alert body:

```
Scheduled tasks - overdue (1):
  • system_health_watchdog (System health watchdog) - every 10 minutes,
    last run 20/07/2026 10:26 MYT, 3h 10m late - 
    http://localhost:3000/system-management/scheduled-tasks/2ccb4ede-…
```

### Suite results

- `pytest tests/test_scheduled_task_overdue.py tests/test_system_health_watchdog.py tests/test_health_summary.py` → **50 passed**.
- `vitest .../scheduled-tasks/lib/mapFormToUpdateBody.test.ts` → **6 passed**.
- `tsc --noEmit` → no errors in touched files (4 pre-existing errors remain in `products/*` tests).
- `eslint` on the touched directory → clean (1 pre-existing error remains in `RunLogsTable.tsx`).

### Defects found and fixed during the slice

1. **Clearing the grace override silently did nothing.** The FE sent a metadata object
   with the key omitted, but `update_task` *merges* metadata and removes a key only on an
   explicit `null`. Caught by browser verification, not by the unit tests - the DB still
   read `{"grace_percent": 50}` after a save that appeared to succeed. Fixed by sending the
   `null` sentinel, and pinned by `mapFormToUpdateBody.test.ts`.
2. **Four watchdog tests were already failing on the base branch**, masked by a stale test
   double: `notify_spy` did not accept the `dedup_id` kwarg. Fixing that exposed a second
   pre-existing gap - the digest test seeded no users, so `sent` (which reflects resolved
   recipients) was always `False`. Both fixed; these tests cover the code this slice edits.

### Migration

`289_scheduled_task_grace_percent` adds `system_settings.health_task_grace_percent`
(default 25), guarded by an `information_schema` existence check so it is idempotent.
Applied to the local DB; `alembic current` → single head. Per-task overrides need no
migration - they reuse the existing `scheduled_tasks.metadata` JSONB.

---

## S4 - WhatsApp round-trip latency SLA (CRM side)

The n8n half (OBS-S4-01..04) is specified in `n8n-contract-handoff.md` and implemented by
the n8n session. Everything below is the CRM side and ships independently: all new ingest
fields are optional, so today's n8n payloads keep validating. Until n8n lands, the metric
simply has no data - nothing breaks and no alert fires on an empty window.

### Results

| Id | Verdict | Evidence |
|----|---------|----------|
| OBS-S4-05 | PASS | `test_pairs_incoming_to_outgoing_by_turn_id` - pairing is by `turn_id`, never proximity. |
| OBS-S4-06 | PASS | `test_pairing_is_immune_to_bursts` - 3 rapid turns with mixed latencies resolve to 30s/2s/3s; proximity pairing would mis-assign all three. |
| OBS-S4-07 | PASS | `test_proactive_send_is_excluded_not_paired` - a campaign send between an incoming and its reply neither steals the pairing nor invents a turn. |
| OBS-S4-08 | PASS | `test_multi_part_reply_measures_the_first_outgoing` - a 3-part answer is judged on when it starts (perceived responsiveness). |
| OBS-S4-09 | PASS | `test_sent_at_is_never_used_as_the_clock` - feeds a row whose outgoing `sent_at` *precedes* its own incoming, as observed in production, and asserts latency comes out 4.0s rather than −15s or 10s. |
| OBS-S4-10 | PASS | `test_unresolved_respond_ts_is_not_measured` - rows awaiting resolution are omitted, never approximated from `sent_at`. |
| OBS-S4-11 | PASS | `test_p99_tolerates_exactly_one_percent` + `test_p99_reflects_a_slow_tail_beyond_one_percent`. |
| OBS-S4-12 | PASS | `test_min_sample_size_prevents_alerting_on_one_turn`, `test_stats_empty_window_is_not_a_breach`. |
| OBS-S4-13 | PASS | `test_stalled_turn_detected_regardless_of_sample_size` - hard ceiling has no sample floor. |
| OBS-S4-14 | PASS | `test_incoming_with_no_reply_is_reported_separately`, `test_unanswered_within_threshold_is_not_flagged`. |
| OBS-S4-15 | PASS | `test_undelivered_message_does_not_affect_latency` - an offline recipient cannot breach the SLA. |
| OBS-S4-16 | PASS | `test_undelivered_counted_separately`, `test_recently_sent_not_yet_delivered_is_not_counted`. |
| OBS-S4-17 | PASS | `test_webhook_lag_is_measurable_and_separate_from_latency` - 45s webhook lag reported alongside, not inside, the 50s latency. |
| OBS-S4-18 | PASS | `test_webhook_lag_null_when_ingest_at_missing`. |
| OBS-S4-19 | PASS | `test_resolves_respond_timestamp_and_status`, `test_passes_contact_identifier_not_just_message_id` (the endpoint needs contact **and** message id). |
| OBS-S4-20 | PASS | `test_not_found_increments_attempts`, `test_not_found_at_max_attempts_marks_not_sent` - "not found = not sent", but only after transient failure is ruled out. |
| OBS-S4-21 | PASS | `test_transient_error_increments_without_concluding` - a 503 must never be recorded as `not_sent`. |
| OBS-S4-22 | PASS | `test_one_bad_row_does_not_abort_the_batch`, `test_respects_limit`, `test_exhausted_rows_are_not_retried`. |
| OBS-S4-23 | PASS | `test_seconds_epoch_is_handled_as_well_as_milliseconds` - treating ms as seconds yields year-58xxx dates. |
| OBS-S4-24 | PASS | `test_missing_timestamp_in_payload_is_not_resolved` - never substitute `now()` for a clock the payload didn't carry. |
| OBS-S4-25 | PASS | Ingest verified over real HTTP: `turn_id` stored, `ingest_at` server-stamped, `resolve_attempts` defaulted. A legacy payload with neither `turn_id` nor `message_id` still returns 201. |

### Scenario simulation (`scripts/simulate_chat_latency.py`)

Synthesizes turns into `chat_histories` inside a rolled-back transaction and evaluates
them through the same path the scheduled task uses:

```
p99 target 10s | ceiling 30s | min sample 30 | no-reply 5m

healthy - 40 turns @ 3s                turns=40  p99=3.0s   ok
degraded - 40 turns @ 25s              turns=40  p99=25.0s  ALERT  p99 exceeds target
one stalled turn among 40 healthy      turns=41  p99=90.0s  ALERT  + ceiling, worst 90s
incoming with no reply (12m)           turns=40  p99=3.0s   ALERT  no reply, longest 12m
webhook arrived 40s late               turns=1   p99=44.0s  ALERT  ceiling; lag max 40s
proactive sends only (no turns)        turns=0   p99= -      ok
```

The fourth row is the important one: it alerts while p99 reads a healthy 3.0s. A turn that
never completes never enters the distribution, so a p99-only alert would stay green
through exactly the dropped-webhook outage this slice exists to catch.

### Suite results

`pytest tests/test_chat_latency.py tests/test_chat_message_resolver.py` → **34 passed**.

### Defects found during the slice

1. **Two of my own test expectations were wrong**, and the code was right. `p99` over 99
   fast + 1 slow turns is 1.0s, not 45s - a 99th percentile tolerates exactly 1% by
   definition, and I had conflated it with `max`. Separately, a 20-turn breach test sat
   below the default `min_sample` of 30, so staying quiet was correct behaviour. Both
   tests rewritten; the percentile case is now pinned by a pair of tests that state the
   distinction explicitly.
2. **`run_chat_latency_watchdog` called a `_mark_ok` helper that did not exist.** The
   recovery branch would have raised `NameError` the first time the condition cleared - 
   in production, after an incident, which is the worst possible moment. Found by reading
   the module rather than by a test: the service tests exercised `_eval_chat_latency` but
   never the entry point's fire/recover state machine. Added `_mark_ok` (symmetric with
   the existing `_mark_alerting`, replacing an inline duplicate in the original watchdog)
   and two tests that drive fire -> recover end to end.
3. **sqlite cannot autoincrement a `BigInteger` primary key**, so every fixture insert
   failed `NOT NULL constraint failed: chat_histories.id`. Added a DDL-only shim
   (BigInteger PK → Integer for sqlite) in the same style as the existing JSONB/ARRAY
   shim in `tests/conftest.py`. Postgres uses a sequence and is unaffected.

### Migrations

- `290_chat_history_latency_columns` - `respond_ts`, `delivery_status`, `delivered_ts`,
  `read_ts`, `resolve_attempts`, `turn_id`, `ingest_at`, plus two partial indexes (the
  resolver's unresolved-rows query, and turn pairing). All nullable with **no backfill**:
  existing rows genuinely have no Respond timestamp and no turn, and inventing one would
  fabricate latency data (OBS-X-02).
- `291_chat_latency_settings_and_tasks` - four `system_settings` thresholds (p99 target
  10s, ceiling ×3, no-reply 5min, min sample 30) and seeds the `chat_message_resolver`
  and `chat_latency_watchdog` tasks at 60s, `ON CONFLICT (key) DO NOTHING`.

Applied to the local DB; `alembic current` → single head `291`.

### Not yet done for S4

- No FE surface yet - the p99 tile and breach list land with S1 (dashboard) and S5 (chat
  page), per the plan's sequencing.
- The resolver has not been exercised against the live Respond API; its behaviour is
  pinned by a fake client. First real run happens once n8n starts populating `message_id`.


---

## S5 - chat history admin UI

### Results

| Id | Verdict | Evidence |
|----|---------|----------|
| OBS-S5-01 | PASS | `test_date_range_filters_on_sent_at`, `test_default_window_is_last_24h` - an unbounded scan of this table is never the intent. |
| OBS-S5-02 | PASS | `test_contact_filter`, `test_direction_filter`. |
| OBS-S5-03 | PASS | `test_search_matches_message_text`, `test_search_matches_phone` (also name). |
| OBS-S5-04 | PASS | `test_newest_first`. |
| OBS-S5-05 | PASS | `test_keyset_pagination_walks_without_gaps_or_repeats` - 10 rows over 5 pages, no repeats, no gaps. |
| OBS-S5-06 | PASS | `test_ties_on_sent_at_are_broken_by_id` - five rows sharing one timestamp still paginate deterministically. |
| OBS-S5-07 | PASS | `test_cursor_none_when_exhausted`. |
| OBS-S5-08 | PASS | `test_thread_returns_messages_around_an_anchor`, `test_thread_is_scoped_to_one_contact`, `test_thread_ordered_oldest_first`. |
| OBS-S5-09 | PASS | `test_outgoing_rows_carry_turn_latency` - latency sits on the reply, not the trigger. |
| OBS-S5-10 | PASS | `test_breached_only_returns_both_sides_of_the_breaching_turn`, `test_breached_only_ignores_unresolved_rows`. |
| OBS-S5-11 | PASS | `test_display_name_prefers_stored_name`, `test_display_name_falls_back_to_phone_not_respond_id` - the Respond id is never rendered. |
| OBS-S5-12 | PASS | Browser-verified via the sidebar on a production build: nav entry renders, grid loads 50 real rows with resolved contact names, filters apply, empty state shows for an empty range, thread drawer opens scoped to the contact. Zero console errors. |

### Suite results

`pytest tests/test_chat_history_query.py tests/test_chat_latency.py tests/test_chat_message_resolver.py`
→ **54 passed**. `tsc --noEmit` and `eslint` clean on the new files.

### Deviations from the plan, and why

1. **`chat_histories` was NOT registered in the `list_query` registry**, as the plan
   proposed. That registry's export path returns the entire filtered set as JSON for the
   browser to convert to XLSX - precisely the shape that falls over on this table. Export
   goes through My Downloads instead (as agreed during the grill), and the grid uses a
   dedicated endpoint because keyset paging does not fit list_query's page/limit contract.
2. **"Breached only" returns both messages of a breaching turn**, not just the slow reply.
   A lone outgoing row shows a slow answer with no visible question, which is useless for
   triage.
3. **Permission slugs are registered but granted to zero roles.**
   `check_user_has_permission` short-circuits for superadmin/admin, which is how
   `system.respond_outbox.view` operates at zero grants today. For a page rendering raw
   customer messages, admin-only is the correct default. An earlier draft auto-granted to
   peer roles; that was a silent no-op, so it was removed rather than left in place
   pretending to work.

### Defect found during browser verification

The thread drawer opened scrolled to the oldest message. Because bot replies in this data
run to hundreds of lines, the message the user actually clicked was several screens below
the fold - the drawer claimed to show the transcript "around the selected message" while
showing something else entirely. Fixed by scrolling the anchor into view on open.

### Known limitation (not a defect in this slice)

The thread orders by `sent_at`, which n8n currently fills with its own clock. On today's
data a reply can therefore sort *before* the question it answers:

```
09:08  > CAN I SUBMIT SPONSORSHIP FORM
09:11  < I have attached the file(s) below...   <- reply
09:11  > i want to submit complain              <- the question it answers
```

This is the S4 root cause rendering faithfully, and it resolves itself once n8n sends the
raw Respond timestamp. Deliberately not papered over with a `COALESCE(respond_ts, sent_at)`
ordering, which would hide the underlying data problem while it still exists.

### Migration

`292_chat_history_admin_perms_and_purge` - two PII-scoped permission slugs,
`system_settings.downloads_retention_days` (default 30), and seeds the
`user_downloads_purge` daily task. That purge closes a real pre-existing gap: nothing has
ever deleted `user_downloads` rows or their stored objects, so `complaint_pdf` artifacts
have been accumulating since that feature shipped. Applied locally; single head `292`.

---

## S1 - health dashboard: honest counts, then actionable ones

### Results

| UAC | Verdict | Evidence |
|---|---|---|
| OBS-S1-01 | PASS | `date_from`/`date_to` on `GET /system/health/summary`; window threaded into the email, imports and integrations builders. |
| OBS-S1-02..04 | PASS | Four-bucket classification (success / failed / benign / in flight) in `integration_outcome.py`; the buckets sum to the channel total, so `n8n_crm_chat_outbound` no longer renders 0/0 of 13. |
| OBS-S1-05 | PASS | Writer fixed at 3 sites in `sla_tracking.py`. Root cause: `handle_validation_error` returns `AppException`, **not** `HTTPException`, so a deliberate 4xx refusal fell through the `except Exception` arm and was logged `status="failed"`. |
| OBS-S1-06/07 | PASS | `_BENIGN_SIGNATURES` is a per-channel allowlist. Live 30d: `sla_management` 46 raw failures → 0 failed / 52 benign, while `respond_io`'s 1029 real 401/403s stayed failures. |
| OBS-S1-08 | PASS | Email Queue leads with `Failed in window`, all-time demoted to a hint - the 63 that read as a live incident was an all-time total. |
| OBS-S1-09/10 | PASS | Drill-through link + explicit empty states. |
| OBS-S1-11/12 | PASS | `integration_failure_signature.py` - uuids, digit runs and ISO timestamps masked; `status_code` participates in the key so 401 and 403 stay separate. |
| OBS-S1-13 | PASS | Only `OUTCOME_FAILED` rows feed the signature list; benign/in-flight rows are excluded so the classification is not undone at render time. |
| OBS-S1-14 | PASS | httpx `"For more information check: <mdn url>"` suffix trimmed, with the original kept if trimming would blank the row. |
| OBS-S1-15 | PASS | Top 3 causes render inline under the channel row with count, status code and un-masked message. |
| OBS-S1-16 | PASS | Drill-down carries the selected range; verified end-to-end below. |

### Suite results

- `pytest tests/test_integration_failure_signature.py` - 15 passed
- `pytest tests/test_integration_outcome_classification.py` - 13 passed
- `pytest tests/test_health_summary.py` - 3 passed
- `npx vitest run "app/(protected)/system-management"` - 95 passed (20 files)

### Live-data validation (OBS-S1-11..16)

Against the local prod-copy DB over a 30-day window, `respond_io` shows 821 failures.
Those 821 collapse to **three** distinct causes:

| Count | Code | Cause |
|---|---|---|
| 428 | 401 | `Client error '401 Unauthorized' for url '…/contact/id:55555/message'` |
| 330 | 403 | `Client error '403 Forbidden' for url '…/contact/id:900000008/message'` |
| 18 | - | `24h window closed and template send skipped for use case 'sla_daily_summary': configured template was removed on sync` |

The digit masking is doing the work here: the contact id varies per row, so without it these
would render as ~758 unique one-off errors and the pattern would be invisible.

### Browser verification (prod build, `:3002`, reached via the sidebar)

1. Sidebar → System Management → System Health → click **30d**.
2. The `respond_io` row renders the three causes inline. Console: 0 errors, 0 warnings.
3. The Failed link resolves to
   `…/integration-logs?integration_channel=respond_io&status=failed&created_from=2026-06-20T09:05:00.000Z&created_to=2026-07-20T09:05:00.000Z`.
4. Following it lands on **`1 - 50 of 821`** - the destination count equals the number clicked.

### Per-cause drill-down (OBS-S1-17 .. OBS-S1-20)

Each cause on the card is itself a link, filtered to that cause alone:
channel + status + range + `status_code` + AND-ed `error_contains` terms. The
log list shows a banner naming the active cause with a **"Show all failures"**
escape hatch, because the cause filter has no control in the filter panel.

Live verification, card count vs link count:

| Cause | Card | Link lands on |
|---|---|---|
| respond_io 401 | 428 | **428** |
| respond_io 403 | 330 | **330** |
| respond_io template removed | 18 | **18** |

Clearing the banner widens 428 → 821 (all failures) and drops the banner. Console clean.

### Defect found: one filter term was not enough (found in the browser, not by tests)

The first cut sent a single `error_contains` - the longest stable substring.
Clicking the 401 cause showed **433** rows against a card reading **428**.

The 5 extra rows were a genuinely different fault: `401` against
`/conversation/status` rather than `/message`. The longest stable run of the
`/message` message stops at the url version digit (`…api.respond.io/v`, because
`v2`'s digit is a volatile token), and that prefix is shared by both faults. One
substring simply cannot express the group.

Fix: emit **all** stable segments (min 4 chars, capped at 6) and AND them - 
`/message'` is short but decisive, so the minimum length had to come down from
12 to 4. `test_card_count_equals_link_count` now seeds exactly this two-endpoint
shape and asserts card count == link count for every cause, so the invariant is
pinned in sqlite rather than depending on live data.

Worth noting: every unit test passed on the single-term version. The mismatch was
only visible by comparing two numbers on two different screens.

### Defect found while wiring the drill-down

`integrationFailedHref` hardcoded `created_from = now - 24h` while the dashboard could be
showing 7 or 30 days. Clicking "821 failed" on a 30d view landed on the last 24h - a
different, much smaller set than the number clicked, with nothing on screen to say so. The
href now takes the dashboard's own range. This is exactly the class of bug the range picker
introduced in OBS-S1-01 and it went unnoticed until the count was checked against the
destination, so the e2e assertion above pins the row count, not just the URL.

---

## S3 - `api_call_log` request telemetry

### Results

| UAC | Verdict | Evidence |
|---|---|---|
| OBS-S3-01 | PASS | Migration 293: `api_call_log` + 4 indexes, two `system_settings` retention columns, prune task seed, view permission. Single head. |
| OBS-S3-02 | PASS | `ApiCallLogMiddleware` (pure ASGI) writes exactly one row per request, synchronously. |
| OBS-S3-03 | PASS | `test_a_brand_new_route_is_logged` drives a route the middleware has never seen, with no per-endpoint code. |
| OBS-S3-04 | PASS | Key-based redaction (recursive, case-insensitive) + 8KB truncation. Redaction runs **before** truncation. |
| OBS-S3-05 | PASS | `X-Source` / `X-Tool-Name` / `X-Correlation-Id` recorded; live row shows `source=mcp tool=stock_balance corr=verify-s3-001`. |
| OBS-S3-06 | PASS | Missing header → `source='unknown'`, row still written. |
| OBS-S3-07 | PASS | MCP client sends all three headers, correlation id unique per call; 6 tests in `sorento_crm_mcp/tests/`. |
| OBS-S3-08 | PASS | Live-exercised: work-calendar, team-members, respond-contacts, contact-access-types, next-assignee, plus a 404 - all previously unlogged, all produced rows. |
| OBS-S3-09 | PASS | `integration_log` untouched by this slice. |
| OBS-S3-10 | PASS | Two-stage prune: payloads NULL at 30d, rows DELETE at 180d, both configurable. |
| OBS-S3-11 | PASS | `test_a_failing_log_write_does_not_break_the_response`. |
| OBS-S3-12 | PASS | Page reached via sidebar; source/outcome/date/correlation filters + search; empty state renders; console clean. |
| OBS-S3-13 | PASS | Measured below. |

### Suite results

- `pytest tests/test_api_call_log_{service,middleware,prune}.py` - 61 passed
- `python -m pytest tests/` (MCP) - 125 passed, 1 pre-existing failure (see below)

### Latency cost (OBS-S3-13)

Synchronous logging is the deliberate choice - a buffered writer drops exactly the
records you need when the process dies. Measured on the same endpoint, 80 requests each,
after warmup, toggling `API_CALL_LOG_ENABLED`:

| | p50 | p95 | p99 |
|---|---|---|---|
| With logging | 2.19 ms | 2.64 ms | 3.54 ms |
| Without | 2.37 ms | 2.69 ms | 2.82 ms |

The p50/p95 delta is inside run-to-run noise (with-logging measured marginally *faster* at
p50, which is noise, not a speedup). The real cost shows at p99: **~0.7 ms**. The isolated
row-write is p50 0.40 ms against a warm pool.

A `API_CALL_LOG_ENABLED` settings flag was added as an ops kill switch - it made this
measurement possible, and it means a write-path problem can be shut off without a deploy.

### Deviation from the plan

The plan called for a Phase-1 mocked FE prototype. Skipped: the response contract is fully
pinned by OBS-S3-01 and the page is a standard DataGrid listing with a detail drawer, so
there was no UX question a mock would have answered. Built directly against real rows.

### Defect found: pytest resolved the MCP package to the MAIN checkout, not this worktree

The first run of the new MCP tests failed with `KeyError: 'X-Source'` - the headers I had
just added were absent. They were absent because
`../sorento_crm_backend/venv/bin/pytest` imported
`sorento_crm_mcp` from `/Users/tehjayson/Documents/foundryx/sorento_crm/sorento_crm_mcp/`
(the pip-installed editable path, which points at the main checkout) rather than from the
worktree. The tests were passing/failing against unmodified code.

Confirmed by printing `module.__file__` from inside a pytest run. `python -m pytest`
prepends the cwd and resolves correctly. **Any MCP test run from this worktree must use
`python -m pytest`, not the `pytest` entry point** - otherwise it silently tests the wrong
tree and a green run means nothing.

### Defect found in regression verification: the middleware missed most MCP traffic

Driving a real tool through the running MCP server (`crm_master_products_list`) returned
correct data but produced **no log row**. The middleware was scoped to
`/api/v1/external/*`, and most MCP tools proxy ordinary CRM endpoints - that catalogue is
`/api/v1/master-data/products`. So the client was sending full attribution and the server
was discarding it, for the majority of MCP calls.

The plan's wording was "every `/api/v1/external/*` route **and every MCP-originated
call**"; only the first half had been implemented. Scope is now
`path.startswith('/api/v1/external') OR X-Source present`. Keyed on the header rather than
an endpoint allowlist so any self-identifying caller is recorded wherever it lands, while
internal UI traffic (no `X-Source`) stays out and cannot feed the table its own reads.

Verified live: `/api/v1/master-data/products` now logs `source=mcp
tool=crm_master_products_list`, with the same correlation id across the 307 redirect and
the followed 200 - so the redirect hop is visible rather than hidden.

Not caught by any test, because every test asserted the prefix behaviour that was
implemented. Two tests added: MCP-on-non-external is logged, and the same route without
attribution is not.

### Pre-existing bug fixed on request: 500 for a legitimate 400 (OBS-S1-05b)

`create_sla_tracking_integration` - the endpoint n8n calls to open a conversation SLA - 
wrapped every failure in `except Exception: raise handle_internal_error(str(e))`. A
deliberate refusal ("Respond contact not found for phone number: X") is raised as an
`AppException` carrying 400 / VALIDATION_ERROR, so it was re-wrapped into
500 / INTERNAL_ERROR with the real message surviving only as a string stuffed inside the
500 body. `HTTPException` was flattened the same way, being a subclass of `Exception`.

Same bug class as the three handlers fixed in S1; this one is a separate function and was
missed. Found by exercising the live endpoint during the regression pass, not by a test.

Held back initially because flipping 500→400 on n8n's hot path could change its error
branching; the user confirmed n8n does not branch on error code, so it is fixed here.

Internal-regression check before changing it: no test asserts 500 for this endpoint, no
frontend calls it, and the handler writes an `integration_log` only on the SUCCESS path - 
so the change is confined to the status code and body, with no logging side effects.

Live, after: `400 {"message": "Respond contact not found for phone number: +60100000000",
"code": "VALIDATION_ERROR"}`. Malformed body still 422.

Five tests added, including `test_a_genuine_crash_is_still_a_500` - the narrowing must not
hide a broken server behind a 4xx, which would be the opposite regression.

### Pre-existing failure, not fixed here

`sorento_crm_mcp/tests/test_presenters.py::test_stock_uses_relabelled_location_fields`
fails on the base tree as well (verified by stashing this slice's MCP changes and
re-running). Unrelated to telemetry; left alone to keep the slice scoped.


---

## Final regression verification (pre-PR)

Scoped to the test files covering the 30 backend modules the diff touches, rather than the
full 262-file suite - a full run costs 40+ min and re-verifies untouched subsystems. Every
failure was then re-run on a base-commit worktree, because a failing test is only a
regression if it passes on base.

| Suite | This branch | Base | New regressions |
|---|---|---|---|
| Affected backend (29 files) | 300 passed, 2 failed, 4 errors | same names | **0** |
| SLA (29 files) | 287 passed, 10 failed | same names | **0** |
| MCP (`python -m pytest`) | 125 passed, 1 failed | same name | **0** |
| Frontend (full vitest) | 10 failed | 13 failed | **0** |

Pre-existing failures confirmed identical on base, not touched by this branch:

- `test_smart_chat_send.py` - 2 (`422 != 200` on the send route)
- `test_rbac.py` - 4 errors (`sqlalchemy.exc.OperationalError` in the fixture)
- `test_conversation_sla_coverage_fanout.py` - 6
- `test_sla_assignee_team_derivation.py` - 3, `test_sla_due_escalations.py` - 1
  (all `400 != 200` on "Cannot escalate a resolved conversation SLA tracking" - the tests
  and the service disagree on the base tree)
- `sorento_crm_mcp/tests/test_presenters.py` - 1
- Frontend: portal `LookupSelect` (2), `notification-channels-preference` (3),
  `settings/system-health` (5)

The frontend count went 13 → 10 because this branch fixes the `created_from` indicator
assertion; the two `AccessAgentForm` cases are order-dependent flakes in code this branch
never touches.

### Live end-to-end checks

- `POST /external/chat-history/messages` → 201 with a real row id, and the logged payload
  matches the sent body - the middleware's buffer-and-replay does not corrupt the n8n
  ingest path (the single largest regression risk in this branch).
- MCP server booted and real tools driven over streamable-HTTP: correct data returned.
- Integration logs (4046 rows unfiltered), scheduled tasks (16, prune task seeded), health
  (5 cards, 0 overdue), chat history, api-call-log - all reached via the sidebar, console
  clean.
- Migrations 289 - 293, single head, chained onto a committed migration. No new migrations
  on `main`; merges clean.
