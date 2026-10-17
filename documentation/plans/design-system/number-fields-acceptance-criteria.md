# UAC: NUMBER-FIELDS (every number field can be cleared while typing; only the final value is checked)

Plan: `PLAN-number-fields.md`. Lane NUMBER-FIELDS, branch `crew/number-fields`. Size M, frontend only.

## Journey

Owner request (17 Oct 2026): on Order inquiry OI-2610-0057, line CB6638 (Remaining 212), the
"Request CS to reserve" dialog shows Requested = 1. The salesperson wants 2. They press Backspace to
clear the 1 and the field snaps straight back to 1, so changing 1 to 2 or 1 to 4 is a fight. Root
cause: `RequestReserveDialog.tsx:216` rewrites every keystroke through
`Math.min(Math.max(raw || 1, 1), remainingQty)`.

After this lane, on every number field in the app:

1. The salesperson clears the 1. The field stays empty. They type 2. The field shows 2.
2. They clear it and press "Park request" with the field empty. Nothing is sent. The field shows a
   red outline and a short message under it ("Enter a whole number between 1 and 212."). The dialog stays open.
3. They type 300 (more than the 212 remaining) and confirm. Nothing is sent; the same message names
   the allowed range.
4. They type 4 and confirm. The request is sent with 4.

## Shared component

- UAC1.1 [FE][T] Given a number field holding 1, when the user deletes the 1, then the field is
  empty (not 0, not 1, not the min) and stays empty until the user types again.
- UAC1.2 [FE][T] Given an empty number field, when the user types 2, the field shows 2 and the
  value handed to the screen is the number 2.
- UAC1.3 [FE][T] While typing, partial input (`-`, `1.`, `0.`) is never rewritten; the screen sees
  "no value yet" for anything that does not parse to a number.
- UAC1.4 [FE][T] A min / max / step / whole-number rule is never applied by rewriting what was typed.
  Typing 300 into a field with max 212 shows 300.
- UAC1.5 [FE][T] Arrow Up / Arrow Down and the spinner still step the value (native number input).
- UAC1.6 [FE][T] When the screen resets the value from outside (dialog reopened, row reloaded), the
  field shows the new value.
- UAC1.7 [FE][T] The shared check returns one plain message per failure: empty on a required field,
  not a number, below min, above max, not a whole number where only whole numbers are allowed.
  Wording is fixed in the plan.

## Final-value check (every screen)

- UAC2.1 [FE][T] Given a required number field left empty, when the user saves / confirms, then
  nothing is sent, the field is marked invalid (`aria-invalid`) with its message under it, and the
  dialog / form stays open with the typed values intact.
- UAC2.2 [FE][T] Given a value outside min / max or a fraction where only whole numbers are
  allowed, when the user saves, the same: nothing sent, message names the limit.
- UAC2.3 [FE][T] Given a grid cell that saves on blur, when the user leaves it empty or invalid,
  nothing is sent, the cell shows its message, and the last saved value is untouched on the server.
- UAC2.4 [FE] The message clears as soon as the value becomes valid (no second save click needed to
  see it go away).
- UAC2.5 [FE] Never stuck: every refusal leaves a visible message on the field; no Save button is
  silently disabled with no message explaining why.
- UAC2.6 [FE][T] Valid values save exactly as typed (no rounding, no clamping on the way out).

## Census

- UAC3.1 [T] A source-scan test fails if any `.tsx` under `sorento_crm_frontend/` (tests excluded)
  renders a raw `type="number"` input outside the shared component.
- UAC3.2 [T] The same test fails if an `onChange` rewrites a typed number with a fallback or clamp
  (`|| 0`, `|| 1`, `Math.max(`, `Math.min(` applied to `event.target.value`).

## Screens (browser pass at 1280 and 375)

- UAC4.1 [E2E] OI detail > Request CS to reserve (OI-2610-0057, CB6638): journey steps 1-4.
- UAC4.2 [E2E] At least five more screens from the audit table in the plan (PR lines, SO / OI qty,
  stock transfer, a price field, a settings number): clear, retype, save empty (message), save valid.
