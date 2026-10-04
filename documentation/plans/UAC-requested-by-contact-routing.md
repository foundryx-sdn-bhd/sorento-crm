# UAC - "Requested by" as a contact FK + CS routing on the requestor

Acceptance criteria for `PLAN-requested-by-contact-routing.md`. Every line must pass (BE + FE) with an
automated test or a scripted check before manual eyeball. Regression lines are hard blockers.

Legend: ☐ pending · ☑ passed (fill in as verified).

## Journey (Phase 0 - governing)

**Actor:** a contact (salesman / agent) opening the submission portal from a WhatsApp link.

Today the portal asks "Requested by" (PR / SF) and "Salesperson" (stock inquiry) as **free text**,
pre-filled with the submitting contact's own name. CONTACT X submits on behalf of other salesmen and
never for himself. CS routing reads the **submitter**, so CONTACT X's forms miss CONTACT Y's pinned CS and
land on round robin - every time, and there will never be a pin for CONTACT X.

New journey:

1. Contact opens the portal link. System already knows who they are (portal token → respond contact).
2. Form shows **Requested by** as a picker, pre-filled with themselves - one tap for the common case,
   one selection for CONTACT X's case. Nothing else in the journey changes.
3. The list they choose from is not "every contact in the CRM": it is the contacts belonging to
   market segments an admin has marked as selectable (e.g. Project). Names only - no phone, no
   company, nothing else leaked into a portal that any token-holder can open.
4. They submit. The form routes to the CS pinned for the **person the request is for**, not the person
   who typed it. The submitter still owns the conversation: every status update, approval message and
   portal link keeps going back to whoever submitted.

Derived, not asked: the requestor defaults to the submitter; the label text stored on the row is
derived from the chosen contact, so PDFs / list search / portal search need no new field.

## A. Schema / migration

- A1 ☐ Migration adds `market_segments.is_requestor_selectable BOOLEAN NOT NULL DEFAULT false`.
- A2 ☐ Migration adds `purchase_requests.requested_by_contact_id UUID NULL` FK →
  `respond_contacts(id) ON DELETE SET NULL`, indexed. (PR **and** SF share this table.)
- A3 ☐ Migration adds `stock_inquiries.salesperson_contact_id UUID NULL` FK →
  `respond_contacts(id) ON DELETE SET NULL`, indexed.
- A4 ☐ Existing free-text `purchase_requests.requested_by` / `stock_inquiries.salesperson` columns are
  **kept** and remain the display label (PDF, list columns, portal search unchanged).
- A5 ☐ Migration is idempotent (re-run = no-op) and chains onto the committed head; `alembic heads`
  shows exactly ONE head after merge.
- A6 ☐ Downgrade drops the three additions cleanly and leaves the text columns untouched.
- A7 ☐ `alembic upgrade head` then `downgrade -1` then `upgrade head` round-trips on a scratch schema.

## B. Segment gate (catalog + admin UI)

- B1 ☐ `GET` market-segments catalog returns `is_requestor_selectable` per row.
- B2 ☐ `PUT` market segment toggles `is_requestor_selectable`; other fields unaffected.
- B3 ☐ Settings → Market Segments **Edit market segment** modal renders a "Selectable as requestor"
  checkbox; save persists and the DataGrid reflects it.
- B4 ☐ Zero segments flagged → requestor list is EMPTY except the submitter (see D3). No 500, no
  "all contacts" fallback (fail closed - this list is a directory).

## C. Requestor options endpoint

- C1 ☐ Portal (token-auth) `GET .../portal/requestor-options?token=…&q=` returns contacts having ≥1
  **active** segment with `is_requestor_selectable = true`, shape `[{id, name}]` - **names only**.
- C2 ☐ Response contains no phone, no `respond_io_id`, no email, no company, no segment codes.
- C3 ☐ `q` filters server-side (case-insensitive substring on name); results capped (default 50) and
  ordered by name; the cap is documented in the response (`has_more`).
- C4 ☐ Invalid / expired / missing token → 401, never a partial list.
- C5 ☐ Internal (JWT) `GET /api/v1/.../respond-contacts/requestor-select?q=` returns the same set for
  CRM-side edit forms; requires a valid principal (401/403 otherwise).
- C6 ☐ A contact whose only flagged segment is INACTIVE is excluded.
- C7 ☐ Duplicate names are returned as distinct rows (id-keyed); the UI must not collapse them.

## D. Portal form (PR / SF / stock inquiry)

- D1 ☐ "Requested by" (PR/SF) and "Salesperson" (stock inquiry) render as a searchable select, not a
  text input. Placeholder + states: loading / empty / error / data.
- D2 ☐ Default selection = the submitting contact.
- D3 ☐ The submitting contact is ALWAYS an option, even when they belong to no flagged segment
  (self-service can never be blocked).
- D4 ☐ Field is required to submit PR / SF / stock inquiry; validation message is human-readable.
- D5 ☐ Submitting persists BOTH `*_contact_id` and the derived label text.
- D6 ☐ Re-opening a draft / rejected form pre-selects the saved contact by id and shows its name
  (no UUID visible anywhere, per the cursor rule).
- D7 ☐ A saved contact that has since lost eligibility still renders its name when re-opened
  (`selectedOption` passthrough), and can be kept on re-submit.
- D8 ☐ Works at ~375px width (mobile): the picker opens, scrolls, and is dismissible.
- D9 ☐ Complaint portal form is **untouched** (its `salesperson` stays free text this slice).

## E. Routing (the actual bug)

- E1 ☐ CONTACT X submits a PR with requested_by = CONTACT Y, Eric has a CS pin matching the form →
  the tracker assigns to Eric's pinned CS.
- E2 ☐ Same, but Eric has NO pin → **round robin** (never falls back to the submitter's pin).
- E3 ☐ `requested_by_contact_id` NULL (legacy / internal-created row) → behaviour byte-identical to
  today: pin lookup on the submitter, then round robin. **REGRESSION (hard).**
- E4 ☐ `tracker.respond_contact_id` is ALWAYS the submitting contact, never the requestor - so the
  chat panel, `_notify_*` WhatsApp sends, and portal links keep addressing the submitter.
- E5 ☐ Stock inquiry `project_sales` and `purchasing` stages both use the requestor for pin lookup.
- E6 ☐ PR/SF approval stage: default-approver override still wins over pin + round robin (unchanged).
- E7 ☐ Requestor contact deleted (FK SET NULL) → routing degrades to E3, no exception.
- E8 ☐ Editing `requested_by` after submit does NOT re-assign an already-active tracker; the next
  spawned stage uses the new value. Asserted, not incidental.
- E9 ☐ Every failure path in requestor resolution degrades to today's behaviour and logs - a bad FK
  can never 500 a portal submit.

## F. Backfill

- F1 ☐ `scripts/backfill_requested_by_contact.py --dry-run` reports matched / ambiguous / unmatched
  counts and writes NOTHING (no autoflush side effects).
- F2 ☐ Matching is case-insensitive exact on the contact's name and on `first_name + ' ' + last_name`;
  "CONTACT Y" → CONTACT Y, "ERIC" → CONTACT Y only when exactly one contact matches.
- F3 ☐ Ambiguous ("Cindy" with both *Cindy* and *CONTACT W* present) → left NULL and listed in the
  report. Never guessed.
- F4 ☐ Idempotent JOIN-based "set where mismatch": re-running corrects prior wrong values, not just
  NULLs (per the backfill lesson).
- F5 ☐ Batched by keyset, not `yield_per` + commit; verified at `--batch 1`.
- F6 ☐ Covers `purchase_requests` (PR + SF) and `stock_inquiries`; does not touch complaints.
- F7 ☐ Live run output is captured in the PR description (matched / left-null lists).

## G. Tests

- G1 ☐ pytest: routing matrix E1-E9 as service-level tests on Postgres (no sqlite), seeding real FK
  targets.
- G2 ☐ pytest: endpoint tests for C1-C7 (happy path + auth denial + validation).
- G3 ☐ pytest: backfill matcher table F2-F3 as pure-function tests (paraphrase/casing table, not one
  hard-coded string).
- G4 ☐ vitest: portal picker component - loading / empty / error / data, default-to-submitter,
  required validation, saved-but-ineligible contact renders.
- G5 ☐ playwright: portal → open PR → pick a requestor other than self → submit → assert the
  `/api/v1/*` payload carries `requested_by_contact_id` and the row lands on the expected assignee.
- G6 ☐ Full backend suite green with zero `errors` (exclusive DB).

## H. No-regression

- H1 ☐ PR / SF / stock inquiry PDF exports render "Requested by" / "Salesperson" exactly as before.
- H2 ☐ Portal search by requestor name (`portal_service` ilike on the text columns) still matches.
- H3 ☐ CRM list columns + DataGrid sorting on those fields unchanged.
- H4 ☐ Internally-created (non-portal) PR / SF / stock inquiry rows still submit with the field empty
  where it is optional today.
