# Behaviour card: CS routing conditions (product lines, debtor type), no-CS finish (FORM-ROUTING-CONDITIONS)

Base: origin/main 760ed152b. Dev data: `sorento_cagent_stack`, read-only. Person names replaced by placeholders.

**Answered 14 Oct 2026:** Q1 (a), Q2 (a), Q3 (a), Q4 (a), Q5 (b) Cash Sales for a Project cash debtor.

## Today (code)

- Approval emits `approved` (`procurement_service.py:10281`); the CS stage config starts on it
  (dev `form_sla_configs`: PR + SF `customer_service` start_event=approved, resolve=resolved,voided);
  `_start_for_config` (`form_sla_service.py:886`) always creates a CS task. Routing rules only pick WHO
  (`_resolve_pinned_assignee` `form_sla_service.py:1167`); no match = round-robin.
- Rule conditions see the form header row only (`_load_form_field_values` `form_sla_service.py:1076`);
  the picker offers lookup fields (sales_type, sponsor_subject) + customer_name / project_title /
  total_project_value (`cs_routing_service.py:155-209`). Nothing about lines or the customer record.
- Forms store only `customer_name` text. Portal picker lists DISTINCT `orders.debtor_name`
  (`portal.py:495-508`), free text allowed (`AsyncCombobox.tsx:60`); internal edit form is a plain
  input (`PurchaseRequestForm.tsx:621-636`).
- No usable customer type: `customers.customer_type` = company/individual, `market_segment_code` = SCM.
- After approval a form sits at `approved` until CS presses Processed / Close
  (`procurement_service.py:7700-7735`); portal revisions allowed at approved (dev
  `portal_revision_configs` allowed_statuses ["submitted","approved"]); SF/PR approved automations email
  ONLY the assigned CS PIC (dev `automations` include_assigned_cs_pic=true, no other recipients).

## Rules (proposed)

1. Rule editor (contact page, existing condition rows `PredicateBuilder.tsx`) gets two conditions, for
   PR and SF, operators equals / does not equal:
   - **Product lines**: `Has lines` / `No lines`. Has lines = at least one line with a non-blank item code.
   - **Customer debtor type**: `Project` / `Project cash` / `Cash sales`, read from the picked customer.
2. The rule's CS PIC dropdown gets one more choice: **No CS (finish at approval)**. When the winning rule
   (lowest priority among matching, as today) is No CS, the CS stage does not start; the form goes
   straight from Approved to **Completed**. No CS task, no CS email (the approved automation's only
   recipient is the CS PIC, so nothing goes out), no CS buttons (they need status approved,
   `procurement_service.py:7700`), portal revisions closed (Completed is not in allowed statuses),
   Void blocked (terminal, like Processed by CS `procurement_service.py:7846`). The customer gets the
   existing "approved" WhatsApp only; no second message.
3. No match still round-robins exactly as today (no behaviour change for existing rules).
4. New customer field **Debtor type** (project / project_cash / cash_sales), select on Customers edit +
   detail (same pattern as Account level, `CustomerForm.tsx` / `CustomerDetail.tsx`).
   Backfill once: 302- accounts -> project_cash; 301 "CASH (...)" walk-ins blank; everything else
   (incl. Mocha) -> project. New customers later (AutoCount sync, order import) get the same prefix rule
   on create; an existing value is never overwritten.
5. PR/SF customer picker (portal + internal edit form) searches customers (name, code shown) and stores
   `customer_id` beside `customer_name`. Free text still allowed: no customer_id, no debtor type.
6. PR: picking a customer auto-fills Sales type from its debtor type (project -> Project,
   cash_sales -> Cash Sales, project_cash -> see Q5); the requester can still change it.

## Real dev examples

| # | Form | Today | Proposed |
|---|------|-------|----------|
| 1 | PSSF26-0391, SALESMAN D, 1 LIVING DEPOT SDN BHD - [CERAMIC], 0 lines | CS task, processed_by_cs | Product lines = No lines; with a "No lines -> No CS" rule: Completed at approval, no CS task |
| 1b | PSSF26-0345, KEE LIN TRADING SDN BHD (PROJECT), 1 line, blank code, RM500 "Por Thor Praying 2026" | (rejected) | counts as No lines under Q3 (a) |
| 2 | PSSF26-0380, SALESMAN B, AIMA CONSTRUCTION SDN BHD (PROJECT-CASH), 1 line ACC6001 | SALESMAN B SF wildcard -> CS PIC Y | Has lines -> same CS PIC Y |
| 3 | PR26-0335, SALESMAN A, CMPC ENTERPRISE (PROJECT-CASH) (302), sales type blank | SALESMAN A's rules (project -> CS PIC X, cash_sales -> CS PIC Z) both miss -> round-robin | picker fills customer, debtor type Project cash; rule "debtor type = Project cash -> CS PIC Z" routes it |
| 3b | PR26-0367, SALESMAN C, EMPIRE RESOURCES (PROJECT-CASH) = 302-E003, typed Project (same debtor typed Cash Sales on PR26-0358) | requester's guess | sales type auto-filled per Q5, routing can use debtor type instead |
| 4 | PR26-0368, SALESMAN C, GLOBAL INGRESS SDN BHD (PROJECT) = 300-G101 | typed Project | debtor type Project, sales type auto Project |
| 5 | Rule set, SALESMAN A (dev): PR P1 sales_type=project -> CS PIC X, PR P3 sales_type=cash_sales -> CS PIC Z, SF P2 wildcard -> CS PIC X | | add SF "Product lines = No lines -> No CS" above the SF wildcard; optional PR "debtor type = Project cash -> CS PIC Z" |

PSSF26-0404 (owner's case, prod) is not on dev: UNVERIFIED, expected same as example 1.

## Edge cases

- Free-text customer: debtor type blank; `equals` never matches, `does not equal` matches (NULL rule,
  `cs_routing_match.py:31-33`).
- Debtor type edited after submit: rules read the value at approval time.
- Lines changed by a revision before approval: evaluated at approval.
- A No CS rule needs no PIC eligibility check; a PIC rule keeps today's ineligible -> round-robin fallback.
- Existing forms get no customer_id backfill (dev open: 2 PR approved, 2 SF submitted, 4 SF draft).
- Completed shows in PR list label, status pill, portal label, revision status options.

## Questions

- Q1 Where "skip CS" lives: (a) per-salesman rule outcome "No CS" (rule 2; each salesman needs the
  rule; 14 contacts have SF rules on dev) / (b) fixed for everyone: any PR/SF approved with no lines
  skips CS, no setting. Rec (a): you placed the condition in the per-contact rules and it leaves today's
  no-match fallback untouched.
- Q2 Final status: (a) new Completed / (b) reuse Processed by CS. Rec (a): CS never touched it.
- Q3 "No product lines": (a) no line with an item code / (b) zero lines at all. Rec (a): dev has blank-code
  money lines (PSSF26-0345/0346) that are not products.
- Q4 Backfill: (a) every 302- account -> project_cash (890 on dev: 843 exact "(PROJECT-CASH)", 21 typo tags
  like "(PROJECT - CASH)", "(ROJECT-CASH)", 26 no tag e.g. 302-C069 CHARLES & YANG RENO) / (b) 302- AND the
  name tag. 301 walk-ins (28, e.g. 301-C039 CASH (PERSON A)) blank. Names Project / Project cash / Cash sales.
  Rec (a): the prefix is the AutoCount ledger, the tag is free text.
- Q5 Sales type for a Project cash debtor: (a) Project / (b) Cash Sales / (c) new third option "Project cash".
  Rec (a): requesters chose Project 4 of 5 times for EMPIRE RESOURCES; rules that must tell project-cash
  apart use the debtor type condition.
