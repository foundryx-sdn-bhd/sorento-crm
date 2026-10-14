# UAC: product name hidden when it equals the code (NAME-EQUALS-CODE)

Plan: `PLAN-name-equals-code.md`. Card: `CARD-name-equals-code.md`. Mockup:
`documentation/mockups/NAME-EQUALS-CODE/index.html`.

Owner answers 14 Oct 2026: Q1 the rule applies to Name columns too; Q2 a (quotation line
description kept); Q3 a; Q4 a; Q5 a. Mockup v1 approved; the Q1 change lives here, not in a v2 mock.

## AC-1 The rule (one helper)

`sameDisplayText(a, b)` is true when both, after trim + collapsing inner whitespace + lower-case,
are equal. `distinctName(code, name)` returns the name only when it is non-blank and not
`sameDisplayText(code, name)`, else `null`. `codeWithName(code, name, sep)` returns the code alone
when `distinctName` is null, else `code + sep + name`.

| code | name | distinctName | codeWithName(' - ') |
| --- | --- | --- | --- |
| `11X11` | `11X11` | null | `11X11` |
| `11X11` | `11x11` | null | `11X11` |
| `1861` | ` 1861 ` | null | `1861` |
| `CWC  1009` | `cwc 1009` | null | `CWC  1009` |
| `2102AW` | `` / null / `   ` | null | `2102AW` |
| `SRT6641` | `SRT6641 FLOOR TRAP` | `SRT6641 FLOOR TRAP` | `SRT6641 - SRT6641 FLOOR TRAP` |

The backend twin (`app/utils/product_display.py`) gives the same answers for the same table.

## AC-2 Pickers (shared renderers)

Given a SearchableSelect, SearchableMultiSelect, portal AsyncCombobox / AsyncMultiCombobox /
MultiPillInput option whose second line equals its label under AC-1, the option shows ONE line
(the label). A second line that differs still shows. Typing the hidden text still finds the
option (search unchanged). Applies to every entity (Q3 a).

## AC-3 Joined labels

Every option builder / label that joins a product code and name (`·`, ` - `, ` / `, `(name)`)
goes through `codeWithName` and keeps its own separator: equal name gives the code alone
(`1861 · 1861` becomes `1861`); a differing name keeps today's text. Same for selected chips and
triggers. Product sets: set code vs set name, same rule.

## AC-4 Two-line cells and Name columns

- A table cell printing the code with the name under it (or beside it) prints the code alone when
  the name equals the code.
- Master/list grids with a separate Name column (Products list, stock balance, promotion products
  grid + list, promotion detail products, certificate covered products, spec verification, product
  attachments, product suppliers, brochure image picker, flyer match report, SCM supplier codes,
  product sets list for set name vs set code): the Name cell is BLANK when the name equals the code
  (Q1). Sorting, filtering and search on the column are unchanged. A differing name shows as today.
- Product detail page (the product's own record): the "Product Code: X" line under the heading is
  hidden when the heading (name) equals the code; the Overview "Product Name" field shows `-` when
  equal, like the other empty fields on that card. The edit form input keeps the stored value.
- A read-only product-name column beside the code in a line table (series Products "Description",
  `AmendmentAutocountChangeList` product column) shows `-` when equal.
- A line's own stored description (SO lines, worksheet, allocation, fulfilment sheet, amendment
  delta, quotation line) is unchanged (Q1 iii, Q2 a). Client-side CSV exports keep their columns.

## AC-5 Dialogs, headers, inline text, tooltips

Dialog titles/descriptions, sheet headers and sentences that print code and name ("1861 - 1861
will be dismissed", "11X11 / 11X11 changed") print the code alone when equal. A tooltip that would
only repeat the cell's code is dropped; a `CODE - Name` tooltip becomes the code (Q5 a).

## AC-6 Existing guards agree

The existing ad-hoc guards (exact-match `!==` and lower-case variants, e.g.
`lib/dealer-kit/product-block.ts:57`, `scm/components/ContainerRequestSection.tsx:150`,
`OrderInquiryDocumentDialog.tsx:315`, `StockDebtCellDialog.tsx:677`, `RequestTagDesigner.tsx:2006`)
call the helper, so `11x11` under `11X11` is hidden everywhere.

## AC-7 Backend text

- Printed price tag: set member line `- CWCX1009-RL (CWCX1009-RL) <dims>` becomes
  `- CWCX1009-RL <dims>`; package part line `+ CODE NAME <dims>` drops NAME when equal.
- API labels: SCM policy `scope_label` (list + resolve), sales target product scope label, order
  analytics product group label, promotion 409 conflict message: code alone when equal.
- `stock_ask_service.py:474` and MCP `_distinct_name` use the same comparison.
- No chatbot prompt changes; no LLM call in tests.

## AC-8 Browser (375 and 1280)

On the crew test copy, by sidebar clicks: a product picker (quotation line product), the series
Products table, the Products master list (Name cell blank), a quotation line. Each
shows `1861` (or the real code) once, never `1861 / 1861`.
