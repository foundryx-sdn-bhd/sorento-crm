# UAC: OI handover email prints on one page, one line per cell (OI-EMAIL-PRINT)

Plan: `PLAN-oi-email-print-09oct.md`. Mock: `ALIGN-oi-email-print-09oct.html`.

## REMARK

- **AC-P1** A raised row whose note reads "Planning keeps this Buy: ..." prints REMARK `ORDER`
  (HTML and text part). The note is still on the row and on the OI worklist.
- **AC-P2** No `row.note` text of any writer (Replaces / received / Order-back / Was / Also
  cited / Linked to) appears in REMARK. `ORDER BACK <cited document>`, `ORDER N`,
  `CANCEL BALANCE N NOS`, `ADVANCE` / `DELAY` and `CHANGE ITEM CODE TO X (WAS Y)` print as today.
- **AC-P3** (pending Q1) A CHANGE SO NO row whose note names the target (`Moved to SO123`)
  prints `CHANGE SO NO TO SO123`; with no target it prints `CHANGE SO NO`.

## Layout

- **AC-P4** The header table reads S/O NO | SO DATE | CUSTOMER | PROJECT, one row per order,
  SO DATE as dd/mm/yyyy.
- **AC-P5** The line table has no SO DATE and no S/O NO column. Headers: LOCATION, ITEM CODE,
  QTY, [NEW QTY], DELIVERY, [NEW DELIVERY], REMARK; the bracketed two appear only when a line
  changed that value.
- **AC-P6** One order in the email: no group row. Two or more: one group row `S/O NO <n>` before
  each order's first line, spanning every column.
- **AC-P7** Every `th`, and every `td` except REMARK, carries `white-space:nowrap`.
- **AC-P8** The rendered mail's card is 660px wide (`print` width), side gutters 16px; the shell's
  `<style>` carries an `@media print` rule and `@page { size: A4 portrait }`.
- **AC-P9** Printing the mock's "after" email in Chromium to A4 portrait gives one page for 12 lines
  with both CHANGE TO columns present, and nothing clipped on the right.
- **AC-P10** 40 lines print over more than one page with the column header row repeated on each.

## Migration

- **AC-P11** Upgrade rewrites `layout_json` / `body_html` / `body_text` of
  `order_inquiry_handover_default` only while `layout_json` is still `oihr_0004`'s document;
  any other document is left alone and a warning is logged. Downgrade restores oihr_0004's
  three values byte for byte. A database with no row gets one inserted. Idempotent.

## Unchanged

- **AC-P12** Subject, headline, verbs, values, dates, attachments suffix, "Raised by" line and
  button are unchanged; every other email keeps its card width (`standard` 600 / `wide` 900).
