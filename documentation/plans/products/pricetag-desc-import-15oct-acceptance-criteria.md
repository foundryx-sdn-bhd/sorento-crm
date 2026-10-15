# UAC: price tag descriptions from the flyer product-code sheet (PRICETAG-DESC-IMPORT)

Plan: `PLAN-pricetag-desc-import-15oct.md` (alongside). Owner request 15 Oct 2026; card answers
relayed by crew the same day (Q1 a, Q2 b, Q3 a, Q4 a, Q5 a).

Script: `sorento_crm_backend/scripts/import_price_tag_descriptions.py --file <xlsx> [--company SRT] [--apply]`.
It writes `products.price_tag_description` (`app/models/product.py:279`) and nothing else.

## Reading the sheet

- AC-1 The first worksheet is read. The header row (row 1) is found by name, case-insensitive:
  `Product Code` and `Description`. A sheet without both headers stops the script with a message
  and exit code 1, and writes nothing.
- AC-2 Row numbers in every output are the Excel row numbers (header = row 1).
- AC-3 A cell's text is used as printed: `\r\n` becomes `\n`, trailing spaces on each line and
  blank space around the whole cell are removed. Line breaks inside the text are kept (Q5 a).
- AC-4 Skipped and listed, never written:
  - a row with no code ("no code");
  - a row with no description ("no description");
  - a row whose description starts with `+` or `-` (part of a code spilled into the text,
    e.g. row 764 `SRTKT71SS` / `-BL...`) ("text starts with part of a code").

## Which product a row reaches

- AC-5 Exact: the code, compared after code normalisation (upper case, spaces around `-`
  removed, ends trimmed: `product_alternative_service.normalize_code`), names a product of the
  chosen company -> that product, rule `exact`.
- AC-6 Combo: a code cell holding `A + B` (exactly two codes) -> the whole description goes on
  product A only, rule `combo`; B is never written by that row. More than two codes ("combo is
  not two codes") is skipped and listed.
- AC-7 Code not found in the company -> skipped and listed ("code not found in the CRM").
- AC-8 Variants (Q1 a, flyer rule R1, `product_spec_flyer_ingest.py:359`): a product whose
  normalised code is `<sheet code>-<anything>` is a variant of that sheet code, rule `variant`, when:
  - its own code is not printed anywhere in the sheet's code column (either side of a combo);
  - of all sheet codes it extends, this one is the longest;
  - the sheet code is itself a product of the company; otherwise every such variant is skipped
    and listed ("variant of a sheet code that is not a product");
  - its stored `finish` and `seat_material` (`product_specifications.values[key].value`) equal
    the sheet product's (both empty counts as equal); otherwise skipped and listed ("variant has
    a different finish" / "variant has a different seat material" / both);
  - the sheet code's rows were usable; otherwise skipped and listed ("variant of <code>, which
    was skipped").
- AC-9 Only products of `--company` (default `SRT`) are read or written. An unknown company code
  stops the script with exit code 1.

## Same code on several rows (Q2 b)

- AC-10 Rows reaching the same sheet code whose texts are equal after formatting (all spacing and
  line breaks collapsed to one space, spacing around `:` made `: `, a `D: ` prefix dropped, case
  ignored) are one row; the FIRST of them (sheet order) is written.
- AC-11 Otherwise, when every shorter text (formatted as in AC-10) is contained in the longest,
  the longest row's text is written (e.g. `SRTMRL707`: row 3 "Frameless LED Mirror" and row 223
  the full spec -> row 223's text).
- AC-12 Otherwise the code is skipped and listed ("same code on several rows with different
  text") with every row number and every competing text. A combo row and a plain row of the same
  BF count as the same code.

## Trap lines (Q3 a)

- AC-13 A text that prints both an S-Trap and a P-Trap line is cut per product: an `s_trap`
  product (stored `trap_type`) keeps the S-Trap block, a `p_trap` product keeps the P-Trap block.
  A block runs from its `S-Trap` / `P-Trap` line (line start, optional space or `-`, any case)
  up to the next trap line, the next line starting with `*`, or the end. Every other line is
  kept as printed, outlet list included (no number rewriting).
  Example, row 66 `SRTWC286-SH` -> `SRTWC286-SH-P`:
  `Twister Flushing Technology\nD: L680xW365xH735mm\nP-Trap: Horizontal\nOutlet 180mm\n*PP Seat Cover`.
- AC-14 Both traps printed and the product has no stored trap type -> skipped and listed ("both
  traps printed, product has no trap type").
- AC-15 Only one trap printed and the product's stored trap type is the other one (dev:
  `SRTWC201-P` from row 85) -> skipped and listed ("product's trap type is not printed").
  A product with no stored trap type takes a one-trap text unchanged.
- AC-16 The rule name gains `+trap` when a cut happened (`exact+trap`, `variant+trap`).

## Dry run, apply, idempotency

- AC-17 Without `--apply` nothing is written to the database (no product change, no audit row).
- AC-18 Every run prints one line per product to update (rule, product code, old text, new text,
  line breaks shown as ` / `) and a summary: updates by rule, unchanged, skipped by reason.
- AC-19 A product whose current text already equals the new text is "unchanged", never updated.
  A product with a different non-empty text is overwritten (Q4 a); its old text is in the report.
- AC-20 `--apply` writes every update in ONE transaction and commits once; any error rolls back
  everything.
- AC-21 Each updated product gets exactly one audit row (entity `product`, action `UPDATE`) with
  the old and new `price_tag_description`, written through the existing ORM audit listener.
- AC-22 A second `--apply` updates 0 products and writes 0 audit rows; a dry run after apply
  reports 0 to update.

## Reports (for the owner and for marketing)

- AC-23 Every run (dry run and apply) writes two CSV files next to the xlsx and prints their
  paths: `<stem>-price-tag-changes-<dryrun|apply>-<YYYYMMDD-HHMMSS>.csv` and
  `<stem>-price-tag-skipped-<dryrun|apply>-<YYYYMMDD-HHMMSS>.csv`.
- AC-24 Changes CSV columns: `product_code, sheet_code, sheet_rows, rule, status (update|unchanged),
  active, old_description, new_description`. Full texts, real line breaks inside quoted cells.
- AC-25 Skipped CSV columns, written for marketing to fix the sheet: `sheet_code, sheet_rows,
  product_code (empty when the whole row is skipped), reason (plain words, as above),
  sheet_texts` where `sheet_texts` holds each competing text as `row N:` + the text, separated
  by a blank line. Sorted by first sheet row.

## Rendering check (crew, Q5)

- AC-26 After the dev-copy apply, a price tag that binds `Price tag description` for
  `SRTWC286-SH-P` shows the text on separate lines (not run together). If it does not, this is
  reported before any prod run.
