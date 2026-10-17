# AC-BILLING browser evidence (S2-9, S3-1, S3-2, S5-8)

Run 17 Oct 2026, agent-browser (headless Chromium, session `acbilling`), FE `npm run dev` on :3000,
BE uvicorn on :8000, empty local DB `sorento_ci`. Masked, illustrative data only.

## Data seeded
- Superadmin user, one integration + API key (acting as that user), all tenant modules installed.
- Customers `300-X918` "DEALER A SDN BHD [A/C IV]" and `300-Y204` "DEALER B TRADING [A/C II]"; products,
  agent `ZZFIN-AG1`; SO `SO-2608/0001` with one line ref `SRT_DB:SO:9001:1`.
- `billing_documents_v1.json` (customer code swapped to 300-X918) pushed through
  `POST /api/v1/external/ingest/billing_documents`: 6 created.
- Three `ar_outstanding` pushes: IV-1001 1,650.00 (due 25/11/2026), CN-1001 -200.00, DN-3001 55.00,
  IV-1003 100.00 then dropped from the later push so it went to 0 (Paid); CS-2001 and IV-1002 never
  covered so they stay NULL; customer total 1,505.00. Customer 300-Y204 never pushed (NULL).
- Four documents had no `customer_name` in the fixture, so the name was filled by SQL (see D2).

## Results (walked from `/` by sidebar clicks, 1280 then 375)
| Step | Result |
| --- | --- |
| FINANCE > Billing Documents leaf present, opens list | PASS |
| List columns Type/Number/Date/Customer/Agent/Total/Outstanding/Due date/Status; Paid, blank, negative CN shown | PASS |
| Type filter (Cash sale) -> `document_type=cash_sale`, 1 row | PASS |
| Open only -> `open_only=true`, 3 rows (DN, CN, IV-1001), paid and NULL excluded | PASS |
| Search `CS-2609` -> `query=CS-2609`, 1 row | PASS |
| No-match search -> "No billing documents match these filters." | PASS |
| Row click -> record: header, Invoice/Posted badges, meta strip "Outstanding 1,650.00 . due 25/11/2026 . as of 26/10/2026", tabs Details/Lines/Related | PASS |
| Lines tab: 2 lines, line 1 shows "From SO SO-2608/0001" | PASS |
| Related tab with links: CN and DN listed, Sales orders SO-2608/0001 | PASS |
| Related on cash sale with none: heading "No related documents" + hint, no button | PASS |
| Prev/next: Prev from 6/6 went to 5/6 (CS), Next disabled at the end | PASS |
| Cash sale (NULL outstanding): meta strip omits Outstanding | PASS |
| Customer record 300-X918: "Outstanding RM 1,505.00 as of 26/10/2026"; Billing documents tab lists 6, request carries `customer_id` | PASS |
| Customer 300-Y204: "Outstanding: Not synced"; tab empty state "No billing documents yet" | PASS |
| SO record: Invoices tab lists IV-2609/0001 with outstanding 1,650.00 (`sales_order_id` request) | PASS |
| 375px: list, record (3 tabs), customer + tab, SO Invoices tab: no horizontal page overflow, no clipping (grid scrolls inside its card) | PASS |
| `errors`: none on any page. `console`: no errors; one Radix warning at 375 (D3) | PASS |
| No UUID in visible text | PASS |

Screenshots: `list-1280.png`, `record-375.png`.

## Defects and observations (no product code changed)
- D1 (low, environment): a tenant with zero `tenant_modules` rows gets every module `enabled:false` from
  `/system/modules/me`, and the sidebar hides everything but Dashboards/Ideas, although CLAUDE.md says
  an empty tenant means "legacy, all on". Likely `app/modules/runtime/installer.py` /
  `app/api/v1/system` modules/me vs `frontend lib/menu-module-filter.ts`. Not in this lane; worked around by
  installing every module.
- D2 (low): the grid and record show `customer_name` from the AutoCount snapshot only. When it is null but
  `customer_id` is linked the Customer cell is blank (record page showed the linked customer once
  populated). A fallback to the linked customer's name would avoid a blank. Real feed always carries a name, so
  cosmetic. `billing_document_query_service.py:152`.
- D3 (low, likely pre-existing): at 375 with the mobile sidebar sheet, console warns "Missing Description or
  aria-describedby for DialogContent" (the sheet, not the new screens).
- D4 (observation): Reference/Description render the literal text "None" when empty on the record page
  (cash sale); other pages use "-". Cosmetic, in the Details tab of
  `app/(protected)/finance/billing-documents`.
- D5 (observation, pre-existing): on the Customers and Sales Orders lists a row click did not always
  navigate until the code/number link or a settled page was clicked; Billing Documents rows navigate fine.
  Not caused by this lane.
- Env note: `api_call_log` table is absent in the bootstrapped DB, so the API logs a (swallowed) error
  per external call. Also the DB was stamped at `sr_0001` and needed `alembic upgrade head` for fin_0003.

Dev servers left running: BE :8000 (uvicorn, env `.env.ci-tests`), FE :3000 (`npm run dev`).
Evidence login (local only): evidence@sorento-evidence.com, password in the scratch seed, not committed.
