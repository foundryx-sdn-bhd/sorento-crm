# UAC: HA-MONITOR

Plan: `PLAN-ha-monitor-14oct.md`. Tests: `sorento_crm_backend/tests/test_healthchecks_ping.py`
(HB) and `sorento_crm_backend/tests/test_cf_proxy_script.py` (CF).

## Scheduler heartbeat (HB)

- **HB-1** With `HC_SCHEDULER_URL` unset (or blank), the scheduler sends nothing and opens no DB
  session for it. (`test_unset_url_does_nothing`, `test_tick_does_nothing_when_env_unset`)
- **HB-2** With it set, a real APScheduler job runs every 5 minutes, reads the DB once and pings
  the URL. (`test_start_scheduler_registers_the_heartbeat_job`,
  `test_tick_reads_the_db_then_pings_success`)
- **HB-3** A failed DB read pings `<url>/fail`. (`test_tick_pings_fail_when_the_db_read_breaks_and_never_raises`,
  `test_fail_ping_hits_the_fail_endpoint`)
- **HB-4** The ping never raises, has a timeout of at most 10 s, and does not block the caller.
  (`test_send_never_raises_and_never_logs_the_url`, `test_ping_hits_the_url_with_a_short_timeout`,
  `test_ping_is_fire_and_forget`, `test_ping_survives_a_thread_that_cannot_start`,
  `test_tick_never_raises_when_the_ping_itself_breaks`)
- **HB-5** The ping URL never reaches a log line, including the HTTP library's own logging, and a URL that is not http(s) is not sent.
  (`test_a_real_ping_never_logs_the_url`, `test_a_url_that_is_not_http_is_refused_without_logging_it`)

## n8n heartbeat (N8N)

- **N8N-1** A workflow JSON runs every 5 minutes: one DB read through `sorento-crm-db`, one MCP
  tool call, then a ping; any failure (DB, MCP transport, MCP tool error inside an
  `isError: false` answer) means no ping. Verified by hand against a local MCP server (PR body).
- **N8N-2** The runbook has import steps, each with a check and a rollback.

## Runbook (RB)

- **RB-1** `documentation/runbooks/monitoring.md` lists the Kuma monitors (name, URL, interval,
  keyword or status, threshold) for fe-sorento `/health`, n8n `/healthz`, shared service
  `/health`, the frontend `/`, and TLS expiry for each host.
- **RB-2** Kuma pushes to a Healthchecks check every minute (watch the watcher).
- **RB-3** Healthchecks checks: scheduler, n8n heartbeat, nightly backup, restore test, with
  schedules and grace periods.
- **RB-4** Alert routing options (Telegram, email, push) for the owner to choose; none through
  our own WhatsApp path.
- **RB-5** respond.io "webhook disabled" email becomes an alert through a Gmail filter.
- **RB-6** No real IPs, origin hostnames, secrets or tokens in the repo.

## Cloudflare switch (CF)

- **CF-1** `status|off|on [--yes]`; only `--yes` writes, the default prints the plan.
  (`test_off_without_yes_is_a_dry_run`, `test_status_lists_each_host_without_ips_and_writes_nothing`)
- **CF-2** Only the A/AAAA/CNAME records of fe-sorento, automate-sorento, chat and ss in
  `foundryx.my` are read or written; a foreign record from the API (name, zone, id) stops every
  write. (`test_off_with_yes_unproxies_only_the_sorento_address_records`,
  `test_the_list_filter_alone_refuses_a_foreign_record`,
  `test_the_write_guard_alone_refuses_a_foreign_record`, `test_a_record_from_another_zone_is_never_patched`,
  `test_a_record_id_that_is_not_a_plain_id_is_never_patched`, `test_two_zones_with_the_same_name_refuses`)
- **CF-3** `off` = proxied false, TTL 60; `on` = proxied true, TTL 1 (auto); records already in
  the target state are left alone. (`test_on_with_yes_reproxies_with_auto_ttl`,
  `test_records_already_in_the_target_state_are_left_alone`)
- **CF-4** The token comes from the file named by `CF_TOKEN_FILE` and is never printed, nor are
  record contents (origin IPs); the token is never sent across a redirect.
  (`test_a_token_file_that_is_not_one_plain_token_refuses_without_echoing_it`,
  `test_an_unexpected_exception_prints_only_its_type`, `test_a_patch_exception_prints_only_its_type`,
  `test_real_http_client_does_not_follow_a_redirect_with_the_token`,
  `test_missing_token_file_env_refuses`)
- **CF-5** The runbook says when to use it, what breaks while it is off, and when to switch back.
