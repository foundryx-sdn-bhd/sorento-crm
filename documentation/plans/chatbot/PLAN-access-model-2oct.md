# PLAN - Chatbot access model: roles -> domains -> fields, one registry

Status: planned, mock v8 FINAL (owner ok 2 Oct 2026; v5-v8 added stamps, tier on role, region, simplified layout); red tests S1-S5 + Reports posted on #1434, S6 + S7 red tests in progress; build after #1405 and #1429 merge. Track: L / standard (migration, RBAC,
prompt). Lane ACCESS-MODEL, branch `crew/access-model`, one PR. Depends on PR #1405 (prompt
variables, open) and PR #1429 (per-audience trim, docs only, open); slices S1 to S6 do not touch their
files, S7 rebases onto them once merged.

Inputs: behaviour card `CARD-access-model-2oct.md` (owner answers Q1 to Q5 = (a), Q2 confirmed 2 Oct), UAC
`access-model-2oct-acceptance-criteria.md`, mock v5
`documentation/mockups/ACCESS-MODEL/index.html`. Paths under `sorento_crm_backend/app/` unless shown.

## Owner additions (2 Oct, later): multi-role, stamps, region

**A. More than one role per contact.** The model already allows it (`contact_chatbot_roles` PK is the
pair; the Access tab shows role chips). The single-valued picker the owner means is the chatbot
**Tier** select (Dealer / Office / End user), `ContactChatbotSection.tsx:90-94,199-206`, stored as
`respond_contacts.chatbot_profile.tier` (one string, `turn/state.py:166`). Contact access types are
already multi (checkboxes, `ContactEditDialog.tsx:280-300`; on the prod copy 49 contacts hold more than one, 25 hold all 7).
Tier is read in three places: staff behaviour (`is_staff_profile`, tier == office: no bot escalation
offers, `turn/state.py:192-203`), the default promotion tier (`turn/narrow.py:411,426`), and the
profile line the parser sees (`turn/memory.py:447-448`). Owner ruled 2 Oct (Q1 a, Q2 a):
- each role carries an `audience_tier` (dealer / office / end_user / none); a contact's tiers = the
  set over its roles; the Tier picker on the Chatbot tab is removed (one place to set it);
- conflict rule = UNION, most permissive: grants are the union of roles; `sees_all_customers` if any
  role has it; staff behaviour if any role is office; promotions answer for every tier held (the
  narrower already accepts a list, `turn/narrow.py:414-424`). Restrictions for one person are a
  contact remove-override, which beats every role (AC-AM-5);
- unaffected by roles: stock visibility mode (Q5, per contact) and the dealer salesman referral that
  reads it (`turn/state.py:184-190`).

**B. Per-contact stamp switches.** Two stamps today:
- incoming: " - has incoming" / " - no incoming" on a product roster line plus "None of these have
  incoming stock right now." (`lanes/business/pickers.py:82-118`; carried as data
  `incoming_by_code`; turn roster `turn_runtime.py:2304-2305`);
- product attachment: per-uuid has/no attachment stamp (`lanes/business/answer.py:4160-4180`,
  `miss_suggest.py:573`).
Owner ruled 2 Oct (Q3 a, Q4 a; context: these switches are mainly for dealers, reveal less to them): two `field` rows in the tree, `stamp.incoming` under Incoming stock
and `stamp.product_attachment` under Product attachments, ticked on every seeded role so today's
behaviour holds; the per-contact switch is the existing field override (untick on the Access tab).
Hidden stamp = the line prints bare and the "None of these have incoming" sentence is dropped; the
roster itself is unchanged. Mock v5: the two rows, Tier on the role (list column, role dialog, chip), Tier
select removed from the contact Chatbot tab. Schema: `chatbot_roles.audience_tier` (dealer / office /
end_user / NULL); `chatbot_profile.tier` stops being read (migration copies nothing: tier is NULL for all
100 contacts on the prod copy, query in section 4); `EffectiveAccess.tiers: frozenset[str]` feeds
`is_staff_profile` (office in tiers), the promotion default (tiers list) and the parser profile line.

**C. Region: MOVED OUT (owner 5 Oct).** The contact Regions field (`respond_contacts.regions` and its
Access tab control) is its own lane, CONTACT-REGION. This lane builds no region column, no
`EffectiveAccess.regions`, no region control and no `eta_policy.contact_regions` repoint; it rebases onto
CONTACT-REGION when that merges and leaves the Region card's slot (step 3 of mock v8) to it. Trigger to
fold region into `effective_access`: a second reader outside incoming, after both lanes are merged.

## Key design choice: keep every enforcement seam, change only what fills it

Today four seams already enforce access, keyed on strings:
- domain gate `turn/apply.py:2199-2203` reads `Profile.grants` (dormant: `grants=None`, `turn_runtime.py:475`);
- field/ask gates read `ctx.access.attributes` (reveal keys): `output_structurer` `lanes/business/fetch.py:2604-2716`,
  `lanes/business/__init__.py:62,68,99,1394,1459-1470,1522,1601,1671`, `answer.py:1155,1161`, `engine.py:3985-3988`;
- incoming REST columns `field_access.py:365-589` via `agent_field_access`;
- turn refusal `engine.py:4259-4264` on `access.allowed` (agent check).

The new tree fills the SAME inputs: `effective_access()` returns `domains` (-> `Profile.grants`, gate
switched to compare `row.name`) and `attributes` (field keys, which KEEP today's reveal-key strings:
`inventory.sellable`, `purchase_orders.placed`, `purchase_orders.supplier`, `purchase_orders.cost`,
`sales_orders.outstanding`, `sales_orders.sales_report`, `scm.low_stock_report`,
`incoming_stock.<field>`). No grant constant moves; the leak surface is the one function.

**Reports section (owner 2 Oct, N1).** Report-type grants sit in their own REPORTS section of the
tree, not under a domain: Low stock report (`scm.low_stock_report`), Outstanding SO report
(`sales_orders.outstanding`), Sales report (`sales` domain from #1405: report, analysis, top selling).
Reports is a grouping in the UI and the grant model only; the parser domain list is unchanged:
- `chatbot_domain_fields.kind = 'report'` for the two ask grants; they keep their OWNING parser domain
  (`inventory`, `order`) for enforcement, so a report answers only when its owning domain is also
  granted (UI: "Needs Stock" / "Needs Orders / DO"). Granting the report never grants the domain
  (that would open every stock/order ask).
- `chatbot_domains.access_section` (NULL, or `reports`): the `sales` row is `reports`, so the whole
  domain lists under Reports; its grant is the domain grant as for any other domain.
- No new `chatbot_domains` row, no routing change. A future report = a `report` field row (or a domain
  row with `access_section = 'reports'`).

## Schema (one Alembic revision, additive; down_revision re-parented at PR time)

| Table | Columns | Notes |
|---|---|---|
| `chatbot_domain_fields` | id, domain_name FK `chatbot_domains.name` ON UPDATE CASCADE, key (unique), label, kind (`field`/`report`), prompt_tag NULL, sort_order | seeded: 7 reveal keys + 23 incoming fields (`field_access.GATED_FIELDS`) + `spo_allocation.spo_number`, `purchase_orders.po_number` (new, enforced in S5) |
| `chatbot_roles` | id, code (unique, slug of name at create, immutable), name, description, sees_all_customers bool, sort_order, created_by/updated_by | `__company_shared__ = True` (LESSONS: seeded reference table) |
| `chatbot_role_domains` | role_id FK CASCADE, domain_name FK | PK (role_id, domain_name) |
| `chatbot_role_fields` | role_id FK CASCADE, field_key FK `chatbot_domain_fields.key` | PK (role_id, field_key) |
| `contact_chatbot_roles` | contact_id FK `respond_contacts.id` CASCADE, role_id FK RESTRICT | PK pair; RESTRICT backs AC-AM-3 |
| `contact_access_overrides` | id, contact_id FK CASCADE, domain_name, field_key NULL, granted bool, updated_by | partial uniques: (contact, domain) where field NULL; (contact, field) where field NOT NULL (pattern `agent_field_access`, `models/access.py:519-534`) |
| `chatbot_domains.access_section` | Text NULL (`reports`) | `sales` row seeded `reports` |
| `chatbot_domains.access_group`, `access_label`, `access_description` | Text NULL | mock v8 plain-English section, switch label and one-line description per domain (the parser's `label` stays untouched); seeded from mock v8; registry falls back to `label` when NULL |
| `chatbot_domains.escalation_agent_code` | Text NULL FK `access_agents.code` ON UPDATE CASCADE ON DELETE SET NULL | seeded per card 3b |

Data step (same revision): `reveal_key` NULL on `inventory` and `order` rows (they are fields now);
create the 5 roles + ticks (card section 2); assign contacts by the mapping rules in
`access-model-2oct-mapping.sql` (pure SQL INSERT ... SELECT, no ORM); write overrides for the four
exception contacts generically (computed from the diff between role ticks and today's keys, not by
name). `contact_field_reveals`, `agent_field_access`, `contact_agent_access` are left untouched (rollback
path) and stop being read by the chat path; dropping them is a later lane.

## Slices (commits on the lane, red tests first per owner rule 2 Oct)

- **S1 Schema + seed + mapping.** Models in `app/models/chatbot_access.py`, migration, loss-check test.
  Tests: `tests/test_chatbot_access_migration.py` (seed a chain of contacts mirroring the 9 mapping
  shapes; assert roles/overrides; AC-AM-19/20; new domain row ticks nobody AC-AM-16).
- **S2 `effective_access` + turn wiring.** `app/services/chatbot/access_tree.py::effective_access(db, *,
  contact_respond_id, space_id) -> EffectiveAccess(domains, attributes, sees_all_customers, roles)`;
  union of roles, overrides applied, intersection across duplicate rows (AC-AM-8), fail closed on read
  error. `head/access.check_access` returns `allowed=True` iff the contact resolves (agent check dropped
  from the chat turn, AC-AM-11), `attributes` from the tree. `load_profile` sets `grants=domains`;
  `apply.py:2199` compares `row.name`. Access read moved before `parser.resolve_config`
  (`engine.py:3475` vs `3738`, AC-AM-13). Customer scope: `contact_customer_scope.is_office_staff`
  reads `sees_all_customers`; unlinked + not-all -> refused for order/outstanding/sales (AC-AM-9).
  Tests: `tests/test_chatbot_access_tree.py`, `tests/test_chatbot_access_turn_gate.py`,
  `tests/test_contact_customer_scope_role_flag.py`.
- **S3 Escalation agent from the domain.** `_next_assignee_body` / `_sla_body`
  (`lanes/escalation.py:1543-1580`) take `agent_code` from the domain row's `escalation_agent_code`,
  falling back to the parser value only when the row has none. Tests:
  `tests/test_chatbot_escalation_domain_agent.py` (AC-AM-17/18).
- **S4 Admin API.** `app/api/v1/system/chatbot_roles.py` (mounted with the chatbot module guard like
  `chatbot_field_reveals.py`): roles CRUD (`reference_data.manage`; delete 409 when held), role ticks
  GET/PUT (changed rows), contact roles + overrides GET/PUT (`contacts.edit`), registry GET (domains +
  fields + escalation agent/team + tier-1 team names + `group` / `access_label` / `access_description`), and GET
  `/roles/{id}/contacts` -> `[{id, name}]` for the role page (mock v8). Audit via `__audit_track__`. Tests:
  `tests/test_chatbot_roles_api.py` incl. RBAC denials and `response_model` field assertions.
- **S5 Registry derivations.** `FIELD_REVEAL_KEYS` and `GATED_FIELDS` read `chatbot_domain_fields`;
  `field_access.allowed_fields_for` (incoming REST) reads the contact's tree instead of
  `agent_field_access`; `SUGGESTED_AGENTS` from active `access_agents`; `CHATBOT_TOOL_DOMAINS` from
  `chatbot_domains.tools`. `default_policy()` call sites move to the loaded `Policy` where it is one
  call away (sites 2, 3, 6, 7, 10, 11 in the research map: `answer.py:2308,3108`, `fetch.py:206,297`,
  `resolve_gate.py:197`, `tier_gate.py:38`); sites that stay on the seed: `lane_vocabulary.py:59`
  (blank-install default), `fetch.py:1328` (security allow-list), and the pure helpers
  `predicate.py:148,390`, `fetch.py:1771` (no session; named trigger: the next change to switch words
  or base property words). SPO number / PO number field gates added to the presenters' restricted
  fields. Tests: `tests/test_chatbot_registry_derived.py`, extend `tests/test_field_access*.py`.
- **S6 Frontend (mock v8).** Chatbot Roles list `app/(protected)/user-management/chatbot-roles/page.tsx`, role page
  `[id]/page.tsx`, shared `components/chatbot-access/AccessSwitches.tsx` (grouped switches, one-level details, stamp previews) + `AccessSummary.tsx`, contact Access tab replaces
  `ContactAccessAgentsTable` + `ContactFieldRevealsSection` with roles + tree + escalation card; service
  `services/chatbotAccessService.ts`, hooks `hooks/useChatbotAccess.ts`; menu entry
  `config/menu.config.tsx` Access group. Vitest for tree tick logic (indeterminate, override badges,
  changed-rows save) and services. agent-browser 1280/375 on the crew copy.
- **S7 Prompt trim (after #1405 and #1429 merge; rebase first).** Generalise #1429's planned
  `PROMPT_GATES` to `chatbot_domain_fields.prompt_tag` + domain name tags: `{{#only <domain>}}` /
  `{{#only <field key>}}`; render per contact from `EffectiveAccess`; migration publishes the tagged
  prod text and proves full-access render = current production byte-for-byte. Leak matrix
  `tests/test_chatbot_access_leak_matrix.py` (role preset x domain x field: prompt absent AND refused,
  AC-AM-12/14).

## Execution (LEAD pattern, owner 3 Oct)

The lane lead writes no product code or tests. Red tests: TESTER subagents (done for S1 to S7 + owner
additions + v8; proofs on #1434). Build: one CODER subagent per slice in order S1..S7, told it may not
weaken or edit a tester assertion without the lead's sign-off; each run capped at 60 min with a progress
note every 20; then a separate REVIEWER subagent and a SECURITY-REVIEWER subagent (RBAC/permission
boundary) in parallel; conclusions at most 15 lines each. Kill-proof after green per slice group.

## Build split while #1429 is open (crew go, 5 Oct)

#1429 (head fbc88b8f) changes `head/access.py`, `head/parser.py`, `engine.py`, `lanes/business/answer.py`,
`lanes/business/__init__.py`, `contact_field_reveal_service.py`, `api/v1/system/chatbot_field_reveals.py`,
`schemas/chatbot_field_reveal.py`, FE `ContactFieldRevealsSection.tsx` + `contactFieldRevealService.ts`,
and adds migration `ppa_0001`. Build NOW only what touches none of those:
- **Now:** S1 (models, migration chained after `creg_0001_contact_regions`, `access_seed.py`, plus the
  pure `access_tree.effective_access` module that the loss report and the contact access API read; no
  turn wiring); S3 (`turn/policy.py`, `turn_runtime.lane_parse_output`, `lanes/escalation.py`); S4 (new
  `api/v1/system/chatbot_roles.py` + mount); S6 (frontend: new pages/components; the contact Access tab
  stops rendering `ContactFieldRevealsSection` but the component file is left alone).
- **Held until #1429 merges, then rebase:** S2 turn wiring (`check_access`, `load_profile` grants,
  `apply.py` gate, access read before the parser render, customer scope flag, tier/staff, stamp
  switches incl. `build_suggest_offer` in `answer.py`); S5 (registry derivations incl.
  `contact_field_reveal_service`); S7 (prompt tags, generalising #1429's `PROMPT_GATES`). The S1
  migration is re-parented onto `ppa_0001` if #1429 merges first.

## Migration on a dev copy + hand test

Idempotent SQL for crew at `crew/state/migrations/ACCESS-MODEL.sql` (CREATE ... IF NOT EXISTS, INSERT
... ON CONFLICT DO NOTHING; the `reveal_key` UPDATE on two rows is held for the owner as destructive).
Before the hand test: apply on a private clone of the prod copy, run mapping SQL part A/B + the loss
check, record counts in the PR. Hand-test script `laneboard/scripts/<PR>.md`: owner opens CONTACT O Access tab, unticks Last purchase cost, asks the bot "last cost <product>" -> refused, prompt
preview lacks LAST PURCHASE COST; creates role "Project sales", ticks Orders, assigns a test contact.

## Review

reviewer + security-reviewer (auth/RBAC/permission boundary) in parallel after S6, browser pass
1280/375. Security notes to carry: agent create route needs only sign-in today
(`api/v1/user_management/access_agents.py:91-95`, `get_current_user`), out of scope but reported;
`evaluate_agent` lacks the NULL-workspace fallback the reveal path has (`mcp_access_service.py:59-80`
vs `field_access.py:278-328`) - `effective_access` uses one resolver for both.

## Notes to relay

- N1 resolved (owner 2 Oct): Reports section, see above. N2 resolved: Q2 yes.
- N3: crew shared dev DB `sorento_ai_automation_0921` (main checkout `.env`) does not exist locally.
