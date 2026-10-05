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

## Design (on the card's recommendations; revised where the owner answers differently)

### Schema (one additive revision, `aig_0001_chatbot_allowed`, down_revision = main head)
- `respond_contacts.chatbot_allowed` BOOLEAN NOT NULL DEFAULT true (Q1 recommendation b), the
  `esc1_0001_escalation_allowed` shape: `SET LOCAL lock_timeout`, `ADD COLUMN IF NOT EXISTS`,
  downgrade drops it. Every row existing at migration time reads TRUE; nothing else to backfill.
  (If the owner picks Q1 a: `DEFAULT false` plus `UPDATE respond_contacts SET chatbot_allowed = true`
  in the same migration, and the 148 turn-test fixtures name the flag.)
- Model `RespondContact.chatbot_allowed` (`models/access.py:230`), default True both sides.
- `RespondContactCreate.chatbot_allowed: Optional[bool] = None` (`schemas/user.py`), None = the
  column default; the n8n sync (`api/v1/external/respond_contacts.py:104`) and the scheduled sync
  (`services/respond_sync_handler.py:262`) pass `False` when they CREATE a row; an existing row is
  never touched by a sync.

### Engine gate (`services/chatbot/engine.py::_run_turn`)
- `services/chatbot_allowed_service.py::is_allowed(db, respond_io_id)`: one SELECT, True when ANY
  `respond_contacts` row with that `respond_io_id` has the flag on, False when no row (the same
  fail-direction `escalation_allowed` takes for a respond.io id on two rows).
- In `_run_turn`, after the scoped factory is built (`:2784`) and BEFORE `if offload is None`
  (`:2786`): `if not _ai_allowed(session_factory, contact_respond_id): return
  _ai_not_allowed_turn(...)`. The helper copies the media-denied close (`:3958-4015`) without the
  reply: D15 dedup first (`_existing_turn`, a duplicate replays `_duplicate_result`), then
  `_insert_turn` (no ticket, no queue), a `TurnTrace` with one `received` record, status
  `skipped`, summary "AI not allowed for this contact; sent no reply", why "The CRM decides which
  contacts the chatbot answers; this contact is not allowed, or is not in the CRM.", then
  `_close_turn(status="done", stage="received", branch_kind="ai_not_allowed", error=None,
  response={"actions": [], "reply": {"text": ""}})` and a `TurnResult(actions=[], reply={"text":
  "", "quick_replies": None}, status="done", stage="received", branch_kind="ai_not_allowed",
  is_test=dry_run)`. No parser config is resolved, no session vars read or written, no memory, no
  `update_contact_fields` action.
- New declared branch kind `ai_not_allowed`: `contracts.py:303` BRANCH_KINDS, `trace.py:_LANE_WORDS`
  ("AI not allowed"), the pinned external-contract fixture's enum
  (`tests/chatbot/fixtures/external_contract_main_f14802a7c.json:366`, as `media_denied` was added),
  FE `chatbotTurn.types.ts` BranchKind, `turnPresentation.ts` LANE_WORDS + `turnHeadline` case
  ("Not allowed", tone ok). The console needs no change: `useChatbotConsole.ts:95` already renders a
  "(no reply)" bubble carrying the branch-kind chip when a turn sends nothing.
- n8n untouched: the response is `delegate: null, actions: [], branch_kind: "ai_not_allowed"`.

### Contact card, list, copy, system API, script
- `ContactChatbotUpdate.chatbot_allowed: bool | None` (`api/v1/user_management/contacts.py:363`),
  absent = leave alone; handler sets it; response schema + `contact_to_response_dict`
  (`contact_service.py:765`) carry it; list filter `ai=yes|no` through `access_filters` into the
  `(name, column)` loop at `contact_service.py:262`.
- Copy access: `("chatbot_allowed", "AI replies")` appended to `SWITCHES`
  (`contact_access_copy_service.py:49`); snapshot, diff, summary and write follow for free.
- System API (`api/v1/system/respond_contacts.py`): `RespondContactOutboundRow.chatbot_allowed`,
  query `ai=allowed|blocked` (400 otherwise), counts unchanged (they are the outbound audit line).
- `chatbot_allowed_service.py` also holds `status_counts`, `set_all`, `set_contact` (the
  `respond_outbound_service.py:126-178` shapes, reusing its `find_contact`);
  `scripts/set_contact_ai.py` mirrors `scripts/set_contact_outbound.py` (dry run without `--yes`).
- FE: `contact.types.ts` `chatbot_allowed?`; `contactChatbotService.ts` profile field +
  `saveContactChatbotAllowed` (own PUT, one key, the escalation pattern); hook
  `useSaveContactChatbotAllowed`; `ContactChatbotSection.tsx` SwitchRow "AI replies allowed", hint
  "When off, the chatbot sends this contact nothing, not even an error."; `ContactsList.tsx`
  `yesNoColumn('chatbot_allowed', 'AI replies', ...)` beside Escalation.

### Tests (red first)
- `tests/chatbot/test_ai_allowed_gate.py`: unknown contact silent; flag false silent (no parser
  call, no actions even when human-intervened, no session write); flag true normal; gate before
  offload (never enqueues) and before the ticket (never takes one); dry run gated; duplicate replays
  empty; two rows one allowed.
- `tests/chatbot/test_ai_allowed_gate_api.py`: contact GET/PUT/403/list/filter, sync creates
  false, copy-access diff, system API row + filter, service functions.
- `tests/test_migration_aig_0001_chatbot_allowed.py`: the `esc1_0001` test shape.
- `tests/chatbot/test_contracts.py`: fifteen branch kinds.
- vitest: `ContactChatbotSection.aiAllowed.test.tsx`, `contactChatbotService.aiAllowed.test.ts`.
