# CARD: CHATBOT-EVAL (generalisation harness, size L)

Status: answered 10 Oct (crew a67082077): Q1 (a) + one reference run on snapshot label v49;
Q2 (a) media + ideate excluded, listed as known gaps; Q3 (a) gate 200 hidden + 1 variant, full set
nightly / on demand, TPM backoff; Q4 (a) owner spot-checks 30 labels on one Lavish page; Q5 judge
pluggable, OpenAI judge now (same-family risk noted), Claude Sonnet once ANTHROPIC_API_KEY exists.

## What already exists (measured, reused, not rebuilt)

- Turn seam: `POST /api/v1/external/chat/turn` (`app/api/v1/external/chat.py:171`) -> `run_turn`
  (`app/services/chatbot/engine.py:2926`). `is_test`/`test_run_id` makes it a dry run
  (`contracts.py:791`): only a `chatbot.turns` row is written, no integration_log, no usage row,
  every action stamped `dry_run` (`engine.py:16-20`, `:1588`, `chat.py:239`). The CRM never sends;
  n8n executes actions (`chat.py:1-8`), so no n8n = no egress to WhatsApp.
- Known dry-run leaks: `ideate` lane writes an idea row + real LLM extraction (#1179); media turns
  spend `contact_media_usage` (`console_service.py:~585`). Harness excludes both (see Q2).
- Multi-turn: `session_patch` returned on dry run is fed into the next turn
  (`previous_conversation_state`, `engine.py:1218`), as `scripts/chatbot_console_check.py` does.
- Tokens: parser usage on the `understood` trace facts (`head/parser.py:950`); ~33.5k prompt
  tokens per parse, 92-98% cached (`PLAN-llm-test-cost-4oct.md` section 1). No USD price in repo.
- Cassettes (`app/services/llm_cassette.py`) make replays free but would hide the very prompt
  changes we want to measure, so the eval runs live (cassette mode off), budget-capped.

## Data (snapshot `sorento_ai_automation_20261006`, read-only)

- `chatbot.turns` 11,161 rows (envelope + response + trace); `chatbot.chat_ask_facts` 8,987 asks
  joined by `turn_id`, 8,767 text / 220 non-text. Domains: inventory 4295, incoming 1700, order 914,
  product_attachment 505, none 497, promotion 358, master_products 339, purchase_cost 166, others < 50,
  **sales 6** (sales asks are new; 9 Oct failures are not in this snapshot).
- Audience: staff 7860, dealer 1015, unknown 112. Topic labels: 0 on snapshot (CA-2 labeller not
  run there); taxonomy v1 (31 leaves, `chatbot.chat_topics` on `sorento_cagent_stack`, PR #1551).
  Cases get topic labels from the CA-2 prompt at case-build time.
- Parser prompt label `production`: v49 on the snapshot, v60 on `sorento_cagent_stack`, prod v52
  (owner trace). v52 text is not on any local DB (UNVERIFIED whether reconstructable from git).

## Case schema (JSON, one file per case, stored only under `crew/state/eval/`)

```
id, split (dev|hidden), topic_code, language (en|zh|ms|mixed), turns (1|n), source (snapshot|prod-9oct),
contact: {respond_id (real id in the eval clone), audience, access_type, flags {is_allowed_stock...}},
history: [{role: user|bot, text}]    # masked; user turns are REPLAYED, bot turns are context only
message: {text, reply_to?},
variants: [{kind: typo|mix|reorder|extra|followup, text, same_meaning_check: pass}],
expect: {
  outcome: answer | clarify | not_supported | handover,
  acceptable_alternates: [...]          # e.g. clarify OR answer both pass
  must:     [ {code: "SRTW1700-GY"}, {number: 1630, near: "BRW"}, {date: "2026-10-03"} ],
  options:  [codes/values a good clarify must list]   # clarify only
  must_not: [ "tool_error", "generic_retry", "uuid", "stale_subject:<code>", "repeat_prev_reply" ],
  judge_note: "one line for the LLM judge, wording only"
}, provenance: {sql: [...], labelled_by, checked_by}
```

## Grading (deterministic first)

1. Deterministic, on the TurnResponse + `chatbot.turns.trace`: tool errors / 4xx in trace, required
   codes/numbers/dates present, clarify options present, forbidden subjects absent, reply identical
   to previous bot reply (stuck), UUIDs, empty reply.
2. LLM judge only when deterministic checks pass and `judge_note` exists (wording: is the clarify
   question clear, is the not-supported line honest). Calibrated on 40 hand-labelled replies; judge
   must agree >= 90% before its scores count.
3. Verdict per case: correct / good-clarify / wrong / stuck / error.
   Report per topic + language: underfit = pass rate on originals; overfit gaps = dev minus hidden,
   originals minus variants. Plus tokens, est. cost, p50/p95 latency, wall time.

## Five real examples (snapshot, staff contacts unless stated; facts DB-verified at labelling)

1. `Srtw1700 ETA` (incoming.product_eta) -> clarify. options must include SRTW1700-SS-GY and
   SRTW1700-GY; must_not invent an ETA. Snapshot reply did this (pass).
2. `SRTWC8518-SH x 10` (inventory.quantity_check) -> answer: no stock for 10, ETA 03/10/2026 from
   the SPO row; must_not say "in stock". Variant `srtwc8518sh 10 units ada stok?` must grade the same.
3. `Pls send me srtwc8066 promotion` (promotion.product_promotion) -> clarify with tiers
   Office / Dealer / End user, OR answer with the office file (staff may see all). Both pass.
4. prod 9 Oct, multi-turn: prior ask on CBDR4003, then a new sales ask by brand + category; pick
   answer `Sorento and mocha` -> answer: sales for brands Sorento + Mocha in that category;
   must_not: stale_subject:CBDR4003, spec search, "No sales for BK007A". (today: wrong)
5. prod 9 Oct: `Total sale deliver for 1/10-9/10/26` -> answer total delivered sales for the period
   OR clarify scope (all customers? which brand?); must_not: tool_error 422 subject_required,
   generic_retry. Same family: `water closet` / `kitchen sink` -> category, must_not customer pick.

## Labelling method

- Sampling: stratified by topic leaf x audience x language x single/multi-turn, ~300 cases + the
  9 Oct failures (rebuilt as cases from the owner's traces, contact = a staff contact on the clone).
- Expected outcome drafted by an Opus subagent from the conversation + SQL it runs on the clone;
  numbers/codes/dates come from the SQL rows, never from the prod reply (prod may be wrong).
  The prod reply is shown to the labeller only as "what happened", not as the label.
- Variants: LLM-generated (5 kinds), then a second LLM call filters "same meaning, same rubric";
  rejected variants dropped. Dev/hidden split by case (variants follow their case).
- Spot check: 30 sampled labels (stratified) shown to the owner in one Lavish page; if > 3 wrong,
  the batch is relabelled with the corrections as guidance.
- Masking: person names/phones/emails in text + history replaced by stable fakes (HMAC map kept in
  `crew/state/eval/`, never in git); business data (codes, qty, dates, company names) kept.

## Runner + gate

- `sorento_crm_backend/scripts/chatbot_eval.py` (repo; no data): `EVAL_DATA_DIR`, `--split`,
  `--engine http|<module:Class>` (pluggable `Engine.turn(case, text, state) -> Outcome`; default
  posts to the lane backend's `/external/chat/turn` with `is_test` + `test_run_id`), `--budget-usd`,
  `--max-parallel`, `--baseline <json>`. Hard stop when tokens x price passes budget.
- Substrate: a private clone of the 6 Oct snapshot per run (DB `sorento_eval_*`), MCP + backend on
  crew ports, n8n not running, ideate + media cases excluded.
- Hidden-set protection: lanes run `--split dev` only. The hidden run is executed by crew
  (`crew eval <LANE>`) against the lane's stack; only aggregate scores leave `state/eval/hidden/`,
  posted as `crew-note: eval hidden <score> (main <baseline>, delta <x>)`.

## Cost per run (estimate)

Prices (Standard tier, per 1M tokens, https://developers.openai.com/api/docs/pricing, read 10 Oct 2026):
gpt-5.4-mini $0.75 input / $0.075 cached / $4.50 output; gpt-4.1-mini $0.40 / $0.10 / $1.60.
Full run ~84M prompt tokens at 90% cached = 8.4M x 0.75 + 75.6M x 0.075 = $6.30 + $5.67, plus
~0.75M output x 4.50 = $3.38: **~$15 per full run**. PR gate (~19M prompt tokens): **~$3.5**.


- Full: 300 cases x (1 + 5 variants) = 1,800 graded turns, + ~40% replayed history turns ~ 2,500
  parses x 33.5k ~ 84M prompt tokens (~90% cached) + clarifier/tool calls. Wall: ~5 s/turn,
  needs ~8 parallel for < 30 min; OpenAI TPM 429s seen before (console check).
- PR gate (recommended): 200 hidden originals + 1 random variant each ~ 560 parses ~ 19M tokens,
  ~10 min at 6 parallel.

## Questions (owner)

Q1 Baseline prompt. (a) Clone the snapshot and publish main's vendored parser prompt as the label
the run uses (measures main code + main prompt), recording its version; (b) keep snapshot v49.
Rec (a): a PR gate must measure what the branch would ship; v52 (prod) is not on any local DB.
Q2 Media + ideate cases. (a) Exclude in v1 (220 non-text of 8,987; image fetch is egress, ideate
writes rows); (b) include with a stub. Rec (a).
Q3 Gate size. (a) PR gate = 200 hidden originals + 1 variant each (~10 min), full set on demand /
nightly; (b) full set every PR (~30 min, TPM risk). Rec (a).
Q4 Spot check. (a) Owner checks 30 sampled labels on one Lavish page; (b) crew tester agent checks.
Rec (a): the labels define "correct" for every later lane.
Q5 Judge model. (a) Claude (Sonnet) judge, different family from the OpenAI parser, needs an
ANTHROPIC_API_KEY in the eval env; (b) OpenAI gpt-5.4-mini judge. Rec (a): avoids self-preference.
