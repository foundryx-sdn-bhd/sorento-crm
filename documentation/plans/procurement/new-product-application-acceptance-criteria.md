# UAC - New Product Application (NEW-PRODUCT-APP)

Card: `CARD-new-product-application-17oct.md`. Plan: `PLAN-new-product-application-17oct.md`.
Mock: `mockups/new-product-application-v2.html`. Staff side only (Q1 with the owner).
Every criterion is checked by a named test (pytest `tests/test_new_product_application*.py`, vitest beside the
component) or by the browser pass (B).

## Create and edit (Draft)

- **AC-NPA-01** A holder of `procurement.new_product_applications.create` creates a Draft; the number is
  `NPA-000001`, per company (highest issued + 1 under an advisory lock, never the row count), applicant = the caller.
- **AC-NPA-02** A Draft saves with any subset of fields (every line field may be empty in a draft);
  an empty number field is stored as NULL (never 0).
- **AC-NPA-03** Only the applicant edits or deletes their Draft (403 otherwise). Delete draft is the countdown
  pending action `new_product_application.delete` (10 s, hard delete, lines go with it); no confirm dialog.
- **AC-NPA-04** Item group, base UOM, brand, item type, supplier and stock inquiry ids must exist in the caller's
  company; anything else is 422 naming the field (never a silent drop).
- **AC-NPA-05** Created from a stock inquiry (`?stock_inquiry_id=`): the form pre-fills inquiry link, project name,
  customer TEXT (no debtor id), needed-by, and line 1 = proposed code + description + expected qty from the inquiry.
  No customer, product or name matching happens anywhere (B + vitest).

## Submit, approve, reject

- **AC-NPA-10** Submit refuses (422, one message per missing field naming the line) unless: at least one line;
  every line has description, item group and base UOM. The Draft keeps everything typed.
- **AC-NPA-11** Submit moves Draft -> Submitted, stamps `submitted_at`, notifies the approvers (AC-NPA-40).
- **AC-NPA-12** Submit from Rejected is one press: Rejected -> Draft -> Submitted, approvers notified again.
- **AC-NPA-13** Approve / Reject only by holders of `approve` (403 otherwise), only from Submitted (409 otherwise).
  Reject needs a reason (422 empty); the reason shows in the red banner.
- **AC-NPA-14** While Submitted or Approved every header and line write answers 422
  `new_product_application_locked`. Approve, reject, revise, cancel and line linking are not blocked by it.

## Revise and cancel

- **AC-NPA-20** Revise (applicant or approver) from Submitted or Approved needs a reason (422 empty), returns to
  Draft, clears the approval stamp, and writes one audit row carrying the reason.
- **AC-NPA-21** Revise from Submitted notifies the approvers that it was withdrawn; from Approved it notifies nobody.
- **AC-NPA-22** Revise and Cancel are refused (409) once any line is opened or closed.
- **AC-NPA-23** Cancel (applicant) from Draft / Submitted / Approved; reason optional; Cancelled is terminal.

## Purchasing opens the code

- **AC-NPA-30** On an Approved application a holder of `open_code` links a line to an existing ACTIVE product of the
  same company (`opened_product_id`, who, when). Any other status: 409. Other company / inactive / unknown: 422.
- **AC-NPA-31** Product search for linking ignores dashes, spaces and case, only lists matches (limit 20), and
  returns nothing for an empty query. Nothing is preselected or auto-linked (vitest: no auto-pick).
- **AC-NPA-32** "Not opening" closes a line with a required reason (422 empty).
- **AC-NPA-33** Undo (holder of `open_code`) clears a line's link or close on an Approved or Completed application;
  a Completed one returns to Approved.
- **AC-NPA-34** When every line is opened or closed the application becomes Completed in the same request and the
  applicant is notified.
- **AC-NPA-35** Linking a line that is already opened or closed answers 409 (two purchasers at once).

## Notifications (only when someone must act)

- **AC-NPA-40** Submitted -> active, non-integration holders of `approve` with a grant for the company, applicant
  excluded. Approved -> the same set for `open_code`. Rejected / Completed -> applicant. Withdrawn (AC-NPA-21) ->
  approvers. Nothing else notifies. Sent after the commit; a notification failure never undoes the move.

## Reads

- **AC-NPA-50** List: holders of `view` see every application of their companies; `create` holders without `view`
  see their own. DataGrid search (number, proposed code, description), Status and Applicant filters, sort, paging
  through `buildDataGridParams`; listing key `procurement.new_product_applications.view`.
- **AC-NPA-51** Detail renders header, lines with the "Opened as" column and the audit trail, each with an explicit
  empty state; prev/next navigation. No UUID is shown anywhere.
- **AC-NPA-52** The stock inquiry detail shows a "New product applications" row: "None yet", or each application's
  number (link), status and opened codes. The gear item "Create new product application" shows only on a
  `responded` inquiry for holders of `create`.
- **AC-NPA-53** Another company's applications are invisible (404) and never listed.

## Layout

- **AC-NPA-60** Create, edit and view are one document card; the line table scrolls inside its card; usable and
  not clipped at 375 and 1280 (B). Every optional select is clearable; every dropdown is SearchableSelect.
- **AC-NPA-61** Menu: Procurement Management > New Product Applications, gated by `view` or `create`.
