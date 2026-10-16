# Overview

End-user documentation organised by department. The AI assistant is intended to source from these files when answering "what do I do?" / "how do I…" questions.

## Structure

* `**[_shared/](./_shared/)**` - flows reused by multiple departments (e.g. the generic Resource Management → Files upload flow). Department guides link here instead of repeating the same steps.
* `**[purchasing/](./purchasing/)**` - packing list upload, proforma invoice upload (a supplier's own workbook, read line by line), translating a supplier's own wording into English on that invoice, product attachments, folder management, stock-inquiry review, product-master upload, SPO upload.
* `**[warehouse/](./warehouse/)**` - GRN upload (header + lines), Delivery Order upload (tracking + lines).
* `**[marketing/](./marketing/)**` - promotion upload, marketing-form upload, catalogue packages (Combos) on a product and the price tags they print, working a price tag request (design, pinned change requests, printing choice and collection, product-data review, design history).
* `**[project-sales-admin/](./project-sales-admin/)**` - review submitted purchase requests / sponsorship forms and send them for approval.
* `**[project-sales-manager/](./project-sales-manager/)**` - approve purchase requests / sponsorship forms via emailed token link, flow stock inquiries to purchasing.
* `**[project-sales-rep/](./project-sales-rep/)**` - portal access (WhatsApp link + OTP), how to file complaints / stock inquiries / purchase requests / sponsorship forms / price tag requests, and what happens to a price tag request afterwards (design review, pinned change requests, approve, collect).
* `**[system-management/](./system-management/)**` - admin/ops reference: operational logs and configuration tables (imports, integration logs, scheduled tasks, email/WhatsApp delivery, import column mappings), troubleshooting a failed notification, reading/retrying a chatbot turn trace, the chatbot's "last purchase cost" answer and the per-contact Field reveal that gates it, the chatbot's outstanding report (sales order backlog / delivery order pending) and its own Field reveal gate, the chatbot's product spec visibility policy (contact override / market segment / default), and the AutoCount integration contract.
* `**[user-management/](./user-management/)**` - manage users/roles/teams, and admin Settings pages including Portal Revisions (letting a dealer revise a submitted portal form instead of starting over).
* `**[supply-chain/](./supply-chain/)**` - uploading the sales order book, purchase order book, Order Inquiry sheet and reorder-level listing that a reorder plan is built from; running a reorder plan and confirming decisions; printing the order summary sheet; the low stock report workbook (Low stock / All sheets) from a plan's Actions menu or over WhatsApp, and its Field reveal gate; deciding Buy vs Borrow on Fulfilment Planning; planning a sales order nobody decided (delivered or Completed included) and reading the Planned pill on the Sales Orders list; what happens when a sales order changes after planning (manual edit, book upload, or AutoCount) and how CS Confirms or Amends it.

## Conventions

Every guide follows the same shape:


1. One-line summary of when to use the flow.
2. **Steps** - short numbered list with exact UI labels in **bold**, taken verbatim from the React components.
3. **What's captured** / **What gets created** - the data the system records.
4. **How you'll be notified** - in-app toast vs. WhatsApp / email vs. background processing.
5. **Bulk import** (where supported).
6. **See also** - cross-links to related guides.

UI labels (menu items, page titles, button text, dialog titles) are quoted **verbatim from the frontend**, not paraphrased - if a label changes in the code, the guide must change too. We use frontend names (e.g. *Files*, *Packing Lists*, *Delivery Orders*) rather than backend / database names (e.g. *Attachments*, *Inbound Shipments*, *Order Tracking*).

## Status

* ✅ Purchasing - drafted, fact-checked against the codebase + verified live with Playwright.
* ✅ Warehouse - drafted (GRN, Delivery Orders).
* ✅ Marketing - drafted (Promotion, Marketing Form, catalogue packages on price tags: Combos and Sold with on the product, parts and the package warning on a request, one line many tags in the designer; Price Tag Requests: Design section + full-screen viewer, pinned change requests and Done, Printing / Mark ready for collection / Mark collected + auto-collect, Product data changed review, History).
* ✅ Project Sales Admin - drafted (review PR, review Sponsorship Form).
* ✅ Project Sales Manager - drafted (approve via email, flow stock inquiry).
* ✅ Project Sales Rep - drafted (portal overview: verify card, centred dashboard with Filter / Sort / view toggle including the List DataGrid, duplicate; submit via portal, including the sectioned Price Tag Request form, per-file AI extract, no line reorder, packages and parts on a price tag line, and Revise on a submitted request instead of post-submit edit; review a price tag design: required Printing choice, Design first + lightbox zoom/pan, pinned change requests and Send, Approve, Download PDF or Mark collected, WhatsApp at every status change).
* ✅ System Management - drafted (operational data reference, troubleshoot failed notifications, chatbot turn trace + retry, chatbot "last purchase cost" answer + Field reveal gate, chatbot outstanding report (sales order backlog / delivery order pending) + its Field reveal gate, chatbot spec visibility policy (Show only / Hide these, contact override / market segment / default), AutoCount integration contract, import column mappings).
* ✅ Supply Chain - drafted (upload sales orders / purchase orders / order inquiry sheet / reorder levels; sales & purchase history uploads retired in favour of the AutoCount integration; run a reorder plan; print the order summary; the low stock report workbook and its Field reveal gate; Buy/borrow decisions on Fulfilment Planning, including the Local pill and the borrow-source Location table; planning an undecided sales order from the list, what counts as decided, the ordered-quantity ask and the Planned pill; sales order changes after planning, the board's composed suggestion and Confirm/Amend).
* ✅ User Management - drafted (Configure Portal Revisions, naming the Price Tag Request row).

## Source-of-truth notes

* **Auto-link is the default.** Generic file uploads (Pattern A) fire an outbound webhook to **n8n** with the file URL and the attachment-type name. The integration workflow attached to that type parses the file, creates the relevant business records, and **calls back into the CRM** to attach the file to those records. Users do **not** link files manually after upload.
* The columns the parser expects (e.g. for a packing-list Excel) are defined in the n8n workflow attached to that attachment type, **not** in this codebase. Ask your integrations admin for the current template if a parser fails.
* The only attachment types seeded by Alembic migrations are **Promotion** (`code = 'promotion'`) and **Complaint Document** (`code = 'complaint_document'`). All other types (*Packing List*, *Product Attachments*, *Marketing Form*, etc.) are tenant-specific and must be created under [**Resource Management → Attachment Types**](/resource-management/attachment-types).
* Module-specific imports (Pattern B) - `product_import`, `spo_import`, `grn_listing_import`, `grn_lines_import`, `stock_import`, `order_tracking_import`, `delivery_order_detail_import` - are processed by the backend's import-job pipeline and surface progress via the `Latest … import` panel on the relevant module's list page.
* **Approval links are token-based, one-time use, and expire after 24 hours.** Project sales managers approve / reject purchase requests and sponsorship forms via the emailed link without logging in to the CRM. If a link expires, the project sales admin can re-issue one from the request detail page.
* **Project sales reps are notified only via WhatsApp** through Respond.io - there is no in-portal notification inbox. WhatsApp covers: complaint reply, stock-inquiry decisions (flowed / rejected / responded), and PR / sponsorship-form approval / rejection.
* **AI Extract on the portal is server-side.** When a rep clicks **AI Extract** on a complaint, the file is uploaded to the backend and an LLM proposes field values; the rep confirms before they're applied. Currently enabled on **complaint** submissions only.
