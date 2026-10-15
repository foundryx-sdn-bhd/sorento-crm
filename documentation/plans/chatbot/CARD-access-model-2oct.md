# Behaviour card - chatbot ACCESS MODEL: roles -> domains -> fields, one registry

**Owner answers (2 Oct 2026):** Q1 start with the 5 roles listed, but roles are DYNAMIC: the owner
creates, renames and deletes roles and ticks domains/fields per role in the UI (Chatbot Roles admin,
mock v3 tabs "Chatbot roles" + "Role: Purchasing" in `documentation/mockups/ACCESS-MODEL/index.html`); no role list in code, the 5 are
seed rows only. Q2 not answered yet: build on rec (a), crew confirms. Q3 (a) "sees all customers" is a
flag on the role. Q4 (a) agents kept for escalation / routing / SLA / n8n only, not the chat gate.
Q5 (a) stock visibility mode stays as is, shown inside the Stock domain. Mock v2 escalation linkage
seen, no change. Q2 (2 Oct, later): yes, CONTACT K and CONTACT E -> Sales office, CONTACT M gains Ideas. N1 (2 Oct):
neither nested nor top-level domains: a separate REPORTS section holds Low stock report, Outstanding SO
report and every other report-type grant (Sales report), parser domain list unchanged (mock v4).

Lane ACCESS-MODEL, size L, 2 Oct 2026. Card before any code. Paths under `sorento_crm_backend/app/`
unless shown otherwise. Data facts are from the local prod copy `sorento_ai_automation_0925`
(read-only queries; 100 contacts). Crew note: the crew shared dev DB named in the main checkout's
`.env` (`sorento_ai_automation_0921`) does not exist on this Postgres, so the mapping SQL
(`access-model-2oct-mapping.sql`) must be run by crew on whichever copy is the hand-test base.
The owner picture (claude.ai artifact C3YTBDqNVZVejc5Q8xSorR) is not shared with this account; the
card is built from the brief and the code.

## 1. What a contact can reach today (four switches, read in four places)

| Piece | What it gates | Read at turn time |
|---|---|---|
| `contact_agent_access` (agent per contact, `valid_from/valid_to`) | whole turn: parser's `suggested_agent` (default `general_enquiries`, `contracts.py:188`) must be held, else canned "no access" | `head/access.py:104-136` -> `mcp_access_service.evaluate_agent:41-99`; routed `engine.py:4259-4264` |
| `contact_field_reveals` (7 keys, `contact_field_reveal_service.py:41-61`) | 4 keys hide a field (`inventory.sellable`, `purchase_orders.placed`, `purchase_orders.supplier`, part of low stock workbook); 4 gate a whole ask (`purchase_orders.cost`, `sales_orders.outstanding`, `sales_orders.sales_report`, `scm.low_stock_report`) | `ctx.access.attributes`; `output_structurer` `lanes/business/fetch.py:2604-2716`; grant checks `lanes/business/__init__.py:62,68,99,1459-1470`; `answer.py:1155,1161`; `engine.py:3985-3988` |
| `agent_field_access` (23 incoming-stock fields owned by agent `incoming_stock_enquiries`) | columns of the incoming-stock REST payload only | `field_access.py:56-117`, applied in `api/v1/incoming_stock.py:126,447` |
| `contact_access_types` (dealer / office / end user ...) | brand tier gate, promotion + attachment audience, "office staff sees all customers" | `lanes/business/services.py:120-135`, `contact_customer_scope.py:21-51`, `marketing/promotions.py:145` |
| `chatbot_domains.reveal_key` + `Profile.grants` | domain gate, **dormant**: `grants=None` always | `turn_runtime.py:431-436,475`; consumer `turn/apply.py:2199-2203` |

Measured problems:
- **Every agent grant lapses on 31 Dec 2026.** All 472 allowed rows have `valid_to` 2026-12-30/31
  (`assign_default_agents_to_contact`, `contact_service.py:184-186`); `evaluate_agent` requires
  `valid_to > now` (`mcp_access_service.py:89`). From 1 Jan 2027 every contact is refused.
- No map from agent to domain exists in code; which agent a turn needs is whatever the parser says.
- The parser prompt is one text for everyone (`head/parser.py:573-610`), rendered at `engine.py:3475`
  BEFORE grants are read at `engine.py:3738`.
- `default_policy()` code seed: 11 call sites (brief said 18; measured `grep`), listed in the plan.

## 2. New model (one tree, read by enforcement AND prompt trim)

```
Contact
 |- Roles (one or more)          contact_chatbot_roles
 |    Role -> domains ticked     chatbot_role_domains
 |         -> fields ticked      chatbot_role_fields
 |- Domain overrides (+ / -)     contact_domain_overrides
 |- Field overrides  (+ / -)     contact_field_overrides   (replaces contact_field_reveals + agent_field_access contact rows)
 |- Customer scope               respond_contact_customers (unchanged); role.sees_all_customers
Registry: chatbot_domains (+ fields list per domain: chatbot_domain_fields), prompt blocks tagged {{#only domain[.field]}}
```

Effective access = union of role grants, then contact overrides (minus wins over plus for the same
row). **No row = no access** (new contact gets nothing). No validity window.

### Domain tree with fields (default = what a NEW role starts with: nothing ticked)

| Domain (`chatbot_domains.name`) | Label | Sensitive fields (tickable) | Today's key it absorbs |
|---|---|---|---|
| `master_products` | Product | - | - |
| `product_attachment` | Product attachments | - | - |
| `resource_attachment` | Resource attachments | - | - |
| `promotion` | Promotions | - | - |
| `forms` | Forms | - | - |
| `portal_link` | Portal link | - | - |
| `inventory` | Stock | `sellable` (outstanding SO on stock), `on_order` (PO placed qty on stock) | `inventory.sellable`, `purchase_orders.placed` (stock half) |
| `low_stock` (new row, split from inventory tool `crm_low_stock_report`) | Low stock report | - (whole workbook) | `scm.low_stock_report` |
| `order` | Orders / DO | - | - |
| `outstanding` (new row, split from order tool `crm_outstanding_report`) | Outstanding SO report | - | `sales_orders.outstanding` |
| `sales` (row added by PR #1405) | Sales report / analysis / top selling | - | `sales_orders.sales_report` |
| `incoming` | Incoming stock | the 23 `GATED_FIELDS` (container no, forwarders, consignee, permit, 11 dates...) | `agent_field_access` |
| `spo_allocation` | Last in (SPO) | `spo_number` (gap G2 in #1429 plan) | - |
| `purchase_order` | Purchase orders | `supplier`, `po_number` | `purchase_orders.placed` (PO half), `purchase_orders.supplier` |
| `purchase_cost` | Last purchase cost | `supplier` | `purchase_orders.cost` |
| `ideate` | Ideas | - | agent `ideation` |
| `goods_receive` | (unsupported, hidden) | - | - |

Entity kinds (product, customer, order, brand...) stay shared vocabulary in every prompt.

### Proposed roles (presets; owner ticks)

| Role | sees all customers | Domains | Fields |
|---|---|---|---|
| Dealer | no (linked customers only) | product, both attachments, promotion, forms, portal_link, stock, order, incoming | incoming: ETA, remaining qty only |
| Sales office | yes | Dealer + spo_allocation, purchase_order | stock: sellable, on_order; incoming: ETA, container no, remaining qty (today's `DEFAULT_ALLOWED`, `field_access.py:117`) |
| Purchasing | yes | Sales office + purchase_cost | + PO supplier, cost supplier |
| Warehouse | yes | stock, incoming, spo_allocation, low_stock, product | all incoming fields |
| Management | yes | every supported domain incl. outstanding, sales, low_stock, ideate | every field |

## 3. How today's tables map

| Today | New |
|---|---|
| `access_agents` as a chatbot gate | gone from the chat turn; the gate is the domain tree. Table STAYS as the ESCALATION path (section 3b: each domain names its agent + team set), team/SLA routing (`agent_teams`, `sla_service.py:2613`, `cs_routing_service.py:81`) and n8n preflight `external/access_agent.py:25-68` (Q4) |
| `contact_agent_access` | not read by chat; migration converts holders to roles (section 4) |
| `contact_field_reveals` | keys become domain grants or field ticks per the table above; per-contact leftovers become overrides; table retired after one release |
| `agent_field_access` (agent-wide default) | `incoming` field ticks on each role; 3 contact override sets -> `contact_field_overrides` |
| `contact_access_types` | unchanged: brand tier, promotion/attachment audience. "Office sees all customers" moves to `role.sees_all_customers` (Q3) |
| `respond_contacts.chatbot_stock_allowed` etc. | unchanged switches (stock denial, escalation, packing list) |
| `SUGGESTED_AGENTS`, `ENTITY_HINTS`, `FIELD_REVEAL_KEYS`, `CHATBOT_TOOL_DOMAINS` | derived from the tables; `default_policy()` readers take the loaded policy, except two that must stay on the seed (`lane_vocabulary.py:59` blank-install default, `fetch.py:1328` security allow-list) |

## 3b. Agents stay as the ESCALATION path (owner, Lavish v1: "we need the linkage to agent mostly for escalation path")

Today a hand-off is routed by two values (`lanes/escalation.py:1543-1553` `_next_assignee_body`):
`agent_code` = the parser's `routing.suggested_agent` (a guess, default `general_enquiries`) and
`team_code` = the domain's `chatbot_domains.escalation_team_code`. `/external/next-assignee` then picks
the tier-1 team of that (agent, team set) in `agent_teams` and round-robins (`escalation_services.py:72-89`).

New: agents stop gating ACCESS, and keep (and firm up) ROUTING. Each domain row names its escalation
agent next to its team set (new column `chatbot_domains.escalation_agent_code`, FK `access_agents.code`),
so the hand-off no longer depends on the parser's agent guess. Proposed per domain (agent_teams rows on
the prod copy; tier-1 team in brackets):

| Domain | Team set (today) | Escalation agent (new column) | Tier 1 |
|---|---|---|---|
| Product | purchasing | general_enquiries | Purchasing - Product, Stock Inquiry |
| Product attachments | marketing_product | general_enquiries | Marketing - Product, Marketing Executive |
| Promotions | marketing_promotion | general_enquiries | Marketing - Promotion Sorento, Marketing Executive |
| Forms | marketing_form | marketing_form | Marketing - Forms |
| Stock | warehouse | general_enquiries | Warehouse Executive |
| Orders / DO | customer_service | order_enquiries | DO Customer Service, Customer Service Executive |
| Incoming stock | purchasing | incoming_stock_enquiries | Purchasing - Incoming, Purchasing - Packing List |
| Purchase orders, Last purchase cost | purchasing | general_enquiries | Purchasing - Product, Stock Inquiry |
| Last in (SPO), Resource attachments, Portal link, Ideas | none today | none (no hand-off offered, as today) | - |
| Outstanding SO, Sales report (new rows) | customer_service (proposed) | order_enquiries | DO Customer Service, Customer Service Executive |
| Low stock report (new row) | warehouse (proposed) | general_enquiries | Warehouse Executive |

Contact view: the Access tab lists, for each GRANTED domain, the team that takes its hand-off, plus
today's switches that change it: "Can escalate to a person" (`respond_contacts.escalation_allowed`,
#1406), dealer referral to their salesman (`stock_availability_only`, `turn/state.py:184-190`), and the
contact's CS routing rules (`respond_contact_cs_routing`, 31 rows: purchase_request 17,
sponsorship_form 14). Agents and team sets are edited where they are today (Access agents admin,
`UM/access-agents/components/AccessAgentDetail.tsx:323-458`); the domain row only points at one.
`contact_agent_access` (who HOLDS an agent) is not needed for routing: `next-assignee` reads
`agent_teams`, not the contact's grants (UNVERIFIED for the n8n preflight path, checked in the plan).

## 4. No internal user loses access (100 contacts on the prod copy)

Today's effective sets (query in `access-model-2oct-mapping.sql`, part A):

| Today | Contacts | New |
|---|---|---|
| 5 standard agents (`general_enquiries`, `incoming_stock_enquiries`, `lead_time_enquiries`, `marketing_form`, `order_enquiries`) + `sellable,placed` | 81 | Sales office |
| same + `conversation_analysis` | 1 (CONTACT L) | Sales office |
| only `general_enquiries` + `sellable,placed` | 2 (CONTACT K, CONTACT E) | Sales office (Q2) |
| 5 agents + `sellable,placed,outstanding` | 1 (CONTACT F) | Sales office + override `+outstanding` |
| 5 agents, no reveal keys | 2 (CONTACT Q, CONTACT A) | Sales office + overrides `-stock.sellable -stock.on_order -purchase_order` (exact) |
| 5 agents + `sellable,cost,placed,supplier` (+/- conversation_analysis) | 5 (CONTACT P, CONTACT O, CONTACT D, CONTACT G, CONTACT C) | Purchasing; CONTACT O also `+incoming` all 20 overridden fields (today's `agent_field_access` contact rows) |
| all 7 keys | 2 (CONTACT M, CONTACT H) | Management (all incoming fields already in the role; CONTACT M gains `ideate`, Q2) |
| reveal keys but NO agent (refused every turn today) | 3 (CONTACT N, CONTACT J, CONTACT I) | no role (still refused) |
| nothing | 3 | no role |

Totals from part B of the SQL on the copy: Sales office 87, Purchasing 5, Management 2, none 6 = 100.

Part A of the SQL lists every contact with its proposed role and overrides (crew runs it read-only on the
hand-test copy). The migration lane adds a loss check: today's reachable keys vs the new tree per
contact, zero "lost" rows before the migration is accepted.

## 5. Prompt follows the same tree

- Every parser block gets a tag: `{{#only purchase_cost}}...{{/only}}`, `{{#only incoming.consignee}}`.
  Untagged blocks render for everyone. A contact's prompt = blocks of its granted domains/fields
  (generalises #1429 `PROMPT_GATES`; one table, not a second copy).
- Grants are read BEFORE the parser render (move the access read ahead of `engine.py:3475`).
- Refusal and trim read one function `effective_access(contact)`; a test asserts that every domain
  absent from the prompt is also refused at the fetch seam (leak matrix: role x domain x field).

## 6. Edge cases

1. Domain removed by override -> its prompt blocks vanish AND an explicit ask gets the canned no-access reply (`lanes/canned.py:49`).
2. Field unticked inside a granted domain -> answer drops the field (`output_structurer`), prompt loses that field's lines; the rest of the answer stands.
3. Unknown contact / no role -> minimal prompt (untagged blocks) and every domain ask refused.
4. Two contact rows share one respond.io id -> intersection of both trees (fail closed, like `turn_runtime.py:450-457`).
5. New domain added later (e.g. `sales` from #1405) -> ticked on no role; the migration that adds it ticks Management only.
6. Dealer with linked customers -> order/outstanding answers scoped to them; contact with no links and no `sees_all_customers` role -> order asks refused (closes #1429 gap G1). Today unlinked = unscoped (`services.py:554-583`).
7. Role edited -> applies on the next turn for every holder (no cache beyond the turn).
8. 31 Dec lapse disappears: roles have no expiry.
9. Escalation: agent + team set come from the DOMAIN row (section 3b), not the parser's guess; a domain with no team offers no hand-off (as today). n8n preflight and SLA keep agents (Q4).
10. Stock visibility (detailed / compact / availability, warehouses) stays its own setting shown inside the Stock domain (Q5).

## 7. Questions (owner)

Q1. Role list and ticks as in section 2 (Dealer, Sales office, Purchasing, Warehouse, Management)?
(a) yes as listed, (b) edit. **Recommend (a)**: covers all 100 contacts with 4 contacts needing overrides; Warehouse and
Dealer have no holders yet but are the dealer-safety presets.

Q2. CONTACT K and CONTACT E hold only `general_enquiries`; CONTACT M lacks `ideation`. (a) Sales office for CONTACT K/CONTACT E,
Management incl. ideate for CONTACT M (small gain), (b) exact-equivalent overrides. **Recommend (a)**: which
asks `general_enquiries` alone allowed depended on the parser's agent guess (no agent->domain map,
`contracts.py:172-188`), so "exact" is not computable.

Q3. Who sees all customers? (a) a flag on the role (Sales office / Purchasing / Warehouse / Management),
(b) keep today's rule: an active "Sorento/Cabana/Mocha Office" access type (`contact_customer_scope.py:21`).
**Recommend (a)**: one tree; 45 contacts carry an office type today, 92 would be internal roles; with 0
customer links on the copy nobody changes scope now.

Q4. Agents after the cut: (a) keep `access_agents` + `agent_teams` as the escalation path, each domain row
names its escalation agent (section 3b table), stop using agents as the chat ACCESS gate; (b) keep today's
parser-guessed agent for escalation; (c) retire agents. **Recommend (a)**: owner wants agents for the
escalation path, and a fixed domain->agent link makes the same ask always reach the same team.

Q5. "Exact quantities" for dealers: (a) keep stock visibility mode (availability / compact / detailed)
as today's separate setting, surfaced inside the Stock domain row; (b) turn it into field ticks.
**Recommend (a)**: it also carries warehouse lists (`stock_visibility_policies`, 36 rows) that a tick
cannot express.
