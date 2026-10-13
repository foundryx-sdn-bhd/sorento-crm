# UAC - AI replies allowed per contact (AI-ALLOWED-GATE)

Plan: `PLAN-ai-allowed-gate-5oct.md`. IDs AC-AI-n. Owner answers 5 Oct 23:52: Q1 (a) default FALSE
for every new row, backfill TRUE; Q2 (a) status done + branch kind `ai_not_allowed`; Q3 console and
dry-run turns gated too; Q4 "AI replies allowed" / "When off, the chatbot sends this contact nothing,
not even an error."; Q5 copy facet, list filter and system API filter all in. Every AC names its test.

## Schema
- AC-AI-1: `respond_contacts.chatbot_allowed` BOOLEAN NOT NULL; after upgrade every contact that
  existed reads TRUE, a row inserted afterwards reads FALSE, and a second upgrade leaves a blocked
  row blocked. `tests/test_migration_aig_0001_chatbot_allowed.py` (existing rows / new row / re-run tests)
- AC-AI-2: the migration chains onto main's head, is re-runnable, downgrades and re-upgrades.
  `test_revision_chains_onto_mains_head`, the down/up test

## Engine gate (`tests/chatbot/test_ai_allowed_gate.py`)
- AC-AI-3: a turn for a respond.io id with NO `respond_contacts` row ends silently: zero actions, no
  reply text, no parser call, branch `ai_not_allowed`, status done at received, never the generic
  error reply. `TestUnknownContact` (3 tests)
- AC-AI-4: a turn for a contact whose flag is FALSE ends the same way, writes nothing to the
  contact, and emits no `update_contact_fields` action even when `is_human_intervened` is true.
  `TestBlockedContact` (4 tests)
- AC-AI-5: a contact whose flag is TRUE runs exactly as today; a contact created through
  `ContactService.create_contact` is blocked until ticked, then answers; human-intervened handling
  is unchanged. `TestAllowedContact` (3 tests)
- AC-AI-6: the gated turn is recorded and closed (`done` at `received`, `finished_at` set) with the
  trace line "AI not allowed for this contact; sent no reply", so the console and Chat History show
  a terminal state. `TestUnknownContact::test_the_turn_is_recorded_and_closed_with_the_trace_line`,
  `TestBlockedContact::test_the_blocked_turn_row_is_closed_done_at_received_with_the_trace_line`
- AC-AI-7: the gate runs before the worker offload (never enqueues) and before the ordering ticket
  (never takes one). `TestGateOrdering` (3 tests)
- AC-AI-8: a dry-run envelope is gated the same way; a duplicate delivery replays the silent turn.
  `TestDryRunAndDuplicate` (2 tests)
- AC-AI-9: a respond.io id on two rows is blocked when ANY row is unticked and allowed only when
  every row is ticked (security review S1, the `escalation_allowed` duplicate rule).
  `TestTwoRowsOneBlocked` (2 tests), `test_is_allowed_is_false_when_any_row_with_that_respond_id_is_blocked`
- AC-AI-10: `ai_not_allowed` is a declared branch kind. `tests/chatbot/test_contracts.py`
- AC-AI-11: n8n untouched; the gated result is `duplicate=False, actions=[], delegate=None`, reply
  text "" (crew's route-turn check, plan "Engine gate"). Asserted by AC-AI-3/4.

## Contact API and list (`tests/chatbot/test_ai_allowed_gate_api.py`)
- AC-AI-12: a contact created through the service reads `chatbot_allowed: false`; a seeded blocked
  row reads false; a seeded allowed row reads true.
  `test_a_contact_created_through_the_service_reads_blocked`, `test_a_blocked_contact_reads_blocked`
- AC-AI-13: `PUT /contacts/{id}/chatbot {chatbot_allowed}` sets it, absent leaves it alone, 403
  without `user_management.contacts.edit`.
  `test_put_blocks_and_allows_the_contact_and_absent_leaves_it_alone`, `test_writing_the_flag_needs_the_contact_edit_grant`
- AC-AI-14: list rows carry the flag; `ai=yes|no` filters; another value is 422.
  `test_list_rows_carry_the_flag`, `test_list_filter_ai_yes_selects_only_allowed_contacts`,
  `test_list_filter_ai_no_selects_only_blocked_contacts`, `test_list_filter_ai_rejects_any_other_value`

## Create and sync
- AC-AI-15: `ContactService.create_contact` (the Contacts page, the syncs) leaves the column default,
  so a new contact is blocked. `test_create_contact_leaves_the_default_blocked`
- AC-AI-16: the n8n sync and the scheduled sync create a NEW contact blocked; an update of an
  existing (allowed) contact leaves the flag alone.
  `test_the_n8n_sync_creates_a_new_contact_blocked`, `test_the_n8n_sync_updating_an_existing_contact_leaves_the_flag_alone`, `test_the_scheduled_sync_creates_a_new_local_contact_blocked`

## Copy access, system API, service, script
- AC-AI-17: copy access reports and writes the flag as the "AI replies" switch; equal flags report
  nothing. `test_copy_access_diff_reports_a_change_when_the_flag_differs`,
  `test_copy_access_diff_reports_nothing_when_the_flag_matches`, `test_copy_access_writes_the_flag_onto_the_target`
- AC-AI-18: `GET /system/respond-contacts` rows carry the flag beside `outbound_enabled`;
  `ai=allowed|blocked` filters; another value is 400. `test_system_list_*` (4 tests)
- AC-AI-19: `chatbot_allowed_service`: `is_allowed` (no row false, any true row true),
  `status_counts`, `set_all`, `set_contact` (404 for an unknown reference, dry runs write nothing).
  `test_is_allowed_*`, `test_status_counts_*`, `test_set_all_*`, `test_set_contact_*` (10 tests)
- AC-AI-20: `scripts/set_contact_ai.py`: `--status` never writes; without `--yes` a mutating run is
  a dry run that exits 2; `--off/--on --contact` and `--all` with `--yes` write. `test_script_*` (5 tests)

## Screen
- AC-AI-21: the Chatbot card shows the "AI replies allowed" switch with the hint "When off, the
  chatbot sends this contact nothing, not even an error.", checked from the flag; toggling saves
  through its own mutation, never the card save or the escalation save.
  `ContactChatbotSection.aiAllowed.test.tsx` (5 tests)
- AC-AI-22: the service reads the flag (absent = blocked, the production default), the AI save sends
  only `chatbot_allowed`, the card save never sends it. `contactChatbotService.aiAllowed.test.ts` (5 tests)
- AC-AI-23: the contacts list shows an "AI replies" yes/no column and an "AI replies" filter;
  verified in the browser pass (plan "Browser pass"), no UUID shown, usable at 375px and 1280px.
