# Behaviour card: Sample Request (consignment requisition) in project management (SR-CORE)

Status: card + mock filed for owner approval (15 Oct 2026); card SETTLED (owner Q1 16 Oct 00:21, crew Q2-Q5 = (a)); mock v2 (16 Oct, rebuilt from existing components after owner notes 08:54) awaiting owner OK. Size L, slice 1 of 3 (SR-STOCK, SR-RETURN follow).
Mock: `mockups/sample-request-core.html` (open the file in a browser; every element names its component + path).
Sources: crew discovery `state/reports/SAMPLE-REQUEST-DISCOVERY-15oct.md` sections 1-4, owner rulings 16 Oct 00:10.
Paths: BE = `sorento_crm_backend/app/`, FE = `sorento_crm_frontend/`.

## What ships in this slice

One record, **Sample Request** `SR-000001`, that replaces the paper "Consignment Requisition Form". The salesperson
fills it in the portal or in the project's new **Sample requests** tab; the coordinator works it from one
cross-project queue and moves it up to **Delivered**. Stock allocation, borrow approval, transfers, the CSGN link
and returns are NOT in this slice; the record leaves a column or a status slot for each (see "Seams").

## Rules

1. **Header = the paper header.** Salesperson (the signed-in user, or the portal contact's linked user), request
   date (today), customer (debtor), project, scope note ("FOR UNIT - TOWER A & B"), requested delivery date OR
   "Immediate arrangement", ship-to name + address (any party, not only the customer), site contact name + phone +
   note ("call 1 day before"), delivery mode, loan period, goods from, purpose, board mounting.
2. **Project, soft transition (ruling 3).** `project_id` is nullable and a free-text `project_title` sits beside it.
   Switch `sample_request_require_project` (System Settings, default OFF): OFF = pick a registered project OR type a
   title; ON = a registered project is required on submit. Turning it ON never touches saved requests.
   Requests made from a project's tab always carry that project.
3. **Quotation, soft transition (ruling 5).** `quotation_version_id` nullable, picked from the project's quotations.
   Switch `sample_request_require_quotation` (default OFF): ON = submit refuses without one. Only offered when a
   project is picked.
4. **Customer.** Picked from debtors (`customers`, shows `customer_code - name`). No debtor (Form B's PINNACLE HOME):
   type the name; the record shows "No debtor code" to the coordinator. CRM never creates the debtor.
5. **Lines are products, picked, never typed.** Each line = product (required) + optional "or" alternative product
   + qty (default 1) + remark. The picker search ignores dashes, spaces and case (`TPE9101` finds `TPE-9101`; the
   same `regexp_replace(product_code,'[^A-Za-z0-9]','','g')` already used at `services/product_code_family.py:79`),
   so a typed "B2155-BLUE" lists `B2155-BLUE-DIY` among the hits. The search only LISTS products; a human always picks
   one. Nothing is auto-matched, nothing is chosen for the user, and no name or nearest-code matching runs anywhere
   (owner rule). A "X OR Y" line = product X + alternative Y, both picked.
   **The product must be in the product list** (owner, 16 Oct 00:21). There is no free-typed or unmatched line. When a
   search finds nothing, the picker says: "<typed text> is not in the product list. Pick the product you mean, or ask
   purchasing to add it as a new product before you request it." No line is added, and the rest of the request can
   still be saved and submitted, so the form never gets stuck. The API refuses any line whose product_id is not an
   existing active product.
   A "X / Y" set = two lines. No per-line approval columns (paper has two, blank on all 3 forms; ruling 4 makes
   approval borrow-only, which belongs to SR-STOCK).
6. **Board mounting is a header flag, not a line** (it has no SKU): toggle "Mount & label sample board" + label text
   ("TOWER A & B"). It prints as the first row of the requisition, exactly where the paper puts it.
7. **Delivery mode**: Deliver by driver | Self-deliver (salesperson takes it) | Customer collects. Required on submit (Q2).
8. **Loan period**: number + unit (days | months), default 2 months (all 3 forms). Return due = delivered date + period,
   stored for SR-RETURN's reminders.
9. **Goods from**: Demo set | New goods | Either (default Either, since the paper leaves it blank every time).
   **Purpose**: free text, optional.
10. **Statuses (slice 1)**: Draft -> Submitted -> Processing -> Customer confirmed -> Delivered, plus Cancelled from
    Draft / Submitted / Processing. The salesperson submits and may cancel until Processing. The coordinator
    moves Submitted -> Processing -> Customer confirmed -> Delivered and fills the internal fields
    (Consignment No, PO No, confirmed date, delivered date). Registered on the status engine
    (`modules/projects/status_entities.py:51-55`, graph seeded like `services/project_seed_service.py:737`), so
    labels are editable in Status graphs and SR-STOCK inserts its states between Processing and Customer confirmed.
11. **Who sees what.** Salesperson: own requests (portal: by contact, same filter as `services/portal_service.py:885-889`;
    staff: own + the project tab). Coordinator (`projects.sample_requests.process`): all requests, the queue, the
    internal fields, status moves. Site contact phone shows only to the owner and staff with view.
12. **Print**: "Consignment requisition" A4 PDF in the paper's layout (board row first, then lines, footer ticks for
    mode / loan / goods from / purpose, internal box with Consignment No + PO No). WeasyPrint through
    `services/pdf_render.py:153` `render_html`, same as the PR and stock-inquiry PDFs.
13. **On Delivered**, if a quotation version is set, a `ProjectSample` row (`models/projects.py:1558`) is created for it
    (submitted_on = delivered date), so the existing Samples tab collects developer feedback as today.

## Examples (shaped on the 3 paper forms; people replaced by roles)

| # | Input | Result |
|---|---|---|
| A | Salesperson A, portal. Customer `300-J099 JV PERKASA SDN BHD (PROJECT)`. Project switch OFF, typed title "3-tower service apartments, Bukit Senibong". Scope "FOR UNIT - TOWER A & B". Immediate. Ships to the site in Masai, site contact role "Site supervisor", "call 1 day before". Board ON, label "TOWER A & B". 21 product lines, loan 2 months, no mode ticked | Submit refused: "Choose a delivery mode" (rule 7). After choosing Deliver by driver: SR-000001, Submitted, no project link, shows in the coordinator queue. Print row 1 = "MOUNT & LABEL SAMPLE BOARD - TOWER A & B" |
| A2 | Same form, line `TPE9101` | Picker typed "TPE9101" offers `TPE-9101`; line saved as that product |
| A3 | Same form, line `B2155-BLUE-NL` (no such product) | Typing "B2155-BLUE" lists `B2155-BLUE-DIY`; the salesperson decides whether that is the item. Nothing is preselected. If it is not the item: Q1 |
| B | Salesperson B, staff, from project "EDUCITY DEVELOPMENT" tab (if registered). Customer PINNACLE HOME has no debtor: typed. Date 13 Oct. "CB2829SS-CR OR CB2829": `CB2829SS-CR` not in products | Searching "CB2829SS" finds nothing and shows the not-in-list message; the salesperson picks `CB2829` themselves (or leaves the line out and asks purchasing). Request shows "No debtor code" badge. Deliver by driver. Loan 2 months |
| B2 | Same, "CSA150 / CSH2072" | Two lines, `CSA150` qty 1 and `CSH2072` qty 1 |
| C | Salesperson A, Form C, project "52 double-storey terraces, Taman Nusa Duta Fasa 10" for developer TARIKAN ABADI. Ships to CHENXIN PLUMBING SDN BHD (third party) | Ship-to name = the plumber, customer stays JV PERKASA. Board ON. Coordinator: Submitted -> Processing, types Consignment No + PO No, Customer confirmed (date), Delivered (date). Return due = delivered + 2 months |
| D | Switch `sample_request_require_project` ON, portal submit with typed title only | Refused: "Pick the project". A request saved before the switch keeps its typed title and still opens |
| E | Quotation version picked, request reaches Delivered | `ProjectSample` row for that version appears in the project's Samples tab |

## Edge cases

- Salesperson edits after Submitted: not allowed; cancel and resubmit (keeps slice 1 free of revision handling).
- Coordinator cancels a Processing request: allowed, with a reason in the activity feed.
- Same product twice on one request: allowed (paper has it when one is the board's and one is loose); no dedupe.
- Qty other than 1: allowed (integer, >= 1).
- Search finds nothing: the not-in-list message (rule 5); submit stays available for the other lines.
- Request with zero lines and no board: submit refused, "Add at least one item".
- Inactive product: not offered by the picker; an existing line keeps it and shows "inactive".
- Portal contact with no linked company: project lookup returns nothing (`services/sponsorship_link_service.py:46-85`),
  so they type the title, unless the project switch is ON, in which case they cannot submit (same as sponsorship today).
- A project picked in the portal must be one `projects_for_contact` returns; the API re-checks on save.
- Contact phone and address are personal data: never in list rows, only on the detail page and the print.

## Seams left for SR-STOCK / SR-RETURN (no code in this slice)

- Stock panel on the staff detail page: each line expands to `OrderInquiryStockGrid({productId, location})`
  (FE `app/(protected)/project-sales/_shared/components/OrderInquiryStockGrid.tsx:31-41`) AS IS, location `BRW`, which
  renders `CellStockTable` (`fulfilment-planning/components/CellStockTable.tsx:136`). Caveat for SR-STOCK: in group mode
  stock-detail drops bins with `fulfilment_planning=false` (`services/scm/group_netting.py:301-305`), so BRW-DISP
  (group DISP) answers empty there; `location_stock_for_product` (`services/scm/location_stock_service.py:35`) covers
  every active bin and is the better feed for the priority panel.
- Location priority setting (ruling 1), allocations, borrow approval through the Plan flow (ruling 2, PR #1712), the
  approver (ruling 4), StockTransfer link, CSGN match: SR-STOCK. Status slot: between Processing and Customer confirmed.
- Return request, EA print, maintenance remarks, Return Consignment: SR-RETURN. `return_due` already stored.

## Data (additive migration, schema `projects`)

`sample_requests`: id, company_id, request_no (SR-000001 per company, copied from `models/stock_transfer.py:147-183`),
status_id, project_id NULL, project_title NULL, quotation_version_id NULL, customer_id NULL, customer_name, salesperson_id,
source (`staff` | `portal`), contact_id / space_id (portal owner), request_date, delivery_date NULL, deliver_immediately,
scope_note, ship_to_name, ship_to_address, site_contact_name, site_contact_phone, site_contact_note, delivery_mode,
loan_value, loan_unit, goods_from, purpose, board_mounting, board_label, consignment_no, po_no, submitted_at,
confirmed_at, delivered_at, return_due, cancelled_at, cancel_reason, timestamps.
CHECK: project_id or project_title present; delivery_mode / loan_unit / goods_from in their lists.
`sample_request_lines`: id, request_id, line_no, product_id, alt_product_id NULL, qty, remark.
SystemSetting: two booleans (rule 2, 3), added to both manual dict builders (CLAUDE.md lesson).
Permissions: `projects.sample_requests.view | create | process`.
Nothing existing is altered.

## Questions (recommendation first)

1. **ANSWERED, owner 16 Oct 00:21: "The product must be in the product list."** Every line is an existing product a
   human picks. No nearest-match, no free-typed lines; a not-in-list item gets a clear message (rule 5).
2. **ANSWERED (a), crew 15 Oct.** **Delivery mode required on submit?** (a) required; (b) optional, coordinator asks. **Rec (a)**: 2 of 3 paper forms
   left it blank and the coordinator has to chase it; one tap fixes that.
3. **ANSWERED (a), crew 15 Oct.** **Portal visibility.** (a) Opt-in per contact, like price tag requests (`GRANTABLE_PORTAL_FORM_TYPES`,
   `services/portal_service.py:95`); (b) every contact (`SUPPORTED_TYPES`, `:81`, which includes dealers).
   **Rec (a)**: only salespeople request samples; (b) would show the form to every dealer contact.
4. **ANSWERED (a) separate, crew 15 Oct.** **Separate "Sample requests" tab** next to the existing "Samples" (feedback) tab, or one merged tab?
   **Rec separate**: Samples is the developer-feedback log per quotation version (`models/projects.py:1558`) and
   rule 13 already feeds it; merging would mix a workflow list with a feedback list.
5. **ANSWERED (a), crew 15 Oct.** **Who may move statuses.** (a) A new `projects.sample_requests.process` permission granted to the coordinator role;
   (b) anyone with `projects.projects.edit`. **Rec (a)**: edit is held by every salesperson, which would let them
   mark their own request delivered.
