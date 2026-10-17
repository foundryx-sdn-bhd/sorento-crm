# Dealer targets import: layout the reader accepts (lane SALES-TARGET-DEALER)

The real Sorento and Mocha files never enter the repo. This is what the reader understands; the
dry-run report names every row it cannot map and why, so a file that differs shows exactly
where. Rewritten after crew's gate run on the real files (16 Oct, gate fail): the reader now
takes the files as delivered. `sorento_crm_backend/tests/_dealer_target_real_layout.py` copies
that layout with masked names; the Excel template (`GET /sales/targets/import/template`, the
Template button in the import dialog) is the same layout plus a CUSTOMER CODE column.

## Sheets and header rows

- Every sheet is read. A sheet with no header is NOT skipped quietly: every filled row of it is
  reported `not_representable` ("sheet ... not read"), so a dropped sheet never reads as all
  mapped (gate-fail item 8).
- The anchor header row is the first row (within the first 20) with a cell that is WHOLLY a
  dealer-name or code header (`DEALER NAME`, `CUSTOMER NAME`, `NAME`, `DEALER`, `CUSTOMER`,
  `CUSTOMER CODE`, `CODE`). A title such as `SORENTO DEALER INCENTIVE 2026` is never one.
- Header rows stack: up to 3 rows above the anchor that hold period-group words (`Q1`,
  `QUARTERLY`, `YEARLY`, `MONTHLY`, `(POINT)`), and up to 4 rows below it that hold only header
  text (`JAN-MAR`, `JAN`, `Tier 1`, `Tier 1 Incentive`, `4%`, `PERIOD`, `%`, `TARGET`).
- Each column is read through ALL its header rows (a merged header reads in every column it
  spans), so `Q1` / `JAN-MAR` / `Tier 1 Incentive` is the Q1 rate column of tier 1.

| Header text (any level) | Meaning |
|---|---|
| `DEALER NAME`, `CUSTOMER NAME`, `NAME`, `DEALER`, `CUSTOMER` | Dealer name; the customer is matched on it (below). Merged down over the dealer's account rows. |
| `CUSTOMER CODE`, `CODE` | Optional. When a row has a code, the code wins over the name. |
| `ACCOUNT`, `A/C` | Account label: `A/C I` .. `A/C IV`, `Deluxe`, `Mastile`, `W-Ply`, `[CERAMIC]`, `[IBORN]`. |
| `PAY TO` | Optional: the account paid (a code, or an account label of the same dealer). |
| `ROUND` | Optional: `sen` / `0.01` (default) or `ringgit` / `RM` / `1`. |
| `Q1` .. `Q4` (with or without `QUARTERLY` above, `JAN-MAR` below) | Quarterly scheme; Q1 is always January to March of the import year. |
| `Monthly` over `JAN` .. `DEC`, or a month alone | Monthly scheme. |
| `YEARLY`, `ANNUAL`, `(YEARLY)`, or a 12-month range (`JAN-DEC'26`, `DEC'25-NOV'26`) | Yearly scheme; the range is its window. |
| `PERIOD` under the yearly block | A per-row yearly window (`DEC'25-NOV'26`); it moves the YEARLY target only. |
| `Tier n` / `Tier n Incentive` | Threshold and incentive of tier n, paired by n inside one period. |
| `POINT` / `PTS` in the column's own headers | That COLUMN is in points; any other column is RM (units per column, item 4). |
| `n%` as a header, e.g. `4%` under `Q1` | Default rate for a threshold in that column with no incentive of its own. |

Without `Tier n` headers, a column whose header says `%`, `RATE` or `INCENTIVE` holds the rates
of the threshold column to its left, and a lone column holds tier text (`300,000 - 2%`).

## Which customer a row is (owner ruling 16 Oct 09:12)

1. A pick the user made in the preview for that row (or Skip).
2. A CUSTOMER CODE on the row: matched without regard to case or spaces; unknown fails the row.
3. Otherwise the NAME: the file's dealer name and every CRM customer name are normalised (case,
   spaces, punctuation ignored; bracketed ledger markers like `[A/C I]` dropped; `SDN BHD`,
   `SDN. BHD.`, `S/B` dropped; `ENTERPRISES` = `ENTERPRISE`), then the account label must match:
   `A/C I` .. `A/C IV` = the customer's `account_level` 1 .. 4, any other label (`[CERAMIC]`,
   `Deluxe`) = a bracketed marker in the customer's name (the Mocha A/C II / III ledgers have no
   level, only the marker). Exactly one candidate = auto-mapped. None, two or more = `needs_pick`
   with up to 3 suggested customers; nothing ambiguous is linked on its own.

## Cells

Merged cells are read as their top-left value on every row they cover. **A merged threshold
over several data rows makes those rows one target**, per scheme kind and unit.

- Thresholds: `300000`, `300,000`, `RM 300,000`, `300k`, `>=1500000`, `≥1500000`.
- Incentives: `0.02` (a number below 1 is a fraction: 2%), a `%`-formatted cell, `2%`, `3`,
  `A/C I- 3%` (only that account earns, ruling 2), `Deluxe 2% / Mastile 3%`,
  `+ Additional 1%`, `3% + Additional 1%`.
- An incentive naming an account of the same dealer that is NOT counted in the target is the
  pay-to case: the target counts its own rows and pays the named account.
- A rate typed on some rows of a merged target only is those accounts' rate.
- `+ Additional x%` written as a second tier at a threshold already paid, or with no threshold
  of its own, is that threshold's additional line (computed and rounded on its own, ruling 3;
  every additional line reached pays, ruling 5). A second tier at the same threshold with a
  plain rate is NOT guessed: its row fails with "repeats in one period".
- A rate above 20% (`RATE_LIMIT_PCT`; e.g. `0.5`, read as 50% under the fraction rule) fails
  its row: "rate 50% looks wrong: Excel 0.5 means 50%, type 0.005 or 0.5% for half a percent"
  (crew ruling 16 Oct). The real files top out at 10%. Two unlabelled rates in one cell (`2% / 3%`) and a rate typed in a threshold column
  fail the row.
- All targets of one dealer (schemes sharing a row or a pay-to account) are created together or
  not at all; two rows of one file with the same accounts and dates fail the second.
- `-` or blank: no tier. `NIL` beside a threshold, an amount where a rate belongs
  (`A/C I- 100000`), an account the dealer does not have (`A/C 1`), or a threshold with no
  incentive: the row fails with the reason (item 7). Never mapped silently.

## Report (dry run and apply)

One row per sheet data row: `sheet`, `row`, `pick_key` (`<sheet>!<row>`), `code`,
`name_in_file`, `account_in_file`, `customer`, `match` (`auto` | `code` | `picked`),
`suggestions`, `status` (`mapped` | `needs_pick` | `skipped` | `not_representable`), `reason`,
`warnings`, `target_keys`, and on apply `target_nos`. The summary counts each status, plus
`auto_mapped` (rows matched by name on their own). A target is created only when every row of
it (and its pay-to account) is mapped. The dry-run script exits non-zero unless every row is
mapped and every sheet was read (`gate_passes`).

## Second real-file gate (16 Oct): shapes the files really have

- No ACCOUNT column: the account is a suffix of the dealer name cell ("<dealer> A/C I",
  "<dealer> - A/C III", "<dealer> (A/C IV)"); the reader splits it off. A CRM name with the same
  bare suffix answers to that account too.
- `TIER n` beside a plain `INCENTIVE` column: an incentive pairs with the threshold column right
  before it, else with the same `Tier n`, else with the one incentive of its block.
- `QUARTERLY (POINT)` over `JAN-MAR` .. `OCT-DEC` plus one `INCENTIVE`: one quarterly points
  target, that incentive paying every quarter.
- `Monthly` over `Tier n` / `Tier n Incentive` with no month: the same tiers every month.
- A column under period words the reader cannot place fails every row with a figure in it.
- Yearly Tier 1-3 per account beside Tier 4-5 merged over the accounts (crew ruling 16 Oct, from
  owner ruling 4, one tier scale): one dealer target (Tier 4-5, combined sales, rate per account)
  plus one target per account (Tier 1-3, its own sales) that names the dealer target in
  `replaced_by_target_id`. In a period the dealer target reaches a tier, the account target
  pays nothing; below it, the account target pays its own tier.

## Fifth real-file gate (16 Oct, round 4 on the 15 Oct prod backup)

- A name whose ledgers hold none of the file's account (Mocha A/C II / A/C III beside "[A/C I]",
  "[CERAMIC]", "[IBORN]") waits on a pick with "no ledger of this name has the account" and that
  dealer's ledgers as the suggestions. It is never called "more than one customer". A/C II is not
  read as [CERAMIC] unless the owner rules so.
- Ledgers of one name and account whose names differ only in punctuation or spacing: the one the
  file writes letter for letter wins (case and runs of spaces aside, markers dropped). When the
  file writes neither way, the row stays a pick.
- The same threshold written twice in one period with the same rate is one tier, with a warning
  on the row. Different rates at one threshold still fail with "repeats in one period".

## Sixth real-file gate (17 Oct, round 5)

- The import reads and writes for ONE acting company, the session's single-company scope; any
  other scope (all companies, none) fails with "Import dealer targets for one company at a time"
  (`IMPORT_NEEDS_ONE_COMPANY`). The customer lookups (name index, CUSTOMER CODE, picks) filter
  on that company by name as well, so a session without the scope listeners cannot read another
  company's ledgers. The dry-run script installs the listeners before it opens a session.
- A file name with a bracketed branch ("<NAME> (KL)") that the one ledger of the name lacks waits
  on a pick ("no ledger of this name has the branch "KL""), suggesting that ledger. Not a branch:
  an account marker, the account the ledger answers to ("(CERAMIC)"), "(M)" and a registration
  number ("(123456-X)").
- Rows of one target that land on the same CRM customer (three "A/C I" rows of one dealer) fail
  with "rows 2, 3 and 4 are one CRM customer; a target lists each account once".
- The masked dry run keeps the row numbers a reason points at.

## Mocha account rule (owner, 17 Oct)

- In Mocha's file (company code MCH) A/C I is the dealer's "- [A/C I]" ledger, A/C II its
  "- [CERAMIC]" ledger and A/C III its "- [IBORN]" ledger (`ACCOUNT_LABEL_LEDGERS`). The rule
  only adds an account label to a ledger of the same dealer name; it never matches across
  names. Every other company reads A/C II / A/C III as account levels only.
