# PLAN: debtor identity = debtor code (DEBTOR-CODE-LINK)

Status: Merge-ready (CI green on f7bd8e24, browser PASS 8/8; owner waived the hand test 4 Oct); archive on merge. Track: L (rewrites customer links; external ingest surface).
Card (rules, examples, questions): `documentation/plans/autocount/CARD-debtor-code-link-04oct.md`.
UAC: the card's rules R1-R10 and examples E1-E7 are the acceptance criteria for this lane.
PR: #1474 (`crew-lane: DEBTOR-CODE-LINK`).

## Journey

AutoCount (through the shared service) pushes a debtor. The CRM finds the customer by company +
debtor code and updates it, whatever key or ref the push carries, so the open REST `/debtorbypage`
feed (no AutoKey) updates the same ledgers the vendor API used to. Today 18 of 44 such records
fail "already linked to another source" (crew E2E, 4 Oct).

## Owner and crew rulings

- Owner 4 Oct ~11:20: linkage is the debtor code, not the key. No name matching.
- Owner 4 Oct ~11:25: several rows per code -> update the first one; documents follow the name on
  the document, so documents are out of scope. Sales agent still goes to every same-code row (#1468).
- Crew 4 Oct: Q2 (a) keep the stored ref and warn `ref_mismatch`; Q3 (a) keep each ref's prefix.
- Owner 4 Oct: Q1 (a) first = oldest created_at, ties to the lowest id.
- Owner 4 Oct ~11:30: exact (code, name) row first, else the oldest row. Never name alone.
- Owner decision 4 Oct: never rename (the oldest row keeps its name); unique code per company
  follows in lane DEBTOR-CODE-UNIQUE; the SO `debtor_name` backfill ships here (R10).
  Q4 (name clash) is gone with it.

## Design (simplest thing that works)

### Slice 1: customer ingest (one seam)

`MasterIngestService._apply_scoped`: for `entity_type == "customers"` the ref-first lookup and the
(code, name) adopt are replaced by one code lookup:

1. `_customer_by_code(company, code, name)`: `SELECT id ... WHERE company_id = :cid AND
   lower(btrim(customer_code)) = :code ORDER BY (lower(btrim(customer_name)) = :name) DESC,
   created_at, id::text LIMIT 1` (exact-name row first, else the oldest).
   When the target's name differs from the pushed one, `customer_name` is dropped from the columns
   (never rename).
2. None -> the existing create path (`_insert` + `_link`).
3. Found -> the existing update path (segment fill-only, `_diff`, `_update`), plus:
   - ref bookkeeping per R5 (link only when both the row and the ref are free; otherwise keep and
     warn `ref_mismatch` unless the row already holds the pushed ref).

No new table, no new column, no registry, no setting. `_adopt_customer` (D13) becomes this lookup for the
masters push; `customer_back_create` and the document ladders are untouched (R8).

### Slice 2: one-off link conversion (R9)

`sorento_crm_backend/scripts/convert_customer_refs_to_code.py`: plan (dry run by default), apply
(CSV of every change written first), revert from the CSV. A script, not an alembic migration, so a
merge or deploy never rewrites prod links; the prod run is crew-held after owner approval.
Equivalent idempotent SQL for the dev copy goes in a `crew-migration:` comment.

### Slice 3: SO issue name (R10)

`alembic/versions/sdn_0002_so_debtor_backfill.py` (data only) + fill-forward in
`document_ingest_service._header_values` and `SalesOrderService.create`. Done, `cb2f2751`.

### ss

No ss change required (the CRM no longer matches by ref). Optional: switch the vendor-API debtor
preset `key_fields` to `AccNo`.

## Test list (red first)

- `tests/test_debtor_code_link.py` T1-T13 (card R1-R7, E1-E6).
- `tests/test_convert_customer_refs_to_code.py` S1-S13 (R9).
- `tests/test_ingest_parity_s0_masters.py` D13 class rewritten to the owner's rule.

## Coordination

#1468 (CUSTOMER-SALES-AGENT, merge-approved, not merged) adds the agent fan-out to the customer
ref / adopt / create paths in the same function. Whichever lands second merges the other: on this
lane the fan-out call moves into the single code-first path.

## Done when

Red tests green, kill tests red, reviewer + security-reviewer clean, conversion dry-run checked on a
sandbox copy, crew E2E through the shared service shows 0 "already linked" failures, owner hand test.
