# CUSTOMER-SALES-AGENT behaviour card (4 Oct 2026)

Facts from code on origin/main 3827d8da (file:line, backend paths under `sorento_crm_backend/`).
Dev-copy counts are the crew's (this cloud sandbox has no dev DB and its network policy blocks the
AutoCount debtor host, see Q5); every example below is masked.

## What exists already (reuse, do not rebuild)

- `customers.sales_agent_id` FK `sales_agents` SET NULL (`app/models/order.py:172-176`); the ORM
  properties `sales_agent_code` / `sales_agent_name` (`order.py:218-233`) already reach the customers
  list (`CustomersList.tsx:161-171`, "CODE - person") and detail (`CustomerDetail.tsx:147-150`).
- `sales_agents.person_label` exists (`app/models/sales_agent.py:62`), is editable on the agent
  detail (`SalesAgentDetail.tsx:116,194`) through `sales_agent_service.annotate`
  (`app/services/scm/sales_agent_service.py:245-247`). It is NULL on all 80 agents today.
- Agent codes are stored/compared `upper(btrim())` (`sales_agent_service.normalize_code:40`); shared
  rows have `company_id IS NULL` (`sales_agent.py:80`).
- Customer push: `_customer_columns` (`app/services/master_ingest_service.py:468-496`); unknown
  market segment drops with warning `segment_unknown` (`:489-495`). Each record runs in its own
  savepoint, dry run rolls the batch back (`:1115-1124`, `:910-915`).
- An `agent_unresolved` warning word already exists in the contract vocabulary
  (`app/services/finance/billing_document_ingest_service.py:99`, `api/v1/external/contract.py:248`).
- DO/outstanding ingest back-creates a customer on a debtor code+name pair with no source ref
  (`app/services/scm/customer_back_create.py:63`), which is how one code ends up on several rows.
- notify-salesman reads `customer.sales_agent_id` (`app/services/stock_ask_service.py:383`): untouched.

## Rules

R1. **Field.** `CanonicalCustomer.sales_agent_code` (optional, max 100). Key absent = the agent is
untouched (an older shared service never sends it).

R2. **Resolve.** Normalised code (`upper(btrim())`) against `sales_agents` visible to the company
(shared or the company's own), the same match `_lookup_id(..., normalized=True)` does. No create.

R3. **Unknown code.** Record still ingests, agent untouched, warning `agent_unresolved` (existing
word; no new vocabulary).

R4. **Write.** The resolved agent is written on the ledger the record links to (by source ref,
adoption or create) AND on every other `customers` row of the SAME company whose code equals the
debtor code (`lower(btrim())`), which covers DO back-created rows. Never a row with another code,
never another company.

R5. **Blank code** (AutoCount debtor with no agent): no-op, the CRM agent is left unchanged on
every row. Only a non-blank code that resolves overwrites (owner, Q1 (b)).

R6. **Person.** `person_label` default = code minus a trailing roman-numeral level
(`I..X`, separated by space and/or `-`), trimmed: `AGENT-A III` -> `AGENT-A`, `AGENT-C - I` -> `AGENT-C`,
`ABC` -> `ABC`, `QI` -> `QI` (no separator, not a level). Filled ONLY where it is NULL: a one-off
migration backfill, plus on agent create (ingest `sales_agents` insert and
`sales_agent_service.resolve_or_create`). A label staff typed is never overwritten. Staff edit it on
the existing agent detail.

R7. **Group agent.** Over the group's ledgers that HAVE an agent: one distinct person
(`lower(btrim(person_label))`, falling back to the derived label) = that person, shown as the
person label; two or more = "Mixed". No ledger with an agent = blank. Ledgers without an agent do
not make a group mixed (Q3).

R8. **Office list.** Customer Groups list gets an Agent column (person / "Mixed" / blank) and a
filter "Mixed agents only". Group detail shows the same value under the header, and its Ledgers tab
gains an Agent column (the API already returns `sales_agent_code` / `sales_agent_name` on each
ledger via `CustomerResponse`; the tab does not draw it today), so the office sees which ledger
disagrees.

## Examples (masked)

| # | Situation | Push | Result |
| --- | --- | --- | --- |
| E1 | Ledger `300-X0**` linked to `DB1:1**`, agent unset | `sales_agent_code: "agent-a iii"` | ledger agent = `AGENT-A III`; person `AGENT-A` |
| E2 | Code `300-X0**` on 2 rows: the linked one + a DO back-created row with a spelling-variant name | `sales_agent_code: "AGENT-B - I"` | both rows = `AGENT-B - I` |
| E3 | Ledger has hand-set `AGENT-C - II` | `sales_agent_code: "AGENT-C - I"` | `AGENT-C - I` (AutoCount wins) |
| E4 | `sales_agent_code: "NOBODY IX"` | | record UPDATED, agent untouched, warning `agent_unresolved` |
| E5 | Group "GRP-1" = 300-X0** (`AGENT-B - I`), 300-X0** (`AGENT-B III`), 300-X0** (no agent) | | group agent `AGENT-B` |
| E6 | Group "GRP-2" = 300-Y0** (`AGENT-B - I`), 300-Y0** (`AGENT-C - I`) | | "Mixed", listed under the filter |

Crew dev counts: 374 codes on several name rows (agents agree 332, conflict 3); 796 groups / 2800
ledgers, by person 356 agree, 59 conflict.

## Edge cases

- Same code in Sorento and Mocha: each company's push writes only its own rows (R4).
- Inactive agent: still written (AutoCount says so); the customer page shows the code as today.
- Agent push carrying `person_label` (`_sales_agent_columns`, `master_ingest_service.py:645`)
  still writes it when sent, unchanged by this lane.
- Two pushes in one batch for the same code with different agents: last record wins (same
  as any other column).
- Populating `person_label` changes what other screens group/label by (Q2).

## Questions (max 5, each with a recommendation)

- Q1 Blank `SalesAgent`: DECIDED (b) leave the CRM agent unchanged (owner, 4 Oct).
- Q2 Fill `person_label`: DECIDED (a) backfill where empty, accepting that sales achievement
  siblings and the agent text on lists regroup by person (owner, 4 Oct).
- Q3 Unassigned ledgers: DECIDED (a) ignored for Mixed (owner, 4 Oct).
- Q4 Unknown agent code: DECIDED (a) skip + `agent_unresolved` (owner's written decision).
- Q5 Real debtor API / shared service: DECIDED (b) crew runs the ss copy feed -> CRM copy E2E on the
  Mac; this lane does the CRM side and tests in the sandbox with masked fixtures.

Privacy: the repo is public. Every agent code, group name and debtor code in docs and tests is a
fake but stable label (`AGENT-A I`, `GRP-1`, `300-X0**`); never a real name.
