# PLAN: contact bulk setup from the owner's dealer Excel (CONTACT-BULK-SETUP)

Status: building, M track (script only, no migration, no UI, no auth change). Behaviour card:
crew/state/files/contact-bulk/CARD.md (outside git, holds real names). UAC alongside:
`contact-bulk-setup-4oct-acceptance-criteria.md`.

## Journey
Owner hands an Excel of approved dealers (sheet "working file"). Every approved person gets the AI
access his hand-configured example contact has, added to whatever they already hold. Customer links
are never guessed: a review sheet lists candidates, CS ticks, a second run links the ticked rows.

## Shape (simplest thing)
One script, `sorento_crm_backend/scripts/contact_bulk_setup.py`, no new service, no endpoint.
- `parse_workbook(path) -> list[Entry]`: APPROVAL = YES rows only; one Entry per (row, person slot):
  row number, company name, companies (SRT/MCH from "Dealer Registered Under"), name, raw phone,
  normalised phone (`+60...`), level (owner/employee). Invalid phone -> listed, not matched.
- `desired(entries) -> dict[phone, Desired]`: union across rows (ADD rule).
- `plan(db, desired) -> list[change rows]`, read through `contact_access_copy_service.snapshots`.
  Each row: phone, contact label, facet, before, add, status (add / kept / already). Missing phones
  listed separately.
- `apply(db, plan, actor_id)`: one transaction (savepoint + commit) per contact, ADD-only writes,
  one `contact.bulk_setup` audit row per changed contact. Re-run = zero changes.
- `links_review(db, entries, out_xlsx)` / `apply_links(db, reviewed_xlsx, entries, actor_id)`.
- CLI: `--excel PATH --db-url URL [--dry-run (default) | --apply] [--out CSV]
  [--links-review OUT.xlsx] [--apply-links REVIEWED.xlsx] [--actor-email E]`.

RULES constant in the script (codes, not data): company SRT/MCH; BO types dealer (SRT) / mocha_dealer
(MCH) + end_user; employee end_user; stock mode availability, locations SRT -> BRW, MWH; MCH -> HQ,
MOCHA-WH; segment retail; media image allowed; agents general_enquiries, order_enquiries,
incoming_stock_enquiries allowed 2026-01-01 00:00 -> 2026-12-31 00:00 (naive, as Wong's rows);
switches chatbot_stock_allowed + chatbot_eta_offset_applied ON, escalation_allowed OFF (owner Q3). Stock override is SET exactly (owner Q2); everything else ADD-only. Owner answers 4 Oct: Q1 keep agent grants, Q4 BO wins, Q5 ticked customer -> every found contact of the row. Every rule read off the example
contact's stored rows (card).

## Out of scope
Creating contacts, tier, field reveals, document types (example has none = default), escalation /
notify / packing switches, removing anything.

## Tickets
T1 tests (tester, red) -> T2 script (coder) -> T3 dry run + review sheet on snapshot, rehearsal on a
copy of `sorento_cagent_stack` -> reviewer + security-reviewer (PII handling).
