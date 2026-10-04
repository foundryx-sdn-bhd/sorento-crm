# PLAN: retail sales reports on the reports kernel, one query layer for the screens and the chatbot (#1267)

Status: S1 built and ready for review (26 Sep 2026; merged head re-verified: backend S1 +
chatbot + report suites, MCP, vitest, browser at 1280 and 375; main merged again after #1260
landed the `sales` module first, see S1-B), track full, on PR #1269 (the plan rides inside the feature PR,
owner ruling 26 Sep ~08:25Z; Q9 (a), Q10 (a); Mocha AutoCount connected in production, so S4
shrinks to verifying Mocha sales orders arrive). S2, S3, S5, S6 not built. Build notes and the
deviations S1 took from this text are in section S1-B below.
S4 check (26 Sep, code only, no prod access from the lane): no CRM code change is needed for
Mocha orders. `POST /external/ingest/sales_orders` files each batch under the company named by
`companyCode` (or the integration's `config_json.company_code`), matched on `companies.code`
then `companies.autocount_ref` (`app/api/v1/external/company_anchor.py`), and refuses (422)
rather than defaulting to Sorento. What is left is data, not code: count Mocha's
`sales_orders` on prod (AC-S4-2), then switch on Mocha's `so_feed_live` on the Companies page.
S4 stays as that verification only.

Round 5 status line, kept: draft, round 5 (26 Sep 2026), ready to build (S0, S4 and S1 can start now). The owner's
answers to round 4's Q6 to Q8 (PR #1269 comment 5844298765, 26 Sep 07:34:40Z, quoting "## Round
4" with the answers inline) are folded in: section R5 carries an "Owner ruling 26 Sep 07:34
Q<n>" line per answer. Q6: no scheduled WhatsApp send, every scheduled-WhatsApp path is withdrawn.
Q7: the scheduled send is an **automation** on the existing Automation screen, run by the
existing `automation_runner` scheduled task, so `sales.report_subscriptions`, the Scheduled sends
dialog, its routes and the `sales.reports.schedule` slug are withdrawn and **this plan adds no
table** (R5.3). Q8: (a) ruled. Q9 is unanswered and recommendation (a) stands; Q10 is new (R5.6).
Posted on PR #1269 as comment 5844369383 ("Round 5").
Round 1 to 4 text is kept; a "Round 5:" note marks each place a ruling changes. Track per slice
unchanged (R5.5): S6 stays full (a migration and an outbound send of business figures). Nothing
built.

Round 4 status line, kept: draft, round 4 (26 Sep 2026). The owner's answers to round 2's Q1 to Q5 (PR #1269 comment
5844192717, 26 Sep 07:16:21Z, quoting "## Round 2" with the answers inline) are folded in: section
R4 carries an "Owner ruling 26 Sep 07:16 Q<n>" line per answer, reconciles round 3 (which was
written before these answers: the WhatsApp file rule, the scheduled sends, the module), the
reconciled slice list with parallel lanes (R4.5) and the open questions Q6 to Q8 plus Q9 (R4.6),
posted on PR #1269 as comment 5844247992 ("Round 4"). Round 1 to 3 text is kept; a "Round 4:" note marks
each place a ruling changes. Track per slice unchanged (R4.5). Nothing built.

Round 3 status line, kept: draft, round 3 (26 Sep 2026). The owner's grill answers (PR #1269 comment 5844136277,
26 Sep 07:06Z) are folded in: section R3 carries an "Owner ruling 26 Sep 07:06 G<n>" line per
answer, the module and schema alignment with #1260 round 5, the new slice S6 (the weekly scheduled
Excel by email or WhatsApp, G9), the round 3 slice waves, G7 asked again in plain words, and the
round 3 questions Q6 to Q8, posted on PR #1269 as comment 5844192500 ("Round 3"). Round 2 (section 0, the Lavish review of 26 Sep 06:27Z) and round 1
text is kept; a "Round 3:" note marks each place a ruling changes. Round 2's Q1 to Q4 are still
unanswered and their recommendations stand; Q5 is re-answered by R3.2. Track: full for S1, S2,
S3, S4 and S6 (S6 adds a table in the `sales` schema, a slug and an outbound send); S5 small fix
track; S0 a measurement with no code. Nothing built.
Domain: sales. Classification (round 2, note 2): **the Sales module** (`sales`, the module key
#1260 creates) for the menu, the permission and the chatbot route; **no new table**, so there is
nothing to put in a Postgres schema (section 0.2). Round 3: superseded by R3.2, aligned with
#1260 round 5: the `sales` module with the `sales` Postgres schema for every new table, which here
is one table, `sales.report_subscriptions` (S6). The reports are two definitions registered on
the existing reports kernel (`app/services/reports/`), which the sponsorship report uses today.
Round 4 (Owner ruling 26 Sep 07:16 Q5, "we need a sales schema and a sales module so we are more
modular"): the `sales` module and the `sales` Postgres schema are the ruling; every new table of
this plan lives in `sales` (R4.3).
UAC: `retail-sales-reports-26sep-acceptance-criteria.md` alongside (the contract; the journey J1
to J9 lives there and is not repeated here).
Mockup: `mockups/retail-sales-reports.html` (round 2: Yearly comparison and the Sales report on
the kernel's report page, Sorento and Mocha, My Downloads, and the chatbot replies with the file
or text rule, each at 1280 and 375).
Issue: #1267.

Backend paths are under `sorento_crm_backend/`, frontend under `sorento_crm_frontend/`, MCP under
`sorento_crm_mcp/`. Line numbers are on origin/main 46711c61 unless a PR branch is named.

## S1-B. S1 as built (26 Sep 2026)

What S1 shipped, and where it differs from the text below (each deviation is the smaller
design; nothing here changes a ruling):

- **One measure follows the Basis filter.** `sales_value` reads the confirmed (delivered)
  value or the ordered value by the Basis filter; `ordered_value` and `delivered_value` stay
  in the catalogue for Configure summary. The basis line is `ReportDefinition.note`, printed
  under the filter bar and in the workbook's title block.
- **The Company filter is a select param the kernel knows** (`Dataset.company_param`): the
  options are the caller's granted companies, the default is their current company, a
  company outside the grant is 403, and the company arm is fail-closed (no grant, no rows).
  A company-scoped dataset's statements run with the ORM company listener off; the kernel
  applies the company itself (a user granted Sorento and Mocha reads Mocha from a Sorento
  session). The export job carries the enqueuer's grant (`company_grants`).
- **`sheet_per` writes blocks on the one SUMMARY sheet** (Q1 (b)), and the screen shows the
  same blocks (`ReportLayouts.blocks`), each with its table and chart (J2).
- **VARIANCE only when the rows are the period's years and the columns the months of the
  year** (`Column.period_years`, `registry.MONTHS_OF_YEAR`); re-pivoted by month or channel,
  or years by channel, the column totals come back (reviewer finding; review round 2 B1).
- **VARIANCE runs JAN to the as-at month, a missing cell read as 0 in both years** (review
  round 2 B1, `engine.variance_months`): a month this year sold nothing in and last year did
  counts against this year, so the total is this year to date minus last year over the same
  months. A month neither year sold in stays blank. The chatbot's Difference column uses the
  same months for both its shapes (months down, or one line per channel).
- **The chatbot says an axis it cannot draw** (review round 2 S1): a sales analysis asked by
  customer, product, date, warehouse, supplier or transporter gets one line naming month,
  year and channel, with no figure and no fetch. `year` maps explicitly to one line per
  channel, the years across.
- **The detail truncates** past the 5,000 line cap instead of refusing (`DetailLayout.cap`),
  newest lines first, whole-set totals kept, so a three-year summary is never refused.
- **Chatbot: no `compare_years` key.** The period dates carry the years ("2025 vs 2026" is
  1 Jan 2025 to 31 Dec 2026); `group_by` gains `month` and `year`; new nullable keys
  `sales_basis` and `sales_company`; the ask is `order_status "sales_analysis"`. The prompt is
  republished and promoted by the S1 migration (the chatbot_rearch_s12 rule).
- **The route is the chatbot's only** (`X-API-Key` required, security review B2), the dealer
  check reads with the company scope off (B1), ten asks per contact per ten minutes.
- **The `sales` module stays dormant** (review round 2 S2, 27 Sep, captain's brief: "#1260
  shipped it dormant on purpose, keep it so"). The S1 migration writes no `tenant_modules`
  row, and its downgrade leaves any such row alone. Owner step after deploy: switch on Sales
  in App Store; until then the menu item is hidden from every non-admin.
- **The dev "1 Issue" badge is not S1's** (review round 2 S3): it is React's key warning from
  `Demo1Layout`, a file S1 does not touch, and it also showed on a fresh Dashboards load.
  The retaken `evidence/s1/merged-head/` screenshots (1280 and 375) show no badge.
- **AC-S1-6's "a list over 50 ids" is not applicable in S1**: no id-list param exists yet
  (customer lists arrive with S2 and S5).
- **Company names come from `companies.name`** ("SORENTO - DEALER"); the PDF's "SORENTO SDN
  BHD" is a rename on the Companies screen, not a code change.
- **New FE files:** the route wrapper, `ReportPivotChart.tsx` and `reportFormat.ts` (a plain
  module for the whole-ringgit formatter: a 'use client' component file must export
  components only, LESSONS-LEARNT 106).
- **Merged with #1260 S6 (main dc10a1afb, 26 Sep 14:19Z), which landed the `sales` module
  first.** One module, one router, one menu group:
  - `sales_s1_reports_module` now sits on `sales_0002_team_leader` (one alembic head). The
    schema and the catalog row are `sales_0001_teams`'s, so S1's `CREATE SCHEMA IF NOT
    EXISTS` and catalog insert are no-ops there, and S1's downgrade leaves the catalog row
    to that revision. S1 no longer enables the module (review round 2 S2, above).
  - `/api/v1/sales` is mounted once; its router holds the teams routes and the analysis
    route.
  - The Yearly comparison joins #1260's Sales group (Sales Teams, Sales Agents), which has
    no group-level `moduleKey`, so the item carries `moduleKey: 'sales'` itself.
  - The manifest dependencies are #1260's (`base`, `product`, `order`), and the purge file
    lists #1260's teams tables. This plan still adds no table.

## R5. Round 5: the owner's answers to round 4's Q6 to Q8 (26 Sep 07:34Z)

The owner answered Q6 to Q8 inline on PR #1269 (comment 5844298765, 26 Sep 07:34:40Z, quoting "##
Round 4"). The reply is binding. Q9 got no answer. Nothing earlier in this file is deleted; a
"Round 5:" note marks each place a ruling changes. Line numbers are on origin/main 46711c61.

### R5.1 Ruling lines

- **Owner ruling 26 Sep 07:34 Q6**: "we don't scheduled send whatsapp". Applied (R5.2): a
  scheduled send never goes by WhatsApp. Every scheduled-WhatsApp path is withdrawn: the WhatsApp
  channel of S6, the window-open text then file, the window-closed template (round 3's (a) and
  round 4's (b) "Reply to this message and the Excel is sent here."), the `sales_report_scheduled`
  WhatsApp use case, the Meta template approval in S6's DoD, the `deliver_to_contact_id` claim on
  the next chat turn, and the `respond_contact_id` / `outbound_enabled` skip rules. WhatsApp only
  answers when asked: the chatbot's text + file on every answer (R4.2, Q2) is unchanged, and it
  always replies to a message the contact just sent, so the 24 hour window is open.
- **Owner ruling 26 Sep 07:34 Q7**: "we can use our scheudled task and automation". Applied
  (R5.3): a scheduled send is **an automation** on the existing System Management > Automation
  screen, run by the existing `automation_runner` scheduled task. Not a Views menu > Scheduled
  sends dialog, not a new `sales.report_subscriptions` table, not new routes, not a new slug. The
  automation holds both the recipients and the view, so no table is needed (R5.3 says why).
- **Owner ruling 26 Sep 07:34 Q8**: "okay". Applied: (a), the file is this year to date, as at
  the send date: the view's saved filters and table layout, with the period replaced by 1 January
  of the send date's year to the send date. A recommendation until now; a ruling from here.
- **Q9: not answered.** Recommendation (a) stands and stays written into the UAC (AC-R4-2): every
  column on every line, under a line of column names.

### R5.2 Q6 applied: the scheduled send is email only

What goes: R3.3 step 4 (WhatsApp, window open and window closed), R4.6's Q6 mechanism (the
template text, the one-shot push on the next chat turn within 7 days), S6's WhatsApp tests, the
Meta-approved `sales_report_scheduled` template gate in S6's DoD, and the WhatsApp half of
AC-R3-14, AC-R3-18 and AC-R4-11 (each kept with a Round 5 note). The chatbot seam is untouched: the
risk "WhatsApp cannot carry the file outside the 24 hour window" no longer applies to anything in
this plan, because nothing here starts a WhatsApp conversation.

### R5.3 Q7 applied: the weekly send is an automation

**What exists (measured).** The brief pointed at "the daily low stock email". There is none: no
scheduled task, automation trigger, migration seed or email event sends low stock by email. The low
stock report is the chatbot and SCM on-demand export (`generate_low_stock_report`,
`app/tasks/export_tasks.py:1092`, pushed to the chat by `_push_low_stock_to_chat`, `:1003`), and
the daily reorder run (`_handler_scm_reorder_run`, `app/scheduler/task_scheduler.py:411-450`)
sends no email. The owner's words name the two pieces that do exist and that every scheduled email
of the CRM already uses:
- **Scheduled task:** the `scheduled_tasks` table (`app/models/scheduled_task.py:10-33`), one row
  per handler key, the heartbeat `run_due_tasks` (`app/services/scheduled_task_service.py:530`,
  every 10 s from `start_scheduler`, `task_scheduler.py:598`), worker only (`ENABLE_SCHEDULER`).
  The row `automation_runner` (seeded in `alembic/versions/174_email_templates_and_automation.py`,
  registered at `task_scheduler.py:577`) calls `_handler_automation_runner`
  (`task_scheduler.py:335-337`), which calls `AutomationService.evaluate_due()`
  (`app/services/automation_service.py:317-338`): every enabled automation whose `next_run_at` is
  due runs once.
- **Automation:** the `automations` table (`app/models/automation.py:21-80`): a trigger
  (`trigger_type` plus a free JSONB `trigger_config`, `:29-30`), an email template
  (`email_template_id`, `:33-38`), the recipients (`recipient_config` JSONB, `:40`: `user_ids`,
  `role_ids`, `extra_emails`, `one_email`, normalised at `automation_service.py:382-424`,
  resolved by `resolve_recipients`, `app/services/automation_recipients.py:19`), a schedule
  (`schedule_type` manual or daily, `run_time`, `timezone`, `:53-55`), `next_run_at`, and a run
  log (`automation_runs`, `:83-107`). Triggers are registered in
  `app/services/automation_triggers.py` (`TriggerSpec` `:29-44`, `register` `:63`, `fire` `:71`;
  twelve triggers today, the first `days_before_promotion_end` at `:169`). A run renders the template
  per match and recipient (`_send_per_match`, `automation_service.py:614`) and queues a
  Notification plus an email delivery (`_enqueue_email`, `:826-895`), which
  `notification_tasks.send_notification_deliveries` turns into an `email_outbox` row
  (`app/tasks/notification_tasks.py:252-289` for the one-email-to-all shape, `:314-330` for one
  per recipient). The screen is System Management > Automation
  (`sorento_crm_frontend/app/(protected)/system-management/automation/`, `AutomationForm.tsx`,
  `RecipientPicker.tsx`, `AutomationRunsTable.tsx`), gated by `automation.automations.view`,
  `.add`, `.edit`, `.delete` and `.run` (`app/api/v1/system/automation.py:39-150`); it already has
  Enabled (toggle, `:116`), Run now (`:143`) and a runs table.

**Can the automation hold the recipients and the view? Yes, so there is no new table.**
- **The view** goes in `trigger_config`, which is free JSON stored as is
  (`app/schemas/automation.py:37`): `{"report_view_id": "<a shared view of sales or
  sales_yearly>"}`.
- **The recipients** are the automation's existing `recipient_config`, picked with the existing
  `RecipientPicker` (users and roles). One automation = one view sent to many people, where
  `sales.report_subscriptions` had one row per person per channel.
- **Enabled, Run now (was Send now), the run log (was Last sent), Delete and the schedule** are
  the automation's own.

**What S6 adds to the automation machinery (small, generic, each with a named reason):**
1. **A trigger `sales_report_view`**, registered with `register(...)` in
   `app/services/automation_triggers.py` beside the twelve others, its function in
   `app/services/sales/report_automation.py`. `config_schema`: `report_view_id` (required). One
   match per run. The function:
   - loads the view; a view that is gone, not shared, or not `sales` / `sales_yearly`, or the
     `sales` module disabled, gives no match and a run summary reason (`view_missing`,
     `view_not_shared`, `not_a_sales_report`, `module_disabled`), so nothing is sent;
   - builds the workbook **inline** on the scheduler process (as `_handler_scm_reorder_run` runs
     its pipeline inline, `task_scheduler.py:411-450`) through the kernel's export
     (`generate_report_xlsx`) under the view's company, with the view's filters and pivot and the
     period replaced by year to date as at the send date (Q8). It lands one `report_xlsx` My
     Downloads row owned by the automation's owner (`_resolve_owner_user_id`,
     `automation_service.py:24`), kept 30 days like every download;
   - puts on the match context what the template prints (view name, report title, company, basis,
     "as at" date, the totals line; no UUID) and the file reference `attachment: {filename,
     storage_provider, storage_key}`.
2. **The attachment reaches the email.** `_send_per_match` copies `match.context["attachment"]`
   into the Notification data through `_enqueue_email`; both `enqueue` calls in
   `notification_tasks.py` (`:264`, `:318`) pass it on as `attachment_filename`,
   `attachment_storage_provider`, `attachment_storage_key`, which `email_outbox_service.enqueue`
   already takes (`app/services/email_outbox_service.py:86-88`). No attachment on the context =
   today's behaviour, so every existing automation is unchanged (tested).
3. **Access re-checked at every send (G10).** `TriggerSpec` gains one optional field,
   `recipient_check(db, recipient, context) -> bool`, applied in `_send_per_match` after
   `resolve_recipients` (`automation_service.py:635`). For `sales_report_view` it keeps only an
   active user holding `sales.reports.view` and granted the view's company; anyone else (a role
   member without the slug, a lost grant, an address typed into Extra emails, which has no user to
   check) is skipped and listed in the run summary as `no_access`. Reason, stated: G10 ruled that
   only holders see these figures, and an automation's recipients can drift from the grants after
   it is saved. Every other trigger leaves the field empty and is unchanged.
4. **A weekly schedule.** `schedule_type` gains `weekly` (the check at `automation_service.py:354`)
   and `automations` gains one nullable column `run_weekday` smallint (0 Monday to 6 Sunday); the
   next run is the next `run_weekday` at `run_time` in the automation's timezone,
   `_next_run_for_weekly` beside `_next_run_for_daily` (`:50`) in `_compute_next_run` (`:71`). The
   owner asked for "produced weekly"; a daily automation would send the same view every day. Any
   automation can use Weekly, which is why it is a column on the automation and not a field of this
   trigger. #1260 S5 may import the helper; round 4's `app/services/sales/schedule.py` is withdrawn.
5. **One seeded email template** "Sales report (weekly)" (an `email_templates` row, in S6's
   migration): subject `{{ view.name }}, as at {{ as_at }}`, the body the text header of 0.6
   (report and view name, company, basis, the period "as at", the totals line). It satisfies the
   NOT NULL `email_template_id`; the owner can edit its wording on the Email Templates screen. No new
   email event: automation emails already flow through the Notification path's event key.
6. **The form.** `AutomationForm.tsx` today builds only `days_before` into `trigger_config` and
   hard-codes `action_type` (`:214-215`). For `sales_report_view` it shows **Report view**
   (`SearchableSelect` of shared views of the Sales report and Yearly comparison, by name) in place
   of Days before; the Schedule radio gains **Weekly** with **Day** (`SearchableSelect`, Monday to
   Sunday) and Time. Nothing else on the screen changes.

**Withdrawn by Q7 (each kept in the text above with a Round 5 note):** `sales.report_subscriptions`
and its model, purge entry and invariants test; `ReportViewsMenu` > Scheduled sends and the
Scheduled sends dialog (frames 9 and 10 of round 3); `GET|POST /api/v1/sales/report-subscriptions`,
`PATCH|DELETE .../{id}`, `.../{id}/send-now`; the slug `sales.reports.schedule` and its grant
sweep (setting up a send is `automation.automations.add`, as for every automation); the
`sales_report_scheduled` email event; the runner handler `send_sales_report_subscriptions` and its
`scheduled_tasks` row (the existing `automation_runner` runs it); the per-person, per-channel row
and the `integration_log` bookkeeping (the automation's run log replaces it).

**Where the file lands.** Round 3 put a copy in each recipient's My Downloads. An automation builds
one file per run for all its recipients, so the file is attached to each email and one copy sits in
the automation owner's My Downloads. Trigger for a copy per recipient: the owner asks for it.

### R5.4 The sales schema after Q7

**This plan now creates no table.** The inventory of R4.3, re-checked:

| Thing | New table? | Where it lives |
| --- | --- | --- |
| Scheduled sends (S6) | **no** (was `sales.report_subscriptions`) | an `automations` row per weekly send (core `public.automations`, owned by the automation module), its runs in `automation_runs`, one seeded `email_templates` row, and one new column `public.automations.run_weekday` |
| Saved views, set-apart and exclude lists | no | `report_views` rows |
| Person labels (S2, Q3) | no | `public.sales_agents.person_label` |
| Debtor type (S3) | no, a column | `public.customers.debtor_type` |
| The chatbot's and the weekly send's files | no | `public.user_downloads` rows |
| Sales orders, lines, agents, customers | no | `public` |

- **The `sales` schema is still created** by the lane that creates the `sales` module (this S1 or
  #1260 S6, whichever lands first), per Owner ruling 26 Sep 07:16 Q5 (R4.3). This plan puts
  nothing in it; #1260's tables (`sales.teams`, `sales.targets`, ...) will. AC-R4-10 stays as the
  guard: any table a later ruling adds goes in `sales`.
- **`purge_tables.json`:** this plan adds no entry. If this S1 creates the module before #1260,
  `sorento_crm_frontend/modules/sales/purge_tables.json` ships with an empty list, and #1260 fills
  it.
- The weekly-send automation rows are not purged with the `sales` module (they live in the
  automation module); with the module disabled the trigger gives no match (`module_disabled`) and
  sends nothing.

### R5.5 Slices, round 5: final list, with parallel lanes

Each slice is its own lane and PR (one lane = one branch = one PR).

| Wave | Slice | Track | Needs merged first | Runs beside | Round 5 change |
| --- | --- | --- | --- | --- | --- |
| 0 (now) | **S0** measure on the prod copy (captain, no code, no PR) | none | nothing | S4, S1 | none |
| 0 (now) | **S4** Mocha's AutoCount SO feed (ESB, mostly outside this repo) | full | nothing | S0, S1, wave 2 | none |
| 1 | **S1** dataset, Yearly comparison, kernel extensions with `sheet_per`, Export to Excel, `crm_sales_analysis` with text + file on every answer; creates the `sales` module and schema if #1260 S6 has not | full | nothing | S0, S4; S3's migration and ingest half | Q9 still (a); an empty `purge_tables.json` if it creates the module |
| 2 | **S2** Sales report: person, set apart, exclude, quarter, shared views | full | S1 | S3, S6 | none |
| 2 | **S3** debtor type and the product brand axis | full | S1 for the dataset half | S2, S6 | none |
| 2 | **S6** the weekly Sales report email as an automation: trigger `sales_report_view`, attachment pass-through, `recipient_check`, Weekly schedule (`automations.run_weekday`), seeded template, the form's Report view and Day | full | S1 | S2, S3 | Q6: email only; Q7: automation, no table, no dialog, no routes, no slug; Q8 ruled |
| 3 | **S5** Mocha on the Sales report | small fix | S2, S3, S4 | nothing | none |

- **Parallel lanes today:** S0, S4 and S1 at once. S3's migration and ingest half may open beside
  S1. After S1 merges: S2, S3 and S6 at once (three lanes). S3 (`customers.debtor_type`) and S6
  (`automations.run_weekday` and the template seed) both carry a migration, so the pre-PR gate
  re-parents each before its merge. S5 last.
- **S6 needs S1** for the kernel export under a company and the per-report module check; it no
  longer needs anything from #1260 (the weekly helper lives in `automation_service.py`).
- **Track check:** S6 stays full: a migration, and an outbound email of business figures
  (`security-reviewer` runs on it). Its diff is smaller than round 4's (no table, no routes, no
  dialog, no WhatsApp). No other slice moves.

### R5.6 Open questions (at most 2)

Posted on PR #1269 as comment 5844369383 ("Round 5"), with the final slice list of R5.5.

- **Q9 (from round 4, unanswered). How wide the text part of an answer may be.** (a) every column
  on every line under a column name line; (b) past 4 columns, the row total only. **Recommend
  (a)**, unchanged; S1 builds (a) unless the owner rules (b).
- **Q10 (new). The brief said "the scheduled task and automation the daily low stock email uses";
  there is no daily low stock email in the CRM.** The machinery that sends scheduled emails is
  System Management > Automation, run by the `automation_runner` scheduled task (R5.3). Options:
  (a) the weekly Sales report is an automation there (Report view, Weekly, Day, Time, recipients);
  (b) you meant another job. **Recommend (a)**: it is the only scheduled email path the CRM has,
  and it already has recipients, Enabled, Run now and a run log.

## R4. Round 4: the owner's answers to round 2's Q1 to Q5 (26 Sep 07:16Z)

The owner answered Q1 to Q5 inline on PR #1269 (comment 5844192717, 26 Sep 07:16:21Z). The reply
is binding. Round 3 (comment 5844192500, 07:16:19Z) was posted two seconds earlier and was written
before these answers, so R4.4 reconciles it. Nothing earlier in this file is deleted; a "Round 4:"
note marks each place a ruling changes.

### R4.1 Ruling lines

- **Owner ruling 26 Sep 07:16 Q1**: "(b) ... - okay". Applied: with both channels ticked, one
  Excel holds the DEALER and PROJECT TEAM blocks one under the other, like the PDF
  (`WorkbookSpec.sheet_per`, kernel extension in S1). A recommendation until now; a ruling from
  here.
- **Owner ruling 26 Sep 07:16 Q2**: "always text + file, no cutoff". Applied (R4.2): **every
  answer the chatbot gives from `crm_sales_analysis` is sent as a text reply AND the Excel file of
  the same query.** The 12 figure rule is withdrawn, the list / small table / large table shapes
  no longer decide anything, and no override words exist: "in Excel", "as a file" and "send the
  report" are not needed (the file always comes), and **"as text" is dropped**, because the plan
  has no answer that needs a text-only reply and the ruling says "always". Trigger to bring "as
  text" back: the owner asks for a text-only reply.
- **Owner ruling 26 Sep 07:16 Q3**: "okay". Applied: S2's migration pre-fills
  `sales_agents.person_label` from the code's name part (SEAN I and SEAN III become SEAN), never
  overwrites a typed label, and the owner corrects exceptions on the Sales Agents screen.
  `person_label` is an existing column of `public.sales_agents` (`app/models/sales_agent.py:60`),
  so this adds no table.
- **Owner ruling 26 Sep 07:16 Q4**: "okay". Applied: the report sections are shared saved views
  in the Views menu, not tabs; the per-month tables are the workbook's month sheets.
- **Owner ruling 26 Sep 07:16 Q5**: "we need a sales schema and a sales module so we are more
  modular". Applied (R4.3): the `sales` module **and** the `sales` Postgres schema, the same one
  #1260 round 5 recommends (module key `sales`, schema `sales`; the firstmate note on PR #1269,
  07:18Z, confirms the names). Every new table of this plan lives in schema `sales`. Round 3's
  R3.2 already recommended this; it is now the ruling.

### R4.2 Q2 applied: text + file on every answer

- **What is sent.** For every answer (a figure or a table the tool fetched):
  1. **The text**, at once, in the turn: the header (report, company, channel, basis, period,
     agent and codes, excluded customers, the count) and **the whole answer**: one line per row,
     `label: RM a` for one value column, `label: a | b | c` under a column name line for two or
     more, then the totals line. Every row, whatever the count; n8n chunks a long message (owner,
     PR #1258 05:32Z). No "Full table in the attached Excel" in place of rows: the text is the
     answer, not a summary. How wide a text line may get is Q9.
  2. **The Excel file** of the same query, as a document, through the low stock report's path
     (0.6 "The file path"): a `report_xlsx` My Downloads row, `generate_report_xlsx` on the
     worker, `attachments` returned when ready inside the sync window (7 s cap,
     `app/api/v1/scm/low_stock_report.py:120-146`), otherwise the text ends "The Excel follows
     here." and the worker pushes it once (`_push_low_stock_to_chat` and `_claim_chat_delivery`,
     `app/tasks/export_tasks.py:911-927, 1003-1089`, generalised from `low_stock_xlsx` to
     `report_xlsx`).
- **Not an answer, so no file:** a clarify question ("Sorento or Mocha?", "Which Tan: TAN KH or
  TAN WL?", "Debtor type or product brand?", "How many agents do you want to see?" for a ranked ask
  with no N), the dealer contact refusal, and an error line. The file comes with the answer that
  follows.
- **The 24 hour window does not bite here.** A chatbot answer replies to a message the contact
  has just sent, so the window is open for the text and for the file, including the worker's push
  seconds later. If the push is refused anyway (`attachment_window_closed`,
  `app/services/respond_chat_template_service.py:697-720`), the existing failure record applies
  (`user_downloads.error`, an outbox row), and the file stays in My Downloads.
- **Who owns the file:** the CRM user linked to the contact; with no linked user, the act-as user
  (the low stock rule, `app/api/v1/scm/low_stock_report.py:432-440`). My Downloads keeps a row per
  answer for the existing 30 day retention (`purge_expired_downloads`,
  `app/services/download_service.py:250`), so the extra rows age out with no new job.
- **What changes in code (all in S1, the slice that builds the chatbot seam):** the presenter no
  longer counts cells; the route always queues the workbook (the `deliver=file` switch of 5.5 is
  withdrawn, the route always delivers); the parser drops `reply_format`; the pending line
  becomes "The Excel follows here."; the file name is the kernel's `<title>-<period>.xlsx`.
  Every chatbot golden of S1, S2, S3 and S5 becomes a text golden plus an attachment assertion.
- **Cost, stated:** every answer now waits on a worker job for its file (up to 7 s in the turn,
  then the push). The text goes first and does not wait for the file.

### R4.3 Q5 applied: the `sales` module and the `sales` schema

**How existing module schemas are set up (measured on origin/main 46711c61):**
- **Models:** each module table declares its schema in `__table_args__`, for example `{"schema":
  "chatbot"}` (`app/models/chatbot_turn.py:69`) and `{"schema": "projects"}`
  (`app/models/project_so.py:135`); `app/models/scm.py:3-10` states the rule: a module-to-module
  FK is schema-qualified (`ForeignKey("scm.reorder_run.id")`), an FK into core is unqualified
  (`ForeignKey("products.id")`).
- **Migrations:** the module's first migration runs `CREATE SCHEMA IF NOT EXISTS <key>`
  (`alembic/versions/273_scm_module_schema.py:42`, `472_chatbot_turns.py:46`,
  `354_projects_schema_move.py:362`).
- **Alembic:** `alembic/env.py` runs `include_schemas=True` with an `include_name` filter
  (:57-69, :80, :100) against `KNOWN_SCHEMAS`, built from the models' own schemas (:49-54), so a
  new schema needs **no env change**: it is covered the day its first model declares it. A schema
  with no model yet is filtered out of autogenerate, never proposed as a DROP.
- **Uninstall:** the module's tables are listed schema-qualified in
  `sorento_crm_frontend/modules/<key>/purge_tables.json` (`modules/projects/purge_tables.json`
  lists `projects.*`), a purge deletes rows through the ORM model classes and never issues `DROP
  SCHEMA` (`app/modules/projects/purge.py:1-40`, ADR-0011
  `documentation/adr/0011-project-sales-tables-live-in-the-projects-schema.md`), and an invariants
  test guards it (`tests/test_projects_module_purge_invariants.py`).

**What this plan does with it:**
- **Module `sales`**, shared with #1260 (unchanged from R3.2): `app/modules/sales/bootstrap.py`
  (`MODULE_KEY = "sales"`), the `MODULE_MANIFEST` entry, `"sales": "sales"` in
  `permission_module_map.py`, routers under `app/api/v1/sales/` behind
  `require_module_enabled_with_api_key("sales")`, the Sales menu group with `moduleKey: 'sales'`,
  `/sales` to `sales` in `lib/route-module-map.ts`. Whichever of this S1 and #1260 S6 lands
  first creates them; the other reuses them (AC-R3-2).
- **Schema `sales`:** the lane that creates the module also runs `CREATE SCHEMA IF NOT EXISTS
  sales` in its migration, so the boundary shows in `\dn` from the first sales lane on, table or
  not. If that is this S1, its migration (the slug grant sweep and the `chatbot_domains` row it
  already carries) gains the one line. Every later migration that adds a `sales.*` table repeats
  the idempotent line, so lane order never matters.
- **Every new table of this plan is in `sales`.** The inventory, measured against this plan:

  | Thing | New table? | Where it lives |
  | --- | --- | --- |
  | Scheduled sends (S6) | **yes** | `sales.report_subscriptions`, `{"schema": "sales"}`, model in `app/models/sales.py` beside #1260's `sales.*` models (#1260 3.7: one flat model file per domain), listed in `modules/sales/purge_tables.json`, purge invariants test |
  | Saved views, and the set-apart and exclude lists in them | no | rows of the kernel's existing `report_views` (`app/models/report_view.py:21-22`), shared with the sponsorship report; a view is a row, not a table |
  | Person labels (S2, Q3) | no | the existing column `public.sales_agents.person_label` |
  | Debtor type (S3) | no, a column | `public.customers.debtor_type`, on the customer's own core table |
  | The chatbot's and the scheduled send's files | no | the existing `public.user_downloads` rows (My Downloads) |
  | Sales orders, lines, agents, customers | no | stay in `public`, owned by `order` and `product` (#1260 3.7, "Not recommended: a `sales` schema holding `sales_agents` or `sales_orders`") |

  Named trigger: a later ruling that adds a table (footnotes stored in the CRM, G8 (b); dated
  person labels) puts it in `sales` by this ruling.
  - Round 5 (Owner ruling 26 Sep 07:34 Q7): the first row is withdrawn. Scheduled sends are
    `automations` rows, so this plan creates no table; the `sales` schema is still created by
    the module-creating lane (R5.4).
- **The code is modular too:** routes in `app/api/v1/sales/` (`analysis.py`,
  `report_subscriptions.py`), services in `app/services/sales/` (`schedule.py`,
  `report_subscriptions.py`), the model in `app/models/sales.py`, schemas in
  `app/schemas/sales.py`, pages under `app/(protected)/sales/`. The dataset and the two report
  definitions stay in the kernel's registration folders (`app/services/reports/datasets/`,
  `definitions/`) beside the sponsorship ones, and carry `module_key = "sales"`, which is what the
  per-report module check (5.3 extension 1) reads.
- **The `sales` Postgres schema holds no view, function or copy of core data.** Reports read
  `public` tables through the kernel; nothing is mirrored.

### R4.4 Round 3 reconciled with these answers

Round 3 was written before 07:16Z. Where it and the rulings meet:
- **The WhatsApp file rule (round 3 R3.1 "Round 2 Q1 to Q4" bullet, 0.6, 5.5):** superseded by
  Q2. There is no shape rule; every answer is text + file (R4.2). Round 3's AC-R3-6 ("Debtor type
  or product brand?") is a clarify question and carries no file.
- **Scheduled sends (R3.3, S6):** already text then file on WhatsApp and a file by email, which is
  Q2's rule, so the window-open path is unchanged. The window-closed path (Q6) had the template
  text only, which breaks "always text + file"; R4.6 re-recommends it. The period (Q8) and where
  it is set up (Q7) are unchanged recommendations.
- **Module (R3.2):** round 3's re-answer is now the ruling (Q5). One correction: R3.2 and S6
  named `modules/sales/purge_tables.json`; the file is
  `sorento_crm_frontend/modules/sales/purge_tables.json` (the `modules/projects/` precedent),
  and R4.3 adds `CREATE SCHEMA IF NOT EXISTS sales` to the module-creating lane rather than only
  the first table's lane.
- **Round 3's Q1 to Q4 "recommendations stand"** (R3.1): now rulings, with no change of content.

### R4.5 Slices, round 4: reconciled list, with parallel lanes

Each slice is its own lane and PR (one lane = one branch = one PR). The waves are round 3's; what
round 4 changes inside each slice is in the last column.

| Wave | Slice | Track | Needs merged first | Runs beside | Round 4 change |
| --- | --- | --- | --- | --- | --- |
| 0 (now) | **S0** measure on the prod copy (captain, no code, no PR) | none | nothing | S4, S1 | none |
| 0 (now) | **S4** Mocha's AutoCount SO feed (ESB, mostly outside this repo) | full | nothing | S0, S1, wave 2 | none |
| 1 | **S1** dataset, **Yearly comparison**, kernel extensions with `sheet_per` (Q1), Export to Excel, `crm_sales_analysis`, the chatbot text + file on every answer (Q2); creates the `sales` module **and schema** if #1260 S6 has not (Q5) | full | nothing | S0, S4; S3's migration and ingest half | Q1 ruled; Q2: no shape rule, every answer text + file, pending line "The Excel follows here."; Q5: `CREATE SCHEMA IF NOT EXISTS sales` |
| 2 | **S2** Sales report: person (pre-fill, Q3), set apart, exclude, quarter, Sorento's shared views (Q4), project orders included | full | S1 | S3, S6 | Q3 and Q4 ruled; chatbot goldens text + file |
| 2 | **S3** debtor type and the product brand axis | full | S1 for the dataset half | S2, S6 | chatbot goldens text + file |
| 2 | **S6** weekly scheduled Excel by email or WhatsApp, `sales.report_subscriptions` in the `sales` schema (Q5) | full | S1 | S2, S3 | Q5 ruled; window-closed path per Q6 (R4.6) |
| 3 | **S5** Mocha on the Sales report | small fix | S2, S3, S4 | nothing | chatbot golden text + file |

- **Parallel lanes today:** S0, S4 and S1 at once (three lanes). S3's migration and ingest half
  may open beside S1 in wave 1 and finish in wave 2. After S1 merges: S2, S3 and S6 at once
  (three lanes; S3 and S6 both carry a migration, so the pre-PR gate re-parents each before its
  merge). S5 last.
- **Beside #1260:** #1260 S6 (its first lane) and this S1 both may create the `sales` module and
  schema; the second to land reuses the files and its `CREATE SCHEMA IF NOT EXISTS` is a no-op.
  This S6 and #1260 S5 share `next_run_for`; the first to land writes it.
- **Track check:** no slice moves track. S1 stays full (slug, company parameter, module check);
  S5 stays under 300 lines (its golden gains one attachment assertion).
- Round 5: superseded by R5.5. S6 is now the weekly Sales report email as an automation (Owner
  ruling 26 Sep 07:34 Q6 and Q7): no `sales.report_subscriptions`, no WhatsApp, no shared
  `next_run_for` with #1260 S5.

### R4.6 Open questions (Q6 to Q8 from round 3, and one new)

- **Q6. A scheduled WhatsApp send outside the 24 hour window, under "always text + file".**
  Respond.io cannot send a file outside the window: `send_chat_attachment_for` refuses
  `attachment_window_closed` up front (`app/services/respond_chat_template_service.py:682-720`),
  and only text falls back to an approved template. Options:
  - (a) the template text with the totals line and "The Excel is in your My Downloads" (round 3's
    recommendation; text without the file on WhatsApp, the file by email and in My Downloads);
  - (b) **the template text with the totals line, ending "Reply to this message and the Excel is
    sent here."; the person's reply opens the window and the file is pushed once**;
  - (c) skip WhatsApp that week and send by email only.
  **Recommend (b):** it is the only option where the WhatsApp recipient still gets both text and
  file. Mechanism, reused: the send writes the `report_xlsx` My Downloads row with
  `deliver_to_contact_id` set and `delivered_at` null (the columns the low stock hand-off already
  uses, `app/models/download.py:56-60`); the contact's next chat turn (`POST
  /api/v1/external/chat/turn`, `app/api/v1/external/chat.py:171`) enqueues the existing one-shot
  push (`_claim_chat_delivery`, `export_tasks.py:911-927`) for any such row of that contact, so
  the file goes once and a retried job sends nothing. The reply is also answered as a normal
  message. A row not claimed within 7 days is left in My Downloads and not pushed later. One
  approved Meta template, `sales_report_scheduled`, as round 3 had.
  - Owner ruling 26 Sep 07:34 Q6: "we don't scheduled send whatsapp". None of (a) to (c): a
    scheduled send never goes by WhatsApp, and the mechanism above is withdrawn (R5.2).
- **Q7. Where a scheduled send is set up.** (a) on the report screen, Views menu > Scheduled
  sends, for the view on screen; (b) on the person's contact record, beside #1260's Sales updates
  tab. **Recommend (a)**, unchanged: a send is one view plus people, and the view lives on the
  report screen.
  - Owner ruling 26 Sep 07:34 Q7: "we can use our scheudled task and automation". Neither (a)
    nor (b): an automation on System Management > Automation, run by `automation_runner` (R5.3).
- **Q8. The period of a scheduled file.** (a) this year to date, as at the send date; (b) the
  period saved in the view. **Recommend (a)**, unchanged: your PDFs are "as at" the day they are
  made.
  - Owner ruling 26 Sep 07:34 Q8: "okay", (a).
- **Q9 (new). How wide the text part of an answer may be.** With text + file on every answer, the
  text of a wide table (for example agent x debtor type: 61 agents x 7 accounts) is one long line
  per agent. Options: (a) every column on every line (`SEAN: 48,210 | 12,400 | 7,980 | ...`) under
  a column name line; (b) past 4 columns, each line carries the row total only and the columns
  are in the Excel. **Recommend (a):** it follows "no cutoff", the file carries the same table in
  columns, and n8n chunks the length.
  - Round 5: not answered on 26 Sep 07:34; (a) stands (R5.6).

## R3. Round 3: the owner's grill answers (26 Sep 07:06Z)

The owner answered G1 to G10 inline on PR #1269 (comment 5844136277, 26 Sep 07:06Z, quoting "##
Grill questions"). The reply is binding. Each answer has one ruling line here, repeated where it
changes the UAC and the mockup. Nothing earlier in this file is deleted: sections 0 to 10 keep
their round 2 text, and a "Round 3:" note marks each place a ruling changes.

### R3.1 Ruling lines

- **Owner ruling 26 Sep 07:06 G1**: the owner kept only option (c) and the recommendation, "(c)
  either one, chosen on the screen, with a default ... Recommend (c), with delivered as the
  default ... The basis is printed on every header." Applied: the Basis filter (Ordered or
  Delivered) is on both screens, the chatbot and the scheduled send; the default is Delivered; the
  basis line prints on every header, export and reply. The reply dropped option (d) and the 2
  percent tolerance, so no figure is ruled: the invoice feed trigger (section 4, section 7) now
  reads "the owner asks for it after reading S0's measured gap".
- **Owner ruling 26 Sep 07:06 G2**: "Options: the Mocha company (AutoCount db2)". Applied: Mocha
  is the company. Connecting Mocha's AutoCount sales order feed (S4) stays a prerequisite for
  Mocha's figures (S5).
- **Owner ruling 26 Sep 07:06 G3**: "Recommend (a) ... Also keep (d) as a separate 'by product
  brand' axis ... - agree". Applied: `customers.debtor_type`, AutoCount `Debtor.DebtorType`
  stored raw (S3); plus a **Product brand** dimension and filter on the dataset (the category
  prefix, `product_class_signal.py:30-37`), usable on screen through Configure summary and a
  shared view "By product brand", and in the chatbot. Both land in S3.
- **Owner ruling 26 Sep 07:06 G4**: kept "(a) the sales order's dealer or project class" and
  answered "does report B's per-agent table include project orders, or dealer orders only? -
  include". Applied: DEALER and PROJECT TEAM are `demand_class` retail and project. The Sales
  report's per-agent views (By account, By month, By quarter) carry no Channel filter, so they
  count dealer and project orders together, as the round 2 mockup already drew ("Dealer,
  Project"). The "Dealer vs set apart" view keeps Channel = Dealer, because that table is named
  "DEALER - SALESMAN" in the PDF.
- **Owner ruling 26 Sep 07:06 G5**: "Recommend (a) ... - agree". Applied: total variance = this
  year to date minus the same months of last year; months after the as-at date blank (kernel
  extension 4, AC-S1-10).
- **Owner ruling 26 Sep 07:06 G6**: "okay, date i think assume is ok". Applied: (b). No period in
  the message = the current calendar year, printed in the header; ask only when the words are
  ambiguous (AC-S1-22).
- **Owner ruling 26 Sep 07:06 G7**: "what's this quesiton for". Not a ruling. The recommendation
  (a) stands (a shared saved view holds the customer list), and the question is asked again in
  plain words in the Round 3 comment (R3.5).
- **Owner ruling 26 Sep 07:06 G8**: "Recommend (c) for now ... - ok". Applied: footnotes are typed
  into the Excel after export; nothing is stored in the CRM. Trigger for (b): the owner asks for
  notes kept in the CRM.
  - Owner ruling 26 Sep 07:06 G8: (c) for now, "ok".
- **Owner ruling 26 Sep 07:06 G9**: "build both in this plan". Applied: (a) on-demand export is
  S1 and S2 (My Downloads, unchanged) and (b) **a weekly scheduled Excel sent to named people by
  email or WhatsApp is a slice of this plan now, S6** (R3.3). No deferral.
- **Owner ruling 26 Sep 07:06 G10**: "(a) is fine". Applied: one slug `sales.reports.view`,
  granted by role; on WhatsApp staff only with the existing Sales report grant (reveal key
  `sales_orders.sales_report`). Agents seeing only their own rows is not built; trigger: agents
  are given access. The scheduled send re-checks the slug and the company grant of every
  recipient at every send (R3.3).
- **Round 2 Q1 to Q4**: not answered. Their recommendations stand: Q1 (b) both channel blocks in
  one Excel; Q2 the file or text rule with the cut at 12 figures; Q3 person labels pre-filled
  from the code's name part; Q4 sections as shared saved views.
  - Round 4: answered at 07:16Z. Q1, Q3 and Q4 "okay" (rulings, content unchanged); Q2 ruled the
    other way, "always text + file, no cutoff", so the 12 figure rule is withdrawn (R4.2).
- **Round 2 Q5 changed, to align with #1260 round 5.** The sales targets plan
  (`PLAN-sales-targets-opportunities-26sep.md` on `claude/sales-targets-opportunities-plan-7behob`,
  round 5 header and 3.7 "Module and schema (round 5)") now recommends the `sales` module **with
  its own `sales` Postgres schema** for every new table (`sales.teams`, `sales.targets`, ...,
  `sales.update_subscriptions`), on the owner's ADR-0011 precedent for Project Sales. This plan
  follows it (R3.2).
  - Round 4: Owner ruling 26 Sep 07:16 Q5, "we need a sales schema and a sales module so we are
    more modular": the alignment is now the ruling (R4.3).

### R3.2 Module and schema, aligned with #1260 (replaces the "no sales schema" half of 0.2)

- **Same module:** `sales` (`app/modules/sales/bootstrap.py`, `MODULE_MANIFEST` entry, `"sales":
  "sales"` in `permission_module_map.py`, routers under `/api/v1/sales/`, the Sales menu group).
  #1260 S6 is that plan's wave 1 and creates it; if this plan's S1 lands first, S1 creates the
  same files with the same content and #1260 S6 reuses them (unchanged from round 2).
- **Same schema for every new table:** this plan's one new table, the scheduled send
  subscriptions of S6, is `sales.report_subscriptions`, in the `sales` schema, named without the
  `sales_` prefix as #1260's map does (`sales.update_subscriptions` is its neighbour). Whichever
  lane creates the first `sales.*` table runs `CREATE SCHEMA IF NOT EXISTS sales` (idempotent, so
  both plans' migrations carry it); `modules/sales/purge_tables.json` gains
  `sales.report_subscriptions`, and uninstall purges its rows through the ORM and never drops the
  schema (ADR-0011). Raw SQL names the schema.
  - Round 4 (Owner ruling 26 Sep 07:16 Q5): ruled. The purge file is
    `sorento_crm_frontend/modules/sales/purge_tables.json` (the `modules/projects/` precedent), and
    the module-creating lane also runs `CREATE SCHEMA IF NOT EXISTS sales` (R4.3).
- **Saved views stay the kernel's `report_views` rows** (`app/models/report_view.py:21-22`,
  `public`). They are a kernel table shared by every report (the sponsorship report's views live
  there too), not a sales table, so moving them would move procurement's views. A saved view is a
  row, not a new table.
- **Core data stays in `public`:** `sales_orders`, `sales_order_lines`, `sales_agents`,
  `customers` (and its new `debtor_type` column, S3). #1260 3.7 says the same ("Not recommended:
  a `sales` schema holding `sales_agents` or `sales_orders`").
- Round 2's AC-R2-6 ("no new table and no `sales` schema") is superseded by AC-R3-1 and keeps its
  text with a Round 3 note.

### R3.3 S6: the weekly scheduled Excel, by email or WhatsApp (G9 (b))

Round 5 (Owner ruling 26 Sep 07:34 Q6 and Q7): this section is superseded by R5.2 and R5.3 and
kept as round 3 wrote it. The send is email only (no WhatsApp) and is an automation on System
Management > Automation run by `automation_runner`; the table, the dialog, the routes, the slug and
the runner handler below are withdrawn. What carries over: the period (Q8, ruled), the access
re-check at every send (G10, now `recipient_check`), the kernel export under the view's company,
and the text header.

**Does #1260 S5's per-recipient schedule fit? Its schedule does; its table does not.** Measured on
the #1260 plan (3.6, S5): `sales.update_subscriptions` is one row per **WhatsApp contact** per
**agent, dealer or team followed**, and its content is a target progress text built by
`render_progress_message`. This send is a **report view** to a **CRM user** by **email or
WhatsApp**, and its content is a file. Bending that table would add a follows kind, a view id and
a channel column that no target row uses, and would tie this slice to #1260's last-merging lane
(its wave 3, S5 last). So S6 has **its own table and slice**, and reuses the schedule parts that
do fit:

- `next_run_for(...)` (the weekly extension of `_next_run_for_daily`,
  `app/services/automation_service.py:50`) in `app/services/sales/schedule.py`: whichever of this
  S6 and #1260 S5 lands first writes it, the other imports it.
- The runner shape: one seeded `scheduled_tasks` row that dispatches every due row
  (`automation_runner`, `app/scheduler/task_scheduler.py:335-337`, and #1260 S5's
  `sales_target_broadcast_runner`), worker only (`ENABLE_SCHEDULER`, `worker.py:74-80`).
- The idempotency rule: `next_run_at` advanced in the same transaction as the `integration_log`
  row; a row with a success log since its previous scheduled time is skipped.

**Table `sales.report_subscriptions`** (S6 migration; `CompanyScopedMixin`):

| column | type | note |
| --- | --- | --- |
| `id`, `company_id` | | the company the file is for (the view's Company filter) |
| `report_view_id` | uuid FK `report_views` ON DELETE CASCADE, not null | a **shared** view of `sales` or `sales_yearly` |
| `user_id` | FK `users` ON DELETE CASCADE, not null | the named person |
| `channel` | varchar(16) not null, check `email` or `whatsapp` | one row per channel |
| `weekday` | smallint not null default 0 | 0 Monday to 6 Sunday |
| `send_time` | time not null default 09:00 | |
| `timezone` | varchar(64) not null default `Asia/Kuala_Lumpur` | |
| `enabled` | bool not null default false | ships off; the owner turns a row on after one Send now |
| `next_run_at`, `last_sent_at` | timestamp null | runner bookkeeping |
| `created_by`, `created_at`, `updated_at` | | |

Unique `(report_view_id, user_id, channel)`. Weekly only, because the owner asked for "produced
weekly"; trigger for daily or monthly: the owner asks (the shared `next_run_for` already takes a
frequency).

**The send, per due row** (runner handler `send_sales_report_subscriptions`, RQ, worker):
1. **Access, re-checked every time:** the user is active, holds `sales.reports.view` and is
   granted the row's company. Otherwise nothing is built or sent, the row is skipped with an
   `integration_log` failure `no_access`, and `next_run_at` still advances (G10).
2. **The file:** a `report_xlsx` My Downloads row owned by the user (the kernel's export,
   `app/api/v1/reports/reports.py:238-253`), built by `generate_report_xlsx` under the row's
   company, with the view's saved filters and pivot and the period replaced by **1 January of the
   send date's year to the send date** ("as at" the send date, Q8). The file also sits in the
   user's My Downloads.
3. **Email:** `email_outbox_service.enqueue(event_key="sales_report_scheduled", to=user.email,
   subject="<view name>, as at <dd/mm/yyyy>", body_text=<the text header>,
   attachment_storage_provider=..., attachment_storage_key=<the download's file>)`
   (`app/services/email_outbox_service.py:70-90`), with the event registered in
   `email_event_registry.py`. A user with no email is skipped and logged.
4. **WhatsApp:** to `users.respond_contact_id` (`app/models/user.py:63`); a user with no linked
   contact, or a contact with `outbound_enabled` false, is skipped and logged.
   - **Window open:** the text header, then the file through `send_chat_attachment_for`
     (`app/services/respond_chat_template_service.py:682`), the low stock report's path.
   - **Window closed:** Respond.io has no attachment-carrying template (`:697-720`, it refuses
     with `attachment_window_closed`), so the file cannot go. The row sends the approved template
     text through `send_text_or_template` with use case `sales_report_scheduled` (header, totals
     line, "The Excel is in your My Downloads.") (Q6). The Meta template approval is an ops gate
     in S6's DoD, as in #1260 S5.
   - Round 4 (Owner ruling 26 Sep 07:16 Q2, "always text + file"): the window-closed text alone
     no longer meets the rule. Q6 is re-recommended as (b): the template text ends "Reply to this
     message and the Excel is sent here.", the download row is written with
     `deliver_to_contact_id` set, and the contact's next chat turn triggers the one-shot push
     (R4.6).
   - Round 5 (Owner ruling 26 Sep 07:34 Q6, "we don't scheduled send whatsapp"): step 4 is
     withdrawn whole. A scheduled send is email only (R5.2).
5. **The text header** is the chatbot's (0.6): report and view name, company, basis, period "as
   at", the totals line. No UUID.

**Screens** (S6 frontend; one new dialog, no new page):
- `ReportViewsMenu` gains **Scheduled sends** for the active shared view, shown to holders of the
  new slug `sales.reports.schedule` (granted to admin and superadmin in S6's migration, others by
  role). It opens **Scheduled sends: <view name>**, a modal with one row per subscription (Person,
  Channel, When "Mon 09:00", Enabled switch, Next send, row actions Send now and Delete) and
  **Add**: Person (`SearchableSelect` of users holding `sales.reports.view` in the view's company),
  Channel (`SearchableSelect`: Email, WhatsApp), Day (`SearchableSelect`), Time. Where it is set
  up is Q7.
- Delete is the deferred hard delete (countdown with Cancel, no confirm), as every delete.
- Send now runs one send for that row in a worker job and toasts "Sending <view name> to
  <person>"; the result is in the row's Last sent.
- Routes `GET|POST /api/v1/sales/report-subscriptions`, `PATCH|DELETE .../{id}`, `POST
  .../{id}/send-now`, behind `require_module_enabled_with_api_key("sales")` and
  `sales.reports.schedule`; a view that is not shared, not a sales report, or another company's is
  422 / 403.
- Round 5 (Owner ruling 26 Sep 07:34 Q7): the screens and routes above are withdrawn. The send is
  set up on the existing Automation screen (trigger "Sales report view", Report view, Weekly, Day,
  Time, recipients), gated by the existing `automation.automations.*` slugs (R5.3).

### R3.4 Slices, round 3: every slice in scope now, ordered for early value, with parallel lanes

Each slice is its own lane and PR (one lane = one branch = one PR), Phase 1 frontend mock first,
Phase 2 tester-first, Phase 3 reviewer plus browser at 1280 and 375. Nothing waits on a further
ruling; G7 and Q6 to Q8 only change details inside S2 and S6.

| Wave | Slice | Track | Needs merged first | Runs beside | What the owner gets |
| --- | --- | --- | --- | --- | --- |
| 0 (now) | **S0** measure on the prod copy (captain, no code, no PR) | none | nothing | S4, S1 | the SO vs PDF gap (G1), the raw debtor types (S3), the person split (S2) |
| 0 (now) | **S4** Mocha's AutoCount SO feed (ESB, mostly outside this repo) | full | nothing | S0, S1, wave 2 | Mocha's orders arrive in the CRM |
| 1 | **S1** the dataset, **Yearly comparison**, the kernel extensions (with Q1's `sheet_per`), on-demand Export to Excel (G9 (a)), `crm_sales_analysis` and the chatbot file | full | nothing (creates the `sales` module if #1260 S6 has not) | S0, S4; S3's ingest half | report A on screen, in Excel and on WhatsApp |
| 2 | **S2** the **Sales report**: agent as a person, set apart, exclude, quarter, Sorento's shared views, project orders included (G4) | full | S1 | S3, S6 | report B for Sorento |
| 2 | **S3** debtor type (the account columns) **and the product brand axis** (G3) | full | S1 for the dataset half; its migration and ingest half can start in wave 1 | S2, S6 | the SORENTO..SAMPLE columns; "by brand" |
| 2 | **S6** the weekly scheduled Excel by email or WhatsApp (G9 (b)), `sales.report_subscriptions` | full | S1 | S2, S3 | Yearly comparison sent weekly at once; Sales report views as soon as S2 publishes them |
| 3 | **S5** Mocha on the Sales report: Mocha's shared views, "Mocha Q2 by agent without Dilooma" | small fix | S2, S3, S4 | nothing | report C |

- **Why this order:** S1 is the earliest value (report A needs no missing data) and holds every
  shared piece (dataset, kernel, module). Wave 2's three lanes touch different files: S2 and S3
  each add dimensions to `datasets/sales_order_lines.py` (a trivial merge), S6 adds a table,
  a runner and one dialog. S5 is configuration and closes the plan.
- **S6 beside #1260:** S6 does not wait on #1260 S5; if #1260 S5 lands first, S6 imports its
  `next_run_for`, otherwise S6 writes it (R3.3).
- **Merge order in wave 2:** as each is ready; the pre-PR gate (`./scripts/alembic-reparent.sh`,
  single head) runs before each merge because S3 and S6 both carry a migration.

### R3.5 G7 in plain words (asked again)

In your Sorento report, HANLIM is shown as its own row, apart from the salesmen. In your Mocha
report, DILOOMA and PINTAR are left out of the "without project" total. The question is how the
system knows which customers to set apart or leave out: **recommend (a)**, a customer list saved
with the report view, which an admin edits on the screen. Also: are there other customers you set
apart the way you do HANLIM?

### R3.6 Round 3 questions (at most 3, each with a recommendation)

- **Q6. WhatsApp outside the 24 hour window.** Respond.io cannot send a file to someone who has
  not messaged in the last 24 hours. Options: (a) send the approved template text with the totals
  line and "The Excel is in your My Downloads"; (b) skip the WhatsApp send and email only.
  **Recommend (a).**
  - Round 4: re-asked under Owner ruling 26 Sep 07:16 Q2; the new recommendation is the reply to
    fetch option (R4.6).
  - Round 5: Owner ruling 26 Sep 07:34 Q6, "we don't scheduled send whatsapp": no scheduled
    WhatsApp at all (R5.2). Q7 and Q8 below: ruled in R5.1.
- **Q7. Where a scheduled send is set up.** Options: (a) on the report screen, in the Views menu,
  for the view being sent; (b) on the person's contact record, beside #1260's Sales updates tab.
  **Recommend (a)**: a send is a view plus people, and the view lives on the report screen.
- **Q8. The period of a scheduled file.** Options: (a) this year to date, as at the send date; (b)
  the period saved in the view, unchanged. **Recommend (a)**: your PDFs are "as at" the day they
  are produced, and a saved fixed range would send the same old figures every week.

## 0. Round 2: the owner's Lavish review (26 Sep 06:27Z)

The ten notes are verbatim in PR #1269 (comment starting "Owner Lavish review of the retail sales
reports mockup"). Each has one ruling line here, and the same line is repeated where it changes
the UAC and the mockup.

- **Owner ruling 26 Sep 06:27 (Lavish) 1**, on the yearly comparison header: "see if we can reuse
  our reporting module component ... I don't want too many new componetns". Applied: both
  reports are `ReportPage` screens driven by kernel definitions (0.1). The round 1
  `SalesReportPage`, `SalesReportDocument`, `sales_grid` and `sales_reports_layout.py` are
  dropped (table in 0.1).
- **Owner ruling 26 Sep 06:27 (Lavish) 2**, on "Menu under Sales > Reports": "is this under
  sales module and sales schema? our sponsorhisp form got a reporting function, see if we can
  reuse that". Applied: the sponsorship report's function **is** the reports kernel, and it is
  reused whole (0.1). Module and schema answer in 0.2.
- **Owner ruling 26 Sep 06:27 (Lavish) 3**, on the Sorento by account header: the same two asks
  as notes 1 and 2. Applied: as 1 and 2.
- **Owner ruling 26 Sep 06:27 (Lavish) 4**, on "Mocha sales report by account": "same like
  sorento report ... don't call it sorento by account la, call it sales report ... applicable in
  both companies". Applied: one report, **Sales report**, with **Company** as a filter. Report B
  (Sorento by account) and report C (Mocha by account) are the same definition; their sections
  become shared saved views of it. Report A stays **Yearly comparison**, the same for both
  companies, also with the Company filter (0.3).
- **Owner ruling 26 Sep 06:27 (Lavish) 5**, on the Excel export: "make sure the downloading
  follows our principle of putting in my downloads". Applied: Export to Excel is the kernel's
  export, which already queues the workbook and lands it in **My Downloads** (0.4). The round 1
  sync streamed `/export` route is dropped.
- **Owner ruling 26 Sep 06:27 (Lavish) 6**, on the WhatsApp dealer year comparison: "i expect
  the reprot to be sent instead of text, just like lowstock report". Applied: that answer is an
  Excel file with a short text header, sent the way the low stock report is (0.6).
- **Owner ruling 26 Sep 06:27 (Lavish) 7**, on "Sales by account ... SEAN": "this one okay can
  send text". Applied: text (0.6).
- **Owner ruling 26 Sep 06:27 (Lavish) 8**, on "Mocha sales by agent": "thsi one also can send
  text". Applied: text (0.6).
- **Owner ruling 26 Sep 06:27 (Lavish) 9**, on "SEAN I or SEAN III?": "i expect is both, sean.1
  and sean iii are saem person". Applied: an agent is a **person**. When a person label exists,
  the bot never asks which code; it sums every code under the label and names the codes in the
  header (0.5).
- **Owner ruling 26 Sep 06:27 (Lavish) 10**, on the chatbot frame: "we need to gauge whether to
  send a file or text, or we should do text + files so available in differne formats?" Applied:
  a rule by answer shape, with a recommendation on the default (0.6, question Q2).

### 0.1 What is reused (notes 1, 2, 3), and what is still new

**The reporting module is the reports kernel**, built for the sponsorship report
(`PLAN-reporting-foundation`, archived under `documentation/plans/_archive/reports/`). It is
generic: every route, hook and component takes a report key, and "report #2 is a two-line route
wrapper over this file" (`components/reports/ReportPage.tsx:265-269`).

The sponsorship form's reporting function, and what each piece gives these reports:

| Piece (the sponsorship report uses it today) | file:line | Reused for |
|---|---|---|
| The page: `ReportPage({ reportKey, breadcrumb })` | `components/reports/ReportPage.tsx:270-276` | Both screens. Header actions (views menu, Configure summary, Export to Excel) :439-465; filter bar :515-520; capped warning :524-541; empty state with "Reset to report default" :563-581; Detail and Summary line tabs :584-646 |
| Route wrapper page | `app/(protected)/procurement-management/sponsorship-forms/report/page.tsx:3,17-24` | Copied twice with a new key; it is the only new frontend file per report |
| Filter bar: date basis, period (year, month chips, custom), selects | `components/reports/ReportFilterBar.tsx:134-144`, `MonthChips` :81 | Company, Channel, Basis, Sales agent, Debtor type, Set apart, Exclude customers, Period |
| Pivot table: one row dimension x one column dimension, row, column and grand totals, first column pinned, sideways scroll | `components/reports/ReportPivotTable.tsx:46` | Every table (year x month, agent x debtor type, agent x quarter) |
| Saved views: Mine and Shared, publish, set default (`reports.views.publish`) | `components/reports/ReportViewsMenu.tsx:34-46`; `app/services/reports/views_service.py:53-77` | Report B's and C's sections become shared views (0.3); the HANLIM set and the named project debtors are stored in a view (G7) |
| Configure summary: rows, columns, measures from the catalogue | `components/reports/ConfigureSummaryDialog.tsx:17-29` | Any other cut the owner wants, with no code |
| Detail tab: a `DataGrid` of the lines with a totals footer | `ReportPage.tsx:598-641` | The sales order lines behind every figure (the drill the PDFs cannot give) |
| Export: `POST /reports/{key}/export` creates a `report_xlsx` My Downloads row and queues `generate_report_xlsx` | `app/api/v1/reports/reports.py:216-261`; `app/tasks/report_export_tasks.py:32`; `hooks/useReports.ts:108-123` | Export to Excel on both screens (note 5) and the chatbot file (note 6) |
| Workbook: title block, money cells, totals as values, a SUMMARY sheet plus one sheet per month of the period | `app/services/reports/xlsx_renderer.py:410-439`, `_render_summary` :306-408; `engine._month_sheets` :734-761 | The Sales report's per-month tables (report B's "Monthly" section) come out as the month sheets with no new code |
| Declaration: `Column`, `Dataset`, `DateBasisParam`, `PeriodParam`, `SelectParam`, `PivotLayout`, `WorkbookSpec`, `ReportDefinition` | `app/services/reports/registry.py:71-99, 111-150, 156-187, 257-260, 263-290, 296-308` | One new dataset, two new definitions |
| Engine: params, predicates, `_pivot` (SUM, `(blank)` bucket, 5,000 cell cap) | `app/services/reports/engine.py:156-221, 227-236, 529-620, 47-48` | Every figure on the screens, the exports and the chatbot (one query layer) |
| Routes: catalogue, meta, run, export, views | `app/api/v1/reports/reports.py:133, 148, 184, 216, 267-343` | No new report route |
| Frontend service and hooks | `services/reportService.ts:267-356`; `hooks/useReports.ts:32-123` | Unchanged |

`QueryContext.values` (`engine.py:235`) already carries the resolved filter values to every
column expression, so a "set apart" dimension (HANLIM as its own row) is a dataset column, not an
engine change.

**Round 1 pieces dropped:** `sales_analysis_service.sales_grid`, `sales_reports_layout.py` and
its `SalesReportDocument`, the `SalesReportPage` component, the routes `GET /sales-reports/{key}`
and the sync `/export`, and the per-report line tabs. The round 1 argument for a separate
primitive (section 5.1 of round 1: "`_pivot` binds to one definition with one period ... the
shapes are reused, the engine is not") is withdrawn: the owner asked for the kernel, and the
gaps below are small enough to close in it.

**What is still new, and why** (the whole list):

| New | Kind | Why nothing existing does it |
|---|---|---|
| `app/services/reports/datasets/sales_order_lines.py` | dataset (backend) | The kernel has one dataset, sponsorship forms. The sales order lines, with the sales report's money rule (`sales_report_service._per_line_exprs` :198-220 and `_common_filters` :236-271 imported, not copied) |
| `definitions/sales.py`, `definitions/sales_yearly.py` | two definitions (backend) | One per report: "Sales report" and "Yearly comparison" |
| `app/(protected)/sales/reports/page.tsx`, `app/(protected)/sales/yearly-comparison/page.tsx` | two route wrappers (frontend) | Each is the sponsorship `page.tsx` with a new key |
| `ReportPivotChart` (`components/reports/ReportPivotChart.tsx`) | **the one new component** | The yearly comparison's line chart under the table. Nothing draws a chart from a `ReportPivotLayout`; it wraps the existing recharts wrapper `components/ui/chart.tsx:5`, as `SLAKpiDashboardContent.tsx:15` does |
| Variance row: `PivotLayout.variance = "last_two_rows"`, `ReportPivotLayout.variance_row`, one extra row in `ReportPivotTable` and `_render_summary` | kernel extension | The kernel only derives row, column and grand totals (`engine.py:551-575`); the PDF's VARIANCE row is a difference |
| Fixed column values on a `Column` (`fixed_values`, JAN to DEC) | kernel extension | A month-of-year axis must print twelve columns even when October to December have no sales yet; `_pivot` lists only the values present unless the axis is the period's own `YYYY-MM` months (`engine.py:578-585`) |
| `PivotLayout.chart = "line"` and a native openpyxl `LineChart` under the summary | kernel extension | The PDF prints a chart under each block; the renderer writes none today |
| `WorkbookSpec.month_sheets: bool` (default true) | kernel extension | A three-year yearly comparison must not write 33 month sheets (`engine._month_sheets` :734-761) |
| `WorkbookSpec.sheet_per` (one sheet per value of a filter) | kernel extension, only if Q1 is (b) | Both channel blocks in one file |
| `ReportDefinition.module_key` and a per-report module check | kernel extension | The reports router sits under the procurement guard "while the sponsorship report is the only one ... it moves when a second module owns a report" (`app/api/v1/__init__.py:234-241`, `reports.py:7-9`). This is that trigger |
| The company arm made fail-closed, and the company passed into the export job | kernel fix | The TODO says to do it "the day a dataset declares scope='company'" (`engine.py:311-315`); this dataset is the first. The export task has no enqueuer company (`report_export_tasks.py:39-45`) |
| `GET /api/v1/sales/analysis` and MCP tool `crm_sales_analysis` | chatbot seam | The MCP server wraps backend GETs; the kernel's run is a POST. The route calls `engine.run` for the `sales` definition, so the screens and the bot share one query |
| The chatbot report file sender | chatbot seam | Generalises the low stock push (`app/tasks/export_tasks.py:1003-1089`) from one kind to the kernel's `report_xlsx`. The second case pays for the generalisation (CLAUDE.md "Simplest thing") |

No new table, no registry, no rule engine.

### 0.2 Module and schema (note 2): "is this under sales module and sales schema?"

**How the codebase organises this, measured:**
- **Module keys** are `app/modules/<key>/bootstrap.py` files; there are 21 (dealer_kit, projects,
  automation, scm, audit, order, procurement, chatbot, notifications, public_view_links,
  inventory, activities, marketing, complaints, resources, base, product, tickets, sla, forms,
  email_templates). **No `sales` key exists.** Sales orders belong to `order`
  (`app/modules/order/manifest.py:4-24`), mounted at `/order-management` behind
  `require_module_enabled_with_api_key("order")` (`app/api/v1/__init__.py:66-71`). A slug prefix
  maps to a module in `app/modules/runtime/permission_module_map.py:17-35` (no `sales` entry).
- **Postgres schemas** are used by four modules only: `projects`
  (`alembic/versions/354_projects_schema_move.py:362`), `scm` (`273_scm_module_schema.py:42`),
  `dealer_kit` (`app/models/dealer_kit.py:42`) and `chatbot` (`472_chatbot_turns.py:46`).
  Everything else is in `public`, including `sales_orders` (`app/models/order.py:480`, no schema),
  `sales_order_lines`, `sales_agents` and `customers`. **No `sales` schema exists.**
- **Navigation**: `config/menu.config.tsx:78` is the `SALES` heading (Project Sales :80, Delivery
  Orders :142, Marketing :164). The sponsorship report is under OPERATIONS > Project Sales Admin
  (:620-624, `moduleKey: 'procurement'`). A child can carry its own `moduleKey` inside a group
  (`app/components/layouts/demo1/components/sidebar-menu.tsx:78-86`).
- **#1260 (sales targets, PR #1260, plan on `claude/sales-targets-opportunities-plan-7behob`)**
  creates the `sales` module: `app/modules/sales/bootstrap.py`, `"sales": "sales"` in the
  permission map, slugs `sales.*`, routers under `/api/v1/sales/*`, and a **Sales** group under
  the SALES heading with Targets, Opportunities, Sales Teams and Sales Agents (its section 3.7,
  :626-661, and :206-214). It keeps its tables in `public` by the uninstall test ("durable
  business records ... stay in `public`", its :11-13 and :676-677).

**Recommendation (Q5 confirms): Sales module yes, sales schema no.**
- **Module**: the two reports declare `module_key = "sales"`; the slug is `sales.reports.view`
  (the owner's "sales reports: view", G10); the chatbot route is `GET /api/v1/sales/analysis`
  behind `require_module_enabled_with_api_key("sales")`; the menu items **Sales report** and
  **Yearly comparison** sit in #1260's Sales group, after Sales Agents, with `moduleKey:
  'sales'`. Whichever lane lands first (#1260 S6 or this S1) creates
  `app/modules/sales/bootstrap.py`, the catalogue row and the permission map entry; the other
  reuses it. Reason: the owner reads these as sales functions next to targets and agents, and
  one Sales group matches #1260.
- **Schema**: this plan adds **no table** (the one new column, `customers.debtor_type` in S3,
  belongs on the customer's own table in `public`). The data stays where the `order` module keeps
  it. A `sales` Postgres schema would hold nothing, and #1260 already chose `public` for its own
  tables. Named trigger: if #1260 ever moves its tables to a `sales` schema, nothing here moves,
  because nothing here is a table.
  - Round 3: superseded by R3.2. #1260 round 5 now puts its tables in a `sales` schema, and this
    plan's S6 adds one table, `sales.report_subscriptions`, in the same schema. Saved views stay
    the kernel's `report_views` rows; core sales data stays in `public`.
- **Why not the `order` module**: round 1 put the routes under `order_management` because the
  `sales` module did not exist. Now that #1260 creates it and the owner asked, the reports go
  where the owner looks for them. The data stays owned by `order`; the report only reads it.

### 0.3 One Sales report for both companies (note 4)

- **Sales report** (`key = "sales"`), title "Sales report", replaces report B (Sorento by
  account) and report C (Mocha by account). **Company** is a single-select filter (the caller's
  granted companies, default the current company). Every section of both PDFs is a view of the
  same definition:

| Shared view | Rows x columns | From |
|---|---|---|
| By account (default) | agent (person) x debtor type | B monthly and year tables, C "by account" |
| By month | agent x month | C |
| Debtor type by month | debtor type x month | C |
| Dealer vs set apart | seller group (DEALER - SALESMAN, then each set-apart group) x month | B's DEALER - SALESMAN vs HANLIM table |
| By quarter | agent x quarter | C |
| Set apart customers by month | customer x month, filtered to the set-apart list | C's project debtors |

  A view stores its filters too, so each company's views carry its own set-apart list (HANLIM for
  Sorento) and exclusion list (DILOOMA, PINTAR for Mocha). The per-month tables of report B are
  the workbook's month sheets (0.1); on screen, the month chips of the period filter show one
  month.
- **Yearly comparison** (`key = "sales_yearly"`) stays report A: rows year, columns JAN to DEC,
  the variance row, the chart. The same for both companies, with the Company filter. The two
  blocks (DEALER, PROJECT TEAM) are the Channel filter; how both reach one Excel file is Q1.
- **Mocha before S4**: the Company filter lists Mocha as soon as the user is granted it. With no
  Mocha sales orders the page shows the kernel's empty state ("No sales orders for Mocha in this
  period"), never a hidden menu item. The round 1 rule "hide Mocha by account while
  `so_feed_live` is false" is withdrawn, because there is no Mocha-only menu item any more.

### 0.4 Downloads (note 5)

The principle is written as owner rulings, not in `PRINCIPLES.md`: "our export of the excel and
pdf needs to use My Downloads process, similar to other downloading buttons"
(`documentation/plans/scm/po-spo-site-pool-and-order-sheet-downloads-acceptance-criteria.md:6-7`,
10 Sep; AC-19 at :149-151 retires the browser blob save). The helper is the My Downloads flow:
- backend: `DownloadService.create(kind="report_xlsx")` then `enqueue_job(generate_report_xlsx)`
  (`app/api/v1/reports/reports.py:238-253`);
- frontend: `useReportExport` invalidates `MY_DOWNLOADS_QUERY_KEY` and toasts "`<file>` is being
  prepared in My Downloads" (`hooks/useReports.ts:108-123`); the drawer row downloads through
  `fetchDownloadUrl` (`services/myDownloadsService.ts:69-77`, `components/my-downloads/DownloadRow.tsx:87-98`).

Both reports use it unchanged. No `saveBlobAs`
(`app/(protected)/project-sales/_shared/services/fileDownload.ts:19-31`), which is the retired
pattern. The file name is the kernel's `<title>-<period>.xlsx` (`reports.py:201-213`), for
example `Sales report-JAN-SEP'26.xlsx`.

### 0.5 An agent is a person (note 9)

- A person is `COALESCE(person_label, sales_agent)` (`order_inquiry_header_service.py:105`), and
  every code sharing a label counts (#1260 R2, owner-confirmed on #1260 06:09Z: "yeah").
- **The bot never asks which code when a label exists.** "Sean" resolves to the label SEAN and
  every code under it; the header names them ("Sales agent: SEAN (SEAN I, SEAN III)").
- **When no label exists yet** (0 of 80 today), the bot groups codes by the name split the model
  already documents, "(name, I|III|IV)" (`app/models/sales_agent.py:56-59`): SEAN I and SEAN III
  are both SEAN, and are summed and named in the header. It asks only when the typed name matches
  two **different** names (for example "Tan" matching TAN KH and TAN WL).
- S2's backfill: the same split pre-fills `person_label` for every code with a roman suffix, and
  the owner corrects any on the Sales Agents screen (Q3).

### 0.6 Chatbot: file, text, or text + file (notes 6, 7, 8, 10)

**The rule, by the answer's shape** (counted after the query, before the reply):
- **Text** when the answer is a **list**: one value column (one figure per row), any number of
  rows. "Sean's sales this month by brand" (3 rows x 1) and "Mocha Q2 by agent" (8 rows x 1) are
  lists. Long lists are not the bot's problem: n8n chunks a long WhatsApp message (owner, PR
  #1258 26 Sep 05:32Z; the outstanding report D8, `PLAN-chatbot-outstanding-report.md:39`).
- **Text** when the answer is a **small grid**: two or more value columns and at most 12 value
  cells (rows x value columns), printed as `label: a | b | c` lines. Example: "HANLIM this year vs
  last year" (1 row x 3).
- **File** when the answer is a **grid over 12 value cells**: "compare dealer sales 2025 vs 2026
  by month" is 12 months x 3 columns = 36 cells, so it goes as the Excel workbook, the way the low
  stock report does. The file always comes with a short text header (company, channel, basis,
  period, the totals line, "Full table in the attached Excel"), because the low stock report's
  text "explains the files" (`app/services/chatbot/engine.py:4584-4587`).
- **Asked for a format**: "in Excel", "as a file" or "send the report" forces the file; "as text"
  forces text. That is the only override.

**Recommendation on the default (Q2): not text + file for everything.** A file costs a worker job,
up to a few seconds' wait and a 24 hour window check, and a three-line answer with an attachment
is noise. So lists and small grids are text only, grids are text header + file (which is "text +
file", where it helps), and any answer can be re-sent as a file on request.

Round 4 (Owner ruling 26 Sep 07:16 Q2, "always text + file, no cutoff"): the shape rule above and
the override words are withdrawn. Every answer is the whole answer as text plus the Excel file of
the same query; clarify questions and refusals carry no file; "as text" is dropped (R4.2). The
file path below is unchanged and now serves every answer.

**The file path, reused from the low stock report**: the route creates a `report_xlsx` My
Downloads row owned by the CRM user linked to the contact (as `low_stock_report.py:437-444,
470-476` does), enqueues the kernel's `generate_report_xlsx` on `imports`, waits up to the same
sync window (`low_stock_report.py:120-146`, capped at 7 seconds), and returns `attachments` when
ready (`:313-342`); the presenter passes them through and the engine adds `send_attachments`
after `send_message` (`app/services/chatbot/engine.py:4602-4614`). When it is not ready, the reply
says "Preparing the Excel, it will be sent here when ready." and the worker pushes the file
(`_push_low_stock_to_chat`, `app/tasks/export_tasks.py:1003-1089`, generalised to take the
download kind; `respond_chat_template_service.send_chat_attachment_for` :682, 24 hour window
:697-720). The file also lands in that user's My Downloads.

## 1. In plain words (for the owner)

You keep three spreadsheets by hand. The CRM already holds every Sorento sales order line that
AutoCount sends, with the customer, the sales agent code, the dealer or project class and the
amount. Round 2 builds them on the report screen you already have for sponsorship forms:

- **Yearly comparison** (was report A) can be built now: dealer or project sales per month for
  2024, 2025 and 2026, the variance row and a line chart. The same screen for Sorento and Mocha.
- **Sales report** (was reports B and C) is **one report for both companies**, with Company as a
  filter. Its tables (by account, by month, by quarter, HANLIM set apart, Dilooma and Pintar left
  out) are saved views you pick from the Views menu. It needs two things we do not hold today:
  the sales agent as a **person** (SEAN I and SEAN III are one person, Sean) and the **debtor
  type** of each customer, which AutoCount sends and the CRM throws away today.
- **Mocha** has no sales orders in the CRM yet: its AutoCount sales order feed was never
  connected. Mocha shows an empty report until it is (S4).
- **Export to Excel** lands in **My Downloads**, like every other export.
- **On WhatsApp**, short answers come as text; a table (such as dealer sales 2025 vs 2026 by
  month) comes as an Excel file with a short summary, like the low stock report.
  Round 4 (your Q2 answer): every answer comes as text and as the Excel file.
- Round 3 (G9): **every week**, the report views you choose are sent as an Excel file to the
  people you name, by email or WhatsApp (S6).

Every figure is a **sales order** figure, not an invoice figure: the CRM has no customer invoice
table. Your spreadsheets look like invoiced sales. Section 4 says what that means and G1 asks you
to choose.

The report screens and the chatbot both call **one** query (the report engine), so "Sean's sales
this month by brand" on WhatsApp and the Sales report screen can never disagree.

## 2. Measured facts (read only)

### 2.1 What already exists and is reused

**The sales report service** (`app/services/sales_report_service.py`):
- `sales_report(db, *, product_code, customer_query, customer_ids, channel, warehouse_codes,
  date_from, date_to, detail)` (:275). Ordered, confirmed and outstanding value and quantity per
  month, summed in SQL (docstring :11-31).
- `_per_line_exprs()` (:198-220) is the money rule this plan reuses unchanged:
  `confirmed_qty = LEAST(qty_delivered, qty_ordered)` (:202), `confirmed_value =
  ROUND(line_total * confirmed_qty / NULLIF(qty_ordered, 0), 2)` per line (:204-210), outstanding
  only while the SO and the line are `open` (:211-213), `ordered = confirmed + outstanding`
  (:214-215).
- `_common_filters(...)` (:236-271): cancelled SO and cancelled line excluded (:241-242), the
  bucket date not null (:246), `channel` dealer means `demand_class = 'retail'`, project means
  `'project'` (:264-267). It takes `bucket_expr` as a parameter, so a report can bucket by
  `sales_orders.order_date` without a second copy of the predicate.
- `_bucket_expr()` (:191-195) is `COALESCE(sales_order_lines.required_date,
  sales_orders.order_date)`: the sales report files a line under its **delivery** month.
- Company scope is applied by the ORM `do_orm_execute` listener (:50-54); `sales_orders` and
  `sales_order_lines` use `CompanyScopedMixin` (`app/models/base.py:92-118`).
- Route `GET /api/v1/order-management/sales-report` (`app/api/v1/order_management/orders.py:1576`),
  permission `order_management.orders.view` (:1646), per-contact reveal key
  `sales_orders.sales_report` re-checked for API-key callers (:1713-1726). No frontend screen
  calls it; it is chatbot only.
- MCP tool `crm_sales_report` (`sorento_crm_mcp/sorento_crm_mcp/catalog.py:725-764`), presenter
  `_sales_report` (`sorento_crm_mcp/sorento_crm_mcp/presenters.py:2352`), chatbot grant
  `_SALES_REPORT_GRANT` (`app/services/chatbot/lanes/business/__init__.py:66`).

**Top X hot selling** (plan PR #1175, S1 presenter PR #1258, S2 route + S3 MCP tool PR #1263, all
open, not on main). On branch `claude/top-selling-s2-s3-j8nhan`:
- `top_selling(...)` in `app/services/sales_report_service.py:574` and
  `current_year_window(today)` (:552); route `GET /api/v1/order-management/top-selling`
  (`app/api/v1/order_management/orders.py:1848`) on the same `sales_report_router` (:1580), with
  the dealer scope helper `_top_selling_dealer_scope` (:1809).
- MCP ToolSpec `crm_top_selling_report` (`sorento_crm_mcp/sorento_crm_mcp/catalog.py:766`),
  reveal key reused, no paging params, `related_tools=("crm_sales_report",)` (:800).
- Response carries `total_count` and whole-set `totals` from window functions, so a cut never
  changes the count (PR #1263 body). Filter `sales_agent_ids` on `sales_orders.sales_agent_id`.
- Owner rulings 26 Sep that this plan inherits verbatim (PR #1175 plan
  `documentation/plans/chatbot/PLAN-chatbot-top-x-hot-selling-24sep.md:19-56` on that branch):
  no "more" / "next" / "lagi" anywhere; the header states the full count; no N and a list too
  long for one message = state the count and ask how many; no default metric; clarify, never
  assume; dealer contacts forced to their own ledgers.
- On main the older counted-set answer still pages by 5 on "more" (`app/services/chatbot/lanes/
  business/answer.py:2620-2634`, AC-1534 in `chatbot-turn-rearch-acceptance-criteria.md:215`).
  This plan's tool never takes that path.

**The reporting kernel** (`PLAN-reporting-foundation`, archived under
`documentation/plans/_archive/reports/`):
- `app/services/reports/registry.py` (Dataset, params, `WorkbookSpec` :264-294),
  `engine.py` (`_pivot` :529: one row dimension x one column dimension x measures, row, column
  and grand totals, `BLANK_VALUE` :59, `PIVOT_CELL_CAP` 5000 :47), `xlsx_renderer.py`
  (`render_workbook` :410, `_title_block` :120, `_write_money` :98, totals written as values,
  never formulas, docstring :14-16), `views_service.py` (saved views).
- Wire schema `app/schemas/report.py`: `ReportPivotLayout` (:73-86) with `row_dim`, `col_dim`
  (values and value labels), `measures`, `row_values`, `cells`, `row_totals`, `col_totals`,
  `grand_total`.
- Frontend `components/reports/ReportPivotTable.tsx:46` renders a `ReportPivotLayout`;
  `ReportPage.tsx:270` (Detail + Summary tabs, Export to Excel :455-461); `ReportFilterBar.tsx:134`;
  `ReportViewsMenu.tsx:34`.
- Its company arm is not fail-closed yet: `engine._predicates` carries a TODO "make this arm
  FAIL-CLOSED ... the day a dataset declares scope='company'" (`engine.py:310-315`); the one
  dataset today is `scope="none"` (`datasets/sponsorship_forms.py:283`).

**Charts:** `recharts` 2.15.1 (`package.json:86`) with the wrapper `components/ui/chart.tsx:5`,
used by `app/(protected)/sla-management/kpi-dashboard/SLAKpiDashboardContent.tsx:15`.
`DESIGN-LANGUAGE.md` has no chart section.

**Sales targets plan** (PR #1260, `documentation/plans/sales/PLAN-sales-targets-opportunities-
26sep.md` on `claude/sales-targets-opportunities-plan-7behob`): the same source and the same two
bases (ordered = `line_total`, delivered = confirmed value, its section 2); R2 recommends a person
counts every code sharing its `person_label`; S6 adds `sales_teams` / `sales_team_members`; G1
records "invoiced (not available: the CRM has no customer invoice table)" (:772-777).

### 2.2 The data behind each dimension

**Sales order header** `sales_orders` (`app/models/order.py:410-500`): `so_number` :421,
`customer_id` :422, `debtor_code` :428, `sales_agent_id` :433 (set from AutoCount's agent code on
ingest, `app/services/document_ingest_service.py:219`), `order_date` Date :434 (not indexed),
`demand_class` project or retail :443, `status` :451 (open, fulfilled, closed, cancelled;
AutoCount "partial" is stored as open, `document_ingest_service.py:131-145`), `source_system` :452.
No currency (MYR, `app/services/scm/money.py:28`), no header totals, no tax column.

**Sales order line** `sales_order_lines` (`order.py:503-570`): `product_id` :509, `qty_ordered`
:511, `qty_delivered` :512, `unit_price` :518, `discount` :525, `line_total` :526 (AutoCount
"Total (Inc)", tax inclusive, after discount), `required_date` :532, `line_status` :551.

**No customer invoice.** The only invoice tables are supplier proformas (`app/models/scm.py:1702`,
:1852, :1950, :2056). The DO table `orders` (`order.py:286-357`) has money columns, but its money
is documented as unusable (August 2026 sums to 136.3M against 23.2M) and DO rows only exist from
April 2026 (`documentation/plans/chatbot/chatbot-sales-report-acceptance-criteria.md:46-49`). No
AutoCount IV (invoice) document is ingested anywhere.

**Sales agent** `sales_agents` (`app/models/sales_agent.py:42-134`): one row per AutoCount code,
`sales_agent` :50; `person_label` :60 groups codes into people ("38 codes decompose to 16 people
via a `(name, I|III|IV)` split", :56-59); `company_id` NULL = shared across companies :73. **Filled
on 0 codes**: "`person_label` is NULL for every one of the 80 agents on the prod copy"
(`app/services/order_inquiry_header_service.py:96-97`); `demand_breakdown_service.py:52` says the
same. Editable today on the Sales Agents detail screen through `PATCH
/api/v1/master-data/sales-agents/{id}/annotation` (`app/api/v1/master_data/sales_agents.py:109`,
`person_label` written :125), slug `master_data.sales_agents.edit`. The established label rule is
`COALESCE(person_label, sales_agent)` (`order_inquiry_header_service.py:105`,
`demand_breakdown_service.py:132`).

**Debtor type (ACCOUNT I..IV and SORENTO / BRAVAT / CERAMIC / CABANA / PROJECT / SAMPLE).**
- AutoCount `Debtor.DebtorType` arrives on the masters push as `market_segment_code`
  (`app/schemas/canonical_masters.py:139-145`) and folds onto `market_segments.code`
  (`app/services/rules/customer_rules.py:28-47`), whose only seeded rows are `retail` and
  `project` (`alembic/versions/263_market_segments.py:107-110`). **Any other value is dropped**
  with the warning `segment_unknown` (`app/services/master_ingest_service.py:477-482`;
  documents: `document_ingest_service.py:1058-1076`). So if AutoCount's debtor type is SORENTO or
  ACCOUNT I, the CRM receives it and discards it.
- Counter-evidence: the customer Excel importer's test says "a real AutoCount listing's `Debtor
  Type` carries Trade / Cash / Local" and deliberately maps no alias for it
  (`tests/test_customer_import.py:500-523`). Which company's listing that was is not recorded.
- The same split is visible in names: a customer keeps one ledger per account, with the account in
  the name, e.g. `HANLIM TRADING SDN BHD [A/C I]` (PR #1258 goldens), `Deluxe Home Center AC (I)`
  (`customer_rules.py:4-5`), and ledger suffixes `(PROJECT)`, `(CERAMIC & ELLECI)`, `[IBORN]`
  (`app/services/chatbot/lanes/business/fetch.py:1223`,
  `documentation/plans/chatbot/PLAN-chatbot-outstanding-report.md:63`).
- The agent codes carry the same roman numerals (`SEAN I`, `SEAN III`), and the suffix "maps to
  neither company nor market segment in this database" (`sales_agent.py:61-64`).
- Conclusion: **the debtor type is not stored today**. The likely source is AutoCount
  `Debtor.DebtorType`, but that needs the S0 measurement (the raw values the masters push sends)
  and G3.

**Brand / product category.** `product_categories.category_code` is the AutoCount Item Group
(`app/services/autocount_pull_service.py:455`), shaped `<BRAND>-<CLASS>`, prefixes SRT Sorento, CB
Cabana, M Mocha, BRT Bravat, IDC, IB Iborn (`app/services/product_class_signal.py:30-37`); `brands`
(`app/models/product.py:98`) is the AutoCount Item Brand; `products.brand_id` :203. No CERAMIC,
PROJECT or SAMPLE brand or category exists, which is why the report B columns read as **debtor
types**, as the report's own title says ("Weekly Sales by Debtor Type by Sales Agent"). A product
brand breakdown (SRT / BRT / CB) is possible from lines and is offered as a separate axis (G3).

**Dealer vs project team.** `sales_orders.demand_class` (closed vocabulary project | retail,
`app/services/scm/demand_class.py:31-40`, resolved by the ladder :84-139). Prod 2026, cancelled
excluded: retail 30,571, project 5,210, null 170
(`chatbot-sales-report-acceptance-criteria.md:57`). "PROJECT TEAM" may instead mean a group of
people (the footnote "whole project team sales 16pax"); #1260 S6 adds sales teams. G4.

**HANLIM.** A customer, not an agent or a brand: `HANLIM TRADING SDN BHD` with 6 ledgers, 1,230
SOs over 37 months, all retail (`chatbot-sales-report-acceptance-criteria.md:60`). Shown apart
from the agents in report B.

**DILOOMA, PINTAR.** DILOOMA appears as a customer in chatbot replay fixtures only; PINTAR has no
hit anywhere in the repo. Both are named project debtors in report C (Mocha), so they are Mocha
customers and cannot be checked until Mocha's customers and SOs are in the CRM.

**Mocha.** Both a company and a brand:
- Company `MOCHA` (`app/models/company.py:28`, code SRT / MCH comment; plan
  `documentation/plans/inventory/PLAN-company-so-feed-flag.md:17` lists `MOCHA` / Mocha and `SRT` /
  Sorento), "a second AutoCount company" (`alembic/versions/512_integration_ref_company.py:6`), db2
  in `documentation/plans/autocount/PLAN-autocount-brands-ingest.md:13,24`.
- Brand prefix `M` inside Sorento (`product_class_signal.py:33`); "Cabana and Mocha are brands
  INSIDE the Sorento company" (`alembic/versions/371_brand_member_routing.py:4`).
- **Mocha has no sales orders in the CRM**: "its AutoCount SO feed is not connected"
  (`PLAN-company-so-feed-flag.md:10-12`), and `companies.so_feed_live` is false for Mocha
  (`app/models/company.py:31`, migration flips `code = 'MOCHA'`, plan :39).
- Report C's agents (CHONG, CHUA, JASMINE, JIMMY, KENT, CONTACT AG, SMT, TEO JM) overlap report B's
  (CHONG TECK HIN, CONTACT E, CONTACT AB), which fits the shared agent master ("the captain's own
  files show the same agents selling for both companies", `sales_agent.py:66-67`). So report C is
  **Mocha the company** (AutoCount db2), not the M brand inside Sorento. G2 confirms.

**History depth.** 149,383 SOs in total (`chatbot-sales-report-acceptance-criteria.md:53`); source
split autocount 75,600, scm_upload 10,730, scm_so_history 950, scm_order_inquiry 12
(`documentation/plans/scm/PLAN-scm-oi-sheet-migration.md:35`); HANLIM's 1,230 SOs span 37 months,
so Sorento SO history reaches back to about August 2023. The retired six-year history importer
(`scm_so_history`, `tests/test_ingest_parity_s4_contract.py:316-318`) means some pre-feed SOs came
from a one-off export, so **completeness per year is not known**. No explicit cutoff exists in the
ingest code.

**Not reachable from this checkout.** CI's database holds no data (LESSONS-LEARNT: "CI's database
has NO data"), and the PDF totals are on the owner's drive. So the gap between SO figures and the
PDF totals cannot be measured here. S0 measures it on the prod copy before the grill closes.

## 3. Dimension map

| Report dimension | Source | State |
|---|---|---|
| Sales amount RM | `sales_order_lines.line_total` (ordered) or the confirmed value (`_per_line_exprs`) | present; basis is G1 |
| Month, quarter, year | `sales_orders.order_date` (document date) proposed; the sales report uses `COALESCE(required_date, order_date)` | present; G1 |
| As-at date | a parameter; lines with `order_date <= as_at` | present |
| Company (Sorento, Mocha) | `sales_orders.company_id` -> `companies` | Sorento present; **Mocha has no SOs** (S4) |
| Dealer vs project team | `sales_orders.demand_class` retail / project | present (170 nulls in 2026); G4 |
| Sales agent (person) | `sales_orders.sales_agent_id` -> `COALESCE(person_label, sales_agent)` | codes present; **person labels 0 of 80** (S2 backfill) |
| Debtor type (ACCOUNT I..IV; SORENTO..SAMPLE) | proposed new `customers.debtor_type` from AutoCount `Debtor.DebtorType` | **missing** (dropped on ingest); S0 + G3 + S3 |
| Product brand (SRT, BRT, CB, M, IB) | `product_categories.category_code` prefix, or `products.brand_id` | present; offered as a second axis |
| HANLIM row | the HANLIM ledgers (customer ids) set apart | present (6 ledgers) |
| DILOOMA, PINTAR | Mocha customers, excluded or listed by id | Mocha customers not in the CRM yet |
| Footnotes ("Nov'24 ... 16pax") | typed by a person | **nowhere to store**; G8 |

## 4. Basis: sales orders, not invoices

The PDFs look like AutoCount invoiced sales (a weekly "Sales by Debtor Type" is an AR-side
report). The CRM can reproduce:

- **Ordered**: every non-cancelled SO line, as soon as the order exists (`line_total`).
- **Delivered (confirmed)**: the part of each line transferred to DO (`confirmed_value`).
- **Not invoiced**: no invoice table, no IV feed. The closest proxy for invoiced is delivered, but
  the CRM has no delivery date (`qty_delivered` is a running figure AutoCount overwrites,
  `document_ingest_service.py:243`), so delivered can only be filed under the order's own month.

Where SO figures and invoice figures will differ: orders not yet delivered (ordered basis
overstates), deliveries of older orders (filed under the order month), credit notes and returns
(not in the CRM at all), invoices raised without an SO (cash sales, not in the CRM), and the date
used (order date vs invoice date).

Proposal: build on SOs with the basis printed on every screen, export and chatbot header, measure
the gap in S0 on three months against the PDF totals, and name the trigger for an invoice feed:
**if S0 shows a monthly gap above the tolerance the owner sets in G1, an AutoCount IV (invoice)
ingest lane is planned (cross-repo ESB work plus one `sales_invoices` table), and the reports gain
an `invoiced` basis on the same query layer.** Not built before that.

Round 3 (Owner ruling 26 Sep 07:06 G1): the owner kept option (c) with Delivered as the default
and dropped option (d) and its tolerance from the reply, so no percentage is ruled. The trigger
becomes: the owner asks for the invoice feed after reading S0's measured gap.

## 5. Design: two kernel definitions on one dataset (round 2)

**One new dataset, two definitions, two route wrapper pages, one new component
(`ReportPivotChart`), five small kernel extensions, one chatbot route, one MCP tool, the report
file sender, one migration column (S3). No registry, no rule engine, no new table.** Section 0.1
lists every reuse and every new piece with file:line.

### 5.1 The dataset: `sales_order_lines`

`app/services/reports/datasets/sales_order_lines.py`, declared like `datasets/sponsorship_forms.py`
(`COLUMNS` :219-279, `DATASET` :281-292):

- `base(ctx)`: `sales_order_lines` joined to `sales_orders`, `sales_agents`, `customers` and
  `products` / `product_categories`, with `sales_report_service._common_filters` (cancelled SO and
  line out, :241-242) and the per-line money of `_per_line_exprs` (:198-220), imported, not
  copied.
- `scope = "company"`, `company_column = sales_orders.company_id`, **plus** a required Company
  select (below). The kernel's company arm becomes fail-closed in S1 (the `engine.py:311-315`
  TODO names this day), and a Company value outside the caller's grant is 403.
- One date basis: `order_date` ("Order date", G1). The period is the kernel's `PeriodParam`
  (year, month chips, custom from and to).
- **Measures** (money): `ordered_value` ("Ordered RM") and `delivered_value` ("Delivered RM", the
  confirmed value). The Basis filter picks which one the default view shows (G1: delivered).
- **Dimensions** (the round 1 axes, now columns): `year`; `month_of_year` (JAN to DEC, with
  `fixed_values`); `year_month` (the period's own months, `period_months=True`); `quarter` (Q1 to
  Q4, fixed); `channel` (dealer / project / `(blank)`, from `demand_class`); `agent` (the person,
  0.5); `agent_code`; `debtor_type` (S3); `customer`; `brand` (the category prefix,
  `product_class_signal.py:30-37`); `seller_group` (the set-apart group name when the line's
  customer is in the Set apart filter, else "DEALER - SALESMAN").
- **Filters** (`SelectParam`, `registry.py:175-187`): Company (single, required), Channel
  (multi), Basis (single, required), Sales agent (multi, by person), Debtor type (multi, S3),
  Set apart (multi customer, feeds the `agent` and `seller_group` dimensions through
  `QueryContext.values`, so set-apart lines leave every agent row and appear as one row per group,
  sorted last), Exclude customers (multi customer, `notin_`; the header names them).
- Round 3 (Owner ruling 26 Sep 07:06 G3): a **Product brand** filter (multi) beside the `brand`
  dimension, and a shared view "By product brand" (agent x product brand), both in S3. Round 3
  (G4): the per-agent shared views carry no Channel filter, so project orders are included.

### 5.2 Two definitions

- **`sales`**, "Sales report", permission `sales.reports.view`, `module_key = "sales"`. Default
  pivot `rows=agent, cols=debtor_type, measures=[delivered_value]`; the shared views of 0.3.
  `WorkbookSpec` with the report title "Weekly Sales by Debtor Type by Sales Agent" in the title
  block and the company's name from the Company filter; month sheets on (report B's monthly
  tables).
- **`sales_yearly`**, "Yearly comparison", same permission and module. Default pivot
  `rows=year, cols=month_of_year, measures=[delivered_value]`, `variance="last_two_rows"`,
  `chart="line"`, period custom from 1 January two years back to today ("as at" is the period's
  end). `WorkbookSpec.month_sheets = False`. Block titles `<COMPANY NAME> - DEALER` / `- PROJECT
  TEAM` from the Channel filter (G4).
- Both are registered by importing their modules in `app/services/reports/__init__.py:10`.

### 5.3 Kernel extensions (all small, all generic)

1. `ReportDefinition.module_key`: the reports router drops the single procurement guard
   (`app/api/v1/__init__.py:234-241`) and checks the definition's module per request (the
   sponsorship definition declares `procurement`, so it is unchanged).
2. Company arm fail-closed (`engine.py:311-315`) and the enqueuer's company passed to
   `generate_report_xlsx`.
3. `Column.fixed_values`: the axis prints every value in order, empty or not; a cell with no
   lines is blank, never 0. Months after the period's end print blank.
4. `PivotLayout.variance = "last_two_rows"` adds `ReportPivotLayout.variance_row` (last row minus
   the one before, per column, and its total over the columns the last row has, which is G5 (a));
   `ReportPivotTable` renders it under the rows in the muted row style, negatives in brackets;
   `_render_summary` writes it.
5. `PivotLayout.chart = "line"`: `ReportPage` renders `ReportPivotChart` under the Summary table
   (one line per row value, columns on x); the renderer adds an openpyxl `LineChart` under the
   summary. `WorkbookSpec.month_sheets` (default true) turns the month sheets off.

### 5.4 Screens

- Menu: SALES heading > Sales group (#1260) > **Sales report** and **Yearly comparison**, after
  Sales Agents, `moduleKey: 'sales'`, permission `sales.reports.view`. Routes `/sales/reports`
  and `/sales/yearly-comparison`.
- Each page is `ReportPage` with the new key: `PageHeader` title and crumbs, the views menu,
  Configure summary, **Export to Excel** as the one primary button; the filter bar; the Detail
  tab (sales order lines, `DataGrid`) and the Summary tab (the pivot, and for the yearly
  comparison the variance row and the chart under it).
- The basis line prints under the filter bar on both screens and in the workbook's title block:
  "Basis: Delivered (transferred to DO), by sales order date. Sales orders, not invoices." (G1)
- Numbers: whole ringgit on screen as the kernel prints them, the workbook keeps sen; negatives in
  brackets; blank for no sales; `(blank)` group last. Tables scroll sideways inside their card
  with the first column pinned (`ReportPivotTable.tsx` pinned classes); the page never scrolls
  sideways, at 375 or 1280.

### 5.5 The chatbot seam

- **Route** `GET /api/v1/sales/analysis` (new `app/api/v1/sales/analysis.py`, behind
  `require_module_enabled_with_api_key("sales")`, permission `sales.reports.view`, plus the
  `sales_orders.sales_report` reveal key re-check for API-key callers exactly as the sales report
  does, `app/api/v1/order_management/orders.py:1713-1726`). Query params are the `sales`
  definition's filters plus `rows`, `cols` and `n` (1 to 100, cut after `total_count` and totals,
  PR #1263's rule). It calls `engine.run` for the `sales` definition, so it is the screens' query.
  With `deliver=file` it also queues the workbook (0.6) and returns `attachments` or `pending`.
  Round 4 (Q2): no `deliver` switch; every call that returns an answer queues the workbook (R4.2).
- **MCP tool** `crm_sales_analysis` (new `ToolSpec`, `catalog.py:15` shape), domain orders, the
  `sales_orders.sales_report` reveal key, no paging params, `related_tools=("crm_sales_report",
  "crm_top_selling_report")`; in `PRESENTER_TOOLS` (`presenters.py:38`),
  `CHATBOT_READ_ONLY_TOOLS` (`app/services/chatbot/lanes/business/fetch.py:965`), the order
  domain seed in `app/services/chatbot/turn/policy_rows.py`, and one migration for the live
  `chatbot_domains` row (as PR #1263 did).
- **Parser** (`app/services/chatbot_parser_prompt.py:83-98`): `group_by` gains `agent`,
  `debtor_type`, `brand`, `month`, `quarter`, `year`; new nullable keys `compare_years`,
  `exclude_customers`, `sales_basis` (ordered | delivered | unclear), `reply_format` (file | text
  | unsaid). Round 4 (Q2): `reply_format` is dropped; nothing in the message changes the format.
- **Shape rule** (0.6): the presenter counts rows x value columns and picks text, or text header
  + file. Round 4 (Owner ruling 26 Sep 07:16 Q2): withdrawn; the presenter prints the whole answer
  as text and always passes the attachment (R4.2).
- **Person** (0.5): a label wins; no "which code" question when one exists.
- **Clarify, never assume** (owner rulings 26 Sep from #1175): company when both are granted and
  none named; the period only when the words are ambiguous (none said = the current calendar
  year, the top X ruling; G6); the basis when ambiguous; the axis when "by brand" could be the
  debtor type or the product brand (G3).
- **Long answers**: no one-message setting and no "how many?" for a breakdown. Every row is sent;
  n8n chunks (owner, PR #1258 05:32Z, which amends the 01:55Z reading round 1 relied on). A
  **ranked** ask with no N ("best agents this year") follows the top X rule: the header states the
  count and the bot asks how many. Never "more", "next" or "lagi".
- **Dealer contacts**: staff only; a contact linked to a customer gets "Sorry, I can only share
  sales figures for your own account." and nothing is fetched.

### 5.6 Access

- `sales.reports.view` (covers both screens, their export and the chatbot route). Grant sweep in
  S1's migration to admin and superadmin, others through the role editor (#1260's rule, its
  3.7). `security-reviewer` runs on S1 (new slug, company parameter, the per-report module check,
  the fail-closed company arm) and S3 (ingest field).
- Saved views: the kernel's own rules; publishing a shared view needs `reports.views.publish`
  (`ReportViewsMenu.tsx:30-33`).
- Round 3 (Owner ruling 26 Sep 07:06 G10 and G9): `sales.reports.view` confirmed. S6 adds
  `sales.reports.schedule` (set up scheduled sends), granted to admin and superadmin in its
  migration; every scheduled send re-checks the recipient's `sales.reports.view` and company grant.
  `security-reviewer` runs on S6 (outbound business figures, a new slug, a new table).
- Round 5 (Owner ruling 26 Sep 07:34 Q7): `sales.reports.schedule` is withdrawn. Setting up a
  weekly send is the existing `automation.automations.add` / `.edit` / `.run`; every send keeps
  only recipients holding `sales.reports.view` and the view's company (`recipient_check`, R5.3).
  `security-reviewer` still runs on S6 (outbound business figures, the recipient check).

## 6. Slices (round 2; thin, vertical, ordered for early owner value)

Each slice is its own lane and PR, Phase 1 frontend mock first, Phase 2 tester-first, Phase 3
reviewer plus browser at 1280 and 375.

Round 3: the waves, dependencies and parallel lanes are in R3.4, which governs the order. The
slice bodies below stay, with Round 3 notes; S6 is new.

### S0. Measure (captain, read-only SQL on the prod copy; no code, no PR)

Unchanged from round 1: history depth per company per month, three months against the PDF on
both bases and both date rules, the raw `Debtor.DebtorType` values, person label fill and the
codes the name split would group, HANLIM's ledger ids, Mocha SO count and `so_feed_live`, and an
`EXPLAIN ANALYZE` of a three-year year x month pivot through `engine.run`.

### S1. The sales dataset and Yearly comparison, the kernel extensions, the chatbot file (full)

- Backend: the `sales` module (unless #1260 S6 landed first), `sales.reports.view` and its grant
  sweep; the `sales_order_lines` dataset with the dimensions `year`, `month_of_year`,
  `year_month`, `channel` and the filters Company, Channel, Basis; `sales_yearly` definition;
  kernel extensions 1 to 5 (5.3); `GET /sales/analysis`; the report file sender.
- Frontend: `/sales/yearly-comparison` page wrapper, `ReportPivotChart`, the variance row in
  `ReportPivotTable`, the menu items (Sales report is added in S2).
- Chatbot: `crm_sales_analysis` (S1 axes), "compare dealer sales 2025 vs 2026 by month" as the
  **file** answer (note 6), "total project sales this year" as text, the company and period
  clarify lines.
- Tests: dataset sums equal `sales_report`'s ordered and confirmed figures to the sen; cancelled
  out; February SO with a March line counted in February; null `demand_class` in `(blank)` and the
  total; the company arm fail-closed (no scope = no rows) and 403 outside the grant; the
  sponsorship report unchanged under the per-report module check; fixed months blank not 0;
  variance math and brackets; workbook has no month sheets, has the chart, totals are values; the
  export lands a `report_xlsx` row in My Downloads; route 401 / 403 / 422; presenter goldens for
  the file answer (text header + attachment) and the pending line; agent-browser evidence via the
  sidebar at 1280 and 375.
- Round 4: Q1 ruled (`sheet_per` built). Q2: every chatbot golden is the whole answer as text
  plus an attachment assertion, "total project sales this year" included; clarify goldens assert
  no attachment; the pending line is "The Excel follows here." Q5: if S1 creates the module, its
  migration runs `CREATE SCHEMA IF NOT EXISTS sales` and a test asserts the schema exists after
  upgrade.
- DoD: three months reconcile with S0 on the prod copy; the owner reads the yearly comparison
  beside the PDF.
- Round 3: wave 1. Basis filter with Delivered default (G1); variance as (a) (G5); no period =
  current calendar year (G6); Q1's `WorkbookSpec.sheet_per` is built here (recommendation
  stands). On-demand Export to Excel is G9 (a).

### S2. The Sales report: person, set apart, exclude, quarter, both companies (full)

- Backend: `sales` definition; dimensions `agent`, `agent_code`, `customer`, `quarter`,
  `seller_group`; filters Sales agent, Set apart, Exclude customers; person resolution (0.5);
  the `person_label` pre-fill migration (Q3); the shared views of 0.3 for Sorento, published.
- Frontend: `/sales/reports` page wrapper and its menu item. Nothing else: the views menu, the
  filters and the tables are the kernel's.
- Chatbot: "Sean's sales this month" (text, no which-code question), "sales by agent this year"
  (text, every row), "top 5 agents this year", "HANLIM this year vs last year" (small grid, text).
- Tests: two codes one label = one row; no label groups by the name split; a typed name matching
  two different names asks; set apart leaves every agent row, appears once, last, grand total
  unchanged; exclude removes the customers from every cell and total and the header names them;
  quarter buckets; the monthly sheets sum to the summary; the shape rule (1 value column = text,
  12 cells = text, 13 cells = file, "in Excel" = file).
  Round 4 (Owner ruling 26 Sep 07:16 Q2): the shape rule test is replaced by "every answer, 1 cell
  or 427, is text plus the file"; Q3 and Q4 ruled.
- DoD: every active Sorento code has a `person_label` after the owner's review of the pre-fill;
  the count is pasted in the PR.
- Round 3: wave 2, beside S3 and S6. The per-agent views include project orders (G4); the
  set-apart and exclude lists are saved in the shared views (G7 recommendation (a), asked again).

### S3. Debtor type (the account columns) and the chatbot "by account" (full)

Unchanged from round 1 except that the axis is a dataset dimension and the columns are the
default "By account" view: migration `customers.debtor_type` varchar(50) null, written raw on the
masters push and the document back-create, both customer dict builders, the customer header
read-only, the Debtor type filter; "Sean's sales this month by brand" (text, note 7).
`security-reviewer`: yes (ingest).
- Round 3 (Owner ruling 26 Sep 07:06 G3): agreed. S3 also carries the **product brand** axis
  (dimension `brand`, the Product brand filter, the shared view "By product brand"), so "by brand"
  on WhatsApp can mean either; the bot asks "Debtor type or product brand?" only when the words do
  not say. Wave 2; its migration and ingest half can start in wave 1.

### S4. Mocha sales order feed (dependency, mostly outside this repo)

Unchanged from round 1. The only CRM-visible change: Mocha's figures appear in the same two
reports through the Company filter (no menu item appears; there is none to hide).
Round 3 (Owner ruling 26 Sep 07:06 G2): Mocha is the company (AutoCount db2), so S4 stays the
prerequisite for S5. Wave 0: it starts now, because its work is outside this repo.

### S5. Mocha on the Sales report (small fix track)

Was "Report C", now a configuration lane: Mocha's shared views (the same six, with Exclude =
DILOOMA, PINTAR on "By quarter" and Set apart = the named project debtors), published; the
chatbot golden "Mocha Q2 by agent without Dilooma" (text, note 8); the owner reads the Sales
report for Mocha beside the PDF. Under 300 lines, no migration, no permission change: small fix
track.

Round 3: wave 3, after S2, S3 and S4.

### After S5 (only on a ruling)

Weekly delivery (G9), footnotes (G8).
- Round 3 (Owner ruling 26 Sep 07:06 G9, "build both in this plan"): weekly delivery is no longer
  after S5; it is S6 below, in wave 2. Footnotes stay out (G8 (c), the owner's "ok").

### S6. The weekly scheduled Excel, by email or WhatsApp (full; round 3, G9 (b))

- **Round 5 (Owner ruling 26 Sep 07:34 Q6, Q7 and Q8), governs over the bullets below, which are
  kept as written:** S6 is **the weekly Sales report email as an automation** (R5.3).
  - Backend: migration (`automations.run_weekday` smallint null; the seeded "Sales report
    (weekly)" email template; no table, no slug, no `scheduled_tasks` row); `schedule_type`
    `weekly` and `_next_run_for_weekly` in `automation_service.py`; the trigger
    `sales_report_view` (`app/services/sales/report_automation.py`, registered in
    `automation_triggers.py`) that builds the YTD workbook inline under the view's company; the
    attachment pass-through (`_send_per_match`, `_enqueue_email`, the two `enqueue` calls in
    `notification_tasks.py`); `TriggerSpec.recipient_check` and its sales check. Worker
    restart after it lands: the scheduler runs on the worker, and `notification_tasks.py` is an
    RQ task.
  - Frontend: `AutomationForm.tsx` shows Report view for this trigger and the Weekly schedule
    with Day; the triggers catalog already lists the new trigger.
  - Tests: weekly next-run golden table (across a week boundary, the timezone); `_validate`
    accepts `weekly` only with `run_weekday` and `run_time`; the file is YTD as at the send date
    with the view's filters and company (Q8); the email row carries the attachment (provider, key,
    file name) and the template's header; an automation with no attachment on its context is
    byte-for-byte unchanged (the existing automation tests stay green); `recipient_check` drops a
    user without `sales.reports.view`, without the company, inactive, or an extra email, lists
    them as `no_access`, and sends to the rest; a gone, unshared or non-sales view, or the `sales`
    module disabled, sends nothing and records the reason; Run now sends once whatever the
    weekday and does not move `next_run_at`; a second `evaluate_due` in the same minute sends
    nothing; no WhatsApp message is ever sent (no `respond_*` call); agent-browser run from the
    sidebar (System Management > Automation) at 1280 and 375.
  - DoD: the owner creates the automation for one view, presses Run now, reads the email and its
    Excel beside the PDF, and enables it. `security-reviewer`: yes.

- Round 4: the model is `ReportSubscription` in `app/models/sales.py` with `{"schema": "sales"}`
  (Q5, R4.3); the purge file is `sorento_crm_frontend/modules/sales/purge_tables.json`; the
  window-closed path follows Q6 (R4.6).
- Backend: migration (`CREATE SCHEMA IF NOT EXISTS sales`, `sales.report_subscriptions`, the
  runner's `scheduled_tasks` row, the `sales.reports.schedule` slug and its grant sweep); the
  `sales_report_scheduled` email event; the `sales_report_scheduled` WhatsApp use case in
  `TEMPLATE_DEFAULT_USE_CASES`; `next_run_for` (shared with #1260 S5); the runner handler; the
  routes of R3.3; `purge_tables.json`. Worker restart after the handler lands.
- Frontend: **Scheduled sends** in `ReportViewsMenu` for a shared view, the Scheduled sends
  dialog (rows, Add, Enabled, Send now, deferred Delete).
- Tests: next-run golden table (weekly across a week boundary, timezone); the file is YTD as at
  the send date (Q8) with the view's filters; access re-checked (a user who lost the slug or the
  company gets nothing, logged, `next_run_at` advances); email carries the attachment from the
  download's storage key; WhatsApp in window sends text then file, out of window sends the
  template text (Q6), no linked contact or `outbound_enabled` false is skipped and logged;
  idempotency (a second runner tick sends nothing); a non-shared view, another company's view or a
  non-sales report is 422 / 403; route 401 / 403; the row lands a `report_xlsx` in the user's My
  Downloads; purge invariants test; agent-browser run from the sidebar at 1280 and 375.
- DoD: the Meta-approved template is mapped on prod before any WhatsApp row is enabled; the owner
  enables their own row after one Send now looks right, by email and by WhatsApp.
  `security-reviewer`: yes.

## 7. What is not built, and the trigger for each

- **An invoice (IV) feed and an invoiced basis**: S0 gap above the G1 tolerance. Round 3: no
  tolerance was ruled (G1), so the trigger is the owner asking after reading S0's gap.
- **A separate sales query primitive** (round 1's `sales_grid`): the kernel cannot express an ask
  the owner makes, after the extensions of 5.3.
- **Pinned row order** (the owner's own agent order, round 1 AC-S2-5): the owner asks for it
  after seeing the alphabetical order; then a `Column.sort_order` from the view.
- **An `order_date` index**: the S0 `EXPLAIN ANALYZE` is over one second.
- **A quantity measure**: an owner ask.
- **Dated person labels**: an owner ask.
- **Text + file on every answer**: the owner rules Q2 the other way.
  - Round 4: the owner did (Owner ruling 26 Sep 07:16 Q2); it is built in S1 (R4.2). The item
    that replaces it: **"as text" (a text-only reply)**: the owner asks for one.
- Round 3: **Agents seeing only their own rows** (G10 (b)): agents are given access.
  **Footnotes stored in the CRM** (G8 (b)): the owner asks. **Daily or monthly scheduled sends**:
  the owner asks (S6 is weekly, "produced weekly").
- Round 5: **A scheduled send by WhatsApp**: not built, by Owner ruling 26 Sep 07:34 Q6 ("we
  don't scheduled send whatsapp"); no trigger. **A per-person scheduled send table**
  (`sales.report_subscriptions`): not built, by Q7 (the automation holds the view and the
  recipients); trigger: a send needs something an automation cannot hold. **A copy of the weekly
  file in each recipient's My Downloads**: the owner asks. Daily sends are now an automation's
  existing Daily schedule, so that item needs no build.

## 8. Risks

- **SO is not invoice.** Mitigated by the basis line on every output, S0's measured gap, and G1.
- **Debtor type source unproven.** S0 and G3 decide before S3 starts.
- **Kernel changes touch the sponsorship report.** Every extension defaults off, and S1 carries a
  test that the sponsorship report's meta, run and workbook are unchanged.
- **The per-report module check** replaces a router-wide guard: a definition with no
  `module_key` must fail closed (403), tested in S1.
- **Person labels from the name split** may merge two people who share a first name. The owner
  reviews the pre-fill (Q3) and the report shows the codes under each person in the Detail tab.
- **Mocha is blocked on an external feed.** S4 depends on the ESB team.
- **The chatbot file needs the 24 hour window** for the worker push; outside it the pending
  reply is the only message, as for the low stock report (`respond_chat_template_service.py:697-720`).
  Round 4: a chatbot answer always replies to a fresh inbound message, so the window is open; the
  risk stays for the scheduled send only (Q6).
- Round 4: **Every answer now waits on a worker job for its file** (Q2). The text goes first and
  does not wait; the worker must be running (CLAUDE.md "Worker is required"), and a missing
  worker shows as "The Excel follows here." with no file, the failure notice path of the low
  stock report (`_tell_chat_the_report_failed`, `export_tasks.py:944`) covers a failed build.
- **Tax inclusive amounts.** `line_total` is Total (Inc). S0 measures, G1 decides.
- Round 3: **Scheduled sends go to people outside the screen's session.** Access is re-checked
  per send (R3.3), rows ship disabled, and `security-reviewer` runs on S6.
- Round 3: **WhatsApp cannot carry the file outside the 24 hour window**
  (`respond_chat_template_service.py:697-720`). Q6 decides the fallback; email always carries it.
- Round 3: **Two plans write the `sales` schema and `next_run_for`.** Both migrations use `CREATE
  SCHEMA IF NOT EXISTS`, and the helper lives in one file whichever lane lands first.
- Round 5: the WhatsApp window risk above no longer applies (Owner ruling 26 Sep 07:34 Q6: no
  scheduled WhatsApp). The `next_run_for` overlap with #1260 is gone: the weekly helper lives in
  `automation_service.py` (R5.3).
- Round 5: **S6 touches the shared automation path** that every existing automation email uses
  (`_send_per_match`, `notification_tasks.py`). Mitigated: the attachment and `recipient_check`
  are both opt-in by trigger, and S6 carries a test that an existing trigger's email row is
  unchanged.
- Round 5: **The weekly workbook is built inline on the scheduler process.** A slow build delays
  the heartbeat's other due tasks for its duration, as the reorder run does today. S0's `EXPLAIN
  ANALYZE` bounds it; trigger to move it to an RQ job: a build over 30 seconds.

## 9. Grill questions (posted on the PR, at most 10)

- **G1. Basis and date.** The CRM has sales orders, not invoices. Options: (a) ordered amount by
  order date; (b) delivered (transferred to DO) amount by order date; (c) either, chosen on the
  screen, with a default; (d) wait for an AutoCount invoice feed. Also: what gap against your PDF
  totals is acceptable before we build (d)? **Recommend (c) with delivered as the default** (the
  top X ruling: "selling" means delivered), printed on every header, and (d) only if S0 shows a
  monthly gap above 2 percent.
  - Owner ruling 26 Sep 07:06 G1: (c), Delivered default, basis on every header; (d) and the
    tolerance dropped from the reply (R3.1).
- **G2. Is "Mocha" in report C the Mocha company (AutoCount db2) or the Mocha brand inside
  Sorento?** Options: company; brand; both. **Recommend company** (report C's agents and debtor
  types are a separate ledger), which makes S4 (connecting Mocha's SO feed) a prerequisite.
  - Owner ruling 26 Sep 07:06 G2: the company (AutoCount db2); S4 stays a prerequisite.
- **G3. Where do the account columns come from?** Options: (a) AutoCount `Debtor.DebtorType`,
  stored raw on the customer; (b) the ledger suffix in the customer name (`[A/C I]`, `(PROJECT)`);
  (c) a mapping you maintain per customer in the CRM; (d) for SORENTO / BRAVAT / CABANA, the
  product brand on each line instead of the customer. **Recommend (a)**, confirmed by S0's raw
  values, with (d) kept as a separate "by product brand" axis for the chatbot.
  - Owner ruling 26 Sep 07:06 G3: agree, (a) plus (d) as a separate axis (S3).
- **G4. "DEALER" and "PROJECT TEAM" in report A.** Options: (a) the sales order's dealer / project
  class (`demand_class`); (b) the agents in a "Project team" sales team (#1260 S6); (c) the
  customer's debtor type PROJECT. **Recommend (a)**, the classification every other sales answer
  already uses. Does report B's per-agent table include project orders or dealer only?
  - Owner ruling 26 Sep 07:06 G4: (a); report B's per-agent table includes project orders.
- **G5. The variance row as at a date.** Options: (a) total variance = current year to date minus
  the prior year's same months; (b) minus the prior full year; months after the as-at date blank
  either way. **Recommend (a)**, so a September report does not show a large negative that is only
  "October to December not happened yet".
  - Owner ruling 26 Sep 07:06 G5: agree, (a).
- **G6. Chatbot period when none is said.** Options: (a) ask ("This month, this year, or a
  range?"); (b) the current calendar year, printed in the header (the top X ruling). **Recommend
  (b)**, since it is stated rather than silent, and ask only when the words are ambiguous ("last
  quarter" at the start of a year).
  - Owner ruling 26 Sep 07:06 G6: (b), "date i think assume is ok".
- **G7. HANLIM and the named project debtors.** Options: (a) a customer list stored with the report
  (a saved view), editable by admins; (b) hard-coded names; (c) a "set apart" flag on the customer.
  **Recommend (a)**. Are there other customers you set apart like HANLIM?
  - Owner ruling 26 Sep 07:06 G7: the owner asked "what's this quesiton for"; (a) stands and
    the question is asked again in plain words (R3.5).
- **G8. Footnotes such as "*Nov'24 whole project team sales 16pax".** Options: (a) not carried;
  (b) a free-text note per report, company and month, typed on the screen and printed in the
  export; (c) typed into the Excel after export. **Recommend (c) for now**, (b) when you want the
  notes kept in the CRM.
- **G9. "Produced weekly".** Options: (a) on demand only (open the screen, export); (b) a weekly
  scheduled Excel sent to named people by email or WhatsApp. **Recommend (a) first**, (b) as its
  own slice after S5, reusing the per-recipient schedule #1260 S5 builds.
  - Owner ruling 26 Sep 07:06 G9: build both in this plan; (b) is S6, its own table with
    #1260 S5's schedule helper and runner shape (R3.3).
- **G10. Who may see these reports.** Options: (a) a new "sales reports: view" permission, granted
  by role, and on WhatsApp staff only with the existing Sales report grant; (b) sales agents see
  only their own rows. **Recommend (a)**; (b) when agents are given access.
  - Owner ruling 26 Sep 07:06 G10: (a) is fine.


Round 2 note: G1 to G10 are still unanswered and stay recommendations. Round 2 changes only their
wording where a name changed: "report B" and "report C" now read "the Sales report" for Sorento
and for Mocha; G7's saved view is the kernel's shared view (0.3); G10's "sales reports: view" is
the slug `sales.reports.view` (0.2). G6's recommendation (b) matches the top X owner ruling
("No date said = the current calendar year").

### 9.1 Round 2 questions (posted on PR #1269 as comment 5844077953, "Round 2")

- **Q1. The yearly comparison's two blocks in one Excel file.** The screen shows one channel at a
  time (Channel filter; two shared views "Dealer" and "Project team"). Options: (a) the Excel
  holds the channel on screen, so two exports make the two blocks; (b) with both channels ticked,
  the Excel writes one block per channel, one under the other on one sheet, like the PDF
  (`WorkbookSpec.sheet_per`, a small kernel extension). **Recommend (b)**: the PDF is one page with
  both blocks, and the extension is generic (any report can split by a filter).
  - Owner ruling 26 Sep 07:16 Q1: "okay", (b).
- **Q2. Chatbot file or text.** The rule in 0.6: a list (one figure per row) is text, however
  long; a table of up to 12 figures is text; a larger table is a short text summary plus the Excel
  file; "in Excel" or "as text" in the message overrides. **Recommend this rule, and not text +
  file on every answer**: a file for a three-line answer is noise and waits on a worker job. Say
  if the threshold should be another number than 12.
  - Owner ruling 26 Sep 07:16 Q2: "always text + file, no cutoff"; the rule above is withdrawn
    (R4.2).
- **Q3. Person labels pre-filled.** 0 of 80 codes have a person label. Options: (a) S2 fills them
  from the code's name part (SEAN I and SEAN III become SEAN), and you correct any on the Sales
  Agents screen; (b) you type all 80 by hand. **Recommend (a)**: it matches your note that SEAN I
  and SEAN III are the same person, and you only fix the exceptions.
  - Owner ruling 26 Sep 07:16 Q3: "okay", (a).
- **Q4. Report sections as saved views.** Report B's and C's sections (by account, by month,
  debtor type by month, dealer vs HANLIM, by quarter, set apart customers) become shared views in
  the report's Views menu, not tabs; the per-month tables are the Excel's month sheets, and one
  month on screen is one month chip. **Recommend this**: it is the sponsorship report's own
  mechanism, and you can add a view without a code change. The other way is fixed tabs, which
  would be new components.
  - Owner ruling 26 Sep 07:16 Q4: "okay", shared saved views.
- **Q5. Sales module, not a sales schema.** The two reports go in the Sales menu group from the
  sales targets plan (#1260), under the `sales` module and a `sales.reports.view` permission; the
  data stays in the sales order tables, and no table is added, so there is no sales schema (the
  targets plan keeps its tables in the main schema too). **Recommend this.** Whichever of this
  lane or #1260's first lane lands first creates the Sales module.
  - Round 3: unanswered; re-answered to align with #1260 round 5: the `sales` module **with** the
    `sales` schema for every new table (`sales.report_subscriptions`), saved views staying the
    kernel's rows (R3.2). Q1 to Q4 unanswered, recommendations stand.
  - Owner ruling 26 Sep 07:16 Q5: "we need a sales schema and a sales module so we are more
    modular": the `sales` module and the `sales` schema, every new table in it (R4.3).

## 10. Out of scope

A report designer beyond the kernel's Configure summary, a dashboard of KPIs, a quantity measure,
targets and commissions (#1260), an invoice feed (named trigger, section 7), a scheduled send
(G9), footnote storage (G8), and any change to the existing sales report or top X answers.
Round 3: the scheduled send is in scope now (G9, S6); footnote storage stays out (G8 (c)).
Round 5: a scheduled send by WhatsApp is out (Owner ruling 26 Sep 07:34 Q6); the scheduled send
is email only, as an automation (Q7).
Round 4: text + file on every chatbot answer is in scope now (Q2, S1); a text-only reply is out.
