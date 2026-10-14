# Smoke r7, SO380096 line 4 CWCY605 qty 5, head e0e6166ba, 14 Oct 2026
Board timed out under load; retried. Screenshots 04 and 05 show the recipient address in the dialog; text says "the test recipient".

1. PASS. Sidebar to Fulfilment Planning, search, tick, Plan together. 01-selected.png
2. PASS. Amend in place: Borrow 3 DC1-BB + Borrow 2 WH3-BB, reason "browser pass 5", saved. Simulate Would write: "line 4 CWCY605: ORDER_BACK 2 at WH3-BB (settles an existing row)"; "line 4 CWCY605: ORDER_BACK 3 at DC1-BB (settles an existing row)"; "line 4: borrow 1 DC1-BB -> BRW-BB"; "line 4: borrow 2 WH3-BB -> BRW-BB"; "Would confirm: line 4 CWCY605". Line 4 only. 02-simulate.png
3. PASS. Confirm: "SO380096: confirmed as revision 8 (0 purchase rows handed over)". Newest email 3:24 pm: subject "OI: BRW-BB @ SO380096", heading ORDER INQUIRY, one row: BRW-BB | CWCY605 | 5 | 03/08/2026 | ORDER BACK 3 & ORDER BACK 2. No CANCEL, no SHORT. 03-confirm.png, 04-email.png
4. 375x812 View: subject and S/O table readable; the fixed-width email preview is cut at the right edge (QTY and REMARK columns need sideways scroll inside the preview), same as r4. 05-email-375.png

Outbox row: 14/10/2026 3:24 pm, "OI: BRW-BB @ SO380096", pending.
