# PLAN: park reserve requests and reserve notices, send all parked (OI-STAGED-NOTIFY)

Status: Build (owner approved mock v6, 14 Oct). Standard track, size L (additive migration + in-place template layout update, two
sides, status filter, send-all with preview). Lane crew/oi-staged-notify. Card answered by crew
14 Oct (Q1-Q5 all (a)); owner notes on mocks v1 to v5 applied; mock v6 approved by the owner
14 Oct. Red tests next (tester), then coder.

UAC: `oi-staged-notify-14oct-acceptance-criteria.md` (alongside). Card:
`oi-staged-notify-behaviour-card.md`. Mock: `documentation/mockups/OI-STAGED-NOTIFY/index.html` (v6).

## Journey

Owner 14 Oct: purchasing asks CS to reserve across ~100 OIs; each "Request CS to reserve"
emails CS at once (`order_inquiry_reserve_service.py:558`) and each CS Reserve emails
purchasing (`:966`), so ~100 emails each way. Owner v3: "the scenario is user want to park
something, then bulk send, we offer the ability to park, view what's parked, send all parked,
then when user view, they just view what is in the status." Built as a reusable pattern.

## Rules

1. Request lightbox: tickbox "Park this first", UNTICKED on every open (default send). Unticked =
   sent now as today ("Send request"). Ticked = request saved, no email ("Park request").
2. CS side is ONE flow (owner v4): "Reserve (N)" opens a confirmation "Reserve N lines on
   <OI no>" (Product, Requested, Reserve, Balance to buy, Location, Reason; "Notice to
   purchasing: To <name>, Cc <names>"). Its footer is the request lightbox's footer (owner v5:
   unify): "Park notice first" tickbox left, unticked on open, Cancel, ONE primary button
   "Reserve and send to <To name>" (today's behaviour) that reads "Reserve and park notice" when
   ticked. Cancel/Esc reserves nothing.
3. Documents list (Q4) gets ONE "Reserve" status filter (no batches, no send dates):
   - `parked` "Parked request to reserve": a request row CS has not answered whose request is
     parked (not sent to CS);
   - `needs_reserve` "Needs reserve": a request row CS has not answered whose request was sent;
   - `notice_parked` "Parked reserve": an answered row whose notice to purchasing is parked;
   - `reserved` "Reserved": an answered row with nothing parked.
   An OI matches a status when any of its request rows is in it. A "Reserve" column shows the
   OI's earliest status in that order ("Parked request to reserve" names who parked it). Counts per status in the
   filter labels (everyone's).
4. Actions menu (never a top-bar button): "Send all parked requests to CS (N)" (ACKNOWLEDGE) and
   "Send all parked reserves to purchasing (N)" (RESERVE); N = the presser's OWN parked items (Q1);
   greyed with reason at 0.
5. Send dialog: recipient table (To | Cc | Order inquiries | Lines, total row, "No recipient,
   stays parked" line when unresolved) and under it the rendered email, one line tab per email;
   "Send N emails".
6. One email per To person, Cc merged (Q2), exactly what today's automation resolves per item
   (`automation_recipients.py`, `automation_service.py:641-660`), grouped by the first non-actor
   address. Built at send time: requests carry only unanswered rows; notices carry only rows whose
   `qty_reserved` differs from `notified_qty` (actionable, owner rule 13 Oct).
7. "Saved = sent" (#1631): a notice row changed after it was sent is parked again on the next
   parked commit; an amend back to the sent figure drops it out.
8. A multi-OI email is short: subject "Reserve requests: N order inquiries (name)" / "Reserved:
   N order inquiries (name)", heading "N order inquiries need your reserve" / "CS answered your
   reserve requests on N order inquiries", OI and line counts, first 5 OIs by OI no (OI NO, S/O NO,
   CUSTOMER, LINES), "and M more order inquiries.", ONE button: "View order inquiries that need
   your reserve" -> `/project-sales/order-inquiries?reserve=needs_reserve`, or "View reserved order
   inquiries" -> `?reserve=reserved`. A one-OI email keeps today's subject and body unchanged.
9. No automatic send. Handover email path untouched (crew 14 Oct).

## The reusable pattern (owner v3)

Four pieces; the first three are shared, the fourth is per document. No registry, no config
table: a component and a helper with plain arguments.

- FE `components/common/ParkedSendDialog.tsx`: props `title`, `preview` (query result
  `{emails, unresolved}`), `onSend`, `isSending`; renders the recipient table, the line tabs with
  `EmailPreviewFrame`, the footer.
- FE park tickbox: the existing `Checkbox` + `Label`, same footer place on every dialog that
  commits (request lightbox "Park this first", Reserve confirmation "Park notice first"); the
  primary button's label follows it. No wrapper component.
- FE status filter: today's Filters popover + `SearchableSelect`; the status list is per document.
- BE `app/services/park_send.py`: `group_by_recipient(db, trigger_type, items, actor)` (resolve
  each item's recipients with the first enabled automation of the trigger, the same pick as
  `notified_name` `order_inquiry_reserve_service.py:1135`; group by first non-actor address;
  return groups + unresolved), `render_preview(db, trigger_type, group_context)` (the
  automation's template through `EmailTemplateService.render`), and the merge of `raisers[]` /
  `requesters[]` into a group context. The document service builds items, stamps rows and
  appends each group to its existing post-commit queue.

## Schema (migration `oisn_0001_staged_notify`, down_revision `ac_consign_0001_doc_type`)

Additive, nullable, no backfill (NULL = not parked; legacy rows count as sent):

- `projects.order_inquiry_reserve_requests.parked_at` TIMESTAMP NULL (who parked =
  `requested_by`).
- `projects.order_inquiry_reserve_request_rows.notice_parked_at` TIMESTAMP NULL,
  `notice_parked_by` VARCHAR(100) NULL FK users SET NULL, `notified_qty` NUMERIC(15,4) NULL.
- Partial indexes: requests `WHERE parked_at IS NOT NULL`, rows `WHERE notice_parked_at IS NOT NULL`.

Template data: `order_inquiry_reserve_requested_default` and `order_inquiry_reserved_default`
get a bulk branch in `subject` and in each `layout_json` block (`{% if reserve.requests %}` the
short email of rule 8, else today's text byte for byte; the button label branch stays under the
layout's 120-char label limit, `email_layout.py:348`). Updated IN PLACE only while subject/layout
still equal the seeded values (`eml_0002_seed_layouts.py` / `oirs_0001`), same guard as `eml_0002`;
an admin-edited template is left alone (documented limit). Downgrade restores both and drops the
columns.

Crew dev SQL (`crew/state/migrations/OI-STAGED-NOTIFY.sql`): ADD COLUMN IF NOT EXISTS + CREATE
INDEX IF NOT EXISTS (additive); the two guarded `UPDATE email_templates` are held for the owner
(bulk emails render the single shape until applied).

## Backend

`app/services/order_inquiry_reserve_service.py`

- `create_request(..., park: bool = False)`: park -> `parked_at = now`, no queue append.
- `cancel_request` and a commit that completes a request clear `parked_at`.
- `commit_request(..., park_notice: bool = False)`: per touched request row, after the writes:
  park -> `notice_parked_at = now`, `notice_parked_by = actor`, and if `qty_reserved ==
  notified_qty` clear both; no queue append for parked rows. Not park -> today's dispatch, and
  stamp `notified_qty = qty_reserved`, clear `notice_parked_*` on the touched rows.
- `parked_requests_preview(actor)` / `send_parked_requests(actor)` and `parked_notices_preview`
  / `send_parked_notices`: build items (today's `_build_context` / `_build_commit_context` with the
  open / actionable rows), group through `park_send`. Send: `SELECT ... FOR UPDATE` the presser's
  parked rows ordered by id, rebuild groups under the lock, stamp (`parked_at = NULL`; notices:
  `notified_qty = qty_reserved`, `notice_parked_* = NULL`), append each group to the EXISTING
  queue (`_REQUESTED_PENDING_KEY` / `_RESERVED_PENDING_KEY`) in the same transaction; the route
  commits; the existing `after_commit` drain dispatches (`:1182`), `after_soft_rollback` discards.
  A second press blocks on the lock, then finds nothing. Unresolvable groups are not stamped.
  Requests with no open rows and notice rows no longer actionable are cleared without an email.

`app/services/automation_recipients.py`: `include_raiser` / `include_requester` also add every
person in `context["raisers"]` / `context["requesters"]` when present (additive; no other trigger
passes those keys).

Routes (`app/api/v1/projects/order_inquiries.py`):

- `CreateReserveRequestIn.park: bool = False`, `CommitReserveRequestIn.park_notice: bool = False`.
- `GET /order-inquiries/{inquiry_id}/reserve-notices/recipients` -> `{to: {name}, cc: [{name}]}`
  for the confirmation (RESERVE): what today's reserved automation would resolve for this OI's
  open request(s), presser moved to Cc.
- `GET /order-inquiries/reserve-status/summary` -> `{parked, needs_reserve, notice_parked,
  reserved, my_parked_requests, my_parked_notices}` (VIEW/ACKNOWLEDGE/RESERVE).
- `POST /order-inquiries/reserve-requests/parked/preview` and `.../parked/send` (ACKNOWLEDGE);
  `POST /order-inquiries/reserve-notices/parked/preview` and `.../parked/send` (RESERVE).
  Preview -> `{emails: [{to: {name}, cc: [{name}], inquiry_count, line_count, subject, body_html}],
  unresolved: [{inquiry_count, line_count}]}`; send -> `{emails_sent, items_sent, unresolved}`.
- `GET /order-inquiry-headers`: `reserve: Literal["parked", "needs_reserve", "notice_parked",
  "reserved"] | None`; each row gains `reserve_status` (earliest, or null) and `parked_by_name`.

## Frontend

- `ReserveRequestDialog.tsx`: Checkbox "Park this first" in the footer, unticked on open; payload
  `park`; button label follows it; toast "Request #n parked. Send it from Order inquiries > Actions."
- `OrderInquiryDetail.tsx`: Reserve (N) opens new `ReserveConfirmDialog.tsx` (staged lines,
  recipient line from a small `GET .../reserve-notices/recipients` read, "Park notice first"
  tickbox + one primary button); `commitStaged(parkNotice)` sends `park_notice`. Toast on park: "Reserved. Notice to
  <name> parked; send it from Order inquiries > Actions."
- `OrderInquiryHeadersList.tsx`: Reserve filter (SearchableSelect, clearable, counts) synced to
  `?reserve=`, chip, Reserve column (Badge pill + parked-by), toolbar `secondaryActions` rendered
  as the Actions menu even with one item (small `DataGridListToolbar` prop), the two send items
  opening `ParkedSendDialog`; picking a Reserve value sets the state toggle to All.
- Service/hook layering: `orderInquiryReserveService.ts` + `useOrderInquiry.ts` hooks.

## Tests (tester writes first, red)

Backend `tests/test_oi_staged_notify.py` (Postgres, reuse `test_order_inquiry_reserve.py`
fixtures), frontend vitest beside each touched component. List in the UAC.

## Out of scope / follow-ups

- A second document adopting the pattern (trigger: the owner names one).
- "Needs reserve" narrowed per CS person: no assignment field exists; asked with mock v4 (rec: no).
- Admin-edited reserve templates do not get the bulk branch (documented limit).
