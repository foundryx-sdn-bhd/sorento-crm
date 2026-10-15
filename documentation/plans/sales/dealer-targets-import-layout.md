# Dealer targets import: layout the reader accepts (lane SALES-TARGET-DEALER)

The real Sorento and Mocha files never enter the repo. This is what the reader understands; the
dry-run report names every row it cannot map and why, so a file that differs shows exactly
where. Built from the crew analysis of 15 Oct; to be tightened from the masked header rows crew
sends (PR #1715 data request).

## Sheets and header row

- Every sheet is read. A sheet with no header row is skipped and reported once.
- The header row is the first row (within the first 20) holding a cell whose text contains
  `CODE`. Headers are matched by name, case-insensitive, so column order does not matter.
- A second header row directly under it (sub-headers such as `TARGET`, `POINT`, `%`) is read
  when the row under a period header is not data (no code in the code column).

| Header contains | Meaning |
|---|---|
| `CODE` | Customer code (required on every data row). |
| `NAME`, `DEALER`, `CUSTOMER` | Name as typed; compared to the CRM name, a difference is a warning only. |
| `ACCOUNT`, `A/C` (not with CODE) | Account label, e.g. `A/C I`, `Deluxe`, `[CERAMIC]`. |
| `PAY TO` | Code of the account paid (default: the first account given a rate). |
| `ROUND` | `sen` / `0.01` (default) or `ringgit` / `RM` / `1`. |
| `Q1` to `Q4` | Quarterly scheme, one period each. |
| `JAN` ... `DEC` (alone) | Monthly scheme, one period each. |
| `YEAR`, `YEARLY`, `ANNUAL`, or a range like `JAN-DEC'26`, `DEC'25-NOV'26` | Yearly scheme; a range sets the window. |
| `POINT` anywhere in a period header or its sub-header | Thresholds in points (Mocha); otherwise RM. |
| `n%` inside a period header, e.g. `Q1 POINT (4%)` | Default rate for every threshold in that period with no rate of its own. |

A period header may be merged across several columns; every column under it belongs to that
period. Under it, a column whose sub-header has `%` holds rates for the threshold column to its
left (threshold / rate pairs).

## Cells

Merged cells are read as their top-left value on every row they cover. **A merged cell covering
several data rows makes those rows one target** (one threshold over several accounts).

A period cell holds one or more tiers, as a number or as text (mixed cells are normal):

- `300000`, `300,000`, `RM 300,000`, `300k` -> threshold 300,000 (rate from its `%` column, or
  the period header's default).
- `300,000 - 2%`, `300,000 @ 2%`, `300,000 (2%)`, `300,000 = 2%` -> threshold and rate.
- Several tiers: one per line, or separated by `;` or `|`.
- `+ Additional 1% @ 350,000`, `350,000 + Additional 1%`, `Additional 1% (350,000)` -> an
  additional line with its own threshold. With no threshold of its own, the additional line uses
  the threshold of the tier on the same line.
- Rates naming accounts: `300,000 - A/C I 3%`, `A/C I - 3%`, `Deluxe 2% / Mastile 3% / W-Ply 1.5%`
  -> rate per account; accounts are matched to the rows' Account labels in the same target. Only
  the named accounts earn on that tier.
- A rate in a non-merged cell on one account's row of a merged target -> that account's rate.
- `-`, blank, `N/A`, `nil` -> no scheme for that period.

## What one target is

Per merged group of rows (or a single row): one target per scheme found, quarterly (split every
3 months, own tiers per period), monthly (split every month, own tiers per period) and yearly
(one period). Highest tier reached pays; additional lines stack; amounts are % of RM sales.
Dates: the year passed to the import (default the current year), January to December, or the
window a yearly header or cell states (quarters and months then start at that window's start).

## Report (dry run and apply)

One row per sheet data row: `sheet`, `row`, `code`, `name_in_file`, `customer` (CRM name),
`status` `mapped` | `not_representable`, `reason` (plain words), `warnings`, `target_keys` (the
targets the row belongs to, e.g. `Q 2026 300-A0123+300-A0124`), and on apply `target_nos`.
A row is `not_representable` when: no code; no customer with that code; no period cell could be
read (the cell text is quoted); a rate names an account that is not in its target; a threshold
repeats in one period.
