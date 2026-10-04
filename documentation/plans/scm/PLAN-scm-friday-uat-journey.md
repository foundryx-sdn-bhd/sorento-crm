# PLAN - Friday UAT: the end-to-end supply chain journey

Status: DRAFT for the UAT sheet, 2026-08-26. Hand this file to the session that generates the UAT sheet from the phase 1 template.
Lane: `.claude/worktrees/scm-uat` (FE :3080, BE :8080). Stack of PRs: #301 > #305 > #308 > #309 > #310 > #311 > #313, then I (links), G (PO occupancy), P3/P4/P7/P8, part 3 (SO change), S12 (WhatsApp answers).
Companion documents: `PLAN-scm-cs-planning-uat.md` (part 1, board side), `PLAN-scm-purchasing-uat-journey.md` (part 2, purchasing side), `scm-cs-planning-uat-fixture.md` (row by row expectations for SO381895), `scm-cs-planning-uat-acceptance-criteria.md`.

## 1. Purpose

One dataset, one story, walked in order by the people who will use the system. Each station has an actor, an action, and what the actor must see. The sheet is one row per hand-off: Step, Actor, Screen (sidebar path), Data used, Expected, Actual, Pass / Fail, Finding.

## 2. Cast

| Actor | Person | Stations |
| --- | --- | --- |
| Admin | Eling | 1 SO upload, 11 SO change |
| CS | Eling | 2 Fulfilment planning, including the order inquiry sheet upload for the ORDER BACK rows |
| Warehouse | Eling (Aiman observes; he is not in this process) | 3 Stock transfers, 7 Stock upload |
| Purchasing | Joey | 4 Order inquiries (Link PO / Link SPO) |
| Buyer | Josephine and Mr Loo | 5 Reorder planning |
| Buyer who places the PO | Joey | 6 Confirm PO, key into AutoCount, re-upload PO book |
| Purchasing (fulfilment) | Ms Tee | 8 Loading plan, 9 Proforma invoice to packing list, 10 SPO |
| Sales (WhatsApp) | every office person present, on a Sorento phone number | 12 Salesperson questions |

## 3. Dataset (frozen Wednesday night)

| Data | Source | Used at |
| --- | --- | --- |
| SO book with SO381895 (YOTU BUILDER / LOT 2752, agent CONTACT N, 76 lines) and the 148 open retail orders that had no class (all 15 customers ruled retail on 26 Aug) | AutoCount SO export | 1, 11 |
| CS Order Inquiry Forms (1) 12 Aug 16:10, (2) 19 Aug 10:25, (3) 19 Aug 17:23 | `documentation/plans/scm/fixtures/SO381895-form-{1,2,3}-*.xlsx` | 2, 11 |
| 2026 PO and SPO book | AutoCount PO export | 6, 10 |
| Proforma invoice KAILU `KL20260717` (17 Jul; SRTWT7443, SRTWT8203, SRTWT8258-GM; unit price in RMB; cites 202605-S0060 and 202605-S0084) | `Sorento/phase-2/User Requirements/purchasing/fulfilment_example_files/KAILU形式发票(Sorento)260717.xlsx` | 9, 12 |
| Pre-loading list JINBAICHUAN 31 Jul (several proforma blocks in one sheet: SRTWC287A-RL-250 408, SRTWC8152-SH-300-UF 376, ...; container number blank) | `.../fulfilment_example_files/2026-7-31 SORENTO 预装清单.xlsx` | 9, 10 |
| Stock upload of the day | AutoCount stock export | 7 |
| Expected outcome per SO381895 form row (verb, qty, location, link target, not-in-system marks) | `scm-cs-planning-uat-fixture.md` | 2, 4, 11 |

Known facts about the data, so nobody logs them as failures:

- SO381895's sales agent in the CRM is CONTACT N (IB group). Cyndi is the CS who raised the forms.
- 14 ORDER BACK rows on forms (1) and (2) have no SO line in the book (closed in AutoCount). They only enter through the order inquiry sheet upload, with no SO line. We walk them.
- `SPO-2026/08-0046` is not in the system. `202606-S0019` is (45 lines, open SRTWC7405-SC at BRW-IB).
- Every SPO in the book carries a promised date in the past. By the 26 Aug ruling ("trust the book") they still count as incoming and read "overdue N days".
- SRT382-6-DIY on SO415472 reads Buy 71 because the BRW pool is kept for dealer hot-selling items (ruled 26 Aug: the gate stays). Not a failure.
- Ladder v3 proposes stock, not Buy, on several SO381895 rows (C-FH14, CB2805A-DIY, CSH2073, SRTWT2207, the 7405 items). Where the form says ORDER, the CS amends to Buy; the fixture marks these [S].

## 4. The journey, station by station

Walk in this order. Station 11 needs the links from station 6; station 12 runs last so every answer has data behind it.

| # | Station | Actor | Action | Must see | Sidebar path | Build status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | SO upload | Admin (Eling) | Upload the SO book | Import verdict; an unclassified order is refused by name (we pre-classify the 15 customers, so the real upload passes); SO list and SO detail | Supply Chain > Orders > Sales orders > Upload | Built; refuse-unclassified lands with P4 |
| 2 | Fulfilment planning | CS (Eling) | Plan SO381895 from forms (1) and (2): read legend, cell bars, popover (Suggestion and Decision cards, location table with Where / PO qty / Taken), Amend (Buy is a whole-line switch), Order back with the cited document, Approve. Upload the form for the 14 no-line ORDER BACK rows | Decision strip Suggested vs Decided; one OI row per SO line raised; transfers proposed for every reserve from another location; Raised by = Eling | Supply Chain > Project Demand > Fulfilment Planning > tick SO > Plan | Built (#301, #305, #308, #310); Order back verb and one-row-per-line with I |
| 3 | Stock transfers | Warehouse (Eling) | Approve the proposed transfers, mark one moved with an AutoCount reference, cancel one with a reason | Transfers page states; SO detail Transfers tab; nothing closes by itself | Inventory management > Stock transfers | Built (#309) |
| 4 | Order inquiries | Purchasing (Joey) | Read the rows CS raised: every one of them Awaiting, none linked. Tick three and press **Acknowledge (3)** - they link at that moment. **Reject** one with a reason (an empty reason is refused). Then the rest as before: Link PO on an ORDER row; Link SPO on an ORDER BACK row to the cited document; Unlink one (confirm dialog); filter by Acknowledgement = Awaiting / Acknowledged / Changed / Rejected and by Linked = po / spo / none; search by Eling's name | The Acknowledged column reads Joey's name and time on the three, and Linked n of q fills in without a reload; the rejected row reads "Rejected: <reason>" and stops counting as demand; tiers same location > same group > pool > sibling; PO issue date first; Raised by and Raised at | Supply Chain > Project Demand > Order Inquiries | Raised by built (#301); links with I; the handshake with `PLAN-scm-oi-handshake.md` |
| 5 | Reorder planning | Buyer (Josephine, Mr Loo) | Run a manual plan with no warehouse picked; read the Project column for SO381895's products; read the **Awaiting acknowledgement** tile and press it; accept one buy, adjust one, skip one | Project demand = the unlinked remainder of ACKNOWLEDGED (and changed) OI rows only, so an awaiting row contributes nothing and is counted on the tile instead (M310-CR-PJ reads 0 at BRW-BB); no Unclassified column; one history badge; no "Consider N more" line; Use PO offered on retail rows only | Supply Chain > Planning > Reorder Planning | P1 / P5 / P6 built (#311); P3 / P4 / P8 this week |
| 6 | Place PO | Buyer (Joey) | Confirm the draft PO from the plan; key it into AutoCount per the Allocated-to panel; re-upload the PO book **from the Order Inquiries page itself** (Upload > Upload purchase orders, or Upload purchase history for the PO and SPO book), wait for the upload activity drawer to show the job finished - **Link now** and **Open purchase orders** appear only then - press **Link now** (it reports how many linked), then **Open purchase orders** to check | Nothing is offered while the book is still being read; when it lands, Link now links the rows of the products THAT upload wrote and Open purchase orders lands on a list narrowed to that upload's own orders ("Showing the N purchase orders from one upload", with Show all beside it); OI rows read Linked to the new PO - ACKNOWLEDGED rows only, an awaiting one stays unlinked; the upload shows in the same upload activity drawer it does from the reorder page; PO detail Allocated to (outstanding, allocated, free, placements with needed-at location and "location differs"); links survive the re-upload | Supply Chain > Project Demand > Order Inquiries (upload + Link now); Supply Chain > Purchasing > Purchase orders | P7 and G this week; the page-side upload and Link now with `PLAN-scm-oi-handshake.md` |
| 7 | Stock upload | Warehouse (Eling) | Upload the stock export | Board and popover figures move; moved transfers now agree with stock | Inventory management > Stock > Upload | Built |
| 8 | Loading plan | Purchasing (Ms Tee) | Build the loading plan for the container | Cut lines keep their reasons | Supply Chain > Fulfilment > Loading plan | Built (S7) |
| 9 | Proforma invoice to packing list | Purchasing (Ms Tee) | Load the KAILU PI and the JINBAICHUAN pre-loading list; convert to packing list | PI lines with unit price and cited PO; packing list duplicates flagged (container + ETA + date); the multi-block file handled (one upload or one per block, to be confirmed before Friday) | Supply Chain > Fulfilment > Proforma invoices / Packing lists | Built; multi-block reader to verify |
| 10 | SPO | Purchasing (Ms Tee) | Convert the packing list to an SPO; upload the SPO book | Rows in `spo_allocations`; board rung 1 reads "Incoming supply, SPO-nnnn arrives on date (overdue N days)"; Link SPO candidates exist for ORDER BACK rows | Supply Chain > Fulfilment > SPO | Built (#313) |
| 11 | SO change | Admin (Eling) | Re-upload SO381895 with form (3): SRTWCX7405-RL-S-PJ 10 + 10 + 5 becomes 25 on 19 Aug; C-FH14 advanced to 19 Aug | Board opens with the change annotated (was / now); the 25 row is updated in place with its links; the two closed lines' rows cancelled and their links shifted; a late-arriving link flagged; a linked Buy pushed beyond the window gets RELEASE (row moves to the pool, links kept) | Supply Chain > Project Demand > Planning changes > Plan | Part 3 this week |
| 12 | Salesperson questions | Sales (all, WhatsApp) | Ask the bot: stock of SRTWCY7405-PJ; stock of an item with none on hand and no SPO but an open PO; stock of an item on hand with outstanding SO; last incoming cost of SRTWT7443 | Stock answer carries on hand, outstanding SO, available, incoming SPO with its date (overdue marker), and the open PO with its expected date when nothing is incoming; cost answer gives PI date, supplier, unit price, currency, PO reference; visibility policy per contact respected | WhatsApp (n8n + MCP) | S12 this week: presenter and one new MCP tool over the existing last-incoming-cost reader |

## 5. Expected values the sheet should carry

- Station 4: nothing is linked when a row is RAISED any more (`PLAN-scm-oi-handshake.md`). A
  board confirm leaves every row Awaiting and unlinked; the links appear when Joey
  acknowledges. A sheet that expects "Linked" straight off station 2 is reading the old
  behaviour.
- Station 2 and 4 rows: take them from `scm-cs-planning-uat-fixture.md` (one row per form line, expected verb, qty, location, link target, [NS] not in system, [NL] no SO line, [S] stock proposed, [F] location differs).
- Station 5: M310-CR-PJ at BRW-BB reads Project 0; MSK11B reads 26 at BRW-BB (the two confirmed OI rows) and nothing at BRW-IB.
- Station 6: PO-2026/07-0029 style panel: outstanding 500, allocated 500, free 0, three placements, "location differs" against DC1.
- Station 10: `on_order_v` for SRTWCY7405-PJ at BRW-IB reads 332 (SPO-2026/08-0061 lines 2 + 160 + 170).
- Station 12 item 3: `SRTWC286-SH-PP` (dev copy 26 Aug: on hand 0, no SPO, open PO 72 on `202604-S0036` expected 2026-10-30, outstanding SO 180; alternate `MWT5506SS-DIY`, open PO 4 on `202607-S0080`). Item 4: `CSH2071` (on hand 4106, outstanding SO 2204 over 6 orders, available 1902, no SPO; alternate `SRTWT2214`, on hand 5274 / outstanding 4894 over 21 orders). Re-check both on the UAT box the day before: a stock or SO upload can move them.

## 6. Risks and prerequisites

- Migrations 419 (stock transfers), 420 (SPO into spo_allocations), 421 (order inquiry links) exist on the dev copy only. The UAT box needs them applied by Thursday. Owner to be named.
- The 15 unclassified customers must be classified retail before station 1, or the refusal blocks the upload. P4 carries the data migration.
- Station 9: the JINBAICHUAN file holds several proforma blocks in one sheet. Confirm the reader's behaviour and write the expected outcome into the sheet before Friday.
- Station 12 needs a Sorento phone number registered for the bot and the visibility policy set for the contacts who will ask.
- Every SPO date is in the past; purchasing testers should expect the overdue wording everywhere.
- One person (Eling) holds four roles. Order the sheet so her stations do not interleave with Joey's on the same records.

## 7. Open before the sheet is final

1. Owner for applying the migrations on the UAT box.
2. ~~The two product codes for station 12 items 3 and 4.~~ Chosen 26 Aug, see section 5.
3. Station 9 multi-block behaviour.
4. Whether Aiman signs off station 3 and 7 as observer.
