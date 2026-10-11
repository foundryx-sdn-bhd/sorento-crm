# PLAN - CLARIFY-POLICY

Status: v2 after owner rulings (11 Oct 2026); building; Q6-Q8 (a) ruled by crew; Q9 + Q10 ruled by the owner (ask unclear, carry clear; follow-up continues; no expiry). Track:
full, L (additive table + seed migration). LEAD pattern. UAC: `clarify-policy-acceptance-criteria.md`.
Card: `CARD-clarify-policy-11oct.md` (+ `.html`). PR #1570.

## Slices (one branch, one PR)

S1 Loop breaker (AC-120..125). `app/services/chatbot/clarify_policy.py`: `ask_fingerprint`,
`should_break`; fingerprint stored with the bot's own reply; applied once at the reply choke point.
Break reply = what is missing + handover / refer line, no data, no guess (Q4).

S2 Policy table (AC-110..116). Model `ChatbotClarifyPolicy` in `app/models/chatbot_policy.py`
next to `chatbot_domains`; seed constant `CLARIFY_POLICY_ROWS` (one copy, frozen-seed rule as
`turn/policy_rows.py`); migration `clp_0001_clarify_policy` (id <= 32 chars, down_revision = main
head, `CREATE TABLE IF NOT EXISTS` + `INSERT ... ON CONFLICT DO NOTHING`); loader with fallback
(`turn/policy.py` load_policy pattern). Wire reads: `do_period` / `so_period` (default = the skip
switch path, `do_ask.py:101`, `lanes/business/__init__.py:1534-1543`), `outstanding_scope`
(`lanes/business/__init__.py:1947-1963`), `repeat_limit` into S1. Hand-test SQL to
`crew/state/migrations/CLARIFY-POLICY.sql`.

S3 Generic menus (AC-130..132). `turn/apply.py:2094-2113`, `turn/compose.py:1170-1214`.

S4 Carried subject (AC-140..146; rows `carry_combine` + `carry_unclear`). Seam after `decide()` (`turn/apply.py:3412`) and the reconcile
step, before `_focus_rules` (`:3494`): one function `clarify_policy.carry_conflict(...)` returns a
Pending (`turn/pending.py:89`, kind `carry_scope`) as the kind pick does (`turn/apply.py:1814-1817`);
answer arm in `_answer_pending` (`turn/apply.py:721`) via `open_question_answer` positions /
`all`. Clear = `anaphora.backward_reference` (`turn/decide.py:441-455`) or `continuation` (S5) or
REFINE or a held-question answer; otherwise ask. Echoed carry (`current_message: false`, `turn/apply.py:1044-1049`) is NOT a
restatement. "Could apply" = the new domain's allowed kinds (`lanes/business/gate.py` ALLOWED). Crew-note on #1473 (INTENT-FRAMES) naming the function so
the second lane to merge calls it from `frame.py`.

S5 Parser `continuation` (AC-147..149). Copy #1473's "SAME SUBJECT OR A NEW ONE" paragraph (not its
TOPIC RESET part) as `CONTINUATION_ADDENDUM` in `app/services/chatbot_parser_prompt.py`, published as
a new UNLABELLED `chatbot_semantic_parser` version by migration, following #1473's `if_0001` way of
appending onto the wording-layer text (PROMPT-DYNAMIC #1405), never rebuilding from the code
constant. Crew-note on #1473: the second to merge keeps ONE copy of the paragraph. Release step:
owner promotes the version.

## Release step (one ordered step, in the PR body)

1. Crew's eval gate on the clp_0003 parser version (v56 + the amended SAME SUBJECT OR A NEW ONE
   block; DEV, dev key). The version promoted must also carry main's chat report block
   (`ca3_0001_chat_report_prompt`), so crew composes the two before the gate.
2. Owner promotes that version to `production`.
3. Immediately after: `UPDATE chatbot_clarify_policy SET mode='ask', updated_by='release CLARIFY-POLICY',
   updated_at=now() WHERE ask_kind='carry_unclear';`

## Dropped by ruling

v1 S2 promotion level (Q2), v1 S3 attachment-type default (Q3), v1 S4 DO / SO period default (Q1),
v1 S5 collapse line (not ruled; seed = today). The tester's v1 tests for these are removed and the
pre-existing period tests restored to main.

## Order

Coder finishes S1 (Opus). Tester rewrites the red tests for v2 (removes v1 S2/S3/S4/S5 tests,
restores the 6 edited period test files to main, adds S2 policy, S3 menus, S4 carry). Red commit,
red-proof. Coder S2..S4. Kill test per slice. Reviewer (Opus). Migration SQL to crew. Eval gate:
order, product_attachment, promotion, general topics + 30 smoke. Hand test script.

## Out of scope

- Settings UI for the table (later, Lavish mock first; Q8).
- Defaults for kinds with no alternative in code (Q7).
- cand-0274 "has promotion" probe vs file data defect.
