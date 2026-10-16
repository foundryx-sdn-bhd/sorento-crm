# CARD: planning a consignment from its own pages (CONSIGN-PLAN)

Status: filed as a crew-ask on PR #1712 (15 Oct). Crew answered Q4 = (a) and Q5 = (a) (15 Oct);
Q1-Q3 with the owner. No code until the owner answers.

Owner request (15 Oct, verbatim): "Our consignment page don't have a plan like sales order,
right? Our consignment page supposed to look exactly like sales order page in terms of the
buttons also. Like plan selected, we need that also. So for consignment, most of it, the
location is CON consignment. So the user need to select it to say borrow using the decide and
borrow button."

Examples are taken from the AC-CONSIGN-CRM plan's measured dev facts (already in the repo,
masked). This sandbox cannot reach the dev DB; line quantities and CON stock are asked for in
the PR's `crew-data-request` and will be added before the browser check.

## Today (main, read in code)

1. **Consignment detail** reuses the sales order screen (`scm/consignments/[id]/page.tsx:29`,
   `SalesOrderDetail docType="consignment"`). The Plan button and the "planned" chip are hidden
   on purpose for a consignment (`SalesOrderDetail.tsx:1620`, `:1679`), because CONSIGN-DELIVERED-FIX
   (14 Oct) said "its goods are already at the customer, nothing to plan".
2. **Consignment list** (`ConsignmentsList.tsx:42-46`) has search only: no row ticks, no
   Plan selected, no Actions menu. The SO list has Plan selected (N) as its main button
   (`planActions.ts:49-72`, max 50) and an Actions menu of Upload, Add, Reset planning (N),
   Refresh (`SalesOrdersGrid.tsx:1213-1239`).
3. **Plan is only a link.** Plan selected opens `/project-sales/fulfilment-planning?orders=...`;
   nothing is saved by pressing it.
4. **The board WOULD take a consignment today.** `_demand_rows`
   (`project_fulfilment_board_service.py:1555-1560`) admits `open` AND `closed` orders of
   demand class `project`, with `is_undecided_demand()` (`scm/demand.py`): not cancelled, not
   covered, plan qty > 0. Delivery is deliberately ignored (14 Sep ruling, SO421404: "a delivered
   unit nobody sourced is a unit to put back"). A consignment is always `closed` with
   delivered = qty (14 Oct ruling), so every undecided project-class consignment line shows up,
   planned at its full qty. There is no doc_type filter. (Crew flagged this as unverified; the
   code says admitted. A red test will pin it.)
5. **CON is not a planned bin.** Migration 443 turns `warehouses.fulfilment_planning` on only for
   codes ending `-BB|-IB|-IR|-NTC|-AM` (`443_fulfilment_planning_flag_tba_date.py:51,93`). CON does
   not match, so unless an admin switched it on: every consignment line (all sit at CON) reads
   **"Outside fulfilment planning"** on the board, has no Decide, cannot be confirmed
   (`project_supply_service.py:9295-9318`), and CON is never offered as a Borrow donor (R17:
   an off bin gives no stock, no demand and no donor). So with today's configuration the owner's
   flow ("select CON, Decide, Borrow") cannot happen even once the buttons exist.
6. **Decide menu** on ticked board lines: As suggested / Use own location / Borrow from another
   order / Borrow / Use BRW / Buy / Transfer to Cancel SO (`BoardDecideControl.tsx:61-69`).
   "Borrow" (`borrow_other`) picks ONE donor warehouse from the server's candidate list for the
   whole line (`boardAmend.ts:946-990`). There is no separate "Decide and borrow" button; it is
   Decide, then Borrow.
7. **Returns are not netted.** `plan_qty()` = qty_required or qty_ordered, less qty_cancelled.
   `qty_returned` is ignored, so a Fully returned consignment would still be planned in full.

## Rules (proposed)

- **R1 Buttons.** The Consignment list gets row ticks, **Plan selected (N)** as its main button
  (same component, same max 50, same permission `projects.projects.view`) and an Actions menu
  with **Reset planning (N)** and **Refresh**. No Upload and no Add: a consignment only arrives
  from AutoCount (AC-CONSIGN-CRM ruling stands). The consignment detail gets the same **Plan**
  button and **planned chip** a sales order has, under the same rule (project demand class).
- **R2 What "plan" means.** Planning a consignment answers the same question as planning an
  order: where did this line's stock come from. The goods already left for CON when the CG was
  issued; without a decision that movement is "stock that left with nothing behind it".
- **R3 What "Borrow" from CON means (Q1).** Decide, Borrow, donor = CON: the line's qty is
  recorded on the decision as taken from CON. Confirm writes the decision revision and a hold
  on CON stock for the line; **no Buy, so no Order Inquiry row and no order-back to
  purchasing**, and the CON units are no longer offered to any other line. No stock moves: the
  CRM records the decision, AutoCount already moved the goods.
- **R4 CON as a donor (Q2).** CON becomes plannable and offered as a donor for consignment lines
  only; sales order lines never see CON stock.
- **R5 Returns (Q3).** For a consignment, plan qty = ordered less cancelled less returned. A
  Fully returned consignment has nothing to plan: it drops off the board, and its Plan button
  and tick stay but the board says there is nothing to decide.
- **R6 Lines not at CON.** No special case: a consignment line at any other bin plans exactly
  like a sales order line at that bin (own location, borrow, BRW, buy).
- **R7 Everything else unchanged.** Consignments stay out of sales figures, the Stock Debt /
  outstanding / reorder readers (`is_open_demand`, status `open`) still skip them, and Cancelled
  consignments are refused by the board as today.

## Examples (masked dev records, AC-CONSIGN-CRM plan)

| Consignment | Dealer | Agent (class) | Lines | Location | Status | What the planner does |
| --- | --- | --- | --- | --- | --- | --- |
| CG-2024/10-002 | 300-S075 | project | 2 lines, keyed return R-01 | CON | At customer (partly returned) | Ticks it on the list, Plan selected (1), board shows 2 lines at qty less returned; Decide, Borrow, CON; Confirm. No Buy row. |
| CG-202606-062 | 300-J096 | project | 3 lines incl. two `SAMPLE` | CON | At customer (key-less partial return R-H) | Same; the two SAMPLE lines are decided together (both tick, one Decide). |
| CG-202608-072 | 300-V033 | project | 1 line | CON | 'CVT TO DO' return | If the return covers the whole line: Fully returned, nothing to plan (R5). |
| Any retail-class CG | any | retail | any | CON | At customer | No Plan button (same as a retail SO); board would show nothing. |

Exact line quantities and CON on-hand per product: pending the `crew-data-request`.

## Edge cases

- **Already closed / delivered**: every consignment is. Admitted by the board (Today 4); that is
  what makes planning possible at all.
- **Cancelled consignment**: refused by the board as today; Plan selected still navigates but
  the board shows nothing for it.
- **Mixed locations on one CG**: each line plans on its own bin (R6).
- **CON on hand < line qty** (stock at CON already transferred back or out of sync): Borrow from
  CON offers only what CON holds; the rest stays undecided or goes to Buy, the planner's choice,
  same as any donor short of stock.
- **Returns arriving after a confirm**: the returned units are back in the warehouse; under R5 the
  plan qty drops; whether the existing planning-change flow reopens a confirmed line on that
  (as it does for a Cancel SO) is not yet verified and gets a test.
- **Mixed selection** (sales orders and consignments on one board): allowed, the board is a URL
  of order numbers; nothing distinguishes them except the Consignment badge already shown.

## Questions (each with a recommendation)

1. **What Borrow means for a line at CON.** (a) donor = CON: "these goods are the CON stock",
   hold on CON, no Buy; (b) donor = a BRW / -BB bin: record the warehouse the goods left from,
   CON ignored; (c) Buy: purchasing buys back what went out on consignment.
   **Recommend (a)**: it is what "the location is CON, select it, borrow" says, and it is the only
   one that never raises a purchase for goods the company still owns.
2. **CON's planning switch.** (a) code: CON is plannable and a Borrow donor for consignment lines
   only; (b) config only: an admin turns CON's Fulfilment planning flag on in Warehouses, no code,
   but then the ladder proposes CON stock (goods sitting at dealers) to every sales order and
   CON's lines net into the groups. **Recommend (a)**, because (b) would promise dealers' stock
   to other customers.
3. **Returned units.** (a) plan qty nets qty_returned for consignments (a Fully returned CG has
   nothing to plan); (b) plan the full ordered qty regardless. **Recommend (a)**: a returned unit
   is back in the bin and never needed a source.
4. **List buttons.** (a) Plan selected (N) + Actions with Reset planning (N) and Refresh, no Upload
   or Add; (b) also an Upload / Add for consignments. **Recommend (a)**: consignments only come
   from AutoCount (AC-CONSIGN-CRM ruling 1), so Upload / Add would create records nobody can sync.
   **Answered (crew, 15 Oct): (a).**
5. **Demand class.** (a) Plan only for project-class consignments, exactly like sales orders;
   (b) let the board take consignments of any class. **Recommend (a)**: the board is built for
   project demand; a retail CG has no ownership group to plan against.
   **Answered (crew, 15 Oct): (a).**
