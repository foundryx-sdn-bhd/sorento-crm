# UAC - Stock Debt fully decoupled from Fulfilment Planning pins (STOCK-DEBT-DECOUPLE)

Plan: `PLAN-stock-debt-decouple.md`. Status: R1-R3 in build, 14 Oct 2026. Owner answers:
Q1 (a), Q2 (a), Q5 (a). HELD until the owner answers: Q3 (pool decisions, AC-H1) and Q4
(Planned column, AC-H2).

## Journey

**Actor:** purchasing or CS planner on Project Sales > Stock Debt.

**Where they arrive from:** SRTSH1040. SO402757 (BRW-IB, due 1 Sep, 350) reads pinned on six
borrows of BRW-BB on hand, while BB's own orders SO383872 and SO385493 read short.

**Steps and the single decision each:**

1. Planner opens the Sep 26 cell. SO402757 reads pinned, Covered by its own PO 202607-S0067
   lines 77 and 65 (AutoCount S/O link).
2. Planner opens Jan 27. SO383872 and SO385493 (BB) read covered by "On hand BRW-BB": BB's
   stock stays with BB's orders by due date.
3. Planner who wants the borrow to matter acts on the Fulfilment Planning board; this page
   writes nothing.

**What they hold at the end:** a debt that states what is owed against stock that exists
or is linked in AutoCount.

## Acceptance criteria (view = `GET /stock-debt`, `GET /stock-debt/{product}/cell`, export)

- **AC-1 `[T]` An own-group Reserve is not cover.** BRW-IB holds 54 free. Line A (IB, due
  in 1 month, 30) has no decision; line B (IB, due in 2 months, 54) has a confirmed `own`
  Reserve of 54 at BRW-IB. A reads covered 30 "On hand BRW-IB"; B reads short with 24 "On
  hand BRW-IB" (short 30), not pinned.
- **AC-2 `[T]` A Borrow does not move stock between groups.** BRW-BB holds 100, BRW-IB 0.
  IB line (due in 1 month, 100) has a confirmed `other_location` Borrow of 100 at BRW-BB; a
  BB line (due in 3 months, 60) has none. IB line short 100 with empty Covered by; BB line
  covered 60 "On hand BRW-BB"; BB has 40 free.
- **AC-3 `[T]` A stale decision covers nothing (CB6633-NL shape).** `own` Reserve 126 at
  BRW-IB holding 0: line short 126, Covered by empty, its month -126.
- **AC-4 `[T]` No stand-in outside the span.** `group=IB`, IB line with a confirmed Borrow
  on BRW-BB (BRW-BB holds stock): line short, no Covered by entry naming BRW-BB, no Supply
  row for BRW-BB in the cell. Same for a decision at a bin flagged out of fulfilment
  planning.
- **AC-5 `[T]` The AutoCount PO link covers, whole.** PO line (outstanding 100,
  `from_so_line_ref` = line X's `source_ref`), X open 100, and a live order inquiry
  placement of 40 on the same PO line for another line Y of the same product: X reads pinned
  with 100 from the PO; Y gets nothing from that PO.
- **AC-6 `[T]` An in-transit SPO covers through its PO's link.** SPO row (incoming, no own
  `from_so_line_ref`, `from_po_line_ref` = PO line P's `source_ref`), P's `from_so_line_ref`
  = line X's `source_ref`: X reads pinned, Covered by the SPO. Same chain where P is for a
  different product: X gets nothing from the SPO.
- **AC-7 `[T]` A document with no AutoCount link covers nothing (R45 kept).** PO line with no
  `from_so_line_ref` and a live placement of 76 on line X: X short 76, Covered by empty; the
  PO's outstanding stays free in its own month (Supply row free = outstanding).
- **AC-8 `[T]` Received goods follow AutoCount, not the placement.** (a) SPO received 111 on
  the chain of a PO linked to X: X pinned 111 "On hand <bin>", with or without a placement.
  (b) A placement on a received SPO whose PO names another line Z (or no line): X takes
  nothing pinned from it; X draws on hand by due date like any line.
- **AC-9 `[T]` The board is unchanged.** `ProjectSupplyService.planning_assignments` /
  `StockDebtService.assignments_for` over the AC-2 and AC-5 books still pin the Borrow and
  still net the placement exactly as on main. Board/ladder suites green with no edit.
- **AC-10 `[T]` Unchanged rules.** Lendable / order back (lendable suites), R42 PO dating,
  R43 fulfils, R44 no overdue, export column list. The pill for an AutoCount-linked or
  landed line still reads `pinned` (Q5 a).

## Held until the owner answers

- **AC-H1 (Q3)** A decision at a site pool bin for a project line: today it pins; kept as
  is in this build.
- **AC-H2 (Q4)** "Planned" info column in the drill Demand table.

`[T]` = automated test, `[B]` = agent-browser pass.
