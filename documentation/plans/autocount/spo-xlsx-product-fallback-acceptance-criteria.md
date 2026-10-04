# UAC: SPO-XLSX-SUPERSEDE round 2 (product-level fallback, no false incoming)

Plan: `PLAN-spo-xlsx-product-fallback.md`. Fixture = the owner case: Excel rows 95 (on the PL, received 95, location
HQ, no warehouse) and 4 (no PL, received 4); approved GR picks 22 @ IB + 73 @ NTC on the 95 row and 4 @ NTC on the
4 row; AutoCount lines IB 22 and NTC 77 on the PL; shipped 99.

- AC-F1 First push: both Excel rows are superseded (verdict `lines.superseded == 2`), only the two AutoCount rows
  remain, each closed and fully received (22 / 77).
- AC-F2 The picks move by capacity: IB row carries 22, NTC row carries 73 + 4; no pick is left on a removed row,
  the total picked is still 99.
- AC-F3 PL detail: SPO allocated 99, received 99, line status `received`.
- AC-F4 Chatbot incoming for the product does not list the PL.
- AC-F5 Quantities that do not reconcile (AutoCount 22 + 80) leave the Excel rows exactly as before (kept, closed).
- AC-F6 An Excel row naming a warehouse is never fallback-paired.
- AC-F7 No `.delete` grant: the Excel rows stay, closed, retired, received 0, note names the DocKey; picks moved;
  PL allocated 99.
- AC-F8 After a supersede (delete or retire), a new GR for that SPO draws only from the AutoCount lines.
- AC-F9 Pre-repair state (no supersede ran): PL received is max(picks, AutoCount stated) = 99, chatbot does not list
  the line; a partly received PL lists only the warehouse still owed.
- AC-F10 Repair script: dry-run changes nothing and prints the SPO and a scope count (documents, PLs); apply
  produces the AC-F1..F3 end state; a second apply is a no-op.
