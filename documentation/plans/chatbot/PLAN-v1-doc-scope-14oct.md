# PLAN: v1 chatbot - unlinked non-staff contacts get no other customers' documents (V1-DOC-SCOPE)

Status: in review (small fix track + security review; no migration, no new reply text). PR #1648;
V1-DOC-SCOPE-SALES fix round (salespeople keep document access) in review.

## Problem

An unlinked non-staff contact (no customer link, no active office access type; owner rule:
unlinked != customer) can name a delivery-order number, or a sales-order number with the
`sales_orders.outstanding` reveal, and v1 answers with that document for ANY customer.

## Fix (same shapes v1 already uses)

1. The resolver per-match drop (`turn_runtime._without_foreign_sales_orders`) also drops a
   delivery-order match whose customer is not in the contact's links (the
   `so_status._scope_check` rule). Unlinked non-staff: no customer document matches.
2. Business lane SO-by-number with the reveal: `linked = None if ref_staff else links`, the
   rule the PO-ref branch already uses.
3. DO list asks with no number: scoped to links for non-staff (empty links: nothing). Security
   review added the other arms that list customer documents: the outstanding report and the SO
   list are refused the same way; "did you mean" alternatives and the casual clarifier prompt
   go through the per-match drop; the customer PO-ref arm holds an enforced contact to its links.

Staff unchanged. Blocked cases reuse the existing refusal line ("Sorry, that isn't under your
account. I can only check on your own account.").

## Who is staff (crew ruling (a), 14 Oct)

Staff = `chatbot_profile.tier == "office"` OR an active office access type
(`contact_customer_scope.is_office_staff`), the same rule in all three fixes. The engine reads the
scope and the office access bit once per turn (`services.customer_scope_and_staff`) and hands the
bit to the resolver drop and the tool runner, which writes `doc_staff` for the lane every turn.
`ref_staff` (tier only) still gates our own PO / PR reveals. Both keys are engine-only
(`required_fields.ENGINE_KEYS`), so a parser-emitted copy is dropped.

Existing tests in 33 files seeded an unlinked, tierless contact playing an office role; they
now mark it staff explicitly (an office access type, so the tier-driven escalation offer is
unchanged; about 90 marks, per test or in the file's own seeder), assertions unchanged. The
shared `test_outstanding_lane._seed_contact` stays non-staff: about 90 tests rely on that.

## Salespeople keep document access (V1-DOC-SCOPE-SALES, crew rulings 14 Oct)

The owner's prod check found many of the unlinked non-staff contacts are salespeople. For
document access a salesperson is ONLY an ACTIVE, not trashed CRM user linked to the contact
(`users.respond_contact_id`) holding the not trashed role `salesperson`
(`contact_asker.is_salesperson_user`; owner rule 11 Oct). Security review: `classify`'s
`sales_agents.contact_id` arm would keep access after the user is deactivated and would let
`master_data.sales_agents.edit` grant it, so an agent link, an inactive or trashed user, or
another role grants nothing (crew ruling (a)). A customer link does not make such a contact a
dealer. For document scope it is staff:

- `services.customer_scope_and_staff` returns staff = office access type OR salesperson, so
  `doc_staff` includes it. An unlinked salesperson sees DO / SO by number, the DO list, the SO
  list and the outstanding report, as main does today (the 81-contact fix).
- A linked salesperson keeps the core `enforced` flag, so the customer-word gate, the lists, top
  selling, report_ask and the order routes hold it to its links exactly as main does (CA-3 pins,
  `tests/chatbot/test_chat_report_scope_leak.py`, unchanged). It is lifted only on the
  in-process by-number arms: the SO number lookup (scope `staff`) and the resolver per-match drop
  (`engine._reference_scope`). The tier alone still never lifts an enforced scope.
- `ref_staff` stays the tier alone: the customer PO-ref arm and our PO / PR cards behave as main,
  and so does the resolver / clarifier per-match drop for a customer PO ref
  (`po_ref_scope_linked`, `engine._reference_kinds`), so a salesperson never learns which SO
  another customer's PO ref is.
- Routes unchanged (ruling (a)). Follow-up (b), only if the owner's SQL shows linked salespeople:
  make the core `contact_customer_scope.enforced` exclude a salesperson so the order routes,
  report_ask and complaints agree with the chatbot.

## Slice 3: the customer picker (crew audit at 54740870f, 15 Oct)

`engine._customer_scope_gate` left every unlinked contact untouched, so an unlinked non-staff
contact typing another customer's partial name reached the generic resolver: its customer
picker listed other customers' names, and the picker's DO probe (`resolve_gate`
`CUSTOMER_PROBE_TOOL`, outside `run_fetch`'s guard) stamped which of them have DOs. The gate now
treats an unlinked contact that is not document staff (`unlinked_held`: not the tier, no office
access type, not a salesperson) as owning no customer: a customer word it typed this message
refuses the turn before the resolver, with the existing no-link line, the same path an
enforced dealer's word outside its links takes. No picker, no probe, no resolver call for the
word. Document staff are unchanged. AC-CS-03 ("unlinked contact unchanged: the picker") is
flipped under the owner rule unlinked != customer.

Security review round 2: under `order` the resolver reads a brand, category, product or order
word as the customer it names (`entity_resolver._DOMAIN_HINT_EXPANSIONS`), so the parser's tag is
not enough. For a held contact `resolve_kinds` / `resolve_gate.run` (`hide_customers`) drop every
customer row right after the resolver, before the gate builds a picker or runs its DO probe; a
word typed this message that only matched customers gets the no-link line. A carried customer
word is dropped, never refused, so a refusal cannot lock the next turns. Top selling is no
longer skipped for an unlinked contact.

Crew ruling (b), 15 Oct, keeping the owner's CA-3 Q5(a)
(`PLAN-chat-analytics-ca3-chat-dataset-10oct.md`:181-184): a GRANTED report turn by an unlinked
contact is exempt, picker included: the chat report under `chatbot.analytics`, the sales report
(every `SALES_FIGURE_STATUSES` ask: sales report, top selling, sales analysis, ranking) under
`sales_orders.sales_report`. The owner grants those per contact. Everything else (orders, DO, SO,
outstanding, any domain without such a grant) keeps the slice-3 rule. The CA-3, report_ask and
sales-report suites stay unclassified-asker tests (their slice-3 office marks reverted); without
the grant, SF-1 and the chat lane's own grant check already keep every name away.

Security review rounds 4-5: the grant opens the report's customers, never their delivery orders.
For any unlinked contact that is not document staff (`customer_docs_hidden`, granted report turns
included) none of the three customer DO probe sites runs: the gate's picker arm
(`resolve_gate.run(skip_customer_probe=...)`), the roster stamp (`stamp_customer`) and the
narrower's PICKER-ONE stamp (`turn_runtime.stamp_customer_ask`). The low_signal clarifier asks
the resolver itself, so a held contact's customer rows are dropped from its prompt too.

Queued follow-up (crew, 15 Oct, after this PR merges): the same clarifier drop for an ENFORCED
dealer, keeping only its own links' customer rows (pre-existing gap, same files).

## Open before merge

The owner runs `crew/state/reports/V1-DOC-SCOPE-salesperson-check.sql` (read-only) on production
and gives every real salesperson shown as BLOCKED an ACTIVE CRM user with the role `salesperson`
(its `to_fix` column says what is missing).


The owner runs the read-only SQL in the PR's crew-done comment on production to see which unlinked
contacts with neither marker asked order questions in the last 60 days (they lose those answers).

## Acceptance

See `v1-doc-scope-acceptance-criteria.md`.
