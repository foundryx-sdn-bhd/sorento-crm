# UAC - AI replies allowed per contact (AI-ALLOWED-GATE)

Plan: `PLAN-ai-allowed-gate-5oct.md`. IDs AC-AI-n. Draft, pending the owner's answers to the
behaviour card; each AC names its test once the tests exist.

## Schema
- AC-AI-1: `respond_contacts` carries the flag, BOOLEAN NOT NULL; after upgrade every contact that
  existed at migration time reads TRUE (prod behaviour does not change on release).
- AC-AI-2: the migration is re-runnable (ADD COLUMN IF NOT EXISTS) and downgrades cleanly.

## Engine gate
- AC-AI-3: a turn for a respond.io contact id with NO `respond_contacts` row ends silently: zero
  `send_message` actions, no lane, no parser call, no session write, no escalation or PIC routing,
  no memory write.
- AC-AI-4: a turn for a contact whose flag is FALSE ends the same way.
- AC-AI-5: a turn for a contact whose flag is TRUE runs exactly as today.
- AC-AI-6: the gated turn is a recorded, terminal turn: the console / Chat History shows a clear
  terminal state and a trace line saying the AI is not allowed for this contact and no reply was sent.
  No turn is left at `processing`.
- AC-AI-7: `is_human_intervened` handling is unchanged for allowed contacts; `outbound_enabled`
  stays a separate send kill switch.
- AC-AI-8: the gate runs before the worker offload and before the session-vars 404, so a gated
  turn never reaches the generic error reply.

## Contact card and list
- AC-AI-9: the Contact > Chatbot card shows an "AI replies allowed" switch reflecting the flag;
  flipping it saves through `PUT /contacts/{id}/chatbot` (absent = leave alone), behind
  `user_management.contacts.edit`; every contact read carries the flag.
- AC-AI-10: the contacts list shows a yes/no column for the flag.

## Sync, copy, system API, script
- AC-AI-11: a contact created by the n8n sync or the scheduled sync is NOT allowed until a human
  flips it.
- AC-AI-12: the system Respond.io contacts API exposes the flag beside `outbound_enabled`.
- AC-AI-13: a bulk setter script flips the flag per contact or for all, dry-run by default.
