# Evidence run: contact <-> customer links, agent-side customers (AC-40, AC-11)

Lane CONTACT-CUSTOMERS, PR #1366, 29 Sep 2026, agent-browser 0.27.0 (headless Chromium,
session `lane-cc`) against the cloud sandbox stack: backend `uvicorn --reload` on :8000 over
the throwaway `sorento_ci` database (bootstrap_env, all 24 modules installed), frontend
`npm run dev` on :3000, signed in as a seeded superadmin. Seed rows: contact "Mr Lim (Hanlim)"
60120000518; customers 300-H001 HANLIM HARDWARE (KL) and 300-H002 HANLIM HARDWARE (JB) on
SEAN I, 300-H003 HANLIM AC & TRADING and 300-D001 DELUXE HOME CENTER with no agent; agents
SEAN I (Sean) and LCL (Lee CL).

Navigation was by sidebar clicks from `/` at 1280px: Users & Access > People > Internal Users
(the contacts list) > row; Sales > Sales Agents > row. The 375px pass reached the same
records through in-product links (the linked-contact name on the customer detail, browser
back) after the sidebar walk.

## Contact side (Profile tab, Customers card)

1. Internal Users > row 60120000518 opens Contact Details. Profile tab shows the "Customers"
   card after Contact Information: heading, "Add customer" select, empty state "No customers
   linked" / "Link the customer accounts this contact belongs to". No Primary, no Suggested.
2. Add customer opened: options `300-D001 - DELUXE HOME CENTER  No sales agent`, `300-H001 -
   HANLIM HARDWARE (KL)  SEAN I - Sean`, `300-H002 - HANLIM HARDWARE (JB)  SEAN I - Sean`,
   `300-H003 - HANLIM AC & TRADING  No sales agent` (customers select, `limit=50&offset=0`).
3. Picked 300-H002: `POST /api/v1/user-management/contacts/{id}/customers` 201, then GET 200.
   Card text: `300-H002 - HANLIM HARDWARE (JB) | SEAN I - Sean | Unlink`. No UUID anywhere.
4. Unlink clicked: the button became the countdown ("Unlinking in Ns", Cancel), no dialog;
   `POST /api/v1/pending-actions` 202; the card polled `/pending-actions/current`; after the
   window lapsed the card read the empty state again. Body `pointer-events` stayed `auto`.

## Agent side (Sales Agents > SEAN I > Customers tab)

5. Tabs: General, Sales orders, Transfers, Customers. Customers tab: search box "Search code or
   name...", "Assign customer" select, grid columns Code / Name / Region / Market segment /
   Status / Unassign, rows 300-H001 and 300-H002, "1 - 2 of 2".
6. Assign customer > 300-H003 (option showed "No sales agent"): `POST
   /api/v1/master-data/sales-agents/{id}/customers` 200, grid re-read, rows now H001, H002,
   H003 "1 - 3 of 3".
7. Unassign on 300-H001: toast countdown "Unassigning in 2s | Cancel | HANLIM HARDWARE (KL)";
   `POST /api/v1/pending-actions` 202; after the window the grid read H002, H003 "1 - 2 of 2".

## Customer detail (Delivery Orders > Customers > 300-H002, opened from the agent grid row)

8. After re-linking Mr Lim to 300-H002 (POST 201; `GET /customers/{id}/linked-contacts`
   returned the one row), the Details tab shows Contact Information with "Sales Agent: SEAN I -
   Sean", then "WhatsApp contacts": `Mr Lim (Hanlim) | 60120000518 | 29/09/2026`, before
   Opportunities. The name is a link to the contact record (followed in step 9).

## 375px

9. Customer detail at 375x812: `scrollWidth` 360 < 375 (no page overflow); the WhatsApp
   contacts section spans 16..344px. Screenshot `customer-detail-375.png`.
10. Contact record at 375 (via the linked-contact name): no page overflow; Customers card
    16..344px; the linked row wraps to two lines (74px tall): `300-H002 - HANLIM HARDWARE (JB)`
    / `SEAN I - Sean` / Unlink. Screenshot `contact-card-375.png`.
11. Agent Customers tab at 375: no page overflow; the table (950px) scrolls inside its 326px
    container; rows H002 and H003 present.

Console: no errors on any of the pages above (`errors` empty; the only entries were Fast
Refresh logs and NextAuth token debug lines). Nothing new animates beyond the existing
countdown bar.

## Round 3 (owner hand test: multi-select pickers), same stack, 29 Sep 09:00Z

12. Contact record (reached earlier by sidebar): the Customers card now reads "Add customers"
    (the shared `SearchableMultiSelect`) and a "Link 0 customers" button, disabled. Opened:
    options list every customer as `code - name` with the current agent underneath; the
    already-linked 300-H002 is a disabled option.
13. Ticked four options (one by keyboard Enter, three by click after `scrollintoview`; an
    option below the viewport clicked by coordinates closes the popover, which is the
    documented "scroll before click" rule, not a defect: a programmatic click on the same
    off-screen option toggled it). The button read "Link 4 customers"; the list stayed open
    across ticks. One click: `POST /api/v1/user-management/contacts/{id}/customers` 201 (one
    request, four ids); the card then listed five rows (H002 plus the four), the selection
    cleared and the button read "Link 0 customers" again.
14. Agent SEAN I > Customers tab: "Assign customers" multi-select; customers already on this
    agent are disabled options, the others show their current agent or "No sales agent".
    Ticked two, button "Assign 2 customers", one click: `POST
    /api/v1/master-data/sales-agents/{id}/customers` 200 (one request, two ids); the grid
    re-read with both rows added.

## Not covered here

Two-company scope in the browser (the sandbox has Sorento only; AC-22's two-company case is
the pytest `test_ac22_post_link_under_two_company_scope_takes_the_customers_company`). The
read-only variants (AC-12) are vitest-covered, not walked.
