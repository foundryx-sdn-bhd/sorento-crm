# DO-PERIOD-SKIP - acceptance criteria

Owner answers 4 Oct 2026 (behaviour card Q1-Q5, Q5 widened): one per-contact switch,
"Skip date-range questions", default OFF. A switched-on contact is never asked for a period
and may span any range ("this is a management tool").

## Storage

- AC-1 `respond_contacts.chatbot_skip_period_questions` BOOLEAN NOT NULL DEFAULT false, added by an
  additive migration (ADD COLUMN IF NOT EXISTS). Every existing contact reads false.
- AC-2 `GET /api/v1/contacts/{id}` (and the list) returns `chatbot_skip_period_questions`
  (`contact_to_response_dict`, `RespondContactResponse`).
- AC-3 `PUT /api/v1/contacts/{id}/chatbot` accepts `chatbot_skip_period_questions` and persists it;
  omitted leaves it unchanged.
- AC-4 Copy access (`contact_access_copy_service.SWITCHES`) copies the switch like the other chatbot
  switches.

## One helper

- AC-5 `ctx["access"]` (`head/access.py check_access`) carries `skip_period_questions: bool` read
  from the contact row; an unknown contact or a failed read is false (today's behaviour).
- AC-6 One helper `do_ask.skips_period_questions(ctx) -> bool` is the only reader. Every
  period-asking call site calls it; no call site reads the column or the ctx key itself.

## DO list (do_ask.range_reply, lanes/business/__init__.py:2061)

- AC-7 Switch OFF: unchanged. No dates gives "Which period..."; over 31 days gives the cap reply.
- AC-8 Switch ON, no dates: no question; the DO list fetch runs with no date filter (the MCP's own
  20-row cap, newest first) - the pre-#1433 behaviour.
- AC-9 Switch ON, an explicit range of any length (e.g. 1 Jan to 30 Sep): no cap reply; the fetch
  runs with that range.

## SO list (so_status.period_reply, lanes/business/__init__.py:1445)

- AC-10 Switch OFF: unchanged ("Which period..." / 31-day cap reply).
- AC-11 Switch ON, no dates: no question; the reply lists the customer's newest sales orders,
  at most 20, newest first, header without a date window (e.g. "Sales orders for X, latest 20:").
  No rows: "No sales orders for X."
- AC-12 Switch ON, an explicit range of any length: no cap reply; the SO list for that range,
  newest first, at most 20 rows (the same cap the DO list has).

## Unchanged

- AC-13 The customer-scope backstop still runs before either check (a switched-on contact never
  sees a customer outside its links).
- AC-14 The SO list grant check (`sales_orders.outstanding`) still refuses first.
- AC-15 No keyword or regex matching is added; the dates still come from the parser only.

## UI

- AC-16 Contact > Chatbot tab shows a Switch "Skip date-range questions" directly under
  "Chatbot hands over to support teams" (`ContactChatbotSection.tsx`), off by default; toggling it
  saves through `PUT .../chatbot` and survives a reload. No hint text beyond one short line.
- AC-17 Usable at 375px and 1280px.

## Follow-up (not in this PR)

- #1447 REPORT-ENGINE (not merged): `lanes/business/report_ask.py:179` PERIOD FieldSpec
  (`allow_all=False`). Once it lands, its period ask calls `do_ask.skips_period_questions(ctx)` and,
  when true, settles the period without asking (no date filter / the route's own default).
