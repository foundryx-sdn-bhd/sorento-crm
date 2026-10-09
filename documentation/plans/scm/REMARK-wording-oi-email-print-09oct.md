# OI handover email: every REMARK value, current vs proposed (OI-EMAIL-PRINT, owner ask 9 Oct)

Owner, after the layout PASS (9 Oct, about 09:5x): REMARK must be simple and concise, ideally
3 words or fewer. "CHANGE ITEM CODE TO SRT382-6-DIY (WAS SRT382-6)" is too complex.

**Owner rulings (9 Oct), built:** Q-A (b) `ORDER BACK` with no document number; Q-C (a)
`MOVED TO <SO>`; Q-D (a) `ORDER 64` / `CANCEL 4` / `CANCEL ALL`; Q-E (a) `NEW ITEM CODE`;
combos as proposed; the headline word for a product change matches (`NEW ITEM CODE`).
**Q-B pending** (owner asked what PRE-ORDERED is; answered on PR #1543): row 5 still reads
`PRE-ORDERED, DO NOT ORDER`. Row 3's proposal below is superseded by Q-A (b).

Measured on head 12ecdc24. All REMARK text comes from four places:

- `handover_remark()` in `sorento_crm_backend/app/services/project_order_inquiry_service.py:375-443`;
- its verb spellings, `REMARK_SPELLING` (same file :267-278);
- `_tell_product_moves` (same file :2133-2134), which appends the item-code phrase to a line
  that is already queued;
- for reference only, the undone email's OUTCOME column (`project_supply_undo_reconstruct_service.py`,
  `project_supply_undo_service.py`).

Combos are the parts below joined by ", ".

The line table already shows the facts several remarks repeat:

- ITEM CODE prints the NEW code;
- QTY / NEW QTY print the old and new quantity;
- DELIVERY / NEW DATE print the old and new date.

So the remark only has to say WHAT purchasing must do, not the numbers.

## Proposed rule

REMARK = the verb(s) only, each one to three words, with no number and no code that another
column already shows. The one exception is a document number purchasing has to look up
(ORDER BACK), which stays.

## Single values

| # | Kind | Code path | Today | Real example (masked) | Proposed | Rec |
|---|------|-----------|-------|------------------------|----------|-----|
| 1 | raised ORDER | `:400` via `REMARK_SPELLING` `:268` | `ORDER` | SO418869 Buy 140 | `ORDER` | keep |
| 2 | raised RESERVE & ORDER | `:400`, `:269` | `RESERVE & ORDER` | 2025-12-18 golden OI file | `RESERVE & ORDER` | keep |
| 3 | raised ORDER BACK with doc | `:400-402`, `:277` | `ORDER BACK SPO-2026/08-0061` | SO402757 SRTWT6808 16 | `ORDER BACK SPO-2026/08-0061` | keep: the doc is what purchasing looks up (Q-A) |
| 4 | raised ORDER BACK, no doc | `:400`, `:277` | `ORDER BACK` | borrow order-back with no cited doc | `ORDER BACK` | keep |
| 5 | raised PRE-ORDERED | `:400`, `:274` | `PRE-ORDERED, DO NOT ORDER` (4 words) | 2025-12-18 golden OI file | `PRE-ORDERED` | shorten: the word already means "do not order" (Q-B) |
| 6 | raised ALREADY INBOUND | `:400`, `:275` | `ALREADY INBOUND` | inbound SPO already covers the line | `ALREADY INBOUND` | keep |
| 7 | raised ADVANCE (amendment date earlier) | `:400`, `:270` | `ADVANCE` | CS amend moves 30/10 to 23/10 | `ADVANCE` | keep |
| 8 | raised DELAY (amendment date later) | `:400`, `:271` | `DELAY` | SO418869 line 2 | `DELAY` | keep |
| 9 | raised CANCEL BALANCE (amendment qty down) | `:400`, `:273` | `CANCEL BALANCE` | CS amend 10 to 8 | `CANCEL BALANCE` | keep |
| 10 | raised CHANGE SO NO, target known | `:403-414`, `:272` | `CHANGE SO NO TO SO418870` (5) | repoint to another SO | `MOVED TO SO418870` | the target is the instruction (Q-C) |
| 11 | raised CHANGE SO NO, no target | `:400`, `:272` | `CHANGE SO NO` | repoint without a stamp | `CHANGE SO NO` | keep |
| 12 | settled ADVANCE | `:427` | `ADVANCE` | SO402757 01/10 to 25/09 | `ADVANCE` | keep |
| 13 | settled DELAY | `:427` | `DELAY` | SO418869 30/10 to 13/11 | `DELAY` | keep |
| 14 | settled qty up | `:430-431` | `ORDER 64` | SO402757 372 to 436 | `ORDER 64` | keep: 2 words, and the number is the extra to buy, which no column shows (Q-D) |
| 15 | settled qty down | `:428-429` | `CANCEL BALANCE 4 NOS` (4) | SO402761 12 to 8 | `CANCEL 4` | shorten: drop BALANCE and NOS (Q-D) |
| 16 | settled product change | `:433-437`, `:2133` | `CHANGE ITEM CODE TO SRT382-6-DIY (WAS SRT382-6)` (8) | owner's case, AutoCount swapped SRT382-6 to SRT382-6-DIY | `NEW ITEM CODE` | the new code is in ITEM CODE; the old code is dropped (Q-E) |
| 17 | raised row on a new product | `:415-421` | `ORDER, CHANGE ITEM CODE TO SRT382-6-DIY (WAS SRT382-6)` (9) | owner's case, fresh Buy on the new product | `ORDER, NEW ITEM CODE` | as 16 |
| 18 | cancelled (book took the line to 0) | `:440-442` | `CANCEL BALANCE 36 NOS` (4) | SO cancel, line of 36 | `CANCEL ALL` | QTY prints 0 and the old qty is the row's own record; "ALL" says it plainly (Q-D) |

## Combos (multi-change lines)

Each line is parts from the table above, joined by ", ". The same rule shortens each part.

| # | Today | Proposed | Words | Fits one line at 11px |
|---|-------|----------|-------|-----------------------|
| C1 | `ADVANCE, ORDER 64` | `ADVANCE, ORDER 64` | 3 | yes |
| C2 | `DELAY, ORDER 6` | `DELAY, ORDER 6` | 3 | yes |
| C3 | `ADVANCE, CANCEL BALANCE 5 NOS` | `ADVANCE, CANCEL 5` | 3 | yes |
| C4 | `DELAY, CANCEL BALANCE 2 NOS` | `DELAY, CANCEL 2` | 3 | yes |
| C5 | `DELAY, CHANGE ITEM CODE TO X (WAS Y)` | `DELAY, NEW ITEM CODE` | 4 | yes |
| C6 | `DELAY, ORDER 6, CHANGE ITEM CODE TO SRT382-6-DIY (WAS SRT382-6)` (owner's case) | `DELAY, ORDER 6, NEW ITEM CODE` | 6 | yes (about 160px; REMARK has about 210px) |
| C7 | `ORDER, CHANGE ITEM CODE TO X (WAS Y)` | `ORDER, NEW ITEM CODE` | 4 | yes |
| C8 | `ORDER BACK SPO-..., CHANGE ITEM CODE TO X (WAS Y)` | `ORDER BACK SPO-..., NEW ITEM CODE` | 6 | 2 lines in the widest layout |

C5 to C8 cannot reach 3 words while still naming every change: one line can move its date, its
quantity and its product in the same confirm. The proposal keeps every change to 1 to 3 words,
so the cell stays one line in practice. C8 is the only one that can wrap, and only with an
ORDER BACK document plus a product change.

## Undone email OUTCOME column (reference only; separate template, not in this PR)

| # | Code path | Today | Meaning | Proposed |
|---|-----------|-------|---------|----------|
| U1 | `project_supply_undo_reconstruct_service.py:432`, `project_supply_undo_service.py:985` | `removed` | the confirm's raised row is deleted | `REMOVED` |
| U2 | `project_supply_undo_reconstruct_service.py:473` | `restored` | a settled / repointed row goes back to its old values | `RESTORED` |
| U3 | `project_supply_undo_reconstruct_service.py:516` | `reinstated` | a row the confirm cancelled is live again | `REINSTATED` |
| U4 | `project_supply_undo_reconstruct_service.py:539` | `re-covered` | a row the confirm released to the pool is linked again | `RELINKED` |
| U5 | `project_supply_undo_service.py:1001` | `back to 24 on 30/10/2026` (5) | journalled undo restores qty/date; QTY and DELIVERY DATE already print 24 and 30/10/2026 | `RESTORED` |

## Headline (not REMARK, unchanged unless the owner says)

The heading lists the verbs once (`_HANDOVER_VERB_LABEL`, `:306-319`), e.g.
`ORDER, DELAY, CHANGE ITEM CODE`. If 16 becomes `NEW ITEM CODE`, the headline word should
match it (Q-E).

## Questions (recommendation first)

- **Q-A** ORDER BACK document: (a) keep `ORDER BACK <doc>`; (b) `ORDER BACK` only. Rec (a):
  the doc is what purchasing looks up.
- **Q-B** `PRE-ORDERED, DO NOT ORDER`: (a) `PRE-ORDERED`; (b) `DO NOT ORDER`. Rec (a): it is
  the client's own word.
- **Q-C** CHANGE SO NO: (a) `MOVED TO <SO>`; (b) `CHANGE SO NO` with no target. Rec (a).
- **Q-D** Quantities: (a) `ORDER 64` / `CANCEL 4` / `CANCEL ALL`; (b) keep the current
  `CANCEL BALANCE N NOS` spelling. Rec (a).
- **Q-E** Product change: (a) `NEW ITEM CODE` with no codes (the new code is in ITEM CODE);
  (b) `WAS SRT382-6`, the old code in short form. Rec (a), or (b) if purchasing needs the old
  code to find the PO line.
