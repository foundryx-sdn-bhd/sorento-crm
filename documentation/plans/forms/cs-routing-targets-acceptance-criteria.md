# UAC: CS-ROUTING-TARGETS

Plan: `PLAN-cs-routing-targets.md`. Tests: `sorento_crm_backend/tests/test_cs_routing_targets.py`,
`tests/test_cs_pinpoint_routing.py`, `tests/test_form_sla_requestor_routing_matrix.py`,
`sorento_crm_frontend/.../routing/csRoutingMock.test.ts`.

- AC-1 Saving the rules page with one rule whose PIC cannot be assigned returns an error naming
  that rule ("Rule 2: ..."), and every stored rule is unchanged.
- AC-2 Saving sends one request; the stored rules become exactly the rules sent (save = replace),
  with priority = page order. An empty list clears the contact's rules.
- AC-3 Two rules for one form type with the same conditions are refused, naming both rules.
- AC-4 The Assign-to dropdown lists every active, non-trashed, non-integration user who can process
  purchase requests through a non-admin role and is granted Sorento, sorted by name.
- AC-5 A rule can pin a user who is not in the Project Customer Service team; the CS stage of a
  purchase request / sponsorship form is assigned to them, it appears in their My Pending, and they
  get the assignment notification.
- AC-6 A rule pinned to someone no longer eligible (inactive, permission removed, admin-only) falls
  back to round-robin, even when that person is still a team member.
- AC-7 The pin never decides the submit or approval stage, complaints or stock inquiries.
- AC-8 Escalation from a pinned PIC outside the team reaches the next tier's team.
- AC-9 Every routing write (bulk PUT, per-use_case PUT, DELETE) requires
  `user_management.contacts.edit`.
