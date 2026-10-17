# EVAL-BENCH behaviour card (16 Oct)

Lane EVAL-BENCH, step 0 of the 16 Oct chatbot scale design. Crew-only data is cited by id, never by text.
Backend paths are relative to `sorento_crm_backend/`; crew paths to `crew/state/eval/`.

## Rules (what changes)

1. **Provenance on every turn and every run.**
   - The `understood` trace record (`app/services/chatbot/engine.py:4630-4660`, failure branch `:4576-4590`) gains
     `prompt_sha256` (sha256 of `parser_config.system_prompt`, the rendered text from `ai_prompt_registry.render`,
     `head/parser.py:767`), `model`, `provider`, `reasoning_effort`, `code_sha`. Today it holds only `prompt_version`.
   - `code_sha` comes from the same source as `/health` (`app/main.py:595-623`, `GIT_SHA` env, else `git rev-parse`).
   - E2E runner (`scripts/chatbot_eval/run.py:80-90`) records the sha of the SERVER under test from `/health`
     (today it records its own checkout: all 25 runs on 16 Oct say `7482bc81d`), keeps its own as `runner_sha`, and
     rolls the per-turn provenance up into `report.json.provenance` (distinct values; more than one = flagged).
   - Parser-gold rows (`parser-gold/tooling/run_parser.py:52-98`) gain the same five fields.
   - The scorers (`parser-gold/tooling/score.py`, `tooling/compare.py`) refuse a run whose rows lack provenance, and
     refuse to treat runs as reps of one arm when their provenance differs.
2. **L1 replay gate ($0).** `tooling/replay/` replaces the scratchpad `f4_replay.py` (copy kept in
   `tooling/replay/seed-16oct/`). One command takes a base worktree, a head worktree and the recorded traces
   (`runs/*/traces.jsonl`, 8,909 parses), runs the same recorded parser outputs through both, and prints per-case,
   per-stage diffs by id: grounded entities, decision/plan, tool + args, reply text. A diff the PR does not list as
   intended blocks. Hidden-split diffs print ids only; lanes get the dev-split variant. Target < 5 min.
3. **Error bars.** `score.py` and `compare.py` report mean and a paired 95% bootstrap CI (items resampled, reps
   averaged per item) over >= 3 reps per arm. Verdict words: "better" only when the CI lower bound > 0, "worse" only
   when the upper bound < 0, otherwise "within noise". Fewer than 3 reps: the verdict is refused, not guessed.
4. **Real-mix weighting.** A `weights.json` built from the corpus report gives each gate stratum its real share;
   the headline score is the weighted gate score. Growth slices (dealer, Malay/Chinese/mixed, heavy asks) are
   reported beside it, never pooled into it. A restore recipe (`tooling/corpus/restore_chat_slice.sh DUMP DB`)
   loads only the chat tables and minimal lookups from a custom-format dump via a filtered `pg_restore -L` list
   (never `createdb -T`, never a full restore), then refreshes the corpus numbers and candidate regression items.
   Data stays under `crew/state/eval/private/` (chmod 700), never in git, never to cloud lanes.
5. **Hop timing.** Per real inbound message: respond.io time (`chat_histories.respond_ts`, `type='incoming'`) ->
   n8n/webhook time (`chat_histories.sent_at`, `app/api/v1/public/respond_webhook.py:211`, UNVERIFIED which clock) ->
   CRM turn created (`chatbot.turns.created_at`) -> engine start/end (`started_at`/`finished_at`,
   `engine.py:1565,1636`) -> reply at respond.io (outgoing `respond_ts`) -> delivered (`delivered_ts`).
   Report p50/p90/p95 per hop; read-only on `sorento_cagent_stack`; later re-run on the owner's fresh backup.

## Real examples (dev/eval data)

1. PR #1731 reported parser-gold HIDDEN 83 -> 80; only 5/113 items (G155 G156 G207 G216 G217) had a changed parser
   input and they scored the same on both arms. With rule 3 the verdict prints "within noise", not "-3".
2. F4 (#1737): the grounding replay found real entity diffs on cand-0174, cand-0108, cand-0127 while all 6 E2E runs
   stayed 178-180/214. Rule 2 must reproduce those 3 ids on the F4 base vs head pair (acceptance test).
3. HGF4-BASE ran main `8ceadb980` at 05:53Z on an eval DB already holding PR-A's seed; its report says git_sha
   `7482bc81d`. With rule 1 the report carries the server sha and the prompt hash, so the mismatch is visible.
4. Corpus (`sorento_cagent_stack`, 17 Apr-18 Sep): 5.27 s p50 from respond.io message time to CRM turn created,
   of 9.55 s p50 total (2,317 done real turns). Rule 5 splits that 5.27 s.

## Edge cases

- Recorded trace with no `_parser_raw` (parser error turns): replay reports `skipped:no_parse`, not a diff.
- Head tool args differ from the recorded call: no recorded envelope exists, so the replay reports `tool_args`
  as the first diff and does not compose a reply for that case.
- Multi-turn cases: state is taken from the recorded trace of that turn (focus/open question events), not the DB.
- Same message parsed under several prompt versions: each recorded parse is its own replay input (deduped by
  sha of parse + state).
- Old runs without provenance: refused by default (see Q2).

## Questions (each with recommendation)

**Q1. Replay depth (rule 2).** (a) Run the real `engine.run_turn(envelope, session_factory=...)`
(`app/services/chatbot/engine.py:3037`) in-process on the eval DB, using the seams the engine already has:
`dry_run=True` + `mock_reformulator_output` = the recorded parse (`engine.py:1296-1301, 4543-4547`; no Redis, no
usage logs), `previous_conversation_state` = recorded state (`:1352, :4251`); the tool adds only two patches,
the same ones `tests/chatbot/test_turn_replay.py:758-827` uses: `MCPRuntimeClient.call_tool` returns the recorded
envelope for a matching (tool, args) and reports a miss otherwise, and the session factory wraps every turn in an
outer transaction that is rolled back (commits become savepoints), so the eval DB is never written. No product
code change. (b) call the stage functions one by one (`head/grounding.py:714 ground`, `turn/decide.py:619 decide`,
`lanes/business/resolve_gate.py:593 resolve_entity_body`, `turn/compose.py:506 compose`). **Rec (a)**: it grades
the real glue code; (b) re-implements the engine's wiring and drifts like the parser-gold adapter did
(`parser-gold/tooling/adapters.py:188-189`). Fallback if (a) cannot meet 5 min on ~8.9k parses: dedupe by input,
parallel 2, and run grounding-only for the full corpus with whole-turn replay on the deduped set.

**Q2. Old runs without provenance.** (a) Refused by default; `--legacy` scores them with a banner "LEGACY: not a
gate verdict" (needed to validate the CI code on the 16 Oct reps at $0); (b) refused, no escape. **Rec (a).**

**Q3. Reasoning effort.** The parser sends none today (`head/parser.py:1040-1052`). (a) Record what is sent
(`null` = provider default); (b) start sending an explicit value. **Rec (a)**: changing it is step 4 (speed).

**Q4. Weighted gate strata.** (a) Gate = staff + English items, weighted by shape to the corpus mix (code ask
~80%, code-less follow-up 11.4%, number pick 3.2%, ack 2.7%; corpus report lines 338-346, 781-789), weights in
`weights.json` refreshed with each corpus refresh; growth slices dealer / ms+zh+mixed / heavy beside it;
(b) unweighted score plus slices. **Rec (a)** (owner D9). Note: parser-gold has 149 staff and 182 English items of
249, so the weighted gate CI will be wider than the pooled one.

**Q5. DBs this lane creates (lead only, never a subagent).** (a) `sorento_evalbench_throwaway` for pytest via
`scripts/bootstrap_env.py`, and `sorento_chatslice_check` to prove the restore recipe on the 16 Oct eval-base dump
(chat tables only), dropped after; disk is at 90% (44 GB free); (b) no DBs, recipe proven by `pg_restore -l` TOC
filtering only. **Rec (a)**: a recipe never run is a guess.

## Not in scope

No UI, no owner-visible change, so no mockup and no hand test. No auth/RBAC change, so no security-reviewer.
LLM spend: none planned; at most a $3 validation (one parser-gold rep pair) if crew asks.

## Answers (crew, 16 Oct, technical, no owner call)

Q1 (a), Q2 (a) `--legacy` only for the $0 CI validation, Q3 (a) record null and do not change what the parser
sends, Q4 (a) per owner D9, Q5 (a) with limits: chat tables only, check dump table sizes first, stop if the
restore would leave under 30 GiB free, drop both DBs right after the proof and say so in the report. Build on the
eval-base dump until the owner's prod backup arrives.
