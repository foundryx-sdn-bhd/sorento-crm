# Behaviour card - SR-CORE Sample Request (v1, 16 Oct 2026)

Slice 1 of 3 (SR-CORE, then SR-STOCK, then SR-RETURN). The salesperson asks the sample &
consignment coordinator for samples after a quotation. Base origin/main ccb1ce98. Examples are the
three paper "CONSIGNMENT REQUISITION FORM"s from crew discovery (15 Oct). Company, project and
product codes are kept; people and phone numbers are replaced by roles. Mock:
`documentation/plans/project-sales/mockups/sample-request-core.html`.

## Rules in plain words

1. **One request = the paper form.** Header: salesperson, request date, customer (debtor),
   project, quotation, requested delivery (a date, or "Immediate"), scope note ("Tower A & B"),
   ship-to name + address (can be a third party), site contact (name, phone, instruction),
   delivery mode, loan period, goods from, purpose, board mounting. Then item lines. Each request
   gets a number `SR-000001` from the numbering rules (`numbering_service.py:14`).
2. **Two ways in, one record.** The salesperson fills it on the **portal** (new kind "Sample
   request"). Staff can also raise it from the project's **Sample requests** tab on the
   salesperson's behalf. Both save the same row.
3. **Who sees the portal form.** Only a portal contact linked to a sales agent (the salesperson),
   and only when an admin switches the kind on for that contact. That is the rule Customer asks
   and Conversation already follow (`portal_service.py:94`, `portal_form_visibility_service.py:105-110`).
   A salesperson sees only their own requests (the portal's owner check, `portal_service.py:930-953`).
4. **Project: soft transition (owner ruling 3).** Switch *Sample request: project required*
   (System Settings, default **off**).
   - Off: the salesperson either picks a registered project or types the project title as free
     text (as on paper). One of the two is required.
   - On: a registered project is required; the free-text title is gone from the form.
   - Flipping the switch never changes a saved request. Old requests keep their free-text title.
   - Portal project list = projects the salesperson owns (`projects.owner_user_id` = the user
     linked to the contact, `portal_agent.py` `agent_for_user` / `users.respond_contact_id`),
     searched by code or title.
5. **Quotation: soft transition (owner ruling 5).** Switch *Sample request: quotation required*
   (default **off**). Off: optional. On: required. A quotation can only be picked once a project
   is picked, and only that project's quotations are listed (as `SampleDialog.tsx` already does).
6. **Customer.** Picked from the salesperson's own debtor book on the portal (the price tag
   request lookup, `price_tag_request_service.lookup_debtors_for_agent`), or any customer on the
   staff side. The customer code shows next to the name. A customer with no debtor yet (Form B,
   PINNACLE HOME) can be typed as a name and shows **No debtor** (see Q4).
7. **Lines are products, never free text.** Each line picks a product from the product list.
   - Search ignores dashes and spaces, so `TPE9101` finds `TPE-9101`.
   - A paper code that does not exist (`B2155-BLUE-NL`, `CB2829SS-CR`) is not stored: the
     salesperson picks the nearest real product (`B2155-BLUE-DIY`, `CB2829`) and may add a line
     remark ("NL variant").
   - **"X OR Y"** = one line with a main product and an optional **alternative** product. The
     coordinator sends either.
   - **"X / Y" set** = two lines.
   - Qty defaults to 1 (every paper line is 1); any whole number >= 1 is allowed.
   - Brand is not typed; it is read from the product.
   - At least one line is required to submit.
8. **Board mounting is not a line.** A toggle "Mount and label on a sample board" plus an
   optional board label ("TOWER A & B"). It is printed as an instruction above the lines and
   never needs a SKU.
9. **Delivery mode** (one of): **Deliver by driver** (paper: Deliver-Van), **Salesperson
   delivers** (Self-Deliver), **Customer collects** (Self-Collection). Required to submit (on
   paper only 1 of 3 forms ticked one; the coordinator cannot plan without it).
   Ship-to address and site contact are required for "Deliver by driver" and optional otherwise.
10. **Loan period** = a number + days or months (default 2 months, the value on all 3 forms).
    **Goods from** = Demo set / New goods / Either (default Either; never ticked on paper).
    **Purpose** = optional free text.
11. **Statuses in this slice.**
    `Draft -> Submitted -> Customer confirmed -> Delivered`, plus `Cancelled`.
    - Draft: saved, not sent. The salesperson edits freely.
    - Submitted: in the coordinator's queue. The salesperson can no longer edit (they cancel and
      resubmit, or ask in the comments); the coordinator can still correct lines and header.
    - Customer confirmed: the coordinator records the confirmation call and the agreed date.
    - Delivered: the coordinator records the date. The label follows the mode: *Delivered* /
      *Taken by salesperson* / *Collected by customer*. Return due = delivered date + loan
      period, shown on the request (reminders come in SR-RETURN).
    - Cancelled: the salesperson (while Submitted) or the coordinator (any time before
      Delivered), with a reason.
    - SR-STOCK will insert its steps (stock planned, borrow approval, transferred, note issued)
      between Submitted and Customer confirmed. Nothing in this slice blocks that.
12. **Internal refs.** The coordinator types **Consignment No** (the AutoCount CSGN) and **PO No**
    on the request, as on the paper's "For internal purpose" box. Free text in this slice;
    SR-STOCK links the CSGN to the pulled consignment.
13. **Stock peek (read only).** On the staff detail page, each line opens its stock by location
    in the existing `CellStockTable` (see Q1 for which feed). No allocation, borrow or transfer
    in this slice.
14. **Printable requisition.** "Print requisition" makes an A4 PDF laid out like the paper form
    (header, board instruction, lines with alternatives, delivery mode, loan period, goods from,
    purpose, Consignment No / PO No). Same WeasyPrint path as the PR / stock inquiry PDFs
    (`pdf_render.py:153`).
15. **Coordinator queue.** A new page *Project Sales > Sample requests* lists every request
    across projects (and those with only a free-text project), newest first, filter by status,
    delivery mode and salesperson, search by SR no / customer / project. Default view: Submitted
    and Customer confirmed.
16. **Notify on submit.** Submitting fires automation trigger `sample_request_submitted`
    (the same configurable-recipient mechanism the CS reserve request uses,
    `automation_triggers.py:573-605`). The coordinator(s) are the recipients.
17. **Permissions.** View on the project tab = `projects.projects.view`. Create / edit a draft on
    the staff side = `projects.projects.edit`. Coordinator actions and the queue page = new
    `projects.sample_requests.process`. Delete (Draft only, hard delete, deferred countdown) =
    `projects.projects.delete`.
18. **Feedback record.** When a request with a quotation version reaches Delivered, a
    `ProjectSample` row (the existing Samples tab, `models/projects.py:1558`) is created for that
    version, so developer feedback keeps working where it is today. Without a quotation, none.

## Examples (shaped on the three paper forms)

| # | Form | Input | Result |
|---|------|-------|--------|
| 1 | C, 10 Oct | Salesperson A, customer `300-J099 JV PERKASA SDN BHD (PROJECT)`. Project typed: "52 double-storey terraces, Taman Nusa Duta Fasa 10, for TARIKAN ABADI SDN BHD" (no registered project matches). Ship to CHENXIN PLUMBING SDN BHD, Johor Bahru; site contact = plumber's site supervisor, "call 1 day before". Board: on, label blank. 21 product lines (SRT73-CR, CB313, CB2157SS, SSFH12, ...), qty 1. Mode: Deliver by driver. Loan 2 months. | Submitted as SR-000001 with project "(not registered) 52 double-storey terraces ...", No quotation. Coordinator queue shows it. Print shows "MOUNT & LABEL SAMPLE BOARD" above 21 lines. |
| 2 | A, 15 Oct | Salesperson A, JV PERKASA. Project typed (Bukit Senibong service apartments). Scope note "Tower A & B". Requested: Immediate. Board on, label "TOWER A & B". Lines include `B2155-BLUE-NL` and `TPE9101`. No mode ticked on paper. | Salesperson types `TPE9101`, picker offers `TPE-9101`. `B2155-BLUE-NL` finds nothing; they pick `B2155-BLUE-DIY` with remark "NL". Submit is refused until a delivery mode is chosen. |
| 3 | B, 9 Oct | Salesperson B, customer PINNACLE HOME (no debtor), project "EDUCITY DEVELOPMENT". Requested 13 Oct. Lines include "CB2829SS-CR OR CB2829", "CB2805A-SS-CR OR CB2805A" and the set "CSA150 / CSH2072". Deliver by driver. | Customer typed, shown **No debtor**. `CB2829` main with no alternative (CB2829SS-CR does not exist). `CB2805A` main + alternative if both exist. The set becomes two lines, CSA150 and CSH2072. |
| 4 | C, later | Project registered as a CRM project; switch *project required* turned on. | New requests must pick a registered project. SR-000001 keeps its free-text title; staff can link it to the project from its detail page (edit), after which it also shows in that project's tab. |
| 5 | A, delivery | Coordinator records "Customer confirmed 17 Oct", then "Delivered 18 Oct". Quotation version linked. | Status Delivered, return due 18 Dec. A Samples-tab row is created for that quotation version. |

## Edge cases

- **No project and no title** on submit: refused, "Pick a project or type the project title".
- **Project switch on, request saved as Draft before it**: submitting the draft now needs a
  registered project (the switch applies at submit, not at save). Already-submitted rows are untouched.
- **Quotation picked, then the project changed**: the quotation is cleared.
- **Same product on two lines**: allowed (paper sometimes repeats for different towers); not merged.
- **Alternative = main product**: refused.
- **Inactive product**: not offered by the picker; an existing line whose product went inactive
  keeps showing it.
- **Requested date in the past**: refused on submit; "Immediate" is the way to say "now".
- **Long legal project title (8-15 lines)**: stored whole (text), clipped to one line with a
  tooltip in lists, printed in full.
- **Site contact phone**: shown to staff and the owning salesperson only; never on the queue list.
- **Delete**: only a Draft, hard delete, deferred countdown, no confirm dialog. A submitted
  request is Cancelled, never deleted.
- **Salesperson with no linked CRM user** (contact linked to an agent but no `users.respond_contact_id`):
  the project list is empty; they type the title while the switch is off (with the switch on they
  cannot submit, and the form says to ask the coordinator).

## Facts (base ccb1ce98)

- Portal kinds are per-kind code, not a registry: `SUPPORTED_TYPES` `portal_service.py:81`,
  grantable `:95-100`, labels `portal_form_visibility_service.py:38-47`, FE `lib/portal-form-kinds.ts:15-27`,
  `SubmissionForm.tsx` `FIELDS` :191 and `HAS_LINES` :370-373. The existing line grid stores a
  free-text `item_code` (`SubmissionForm.tsx:119-125`, `portal_service.py:1514-1533`), so it is
  NOT reused for lines: sample lines store `product_id`.
- Portal product lookup: `GET /public/portal/lookups/products` `portal.py:459-495` (code or name, active only).
- Project tabs: `ProjectDetailClient.tsx:75-87` (overview ... samples, sponsorships ...); panels use `PanelDataGrid`.
- `ProjectSample` needs a quotation version (`models/projects.py:1558-1597`), routes `samples_pos.py:78-150`.
- Stock: `OrderInquiryStockGrid` (`_shared/components/OrderInquiryStockGrid.tsx:30-41`) reads
  `stock-detail` in group mode, which keeps only fulfilment-planning bins
  (`project_fulfilment_board_service.py:1331`, `group_netting.py:301-305`). BRW-DISP, DISPLAY and
  CON are off-planning in dev, so this grid **does not show them**. `location_stock_for_product`
  (`scm/location_stock_service.py:35`) lists every active pool plus every bin holding stock,
  BRW-DISP and CON included, but sits behind `scm.dashboard.view` (`reorder_runs.py:61`).
- No warehouse-code constants exist for BRW / BRW-DISP / CON (`models/inventory.py:34-71`).
- Settings: a new boolean = column on `system_settings` + both manual dict builders
  (`settings.py:21-205`, `:283-475`) + FE settings page. No soft-transition switch exists yet;
  `cost_price_verification_enabled` (`user.py:447`) is the nearest rollout switch.
- CS reserve requests have no named approver: whoever holds `projects.order_inquiries.reserve`
  commits them (`order_inquiries.py:105`). That is the approver SR-STOCK reuses (ruling 4).
- Sales figures exclude only `doc_type != 'consignment'` (`sales/achievement_service.py:333,601`).
- CONSIGN-PLAN (#1712, docs only) keys "planned at own bin" on a doc-type set and says Sample
  Request adds its type there. That is SR-STOCK's seam, not this slice's.

## Questions (owner)

**Q1 - Stock peek feed (this slice).** (a) Reuse `OrderInquiryStockGrid` as is: BRW group only,
BRW-DISP / DISPLAY / CON are **not shown**. (b) Reuse `CellStockTable` as is but feed it from
`location_stock_for_product`, which lists every location holding the product, BRW-DISP and CON
included, through a projects-permission endpoint.
**Recommend (b)**, because BRW-DISP is priority 1 in your ruling and (a) would hide it. The
priority ordering setting itself lands in SR-STOCK with the allocation.

**Q2 - Where the request lives.** (a) Its own tables (`projects.sample_requests` + lines).
(b) A sales order row with a new doc type.
**Recommend (a)**: a sales-order row would count in sales achievement (only `consignment` is
excluded, `achievement_service.py:333`), and loan period, board mounting and site contact do not
belong on `sales_orders`. SR-STOCK puts the lines onto the Plan board through the doc-type seam
CONSIGN-PLAN leaves.

**Q3 - Paper's per-line "Approved by Sales Person / Manager" columns.** (a) Drop them: the only
approval is the borrow approval in SR-STOCK (ruling 4). (b) Keep a per-line manager approval.
**Recommend (a)**: all three forms left them blank, and ruling 4 names borrow approval as the only one.

**Q4 - Customer with no debtor (PINNACLE HOME).** (a) Allow a typed customer name, flagged
**No debtor**, so the request is not blocked; SR-STOCK's debtor check asks Account to create it.
(b) Require a debtor before submit.
**Recommend (a)**: the coordinator's flow already has "no debtor -> Account creates one" as a step.

**Q5 - Can the salesperson edit after Submit?** (a) No: cancel and resubmit, or ask in
comments; the coordinator can correct. (b) Yes until the coordinator acts.
**Recommend (a)**: the coordinator works from what was submitted; silent changes under them
are how a wrong item gets delivered.
