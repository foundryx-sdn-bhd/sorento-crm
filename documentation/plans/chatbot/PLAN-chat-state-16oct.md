# PLAN: CHAT-STATE - one explicit conversation state for follow-ups

**Status:** Code complete, PR #1747 open (head after this commit), reviewer READY, security PASS, red-first + kill-proof
every round, L1 replay 0 unintended diffs on every slice. Fixed TRAIN bench (86 items, grader #1745 230642edc): main
69.7/86 (81.0%), lane round 11 72.3/86 (84.1%), paired +2.67 [-0.67, +6.67]; no item worse than main. Remaining bot
failures are step-2 family matching (handed to CHAT-CLOSED-LISTS #1755). Track: L, LEAD pattern.
**Lane:** CHAT-STATE, branch `crew/chat-state`. Step 1 of the 16 Oct chatbot scale design
(`crew/state/reports/CHATBOT-SCALE-DESIGN-16oct.html`, sections 2, 3, 4 rows B/C/G, 8 step 1).
**Docs:** card `chat-state-behaviour-card.md` (rules R1-R13), UAC `chat-state-acceptance-criteria.md`, coder-facing
design `chat-state-seam-design.md` (file:line seam, skips, risks).

## Goal

Follow-ups right ~62% today (first asks ~92%). Bar for this step: follow-up TRAIN slice >= 80% over 3 runs (design
step 1), first asks not worse; crew's held-out bar for the owner hand test is >= 90% (crew-only).

## Shape

- State = `Focus` (subject) + `Pending` (open question) + `Focus.last_list` (new, rides inside focus like
  `Focus.tasks`) + `Focus.tasks` (pending task), in `respond_contacts.session_vars`. No migration.
- Parser sees it as one fixed four-line block, never trimmed; empty state adds nothing.
- Parser emits `state_changes` (ops keep / new_subject / set / clear / pick / answer / switch_domain /
  close_question, each with `axis` / `position`; values stay in today's parse fields). Pure `apply_changes` applies
  them at one engine seam; when present it is the only carry writer; when absent (prod label, replay corpus) the
  turn is byte-identical to main.
- Gate: the parser prompt in use contains `state_changes`. Prompt labels are limited to production/staging
  (`app/services/ai_prompt_service.py:23`), so the lane prompt is an UNLABELLED version on the dev copy, pinned per
  run via the dry-run `prompt_overrides` key (`engine.py:1308`); `production` is never moved. Delta text:
  `PROMPT-DELTA-chat-state.md`.
- Every turn's trace `memory` event carries state shown / before / changes / after / rejected.

## Contract (what tests code against)

- `app/services/chatbot/turn/chat_state.py`:
  - `view(state, open_question) -> dict` and `render(view) -> str` (block format in the seam design section 2).
  - `apply_changes(state, verdict, *, open_question, domains, kinds, brands, positions_read_elsewhere) -> Applied`
    with `Applied.valid: bool`, `.state` (a `State` with `state_led` set), `.verdict` (dict, `state_changes`
    popped), `.log` (`{shown, before, changes, rejected}`).
- `app/services/chatbot/turn/tail.py::last_list_after(previous, *, question, sections) -> list[dict] | None`.
- `Focus.last_list` (turn/state.py) and `contracts.Focus.last_list`, wire key `focus.last_list`.
- `State.state_led: str | None` (never serialised).
- `head/parser.py`: `PARSE_OUTPUT_JSON_SCHEMA` unchanged; new `PARSE_OUTPUT_JSON_SCHEMA_WITH_STATE`;
  `ParserConfig.state_block: bool`.
- `turn/context.py::ContextLayers.state_block: str | None = None`.

## Slices (red-first each: `test(red):` commit of tests alone, then code; $0 replay gate on every slice)

| Slice | What | Tests (file) | Parser input? |
|---|---|---|---|
| S1 | `view` + `render` (pure) | `tests/chatbot/test_chat_state_block.py` | no |
| S2 | `last_list_after` + `Focus.last_list` wire | `tests/chatbot/test_chat_state_last_list.py` | no |
| S3 | `apply_changes` (pure), all rules except the period-on-new-ask row | `tests/chatbot/test_chat_state_apply.py` | no |
| S4 | engine seam + carry skips + trace log | `tests/chatbot/test_chat_state_seam.py` (+ `test_memory_trace_and_usage.py`) | no |
| S5 | block into context + schema variant + gate + prompt delta (repo file) | `tests/chatbot/test_chat_state_parser_input.py` | YES (lane label live check, 3 TRAIN runs) |
| S4b | held-slot ops keep/skip/clear/unclear for customer + period; remove static R14 projection | `test_chat_state_seam.py` (R14 tests rewritten) + `test_chat_state_apply.py` | no |
| S6 | R15 unclear held slot -> one real-name ask (policy default `ask`) | `tests/chatbot/test_chat_state_clarify.py` | trigger needs live parses |
| S7 | carried-state hygiene (crew 17 Oct): at turn start on the state path, drop carried customer rows the contact can no longer read (one scope-stamped `Customer.id IN` read + current links for an enforced contact) | `test_chat_state_seam.py` | no |
| S3b | R16 period: kept on follow-ups and on new_subject unless skip/clear; a stated period replaces it (owner 16 Oct ~23:00) | `test_chat_state_apply.py` | no |

### Test list (captain's; the tester writes from this + the UAC)

S1 block:
1. Four fixed lines in fixed order (`subject`, `open question`, `last list`, `pending task`), "none" when empty.
2. Brand, tier, sort, channel and report keys print when set.
3. 50 products / 33 list rows print in full; no "and N more".
4. No uuid substring even when rows/options carry uuids (UAC B3).
5. A roster that is both the open question and the last list prints once; an answered stock task prints once.
6. Equal inputs give equal text; empty state -> render gives an empty/None block (UAC B2).

S2 last list:
1. One data section -> positions 1..N in the section's figure order, label/code from the row.
2. Question options kept with their positions and keys.
3. No list shown -> previous kept (Q4a); a new list replaces it.
4. Two sections with rows -> None.
5. Stored focus without the key reads `last_list` None; `contracts.SessionVars` accepts `focus.last_list`.
6. Round trip `focus_to_wire` / `focus_from_wire` keeps it.

S3 apply_changes:
1. keep: focus unchanged, `state_led == "carry"`.
2. new_subject keeps customers + tier + brands, drops products, filters/specs, report frame; closes pending (R4, R6,
   #1721 step 7, Q4a). (Period row held: S3b.)
3. set / clear touch only that axis; `set` with no field for that axis in the parse -> invalid (R1).
4. pick on the open roster -> `open_question_answer` mode pick + `reference_positions`; pick on a data list -> an
   entity with that row's code, `current_message` true; pick 14 of 3 -> invalid, state unchanged (R9, C4).
5. No answer op -> declared `open_question_answer` / `reference_positions` dropped; `answer` -> verdict untouched
   (R5).
6. switch_domain drops status / period / sort / report frame, keeps products + customers (R2).
7. R3/R8: `set product` replacing one held product keeps that row's quantity when the message gave none (#1617).
8. Entities with `current_message: false` are dropped (no restatement revives the old subject).
9. Pure: same inputs -> same outputs; no message text read (purity suite covers turn/*.py).
10. Unknown axis or a kind the registry lacks -> invalid with a logged reason (C4).

S4 seam (stub parser / dry-run mock emission, $0):
1. Emission without `state_changes` -> same plan, rules_fired and session patch as main.
2. `[keep]` + inventory hint after SRT1 -> inventory fetch for SRT1; no reset / new_ask_drops_* rules fired.
3. Bare "2" with the parser picking 1 -> answers 1; no `engine_pick` trace event.
4. APPLY re-entry gives the same focus as the first pass.
5. Invalid change -> today's path, `memory.state.rejected` filled.
6. `memory` trace event carries `state.shown/before/changes/after`; before = the view the parser was shown.
7. (replaced by S4b) R14 static domain projection: removed on owner ruling 16 Oct ~22:45.

S4b held slots (owner 16 Oct ~22:45; replaces the static R14 projection):
1. Held customer + parse `skip customer` (e.g. an incoming ask): tool args carry no customer; stored focus keeps it.
2. Held customer + `keep customer` (a delivery ask): the customer applies.
3. Held customer + `clear customer`: dropped from the stored focus.
4. Held customer + no statement: applies as today (main-identical).
5. Same four for a held period (`date_window`).
6. Static projection gone: no reader of `chatbot_domains.narrowing` added by this lane remains.

S5 parser input:
1. Flag off: `assemble()` byte-identical to main at levels off / conversation / full (golden).
2. Flag on: block present, the five legacy lines absent.
3. Block over 350 est tokens never trimmed, L2 `dropped` False.
4. `resolve_config` sets the flag only when the prompt contains `state_changes`.
5. `parse()` sends `PARSE_OUTPUT_JSON_SCHEMA` (identity) when off, WITH_STATE when on; the extended schema is strict-
   valid; base schema and `DECLARED_KEYS` unchanged.

S6 unclear held slot (R15, owner 16 Oct ~23:00): `unclear customer` with a held registry-kind `customer` -> no
question, the ask runs as a delivery (domain `order`) for that customer; supplier kind -> incoming/PO (no supplier kind
exists today, untested); only when the type cannot settle it (tested with `unclear period`): default `ask` (pinned) ->
one question with real names, no ids, answer fills only that slot; `assume` -> applies; second unclear -> hand-over.
`[]` / invalid changes stay on today's path until TRAIN shows how often they occur.

## Measurement

- Every slice: `crew/state/eval/bin/replay-gate.sh <main worktree> <this worktree> --split dev` -> zero unintended
  diffs (first asks unchanged; the recorded corpus has no `state_changes`).
- S5 onward: 3 TRAIN runs on `crew/state/eval/cp0/train_followups/` (crew exports; ~$0.7 each) against the lane
  pinned lane version; report TRAIN follow-up + first-ask numbers with error bars. Held-out is crew's.
- The shared dev DB `production` label is v60 (4 Oct) today, not prod's; the base is read from the 15 Oct prod copy
  after the ~00:30 17 Oct swap, and the lane version is published only after it.

## Open

- Unlinked non-staff contacts seeing a whole company's customers (security review 17 Oct): covered by #1648
  (V1-DOC-SCOPE-SALES, owner-approved; "no links" means none). No ticket from this lane.

- Bench (crew 17 Oct, option a): EVAL-BENCH fixes TRAIN gold, grader and legacy-state rows from the lane's root-cause
  analysis (28 of 34 persistent failures are bench). Lane vs main re-run only after the fixed bench is posted; crew
  opens a new envelope (~$4.2). Round 5 fixes the lane classes (S-OFFER regression, A-HELD-WINS, validation
  strictness) red-first at $0.

- Q3: answered by the owner 16 Oct ~23:00 (R16). S3b built in the S4b/S6 round.
- Unclear default: one named constant `ask` in code, pinned by a test (crew); storage + UI later behind
  CLARIFY-POLICY #1570. The owner's type rule settles an unclear held customer before any ask.
- Owner flag (card example 5): a "when" follow-up keeps a held date filter that may hide the answer; parser judges.
- Security review items: stored last-list pick after a scope change (seam design risk 6).
