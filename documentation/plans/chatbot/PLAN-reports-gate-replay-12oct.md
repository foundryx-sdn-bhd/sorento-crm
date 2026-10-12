# PLAN: REPORTS-HELP, offline replay of the reports gate cases

Status: Review (harness built, reviewer round 1 folded; the #1572 gate passed on 12 Oct before any recorded chain
arrived, so no rpt fix was needed in this lane). Small fix track (tests plus one recorder script; no app code, no
migration, no auth change). Helper lane for PR #1572 (REPORTS-STEP2), stacked on its branch `crew/reports-step2`.

## Why

The reports-all gate sat at 22/24 after 7 fix rounds. The live parser is not deterministic (4 of 20 identical
emissions differ run to run), so a red case cannot tell a code bug from a parser wobble. Feeding RECORDED parser
verdicts through the real engine makes every fix round deterministic.

## What exists already (reused, not rebuilt)

`tests/chatbot/test_turn_replay.py` (S6 gate, AC-1590/1591) runs recorded turns through the real `engine.run_turn`
with the parser, access check, entity resolution and MCP tool calls stubbed from the case file. Its `_install_stubs`
and `_compare` are reused as they are.

## What was built

1. `tests/chatbot/test_reports_replay.py`: replays a report chain the way the reports gate runs it.
   - State: `{}` on turn one, then each turn's `session_patch`, sent as `previous_conversation_state`
     (`scripts/chatbot_eval/runner.py` + `engine.py:79`); the contact row is never written.
   - Seed: a case carries `seed` (`{table: [row]}`), because the report lane resolves brand / sales agent /
     category words by SQL (`lanes/business/report_ask.py::_resolve_word`), not through `resolve_entity`.
   - Grade: the last turn goes through the eval's own `grade()`, with tool errors read off the turn's stored
     trace by `parse_trace`, the previous bot reply (stuck check) and the chain's user texts (header-echo rule).
2. Where cases live: `tests/chatbot/replay_reports/` holds hand-built cases only. Recorded gate chains stay out of
   git under `$REPORTS_REPLAY_DIR` (refused inside the repo, DEV split only).
3. Recorder (`scripts/chatbot_record_turn.py`): `--group reports` (requires `--out-dir`) and `--out-dir` (refused
   inside the repo).
4. Replay harness fixes found on the way (in `test_turn_replay.py`, so the S6 gate gets them too): a recorded legacy
   report name (`crm_sales_report`) answers the `crm_report` call the MCP seam turns it into; nameless
   `{kind: tool, domain}` trace entries are not calls; an envelope truncated by the trace's 8 KB cap fails as a
   recording gap instead of replaying the stub.

## Data

Recorded parser outputs come from crew (crew-data-request on #1572, sent redacted via crew send). No customer data
in PR comments or in committed files.
