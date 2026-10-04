# UAC - Customer sales agent from AutoCount Debtor (CUSTOMER-SALES-AGENT)

Plan: `PLAN-customer-sales-agent-4oct.md`. Card: `CARD-customer-sales-agent-4oct.md`.

- **AC-1** A customers push with `sales_agent_code` naming an existing agent (any case/spacing)
  sets that agent on the ledger the record links to.
- **AC-2** The same agent is set on every other customer row of the same company with the same
  code (DO back-created rows), and on no row with another code or of another company.
- **AC-3** An unknown code leaves the agent untouched, the record still ingests, and the record
  carries warning `agent_unresolved`.
- **AC-4** A push without the key leaves the agent untouched; a blank value leaves it unchanged too.
- **AC-5** A dry run writes no agent anywhere.
- **AC-6** `GET /external/contract` lists `sales_agent_code` under customers.
- **AC-7** `person_label` empty gets the code minus its roman-numeral level (`AGENT-A III` -> `AGENT-A`,
  `AGENT-C - I` -> `AGENT-C`, `ABC` -> `ABC`), on backfill and on agent create; a label staff typed is
  never changed.
- **AC-8** A customer group whose assigned ledgers share one person shows that person; two or more
  persons shows "Mixed"; no assigned ledger shows blank. Unassigned ledgers never make it mixed.
- **AC-9** The Customer Groups list has an Agent column and a "Mixed agents only" filter that lists
  exactly the mixed groups; the group detail shows the same Agent value.
- **AC-10** notify-salesman and the chatbot keep reading `customers.sales_agent_id` (no change).
