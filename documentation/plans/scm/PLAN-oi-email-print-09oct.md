# PLAN: OI handover email - no multi-line cells, fits one printed page (OI-EMAIL-PRINT)

Status: Plan, card out for owner answers (feature track, abbreviated per the crew brief: card + mock first, then red tests, one PR; carries one data migration on `email_templates`, so not the small fix track)
Lane: OI-EMAIL-PRINT, branch `claude/oi-email-print-ucrsgl`, PR #1543
Domain: scm (order inquiry handover email, `order_inquiry_handover` automation) + email layout shell
UAC: `oi-email-print-09oct-acceptance-criteria.md` alongside
Mock: `ALIGN-oi-email-print-09oct.html` alongside (before / after, both rendered through the real shell)

## Problem (owner + end user, 9 Oct)

On the "ORDER, DELAY" email for SO418869 (project EUGENIA) the end user asked: "The form can
fit in one page to print out?" Owner: avoid multiple lines inside one cell; hard to read and
to print. In the Outlook photo the rows are tall because REMARK holds
"ORDER - Planning keeps this Buy: 140 landed for this line on SPO-2026/09-0036 stay linked to
this line; adjust the linkage if the Buy replaces them", an internal planning note, and SO DATE
and S/O NO repeat on every row.

## Measured (origin/main bf07eff8, `sorento_crm_backend/`)

- **Who gets it: staff only.** `alembic/versions/oihe_0001_seed_handover_automation.py:164-181`
  seeds `recipient_config.role_ids` = every role whose slug starts `purchasing`, plus the
  raiser, `one_email: true`. No customer address is ever on it.
- **The template** is the `order_inquiry_handover_default` row. It renders through its
  `layout_json` (brand header, heading, one `custom_text` with the two tables, button, footer),
  last written by `alembic/versions/oihr_0004_wide_line_table.py` (`width: "wide"` = 900px card,
  24px gutters, `app/services/email_layout.py:399-405`). `body_html` is only the mirror; the
  plain-text part (`_BODY_TEXT`) is the hand-tuned pipe table.
- **REMARK** = `handover_remark()` (`app/services/project_order_inquiry_service.py:375-441`).
  For a `raised` row it is the verb's spelling, plus `" - " + row.note` whenever the row has a
  note (line 397-405; only suppressed when `was` carries a qty or date).
- **`row.note` is a bag of internal stamps**, `"; "`-joined by many writers. Those that reach a
  raised row before the email is queued:
  - `Planning keeps this Buy: <n> landed ... adjust the linkage if the Buy replaces them`
    (#1362, line 1542-1549) - the one in the photo.
  - `Replaces <n> used; <doc> received <date> into <loc>; Was item <code>` (AC-OH-40..42 /
    OI-PRODUCT-FOLLOW, line 1587-1592, 1628).
  - `Order-back: <donor> lent <n> to <SO> line <k>` (borrow ladder,
    `app/services/project_supply_service.py:7797, 7866, 7904`).
  - Amendment / book-change notes: `Was dd/mm/yyyy`, `No previous delivery date`,
    `Was 10, now 8`, `Moved to <SO>` (`_change_note` line 4453-4467,
    `app/services/planning_change_service.py:3670, 3691`).
  - Form import: `Also cited on the form: <docs>` (`project_order_inquiry_import_service.py:179`).
  The CRM shows the same note on the OI worklist row (`"note": row.note`, line 5233), so
  purchasing still has it one click away ("Open in Order Inquiries").
- **SO DATE / S/O NO per row**: the line dict carries both (`_record_handover`, line 3627-3640);
  the header table (`orders`, `_build_handover_context` line 10958-10966) carries
  so_number / customer / project but NOT so_date.
- **Width**: 900px card. Printable width of A4 portrait at Outlook's default 0.5in margins is
  7.27in = 698px at 96dpi; Chrome's default 10mm margins give 718px. The card is wider than the
  page, which is why it cannot print on one portrait sheet without scaling or clipping.
- Existing tests: `tests/test_order_inquiry_handover_automation.py` (remark table tests,
  including `test_handover_remark_appends_a_cs_note_after_the_verb`, AC-H2, which pins the
  very behaviour this lane removes), `tests/test_oi_handover_wide_migration.py`,
  `tests/scm/test_oi_handover_line_total.py`, `tests/test_email_layout*.py`.

## Behaviour card

### Rules

1. **REMARK is one short instruction.** It is the verb's own spelling plus only the structured
   facts that belong to that verb: `ORDER`, `DELAY`, `ADVANCE`, `ORDER N`,
   `CANCEL BALANCE N NOS`, `ORDER BACK <cited document>`, `PRE-ORDERED, DO NOT ORDER`,
   `CHANGE ITEM CODE TO X (WAS Y)`, joined by ", " as today. `row.note` is never printed in
   the email (HTML or text part); it stays on the row and on the OI worklist. Q1 covers the
   one note that is arguably an instruction (CHANGE SO NO's target order).
2. **No per-row repeat of the header.** SO DATE moves into the header table
   (S/O NO | SO DATE | CUSTOMER | PROJECT). The line table drops SO DATE and S/O NO. When one
   email names two or more orders, each order's lines sit under a one-cell group row naming
   the S/O NO (lines are already sorted by S/O NO then line no), so nothing is lost.
3. **No wrapped cells.** Line table columns:
   LOCATION | ITEM CODE | QTY | [NEW QTY] | DELIVERY | [NEW DELIVERY] | REMARK
   (bracketed columns appear only when some line changed that value, as today). Every header
   and every cell except REMARK is `white-space:nowrap`; font 12px; padding 4px 6px.
4. **Fits A4 portrait.** The handover document moves from the 900px `wide` card to a new
   `print` width: 660px card, 16px gutters (628px of table), which fits Outlook's 698px and
   Chrome's 718px printable width with the 12px outer padding. The shell also gains a
   `@media print` rule (card fluid, outer padding 0, `@page { size: A4 portrait; margin: 10mm }`)
   for clients that honour it. A long order flows onto page 2 with the column header row
   repeated (`<thead>` with `display: table-header-group`).
5. **Everything else unchanged**: subject, headline, verbs, values, date format, attachments
   line, "Raised by" line, button. The plain-text part follows rules 1 and 2 (no note; header
   lines carry the SO date; the pipe table drops SO DATE / S/O NO and prints the S/O NO as a
   group line when there are several).
6. **Admin edits are respected**: the migration converts the row only while its `layout_json`
   is still exactly what `oihr_0004` wrote (same guard as oihr_0004), logs and skips otherwise.
   Rule 1 is code, so it applies to an admin-edited template too.

### Examples (real cases, customer and project names masked)

| # | Case | REMARK today | REMARK after |
|---|------|--------------|--------------|
| 1 | SO418869 (9 Oct, the photo): Buy of 140 kept over goods landed on SPO-2026/09-0036, plus a DELAY on a second line | `ORDER - Planning keeps this Buy: 140 landed for this line on SPO-2026/09-0036 stay linked to this line; adjust the linkage if the Buy replaces them` / `DELAY` | `ORDER` / `DELAY` |
| 2 | SO402757 / SRTWT6808 (30 Sep, EMAIL-HANDOVER-QTY): planning moved the line to Buy 436 | `ADVANCE, ORDER 64` / `ORDER BACK SPO-2026/08-0061` | unchanged (no note involved) |
| 3 | A line re-raised after its old supply was released (AC-OH-40): fresh ORDER row | `ORDER - Replaces 20 used; SPO-2026/08-0012 received 02 Sep 2026 into KL-WH` | `ORDER` (the QTY / NEW QTY columns already carry 20 to N) |

### Edge cases

- **Many lines** (40+): rows continue on page 2+, header row repeated, no row split across
  pages (`page-break-inside: avoid` on `tr`).
- **Long item codes** (e.g. 22 chars, `SRT-WC-ABCDE-1234-WHT`): nowrap means the column grows and
  REMARK narrows; REMARK is the only cell allowed to wrap. At 12px a 22-char code is ~150px, so
  the row still fits 628px with both CHANGE TO columns present.
- **Several SOs in one email**: header table lists each with its own SO DATE; line table has a
  group row per SO.
- **Attachments**: the "Attachments: ..." suffix stays in REMARK (it is a file list, not a note);
  with long names it wraps, which is the one accepted wrap.
- **CHANGE SO NO**: see Q1.
- **Admin-edited template**: migration skips it (logged); rule 1 still applies.

### Questions (with recommendations)

1. **CHANGE SO NO target.** Its note `Moved to SO...` is the only place the target order is
   named. (a) drop it like every other note; (b) print `CHANGE SO NO TO <SO>` from the
   amendment's own `to_value` (structured, not the free note). **Rec (b)**: it is an
   instruction, not a planning note, and it is short.
2. **Column labels.** (a) `NEW QTY` / `DELIVERY` / `NEW DELIVERY` (short, one line);
   (b) keep `QTY CHANGE TO` / `DELIVERY DATE` / `DELIVERY DATE CHANGE TO` with nowrap (the
   table gets ~120px wider and no longer fits portrait with both CHANGE TO columns).
   **Rec (a)**.
3. **Card width.** (a) new `print` width 660px for this email (portrait, no scaling, Outlook
   desktop safe); (b) keep 900px and rely on print CSS / landscape (Outlook desktop ignores
   `@page`, so the photo's client would still clip or shrink). **Rec (a)**.
4. **Multiple SOs.** (a) group row per SO in the line table; (b) keep an S/O NO column only
   when the email names 2+ orders. **Rec (a)**: no extra width.
5. **Undone email** (`order_inquiry_undone_default`) shares the shape. (a) leave it for its own
   lane; (b) fold the same layout in here. **Rec (a)**: the request names the handover email,
   and the undone mail has no REMARK note leak.
