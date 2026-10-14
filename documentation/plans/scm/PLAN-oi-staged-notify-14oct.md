# PLAN: park reserve requests and reserve notices, send them in bulk (OI-STAGED-NOTIFY)

Status: Plan. Standard track, size L (additive migration + in-place template layout update, two
sides, new filter/column/bulk send/preview). Lane crew/oi-staged-notify. Card answered by crew
14 Oct (Q1-Q5 all (a)); owner notes on mock v1 applied in mock v2 (default send, one summary
preview, short email with one button) and on v2 in mock v3 (send under the Actions menu, email
preview in the dialog, "Sent in email" filter in Filters); v3 awaiting owner.

UAC: `oi-staged-notify-14oct-acceptance-criteria.md` (alongside). Card:
`oi-staged-notify-behaviour-card.md`. Mock: `documentation/mockups/OI-STAGED-NOTIFY/index.html`.

## Journey

Owner 14 Oct: purchasing asks CS to reserve across ~100 OIs; each "Request CS to reserve"
emails CS at once (`order_inquiry_reserve_service.py:558`) and each CS Reserve emails
purchasing (`:966`), so ~100 emails each way. Park first, review, send one email per person.

## Rules (card, answered)

1. Request lightbox: tickbox "Park this first", UNTICKED on every open (owner: default send).
   Unticked = sent now as today ("Send request"). Ticked = request saved, no email ("Park request").
2. CS header: tickbox "Park notice" beside "Reserve (N)", unticked on every page load. Ticked =
   reserve saved, notice to purchasing parked. Unticked = emailed now as today.
3. Documents list only (Q4): "Parked" filter (Requests to CS (n) / Notices to purchasing (n),
   everyone's), "Parked" column (kind + who parked), and the toolbar's Actions menu (owner v2:
   not a top-bar button) with "Send parked requests to CS (N)" (ACKNOWLEDGE) and "Notify
   purchasing (N)" (RESERVE); N = the presser's OWN parked items (Q1). Picking a Parked filter
   switches the state toggle to All. A "Sent in email" filter (owner v2) lists the most recent
   100 send batches, newest first, searchable, labelled "Joey to Eling, reserve requests,
   14/10/2026 10:42 (9)"; the email button opens the list with it set.
4. Preview: a table To | Cc | Order inquiries | Lines, one line per email, and under it the
   rendered email itself, one line tab per email (owner v2), then "Send N emails".
   Unresolvable recipients show as a "No recipient" line and stay parked.
5. One email per To person, Cc merged (Q2). To/Cc are exactly what today's automation would
   resolve per item (`automation_recipients.py`, `automation_service.py:641-660`); items are
   grouped by that first non-actor address.
6. Built at send time: requests carry only rows CS has not answered (`qty_reserved IS NULL`);
   notices carry only rows whose `qty_reserved` differs from `notified_qty` (actionable).
7. "Saved = sent" (#1631): a notice row changed after it was sent is parked again on the next
   parked commit; an amend back to the sent figure drops it out.
8. Bulk subject (Q5): "Reserve requests: N order inquiries (name)" / "Reserved: N order
   inquiries (name)". Bulk body (owner): heading with the count, intro with OI and line counts,
   first 5 OIs (OI NO, S/O NO, CUSTOMER, LINES), "and N more", ONE button "View the N order
   inquiries waiting for your reserve" / "View the N order inquiries CS reserved" that opens the
   Documents list filtered to exactly that email's OIs. A one-OI email keeps today's subject and
   body unchanged.
9. No automatic send. Handover email path untouched (crew 14 Oct).

## Schema (migration `oisn_0001_staged_notify`, down_revision `ac_consign_0001_doc_type`)

Additive, nullable, no backfill (NULL = not parked = legacy rows count as sent):

- `projects.order_inquiry_reserve_requests.parked_at` TIMESTAMP NULL
- `projects.order_inquiry_reserve_request_rows.notice_parked_at` TIMESTAMP NULL,
  `notice_parked_by` VARCHAR(100) NULL FK users SET NULL, `notified_qty` NUMERIC(15,4) NULL
- Partial indexes: requests `WHERE parked_at IS NOT NULL`, rows `WHERE notice_parked_at IS NOT NULL`.
- Who parked a request = `requested_by` (only the requester can park it).
- New table `projects.order_inquiry_reserve_send_batches` (id, company_id, kind
  `requests`|`notices`, sent_by, sent_at): one row per email a bulk send produces. Requests get
  `sent_batch_id`, request rows get `notice_batch_id` (UUID NULL, FK SET NULL, indexed). The
  email button opens `/project-sales/order-inquiries?send_batch=<id>` (one param; the batch's
  `kind` says which side; same idea as the expiry reminder's `?expiry_notify_batch_id=`,
  `automation_service.py:608-611`). The batch row also stores `recipient_name` (the To person) and
  `inquiry_count` for the filter label.

Template data: `order_inquiry_reserve_requested_default` and `order_inquiry_reserved_default`
get a bulk branch in `subject` and in each `layout_json` block (`{% if reserve.requests %}` the
short bulk email of rule 8, else today's text byte for byte; the button label branch stays under
the layout's 120-char label limit, `email_layout.py:348`). Updated IN PLACE only while subject/layout still equal
the seeded values (`eml_0002_seed_layouts.py` / `oirs_0001`), same guard shape as `eml_0002`; an
admin-edited template is left alone (bulk then renders through its single shape: documented
limit). Downgrade restores the seeded values and drops the columns.

Crew dev SQL (`crew/state/migrations/OI-STAGED-NOTIFY.sql`): the ADD COLUMN IF NOT EXISTS +
CREATE INDEX IF NOT EXISTS part is additive; the two guarded `UPDATE email_templates` are held
for the owner (bulk emails render the single shape until applied).

## Backend

`app/services/order_inquiry_reserve_service.py`

- `create_request(..., park: bool = False)`: park -> `parked_at = now`, no queue append.
- `commit_request(..., park_notice: bool = False)`: per touched request row, after the writes:
  park -> `notice_parked_at = now`, `notice_parked_by = actor`, and if `qty_reserved ==
  notified_qty` clear both (nothing actionable); no queue append for parked rows. Not park ->
  today's dispatch, and stamp `notified_qty = qty_reserved`, clear `notice_parked_*` on the
  touched rows. Requests touched only by parked rows queue nothing.
- New `parked_requests_preview(actor)` / `send_parked_requests(actor)` and
  `parked_notices_preview(actor)` / `send_parked_notices(actor)`, sharing one grouping helper:
  per item build today's context (`_build_context` / `_build_commit_context` with the open /
  actionable rows), resolve recipients with the first enabled automation of the trigger
  (same pick as `notified_name`, `:1135`), group by the first non-actor address. A group of 1
  keeps today's context; a group of >1 gets `reserve.requests[]`, `reserve.count`,
  `raisers[]`/`requesters[]`, link to the Documents list filtered to Parked.
- Send: `SELECT ... FOR UPDATE` the presser's parked rows ordered by id, rebuild groups under
  the lock, insert one send-batch row per group, stamp (`parked_at = NULL`, `sent_batch_id`;
  notices: `notified_qty = qty_reserved`, `notice_parked_* = NULL`, `notice_batch_id`), append each group to the EXISTING post-commit queue
  (`_REQUESTED_PENDING_KEY` / `_RESERVED_PENDING_KEY`) in the same transaction. The route
  commits; the existing `after_commit` drain dispatches (`:1182`), `after_soft_rollback`
  discards. A second press blocks on the lock, then finds nothing (UAC). Unresolvable groups
  are not stamped.
- Requests with no open rows left, and notice rows no longer actionable, are cleared at send
  without an email. `cancel_request` and a commit that completes a request (`state ->
  reserved`) clear `parked_at`, so the Parked filter never lists a closed request.

`app/services/automation_recipients.py`: `include_raiser` / `include_requester` also add every
person in `context["raisers"]` / `context["requesters"]` when present (additive; no other
trigger passes those keys).

Routes (`app/api/v1/projects/order_inquiries.py`):

- `CreateReserveRequestIn.park: bool = False`, `CommitReserveRequestIn.park_notice: bool = False`.
- `GET /order-inquiries/reserve-parked/summary` -> `{requests_total, notices_total,
  my_requests, my_notices}` (VIEW/ACKNOWLEDGE/RESERVE).
- `POST /order-inquiries/reserve-requests/parked/preview` and `.../parked/send` (ACKNOWLEDGE).
- `POST /order-inquiries/reserve-notices/parked/preview` and `.../parked/send` (RESERVE).
  Preview returns `{emails: [{to: {name}, cc: [{name}], inquiry_count, line_count, subject,
  body_html}], unresolved: [{inquiry_count, line_count}]}`, `body_html` rendered through the
  automation's own template exactly as the send will (`EmailTemplateService.render`); send
  returns `{emails_sent, items_sent, unresolved}`.
- `GET /order-inquiries/reserve-send-batches?limit=100` (newest first) and `.../{id}` ->
  `{id, kind, sent_by_name, recipient_name, sent_at, inquiry_count}` for the filter options and
  the chip (VIEW/ACKNOWLEDGE/RESERVE; company-scoped, unknown or foreign id 404).
- `GET /order-inquiry-headers`: `parked: Literal["requests", "notices"] | None`,
  `send_batch: UUID | None` filters; each row gains
  `parked_request_by_name` and `parked_notice_by_name` (null when nothing parked).

## Frontend

- `ReserveRequestDialog.tsx`: Checkbox "Park this first" in the footer, reset to UNTICKED on open;
  payload `park`; button label follows it; toast "Request #n parked. Send it from Order inquiries."
- `OrderInquiryDetail.tsx`: Checkbox "Park notice" beside Reserve (N), unticked on mount;
  `commitStaged` sends `park_notice`.
- `OrderInquiryHeadersList.tsx`: Parked filter (SearchableSelect, clearable, counts), Parked
  column, toolbar `secondaryActions` for the two items rendered as the Actions menu even with
  one item (small `DataGridListToolbar` prop), "Sent in email" SearchableSelect (clearable,
  options from the batches list) synced to `?send_batch=`, chip "Sent in email: <label>"
  (state toggle All), cleared by its X.
- New `ParkedSendDialog.tsx` (one component, two modes): summary table (To, Cc, Order
  inquiries, Lines, a "No recipient" line when unresolved), line tabs one per email with
  `EmailPreviewFrame` showing `body_html` (clicking a table line selects its tab), "Send N
  emails", toasts, invalidate headers + summary + batches.
- Service/hook layering: `orderInquiryReserveService.ts` + `useOrderInquiry.ts` hooks.

## Tests (tester writes first, red)

Backend `tests/test_oi_staged_notify.py` (Postgres, reuse `test_order_inquiry_reserve.py`
fixtures), frontend vitest beside each touched component. List in the UAC.

## Out of scope / follow-ups

- Parked state on the OI detail page (no indicator beyond the list) - trigger: owner asks.
- Admin-edited reserve templates do not get the bulk branch (documented limit).
