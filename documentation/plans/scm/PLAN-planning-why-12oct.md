# PLAN: Planning "why" trail - why was this cancelled, why can I not confirm

Status: Build. Owner approved mock v3 13 Oct ("ok all good"); UAC final. Track: standard (Size M, LEAD pattern).
Lane: PLANNING-WHY, branch `crew/planning-why`.
UAC: `planning-why-12oct-acceptance-criteria.md` (written after the card is answered).

## Owner ask (12 Oct 2026)

"I also cannot check, I cannot see the log in a nice way from prod; think about the user
experience to ease our load and the user load in checking."

Trigger: SO422492 Confirm showed only "0 of 1 orders confirmed / SO422492: Nothing on this
planning change could be confirmed." and purchasing got CANCEL emails nobody could explain.

Goal: a CS user or the owner answers "why was this cancelled?" and "why can I not confirm?" in
under a minute, without crew.

Out of scope: the wrong CANCEL ALL / under-order bugs themselves (lane OI-CANCEL-BUGS).

## Measured facts

(filled in from code research; every claim cites file:line)

Paths: BE = `sorento_crm_backend/app`, FE = `sorento_crm_frontend/app/(protected)/project-sales`.
OIS = `BE/services/project_order_inquiry_service.py`, PCS = `BE/services/planning_change_service.py`.

1. **The bare sentence has one source.** `BE/api/v1/projects/fulfilment_planning.py:1482-1507`
   raises `AppException(422, planning_change_not_confirmed)` with no `failing_lines` when
   `_confirm_a_planning_change` got no confirm result. Causes (PCS): no pending/accepted rows
   (`empty_result`, :4230-4242), every accepted row overtaken by a newer revision (:4246-4257),
   every live line cancelled in the book (:4285-4302), nothing left to confirm after the cover
   pass (:4487-4517). Undecided rows (`decision` None) are uncovered silently (:4353-4360).
   The rows, their before/after (`from_json`/`to_json`) and `applied_reason` are in hand at the
   throw and discarded. `SupplyFailingLine` (`BE/schemas/project_supply.py:283-290`) and the FE
   rendering (`failingLineText`, `FE/_shared/lib/fulfilmentBoard.ts:1599`) already exist.
2. **A refused Confirm is recorded nowhere.** The per-order write rolls back
   (`BE/services/project_supply_service.py:11274-11277`, `:11316`); audit rows ride the same
   transaction (`BE/services/audit_service.py:1315-1322`); `api_call_log` covers only
   `/external/*` or `X-Source` calls (`BE/middleware/api_call_log_middleware.py:30,55-62`).
   The board runs a silent dry run first and never sends the real press when it refuses
   (`FE/fulfilment-planning/components/FulfilmentBoardPanel.tsx:1639-1691`), so the user's
   refusal usually comes from the dry run.
3. **A cancelled OI row has no who/why/link.** No cancelled_by/at, no decision or planning
   change id; only free text in `note` (`BE/models/project_so.py:1056`). Eleven cancel paths
   (OIS:1031, 1355, 1937, 4011, 4109, 4208, 4348/4360, 8936, 6061; PCS:3764, 3427); only
   `mark_rows` (OIS:6061) records a person. `order_inquiry_rows` is `__audit_skip__`
   (`project_so.py:1003`, its comment says the service writes `log_audit` explicitly; only
   `unacknowledge_rows`, OIS:6307, does). `planning_change_rows` is skipped too
   (`BE/models/planning_change.py:133`).
4. **Emails link to the inquiry header only.** Handover queue carries `row_id` (OIS:3678) but
   the email lines drop it (OIS:10996); outbox `metadata_json` keeps the notification id
   (`BE/tasks/notification_tasks.py:311-324`), notification data keeps `source_id` = the
   inquiry (`BE/services/automation_service.py:661-690`).
5. **Retired planning change rows are invisible.** Superseded rows are dropped from every board
   overlay (`FE/_shared/lib/boardChangeAnnotations.ts:533-548`); the batch page is gone, the
   list links to the board (`FE/planning-changes/components/PlanningChangesListClient.tsx:33-49`).
6. **Existing per-line trail.** `GET /sales-order-lines/{core_line_id}/decision-trail`
   (`BE/api/v1/projects/order_inquiries.py:1338`, `BE/services/decision_trail_service.py`):
   revisions, saved draft, raises, planning-change note stamps; no cancels, no emails, no
   refusals. Opened by `DecisionTrailButton` on the worklist and the board.

## Behaviour card (v1, 13 Oct 2026)

Answers (crew, 13 Oct 2026): Q2 (a) audit_logs, no migration. Q3 (a) every press that showed a
refusal, dry run included, not admin Simulate. Q5 (a) cause only when provable, else "cause not
recorded". Owner, 13 Oct: Q1 (a) one History page, line History icons open it filtered; Q4 (a) recipients
shown to anyone who can see the board.

Owner on mock v1 (verbatim): "too many words, i need structure, reuse components, you just give
me a history page to debug, don't show too many things to the user"; "i am okay with the
timeline, just make sure I can filter by product, SO etc". Mock v2: refusal = table rows (line,
product, change, one reason badge, one action); History = one page "Planning history" under
Project Demand with filters SO, product, line, event, user, date; no new board button.

Owner on mock v2 (verbatim): "make these visible by admin which is me only" (refusal detail);
"i don't need this page, i just need this as side panel (lightbox) from the fulfilment planning
page of the SO(s)". Mock v3: refusal rows and History are admin-only, gated like Simulate
confirm (role slug `superadmin`/`admin`, `BE/api/v1/projects/fulfilment_planning.py:573-581`;
FE `isSuperadminUser`, `sorento_crm_frontend/lib/is-superadmin.ts:19`, `canSimulate`
`FulfilmentBoardPanel.tsx:1265`); non-admins keep today's sentence. History = a Sheet opened
from the board gear menu (beside Simulate confirm) for the SO(s) on the board, filters SO,
product, line, event, user, date. No page, route or menu entry; the line History icon, the
worklist and the board header are unchanged (this supersedes the Q1 "line icons open it"
detail, since History is now admin-only).

Mock: `documentation/mockups/PLANNING-WHY/index.html` (real SO422492, masked).

### Rules

- **R1 Confirm result names each line.** When a Confirm (or its dry run) confirms nothing for
  an order, the result lists every line that stopped it, `Line <no>, <item>: <reason>. <fix>.`
  with "Go to line" (the left-out banner's link, `FulfilmentBoardPanel.tsx:343-349`). Reasons:
  (a) undecided AutoCount change: "AutoCount changed <qty a -> b | product X -> Y | date> on
  <date> and no one has decided it yet. Decide the line, then Confirm."; (b) overtaken: "Already
  settled by revision N (<who>, <when>). Reload the board."; (c) book-cancelled line: "AutoCount
  cancelled this line; nothing to confirm."; (d) replaced change: "AutoCount changed this line
  again; this change is replaced. Reload the board."; (e) nothing pending: one order sentence
  "Every change on this order is already confirmed in revision N (<who>, <when>)." Never the
  bare sentence.
- **R2 Every refused Confirm is recorded**: who, when, the order, each line + reason (R1 text),
  written after the rollback on its own session, visible in the order's History.
- **R3 SO History** (sheet from the board header; line History icons open it filtered to the
  line): newest first, grouped by day (`EventTimeline`). Entries: revision confirmed (who, when,
  lines ordered / cancelled with cause / reduced / order back), AutoCount SO book update (counts
  by kind and by state now), refused Confirm, email to purchasing (Open email shows the stored
  email), "Waiting now" box for undecided changes. A cancel + re-issue of the same line, product
  and qty in one revision is counted ("50 rows re-issued unchanged"), not listed.
- **R4 Cancelled OI row says who, when, why**: "Cancelled by revision N · <who> · <when>" plus
  the cause; History icon opens the line's History.
- **R5 Explain, do not change.** No planning or netting behaviour changes here; the wrong
  CANCEL ALL / under-order are lane OI-CANCEL-BUGS.

### Real examples (SO422492, restored prod copy, masked)

- **E1 line 592 SRTWC8840-SC**: ORDER 25 at rev 1 (24/09); AutoCount 25 -> 22 on 08/10, row
  pending, no decision; rev 4 (12/10, CS user A) cancelled the 25 ("Superseded by revision 4",
  no cause); email "OI: SO422492" CANCEL ALL 25. New: row reads "Cancelled by revision 4 · CS
  user A · 12/10/2026 2:42 pm / Line left out: AutoCount change 25 -> 22 not decided".
- **E2 lines 528 SRTWT2200 140, 560 SRTWCX8840-S-RL 25, 576 SRTWCY8840 25, 656 SRTWB7099 72**:
  same cause, cancelled at rev 3 (12/10 1:24 pm).
- **E3 line 160 SRTWT2201 24**: product changed to SRTWT2219 (decided); row cancelled "the book
  left nothing to buy"; same email ORDER BACK SRTWT2219 6 from BRW-BB. Cause: product changed.
- **E4 rev 4**: 51 rows cancelled, 50 re-issued with the same line/product/qty; History shows 1
  cancel + "50 rows re-issued unchanged".
- **E5 line 1056 SRTWC8318-RL-N**: change row superseded "Line changed again"; Confirm result
  reason (d).

### Edge cases

- Multi-order board: one result block per order, unchanged.
- Person cancels via `mark_rows`: "Cancelled by <person>" (actioned_by, OIS:6061).
- Transfer to Cancel SO (OIS:4208): cause = its typed reason.
- Planning-change retire (PCS:3764): cause names the AutoCount update and its date.
- Undo of a confirm: History shows "Revision N undone by <who>" (audited delete,
  `project_supply_undo_service.py:1133`).
- Rows cancelled before release: see Q5.
- Emails before release link to the inquiry only: shown at order level; the line filter keeps
  an email when it was sent within the same revision commit and names the line's item.

### Questions (each with a recommendation)

- **Q1 One History or two?** (a) one SO History sheet; every line History icon opens it
  filtered (the per-line `DecisionTrailDialog` content folds into it); (b) new sheet plus the
  existing line dialog. Rec (a): one read, one place, no two trails drifting apart.
- **Q2 Where refused Confirms and cancel causes are stored?** (a) `audit_logs` rows (action,
  reason, `new_values` JSON, `root_entity` = the order; `log_audit`, `audit_service.py:246`), no
  migration; (b) new table / new OI row columns (migration, Size L). Rec (a): the store exists,
  the model comment already names it (`project_so.py:1003`).
- **Q3 Which refusals are recorded?** (a) every press that showed the user a refusal, including
  the dry run that runs before every Confirm; not the admin Simulate; (b) only the real POST.
  Rec (a): with (b) the SO422492 message would still be unrecorded (the dry run caught it,
  `FulfilmentBoardPanel.tsx:1645-1691`).
- **Q4 Open email shows recipients?** (a) subject, time, status, text, recipient addresses,
  read-only, for anyone who can see the order's board; (b) same without addresses; (c) admins
  only. Rec (a): "who got this" is part of the why and the recipients are internal purchasing.
- **Q5 Old cancels (before release) have no recorded cause.** (a) who/when from revision N
  (exact: the note names N; N's confirmed_by/at), cause derived only when an undecided AutoCount
  change for that line existed before revision N, else "cause not recorded"; (b) who/when only.
  Rec (a): answers E1/E2 for the cases that already happened, and says so when it cannot.

## Slices (one lane, one PR #1600)

- **S1 BE refusal detail + recording** (AC-W0..W7): `SupplyFailingLine.reason_code/change`;
  explain rows on the `planning_change_not_confirmed` path for admins; `CONFIRM_REFUSED` audit
  row after rollback in `confirm_all` and `confirm_supply`.
- **S2 BE cancel cause** (AC-W8, W9): `_record_cancel` helper beside every cancelled-state
  assignment (OIS, PCS); guard test. Coordinated with #1599 (crew-note posted there).
- **S3 BE history read** (AC-W10, W11): `GET /fulfilment-planning/history`, admin-only.
- **S4 FE** (AC-W12..W15): admin refusal table, gear menu History, History Sheet with filters,
  Open email dialog.

Test list (tester writes red first): pytest `tests/test_planning_why_refusal.py` (W1..W7),
`tests/test_planning_why_cancel_cause.py` (W8, W9 guard), `tests/test_planning_why_history.py`
(W10, W11 incl. a seeded SO422492-shaped case: line 592 ORDER 25 rev 1, pending qty_down 25->22,
rev 4 cancel -> `undecided_change`; 50-style unchanged re-issue counted); vitest beside
`FulfilmentBoardPanel` and the new History sheet (W12..W14, admin vs non-admin).
