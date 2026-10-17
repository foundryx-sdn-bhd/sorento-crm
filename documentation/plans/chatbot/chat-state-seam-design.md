# CHAT-STATE seam design (coder-facing; the tester works from the UAC + test list, not this file)

Plan: `PLAN-chat-state-16oct.md`. Paths under `sorento_crm_backend/app/services/chatbot/` unless noted; code read at
b6b4fa740 (origin/main e46d0b0ae + docs). Assumes card answers Q1(a), Q2, Q5 (crew) and Q3(b), Q4(a) (owner, pending).

## 0. Shape

- New pure module `turn/chat_state.py`: `view(state, open_question)`, `render(view)` (the fixed block),
  `apply_changes(...)` (validate + carry). `turn/tail.py::last_list_after(...)` builds the last list.
- One new nullable parse key `state_changes`. One non-serialised flag `State.state_led`
  (`"answer" | "new_ask" | "refine" | "carry" | None`), set once at one engine seam.
- Last list rides inside `focus` (precedent `Focus.tasks`, `turn/state.py:191-198`: "a second key could disagree").
  No migration; the SessionVars five-key shape is unchanged.
- Carry vs writes: the change list owns CARRY (which parts of the prior state survive). WRITES stay as today: this
  message's values onto axes (`_focus_rules` write half) and what this turn's answer showed/asked
  (`sales_ranking_frame`, `set_clarify_carry` engine.py:5702, `record_top_selling_asked`, `set_page`, `last_list`).
- Gate: off unless the parser prompt in use asks for `state_changes`. Prod label + replay corpus run today's path.

## 1. Schema (`head/parser.py`)

- `_build_json_schema(state_changes: bool = False)` (:85-676). `PARSE_OUTPUT_JSON_SCHEMA` (:678) unchanged, so
  `DECLARED_KEYS` (:683) and schema guard tests stay green. New `PARSE_OUTPUT_JSON_SCHEMA_WITH_STATE`.
- Key last in `properties` and `required` (strict mode); nullable; also in TOLERATED_ABSENT (:708).

```
"state_changes": {"type": ["array","null"], "maxItems": 8,
  "items": {"type":"object","additionalProperties":false,
    "properties": {
      "op": {"type":"string","enum":["keep","new_subject","set","clear","pick","answer","switch_domain","close_question"]},
      "axis": {"type":["string","null"]},
      "position": {"type":["integer","null"]}},
    "required":["op","axis","position"]}}
```

- `axis` is a free string validated in code: fixed axes product, customer, warehouse, brand, tier, channel, period,
  status, sort, quantity, plus every `policy.kinds` kind (operator-edited table, same reason `domain` is free,
  parser.py:377-380).
- No `value` key: an axis's value is the message's existing parse field (one value, one place; grounding and the
  resolver still check it):

| axis | parse field |
|---|---|
| product / customer / warehouse / brand / other kinds | entities[hint=kind] |
| tier | access_levels + tier entities |
| channel | sales_channel |
| period | date_mode, date_filter_start, date_filter_end |
| status | status / order_status / document |
| sort | sort_by / sort_dir |
| quantity | demand_qty and entities[].quantity |

- A domain is always the message's own ask (domain_hint / asks). `null` = prompt did not opt in; `[]` = parser could
  not tell (R11, slice 6).
- Gate: `ParserConfig` (:74) gains `state_block: bool`; `resolve_config` (:750) sets it to
  `"state_changes" in system_prompt` (a literal on the prompt, never the message). `parse()` (:1047) sends WITH_STATE
  only when on. Lane runs reach the prompt via `override_version_id` (engine.py:4485). Engine reads the field only
  when `parser_config.state_block or parser_bypassed` (dry-run `mock_reformulator_output` tests at $0; ignores a
  stray key from the Anthropic path, engine.py:4818).

## 2. The state block

`chat_state.render(view)`, always four lines, "none" when empty:

```
State:
- subject: domain <d>; customer <names>; product <codes (xN)>; brand <..>; tier <..>; channel <..>; period <start> to <end>; status <..>; document <..>; sort <by dir>; report <top_selling/sales_ranking_frame/low_stock keys>; relation <..>
- open question: {"kind":..,"options":[{"position":1,"code":..,"label":..}],"owed":[..]}   (or: none)
- last list (<domain>, <n>): 1 <code or label>; 2 ...; n ...   (or: the open question's options / none)
- pending task: <stock_qty lines as task.open_question items, owed marked>   (or: none)
```

- Subject = every Focus axis incl. brands, tier, sort, channel (today's `current_subject_line` parser.py:814-853 skips
  them). Open question = same object as today, `question.open_question(...)` (question.py:129), plus the one-turn
  slots not in it today: `focus.set_clarify`, `focus.set_page.other_brands`, `focus.top_selling.asked`.
- A roster that is both the open question and the last list prints once. The task line replaces "Open task" and
  "Last answered" (task.py:411-417): a delivered answer is no longer shown twice.
- Labels only (name, raw, code, label); never `uuid`/`uuids`. Never "(and N more)".
- Wiring (slice 5): `turn/context.py::ContextLayers` (:195-233) gains `state_block: str | None = None`. `_render_l2`
  (:377-432) and `_assemble_off` (:457-493), when set, print the brands/kinds line then `state_block` instead of the
  five lines Current subject, Open task, Open question, Pending, Open question options. The shrink loop (:425-431)
  only trims `subject_text`, now None, so the block can never be cut. engine.py:4507-4537 passes
  `state_block=chat_state.render(chat_state.view(state_in, open_question))` when `parser_config.state_block`, keeps
  the text for the log; `open_question` stays the one source for the block and `without_phantom_answer` (:4565).

## A. Seam: engine.py between :4688 (`turn_trace.add("context")`) and :4697 (`with_routing_agent_default`)

After the parse, its assert (:4545-4560) and the phantom-answer drop (:4565-4570); before every post-parse rewrite
(:4697-5060), grounding (:4747), required-ask/scope/report readers (:4814-4857), APPLY (:5063), `lane_parse_output`
(:5070), resolver input (:5316) and both APPLY re-entries (:5634, :5684). All read `state_in`/`verdict`; writing the
change into those two once makes the re-entries idempotent.

```
applied = None
if verdict.get("state_changes") is not None and (parser_config.state_block or parser_bypassed):
    applied = chat_state.apply_changes(
        state_in, verdict, open_question=open_question,
        domains=[...policy domains], kinds=[...policy.kinds], brands=brands,   # brands loaded at :4491
        positions_read_elsewhere=_positions_read_elsewhere(state_in))          # :2661
    remembered_before["_chat_state"] = applied.log   # shown block, before view, changes, rejected
    if applied.valid:
        state_in, verdict = applied.state, applied.verdict   # applied.state.state_led set
state_led = state_in.state_led is not None
answering = state_in.state_led == "answer"
```

- `remembered_before` already carries "_" keys (`_contact_pk` :4300); `write_merged` (tail.py:141) iterates payload
  keys only. The applier pops `state_changes` from the verdict; the raw stays in `parser_raw` (:4663).
- Invalid change (until slice 6): state and verdict untouched, today's path runs, rejection logged.

### A2. `apply_changes` (pure; turn/ rules: no regex, no `.text`, no head/tail imports; covered by
`test_rearch_s2_apply_is_pure.py`)

Validation: at most one of keep / new_subject; `set`/`clear` axes known and not repeated; `set` needs the message's
own field for that axis non-empty; `pick` positions inside `focus.last_list`; `answer` needs an open question and a
non-null `open_question_answer.mode`; `switch_domain` needs domain_hint/asks inside policy domains; `close_question`
with nothing open is a no-op.

Carry, on a deepcopy of the focus, keyed off the op, never the message:
- keep: nothing dropped.
- new_subject (R4, R6): rebuild Focus with only customers, RESET_KEEPS (tier, brands; apply.py:69), tasks (parked by
  the task step, D22) and last_list. Close pending (Q4a), required_ask, set_clarify. Drop `sales_ranking_frame`
  (so #1721 step 7 asks the basis). Period dropped (Q3b).
- clear axis: drop that axis.
- switch_domain, or a domain_hint differing from the held domain (implied switch, logged): `focus.domains` = new;
  drop domain-bound axes status, document, sort, top_selling, sales_ranking_frame, low_stock, set_page; keep
  products, customers, brands, specs and the held period (R2, R16: the period carries unless skip/clear period). Replaces today's wipes at apply.py:1173-1186, :1200-1213, :1222-1232.

Verdict edits (deterministic):
- (a) No `answer` op and no roster pick: `question.without_phantom_answer(verdict, open_question=None,
  positions_read_elsewhere=..., focus_has_product=False)` (question.py:152) drops `open_question_answer` and
  `reference_positions` (ideation media menu and top-selling positions exempt as today). With `answer`: untouched.
- (b) REVERTED 17 Oct (lead ruling after the live diagnostic): current-message entities and stated scalars always
  apply as stated; ops govern only the carry of held parts. Original text: entities kept only for kinds named in a
  `set`, every kind on `new_subject`, and the answered slot's kind on
  `answer` (R5; the kind is the open question option's entity_type, needed for the label match decide.py:139).
  Scalar fields of axes not set are nulled, so the carried focus value is the lane default (`outstanding_carry`
  :3437-3445, `_spec_window`).
- (c) Entities with `current_message: false` (restatements of carried rows) are always dropped.
- (d) R3/R8: a `set product` replacing exactly one held product keeps that row's quantity when the message stated
  none (#1617).
- (e) Pick: section B.
- (f) close_question over the stock task with no other subject op: `open_question_answer = {mode: "cancel"}`
  (reuses `_open_question_answer` cancel arm, apply.py:2899-2902).

Output: `state.state_led` = most specific of "answer" (answer op or roster pick), "new_ask" (new_subject), "refine"
(set, clear, data pick), "carry". `log = {shown, before: view(state_in), changes, rejected:[{op, reason}]}`.

### A3. Carry deciders skipped when the field is present

| Decider | With state_changes | Edit |
|---|---|---|
| decide._subject_reading / _keeps_subject (decide.py:499-616 / :371) | bypassed | apply.py:3420-3421: `if state.state_led not in (None, "answer"): decision = replace(decision, kind=state.state_led, why="state_changes")`; why="state_changes" makes `starts_fresh` (decide.py:114-130) False so the eviction at apply.py:1109-1170 cannot fire. On "answer", decide() runs as today |
| apply._focus_rules carry half (apply.py:1020): topic_reset wipe :1030-1039, sort drop :1173-1186, date drop :1200-1213, document/status wipe :1222-1232 | skipped | kwarg `carried: bool = False`, passed `state.state_led is not None` at both call sites (:3460 reconcile short-circuit, :3502 main) + four `if not carried` guards. Write half (:1041-1106, :1188-1199, :1233-1257), `_broaden` (:1215), `_top_selling_rules` (:1258) stay |
| apply new_ask_closes_stale_roster (:3817-3846), stale_roster_closed (:3361) | keep | none |
| apply `_open_question_answer` :2814, `_open_pick_answer` :2937 | keep (reused answer path) | none |
| turn_runtime.with_carried_entities (:4825), outstanding_carry (:3360), lane_parse_output (engine :5070) | keep (they project the focus, now the authority) | none |
| engine._with_the_engine_pick (:801, call :4973-4987) | skip | `if not state_led and ...` |
| engine._top_selling_verdict (:2872, call :4862-4880) | skip | `if not state_led:` around the call (keep `in_ranking_conversation`) |
| order_list.order_list_verdict (call :4885-4890), so_status.carried_status_verdict (call :4905-4914) | skip | guard each |
| slot readers with_company_pick :4706, with_clarify_answer :4713, _with_account_answer :4716, with_set_count_from_text :4717, with_brand_from_offer :4722 | run only when `not state_led or answering` | one `if` around :4706-4722 |
| everything else at :4697-5060 (routing default, document kind, team escalation, without_restated_rows, with_new_set_words, grounding, set leg, ENGINE_KEYS strip, scope_reply, take_words readers, typed_so_numbers, so_list_verdict, typed_words_verdict, dealer stock-word fixes, roster-domain pick effects, seat link) | keep (step-2 candidates where they read raw text) | none |

Edit budget: state.py 1 field; apply.py 1 override, 1 kwarg at 2 sites, 4 guards; engine.py seam (~12 lines), ~9
one-line guards, block pass-through; move `_answer_pending`'s option-to-entity builder (apply.py:795-850) into a
helper `option_entities(option)` (turn/pending.py), no behaviour change.

### A4. Held slots (owner 16 Oct ~22:45; replaces the static per-domain projection)

The static R14 projection (`engine._without_slots_the_domain_does_not_take`, reading `chatbot_domains.narrowing`) is
removed. New ops, axis `customer` or `period` only: `keep` (applies to this ask), `skip` (not applied this ask, stays
in the stored focus), `clear` (dropped), `unclear` (policy default: `ask` -> one real-name question as an open
question whose answer fills only that slot; `assume` -> applies). A held slot with no statement applies as today.
`skip` needs the same projection point S4 found (after `with_carried_entities` ~engine.py:5372) but driven by the
op, so the tail still writes the full focus. The ask reuses the existing clarify/question composer (find the one that
asks "same or all" today) rather than a new answer path. The default lives behind one function / named constant until
the owner picks the storage.

## B. Changes onto existing consumers (no new answer path)

| Op | Becomes | Consumer |
|---|---|---|
| answer (pending pick/offer) | verdict untouched | `_open_pick_answer` (apply.py:2937) -> `decide.picked_positions` (decide.py:237) -> `_answer_pending` (apply.py:722) |
| answer (stock task) | same | `_open_question_answer` (apply.py:2814) -> `task_mod.run` |
| answer (required ask) | same | `required_fields.reply_verdict` (required_fields.py:322-363) |
| answer (set_clarify / set_page offer / account / company) | slot readers run (`answering`) | unchanged (their text reading is step-2 debt) |
| no answer op | `without_phantom_answer` | readers see "not an answer" |
| pick n, last list = open roster | `open_question_answer = {mode:"pick", picked:[n], items:[], qty_for_all:<parser's>}`, `reference_positions=[n]` | same as answer |
| pick n, data list | append `option_entities(item)` (`{raw: code, hint: kind, canonical_code: code, current_message: True, confident: True}`), drop positions, pending untouched | `_set_kind_field`; resolver settles a code like a typed code; domain carries (R9) |
| set axis | keep the message's field | `_focus_rules` write half |
| clear axis | drop axis, null verdict field | lanes read the focus default |
| new_subject | applier rebuild | NEW_ASK via override; parser's own `topic_reset` left as emitted for task close/park (task.py:556) and episode close (engine.py:5827) |
| switch_domain | `focus.domains` set, domain-bound axes dropped | plan domains apply.py:3586-3589 |
| close_question | pending / required_ask / set_clarify None, or mode "cancel" over the stock task | cancel arm apply.py:2899-2902 |

## C. Last list

Sources today: picker options (`_reply_of` engine.py:8193-8206, `compose_question` compose.py:1105-1161,
`top_selling_pick` pending.py:108-144, did-you-mean compose.py:405-460, rosters in answer_bridge.py); data answers
numbered by `lanes/business/fetch.py::output_structurer` (:3382, :3450, :3490), exposed as `answers` in render order
(:3697-3703, `_rendered_answers` :1528-1541) -> envelope `figures` (turn_runtime.py:5585-5586, :5620) ->
`Answer.sections[i].figures` (compose.py:530).

`turn/tail.py::last_list_after(previous, *, question, sections)`, pure:
- question options keep position, label, code, entity_type, uuid(s), payload value;
- plus rows of the single section with figure rows: position = index+1, label/code from "Product Code" (same rule as
  `compose._codes_without_rows` compose.py:73-93), kind "product"; else title / first value, kind None;
- position collision: question options win; several sections with rows: record none (clear) and log;
- no list shown: keep `previous` (Q4a).
Called from `tail.session_payload` (tail.py:67-88) after `carry_live_state`, and `engine.run_tail`'s payload
(engine.py:10155-10178, `sections=[]`). Stored as `Focus.last_list: list[dict] | None` in turn/state.py:132 +
`focus_to_wire`/`focus_from_wire` (:309-404, tolerant read) AND `contracts.Focus` (contracts.py:673-742, extra=forbid;
`SessionVars(**payload)` validates at engine.py:10179: same slice). v1: only rows with a typed code are pickable.

## E. Risks (each needs a test or a note)

1. Domain-bound carry leaks once today's wipes skip (status/period/sort into a new domain; #1262 R2, ORDER-BROADEN):
   switch / implied switch must drop them.
2. Restated rows revive the old subject after new_subject (`without_restated_rows` engine.py:4728 compares against
   the new focus): drop `current_message: false` entities.
3. Full lists invite echoing rows past PARSER_MAX_TOKENS 2048 (parser.py:41; why the subject is capped at 5 names,
   parser.py:836-840): prompt rule against restating, echo drop, watch completion_tokens on TRAIN.
4. Roster-only side effects lost when pending closes on a new ask (AC-1704 status carry apply.py:892-894,
   answered_positions, outstanding scope, tier payload): v1 picks only typed-code rows from the data list.
5. A picked spec bucket runs through `_as_typed(message)` (grounding.py:776-790) and can come back "Couldn't find":
   keep spec buckets unpickable in v1.
6. Multi-company: a stored list picked after a membership change reuses a stored key; confirm the injected entity
   passes `_customer_scope_gate` (:5372). Security review (SEC-MCP-AUTH #1742).
7. `_reconcile_step` short circuit (apply.py:3450-3469) also calls `_focus_rules`: pass the flag. `state_in` is
   reassigned (:4835, :4843, :4932) and mutated (:4822, :4857): applier returns fresh objects.
8. Forced Decision changes which arm answers (`_answer_outstanding`, `task_mod.run`): intended, measured on TRAIN.
9. `is_affirmative` not dropped; prompt must say "no" to an offer is an `answer`.
10. Quoted reply: change applies to the quoted state (engine.py:4268); `carry_live_state` (tail.py:19-64) may restore
    the live focus; logged.
11. contracts.Focus extra=forbid (contracts.py:682): `last_list` ships with the wire change. Concurrency merge
    (tail.py:141-213) treats focus as one key (last write wins, as today).
12. One-turn questions outside today's open question (set_clarify, set_page.other_brands, top_selling.asked,
    ideation.pending_media) must render in S1 or `answer` can never be declared for them.
13. Slot readers still read raw text to find WHICH option under the parser's `answer`: step-2 debt.
14. Apply `state_diff` (engine.py:5894) hides the applier's change (`memory.state.before` covers it). Episode close
    (engine.py:5818-5843) keys off topic_reset/domain change, so a same-domain new_subject closes no episode: logged.
