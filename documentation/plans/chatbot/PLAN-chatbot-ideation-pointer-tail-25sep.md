# PLAN: the tail keeps the ideate lane's draft pointer (regression since the turn re-architecture)

Status: REVIEWED - small fix track, PR #1230 open, review round 1 addressed 25 Sep 2026
Domain: chatbot / ideation
Branch: `fix/chatbot-ideation-pointer-tail`
UAC: `chatbot-ideation-pointer-tail-25sep-acceptance-criteria.md`

## Problem (measured 25 Sep 2026, owner console walk on stack A, PR #1222 head f64373c95)

Every `ideate` turn mints a NEW draft on the shared service. Three console turns
("i have an idea" / "all actually" / the full idea text) produced three separate
`app_ideation.ideas` rows on `fx_shared_local`; the MCP log shows every
`crm_ideation_turn` request carrying `"session_vars": {"ideation": null}`; the prod copy
`sorento_cagent_stack` holds a pointer on 0 of 100 `respond_contacts`.

Cause: the ideate lane runs through `engine.py::run_tail` (called right after
`ideate_mod.run(ctx, item, dry_run=dry_run)`, around line 4675), and `run_tail`'s
five-key payload (around line 4395) writes `"ideation": before.get("ideation")` - the
pointer loaded at the START of the turn. The ideate lane's own result
(`lanes/ideate.py::build_reply` -> `item["ideation"]`, the `tail_item` handed to
`run_tail`, also on `item["outcome_fragment"]["build-ideate-reply"]["ideation"]`) is
never read back. (`turn/tail.py::session_payload`, the composed-answer arm, has the
same `getattr(ctx, "ideation")` shape but the ideate lane does not reach it.)

- Live turn: `ideation_turn_service.handle_turn` writes the new pointer to the contact,
  then the tail's `overwrite_for_contact` replaces the whole five-key state with the
  stale pointer and wipes it.
- Dry run (console, replay harness): the same stale payload is handed back as
  `session_patch`, so the next turn arrives with no pointer.

Before commit `0a335146e` (22 Sep 2026, turn engine re-architecture, on main)
`tail/compile_state.py:773` wrote
`"ideation": ideate.ideation if ideate else prev.ideation`. The re-architecture dropped
that arm. The console case `tests/chatbot/console_cases/2026-09-24-ideation-draft-keeps-lane.yaml`
injects the pointer by hand through `previous_conversation_state`, which is why it
never caught this.

Downstream symptoms, all the same cause: the recent-files menu (`pending_media` rides
the pointer) is re-listed every turn and a selection such as "all actually" becomes a
new idea's problem text; a bare "yeah" after "Is this right?" has no open draft to
absorb it (`turn/apply.py::open_ideation_draft` is false) and falls to `low_signal`.

## Fix (one seam)

In `run_tail`'s payload: when the tail item carries the `ideation` key (only the ideate
lane's `build_reply` sets it - always present on that item, `None` when the tool
answered no pointer, which is how a completed / cancelled / voted draft clears itself,
the same way `handle_turn`'s own write leaves `ideation` out on a terminal status), the
payload's `ideation` is `item["ideation"]`; otherwise `before.get("ideation")` as today.
No new key, no new table, no change to `handle_turn` or to the lane.

Out of scope (owner, 25 Sep): echoing the candidate photos back in the picker reply is
a follow-up lane.

## Tests (test-first)

- `tests/chatbot/test_ideation_pointer_tail.py` (new):
  - AC-1: dry-run ideate turn, `call_ideation_tool` stubbed to answer
    `session_vars.ideation = {draft_id: "d1", status: "collecting", is_test: true, ...}`
    -> `TurnResult.session_patch["ideation"]` equals that pointer.
  - AC-2: live ideate turn (dry_run false), same stub -> after the turn the contact's
    `respond_contacts.session_vars["ideation"]` equals the pointer (the tail did not wipe
    what `handle_turn`'s write left behind).
  - AC-3: a non-ideate turn (any other lane) with a pointer already stored leaves it
    unchanged (`remembered_before` fallback still works).
  - AC-4: a second dry-run ideate turn fed the first turn's `session_patch` as
    `previous_conversation_state` sends `draft_id: "d1"` in the tool call body (the
    console carries the draft forward).
- Console case: `tests/chatbot/console_cases/2026-09-25-ideation-pointer-carries.yaml`,
  two REAL turns with no injected pointer (`--mock-parser`), graded on the second turn's
  tool body carrying the first turn's `draft_id` - or, if the harness cannot grade the
  tool body, on the second turn staying `ideate` with the reply naming the same draft.

## Verification

- `SORENTO_ENV_FILE=.env.ci-tests pytest tests/chatbot/test_ideation_pointer_tail.py tests/chatbot/test_s3_canned_and_ideate.py tests/chatbot/test_turn_replay.py tests/test_ideation_turn.py -q` green on a private DB.
- Console check on stack A (:8000, contact 900000008) once the fix is cherry-picked
  there: "i have an idea" then "the price tag should show promo in red" -> ONE idea row
  on `fx_shared_local`, second turn's MCP body carries the first turn's `draft_id`.
- No screen changed: no browser pass.

## Review round 1 (PR #1230)

Two should-fix items addressed:

- `app/services/ideation_turn_service.py`'s `_graceful` (the unconfigured and
  create_idea-outage paths) returned the contact's raw DB `session_vars`, not the
  pointer THIS turn actually read. With the tail fix above, a dry run carrying a TEST
  pointer through `session_vars_in` therefore had `session_patch.ideation` come back as
  the contact's REAL stored pointer (possibly `is_test: false`) on an outage, instead of
  the carried test one. `_graceful` now takes the already is_test-guarded
  `ideation_state` explicitly and folds it into the returned `session_vars`, absent when
  there is none - the same "key present only when a draft is open" shape the
  read-modify-write success path already keeps. New test:
  `tests/test_ideation_turn.py::test_outage_on_a_dry_run_keeps_the_carried_pointer_not_the_db_one`
  (red before the fix, green after).
- `tests/chatbot/console_cases/2026-09-25-ideation-pointer-carries.yaml` now sets
  `cold: true` on the case (so turn 1 does not start from the contact's stored memory)
  and a header note that the stack this runs against needs ideation actually configured
  and the shared service reachable, or the case fails for an unrelated reason.

Nits 1 and 2 left as is: the switch on the tail item's shape (`"ideation" in item`)
rather than on `branch_kind == "ideate"`, and the legacy nested `variables.ideation`
pointer not being explicitly cleared on a terminal status (the flat top-level read
already wins per `session_state.five_keys`, so both are cosmetic, not behavioural).
