# Customer groups browser pass (CUSTOMER-GROUP)

agent-browser run against http://customer-group.localhost:3112 (API :8112), script laneboard/scripts/1441.md. Steps 1-21 and 23-24 at 1280 wide, step 24 plus customer detail at 375. Step 22 (chatbot) not run, as instructed. Console and errors read clean on every screen.

| Step | Result | Screenshot |
| --- | --- | --- |
| 1 Sidebar, Customer Groups, "1 - 50 of 527" | PASS | 01-list.png |
| 2 Search hanlim: one row, 6 ledgers, A/C 1 to 4 | PASS | |
| 3 Detail header, 6 ledgers, ledger rows with Account 1 to 4 or "-" | PASS | 03-detail.png |
| 4 Rename in place to "... TEST" (PATCH 200) | PASS | 04-edit.png |
| 5 Rename back | PASS | |
| 6 JUBIN BMS SDN BHD shows 13 ledgers | PASS | |
| 7 Remove 300-J002, no dialog, 5s countdown, 12 ledgers | PASS | 07-countdown.png |
| 8 Remove 300-J006 then Cancel, row stays | PASS | |
| 9 Remove 300-J006 again, 11 ledgers | PASS | |
| 10 Empty name: "Name is required", dialog stays | PASS | 10-name-required.png |
| 11 Duplicate: "A group with this name already exists", dialog stays | PASS | 11-duplicate.png |
| 12 New group opens, 0 ledgers, "No ledgers in this group" | PASS | 12-new-group-empty.png |
| 13 Picker shows 300-J002 "no group", Add gives 1 ledger | PASS | 13-picker.png |
| 14 Customers via sidebar, open 300-C130 | PASS | 14-customers-list-group-col.png |
| 15 Group link + Group ledgers card with "(this ledger)" | PASS | 15-customer-group-card.png |
| 16 Group set to CHIN CHUN HARDWARE, card shows HARDWARE ledgers | PASS | |
| 17 Group link opens HARDWARE group, includes 300-C130 (8 ledgers) | PASS | |
| 18 Clear group: "No group", "Not in a group" | PASS | 18-no-group.png |
| 19 Group set back to HOMEMART | PASS | |
| 20 Filters, Group HANLIM: 6 rows, 1 - 6 of 6 | PASS | 20-filter-hanlim.png |
| 21 Clear filters: full list (5359) | PASS | |
| 23 Delete test group: no dialog, countdown, back on list, group gone, 300-J002 "No group" | PASS | 23-delete-countdown.png |
| 24 375 wide: list, group detail, customer detail; no page overflow, tables scroll inside cards, buttons fit | PASS | 24a-375-list.png, 24b-375-group-detail.png, 24c-375-group-detail-jubin.png, 24d-375-customer-detail.png |

Data restored afterwards: 300-J002 and 300-J006 back in JUBIN BMS SDN BHD (13 ledgers), test group deleted, 300-C130 in CHIN CHUN HOMEMART SDN BHD.

Notes (not defects): the Customer "Last Updated" date did not change after a group edit (15/08/2026). Navigation after a row or back-link click can take several seconds on the dev server.
