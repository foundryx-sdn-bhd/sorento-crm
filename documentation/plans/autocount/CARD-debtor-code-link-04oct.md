# CARD: debtor identity = company + debtor code (DEBTOR-CODE-LINK)

Owner rules this card is built on:

- 4 Oct ~11:20: "for debtor, I want our linkage to be the debtor code, don't need to care about
  the key." Identity between AutoCount / shared service and the CRM is company + debtor code
  (AccNo), never AutoKey, never name.
- 4 Oct ~11:25: when several CRM customers share a debtor code, the master feed updates the FIRST
  one. Sales orders and DOs follow the debtor name on the document, so which row the master feed
  updates does not matter for documents. The sales agent still goes to every same-code row (#1468).

All examples are masked (public repo). Codes like `300-X001` and names like `NAME-A` are fake.
This sandbox cannot reach the dev DB, so the examples are built on the crew's dev counts
(2,415 `AED_SORENTO:<AutoKey>` customer links; 18 of 44 records failed in the 4 Oct local E2E).

## Today (main, measured in code)

1. `MasterIngestService._apply_scoped` matches a pushed customer by `source_ref` first
   (`app/services/master_ingest_service.py:1213`). The vendor API minted `AED_SORENTO:<AutoKey>`;
   the open REST `/debtorbypage` rows have no AutoKey, so ss mints `<db>:<AccNo>` and the ref
   never matches.
2. On a ref miss it adopts by exact (code, name) (`_adopt_customer`, `:649-662`). When that row
   already holds a different ref, the record FAILS: `customer_code=... is already linked to
   another source` (`:1286`). That is the 18/44.
3. Codes are not unique: the unique key is (company, code, name)
   (`app/models/order.py`, `uq_customers_company_code_name_lower`). DO back-created rows
   (`customer_back_create.get_or_create`) share a ledger's code under a different name.
4. One customer row holds at most one ref (`uq_integration_ref_entity`), and one ref names at most
   one row per company (`uq_integration_ref_source_company`).

## Rules (proposed)

- **R1 Identity.** A pushed debtor is matched by company + debtor code: trimmed, case-insensitive
  (`300-x001 ` = `300-X001`). The `source_ref` and the name are never used to pick the row.
- **R2 No row with the code.** Create one (code, name, the other fields), and link the pushed ref.
  Outcome `created`.
- **R3 One or more rows with the code.** Update the FIRST one: oldest `created_at`, then lowest
  `id` as a tie-break (rows from one import batch share a `created_at`). Name, phone, email,
  registration no, tax id, country, region, active and segment (fill-only, unchanged) apply to
  that row only. Outcome `updated`.
- **R4 Other same-code rows.** Untouched, except the sales agent, which #1468 already writes to
  every same-code row of the company. (This lane keeps that; it does not add any other fan-out.)
- **R5 The pushed ref.** Bookkeeping only, never a reason to fail:
  - the first row has no ref and the pushed ref is free: link it;
  - the first row already holds the pushed ref: keep it (re-link touches `last_synced_at`);
  - the first row holds a different ref, or the pushed ref belongs to another row: keep what is
    stored, link nothing, warn `ref_mismatch` (the same code-wins rule products use since
    contract 2.4, `PLAN-ingest-products-code-wins.md`).
- **R6 Name clash.** If renaming the first row to the AutoCount name would collide with a sibling
  that already has that exact (code, name), the first row keeps its name, every other field
  still applies, warning `customer_name_taken`. (Without this the unique index fails the whole
  record.)
- **R7 Companies.** Matching never crosses companies: `300-X001` in SRT and `300-X001` in MCH are
  two customers, each found only by its own company's push.
- **R8 Documents unchanged.** SO / DO / billing customer resolution is not touched (owner,
  11:25).
- **R9 One-off link conversion.** Every existing customer ref `<prefix>:<AutoKey>` becomes
  `<prefix>:<debtor code>` (the row's own trimmed `customer_code`, prefix kept), so no customer
  link carries an AutoKey any more and a deletion or document ref in code form finds the row.
  - Idempotent: a ref already equal to `<prefix>:<code>` is left alone; a second run changes 0.
  - Collision (the target ref already names another row of that company): the FIRST row by R3
    gets it, the other keeps its old ref and is listed in the report. Nothing is deleted.
  - Additive and reversible: the script writes a CSV of (ref id, old, new) before it changes
    anything; `--revert <csv>` puts the old values back.
  - Delivered as a script with `--dry-run` (default) / `--apply`, not an alembic migration, so a
    merge or deploy never rewrites prod links. Dev first; prod is a crew-held run after owner
    approval.

## Examples (masked)

| # | Situation | Push | Today | Proposed |
|---|---|---|---|---|
| E1 | `300-X001` has one row `NAME-A`, linked `AED_SORENTO:1001` | `<db>:300-X001`, `NAME-A`, new phone | FAILED (already linked) | Row updated (phone). Ref kept, warn `ref_mismatch` until R9 runs; after R9 the stored ref is `AED_SORENTO:300-X001`, and if `<db>` is `AED_SORENTO` there is no warning at all. |
| E2 | `300-X002` has a ledger `NAME-B` (created 2025-01) and a DO back-created row `NAME-B SDN BHD` (created 2026-09) | `NAME-B SDN BHD (NEW)`, agent `AGENT-A I` | FAILED or adopts by exact name only | The 2025 ledger is renamed `NAME-B SDN BHD (NEW)` and gets the phone etc.; the DO row keeps its name; both get `AGENT-A I` (#1468). |
| E3 | `300-X003`: ledger `NAME-C` (2025-01) and DO row `NAME-C2` (2026-09); AutoCount now says `NAME-C2` | name `NAME-C2` | adopts the DO row by exact name, the ledger never updates | The ledger is first, but `NAME-C2` is taken by the sibling: ledger keeps `NAME-C`, other fields apply, warn `customer_name_taken`. |
| E4 | `300-X004` exists in SRT and in MCH | SRT push | SRT row only | SRT row only (R7). |
| E5 | `300-X005` not in the CRM | push | created | created, ref linked (R2). |
| E6 | AutoCount renumbers `300-X006` to `300-X060` (AutoKey unchanged) | `<db>:300-X060` | vendor API: ref match, the old row's code is changed to `300-X060` | No row has `300-X060`: a new ledger is created; the `300-X006` row and its history stay as they are. |

## Edge cases

- Blank or whitespace code: rejected by the schema today (`min_length=1`), unchanged.
- Code renumbered in AutoCount: E6. Owner said not to care about the key, so the CRM cannot know
  it is the same debtor. Staff merge by hand if needed.
- Code reused across companies: R7.
- A code later reused in AutoCount for a different debtor: it updates the same CRM ledger (that
  is what code identity means).
- Dry run / pull preview: takes the same path and rolls back, so the preview shows the R3 target
  and the R5/R6 warnings.

## ss side

No ss change is needed for the CRM to work: the CRM ignores the ref when matching. The
`/debtorbypage` preset already keys on `AccNo`. If the vendor-API debtor preset still keys on
AutoKey, switching it to `AccNo` makes new links code-form from day one; optional, ss partner lane.

## Questions (with recommendations)

- Q1 "First" row: (a) oldest `created_at`, then lowest id (b) the row that already holds an
  AutoCount ref, else (a). Recommendation (a): it is what the owner asked for and it does not
  depend on refs at all.
- Q2 Pushed ref differs from the stored one: (a) keep stored + warn `ref_mismatch` (b) re-key the
  stored ref to the pushed one. Recommendation (a): the products precedent, and (b) can collide
  with the 26 E2E refs already on dev.
- Q3 Converted ref prefix: (a) keep the existing prefix, `AED_SORENTO:<code>` (b) use the ss HTTP
  database name. Recommendation (a), and crew confirms the ss HTTP preset's database name for SRT
  prod is `AED_SORENTO`; if it is not, R5 still works, it just warns `ref_mismatch`.
- Q4 Name clash (R6): (a) keep the old name + warn (b) fail the record as today.
  Recommendation (a): one clash should not stop the phone and agent from updating.
