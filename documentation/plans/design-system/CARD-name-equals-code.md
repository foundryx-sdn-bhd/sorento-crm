# Behaviour card: product name hidden when it equals the code (NAME-EQUALS-CODE)

Owner 14 Oct 2026, on a product picker listing `11X11 / 11X11`, `1861 / 1861`, `2102AW / 2102AW`:
"the product name please don't show if same with product code, we should have this principle
across our system".

Mockup: `documentation/mockups/NAME-EQUALS-CODE/index.html` (v1).

## Rules

1. Wherever ONE spot shows a product's code and its name together (picker option, joined label,
   chip, two-line table cell, dialog line, tooltip, printed tag line), the name shows only when it
   differs from the code.
2. "Differs" is decided by one helper: trim, collapse inner whitespace, ignore case. Empty name
   counts as equal (code alone).
3. One frontend helper (`lib/product-display.ts`) and one backend twin; every site routes through
   them. The existing ad-hoc guards (exact match in some places, ignore-case in others) move onto
   the helper so they agree.
4. Search is unchanged: a picker still matches on the name.
5. Chatbot: replies already print the code only (`app/services/chatbot/lanes/business/answer.py:2365-2380`);
   no prompt change, no LLM call.

## Real data (sorento_cagent_stack, read-only)

- 15,339 of 15,339 products have `product_name = product_code` exactly; so today the rule hides
  every second line. Examples: `11X11`, `1861`, `2102AW`, set CWC1009-RL members `CWCX1009-RL`,
  `CWCY1009`, `CWC1009-SC`.
- `description` differs from the code on 15,308 of 15,329 filled rows, e.g. `SRT6641` /
  `SORENTO ANTI-ODOR FLOOR TRAP (150x150MM) SRT6641`; it is a different field and keeps showing.
- Quotation lines store `description_snapshot = product_code` when the product has no description
  (e.g. `SRT320-CR | SRT320-CR`), because the editor defaults the line description to
  `product.description || product.product_name` (`QuotationVersionEditor.tsx:235`).

## Scope found (sweep, 14 Oct)

- Frontend about 130 sites. Choke points: `components/common/SearchableSelect.tsx:509-519`,
  `SearchableMultiSelect.tsx:505-520`, portal `AsyncCombobox.tsx:350`, `AsyncMultiCombobox.tsx:236`,
  `MultiPillInput.tsx:263`; joined-label builders `scmOptionsService.ts:173,186`,
  `scmPolicyService.ts:325`, `productOrSetPicker.tsx:81`, `salesOpportunityService.ts:122`,
  `salesTargetService.ts:124`, `productSpecService.ts:834`, `certificateService.ts:197`,
  `productSetService.ts:177`, the three packing list / SPO `ProductCombobox*.tsx`; about 30
  code-over-name cells (SCM reorder grids, product detail sections, inventory); about 15 dialogs.
- Backend 7 sites: `marketing_service.py:196` (409 message), `order_service.py:1680` (analytics
  label), `scm/policy_service.py:105,493` (scope label), `sales/target_service.py:858` (target
  scope label), `dealer_kit/tag_data_service.py:469,726` (printed tag set members / package parts).
  `stock_ask_service.py:474` is already guarded (exact match). MCP `presenters.py:204`
  `_distinct_name` already hides it (exact match after trim).
- No email template or PDF/Excel export prints code and name in one cell.

## Edge cases

- Name null/blank: code alone (already the case in most builders).
- Name `11x11` vs code `11X11`, or `1861 ` with a trailing space: equal, hidden.
- Name that contains the code plus more (`SRT6641 FLOOR TRAP`): differs, shown.
- Product sets: same rule for set code vs set name.

## Questions

Q1. Separate Code and Name COLUMNS in one grid (`ProductsList.tsx:423`, `StockBalanceGrid.tsx:128/135`,
`PromotionProductsGrid.tsx:29-36`, `AmendmentAutocountChangeList.tsx:55/66`, about 15 grids).
(a) leave untouched, (b) blank the Name cell when equal, (c) drop the Name column.
Recommendation: (a). The rule targets one spot printing both; a column can already be hidden in
column settings, and the Products master must show the name it edits.

Q2. Line Description that defaults from the product name (`QuotationVersionEditor.tsx:235`, stored
as `description_snapshot`, printed on the quotation PDF).
(a) leave as is, (b) default to blank when the name equals the code.
Recommendation: (a) in this lane. It is stored, editable document text, not a display; changing the
default changes issued documents. Separate lane if wanted.

Q3. Shared pickers: hide the second line whenever it equals the label for EVERY entity (customers,
suppliers too), not only products.
(a) yes, in the shared renderer, (b) products only.
Recommendation: (a). One change in `SearchableSelect.tsx:512` covers about 40 product pickers, and a
second line that repeats the label never tells anyone anything.

Q4. Backend text: printed price tag set-member lines (`- CWCX1009-RL (CWCX1009-RL)` becomes
`- CWCX1009-RL`) and API labels (order analytics, SCM policy scope, sales target scope, promotion
409 message).
(a) apply the same rule via one Python helper, (b) frontend only.
Recommendation: (a). The policy and target labels are shown as-is on screen, so (b) leaves
`1861 · 1861` visible.

Q5. Tooltips that repeat code and name (`ProductsList.tsx:475` `title="CODE - Name"`,
`StockClaimsClient.tsx:137` name as tooltip over the code).
(a) apply the rule (tooltip shows the code alone or is dropped when it only repeats the cell),
(b) leave.
Recommendation: (a), same helper, no extra cost.
