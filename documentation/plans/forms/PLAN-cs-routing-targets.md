# PLAN: CS routing rules pin any eligible PIC; saving never loses rules (CS-ROUTING-TARGETS)

Status: in review (crew lane CS-ROUTING-TARGETS, branch `crew/cs-routing-targets`). Track: small
fix by size (no migration, no new screen), but it changes an RBAC gate and who can be assigned, so
reviewer AND security-reviewer run.

UAC: `cs-routing-targets-acceptance-criteria.md` (alongside).

## Journey

Owner, 14 Oct 2026: take a CS user out of the Project Customer Service team (tier-1
`customer_service` team of the `purchase_request` access agent) while a salesman's rule
"Purchase Request + Sales Type = Cash Sales -> that user" keeps routing to them.

## What was wrong (origin/main at dd6ab9864)

- The dropdown, the save check and the assignment-time pin all required tier-1 team membership
  (`cs_routing_service.py` `list_candidates` / `upsert`, `form_sla_service.py`
  `_resolve_pinned_assignee`). A removed member silently fell back to round-robin.
- Save lost rules: `saveRoutingRules` DELETEd every use_case and then PUT the rules one by one, so
  one refused rule dropped it and every rule after it. Two rules with the same conditions also
  collapsed silently (the second PUT overwrote the first).
- The per-use_case PUT/DELETE needed only a login.

## Decisions (behaviour card, crew + owner answers 14 Oct)

| # | Ruling |
| --- | --- |
| Rule | Eligible PIC = ACTIVE, not trashed, not an integration user, holds `procurement.purchase_requests.process` (the permission the CS actions check) through a role other than admin/superadmin, granted the company in play. One function: `cs_routing_service.eligible_pics_query`; `is_eligible_pic` wraps it. |
| Company | Dropdown + save check: Sorento (`DEFAULT_COMPANY_ID`), as the tier-1 team lookup was. Assignment: the stage's own company, `company_for_contact` of the submitting contact (AC-E4/E6), unchanged from main. |
| Q1 | Admin-only users excluded: admin passes every check, so it says nothing about who processes purchase requests. |
| Q2 | Owner: the wider dropdown (directors, sales managers, coordinators who hold the permission) is fine. |
| Q3 | The pin is read ONLY on the `purchase_request` agent's `customer_service` stage (`FormSLAOrchestrator._reads_cs_pin`). Before, membership of each stage's team kept a pinned CS user off the submit and approval stages; with membership gone the stage must decide. |
| Q4 | New bulk `PUT /contacts/{id}/cs-routing` and the old per-use_case PUT/DELETE all require `user_management.contacts.edit`. Dev data (`sorento_cagent_stack`): only 4 non-admin active users lack it (guest, two test roles, one test connector user); every CS role holds it. |
| Q5 | Empty state text only: "No one can process purchase requests yet." No mockup. |

## Build

1. `cs_routing_service.py`: `eligible_pics_query` / `is_eligible_pic`; `list_candidates` = eligible
   users sorted by name (case-insensitive); `upsert` checks eligibility; new `replace_all` validates
   every rule (use_case, eligible PIC, predicate shape, duplicate condition sets) before one bulk
   DELETE + inserts + one commit. Errors name the rule ("Rule 2: ...").
2. `contacts.py`: `PUT /{contact_id}/cs-routing` (`{rules: [...]}` -> `{pins: [...]}`); contacts.edit
   on it and on the old PUT/DELETE.
3. `form_sla_service.py`: `_reads_cs_pin(config)` gates the pin; `_resolve_pinned_assignee` takes
   `company_id` instead of `team_id` and checks `is_eligible_pic`.
4. FE `csRoutingMock.ts`: one PUT. `ContactCsRoutingRules.tsx`: empty-state text.

## Review round 1 (reviewer READY, security-reviewer CLEAN)

- Errors name the rule by the page's own label, "Priority N" = `priority + 1`: the page skips
  rules with no PIC yet, so the position in the request pointed at the wrong card.
- A refused PIC is named by name only, else "The selected user"; never the email.
- At most 100 rules per save (422 above).
- Without `user_management.contacts.edit` the rules page is read-only: no Add / Save / Remove /
  drag, selects and conditions disabled.
- Not taken: server-assigned priorities (the client's page order already is the priority);
  hardening `_reads_cs_pin` against an approval stage configured on the customer_service set (a
  config error, not worse than main); a rollback in the resolver's except (pre-existing); deleting
  the unmounted `ContactCsRoutingTable.tsx` (dead, still gated).

## Not changed, verified

A pinned PIC outside the team: My Pending reads `assigned_to_id` (`sla_service.list_my_pending`),
the assignment notification goes to `assigned_to_id`, escalation draws from the next tier's team
regardless of the current assignee. Team Tasks of former teammates stop showing that PIC's tasks
(expected: they left the team). The handling lock only applies after escalation, and the CS set
has no tier 2 in dev data.

## Trigger to revisit

If the owner wants the dropdown narrower than the permission (Q2), edit `eligible_pics_query` only.
