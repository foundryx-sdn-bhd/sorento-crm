# UAC: OI handover email prints on one page, one line per cell (OI-EMAIL-PRINT)

Plan: `PLAN-oi-email-print-09oct.md`. Mock: `ALIGN-oi-email-print-09oct.html`.

## REMARK

- **AC-P1** A raised row whose note reads "Planning keeps this Buy: ..." prints REMARK `ORDER`
  (HTML and text part). The note is still on the row and on the OI worklist.
- **AC-P2** No `row.note` text of any writer (Replaces / received / Order-back / Was / Also
  cited / Linked to) appears in REMARK.
- **AC-P3** (owner Q-C, 9 Oct) A CHANGE SO NO row whose note names the target (`Moved to
  SO123`) prints `MOVED TO SO123`; with no target (or only the book re-upload's "Moved to the
  ... line" stamp) it prints `CHANGE SO NO`.
- **AC-P13** (owner, 9 Oct: "simple and concise, ideally 3 words or fewer";
  `REMARK-wording-oi-email-print-09oct.md`): `ORDER BACK` with no document (Q-A); `ORDER N`,
  `CANCEL N`, `CANCEL ALL` (Q-D); a product change adds `NEW ITEM CODE` (Q-E), and the
  headline says `NEW ITEM CODE` too. Combos join by ", ", e.g. `ADVANCE, CANCEL 5`,
  `DELAY, ORDER 6, NEW ITEM CODE`. `PRE-ORDERED` (Q-B); the OI worklist and export keep
  `PRE-ORDERED, DO NOT ORDER`.

## Layout

- **AC-P4** The header table reads S/O NO | SO DATE | CUSTOMER | PROJECT, one row per order,
  SO DATE as dd/mm/yyyy.
- **AC-P5** The line table has no SO DATE and no S/O NO column. Headers: LOCATION, ITEM CODE,
  QTY, [NEW QTY], DELIVERY, [NEW DATE], REMARK; the bracketed two appear only when a line
  changed that value.
- **AC-P6** One order in the email: no group row. Two or more: one group row `S/O NO <n>` before
  each order's first line, spanning every column.
- **AC-P7** Every `th`, and every `td` except REMARK, carries `white-space:nowrap`. The
  owner's case `DELAY, ORDER 6, NEW ITEM CODE` takes one line on the 672px card with both NEW
  columns and a 16-char code present (line table 11px).
- **AC-P8** The rendered mail's card is 672px wide (`print` width), side gutters 12px; the shell's
  `<style>` carries an `@media print` rule and `@page { size: A4 portrait }`.
- **AC-P9** Printing the mock's "after" email in Chromium to A4 portrait gives one page for 12 lines
  with both CHANGE TO columns present, and nothing clipped on the right.
- **AC-P10** 40 lines print over more than one page with the column header row repeated on each.

## Migration

- **AC-P11** Upgrade rewrites `layout_json` / `body_html` / `body_text` of
  `order_inquiry_handover_default` only while `layout_json` and `body_text` are still one of
  the unedited states (oihr_0004's output, or the pre-0004 document with or without
  `width: standard`); any other row is left alone and a warning is logged. Downgrade restores oihr_0004's
  three values byte for byte. A database with no row gets one inserted. Idempotent.

## Unchanged

- **AC-P12** Subject, headline, verbs, values, dates, attachments suffix, "Raised by" line and
  button are unchanged; every other email keeps its card width (`standard` 600 / `wide` 900).
