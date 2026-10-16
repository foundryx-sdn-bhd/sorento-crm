# PLAN: portal login linked to several sales agents sees all their customers (PORTAL-MULTI-AGENT)

Status: in build. Track: small fix (S; no migration, no schema change, no CRM screen change).
Security-reviewer joins: the diff changes portal data scope.

## Owner rule (16 Oct 2026)

If one portal login (contact) is linked to more than one sales agent, the portal shows the
customers of ALL those agents together, everywhere in the portal. Nothing silently dropped.

## Today (origin/main e19bc513fd)

- `sales_agents.contact_id` is not unique (non-unique index in
  `alembic/versions/ptag_0001_price_tag_request_foundation.py`; the only unique rule is one code
  per company, `app/models/sales_agent.py` `uq_sales_agents_company_sales_agent`).
- `app/services/sales/portal_agent.py` `agent_for_contact` picks ONE agent (lowest code) and logs
  "linked to N sales agents". Every portal caller scopes to that one id:
  - Conversations: `app/api/v1/public/portal_conversations.py` `_agent_id`,
    `app/services/portal_conversation_service.py` `_rows_query` (`== agent_id`).
  - Customer asks: `app/api/v1/public/portal_customer_asks.py` `_agent_id` (the stock ask scope
    `stock_ask_service._agent_scope` already takes a list).
  - Price tag debtor lookup: `price_tag_request_service.lookup_debtors_for_agent`.
  - Sales opportunity: `app/api/v1/public/portal_sales_opportunity.py` `_require_agent` (also
    403s when that one agent is inactive, even if another linked agent is active).
  - Form visibility: `portal_form_visibility_service.resolve_visible_form_types` hides the
    agent-only kinds when no agent is linked.
- Dev case (sorento_cagent_stack, read-only): contact "Jayson" +60166753328 is linked to
  JAYDEN I (customers 300-4001, 300-H070) and WT I (300-I057, 300-J017). Today the portal shows
  only JAYDEN I's two.

## Change

1. `portal_agent.agents_for_contact(db, contact_id) -> list[SalesAgent]`: every ACTIVE agent
   linked to the contact, by code then id. Inactive agents are left out (their customers do not
   show). `agent_for_contact` / `agent_for_user` stay for the CRM staff to-do only (rule 3: no
   CRM screen change).
2. `price_tag_request_service.sales_agent_for_contact` becomes `sales_agents_for_contact`
   (same seam, returns the list). Empty list = 403 `NOT_A_SALES_AGENT` (unchanged code).
3. Every portal caller filters by the list:
   - Conversations: `_owning_agent_column().in_(ids)`.
   - Customer asks: pass the id list to `stock_ask_service` (list, todo, conversation, PATCH).
   - Price tag debtors: customers `sales_agent_id IN ids`; order debtors `Order.agent IN codes`.
   - Sales opportunity: list / get / customer options over all ids; my-targets is every agent's
     targets together; create stamps the agent that owns the chosen customer (a prospect, with
     no customer, goes to the first agent by code); an edit that moves the opportunity to
     another linked agent's customer moves the opportunity to that agent. Works when at least
     one linked agent is active. Company narrowing: the union of the agents' companies, only
     when every agent has one and the existing scope allows it (never widens).
   - Form visibility: the agent-only kinds need at least one ACTIVE linked agent.
4. No schema change, no FE change (the portal shows no "my agent" panel; opportunity rows
   already carry their own agent).

## Tests (red first: `tests/test_portal_multi_agent.py`, plus the pinned old-rule tests)

UAC: `portal-multi-agent-16oct-acceptance-criteria.md`.

## Hand test

Script at `laneboard/scripts/<PR>.md` on the dev case above.
