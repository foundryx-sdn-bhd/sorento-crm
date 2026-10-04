# PLAN - "Requested by" as a contact FK + CS routing on the requestor

**Status:** drafted 2026-07-27; decisions D1-D11 locked with the user (see §3). Implementation IN PROGRESS
2026-07-28: migration + models + schemas landed by the orchestrator; routing, endpoints, backfills and both
frontends built in parallel. Gate before handoff: FE pass + BE pass + orchestrator code review + whole plan
implemented, THEN one prod build.

## 1. Problem (verified in code + live data)

`FormSLAOrchestrator._start_for_config` (form_sla_service.py:800-813) resolves the CS assignee as:

```python
assignee = self._resolve_pinned_assignee(config.source_entity_type, contact_id, team_id, source_entity_id)
if not assignee:
    assignee = agent_svc.get_next_assignee(str(agent.id), team_id)
```

`contact_id` is the **submitting** respond contact. `_resolve_pinned_assignee` filters
`RespondContactCsRouting.respond_contact_id == contact_id`. CONTACT X (contact
`9ce8ca9e-d0b4-4cf2-8061-82debf2c02b2`) submits PR26-0338 / PR26-0339 / PR26-0340 on behalf of others;
he has no pin and never will, so every one of those forms round-robins instead of reaching the CS
pinned for CONTACT Y.

The requestor is currently **free text**: `purchase_requests.requested_by TEXT` (PR + SF share the
table), `stock_inquiries.salesperson TEXT`, pre-filled in the portal from the submitter
(`SubmissionForm.tsx` field spec `defaultFromContact: 'fullname'`). Free text can't be routed on.

## 2. Approach

Add a nullable **contact FK** beside each text column, make the portal pick it from a
segment-gated list, and use it as the pin-lookup key while leaving the tracker's
`respond_contact_id` (= messaging target) on the submitter.

Non-goals this slice: complaint's `salesperson`; segment-scoped round-robin cursors on the form path
(the form path calls `get_next_assignee` without a contact today - untouched); any change to pin
semantics or predicates.

## 3. Decisions (locked with the user 2026-07-27)

| # | Decision |
|---|---|
| D1 | Submitter keeps receiving all updates. Tracker `respond_contact_id` stays the submitter. |
| D2 | Requestor has no pin → round robin. Never fall back to the submitter's pin (that is the bug). |
| D3 | Requestor dropdown exposes **names only**. |
| D4 | Backfill existing rows by name; "CONTACT Y"/"ERIC" → CONTACT Y; ambiguous left NULL and reported. |
| D5 | Gate the dropdown on market segments, admin-controlled per segment. |
| D6 | Eligible set = flagged-segment contacts **∪ the row's submitting contact ∪ the currently-saved requestor**. Nobody can ever be blocked from submitting, and editing a row whose requestor lost eligibility can't silently blank the field. Applies to the portal AND the CRM staff picker. |
| D7 | The staff picker uses the **same gated list** as the portal - one definition of "who can be a requestor". Missing person → tag their contact once, both surfaces pick it up. |
| D8 | Changing `requested_by` after submit **never** re-routes a live tracker. Later stages use the new value. Re-routing is a human action - and it needs a **Reassign action on the form detail page** (see §5a), because today `ReassignDialog` is only wired into `TeamPendingList` / `MyPendingSLAWidget` and users rarely open the SLA screens. |
| D9 | Display resolves the contact's **live name** when the FK is set; the stored text column stays as the point-in-time record (and the fallback for legacy FK-less rows). A rename fixes every screen with no backfill. |
| D10 | Segment gate is **one boolean** on the segment (`is_requestor_selectable`), surfaced as an indicator column in Market Segments master data so an admin can see at a glance which segments are included. |
| D11 | Complaint keeps its free-text `salesperson`. Pins are keyed `(contact, use_case)` and only `purchase_request` / `sponsorship_form` pins exist; `_start_for_config` even documents "Complaint never reads the pin table", so an FK there buys display consistency and zero routing benefit. |

**Local-data caveat:** the local copy shows only 2 of 55 contacts segmented and zero tagged `project`.
That is stale - prod has every respond contact segmented. Do not size this feature (or conclude the
dropdown ships empty) from the local `respond_contact_market_segments` table.

## 4. Data model

```
market_segments
  + is_requestor_selectable BOOLEAN NOT NULL DEFAULT false

purchase_requests            -- PR + SF
  + requested_by_contact_id  TEXT NULL FK respond_contacts(id) ON DELETE SET NULL   (indexed)
    requested_by             TEXT        -- kept: display label, derived from the FK on save

stock_inquiries
  + salesperson_contact_id   TEXT NULL FK respond_contacts(id) ON DELETE SET NULL   (indexed)
    salesperson              TEXT        -- kept: display label
```

**TEXT, not UUID** (corrected during implementation): `respond_contacts.id` is TEXT in the live schema,
and every existing contact reference follows it (`purchase_requests.contact_id`,
`stock_inquiries.contact_id` are both TEXT). A UUID column cannot FK to a TEXT key - Postgres rejects it
with "incompatible types: uuid and text" at ALTER TABLE time. Same applies to
`attachments.uploaded_by_contact_id`.

Keeping the text column is deliberate: PDF templates, list columns, DataGrid sort and
`portal_service` search all read it today (`ilike` on `requested_by` / `salesperson`), so nothing
downstream changes and legacy rows stay readable. The FK is the routing key; the text is the label.

Eligibility set = contacts with ≥1 **active** `market_segments` row where `is_requestor_selectable`,
via the existing `respond_contact_market_segments` M2M. Fail closed: no flagged segment → empty list
(plus the submitter, D3/D-open-1).

## 5. Backend changes

1. **Migration** (single revision, chained onto the committed main head): three additions from §4.
2. **Models:** `app/models/access.py` MarketSegment flag; `app/models/procurement.py`
   `PurchaseRequestHeader.requested_by_contact_id`, `StockInquiry.salesperson_contact_id`.
3. **Schemas:** market-segment create/update/response gain the flag (add to BOTH manual dict builders
   if any exist - see the `get_user`/`system_settings` lesson); PR/SF/SI create+update+response gain
   the FK.
4. **Requestor options service** `app/services/requestor_options_service.py`:
   `list_requestor_options(db, q=None, limit=50, include_contact_id=None) -> (rows, has_more)`
   returning `[{id, name}]` only. One query: join contact → M2M → segment where flag + active, union
   the `include_contact_id` row.
5. **Endpoints:**
 - portal, token-auth: `GET /api/v1/public/portal/requestor-options?token=&q=` (token resolves the
     submitter, passed as `include_contact_id`).
 - internal, JWT: `GET /api/v1/master-data/respond-contacts/requestor-select?q=`.
6. **Portal write path** (`portal_service`): add the FK to `_editable_fields` for
   `purchase_request` / `sponsorship_form` / `stock_inquiry`; validate the id is in the eligible set
   (or is the submitter) and 422 otherwise; derive + stamp the text label from the chosen contact;
   emit the FK in `_serialize_*_detail`.
7. **Routing** (`form_sla_service`): new
   `_routing_contact_id(source_entity_type, source_entity_id) -> Optional[str]` that reads the header
   FK (`purchase_requests.requested_by_contact_id`, `stock_inquiries.salesperson_contact_id`, else
   None), wrapped so any failure returns None. In `_start_for_config`:

   ```python
   routing_contact_id = self._routing_contact_id(config.source_entity_type, source_entity_id) or contact_id
   assignee = self._resolve_pinned_assignee(config.source_entity_type, routing_contact_id, team_id, source_entity_id)
   if not assignee:
       assignee = agent_svc.get_next_assignee(str(agent.id), team_id)   # D2 - no submitter retry
   ```

   `ConversationSLATracking.respond_contact_id` continues to be set from `contact_id` (D1).
8. **Backfill** `scripts/backfill_requested_by_contact.py`: keyset-batched, `--dry-run` default,
   idempotent JOIN-based set-where-mismatch, prints matched / ambiguous / unmatched.

## 6. Frontend changes

- `app/(auth)/portal/components/SubmissionForm.tsx`: the three field specs switch from text to a new
  `contactSelect` kind backed by `SearchableSelect` in async mode (`fetchOptions` → portal endpoint,
  `selectedOption` from the saved row so an ineligible saved contact still renders its name).
  Default = submitting contact.
- CRM-side edit forms for PR/SF and stock inquiry: same picker via the internal endpoint.
- Settings → Market Segments: "Selectable as requestor" checkbox in the create/edit modal + a column
  in the DataGrid.
- Detail pages keep rendering the label text (no UUIDs in the UI).

## 7. Phasing (three-phase loop)

- **Phase 1 - FE prototype.** Picker in the portal form + the segment checkbox against mocked
  options (`__mocks__/requestorOptions.ts`), all states exercised, Playwright MCP screenshots of
  golden path + empty + error + 375px. Contract for both endpoints documented at the top of the
  service file. No backend code.
- **Phase 2 - BE + tests, test-first.** Migration → models/schemas → options service+endpoints →
  portal write path → routing. Routing matrix (UAC E1-E9) written as failing pytest first, then the
  `_routing_contact_id` change. Then vitest + playwright per UAC G. Backfill dry-run against the
  local prod copy, output pasted into the plan.
- **Phase 3 - review.** `/code-review`, then PR.

## 8. Risks

- **Directory exposure.** The portal endpoint hands a list of contact names to any token holder.
  Mitigated by: names only, segment opt-in, fail-closed empty list, server-side cap. Worth a second
  look during grill - this is the one genuinely new external surface.
- **Silent behaviour flip.** Rows with the FK set route differently than yesterday. UAC E3 pins the
  NULL path byte-identical so only opted-in rows change.
- **Label drift.** Renaming a contact leaves the stored label stale. Accepted (the label is a
  point-in-time record of what was submitted) - see open question 4.

## 8a. Added by the grill - Reassign on the form detail page

D8 depends on a human being able to re-route, and today they effectively cannot: `ReassignDialog`
(`app/(protected)/sla-management/conversation-sla-tracking/components/ReassignDialog.tsx`) is imported
only by `TeamPendingList` and `MyPendingSLAWidget`. No form detail page renders it, and users rarely
open the SLA screens.

- Add a **Reassign** item to the form detail gear menu (complaint / PR / SF / stock inquiry) beside the
  existing "Escalate SLA" and `SlaExtendMenuItem`, acting on the same `activeTracker` those already use.
- Reuse `ReassignDialog` as-is; no new endpoint.
- Invalidate BOTH tracker queries afterwards (`form-sla-trackers` AND `form-sla-tracking`) - the lock
  banner and the SLA banner are separate queries and one of them goes stale otherwise (known gotcha).
- Permission-gate it the same way the SLA screens do.

## 9. Resolved by the grill

All six original questions are answered - see the decision table in §3 (D6-D11). Nothing in this plan is
open; the remaining work is Phase 1.

## 9a. Original questions (kept for the record)

1. **Submitter always selectable?** Plan says yes (never block self-service) even when they belong to
   no flagged segment. Alternative: hard-gate and let an unsegmented contact submit nothing.
2. **Internal CRM edit forms:** same segment gate, or the full contact list for staff?
3. **Re-route on change?** Plan says an active tracker is never re-assigned when `requested_by`
   changes post-submit; only later stages pick up the new value. Should CS get an explicit
   "re-route to requestor's CS" action instead?
4. **Label sync:** leave the stored text as submitted (current plan), or re-derive on every read?
5. **Segment flag granularity:** one boolean on the segment (current plan) vs a per-form-type gate
   (PR-selectable / SI-selectable separately).
6. **Complaint parity:** its `salesperson` stays free text - confirm that's intended, since the same
   on-behalf-of pattern could appear there.
