# CARD: debtor identity = company + debtor code (DEBTOR-CODE-LINK)

Owner rules this card is built on:

- 4 Oct ~11:20: "for debtor, I want our linkage to be the debtor code, don't need to care about
  the key." Identity between AutoCount / shared service and the CRM is company + debtor code
  (AccNo), never AutoKey, never name.
- 4 Oct ~11:25: when several CRM customers share a debtor code, the master feed updates the FIRST
  one. Sales orders and DOs follow the debtor name on the document, so which row the master feed
  updates does not matter for documents. The sales agent still goes to every same-code row (#1468).
- 4 Oct ~11:30 (Q2 of the owner round): within company + debtor code, a row whose name matches
  AutoCount's name exactly (trim / case-insensitive, the unique index's own rule) is updated;
  otherwise the oldest-created row is updated and renamed. Never match by name alone, no fuzzy
  matching.

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
  (`300-x001 ` = `300-X001`). The `source_ref` never picks the row, and a name never picks a row
  on its own: a same-name row under another code is never touched.
- **R2 No row with the code.** Create one (code, name, the other fields), and link the pushed ref.
  Outcome `created`.
- **R3 One or more rows with the code.** Pick the target among the same-code rows of the company:
  1. the row whose name equals the pushed name exactly (`lower(btrim(...))`, the same comparison
     as `uq_customers_company_code_name_lower`, so at most one row can match);
  2. otherwise the oldest `created_at`, then lowest `id` (rows from one import batch share a
     `created_at`), renamed to the pushed name. That rename cannot clash: step 1 found no row with
     that name under this code.
  Name, phone, email, registration no, tax id, country, region, active and segment (fill-only,
  unchanged) apply to the target only. Outcome `updated`.
- **R4 Other same-code rows.** Untouched, except the sales agent, which #1468 already writes to
  every same-code row of the company. (This lane keeps that; it does not add any other fan-out.)
- **R5 The pushed ref.** Bookkeeping only, never a reason to fail:
  - the target has no ref and the pushed ref is free: link it;
  - the target already holds the pushed ref: keep it (re-link touches `last_synced_at`);
  - the target holds a different ref, or the pushed ref belongs to another row: keep what is
    stored, link nothing, warn `ref_mismatch` (the same code-wins rule products use since
    contract 2.4, `PLAN-ingest-products-code-wins.md`).
- **R6 No name clash possible.** Removed by the owner's 11:30 rule: a rename only happens when no
  same-code row has the pushed name (R3 step 2), so the unique index cannot fire.
- **R7 Companies.** Matching never crosses companies: `300-X001` in SRT and `300-X001` in MCH are
  two customers, each found only by its own company's push.
- **R8 Documents unchanged.** SO / DO / billing customer resolution is not touched (owner,
  11:25).
- **R9 One-off link conversion.** Every existing customer ref `<prefix>:<AutoKey>` becomes
  `<prefix>:<debtor code>` (the row's own trimmed `customer_code`, prefix kept), so no customer
  link carries an AutoKey any more and a deletion or document ref in code form finds the row.
  - Idempotent: a ref already equal to `<prefix>:<code>` is left alone; a second run changes 0.
  - Collision (the target ref already names another row of that company): the oldest-created
    row (ties to the lowest id) gets it, since a conversion has no pushed name to compare; the other keeps its old ref and is listed in the report. Nothing is deleted.
  - Additive and reversible: the script writes a CSV of (ref id, old, new) before it changes
    anything; `--revert <csv>` puts the old values back.
  - Delivered as a script with `--dry-run` (default) / `--apply`, not an alembic migration, so a
    merge or deploy never rewrites prod links. Dev first; prod is a crew-held run after owner
    approval.

## Examples (masked)

| # | Situation | Push | Today | Proposed |
|---|---|---|---|---|
| E1 | `300-X001` has one row `NAME-A`, linked `AED_SORENTO:1001` | `<db>:300-X001`, `NAME-A`, new phone | FAILED (already linked) | Row updated (phone). Ref kept, warn `ref_mismatch` until R9 runs; after R9 the stored ref is `AED_SORENTO:300-X001`, and if `<db>` is `AED_SORENTO` there is no warning at all. |
| E2 | `300-X002` has a ledger `NAME-B` (created 2025-01) and a DO back-created row `NAME-B SDN BHD` (created 2026-09) | `NAME-B SDN BHD (NEW)`, agent `AGENT-A I` | no ref match, no exact name: created a third row | No row has that exact name: the oldest (2025 ledger) is renamed `NAME-B SDN BHD (NEW)` and gets the phone etc.; the DO row keeps its name; both get `AGENT-A I` (#1468). |
| E3 | `300-X003`: ledger `NAME-C` (2025-01, linked `AED_SORENTO:1003`) and DO row `NAME-C2` (2026-09, unlinked); AutoCount now says `NAME-C2` | `<db>:300-X003`, name ` name-c2 `, new phone | adopts the DO row by exact name | Exact-name match: the DO row `NAME-C2` gets the phone and the pushed ref (it had none); the ledger is untouched; both get the agent. |
| E7 | `301-X007` is a cash-sale code with many DO back-created names, none equal to AutoCount's debtor name | `CASH SALES` | created another row | The oldest `301-X007` row is renamed `CASH SALES` and updated; every other row keeps its name; all get the agent (#1468). |
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
  and the R5 warning.

## ss side

No ss change is needed for the CRM to work: the CRM ignores the ref when matching. The
`/debtorbypage` preset already keys on `AccNo`. If the vendor-API debtor preset still keys on
AutoKey, switching it to `AccNo` makes new links code-form from day one; optional, ss partner lane.

## Questions (with recommendations)

- Q1 "First" row: (a) oldest `created_at`, then lowest id (b) the row that already holds an
  AutoCount ref, else (a). Recommendation (a): it is what the owner asked for and it does not
  depend on refs at all. **Answered (a), owner 4 Oct**: the oldest created row, ties to the lowest id.
- Q2 Pushed ref differs from the stored one: **answered (a), crew 4 Oct**: keep stored + warn
  `ref_mismatch`.
- Q3 Converted ref prefix: **answered (a), crew 4 Oct**: keep the existing prefix. The script
  never hardcodes it: it keeps field one of EACH ref (`split_part(source_ref, ':', 1)`), the rule
  `PLAN-scm-book-linkage-on-document-lines.md:136-139` already sets ("the database name is data").
  What the repo shows about the source DB names:
  - SRT: every AutoCount ref measured so far is `AED_SORENTO:` (9,067 product refs on the 19 Sep
    prod copy, `PLAN-ingest-products-code-wins.md:15`; the 2,415 dev customer links, crew E2E).
  - MCH: held no references at all on the 19 Sep prod copy (`PLAN-ingest-products-code-wins.md:17`),
    so it has nothing to convert; whatever DB name its first push uses is kept as sent.
  - The ss company config (the HTTP preset's `database_name` per company) is in the ss repo, which
    this sandbox cannot read. The 4 Oct local E2E minted `foundryx_service_s51:` (the copy's DB name,
    PR #1468 crew-note). Crew check on dev before the prod run:
    `SELECT c.code, split_part(r.source_ref, ':', 1) AS prefix, count(*) FROM integration_references r
    JOIN companies c ON c.id = r.company_id WHERE r.entity_type = 'customers' GROUP BY 1, 2;`
    If SRT prod's HTTP DB name is not `AED_SORENTO`, nothing fails: matching is by code (R1) and the
    push only warns `ref_mismatch` (R5).
- Q4 Name clash (R6): **superseded, owner 4 Oct ~11:30**: exact (code, name) match first, else
  the oldest row, so a clash cannot happen.
