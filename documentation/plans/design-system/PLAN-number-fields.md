# PLAN: NUMBER-FIELDS

Status: Plan (size M, full track: frontend only, no migration, no auth change). Lane NUMBER-FIELDS, branch
`crew/number-fields`. Behaviour card + mockup v1 filed to crew; build waits for the answers.

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
