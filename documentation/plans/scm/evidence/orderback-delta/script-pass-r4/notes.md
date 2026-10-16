# Script pass r4, SO380096, head f5100e707, 14 Oct 2026

Environment note: machine load ~30; planning board and OI list timed out several times ("server took too long"), retried until loaded.
Starting state differed slightly from the brief: line 4 was already confirmed Borrow 5 DC1-BB (rev 3); line 6 (WESERP10B) was already rejected, unrelated, and rides along in the confirms.

1. PASS. Sign in, Supply Chain > Project Demand > Fulfilment Planning, search SO380096, tick, Plan together. Board "Planning 1 sales orders together". 01-selected-plan-together.png, 02-board-initial.png
2. PASS. Line 4 Amend: removed Borrow DC1-BB, Add a borrow, WH3-BB, qty 5, reason "browser pass 2", Save decision. Decided: "Borrow 5 (WH3-BB)", Saved. 03, 04, 05
3. PASS (entries recorded). Simulate confirm text: "Would write: line 4 CWCY605: ORDER_BACK 5 at WH3-BB; line 4: borrow 5 WH3-BB -> BRW-BB. Would confirm: line 4 CWCY605". 06-simulate.png
4. PASS. Confirm: "SO380096: confirmed as revision 4 (1 purchase row handed over)", 14/10/2026 1:46 pm. Newest outbox email: subject "OI: BRW-BB @ SO380096"; ONE item row: LOCATION BRW-BB, ITEM CWCY605, QTY 5, DELIVERY 03/08/2026, REMARK ORDER BACK. 07, 08, 09
5. PASS. Line 4 Reject, reason "browser pass 2". Confirm (1 to confirm, 2 rejected incl. line 6). Result banner: "SO380096: confirmed as revision 0 (0 purchase rows handed over)" (odd "revision 0" wording, noted). Newest outbox email 2:04 pm: subject "OI: BRW-BB @ SO380096"; one row: LOCATION BRW-BB, CWCY605, QTY 5, NEW QTY 0, DELIVERY 03/08/2026, REMARK CANCEL ALL. 10, 11, 12, 13
6. PASS. Order Inquiries > Lines, search SO380096, State filter Cancelled (3). No "include cancelled" toggle exists; State = Cancelled is the route. Cancelled rows: CWCY605 ORDER BACK WH3-BB qty 5 taken 0 remaining 0 (1:46 pm); CWCY605 ORDER BACK DC1-BB qty 5 taken 0 remaining 0 (12:48 pm); WESERP10B ORDER, BRW-BB (12:38 pm, other line). Donor locations kept. Non-cancelled CWCY605 rows: ORDER BACK at BRW-BB qty 5 via SPO-2026/05-0058 received (12:38 pm, the known follow-up, still present). 16, 17
7. FAIL (clip). At 375x812 the Outbox detail dialog fits the viewport width, subject "OI: BRW-BB @ SO380096" readable, but the HTML preview is a fixed ~600px email inside a horizontally scrolling frame: QTY/NEW QTY/REMARK columns and "Raised by" line are cut off at the right edge, needs sideways scroll inside the preview. 14-email-reject-375.png, 15-email-reject-375-body.png

Outbox rows created (all pending, to the test recipient):
- 14/10/2026 1:46 pm "OI: BRW-BB @ SO380096" (step 4)
- 14/10/2026 2:04 pm "OI: BRW-BB @ SO380096" (step 5)
