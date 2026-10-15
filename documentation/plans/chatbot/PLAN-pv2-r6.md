# PLAN: PV2 R6 "clarify + references + contract test" (15 Oct 2026, evening)

Status: Plan. Track M (LEAD pattern, red-first, kill-proof). Branch `crew/pv2-redesign-r6` from d08cd98c3 (R5 head,
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

(Classes B-E + Q5(a): mapping pass in progress)

## Gates
Touched tests -n 2, contract test green, reviewer READY, security CLEAN; crew report --done; crew runs the battery.
