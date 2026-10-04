# PLAN - Customer sales agent from AutoCount Debtor (CUSTOMER-SALES-AGENT)

**Status:** Review done (reviewer + security fix round 1 green; card answered by the owner 4 Oct: Q1 b, Q2-Q5 a/a/a/b). Awaiting CI + owner hand test. Track: L (LEAD
pattern; additive data migration). Pair lane: SS-DEBTOR-AGENT (shared service maps AutoCount
`Debtor.SalesAgent` onto `sales_agent_code`).

**Card:** `CARD-customer-sales-agent-4oct.md`. **UAC:** `customer-sales-agent-4oct-acceptance-criteria.md`.

## 1. Journey

AutoCount knows which sales agent owns each debtor ledger; the CRM knows it for 7 of 6358
customers (set by hand). The office wants the CRM to follow AutoCount, see one agent per customer
group (by person, since `AGENT-A I` / `AGENT-A III` are one person), and get a list of groups whose
ledgers disagree so they fix them in AutoCount.

## 2. Decisions

- **D1 Ingest field.** `CanonicalCustomer.sales_agent_code` Optional max 100. Absent = untouched.
  Resolved by normalised code over shared + company agents (`_lookup_id(... normalized=True)` on
  `sales_agents`). Unknown = warning `agent_unresolved`, untouched. Blank = untouched too (owner Q1 (b)).
- **D2 Fan-out.** After the record's row is resolved (ref / adopt / create), one UPDATE sets
  `sales_agent_id` on every `customers` row of the anchor company whose `lower(btrim(customer_code))`
  equals the debtor code's. Inside the record's savepoint, so dry run and failures roll it back.
  The linked row's own change appears in `diff` (column in `_customer_columns`).
- **D3 Person label.** `sales_agent_service.derive_person_label(code)` strips a trailing
  `[\s-]+(I|II|III|IV|V|VI|VII|VIII|IX|X)`; filled where NULL by an additive data migration and on
  agent create (`resolve_or_create`, ingest `_insert` for `sales_agents` when the push carries no
  label). Never overwrites.
- **D4 Group agent.** `CustomerGroupService` computes per group: distinct person keys
  (`lower(btrim(coalesce(person_label, derived)))`) over ledgers with an agent. Response adds
  `sales_agent_label` (str|None) and `sales_agent_mixed` (bool). List takes `agent_mixed=true`
  filter. No new table, no stored column (simplest; recomputed on read, 796 groups).
- **D5 UI.** Customer Groups list: Agent column + "Mixed agents only" filter. Group detail: Agent
  line in the header metadata. Customer list/detail and agent detail already show / edit what is
  needed.
- **D6 Contract.** `sales_agent_code` added to `FIELDS_ADDED["customers"]`; warning word already
  listed.

## 3. Tests (red first)

Backend `tests/test_customer_sales_agent_ingest.py`: set on linked row; fan-out to same-code
back-created row; other code and other company untouched; unknown code warns and leaves agent;
absent key and blank both untouched; dry run writes nothing; case/space-insensitive match; contract
lists the field. `tests/test_sales_agent_person_label.py`: derive cases; create fills; staff label
kept. `tests/test_customer_groups_agent.py`: agree by person, mixed, unassigned ignored, filter.
Frontend vitest: groups list shows Agent / Mixed and sends the filter; detail shows the agent.
