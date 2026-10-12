# PLAN: REPORTS-HELP, offline replay of the reports gate cases

Status: Build. Small fix track (tests only plus any code fix the replay proves; no migration, no auth change).
Helper lane for PR #1572 (REPORTS-STEP2), stacked on its branch `crew/reports-step2`.

## Why

The reports-all gate sits at 22/24 after 7 fix rounds. The live parser is not deterministic (4 of 20 identical
emissions differ run to run), so a red case cannot tell a code bug from a parser wobble. Feeding RECORDED parser
verdicts through the real engine makes every fix round deterministic.

## What exists already (reuse, do not rebuild)

`tests/chatbot/test_turn_replay.py` (S6 gate, AC-1590/1591) already runs recorded turns through the real
`engine.run_turn` with the parser, access check, entity resolution and MCP tool calls stubbed from the case file,
chains carried step to step via `session_patch`. Cases live under `tests/chatbot/replay_turns/<group>/*.json`.

## Shape

1. New group `tests/chatbot/replay_turns/reports/` for the report conversations, one chain file per rpt case
   (redacted recorded parser verdicts per turn, recorded `crm_report` tool envelopes).
2. Extend the harness only where the report lane leaves the four existing seams (checked against the code, cited in
   the commit that does it).
3. Red first: each failing rpt case lands as a replay that reproduces the live failure, then the code fix turns it
   green.

## Data

Recorded parser outputs come from crew (crew-data-request on #1572, sent redacted via crew send). No customer data
in PR comments or in committed files beyond what the redaction keeps.
