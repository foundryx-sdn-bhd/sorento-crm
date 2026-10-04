# PLAN: OI line follows the SO line's product change from AutoCount (OI-PRODUCT-FOLLOW)

Status: built, review round 1 fixed, on hand test (feature track M, one migration). Owner rulings R1-R6 below.
Track: feature (M, carries one migration, so not the small fix track). Cloud lane.
Lane: OI-PRODUCT-FOLLOW, branch `claude/oi-product-sync-6uoz28`, PR #1442.
Domain: scm (ESB sales order ingest, order inquiry rows, planning board apply).
Prod SQL: `oi-product-follow-prod-readonly.sql` (read-only) and
`oi-product-follow-correction-PROPOSED.sql` (owner approves, crew never runs it), alongside.

## Problem (owner, 2 Oct, prod)

SO423414 (TEXON CONSTRUCTION) had its line products changed in AutoCount (same line refs),
but OI-2609-0776 still shows the old products (MWCX7604-S-RL-NEW / MWCY7604 / MWC-SC04-QQ
vs the SO's MWCX7604-SH-S10 / MWCY7604-SH / MWC7604-SC-SH).

Owner rule: when an SO line's product changes, the linked OI line switches to the new
product exactly like quantity and delivery date already do, keeping the old value visible
("was X").

## Measured (origin/main 4f8e97c8, `sorento_crm_backend/`)

How a qty/date change reaches an OI row today (the flow to copy):

1. ESB push: `document_ingest_service._sync_lines` matches the line by `source_ref` and
   writes every column in place (1522-1523), `product_id` included.
2. `_sync_mirror_line` (1370-1392) copies `required_date` and `qty_ordered` to the mirror
   `projects.sales_order_lines` row. **Not `product_id`.** The manual SO edit path does
   move it (`scm/sales_order_service.py:1806-1815`), so the two writers disagree.
3. The planning hook (`api/v1/external/ingest.py:385-420`) diffs before/after by line id
   (`scm/outstanding_diff.py:236-260`); a swapped item code is ONE `product_changed` row
   (`:194-207`), gated on the order holding a decision or a live OI row
   (`planning_change_service.py:1097-1122`, `:2190`).
4. A planner applies the row on the board: `supply.confirm(settle_in_place_line_ids=...)`
   (`planning_change_service.py:4382`) -> `refresh_for_decision` ->
   `_settle_row_in_place` (`project_order_inquiry_service.py:1894-1910`), which writes
   `qty`, `delivery_date`, `previous_qty`, `previous_delivery_date`, flips an
   acknowledged row to `changed` and records the handover "was".
5. **`OrderInquiryRow.item_code` has no writer after creation** (only the raise at
   `:1477` / `:1590` / `:4121`). The apply entry already carries the NEW code
   (`project_supply_service.py:6853`, `_carried_lines` patches identity at `:5318-5328`),
   and `refresh_for_decision` finds the row by `so_line_id` + verb (`:1012-1019`), so the
   row is restated on qty/date and keeps the old product. That is the SO423414 symptom.
6. Side effect of (2): `auto_place_products` reads `entry["line"].product_id`
   (`project_supply_service.py:6955-6961`), the stale mirror product.
7. There is no `previous_item_code`; the "was" UI reads `previous_qty` /
   `previous_delivery_date` (`_shared/lib/orderInquiryAck.ts:90-98`,
   `orderInquiryWorklistColumns.tsx` QtyCell 740-750, `orderInquiryLineFold.ts:279-289`).

## The owner's two "already works" claims, verified

**(1) A new SO line reaches the OI through the existing flow: ALREADY WORKS (via the board).**
ESB creates the core line (`document_ingest_service.py:1543-1553`), mirrors it when the
order is an adopted mirror (`:1589-1605`), and `build_batch` raises an `added` row for an
order that holds a decision or a live OI row (`planning_change_service.py:922-940`,
`:1097-1122`). The OI row is raised when a planner applies that row (CS raises), never
automatically. **WESERP10B on SO423414**: expected reason is that its `added` row is still
PENDING on the planning board (nobody applied it). Read-only Q3 confirms or refutes; if Q3
shows no `added` row at all, Q1 will show whether the line has no mirror (an authored
project order is not self-healed at `:1593-1603`).

**(2) An OI row whose SO line AutoCount cancelled shows cancelled: ALREADY WORKS for a
removed line, GAP for a zeroed line.**
- AutoCount line REMOVED from the push: the leftover sweep marks it `cancelled` because the
  mirror line references it (`document_ingest_service.py:1560-1574`), calls
  `flag_rows_for_cancelled_lines` (`:1578-1585`), and the OI reads `line_cancelled` off
  `SalesOrderLine.line_status` (`order_inquiry_worklist_service.py:482-484`) and shows
  "Line cancelled" (`orderInquiryLineFold.ts:256`, `OrderInquiryVerbPill.tsx:131`). Works.
- AutoCount line kept with qty set to 0: `_line_status` returns `open` for ordered 0
  (`document_ingest_service.py:1819-1825`), so nothing is cancelled and nothing is flagged;
  the OI row stays as it was until a planner applies the `cancelled` change row. The manual
  edit path DOES cancel a line zeroed on the transition (`scm/sales_order_service.py:1817-1828`).
- **M-FH14 138 on OI-2609-0776**: either (a) the zeroed-line gap above (Q1 shows
  `qty_ordered = 0`, `line_status = open`), or (b) the line IS `cancelled` and the row is
  waiting on purchasing's Confirm (ruling C1 of `PLAN-oi-cancelled-line-used-confirm.md`:
  a cancelled-line row goes to To confirm and the header sits Outstanding until confirmed),
  or (c) the row's `so_line_id` / mirror core link is NULL so no line status reaches it
  (Q2 `mirror_core_line` NULL). Q1 + Q2 tell which.

## Owner rulings (2 Oct, on the card)

- **R1 (Q1)** The OI line switches to the new product when CS clicks Confirm in fulfilment
  planning: the same apply moment as qty/date today.
- **R2 (Q2)** A row already linked to a PO/SPO line is NOT unlinked. The link follows
  AutoCount and flows through; the change is flagged with the EXISTING mechanism the
  qty/date change uses on linked rows (`ack_state` -> `changed`, `changed_at`,
  `_dispatch_changed_with_links`). No new unlink behaviour.
- **R3 (Q3)** Fix ALL rows already wrong on prod. Correction script for the owner:
  BEGIN, dry-run count first, default ROLLBACK, plus the read-only check. Crew never runs it.
- **R4 (email)** On Confirm the handover email to purchasing says
  "change item code to <new>" (was <old>), in the same style as the qty/date change lines.
- **R5 (Q4)** New SO lines: today's flow (they appear on Confirm in fulfilment planning).
  No change.
- **R6 (Q5)** A line set to qty 0 in AutoCount stays as today (cancel balance on confirm).
  No change; the qty>0->0 auto-cancel proposal is dropped (S4 removed).

## Design (per the rulings, as built)

- **S1 mirror follows product (ingest).** `_sync_mirror_line` also copies `product_id`
  when the push carries it, same as the manual edit path. Fixes item 6 too.
- **S2 OI row follows product (Confirm).** Migration `oipf_0001_prev_item_code`:
  `order_inquiry_rows.previous_item_code` (nullable). `_settle_row_in_place`: when the
  entry's `item_code` differs from the row's and the row's code is a catalogue code
  (`_is_catalogue_code`; NOT compared with the mirror line, so a line swapped before S1,
  SO423414, still moves), the row takes the new code, keeps the old in
  `previous_item_code`, notes "Was item X", and counts as a real change (acknowledged ->
  `changed`, `changed_at`, `_dispatch_changed_with_links`, handover). Links stay exactly as
  they are (R2). When the settle DECLINES (two live rows, a lone placed row with no link,
  every row actioned), `_restate_product` makes the same writes on every live buy row of
  the line and `_tell_product_moves` puts it in the email once (review round 1, S2).
- **Re-raised rows (tester FAIL on 3db1012a, 3 Oct).** Two shapes re-raise the line
  instead of restating a row: a row whose PO/SPO is already RECEIVED is set aside as
  "used" (history keeps its old code and its link, R2) and a fresh "Replaces N used" row
  is raised; and a line carried along by a Confirm of another line is cancelled and
  re-raised. In both the fresh row is where R1's switch shows: it carries
  `previous_item_code` + "Was item X", and its handover line adds
  "CHANGE ITEM CODE TO <new> (WAS <old>)" with the headline. No ruling exempts either
  shape (R1 is not limited by link state; R2 only forbids unlinking).
- **"Was" rule (review S5).** Every real settle says what THIS change moved:
  `previous_item_code` = old code only when the product moved, else NULL; a product-only
  change clears `previous_qty` / `previous_delivery_date` (no false "Was 10 -> Now 10").
- **S3 UI.** Product cell on the worklist and the OI Lines tab (one shared `ItemCodeCell`):
  new code, muted "was X" under it. The History dialog shows it through the row's note
  ("Was item X"), no separate prefix.
- **S4 email (R4).** The settled handover line carries `was.item_code`; REMARK reads
  "CHANGE ITEM CODE TO <new> (WAS <old>)" after any qty/date phrase, ITEM CODE prints the
  new code, and the headline names CHANGE ITEM CODE. No template migration: the REMARK
  cell already exists in every layout.
- **Known limits.** A product RENAME (same product, new code) is not followed: the old
  code is no longer a catalogue code. The undo email of a product swap names the current
  code (the replay itself restores the column correctly).

## Test list (red first, all built)

- `tests/test_oi_product_follow.py`: ESB re-push moves the mirror `product_id`; an
  identical re-push leaves it.
- `tests/test_oi_product_follow_apply.py`: Confirm restates in place with "was"; product-
  only writes no previous qty/date; acknowledged -> changed; a linked row keeps its link
  and is flagged (R2); a mirror still on the old product still moves (SO423414); a
  non-catalogue code is never rewritten; a declined settle (placed, no link) still moves
  and tells purchasing once; no "was" when nothing moved; email REMARK + headline; pure
  remark join "ORDER 5, CHANGE ITEM CODE TO ..."; worklist payload carries
  `previous_item_code`.
- `orderInquiryWorklistColumns.productFollow.test.tsx`: the Product cell renders "was X"
  only when the product moved.
