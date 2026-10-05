# PLAN - AI replies allowed per contact (AI-ALLOWED-GATE)

Status: behaviour card posted (crew-ask on the lane PR), awaiting owner answers. Track: L by rule
(additive migration + engine rule + toggle on an existing screen; mock skipped, reuses the Chatbot
card's `SwitchRow`). Lane AI-ALLOWED-GATE, one PR.
UAC: `ai-allowed-gate-acceptance-criteria.md`. Paths under `sorento_crm_backend/app/` unless shown.

Owner (5 Oct 22:50): "I am thinking to add the is_allowed_ai field to our respond contact." A WhatsApp
contact not yet synced into the CRM (respond_io_id 551719711) messaged 3 times and got 3x "Sorry, I
ran into a problem understanding that..." because n8n forwards every inbound message (its only check
is `is_human_intervened`) and the engine's only contact check is the 404 in
`services/conversation_variables_service.py:86-95`, which the catch-all in
`services/chatbot/engine.py:3028-3062` turns into the generic error reply. Owner wants: the CRM
decides who the AI replies to; a contact not allowed, or unknown to the CRM, sees NOTHING.

## Verified before the card

- `outbound_enabled` does NOT block the chatbot reply. `/external/chat/turn` returns `actions`
  (`api/v1/external/chat.py:218-219`) and n8n sends them through respond.io itself ("the caller
  executes `actions` and nothing else", `chat.py:535-547`). `assert_outbound_enabled` is asserted
  in exactly one place, `RespondClient._post_contact_message` (`services/integration_service.py:338`),
  i.e. sends the CRM makes itself (stock-ask notify, SLA lines, ideation). It also fails OPEN for an
  unknown identifier (`services/respond_outbound_service.py:40-44`), so even a CRM-side send to the
  incident contact would have gone out. Two reasons the new flag is needed.
- The engine resolves `contact_respond_id` at `engine.py:2775` from the envelope, BEFORE the
  worker offload (`:2786-2789`) and before `_read_session_vars` (`:3798`, the 404). The company
  scope lookup at `:2781` already fails closed to zero rows for an unknown contact but does not stop
  the turn.
- The silent-result shape to copy is the media-denied close (`engine.py:3958-4015`): trace
  `replied` + `remembered` records, `_close_turn(... status, stage, branch_kind, response)`, a
  `TurnResult` with the declared branch kind. Branch kinds are a declared vocabulary
  (`services/chatbot/contracts.py:303-318`, asserted by `tests/chatbot/test_contracts.py:56`,
  mirrored in `sorento_crm_frontend/.../chat-history/types/chatbotTurn.types.ts:37` and
  `turnPresentation.ts:59-120`).
- Rows are created in ONE place, `ContactService.create_contact` (`services/contact_service.py:364`),
  called by the n8n sync (`api/v1/external/respond_contacts.py:105`), the scheduled sync
  (`services/respond_sync_handler.py:261`) and the Contacts page. `scripts/contact_bulk_setup.py`
  never creates a contact.
- Test impact of a FALSE default: 148 of the 153 test files that run a turn seed a contact without
  naming the new column (raw `INSERT INTO respond_contacts` or `RespondContact(...)`), so under a
  FALSE schema default every one of them would answer a silent turn.

## Design (to be confirmed by the card)

See the card on the PR. Sections below are filled in once the owner answers.

### Schema
### Engine gate
### Contact card, list, copy, system API, script
### Tests
