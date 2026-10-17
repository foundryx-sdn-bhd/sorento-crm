# PLAN: NUMBER-FIELDS

Status: PR+CI (size M, full track: frontend plus backend write-schema validators, no migration; security-reviewer
ran because the portal / external intake validators changed). Lane NUMBER-FIELDS, PR #1751, branch `crew/number-fields`.
Reviewer round 6 READY; rounds 1-6 recorded below; owner hand test owed (laneboard/scripts/1751.md).

## Answers (17 Oct, crew ask a68457864)

- Q1 (a) message on Save / confirm (blur for save-on-blur grid cells), then live until fixed.
- Q2 (a) over-limit keeps the typed value and blocks with the message; nothing changes silently.
- Q3 (a) optional fields stay optional: empty = no value, as today.
- Q4 (a) the 6 disabled Save buttons are enabled; a click shows the field message (never stuck).
- Q5 (a) filters use the same component; empty = no filter, no message.
- SCM SO edit: a red test pins "blank never cancels a line" and "typed 0 still cancels".

UAC: `number-fields-acceptance-criteria.md`. Audit: `number-fields-audit.md` (298 rows). Mockup:
`documentation/mockups/number-fields/index.html`.

## Journey

See the UAC. One line: on OI-2610-0057 / CB6638 the "Request CS to reserve" Requested field snaps back
to 1 when cleared (`RequestReserveDialog.tsx:216`, `Math.min(Math.max(raw || 1, 1), remainingQty)` on
every keystroke). The owner wants every number field in the app to accept an empty draft and to check
only the final value.

## Audit summary

298 numeric fields (`number-fields-audit.md`): 58 SNAP (cannot be cleared), 62 SILENT (empty saved as
0 / dropped with no message), 45+ optional (empty = no value, by design), 94 STR-OK (string draft +
message: already the target behaviour), 6 STR-GATED (Save disabled with no message), 11 RHF-OK, 6
filters, 16 other. No shared number input exists; the one shared rule is
`lib/whole-number-range.ts` (string draft + one-sentence message, 2 callers). Four local copies
(`PlanRowPanel.tsx:717`, `InspectorPanel.tsx:139`, `PackingListLinesTab.tsx:133`,
`chatbot-media/page.tsx:579`) and three dynamic renderers (`WorkflowDynamicFields.tsx:33`, portal
`SubmissionForm.tsx:2983`, `components/rule-builder/RuleBuilder.tsx`) carry the rest.

Worst silent case: SCM SO edit (`SalesOrderDetail.tsx:858`) turns a blank Qty into `Number('') = 0`,
and 0 on an existing line cancels it, so clearing a qty silently cancels the line.

## Design

The pattern the 94 good fields already follow (string draft, parse and check on save), made shared.

1. `lib/number-field.ts` (new; `whole-number-range.ts` becomes a thin wrapper so its callers and test
   stay unchanged):
   - `parseNumberDraft(raw: string | number | null | undefined): number | null`. `''`, `'-'`,
     `'.'`, whitespace, NaN, Infinity -> `null`; a number passes through; otherwise `Number(trimmed)`.
   - `numberFieldError(raw, rules: { min?, max?, integer?, optional? }): string | undefined`. One
     sentence per field (the `wholeNumberRangeError` family), returned for ANY failure (empty when
     required, not a number, below min, above max, fraction when `integer`):
     - min and max: `Enter a whole number between 1 and 212.` / `Enter a number between 0 and 100.`
     - min only: `Enter a whole number of at least 1.` / `Enter a number of at least 0.`
     - max only: `Enter a whole number up to 212.` / `Enter a number up to 100.`
     - neither: `Enter a whole number.` / `Enter a number.`
     - `optional: true`: empty passes; the sentence gains `, or leave it blank`.
2. `components/ui/number-input.tsx` (new) `NumberInput`: `Input type="number"` plus
   - `error?: string` -> `aria-invalid`, `aria-describedby`, and the message under the field in
     `FormMessage` style (`text-xs text-destructive`, `components/ui/form.tsx:130`);
   - numeric mode: `value: number | null` + `onValueChange(value: number | null)`. It holds the typed
     string itself and only re-syncs it when the parent's value differs from what the draft parses to
     (dialog reopened, row reloaded), so typing is never rewritten;
   - pass-through mode: string `value` + `onChange`, or react-hook-form `register(...)` spread
     (uncontrolled), unchanged.
   No clamp, no fallback, no `Math.max/min` anywhere inside. Native spinner and arrow keys keep working.
3. Screens: each save / confirm handler runs `numberFieldError` over its number fields; any message ->
   set `showErrors`, render the messages, return without sending. While `showErrors` is on, messages
   re-evaluate live so a fixed field clears at once. Save-on-blur grid cells do the same check at blur:
   invalid -> message in the cell, no request, the server value is untouched.
4. Census: `sorento_crm_frontend/components/ui/number-input.census.test.ts` scans every `.ts/.tsx`
   (tests excluded) and fails on (a) a raw `type="number"` / `type={'number'}` / a ternary yielding
   `'number'` outside `number-input.tsx`, (b) a change handler that rewrites a typed number:
   `(Number|parseInt|parseFloat)(<x>.target.value...) ||`, `Math.max(`/`Math.min(` over
   `.target.value` / `valueAsNumber`, `if (<x>.target.value === '') return`.

Not in scope: backend validators (the UI stops sending empty values, so nothing new reaches them);
text inputs with `inputMode` that already hold a string draft (InlineLineTable, OTP); filters keep
"empty = no filter".

## Slices

One `test(red):` commit per slice before its fix. Tester writes the red tests from the UAC + this
plan's rules only; coder makes them green without editing the tester's assertions.

- S0 shared: `lib/number-field.ts`, `NumberInput`, census test (red until S4 lands; the census list
  is the migration checklist).
- S1 project-sales + order inquiries (incl. Request CS to reserve, ReserveLineForm, fulfilment
  planning dialogs, InlineLineTable left as is).
- S2 scm + order-management + sales (incl. SO edit blank-qty-cancels fix, reorder planner, policies).
- S3 master-data + procurement + dealer-kit + inventory + resources (ProductForm, GRN, PR lines,
  packing lists, tag templates InspectorPanel).
- S4 system + user-management + marketing + SLA + workflow forms + portal + components (dynamic
  renderers, settings pages, chatbot media NumberField, RuleBuilder).

Coder: one at a time in this worktree; continued across slices while its context allows, a fresh
slice brief when it nears its limit (298 sites will not fit one context).

## Test list (tester)

- `lib/number-field.test.ts`: parse table; every sentence above; optional blank; integer fraction.
- `components/ui/number-input.test.tsx`: 1 -> clear -> stays empty, `onValueChange(null)`; type 2 ->
  shows 2, `onValueChange(2)`; `1.` / `-` not rewritten; external value change re-syncs; `error`
  prop sets `aria-invalid` + visible message; pass-through with `register` works.
- `RequestReserveDialog.test.tsx`: clear Requested -> empty; Park with empty -> no mutation, message
  `Enter a whole number between 1 and 212.`, dialog open; 300 -> same message, no mutation; 4 ->
  mutation `qty_requested: 4`; message clears when 4 typed.
- `SalesOrderDetail` (scm): blank Qty on an existing line -> no save, message; explicit 0 still allowed.
- One SNAP screen per slice from the audit (ProductForm Reorder Level, GRNForm Qty Received,
  NumberingRuleEditDialog, PlanOrderQtyLedger): clear -> stays empty; save empty -> message, no call.
- Census test (above).

## Fix round 1 (17 Oct: crew audit of de6d8f4b1 + reviewer NOT READY)

Crew audit: CI never ran (all heads `[skip ci]`), 2 tsc errors (SubmissionForm.tsx:2595, RuleBuilder.tsx:604),
message clipped in narrow cells (reserve dialog, SO edit at 375), SO edit blank qty counted as 0 in Outstanding and
totals. Browser pass: PR26-0385 edit saved a blank line quantity as NULL (crew restored the row to 28.00); the backend
accepted it.

Decisions:
- The field message always wraps under the field (`whitespace-normal`, word break) in the shared component.
- A blank or invalid qty never feeds a computed figure as 0: its line cells and every total that includes it show
  "-" until valid (SO edit, opportunity line amounts).
- Purchase request line Quantity is REQUIRED, > 0, in the UI and on the server (PUT/POST purchase requests); dev data
  has 0 of 1231 lines blank or <= 0. Other line-qty write endpoints get the same server check where a blank makes no
  sense (backend sweep).
- ONE live-commit component (`LiveNumberInput` in components/ui/number-input.tsx) replaces the scm
  CommittedNumberInput, the dealer-kit LiveNumberInput and the inline copies (PlanOrderQtyLedger, SpoPlannerTable):
  valid keystrokes commit, blank/invalid never commit and never rewrite the box, message at blur then live.
- Bad input ("1e") is invalid, never "no value", even on optional fields (`onValueChange(NaN)`).
- `minExclusive` rule: "Enter a number above 0." from the shared helper, no hand-written copies.
- OrderLinesCard add-line Quantity is REQUIRED (min 0, 0 allowed): the schema field is Optional (schemas/order.py:314)
  but the column order_lines.quantity is NOT NULL (models/order.py:556), so a blank can only become 0 or a 500.
- Backend (crew, 17 Oct): internal PR create / PUT / update-and-reply reject a line qty that is null, missing or
  <= 0 (422); order-management order lines reject an explicit null (0 still allowed: 4267 of 116487 dev lines are 0);
  SPO allocation update rejects an explicit null qty (422, today a 500). External PR intake (crew ruling (b), 17 Oct):
  unchanged, a customer / n8n PR is never lost; a line that arrives with a NULL qty shows as needs-a-qty to staff:
  the PR detail Quantity cell shows the edit form's sentence as an error instead of "-", and the edit form flags it on
  load, Update blocked until a valid qty.
  Not in scope (follow-ups): quotation / project PO line update turns null into 1; gt=0 on int line qtys.
- ConvertToPackingListDialog "Quantity to place" is required (blank used to place the full remaining silently).
- System-health field rules equal the backend bounds; packing list quantity_shipped is a whole number.
- `lib/whole-number-range.ts` keeps its stricter format check and takes its sentence from `numberFieldError`.
- Browser-pass rule (crew): a save in a browser pass uses data created for the test or is reverted in the same run,
  and every write is logged in the crew-note.

## Fix round 2 (17 Oct: reviewer round 2 NOT READY)

- Convert to packing list "Quantity to place": 0 is valid again (leaves the line for the next container, backend
  skips it, proforma_invoice_service.py:1999-2002); blank and over-remaining are refused.
- Bad input in string pass-through mode (about 205 sites): NumberInput hands the screen the string 'NaN' and keeps
  the typed text on screen; `numberFieldError('NaN')` is invalid even on optional fields. One central fix, no per-site
  change.
- react-hook-form numbers with bad input show the numberFieldError sentence, never zod's raw "Expected number".
- SpoPlannerTable: the sentence shows after Create SPO / Save changes, then live (Q1), never while typing.
- LiveNumberInput judges a box at blur only once it was edited (an untouched empty box is never red).
- SCM PO edit gets the SO edit rules (blank or invalid qty shows "-" in Outstanding and totals, Save refused with
  the sentence); SO edit treats a negative qty like blank in the totals.
- Browser passes clear a box with the keyboard and read its value before every press (agent-browser `fill ""` did
  not clear a number box; one accidental parked reserve request on OI-2610-0057 was cancelled in the same run).

## Fix round 3 (17 Oct: reviewer round 3 NOT READY, NaN sweep)

The string-mode 'NaN' hand-off (round 2) was swept over 193 call sites: 173 SAFE, 3 FILTER-SAFE, 17 RISK. Decision: keep
the central hand-off (bad input is never "no value") and close every RISK site plus the server: portal draft/submit run
the number checks on Unit price, Total and number fields; the backend refuses non-finite numbers (`'NaN'`,
`'Infinity'`) on the portal / external purchase request decimals and the rule builder `_numeric` (422, nothing stored;
blank stays null as today); PackingListLinesTab footer, PurchaseOrderHeaderCard po_amount, LeadWizardDialog
estimated_value, SCM SO edit unit price / discount, ListQueryFilterDialog, RuleBuilder "between", OrderLinesCard,
PredicateBuilder, OrderForm Trips, PR line Total, ProductSupplierTermsRow, LinkDocumentDialog and the board refuse bad
input with the sentence on the box. The backend validator touches the portal / external intake, so the
security-reviewer joins Phase 3.

## Final round 4 (17 Oct: reviewer r4 NOT READY + security review)

Reviewer r4: B1 order schema Trips half (committed 338896328); B2 board bad-input test made to exercise the gate;
S1 CS routing Save, S2 dealer-kit collection rules (FE + backend validate_tree), S3 planning sheet Confirm never
reads bad input as 0, S4 lead wizard Next enabled with the sentence on press. Security review (no merge blocker):
the lane's rule `_numeric` accepts any int (no OverflowError 500); upper bounds on the quantity validators this lane
added (PR line < 10**13, order line < 10**11, SPO <= 2147483647) so an overflow is a 422, not a 500 with database text;
adjacent: total_project_value huge exponent and portal huge line figures refused with 4xx. Pre-existing and filed as
separate lanes: #1753 (raw database error text in handle_internal_error(str(e))), #1754 (SPO allocation PUT and order
line writes without an edit permission). Browser observations: PR edit top Cancel and product first Update are not
lane-caused (reviewer r4, compared with main); SPO planner quantities are disabled in all dev data (pre-existing),
so its timing is covered by tests only.

## Round 5 (17 Oct: reviewer r5 NOT READY)

- B1 regression: an automation rule "=" / "not =" with bad input saved 'NaN' (before: null); "not = NaN" matched every
  promotion. Backend rule `_numeric` check covers eq / neq too; the automation form refuses before save.
- Collection rules gate narrowed to the number checks only (it also refused empty groups, blank conditions, empty
  "is any of", blank between, which saved before; 0 of 340 dev collections hold a rule tree).
- Portal: "1e1000000" refused with 400 (no abs() overflow); "too large" says "Quantity is too large.".
- Product picker Save from the "By hand" tab with a bad rule number shows a visible message (never a silent no-op).
- Follow-up, not this lane: no lower bound on order line / SPO allocation quantity (large negatives reach the DB).

## Round 6 and 7 (17 Oct)

- Reviewer r6 READY; its two nits fixed red-first: the product picker reopens on "By rule" with no leftover refusal;
  the collection number gate also walks a rule tree whose top level has no `kind`.
- Merging origin/main brought main's new Sample Request form (#1718): its payload builder defaulted a blank / 0 /
  fraction / bad qty to 1 and a blank loan period to 2. Now validate() refuses them with "Enter a whole number of at
  least 1." under the box, nothing sent; both boxes use NumberInput. Main's new nested DataGrid site
  (SampleRequestDetail.tsx) added to the inventory baseline (that test was red on main too).

## Rounds 8 and 9 (17 Oct: crew audit resend + reviewer r7)

- Crew audit item 6: project quotation / project PO line updates refuse an explicit null quantity (422) instead of
  saving it as 1 (omitted and 0 unchanged; no screen sends a null there).
- Reviewer r7: Sample Request "Save as draft" (portal and staff) now runs the same number checks as Submit
  (`numberErrors` in lib/sample-request-form.ts): blank / 0 / bad qty or loan period sends nothing and shows the
  sentence; the collection number gate recurses only into groups, so a condition carrying `"rules": []` is checked.
- Browser rows D 375, F 375 and Sample Request: NOT RUN in the browser (crew ruling (b), 17 Oct): the permission
  system denies browser save presses on the shared dev DB after three accidental writes during browser passes (all
  restored, logged on the PR). Covered by tests and earlier browser evidence (PR body).
