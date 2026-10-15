# PLAN: PV2 R6 "clarify + references + contract test" (15 Oct 2026, evening)

Status: Build (code map done; contract test being written). Track M (LEAD pattern, red-first, kill-proof). Branch `crew/pv2-redesign-r6` from d08cd98c3 (R5 head,
PR #1707 unmerged), worktree sorento_crm-pv2-fix2; draft PR stacked on #1707. Brief crew/state/briefs/PV2-FIX-R6.md +
train-only brief crew/state/reports/PV2-R6-train-brief.md (root classes A-E; train only, by design).
No prompt change in R6 (crew option O2 is R7).

## Why
Crew measured R5 3x: train 15->18/55, held-out 6->8/19, fresh 2->2/18 (bar not met). Turns where the parser said
`unclear` or `clarify.needed` failed 72%; R5's pick code works only when the turn BEFORE left a question with numbered
options, and R5's fixtures always started from that shape, which the live parser rarely produces.

## Behaviours (each red test from a parser output with a fresh paraphrase; never battery wording or ids)
0. Live-parser contract test FIRST: replay recorded TRAIN parser outputs (masked: person names fake, business codes kept)
   through the real turn/apply; assert invariants on every turn, never expected replies:
   I1 reask line only when a question was open at turn start; I2 no entity / which-held / domain question with zero
   options when the parse carries an entity, a refers_to_held entity or a domain, or the last answer printed rows;
   I3 a pick never re-prints the same question; I4 every numbered line printed is an option of the stored pending
   question; I5 a product reference is never offered a document number; I6 no UUID / internal key in any PV2-turn reply.
1. Clarify turns are first-class (`act=unclear`): keep customer / domain / period from the same parse; numbered options
   for every question asked; a family confirm that prints accounts offers them as options; no open question -> a new
   ask, never "didn't catch your choice".
2. A pick removes the picked entity whatever its kind tag (`_without_picked`), so the question never comes back.
3. Follow-up references ("this product", "both", "the others", "that one") read a document's LINES, not its number;
   several -> ask with real names.
4. Customer group = one customer: print the company / group name, no account count; never split a group in asks or answers.
5. UUID / id leak, both halves: the bridge sends the product code or label, never a ref key; the id scrub runs on EVERY
   PV2-turn reply, including v1-rendered replies returned through `_answer_actions`.
6. Owner ruling CA-3 Q5(a): an unlinked contact holding chatbot.analytics gets chat reports like staff, a
   sales_orders.sales_report holder gets sales reports: exempt those GRANTED turns from slice G's hold (picker
   included), like v1 #1648.

## Code map (at d08cd98c3)
(paths under sorento_crm_backend/app/services/chatbot/pv2/ unless stated; file:line at d08cd98c3)

### Class A: clarify dead-ends (behaviours 1, 2; invariants I1-I4)
- Flagged branch apply.py:163-184 returns before any `_one_ask`: the parse's entities / domain / period never reach
  held; only `_origin_of(out.asks[0])` survives as pending.origin (:180), which MEMORY never prints and only a pick
  replays (:975-997). `carried` exemption (:158-162) covers about=entity/spec_attribute only; about=domain becomes
  `clarify_domain` with text "What would you like me to check?" (clarify_copy.py:270) and options [] (`_options_for`
  :2042-2047). With a question open, `_miss` re-asks it and the whole parse is dropped (:174).
- `unclear` branch apply.py:635-641: with a question open -> `_Miss`, held restored (:204), period / entity dropped;
  with none -> `_clarify(options=_options_for)` without `_entity_about` (:2036-2039): about=entity -> zero options.
- Option-less asks: apply.py :176-179, :639, :2096-2106 (`_miss(None)`), :616-618 `_which_product` (from :663, :719,
  :763), :787, :950, :1092-1096, :1223-1227, :1232-1238 (report period, reports.py:325-347), :1403-1409, :1784-1786,
  :1661. A free value on these picks only an exactly equal label (:915-920), else `_Miss` (:944).
- "Sorry, I didn't catch your choice" (clarify_copy.py:93-94) via `render(reask=True)` (:329-330, answer.py:86);
  `reask` set at apply.py:2106 (no pending: brand-new question) and :2121-2126, cleared only when `pending_in` is set
  (:209-210). So it prints with NO open question on any non-answer `_Miss` (:654, :694, :724, :734, :755, :1288,
  :1295, :1309, :1417) and with an option-less pending (:2119-2126).
- Family confirm: printed accounts go to `asked` only (apply.py:1612-1615); pending.options = [yes, no] (:1616-1618);
  accounts rebuilt only by `_numbered`->`_accounts` (:328-329, 341-353); `render` skips options for clarify_confirm
  (clarify_copy.py:332); MEMORY prints `1=yes | 2=no` (history.py:169-173), so printed "2. <account>" collides with
  "2=no" (I4). Did-you-mean: printed codes (:1569-1573) differ from option labels; yes/no appended (:1588).
- `_without_picked` apply.py:1981 removes an origin entity only if `e.kind == payload["kind"]`; payload kind is the
  resolver's (resolve.py:271-280, 806); `_stamped` (:1462-1469) does not stamp kind -> entity stays in `rest` (:985),
  re-resolved (:994-997) -> same question (I3). `_repeat_is_a_miss` (:2144-2155) only when the subject is unchanged.
- FIX direction: the flagged / unclear branches run the parse's asks (entities, domain, period kept in held) and ask
  with options built from what the question names (`_entity_about`, shown rows, held refs of the asked kind); every
  question asked stores the numbered options it prints (family accounts as options, did-you-mean labels = printed
  codes); `reask` only when a question was open at turn start; `_without_picked` matches by phrase / stamped origin,
  not kind.

### Step 0 seam (contract test)
- tests/chatbot/pv2/replay.py `run_case` (:243-286) is apply-only with `CannedResolver` (:131-194), no turn.py, no v1,
  no MEMORY. test_r5_g2_no_reach.py `_report_turn` (:98-127) / test_carry_review_r1.py `_hook` (:170-200) run the real
  `turn.run_turn_v2` with `pv2.call.parse_v2` / `resolve_v2_config` stubbed, real DbResolver + apply, stop at the verdict.
- Cheapest full seam: `_go_v2` + tests/chatbot/test_outstanding_lane.py `_run_turn` (:415-505) WITHOUT `fake_turn`,
  monkeypatching `pv2.call.parse_v2` / `resolve_v2_config` (looked up at call time, turn.py:615, 652-653) to pop the
  recorded parses; state chains through `session_vars.pv2` (engine.py:4266-4301) and the register (:4224-4249); the stub
  receives `memory_block` = the MEMORY the parser saw (I1, I4). `_run_turn` fakes MCP (`_capturing_mcp` :280): keep
  real `fetch_services` for the seeded / eval DB variant.
- Invariant seams: I1 ClarifyAct.reask implies state_in.pending; I2 ClarifyAct about entity / which_held / domain /
  other with options [] while the parse carries an entity / refers_to_held / domain or `register.shown(state_in)` is
  non-empty; I3 numbered pending_in + a pick -> output pending (kind, asked, labels) differs; I4 numbered lines printed
  at turn N vs `history.render_memory` (:168-175) at N+1 (the stub's `memory_block`); I5 `_which_held_options`
  (:1794-1802) offers held refs of every kind (a DO number for "this product"); I6 engine `result.reply["text"]` vs
  replay.UUID_RE (replay.py:41) + internal keys (v1 lane text is not scrubbed today, engine.py:4323 covers PV2 direct).
- Fixture: tests/chatbot/pv2/fixtures/r6_train_parses.json (135 conversations, 404 turns, 65 unclear / clarify.needed;
  TRAIN r1-r3 only; persons and companies fake, business codes kept; asker not recorded) + r6_train_world.json
  (7 fake customers, 59 product codes, 12 document numbers) to seed the test DB.

### Class B: follow-up references (behaviour 3; I5)
- Unclear / flagged asks build options from `_options_for` (apply.py:2042-2046) = `_which_held_options(st.held.refs)`;
  the held ref is the DO/SO itself, so its NUMBER is offered. `_line_products` (:1505-1517) runs only at :658-677
  (refine / new_topic with a points-back product entity); a parse with no entity never reaches it; lines None falls to
  nothing-held (:1284-1288, :1294-1295, :723-724, :753-755).
- Lines: `register.document_lines` (register.py:153-163) reads envelope `rows[].lines`, sent by SO cards only
  (lanes/business/__init__.py:645-655, 1500, 1540); DO lines exist only as child rows when `products` is revealed
  (register.py:72-78).
- "both" / "the others": the parser flags which_held -> same branch, held refs only; earlier products sit in
  `st.topics` (pushed :706, :729) and are never read; "the X one" gets held customer refs (one per account, same label;
  state.py:352-354 via apply.py:1794-1802), not the menu just answered; new_topic starts from an empty Held() (:709);
  `_carry` (:542-566) never carries quantity.
- FIX: ONE reference-options function (shown rows + doc_lines, then topics' refs, then the just-answered menu's
  options) used by `_options_for` and :658; a product reference never gets a document ref.

### Class C: customer group = one customer (behaviour 4)
- A containing-name search returns one family; group name != phrase (resolve.py:801) -> ambiguous with `guess`,
  `keys` + `labels` (:803-813); `_confirm` (apply.py:1354-1358, 1608-1619) prints every account (:1612-1615), options
  stay [yes, no] (:1616-1618) while `_numbered` (:328-329) numbers accounts (I4 mismatch); an exact family match gives one
  ref per account (resolve.py:802).
- Follow-up: `_commit` (:815) stores a held with no customer while the confirm is open -> v1 header "Customer: all"
  (`outstanding_customer_echo` lanes/business/__init__.py:346, 403-405). Owner-rule helper: ledger_family.py:232-255
  `group_names` / `family_words`. A domain or period on an open confirm goes to `_miss` (:169-174); `answer_is_a_refine`
  (:475) needs `_is_a_new_ask` (:301-307), which needs a named entity.
- FIX: a single family whose group name matches is ONE customer labelled by the group name (resolve.py:799-802); the
  confirm prints the group name only (no account list, no count); keep the guessed customer on held; a domain or period
  on an open confirm is a new ask.

### Class D: record detail asks re-print the same card (2 train turns; last, if time)
- A detail ask on the same SO is a refine; `_apply_entities` re-resolves the ref, `_commit` (:799-821) makes the same
  Lookup -> v1 SO-NUMBER-ASK (lanes/business/__init__.py:1465-1501) -> `so_status._card` (so_status.py:93-102) prints no
  lines (lines go only to the register, :224). No slot for a detail field (schema.py:112-127; R7 parser work).
  `_repeat_is_a_miss` (:2129-2159) checks questions only; "Carrying on" opener from `_is_current` (bridge.py:111-116) +
  engine.py:4114-4123.
- FIX (narrow): `_card` prints the SO lines; a refine equal to the held answer says plainly the detail is not on record.

### Class E + behaviour 5: ids, both halves
- E1: `_v1_entity` sends `raw` / `canonical_code` = `ref.key` for every kind except a UUID customer (bridge.py:135-138),
  incl. product_type keys, attachment_type (:209), brand keys into v1 focus (:335-336); report drill `drill_row` returns
  live keys (reports.py:236-250), stored as `ResolvedRef.key` (apply.py:791-795), sent as typed text. FIX: send the
  printed code or label whenever the key is not a code (`clarify_copy.printed_code`, as apply.py:1569).
- E2: the scrub runs only in `_pv2_direct_reply` (engine.py:4319-4323); v1-rendered replies leave via `_answer_actions`
  (engine.py:8039, 8370-8386), row closed :8047-8056. FIX: scrub at `_run_stages` (engine.py:4209-4221), which every PV2
  turn passes (answered-ahead too, :1969), after `_run_stages_body` (:4219), before `_pv2_keep_state` (:4220): result.reply
  text + send_message actions, re-writing the row like `_localize_result` (:3093-3115); `media_box["pv2_state"]` gives
  `known_labels`.

### Behaviour 6: owner ruling CA-3 Q5(a)
- Ruling: PLAN-chat-analytics-ca3-chat-dataset-10oct.md:181-184 (an unclassified contact with the grant sees all, like
  staff; the owner gives the grant per contact). v1's exemption (#1648, V1-DOC-SCOPE slice 3, commit 6b46f867c8 on
  `claude/v1-doc-scope-cx3vva`, engine.py:5302-5322; NOT in this HEAD): `granted_report_turn` = every fetch is
  chat_analytics and the contact holds `chatbot.analytics`, or order_status in SALES_FIGURE_STATUSES and the contact holds
  `sales_orders.sales_report`; `unlinked_held = customer_docs_hidden and not granted_report_turn` (picker exempt too).
- R5 refuses at: pv2/hook.py:105-125 (`no_customer_scope` ignores grants), turn.py:571-573, 640-644, 679
  (`_refused_report` :594-602), chat_report.scope_for (:339-340, no relax), engine.py:5437-5441, fetch
  `scope_customer_ids=[]` (lanes/business/__init__.py:1444-1446), resolver no customers (resolve.py:740-742).
- FIX: one granted-report check (grant in `granted_reveals` and the report dataset is chat or sales), applied in
  `pv2_hook.customer_scope` and at turn.py:644 before `held_to_no_customer`; no grant -> held as R5.

## Lead rulings
- Confirm vs group (15 Oct): the owner rule wins. A family confirm prints the group name only (no account list, no
  count) with options yes / no, so MEMORY's numbering equals the print (I4). An account is picked only when the user
  names one (R5-3). test_r6_a passes either way; test_r6_c pins the group-name-only print.
- Q5(a): R5 hold tests g7, f4_1, f4_2, f4_3 re-scoped (setup only, 9cbbb40441) to an unlinked contact WITHOUT the grant.

## Slice order (red commit, then fix commit, each)
0 contract test (red) -> A (clarify first-class, options stored, reask only with an open question, `_without_picked`)
-> B (reference options) -> C (group = one customer) -> E (bridge codes + scrub at `_run_stages`) -> Q5(a) -> D (if time).

## Gates
Touched tests -n 2, contract test green, reviewer READY, security CLEAN; crew report --done; crew runs the battery.
