# UAC - Chatbot access model: roles -> domains -> fields, one registry

Plan: `PLAN-access-model-2oct.md`. Card: `CARD-access-model-2oct.md` (owner answers 2 Oct 2026).
Mock: `documentation/mockups/ACCESS-MODEL/index.html` (v3). IDs AC-AM-n.

## Roles (dynamic, owner-configurable)

- AC-AM-1 Roles are rows (`chatbot_roles`). An admin with `user_management.reference_data.manage` (the slug that guards contact access type writes today) can
  create, rename, describe and delete a role, and set "Sees all customers", from Chatbot Roles
  (User management > Access). No role code or name appears in application code; the 5 presets
  (Dealer, Sales office, Purchasing, Warehouse, Management) exist only as migration seed rows.
- AC-AM-2 On a role page the admin ticks domains and, inside a domain, fields; Save (n) writes only the
  changed rows; the page re-reads and shows the saved ticks. View and edit are the same layout.
- AC-AM-3 Deleting a role is a 10 s deferred action with Cancel, no confirm dialog. A role held by any
  contact is refused (409, message names the number of contacts); a role held by none is hard-deleted
  with its ticks.
- AC-AM-4 Adding/removing a contact on the role page and on the contact Access tab write the same row; contact role/override writes need `user_management.contacts.edit` (as field reveals today, `system/chatbot_field_reveals.py:48,86`); reads need `user_management.access_agents.view`.

- AC-AM-4b The tree has two sections, Domains and Reports. Reports lists every report-type grant: Low
  stock report, Outstanding SO report, Sales report. A report answers only when its owning domain is
  granted too (Low stock report needs Stock, Outstanding SO report needs Orders / DO); granting a
  report never grants its owning domain. The parser's domain list is unchanged by this lane.

## Contact access

- AC-AM-5 A contact's access = union of its roles' ticks, then its own overrides (domain or field,
  add or remove). For the same domain/field a contact "remove" beats any role "add".
- AC-AM-6 Deny by default: a contact with no role and no add-override reaches no domain; every domain
  ask gets the canned no-access reply (`lanes/canned.py`), and its prompt holds no domain-tagged block.
- AC-AM-7 (mock v8, final) The contact Access tab is one page read top-down: a summary box ("This
  contact can ask about ...", customers, region, extra details, "cannot ask: ..."), then 1 Roles (chips
  with tier, add/remove), 2 Customers they can ask about (read-only, from the roles), 3 Region
  (CONTACT-REGION lane, not built here), 4 What they can ask (one switch per domain, grouped in plain-English
  sections; details open ONE level as a checklist; the two stamp switches with their before/after
  preview sit in the details of Incoming stock and Product photos and documents). A row that follows the
  roles shows nothing extra; a row the contact differs on is highlighted with "Changed for this contact"
  and a "Reset to role" action that removes that contact's overrides for the row. One sticky Save bar
  shows the number of unsaved changes and sends one PUT (roles + overrides). No keys, codes or prompt hints are shown;
  hand-off teams sit in a collapsed "Advanced" section. The old Access Agents grid and Field reveals
  switches are gone. The role page has the same shape (name and tier, customers radio, switches,
  contacts, Advanced).
- AC-AM-8 Two contact rows sharing one respond.io id in one workspace get the INTERSECTION of their
  access (fail closed).
- AC-AM-9 A role with "Sees all customers" unscopes order answers; a contact with linked customers and
  no such role is scoped to them; a contact with neither is refused order/outstanding/sales asks.
  "Sorento/Cabana/Mocha Office" access types no longer decide customer scope.
- AC-AM-10 No validity window: access does not lapse on a date (today's grants all end 2026-12-30/31).

## Owner additions (ruled 2 Oct: Q1-Q4 all (a))

- AC-AM-22 A contact may hold several roles at once (e.g. Sales office and Dealer); grants are the union;
  `sees_all_customers` and staff behaviour apply if ANY held role has them; a contact remove-override
  still beats every role.
- AC-AM-23 Tier (Dealer / Office / End user) is a property of the role (`audience_tier`), not a single
  contact picker; a contact with Dealer and Office roles gets staff behaviour and promotions for both tiers.
- AC-AM-24 The incoming stamp and the product-attachment stamp are tree fields ticked on every seeded
  role; unticking one for a contact makes that contact's rosters print without it (and without the
  "None of these have incoming stock right now." line); the roster rows and order are unchanged.

- AC-AM-25 MOVED to lane CONTACT-REGION (owner 5 Oct): no region behaviour in this lane.

## Enforcement and prompt read ONE tree

- AC-AM-11 `effective_access(db, contact)` is the only reader of roles/overrides for a chat turn. Both
  the domain gate (`Profile.grants`, `turn/apply.py:2199`) and the field/ask gates
  (`ctx.access.attributes`) are filled from it; the agent check (`evaluate_agent`) no longer gates a
  chat turn.
- AC-AM-12 Leak matrix: for every (role preset, domain, field) a test asserts that a domain/field absent
  from the contact's access is (a) absent from its rendered parser prompt and (b) refused or stripped
  at fetch/answer, including when the ask is phrased to name that domain directly.
- AC-AM-13 Access is read before the parser prompt is rendered, so the trimmed prompt is the one the
  parser sees on the same turn.
- AC-AM-14 Every parser block that belongs to a domain or field is tagged with it; untagged blocks render
  for everyone. Full-access render equals today's production text (byte-for-byte check in the migration test).

## Registry

- AC-AM-15 `chatbot_domains` + `chatbot_domain_fields` are the only source for domains, fields, tools,
  field labels and reveal keys. `FIELD_REVEAL_KEYS`, `SUGGESTED_AGENTS`, the incoming `GATED_FIELDS`
  list and `CHATBOT_TOOL_DOMAINS` are derived from rows; `default_policy()` remains only where the
  plan names it (blank-install default, security allow-list).
- AC-AM-16 Adding a feature = one domain or field row + its prompt block tag + tool on the row; it is
  ticked on no role until an admin ticks it (test: insert a row, no contact gains access).

## Escalation (agents stay for routing)

- AC-AM-17 Each domain row carries `escalation_agent_code` (FK `access_agents.code`) next to
  `escalation_team_code`; a hand-off uses the DOMAIN's agent, not the parser's `suggested_agent`.
  A domain with no team offers no hand-off. Contact Access tab lists agent / team set / tier-1 team per
  granted domain beside "Can escalate to a person".
- AC-AM-18 `access_agents`, `agent_teams`, SLA, ticket/complaint routing and the n8n preflight
  `/external/access-agent/check` behave exactly as before.

## Migration (no internal user loses access)

- AC-AM-19 The migration creates the 5 roles with the card section 2 ticks and assigns every contact per
  the mapping SQL: on the 25 Sep prod copy Sales office 87, Purchasing 5, Management 2, none 6, with
  overrides for CONTACT Q, CONTACT A (-sellable -on order -purchase orders), CONTACT F (+outstanding),
  CONTACT O (+20 incoming fields).
- AC-AM-20 Loss check: for every contact, every reveal key and agent-reachable domain it had before is
  still granted after (test on a seeded chain + a read-only script run on the dev copy, zero lost rows).
- AC-AM-21 Migration applied and checked on a dev copy before the hand test; idempotent SQL handed to crew.
