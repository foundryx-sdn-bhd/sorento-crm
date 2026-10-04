# CARD: one customer per debtor code per company (DEBTOR-CODE-UNIQUE)

Owner decision 4 Oct: debtor code is UNIQUE PER COMPANY, one CRM customer row per AutoCount
debtor code, like AutoCount. `301-*` cash / Shopee / walk-in codes included: AutoCount holds one
debtor for each, and each document keeps its buyer in its own `debtor_name`.

All examples are masked (public repo). Codes like `301-X007` and names like `NAME-A` are fake.
This sandbox cannot reach the dev DB, so counts are the crew's 4 Oct read-only checks on shared
dev; the dry run (R11) re-measures them before anything is applied.

## Today (main, measured in code)

1. The only unique key is `(company_id, lower(btrim(code)), lower(btrim(name)))`,
   `uq_customers_company_code_name_lower` (`app/models/order.py:248-254`). Crew dev count: 374
   (company, code) groups hold more than one row, 1,400 rows (SRT 360 groups, MCH 14). Biggest:
   a `301-*` cash code with 231 rows, another with 100.
2. Create paths key on (code, name), so a new buyer name under a known code mints a new row:
   - `CustomerService._upsert_customer_from_debtor` (`app/services/order_service.py:2211-2257`),
     via `_sync_order_master_refs` (`:2285`, called from create/update order and the Excel
     tracking import `:3079`, `:3124`, `:3208`);
   - `customer_back_create.get_or_create` (`app/services/scm/customer_back_create.py:30-79`),
     used by `MasterRefResolver` (`app/services/master_ref_resolver.py:322`) and the outstanding
     import (`app/services/scm/outstanding_import_service.py:2695`);
   - masters ingest `_adopt_customer` (`app/services/master_ingest_service.py:649-662`; #1474
     replaces it with a code-first lookup);
   - manual create `CustomerService.create_customer` (`order_service.py:3694-3737`, 409 only on
     the same code + name);
   - customer Excel import (`app/services/customer_import_service.py:313`, `:556-568`).
3. Code-only lookups pick an arbitrary row of a shared code: `MasterRefResolver._resolve_by_code`
   (`master_ref_resolver.py:333-370`, highest id), outstanding `_resolve_parties`
   (`outstanding_import_service.py:465-479`, last row wins), `.one_or_none()` at `:2682` would
   raise on a shared code.
4. The chatbot finds a customer by joining `lower(btrim(customer_name)) = lower(btrim(debtor_name))`
   (`app/services/entity_resolver.py:3007`, and the ORM joins at `:1158-1240`, `:2016-2065`,
   `:3691`) and searches `customer_name` by trigram (`:3040-3049`).
5. Rows referencing customers (runtime `pg_constraint`, 10 FKs): `customer_contacts`,
   `orders`, `sales_orders`, `respond_contact_customers`, `stock_asks`, `projects.parties`,
   `projects.leads` (RESTRICT), `projects.customer_item_code_map`,
   `finance.billing_documents`, `sales.opportunities` (RESTRICT). Soft references:
   `integration_references` (`entity_type='customers'`), `audit_logs` (`'customer'`),
   `embedding_*` (`source_type='customer'`), `scm.market_segment_backfill_425` (no FK),
   chatbot `respond_contacts.session_vars`. (`customer_addresses` does not exist.)
6. Unique indexes that collide when two rows become one: `uq_respond_contact_customers_pair`
   (contact, customer), `uq_respond_contact_customers_one_primary`,
   `uq_customer_contacts_one_main_per_customer`, `projects.uq_customer_code_map`
   (company, customer, customer_code), `uq_integration_ref_entity` (one ref per row).

## Rules (proposed)

**Identity**

- **R1** A customer is identified by company + debtor code (`lower(btrim(code))`). Never by
  name, never across companies (`300-X004` in SRT and in MCH stay two rows).
- **R2** Every create path looks up by company + code first. Found: use that row, never rename it
  from a document (only the AutoCount masters push may rename, #1474 rules). Not found: create
  one row with the document's code and name. Manual create of an existing code: 409 "Debtor code
  already exists" with the existing customer named.
- **R3** Documents keep their own buyer name: `orders.debtor_name`, `sales_orders.debtor_name`
  (sdn_0001, filled by #1474), billing `customer_name`. Screens already print the document name
  first.
- **R4** Until the merge has run, a code with several rows resolves to the would-be survivor
  (R6), so the lookup is deterministic and every new document lands on the row the merge keeps.

**Merge (a script, not an alembic auto-run)**

- **R5** One group = one (company, `lower(btrim(code))`) with more than one row.
- **R6 Survivor:** the row linked to AutoCount (`integration_references`, entity_type
  `customers`); if several rows are linked, the one whose ref is code-form (`<db>:<code>`); else
  the oldest `created_at`, ties to the lowest id. The survivor keeps its name, code and ref.
- **R6a Survivor name.** The merge never renames the survivor. Where a ref exists the survivor is
  the AutoCount-linked row, whose `customer_name` the masters push writes from AutoCount's own
  debtor name (`app/services/master_ingest_service.py:473` builds the columns from
  `payload.name`; a ref match updates that row at `:1212-1228`). After #1474 the push finds that
  same row by code, so the next push keeps it at AutoCount's current name. The plan CSV states
  `kept_reason` (`code_form_ref` | `autocount_ref` | `oldest`) and `survivor_ref` per group.
- **R7 Fields:** the survivor keeps every value it holds; a NULL/blank on the survivor is filled
  from the retired rows (oldest first). Where two rows both hold a value and differ on customer
  group, active, sales agent, market segment or account owner (survivor against a retired row,
  or two retired rows when the survivor holds none), the group is a **conflict**: listed in the
  dry-run CSV and NOT merged until the owner names a `keep` row (Q2), whose values then win on
  those fields. Personal and contact fields (phone, mobile, email, address, tax id, registered /
  trading names, person names, notes, website) are NEVER carried from a retired row (review S1:
  under a cash code those are one walk-in buyer's details); the CSV's `not_carried` column counts
  them and the merge map keeps them in full. Phone is the one personal field that is also a
  conflict: a retired row's phone that differs (on digits) from the kept row's own phone is
  listed and blocks the group until `keep` is named (crew 4 Oct); two buyers' phones with no
  kept phone are not a conflict, since neither is carried.
- **R7a Listed, never acted on** (crew 4 Oct): `name_mismatch` (retired names that differ from
  the kept name once case, punctuation and `SDN BHD` / `BERHAD` / `ENTERPRISE` / `TRADING` /
  `(M)` noise are removed, i.e. likely another business under the same code) and
  `ref_conflicts` (every row's AutoCount ref when more than one row holds one; Q1 (a) already
  keeps the code-form one).
- **R8 Repoint:** every FK (discovered from `pg_constraint` at run time, as
  `scripts/dedupe_spo_number_format.py` does) moves from retired ids to the survivor. Before the
  repoint, a document with no `debtor_name` gets the retired row's name, so no order loses the
  buyer name it showed.
- **R9 Collisions:**
  - `respond_contact_customers` (an external contact's access, owner 4 Oct, security review B1
    (a)): a retired row's links are never repointed. They are dropped (kept in the map, restored
    by revert) and listed in the CSV's `contact_links_dropped` column so CS re-links each contact
    deliberately. A contact already linked to the survivor keeps that link unchanged.
  - `customer_contacts`: the survivor's main contact stays main; a retired row's main becomes
    `stakeholder`.
  - `projects.customer_item_code_map`: same (company, customer_code) on both: the survivor's
    mapping stays, the retired one is dropped (logged).
  - `integration_references`: the survivor keeps its ref; every retired row's ref is deleted
    (logged, restored by revert). A linked row always wins survivorship (R6), so the survivor
    never lacks a ref while a retired row holds one. Owner rule: linkage by code, so a
    code-form ref wins over an AutoKey-form one.
  - `scm.market_segment_backfill_425` (the downgrade ledger of migration 425, no FK): the
    retired id's row is dropped (logged, restored by revert).
  - Embeddings: the retired row's documents are deleted (derived data, not restored by
    revert); the survivor's stay.
  - `customer_aliases` already on a retired row move with it; one equal to a survivor alias
    is dropped (logged).
- **R10 Retired rows** are deleted after the repoint. Each one's full row, every repointed FK row
  id and every dropped collision row is written to `customer_merge_map` first, plus one
  `audit_logs` row `CUSTOMER_MERGE` per retired id (entity = survivor, old_values = the retired
  row). Existing audit history is never rewritten (it is append-only by trigger).
- **R11 Modes:** `--dry-run` (default) writes one CSV row per group (company, code, survivor,
  retired ids + names, FK counts per table, collisions, conflicts) and changes nothing.
  `--apply` runs one transaction per company, under `--actor <user>` (named on every audit row),
  and refuses to delete a retired row while any FK still points at it. `--revert <run id>` re-inserts the retired rows from
  `customer_merge_map` and points every recorded row back.
- **R12 Aliases:** every retired name goes to
  `customer_aliases (company_id, customer_id, alias_name)`, unique per customer on
  `lower(btrim(alias_name))`. The chatbot name join and trigram search match
  `customer_name OR alias`, within the order's own company, so "find NAME-B2" still lands on the merged customer.
- **R13 Unique index** `uq_customers_company_code_lower` on `(company_id,
  lower(btrim(customer_code))) WHERE btrim(customer_code) <> ''` comes LAST (Q4 (a)):
  `--create-index` adds it after a clean apply and refuses while any code holds two rows; a
  revert drops it first. The model declaration, and dropping the now-redundant (code, name)
  index, ride the follow-up migration that lands after prod is merged.
- **R15 Ordering with #1474.** Once the index exists, a masters push whose name differs from the
  customer's must UPDATE the code's row, never insert: that is #1474's code-first ingest. So
  `--create-index` waits until #1474 is merged and deployed.
- **R14 Order:** sandbox copy -> crew applies on shared dev -> prod only with owner approval.

## Examples (masked)

| # | Group | Today | After the merge |
|---|---|---|---|
| E1 | `301-X007` (cash sales), 231 rows: one linked `AED_SORENTO:301-X007` named `CASH SALES`, 230 DO back-created names (`BUYER-1` ... `BUYER-230`), each with its own SOs/DOs | 231 customers; the customer list shows 231 rows for one AutoCount debtor | 1 customer `CASH SALES` (the linked row). Every SO/DO points at it and still prints its own buyer name. 230 aliases. Chatbot "orders for BUYER-17" still finds them through the alias. |
| E2 | `300-X002`: ledger `NAME-B` (2025-01, unlinked) and DO row `NAME-B SDN BHD` (2026-09, unlinked) | 2 rows | Survivor = the 2025 ledger (oldest). `NAME-B SDN BHD` becomes an alias. |
| E3 | `300-X003` with two refs: row A `AED_SORENTO:1003` (AutoKey form, 2025-01), row B `foundryx_service_s51:300-X003` (code form, 2026-10) | 2 linked rows | Survivor = row B (code-form ref, R6). Row A's AutoKey ref is deleted and logged; A's documents move to B. |
| E4 | `300-X008`: survivor in customer group `DEALER`, retired row in `RETAIL` | 2 rows | Conflict: listed in the dry-run CSV, group skipped until the owner picks. |
| E5 | `300-X009`: WhatsApp contact `+60-xxx` linked (primary) to both rows | 2 links | One link to the survivor, primary. |
| E6 | `300-X004` exists in SRT and in MCH | 2 rows, 2 companies | Untouched: not a group (R1). |
| E7 | After the merge, an Excel tracking row arrives for `301-X007` buyer `BUYER-999` | would create row 232 | Links to `CASH SALES`; the order stores `debtor_name = BUYER-999` (R2/R3). |

## Edge cases

- Blank code: the Excel tracking path mints `DBR-<md5(name)>` (`order_service.py:2211+`); that
  stays, so blank-code buyers stay separate rows.
- Codes that differ only by case or spaces (`300-x001 ` vs `300-X001`) are one group.
- A retired row referenced by `projects.leads` / `sales.opportunities` (RESTRICT): repointed
  first, so the delete never fails.
- A live chatbot conversation holding a retired id in `session_vars` focus: the next lookup
  misses and re-resolves by code (no rewrite of session JSON).
- Prospect codes (`P-<STEM>`) from project leads are already unique per company
  (`project_lead_service.py:385-401`), unaffected.

## Questions (with recommendations)

**Answered 4 Oct (crew): Q1-Q5 all (a).** Conflict CSV from the dev-copy dry run goes back to
the owner as a `crew-ask` so the `keep` column can be filled.

- **Q1 Survivor when several rows are AutoCount-linked:** (a) the code-form ref row, else the
  oldest linked row (R6) (b) always the oldest. Recommend (a): owner rule "linkage by code".
- **Q2 Conflicts (crew dev count: group ~17, phone 1, active 1):** (a) dry run lists them, apply
  skips those groups until the owner fills a `keep` column in the CSV (survivor / retired id);
  the unique index waits for them (b) survivor wins silently. Recommend (a): the brief says do
  not pick silently.
- **Q3 Retired rows:** (a) hard delete after the repoint, full row kept in `customer_merge_map`
  for revert (b) keep as inactive rows with `merged_into_id`. Recommend (a): (b) needs every list,
  lookup and the unique index to filter merged rows forever.
- **Q4 Unique index timing:** (a) this PR ships the alias + merge-map tables and the code-first
  paths; the merge script creates the index after a clean `--apply`, and a follow-up one-line
  migration (`CREATE UNIQUE INDEX IF NOT EXISTS`) lands after prod is merged (b) this PR's
  migration creates the index (deploy fails on prod until merged). Recommend (a): a merge or
  deploy never depends on a destructive run.
- **Q5 Old audit history:** (a) leave it on the retired ids, add a `CUSTOMER_MERGE` row on the
  survivor naming each retired id (b) rewrite old rows to the survivor (needs the audit
  maintainer role). Recommend (a): audit is append-only by design.
