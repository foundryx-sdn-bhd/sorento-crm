# Behaviour card: split one SO line into delivery tranches (OI-LINE-SPLIT)

Base: origin/main c2a3b162e. Size L (new interactive panel, new stored data = migration, email and
Excel change). Branch `crew/oi-line-split`. All paths: BE = `sorento_crm_backend/`, FE =
`sorento_crm_frontend/app/(protected)/project-sales/`.

Owner 16 Oct ~21:20: "1 line can split into multiple lines, different delivery date ... in autocount,
the user change 1 line, reduce qty, change delivery date, then add new lines, so we dont have the
data ... allocate 1 line to multiple lines, allocate the qty, before they confirm, or when they click
confirm ... drag and drop ... very very flexible".

## Today (code)

1. One OI row per SO line, by owner rule (25 Aug, SO414285: "1 line here should correspond to 1 line
   in sales order"), `BE/app/models/project_so.py:1138-1145`. The row holds the CURRENT qty and date
   (`qty` :1016, `delivery_date` :1017) and the "Was" values (`previous_qty`,
   `previous_delivery_date` :1099-1105). Free text: only `note` (:1056). No tranche / split column.
2. AutoCount edits arrive by DtlKey (`BE/app/services/document_ingest_service.py:1450-1530`): a
   known line updates in place, a new key is a new line. The planning change classifies each line
   on its own (`BE/app/services/planning_change_service.py:800-810`: qty_down, qty_up, delayed,
   advanced, added; date wins when both moved). "Reduce + add new lines" arrives as a `qty_down` on
   the old line plus unrelated `added` / `qty_up` lines. Nothing ties them together.
3. Verbs: date later = DELAY, earlier = ADVANCE, qty decrease = CANCEL_BALANCE
   (`BE/app/services/project_order_inquiry_engine.py:148-179`). So the reduced part is told to
   purchasing as CANCEL BALANCE and the new line as a fresh ORDER.
4. The only automatic split (`_apply_pure_splits`, `BE/app/services/project_order_inquiry_service.py:5308`)
   fires only when the new lines have the SAME product, location AND date and take exactly the
   difference. Any date change falls back to netting. The owner's cases all change dates.
5. Confirm (Fulfilment board, `POST /sales-orders/{pso_id}/confirm`,
   `BE/app/api/v1/projects/fulfilment_planning.py:1447` -> `ProjectSupplyService.confirm`
   `BE/app/services/project_supply_service.py:5302`) needs each line's parts to add up to exactly
   its open qty (:6404). It has no date per part.
6. Email "ORDER, ADVANCE, DELAY, CANCEL BALANCE": one row per saved SO line
   (`record_saved_lines`, `BE/app/services/project_order_inquiry_service.py:4280-4295`), QTY and
   DELIVERY from "was", NEW QTY / NEW DATE from the line (template `oihr_0006` :67, REMARK column
   since `oihr_0008`). OI Excel (`store_handover_excel` :12608 -> `export_xlsx`
   `BE/app/services/order_inquiry_worklist_service.py:3328`): one sheet per delivery month, one row
   per OI row, no NEW QTY / NEW DATE / REMARK column (:195-222).
7. No AutoCount write anywhere in Confirm (grep of the three services; UNVERIFIED beyond that grep).
   This lane writes nothing to AutoCount.

## Rules (proposed)

1. A **split** belongs to one SO line (its AutoCount DtlKey). It is a list of **tranches**: qty,
   delivery date, remark. The OI keeps ONE row per SO line (owner rule above); the tranches sit under
   that row, like the merged cells in the staff Excel.
2. **Balance**: line qty minus the tranches = the balance, always on screen. The balance is either
   "Remain at <original date>" (default) or "Cancel balance" (one toggle). Tranches can never add up
   to MORE than the line qty (more qty is a new order and comes from AutoCount).
3. **Verb per tranche** (same rule as today's engine): date later = DELAY, earlier = ADVANCE, same
   date = REMAIN; the cancel balance = CANCEL BALANCE.
4. **When**: (a) before Confirm, from the SO line on the Fulfilment board (saved with the line's
   draft, nothing sent); (b) inside Confirm, from the AutoCount change review of a reduced or moved
   line ("Split instead of cancel"). The OI Lines tab shows the split read-only (expand the row).
   Purchasing gets it with the next Confirm email.
5. **Later AutoCount lines (reconcile)**: each tranche can name the AutoCount line it now sits on,
   picked BY STAFF from that SO's lines of the same item and location. Never auto-linked by qty,
   date or name. A linked tranche's qty is taken off that line's "new" qty (no second ORDER) and off
   the origin line's cancel balance (no false CANCEL). Unlinked tranches show "Not in AutoCount yet".
   Each AutoCount line shows "Explained" when its qty = its own remain + the tranches linked to it,
   else "N unexplained".
6. **Email / Excel**: the origin line prints once (QTY, DELIVERY); each tranche prints as a sub-row
   (NEW QTY, NEW DATE, REMARK with the verb). The OI Excel gets the same sub-rows and a REMARK column;
   each tranche lands on its own month sheet.
7. **Interaction** (mock): drag a divider on the qty bar to move qty between tranches; drag a tranche
   card to another date lane (or "New date"); add / remove / split in two / merge from a right-click
   menu; every action also by keyboard (Tab to a divider or card, arrows move 1, Shift+arrows 10).
   Nothing is saved until Save; an error keeps the panel open with the values.

## Edge cases

- Partly delivered line: only the open qty can be split; the delivered part shows locked.
- Closed or cancelled line: no split.
- Tranche qty 0 is removed; two tranches on the same date offer to merge.
- A tranche whose linked AutoCount line is later deleted or cancelled drops the link and shows
  "Not in AutoCount yet" again.
- Re-split after Confirm: allowed; the change goes to purchasing with the next Confirm email (same
  as any change today).
- Whole-date delay (SO382618, staff "DELAY SCHEDULE" email: 8 dates each moved): today AutoCount
  already carries it line by line (dev: SO382618 open lines now on 10/11/2026, 24/11/2026,
  19/01/2027, 09/03/2027, 23/03/2027 ...), each a plain DELAY. Not part of this lane unless the owner
  asks (dragging a whole date lane would be the extension).

## Real examples

1. **SO417636 CSK14B** (owner Excel + 13/10 email; NOT in the dev DB yet: dev SO417636 has 284 lines
   and no CSK14B, newest dev OI is 14 Oct): line 417 @ 20/10/2026 -> 14 @ 20/09/2026 (ADVANCE, remark
   "Under pre-order, SO422271 already emailed"), 236 @ 20/11/2026 (DELAY), 167 @ 20/11/2029 (DELAY,
   ORDER); line 504 @ 20/12/2026 -> 37 @ 20/11/2026 (ADVANCE) + 467 @ 02/01/2027 (DELAY); 192 and 301
   unchanged. AutoCount netted it to 417->14, 192->632, 504->649 (the 145 extra on 504 and the 182
   total increase are not explained by the screenshots: UNVERIFIED).
2. **SO402757 SRTWB7237** (dev DB, OI-2609-0009): line 2 was 51 @ 15/07/2026, AutoCount cut it to 2
   and line 18 grew 82 -> 121 @ 01/11/2026. Purchasing was told CANCEL BALANCE 49 ("Was 51, now 2")
   and ORDER 121. With a split: line 2 = 2 remain @ 15/07 + 49 DELAY to 01/11/2026, linked to line
   18; line 18 "Explained" (72 own + 49 linked = 121; dev change rows for line 18: qty_down 82 -> 72,
   qty_up 82 -> 121, plus an added 49 @ 01/11/2026 line that AutoCount later cancelled).
3. **SO402757 SRTSA-SS** (dev): line 7 172 @ 01/10/2026 -> 100 @ 01/11/2026, plus new line 103
   126 @ 01/01/2027. Split: 100 DELAY to 01/11/2026 + 72 DELAY to 01/01/2027 linked to line 103;
   line 103 shows "54 unexplained" (a real increase, ordered as today).
4. **SO391691 SRTWT5908** (dev, 7 Sep batch): line 1 220 @ 25/02/2026 -> 82, new line 246 @
   25/02/2026. Same date: today's pure split would not fire either (246 is not the 138 difference);
   split 82 remain + 138 linked to the new line, which shows "108 unexplained".
5. **SO382618** (dev): the whole-date delay above, no split needed.

## Questions (each with a recommendation)

- **Q1 Where it opens**: (a) Fulfilment board line (before Confirm) AND the AutoCount change review
  inside Confirm, one shared panel; OI Lines tab read-only; (b) inside Confirm only. Rec (a): the
  owner asked for both moments, and one panel keeps one behaviour.
- **Q2 Balance**: (a) balance defaults to "Remain at original date", one toggle to "Cancel balance";
  when AutoCount already reduced the line it defaults to "Cancel balance" (today's behaviour);
  (b) tranches must always add up exactly, no balance. Rec (a): staff rarely know every tranche up
  front; the default keeps today's meaning.
- **Q3 Reconcile with later AutoCount lines**: (a) staff link each tranche to an AutoCount line
  (same SO, item, location; exact qty and date listed first, never auto-linked); (b) auto-link exact
  matches. Rec (a): owner rules "no auto matching" and exact links only.
- **Q4 Across SO lines**: (a) a tranche belongs to its origin line and only NAMES the AutoCount line
  it sits on; (b) drag qty freely between different SO lines (re-shapes the SO). Rec (a): AutoCount
  stays the truth for what each line holds; the split only explains it.
- **Q5 Email + Excel**: (a) origin line once, tranches as sub-rows with verb + remark, in both the
  email and the OI Excel (new REMARK column); (b) email only, Excel unchanged. Rec (a): purchasing
  orders from the Excel month sheets, so a tranche must land on its own month.
