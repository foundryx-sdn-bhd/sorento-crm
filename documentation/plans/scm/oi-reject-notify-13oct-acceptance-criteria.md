# UAC: OI reject notify (OI-REJECT-NOTIFY)

Plan: `PLAN-oi-reject-notify-13oct.md`.

- AC-RJ-1: Rejecting one OI row (single endpoint) sends the OI raiser ONE email and ONE in-app
  notification after the reject commits.
- AC-RJ-2: Rejecting several rows of one OI in one batch sends ONE email and ONE in-app
  notification listing every rejected line.
- AC-RJ-3: A batch spanning several OIs sends one email and one in-app notification per OI,
  each listing only its own OI's lines.
- AC-RJ-4: The email carries OI number, SO number, customer, each rejected line (item code,
  qty, delivery date dd/mm/yyyy), the reason, rejected by and when, and a link to the OI. Same
  table layout family as the handover email; no ` · ` joins; blanks print empty, never `None`.
- AC-RJ-5: The in-app notification links to the OI detail page.
- AC-RJ-6: The email is a seeded, enabled automation (`order_inquiry_rejected`) editable on the
  Automations screen; recipient is the raiser (`include_raiser`).
- AC-RJ-7: When the raiser is the rejecter, or is inactive / has no email / is unknown, the
  raiser gets no email and no in-app notice.
- AC-RJ-8: A reject that fails or rolls back sends nothing.
- AC-RJ-9: A reject does not send an `order_inquiry_handover` email.
- AC-RJ-10: The seed migration is additive and idempotent; downgrade removes only its own rows.
