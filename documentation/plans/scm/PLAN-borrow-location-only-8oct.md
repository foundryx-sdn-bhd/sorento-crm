# PLAN: borrow = location only (BORROW-LOCATION-ONLY)

Status: Plan, behaviour card posted for the owner (8 Oct 2026). Feature track, size L (engine +
confirm write + Decide UI + guarded data script). Cloud lane, PR #1534. No code before the
card's answers.

## Owner's ask (8 Oct, paraphrased; the verbatim words are in the lane brief)

Sorento does not borrow a specific SO's stock, only a location's (another location, or the
line's own). Turn "borrow order" off to make it simpler. The replenishment of what was
borrowed is tied to the ORIGINAL SO line (the line that borrowed), so OI shows that line, with
its SO qty and its date, and PO/SPO linking finds it. Existing borrow-order records need a fix.

The case: SO424707 line 80 (B2155-NL-BLUE, location BRW-IR) borrowed 6 at BRW-BB. The engine
picked a donor, SO397450 line 446 (due 21 Jul 2027). OI-2610-0022 then shows line 1200 with SO Qty
276 (the donor's), date 21/07/2027 (the donor's), Supplier "Not linked", PO "-".

## How it works today (code, origin/main 57fd71c4)

Backend paths are under `sorento_crm_backend/`.

1. **Engine.** `walk_line` steps are: pool share, own arrival, step 1 "use" (own group plus
   other groups' FREE stock, as a Reserve), step 2 `order_borrow`, step 3 `supply_borrow`, then
   the pool's own later orders, then buy. These are at `app/services/scm/front_planning_engine.py:1297-1344`.
   - Step 2 offers on hand held by a LATER order that can wait. `_eligible_donor`
     (`app/services/project_supply_service.py:3782-3817`) requires covered or pinned, due on or
     after `as_of + lead + 14`, not TBA, and not the asker's SO.
   - Candidates come from `order_borrow_candidates_for` (`:3819-3917`), ranked by same agent,
     then latest due date, then same group, then same warehouse (`:4136-4149`).
   - Each candidate names `donor_so_number`, `donor_line_no`, `donor_core_line_id` and
     `donor_required_date`.
2. **Reason.** `order_borrow_reason` + `_donor_clause` (`front_planning_engine.py:922-968`)
   produce "Borrow 6 on hand at BRW-BB from SO397450 line 446 (agent, due 21 Jul 2027); its debt
   lands in Jul 2027".
3. **Confirm writes:**
   - An allocation on the borrowing line, `source_type` `other_location`, with the donor's line
     id only inside the JSON `donor_impact_snapshot` (`project_supply_service.py:8273-8323`,
     `:8441-8445`).
   - A supersede of the donor's own decision, "Borrowed by SO<asker> line <n>" (`:7258+`,
     called at `:6933`).
   - The order-back row, built by `_borrow_shortfalls` (`:7576-7792`). For a donor-named
     borrow, `"line": donor_line` (`:7707-7710`), `required_date = donor_required_date`
     (`:7713-7717`), note "Order-back: SO397450 line 446 lent 6 to SO424707 line 80".
4. **OI.** `_raise_borrow_shortfalls` (`app/services/project_order_inquiry_service.py:4051-4071`)
   writes that row under the BORROWER's OI header with `so_line_id` = the DONOR's line. So the
   worklist reads SO Qty and line no from the donor line (`order_inquiry_worklist_service.py:780-781`),
   and the date is the donor's due date.
5. **PO/SPO linking.** The book step matches `row.so_line_id` to the core line's `source_ref`
   against `PurchaseOrderLine.from_so_line_ref` / `SPOAllocation.from_so_line_ref`
   (`project_order_inquiry_service.py:3017-3027`, `:3154-3171`). The PO purchasing raised for
   SO424707 line 80 never matches a row hung on SO397450 line 446. **This is the owner's "not
   linked".**
6. **Already location-shaped.** A borrow from another group WITHOUT a donor line already hangs
   its order-back on the BORROWING line, at the donor LOCATION, at the borrowing line's date
   (`project_supply_service.py:7736-7790`). That is the shape the owner describes.
   - A borrow from the line's own location (`:7742-7743`) or from any bin of its own ownership
     GROUP (`:7752-7760`) raises nothing today. The docstring's reason, "its demand is still inside that location's SO
     qty" (`:7604-7608`), no longer holds for project lines: a project line is demand ONLY
     through its OI rows (`app/services/scm/demand.py:479-541`, where the book leg at `:501` excludes
     `demand_class = 'project'`).
7. **Step 3 (`supply_borrow`)** already writes its order-back on the BORROWING line at the
   borrowing line's date (`project_supply_service.py:7224-7251`). Only its reason names the
   donor SO.
8. **No setting** toggles borrow kinds. The cross-group caps were dropped in
   `443_fulfilment_planning_flag_tba_date.py`.

## Behaviour card (what changes; answers pending, see the questions on the PR)

- **B1. One borrow: from a location.** The Decide menu loses "Borrow from another order".
  "Borrow other location" stays, and the line's own location is allowed. The engine's step 2
  offers on hand AT A LOCATION: the same "can wait" stock as today, summed per location, never
  naming a donor SO in the panel, reason, strip or OI.
- **B2. The reason** reads "Borrow 6 on hand at BRW-BB; ordered back to BRW-BB on this line
  (due <line date>)". There is no donor and no "debt lands in <donor month>". The replenishment
  is this line's, so it is due at this line's date.
- **B3. Confirm** writes the order-back on the BORROWING line:
  - `so_line_id` is the borrowing line, so OI SO Qty is that line's.
  - `delivery_date` is the borrowing line's date.
  - `stock_location` is the borrowed location.
  - The note reads "Order-back: BRW-BB lent 6 to SO424707 line 80".

  This holds for every location, the line's own included. The donor's decision is not
  superseded. Reorder demand counts it at the borrowed location exactly as today
  (`demand.py:520-522`).
- **B4. PO/SPO linking** finds the PO bought for SO424707 line 80, because the row now carries
  that line. No change to the linker itself.
- **B5. Frozen decisions** already saved as "Borrow (order)" still render, as history. No new
  ones can be made.
- **B6. Existing data.** A guarded script (`--dry-run` default report, `--apply` second) re-points
  OPEN donor-line ORDER_BACK rows to the borrowing line. Open means `raised` or `partly_linked`
  with nothing linked. A donor-line row is an ORDER_BACK whose `so_line_id` belongs to a
  DIFFERENT sales order than its OI header. That is a structural test, not a note regex: crew
  found the no-line note "Order-back: SO413742 lent ..." that a regex mis-files as a location row.
  The borrowing line comes from the header's SO plus the note's "to <ref> line <m>". The script also sets the borrowing line's date and rewrites the note to
  the location form. It leaves `placed` / `actioned` / `cancelled` rows alone. The owner runs
  it; it never runs on prod from here.

## Open questions (posted as crew-ask on PR #1534)

- Q1, remove vs setting: pending with the owner.
- Q2, the donor's decision: **(a) leave the donor alone** (crew, from the owner's words, 8 Oct).
- Q3, own-location borrow raises an order-back: pending with the owner.
- Q4, the OI row's date: **(a) the borrowing line's date** (crew, from the owner's words, 8 Oct).
- Q5, data fix scope: pending with the owner.

## Data (6 Oct prod snapshot, read-only, via crew; names masked)

- **The case.** SO424707 line 80 (BRW-IR, 6, due 2026-10-06) has one `other_location`
  allocation of 6 at BRW-BB, confirmed 6 Oct. The donor is named only in
  `donor_impact_snapshot`. The OI-2610-0022 ORDER_BACK row sits on SO397450 line 446 (276,
  2027-07-21, BRW-BB), `raised`/awaiting, 6, with no PO and no link. SO397450 has NO decision,
  so nothing was superseded.
- **Donor-named allocations.** 14 borrow allocations name a donor (10 name an SO). All are
  other-location; none are same-location.
- **ORDER_BACK rows naming a donor SO: 7.**
  - 5 sit on the donor line: OI-2609-0678 has 3 on one line (42 + 42 + 16), plus OI-2610-0022
    and OI-2609-0007.
  - 2 are already on the borrowing line (the no-mirror fallback).
  - Open, raised and unlinked: **4 rows / 106 units**. This is the script's scope.
  - Placed and linked: 3 rows / 11 units, which are left alone.
- **Donor allocations with no order-back: 2.** One is superseded and one is open. That is a gap
  of today's code, out of this lane's scope unless the owner asks.
- **No PO/SPO names SO424707 line 80 yet.** After the fix the row will link as soon as
  purchasing raises a PO for that line. It does not link today because no such PO exists.

## Test list (red first, Phase 2)

- Engine: step 2 options carry no donor fields. The reason has no "from SO" and no "debt lands
  in".
- Confirm: a borrow at another location raises one ORDER_BACK row on the borrowing line, with
  that line's date, the borrowed location and the location note. A borrow at the line's own
  location does the same. The donor's decision stays active.
- Linking: a PO line with `from_so_line_ref` = the borrowing line's ref links the order-back
  row.
- Data script: the dry-run reports and writes nothing. `--apply` re-points only open, unlinked,
  donor-form rows and is idempotent on a second run.
- FE: the Decide menu has no "Borrow from another order"; the strip label reads "Borrow".
