# Smoke r5, SO380096 line 4 CWCY605 qty 5, head 8bb165772, 14 Oct 2026
Board timed out under load; retried. Line 4 started as a confirmed Reject (not undecided); opened it with Change decision and decided from there. Line 6 (rejected, unrelated) rides along in each confirm. Dialog screenshots 05 and 09 show the recipient address; text below says "the test recipient".

1. PASS. Sidebar to Fulfilment Planning, search, tick, Plan together. 01-selected.png, 02-board.png
2. PASS. Reserve DC1 set to 0, Borrow 5 DC1-BB, reason "browser pass 3", saved. Confirm: "SO380096: confirmed as revision 5 (1 purchase row handed over)". Email 2:52 pm, subject "OI: BRW-BB @ SO380096", one row: BRW-BB | CWCY605 | 5 | 03/08/2026 | ORDER BACK. 03, 04, 05
3. PASS. Amend to Borrow 3 DC1-BB + Borrow 2 WH3-BB, reason "browser pass 3". Simulate Would write: "line 4 CWCY605: ORDER_BACK 3 at DC1-BB (settles an existing row)"; "line 4 CWCY605: ORDER_BACK 2 at WH3-BB"; "line 4: borrow 3 DC1-BB -> BRW-BB"; "line 4: borrow 2 WH3-BB -> BRW-BB"; "Would confirm: line 4 CWCY605". Confirm: "confirmed as revision 6 (1 purchase row handed over)". Email 2:56 pm, subject "OI: BRW-BB @ SO380096", one row: BRW-BB | CWCY605 | 5 | 03/08/2026 | ORDER BACK 3 & ORDER BACK 2. No CANCEL text. 06, 07, 08, 09
4. PASS. OI Lines, search SO380096, CWCY605 ORDER BACK rows (default view, no cancelled): DC1-BB qty 3, not linked, taken 0, remaining 3 (2:52 pm row, now qty 3); WH3-BB qty 2, not linked, remaining 2 (2:56 pm); BRW-BB qty 5, linked SPO-2026/05-0058 received (12:38 pm, known follow-up). 10-oi-lines.png

Outbox rows created: 14/10/2026 2:52 pm and 2:56 pm, both "OI: BRW-BB @ SO380096", pending.
