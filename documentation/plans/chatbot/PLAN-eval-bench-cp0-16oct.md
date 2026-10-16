# PLAN: EVAL-BENCH CP0, the bench check against the owner's targets (16 Oct)

Status: Review, CP0 baseline done on 3 runs (17 Oct, spend $11.96 of $12); review page with crew, then the owner. Parent: `PLAN-eval-bench-16oct.md`. No real customer data in this file; real items,
gold and the review page live under `crew/state/eval/` (crew-only).

## The owner's bar (crew relay, 16 Oct)

One build, on a held-out crew-only set, over 3 runs:

| target | bar |
|---|---|
| follow-ups (refine / pick from list / reply to the bot's question / topic switch / "and X?") | >= 90% |
| spec matching | >= 90% |
| attachment type matching | >= 95% |
| code lookups | >= 97% |
| scope leaks | 0 |
| made-up facts | 0 |

Checkpoints: **CP0** bench check (this plan: baseline per slice + 30 real conversations with the bench's verdict
so the owner can confirm the grader), CP1 follow-ups, CP2 spec + attachment types. The owner hand-tests only when
one build meets every bar.

## Slices and how each is graded

Every pass rule reads the persisted turn (`chatbot.turns.trace` + reply), so grading costs $0 and is the same on
every run. Field names: `apply` event `decision.kind` in {answer, refine, new_ask, carry} with `why`
(`app/services/chatbot/turn/decide.py:41-44, 132, 606`), `focus`/`open_question` events
(`app/services/chatbot/trace_detail.py:245, 259`), tool events `{kind: tool, name, args, envelope}`
(`lanes/business/__init__.py:2415-2423`), grounded entities with `hint`, `canonical_code`, `spec_key`,
`spec_value` (`head/grounding.py:268-276`), `access` stage raw (`head/access.py:131-156`), `customer_scope`
event (`engine.py:5550-5558`).

| slice | an item is | pass when |
|---|---|---|
| code lookup | a staff ask naming one code + one fact (stock, ETA, files, price, order) | grounded code = gold code, fact = gold fact, a tool ran for that code, the reply names the code, no clarify (unless gold says the code is ambiguous) |
| follow-up: pick from list | "2", "the second one", a code from the bot's last list | `decision.kind = answer`, picked position = gold, the tool targets the gold item |
| follow-up: reply to the bot's question | answer to an open question (option, yes/no) | `answer` against the open question, resolved to the gold option, the same question is not asked again |
| follow-up: refine | same subject + a new filter | `decision.kind = refine`, subject kept, new filter = gold |
| follow-up: topic switch | a new subject mid-conversation | `new_ask`, the old subject is not carried into the plan |
| follow-up: "and X?" | same fact, new item | `new_ask` with the fact carried and the item replaced by X |
| spec matching | an ask by spec words (incl. typos, synonyms) | grounded `spec_key`/`spec_value` set = gold set; every product listed satisfies the gold spec |
| attachment type | a file ask by type (drawing, catalogue, installation guide, ...) | requested type = gold type (`attachment_types.code`), the files sent are of that type, or an honest "none on record" when gold says none exists |
| scope leaks (0) | every turn of every slice + a non-staff slice (dealer / unlinked test contacts) | the reply shows no customer outside the asker's linked customers, no field hidden for the asker, no internal id |
| made-up facts (0) | every answered turn | every product code, quantity, date and container number in the reply appears in that turn's tool envelopes or the user's own message; a turn whose envelope was truncated (8 KB cap, `trace.py:71`) is re-checked against the DB, never silently passed |

Each slice is graded at turn level; a conversation with several scored turns counts each turn once.

## Items

- **Seeds**: real conversations from the owner's 15 Oct prod backup (`sorento_prod_20261015`, read-only
  `BEGIN READ ONLY ... ROLLBACK`, no copies), picked per slice by `tooling/corpus/refresh.py` shape and trace
  fields. Corpus volumes (17 Apr-18 Sep): code-less follow-ups 1,770, number picks 489, short answers 423,
  follow-ups that repeat a code 1,146, follow-ups that switch code 5,502 (corpus report 4d).
- **Gold**: two independent LLM label passes + crew adjudication; disagreements beyond crew go to the owner, at
  most 5 a week. Gold for "none on record" and spec truth comes from the DB, not from a label.
- **Paraphrase variants** per seed, same gold, in the owner's style: typo (one edit in a non-code word), short
  forms mined from real staff messages, word order (`SRTX stock` / `stock SRTX`), Malay, Chinese.
- **Split** by conversation: held-out 60% crew-only, train 40%; lanes see train failures only. CP0's 30 review
  conversations come from train, so the held-out set stays untouched.
- **Sizes (held-out)** and the precision they buy (95% binomial half-width at the bar):

| slice | seeds | with variants | half-width at the bar |
|---|---|---|---|
| code lookup | 150 | 450 | +-1.6pp at 97% (+-2.7pp on seeds alone) |
| follow-ups, 5 kinds x 40 | 200 | 600 | +-2.4pp pooled at 90%; +-5.4pp per kind |
| spec matching | 80 | 240 | +-3.8pp at 90% |
| attachment type | 60 | 180 | +-3.2pp at 95% |
| non-staff scope | 40 | 40 | zero-tolerance count |

  Variants are correlated with their seed, so the real precision sits between the two columns; the report
  prints both n.

## Runs

- **State, not live history**: a follow-up item carries the bot state recorded at that turn in prod (session vars,
  focus, open question, last list) and is fed through the engine's harness keys, so only the scored turn calls the
  parser. Replaying history live would let one bad early turn change the item (the E2E runner does this today,
  `scripts/chatbot_eval/runner.py:87-94`).
- **3 runs of one build**, each with provenance (D1); a run without provenance is refused.
- **Meets** = for every slice, the mean over 3 runs >= the bar, and 0 scope leaks and 0 made-up facts in every
  run; the page also prints the lowest run and the 95% CI.
- **Cost**: ~1,500 scored turns x ~$0.0055 per parser call = ~$8 per run, ~$25 for 3 runs on the full set; CP0's
  baseline can run on seeds only (~530 turns, ~$3 per run, ~$9 for 3).

## CP0 review page (owner, phone, 15 minutes)

Opened by crew in Lavish (local, never published; real conversations). One column, large type, no tables wider
than the phone.

1. **The bar today** (about 1 minute): six rows, each "target, today's main (3 runs), bar, gap", plain words and a
   colour dot; the follow-up row opens to its five kinds.
2. **Does the bench judge like you?** (about 12 minutes): 30 real conversations, 25 seconds each. Mix: 6 code
   lookups, 10 follow-ups (2 per kind), 5 spec, 5 attachment type, 4 scope/made-up checks; about two thirds the
   bench passed and one third it failed, borderline ones included. Each card: the last 2-3 messages as chat
   bubbles, the bot's reply (trimmed to 6 lines), the bench verdict (tick or cross + one plain sentence why).
   The owner annotates only the cards he disagrees with; no annotation = agree.
3. **What happens next** (about 1 minute): 27+ of 30 agreed = the grader is trusted for CP1; each disagreement
   becomes a grader fix (or a gold fix) before CP1, re-checked on the same card.

## Build steps (each red-first; LEAD pattern)

1. `refresh.py` classifiers + seeds per slice from the prod backup (crew tooling; S5).
2. Gold labelling (2 passes) + paraphrase generator.
3. Grader module (repo, `scripts/chatbot_eval/targets.py`): one pure function per slice over a turn row, unit
   tests on synthetic rows; it reuses the replay driver's turn loading (S3).
4. Runner mode that feeds recorded state (harness keys) and scores the targets; 3 baseline runs on main.
5. The review page generator; 30 cards picked by a fixed seed from train.

## Crew answers (16 Oct)

Q1 staged spend: run 1 first (~$3), check the bench is sane (scoring bugs, flip rate), then runs 2-3 without
asking again; about $12 total; post the spend per run. Q2 (a) growth line beside each bar. Q3 (a) recorded prod bot
state, only the scored turn live. Q4 (a) mean of 3 runs >= bar per slice, 0 leaks and 0 made-up facts in EVERY run,
page shows the lowest run + CI. Q5 (a) review conversations from TRAIN. Drawings: crew checks whether drawing files
are filed under Technical Specifications; cards 02/03 stay out of the gold until then. The mock goes to the owner
only at CP0 with real numbers. Baseline runs on the restored prod DB (`sorento_prod_20261015`) once crew says it is
live.

## Attachment gold rule: drawings (crew data check, 16 Oct)

There is no separate drawing type: `technical_specifications` ("Technical Specifications / Spec / Drawing by
Marketing"; synonyms include drawing, technical drawing, TD, CAD, 图纸, 尺寸图, lukisan; 988 of 989 files sit under
the Marketing technical-drawing folders) IS the drawing type. Gold for a drawing ask = that product's Technical
Specifications files, or "none on record" when it has none. Six products have their `*_TD` drawings typed Product
Photos (SRTSCBD333-UF, SRTUB206-BI, SRTUB6201, SRTUB6503-BI-NEW, SRTUB668, SRTUB668-BI): they are kept out of the
drawing items.

## Grader contract (`sorento_crm_backend/scripts/chatbot_eval/targets.py`, pure, stdlib only)

- `grade_turn(row, item) -> dict`. `row` = a `chatbot.turns` row (recorded or replayed: `envelope`, `trace`,
  `response`, `branch_kind`). `item` = `{item_id, slice, kind, message, gold, asker}`; `slice` in {code_lookup,
  followup, spec, attachment, scope}; `kind` (follow-ups) in {pick, answer, refine, switch, and_x}; `gold` holds
  only the keys the slice needs: `code`, `fact`, `position`, `option`, `filter`, `subject_after`, `spec` (list of
  `{key, value}`), `attachment_type` (code), `none_on_record` (bool), `ambiguous` (bool); `asker` =
  `{audience, linked_customer_ids, hidden_fields}`. Returns `{item_id, slice, kind, passed, reason, checks,
  leaks, made_up}`: `checks` = name -> bool for each rule of the slice, `reason` = one plain sentence for the
  review page (no ids, no internal field names), `leaks` / `made_up` = lists of short findings (empty = clean).
- `scope_leaks(row, asker, watch_customer_names=()) -> list[str]` and `made_up_facts(row) -> list[str]` run on
  every graded turn. `asker` also carries `linked_customer_names`; `item.watch_customer_names` (other customers'
  names, filled from the DB when items are built) is what a leak is looked for, since recorded tool rows carry no
  customer names. Trace locations (checked on 2,500 real rows): positions in `apply.verdict.reference_positions` /
  `open_question_answer` and the `engine_pick` event; open question before = `state.open_question`, after =
  `memory.open_question`; sent attachment types = tool envelope `attachments[*].attachmentType`.
  Made-up rule: every product-code-like token, quantity, date and container number in the reply must appear in the
  turn's tool envelopes or the user's message; list ordinals ("1.", "2.") are not facts; numbers and dates are
  compared after normalising (`1,200` = `1200`, `6 Sep 2026` = `2026-09-06`); a truncated envelope makes the
  finding `unverifiable: <token>` rather than a pass.
- `summarise(results, bars) -> dict`: per slice and follow-up kind: n, passed, rate; leaks and made-up counts;
  `meets` per the crew rule (mean of runs >= bar; 0 leaks and 0 made-up in every run).

## Ideas follow-ups (crew, 16 Oct; from IDEA-CAPTURE #1625, crew-ask 13 Oct 07:31Z)

- **G1, scored** (follow-up kind `answer`, ideas lane): a short non-number reply ("yes", "2026") to the bot's
  similar-ideas list keeps the held idea. Pass: `session_vars.ideation` held before the turn
  (`received.raw.session_vars`; read path `lanes/ideate.py:72-83, 157-161`) is still the same idea after it (no
  `remembered.raw.session_patch` clearing or replacing it), and the bot does not restart with its "what's your
  idea?" opener (today's failure).
- **G2, growth slice** (new behaviour waits per owner D6): a narrowing follow-up right after an idea is created
  ("only for the Penang branch" after IDEA-9003) must not get "what's your idea?". Reported beside the follow-up
  bar with its own n, never inside it.
- Seeds for both come from real ideas-lane conversations in the prod backup; paraphrase variants as for every slice.

## Owner probes on #1720 (crew, 16 Oct): scored as real owner items

Source: `crew/state/reports/CHAT-ADVISOR-SPIKE-owner-probes-1720.md` (rows 1-6, root causes). Recorded turns on the
dev DB (`chatbot.turns`, console, 16 Oct 04:01-04:03Z, #1720 copy), so the follow-up items replay the recorded state:

| probe | turn | slice / kind | gold (what must be understood) |
|---|---|---|---|
| 1 | 8e05309c | spec (typo "undermunt") | class basin + mounting under_counter, fact stock |
| 2 | 9bbad3cc | follow-up `and_x` (new fact, subject carried) | subject = probe 1's undermount basins, fact promotions |
| 3 | 78e4b48d | spec | class basin + mounting under_counter, fact promotions |
| 4 | 77b33ba8 | spec | class basin + mounting under_counter, fact stock |
| 5 | 0df37f38 | follow-up `refine` (brand added) | subject kept, filter brand Sorento; no stray `fits` filter |
| 6 | c51d083c | spec (typo "undrmount", "i mean" correction) | class basin + mounting under_counter + brand Sorento, fact stock |

- `and_x` covers both short forms the owner uses: "and <item>?" (fact carried, item replaced) and "<fact>?" right
  after a lookup (subject carried, fact replaced); gold says which.
- These six are known to lanes (the report is in their briefs), so they cannot measure generalisation: they count in
  their slice rates every run, carry `source: owner_probe`, and the CP0 page lists each one with its verdict.
  Probe 2's outcome for this contact is an access ask (0 access types: data, not code); the follow-up is graded on
  what was understood, the dead-end wording is a separate lane (CHAT-PROMO-NOACCESS).

## Recorded-state runner contract (branch `crew/eval-bench-cp0`, stacked on #1743)

`sorento_crm_backend/scripts/chatbot_eval/replay.py` gains a live mode, reusing the replay seams:

- `live_rows(items, recorded_rows, *, session_factory, max_calls, max_usd, on_row=None) -> list[dict]`; CLI
  `replay.py live --items FILE --recorded FILE --out FILE --max-calls N --max-usd X`.
- Per item: the recorded prod turn row gives the state (session vars + `previous_conversation_state` through the
  dry-run harness keys, as in replay) and the envelope; the item's own `message` (seed text or a paraphrase variant)
  replaces the recorded message text; a fresh message id per (item, run); `dry_run=True`; NO
  `mock_reformulator_output`, so the parser runs live.
- Model calls are allowed (they are part of the bot) and counted: a pass-through wrapper on `llm_provider.*Provider
  .chat` records calls and tokens and prices them; the run stops cleanly at `max_calls` or `max_usd`. MCP tools are
  NOT patched (they hit the live stack named by the env). The network guard allows localhost and the model host only.
- The DB is never written: the same rolled-back outer transaction as replay.
- Output per item: `{item_id, run, provenance, usage: {calls, prompt_tokens, cached_tokens, completion_tokens, usd},
  turn: {envelope, trace, response, branch_kind}}`, the full turn (crew-only file), so `targets.grade_turn` can grade
  it; replay's ids-only rule does not apply to this crew-only output, but stdout/stderr stay text-free.

## Grader v2: sources that exist in real traces (16 Oct, measured on 723 recorded prod turns + current-code eval traces)

Grader v1 read `apply.verdict.entities[*].canonical_code`; real product entities carry it on 1 of 484 (prod) and 7 of
288 (eval). Sanity run on recorded prod turns: code_lookup 0/246, made-up 674 + unverifiable 793. v2 sources:

| check | source (first that exists wins; codes compared after upper + strip non-alphanumerics) |
|---|---|
| code / subject after | `memory` event `focus.after.products[].canonical_code`; else `looked_up.raw.envelopes[].entities` / `product_codes` (strings); else verdict entity `canonical_code` or `raw` |
| subject before | `state` event `focus.products` (or `memory.focus.before`) |
| fact | routed/understood domains, `apply.plan.domains`, named tool names, `memory.focus.after.domains` |
| tool target | named tool events only (`name` set); `product_ids` etc. mapped uuid -> code through the state + memory product lists; literal `scope_terms` / `product_code(s)` |
| pick | `engine_pick.positions`, else `verdict.reference_positions`, else `verdict.open_question_answer.picked` |
| answer | the question = `state.open_question`; answered = `open_question_answer.picked`/`mode`, `is_affirmative`, `continuation`, or `memory.open_question` before -> after changed or cleared; `mode` is not required |
| refine | verdict entities (`spec_key`/`spec_value`; brand `canonical_code`) + `memory.focus.after.extra.specification` / `brands`; subject kept = after products |
| switch | `memory.focus.before` vs `after` products / domains (not set equality with gold) |
| spec | verdict entities with `hint == specification` (`spec_key`, `spec_value`, `raw`) + `memory.focus.after.extra.specification`; match on value strings, key may be empty |
| attachment | verdict entity `hint == attachment_type` (`canonical_code`), `memory.focus.after.extra.attachment_type`, attachment tool `attachment_type_ids`, envelope `attachments[].attachmentType` |
| made-up | sources = user message + bot's previous message + every tool envelope + every `looked_up` envelope field + state/memory product codes + the `prompt_text` event; any `truncated: true` envelope -> `unverifiable`, never a failure |

Every check tolerates a missing `apply` payload (omitted at 32 KB) and falls back to memory / looked_up. The live runner
additionally records full tool results (no 8 KB cap) beside the turn, crew-only, for the made-up check.
Acceptance: re-grade the 723 recorded prod turns; the lead hand-checks 20 random verdicts against the conversation
before any baseline number is posted.

## Runner fidelity and bench fixes (16-17 Oct, review rounds 5-16 on #1745)

Found by the run-1 sanity check and CHAT-STATE's TRAIN root cause; every fix red-first with kill proof (PR comments).

- Live runner (`replay.py live`): runs only on the dev key (`--require-key-last4`, fails closed); the turn reads the
  PROD history as of the recorded turn (is_test=false rows before its created_at), the recorded prompt's L3 / L4 / L5
  blocks verbatim when recorded (old-layout prompts fall back to the as-of history), quoted replies and turn number as
  of the recorded turn. Before this, live turns ran in the test world with no or unrelated history (all pre-fix rows
  discarded, $0.98).
- Grader (`targets.py`): question and subject before the turn come from memory only, never the post-turn `state`
  record (unknown instead); numeric gold options map to the option at that position (label first); boolean spec
  values match through `raw`; omitted payloads are unknown; a typed code that the resolver expands (prefix family)
  satisfies the new subject only when gold says `code_unresolved`; picker option codes are made-up sources; an honest
  "none on record" (attachment tool ran, no file of the type, nothing sent or claimed) passes; summaries count
  unverifiable apart, leave runs without known results out of a rate, and need every run for `meets`.
- Gold (crew tooling `apply_gold_fixes.py`, from the original 759 items): 110 excluded as bench errors: answer items
  that select no option of the question before the turn (thanks, greetings, new asks), legacy `variables` session
  state the engine no longer reads (follow-ups only), refine items that are really new asks / reports / fit asks /
  quantities or whose filter is not in the message; answer gold options now come from the question before.
- Known gaps: the made-up check cannot see engine-side reads (crossdomain PO lines, spec grouping) or render counts,
  so every automatic finding is hand-checked (0 confirmed in runs 1-2); honest none does not check the product the
  tool was asked about (13 of 14 flips verified by hand); held-out refine has only 10 items.
- Prod finding: since #1527 (9 Oct) the parser context layer L2 always overflows its cap (a ~630-token catalogue block
  kept whole), so `Current subject` is cut on 169 of 171 recorded prod turns since 10 Oct.
- Crew ruling 17 Oct: grade outcomes, not understanding (spec must show products of the right type, refine must apply
  the filter and change the answer, only product-vs-customer clarify menus are allowed, honest none needs a searched
  product); DO-field leaks checked for unlinked askers (6 per run, the known #1648 exposure); new follow-up kind
  "hand over to a person" for yes replies to an escalation offer; empty turns (7 of 1,170) are left out.

