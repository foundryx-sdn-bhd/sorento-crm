# Smoke r6, SO380096 line 4 CWCY605 qty 5, head 0cee81c1e, 14 Oct 2026
Board timed out under load; retried. Dialog screenshot 04 shows the recipient address; text says "the test recipient".

1. PASS. Sidebar to Fulfilment Planning, search, tick, Plan together. 01-selected.png
2. PASS. Amend to Borrow 2 DC1-BB + Borrow 3 WH3-BB, reason "browser pass 4", saved. Simulate Would write: "line 4 CWCY605: ORDER_BACK 3 at WH3-BB (settles an existing row)"; "line 4 CWCY605: ORDER_BACK 2 at DC1-BB (settles an existing row)"; "line 4: borrow 2 DC1-BB -> BRW-BB"; "line 4: borrow 1 WH3-BB -> BRW-BB"; "Would confirm: line 4 CWCY605". Line 4 only. 02-simulate.png
3. FAIL. Confirm: "SO380096: confirmed as revision 7 (0 purchase rows handed over)". Newest email 3:04 pm, subject "OI: BRW-BB @ SO380096", heading ORDER INQUIRY, S/O table on top, one row: BRW-BB | CWCY605 | 5 | 03/08/2026 | "ORDER BACK 2 & ORDER BACK 3, CANCEL ORDER 1". Expected no CANCEL; the remark carries ", CANCEL ORDER 1". No SHORT. 03-confirm.png, 04-email.png
4. PASS. 375x812 board, line 4 visible (row with confirmed tick), readable, not clipped; Stock transfers shows TR-000171, TR-000172, TR-000170 (3 proposed). 05-board-375.png

Outbox row: 14/10/2026 3:04 pm, "OI: BRW-BB @ SO380096", pending.
