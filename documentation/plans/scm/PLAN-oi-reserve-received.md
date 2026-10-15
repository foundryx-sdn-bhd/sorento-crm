# PLAN - Request CS to reserve on an OI line linked to a received SPO

Status: PR #1708 READY for review (reviewer READY, security CLEAN, 15 Oct 2026); browser pass + owner hand test owed. Track: M (new rule on an existing
screen, one additive migration per card Q1). Lane OI-RESERVE-RECEIVED, branch `crew/oi-reserve-received` off
origin/main 504ce9252. UAC: `oi-reserve-received-acceptance-criteria.md` (written after the card
answers).
Domain: SCM, order inquiries. Builds on the shipped "Request CS to reserve" flow
(`documentation/plans/_archive/scm/PLAN-oi-request-cs-reserve.md`).

## The problem

Owner, 15 Oct 2026: an SPO line already received into the warehouse, whose stock went to other
sales orders because the schedule moved, still links an OI line in full (SRT6638, SO qty 1260,
Taken 1260, Remaining 0, PO 202511-S0082 via SPO-2026/01-0005, BRW-IR). The pool location now has
plenty of free stock, so purchasing wants CS to reserve it for this SO line, but "Request CS to
reserve" is disabled because the line is linked.

## Owner rulings (15 Oct ~16:00, final)

1. Only rows whose links point to FULLY RECEIVED SPO/PO lines. Open/incoming links: no change.
2. CS accepts (full or partial): release the WHOLE received link; the reserve covers the line;
   whatever CS could not reserve goes back to Remaining. CS declines (0): nothing changes.
3. The released received SPO must never become free / linkable for other SOs (or this row again).
4. A committed reserve does not hold stock in the CRM. No change to Stock Debt / board holding.
   Stock-transfer push to AutoCount is out of scope.

## Card answers (crew, 15 Oct)

Q1 (a) additive released-links table counted in the target tallies; Q2 release only when the
request reaches into the received part; Q3 mixed rows eligible, received links only; Q4 prefix
the email REASON cell, no template change; Q5 texts as in card rule 8. One additive migration,
no backfill; crew applies it to dev with `./crew migrate` before the hand test.

## Design

Backend (`sorento_crm_backend/`):

1. **Model + migration.** `OrderInquiryReleasedLink` -> `projects.order_inquiry_released_links`
   (same mixins/scoping as `OrderInquiryLink`, models/project_so.py:1137): id UUID, company_id,
   row_id (CASCADE), reserve_request_row_id (SET NULL), po_line_id / spo_allocation_id (one of
   them), document, qty > 0, released_by, released_at. Alembic `oirr_0001_released_links`,
   down_revision `dobs_0001_assume_delivered` (re-check at push), IF NOT EXISTS style.
2. **Requestable.** One helper in `order_inquiry_reserve_service.py` beside `_remaining`
   (:122-130): row.qty - open links - reserve links - bundled. "Received" uses the existing test
   behind `links_for_rows` (project_order_inquiry_service.py:6624-6653), not a new one.
3. **Request gate.** `_REQUESTABLE_STATES` (:66) gains placed. The nothing-remaining gate and the
   qty range (:494-508) use requestable.
4. **Commit.** cap = min(requested, requestable) (:806). Release mode = qty > 0 and requested >
   plain remaining. When it applies: write a released row per received link, delete the link
   (claim freed as `_remove_links` does, project_order_inquiry_service.py:11791), stamp the note,
   then write the reserve link as today (:877-894). Amendments (:819-863) never release.
5. **Tallies.** `_linked_by_target` (project_order_inquiry_service.py:8370-8398) adds released qty.
   `relink_to_matching_lines`' own claimed dict (:11633-11636) adds it too. The book step
   (`follow_book_for_rows` :3441) also leaves out the row's own released targets, so the D3
   displacement (:3606-3634) can never strip another line to re-link it.
6. **Payload.** OI detail rows gain `reserve_requestable_qty` (response schema declared, tested).
7. **Email.** `_build_commit_context` (:358-428) prefixes the row reason for a released row.

Frontend (`OrderInquiryDetail.tsx`):

8. The reserve gate (:119-134) and the dialog default/max (:315-329, ReserveRequestDialog.tsx)
   read `reserve_requestable_qty`. placed rows pass the state gate. New tooltip text (AC-RR-41).

## Slices (one lane, one PR)

1. Tester: red pytest (`tests/test_order_inquiry_reserve_received.py`) + red vitest
   (`OrderInquiryDetail.reserveReceived.test.tsx`) from the UAC. Commit `test(red):` alone.
2. Coder: model + migration + service + payload + FE until green. No assertion edits without
   lead sign-off.
3. Kill-proof, reviewer + security-reviewer (new company-scoped table), browser pass 1280/375,
   crew SQL file, hand test.

Tests run: the touched files plus `tests/test_schema_uuid_id_principle.py`; pytest -n 2 max on
`SORENTO_ENV_FILE=.env.ci-tests` (DB `sorento_oirr_ci`, lane-private).
