# Chatbot verification (the console check)

The chatbot has no screen to click, so browser verification cannot cover it. Its equivalent is
`sorento_crm_backend/scripts/chatbot_console_check.py`: a YAML file of real turns, run against a
real backend, graded line by line. Every chatbot PR runs the case file against a LANE backend
before the PR is opened, and once against PRODUCTION after the deploy; the output of both goes in
the PR (or the deploy note). A case that fails is a finding to fix or to explain - never one to
soften.

**Run it after EVERY deploy, not only a chatbot PR's own (LESSONS-LEARNT.md #103).** The
chatbot's entity resolver is SHARED with every other admin-data reference table (`AttachmentType`,
`Customer`, `Product`, ...), so a migration that seeds or edits a row in one of those tables can
change chatbot resolution even when its own PR has no chatbot file in the diff - #707's
`485_shipment_line_photo_type.py` (an unrelated SCM feature) deployed alongside #713 and broke
"photo" resolution three minutes after the deploy, and `git diff` on #713's own files showed
nothing wrong, because nothing in #713 was. A deploy that touches ANY reference table the
resolver searches is chatbot-relevant.

```bash
cd sorento_crm_backend
venv/bin/python scripts/chatbot_console_check.py \
    tests/chatbot/console_cases/2026-09-06.yaml --base-url http://127.0.0.1:8004
```

**`--say` is the same runner without a file**, for trying something by hand. Repeat it and
the repeats are ONE conversation - each reply's session variables feed the next turn exactly
as a YAML `turns:` list does - and each turn prints its branch, reply, `send_message` texts,
quick replies and a one-line trace (tool + args, cross-domain rungs, reveals dropped).
Nothing is graded and nothing extra is written; `--contact` defaults to whoever the bot
last answered.

```bash
venv/bin/python scripts/chatbot_console_check.py \
    --say "check stock srtwc286" --say "PO?" --base-url http://127.0.0.1:8004
# add --prompt-version <ai_prompt_versions.id> to try an UNPROMOTED prompt (dry run only)
```

Every turn is a dry run (`is_test`, `test_run_id`), so nothing outside `chatbot.turns` is written
and no WhatsApp message can leave. The envelope is borrowed from that contact's most recent
`chatbot.turns` row, which is why the script refuses a non-local `--base-url` without `--i-know`:
it reads THIS checkout's database and posts to whatever backend you name, and nothing can check
the two agree. The lane switches (`system_settings.chatbot_business_lane_enabled`,
`chatbot_completed_lanes`) decide whether the CRM answers a turn or delegates it to n8n, and a
delegated turn comes back silent - so the script reads them, prints them, turns every lane on for
the run and restores the exact values in a `finally`. On a production checkout it refuses to
write them at all (Settings > Chatbot is the only sanctioned way there) and runs against whatever
is already set, once `--production` acknowledges where it is pointed.

**What is graded is what the customer would be TOLD** - the reply text plus every `send_message`
action, because the escalation lane's assignment arm composes no reply and sends its sentences as
actions. Silence fails on its own without being asked for, and every case carries a positive
`reply_contains`: a case built from `branch_kind` plus negatives passes on the generic error
reply, which is exactly how four cases read green while their turns had failed.

**The case file grows from the n8n side.** Cases come from what the owner actually sent and what
came back: the report from the n8n run is the source, one case per defect, with the expectation
taken from that defect's own red test so the file and the suite say the same thing. A multi-turn
case is a `turns:` list - each reply's session variables feed the next turn as
`previous_conversation_state`, which is how a picker or did-you-mean sequence is checked without
writing session state.

Two limits worth knowing before reading a red line as a defect:

- **The MCP tool search needs `OPENAI_API_KEY`**, which is empty in every local `.env`. Without it
  any turn that has to pick a tool fails with "no embedding provider is configured" and the case
  cannot be graded locally. Those cases are graded on the production run.
- **Access is decided before routing.** A turn whose agent the contact has no grant for (or whose
  `access_agents` row does not exist at all) comes back `access_denied` whatever the lane does.
  That is a data prerequisite, and the check naming it is the check working.
- **`tests/chatbot/test_replay.py` SKIPS the whole corpus in silence** when it cannot
  resolve the sibling n8n checkout (`tests/chatbot/_corpus.py`). The run says `passed` with
  a skip count nobody reads, so a kill test aimed at a replay fixture proves nothing on its
  own - a reviewer who reverts a hunk and sees green may only be seeing an absent corpus.

  **Confirm the root was resolved rather than assuming, and do NOT reach for
  `CHATBOT_FIXTURES_DIR` first.** Auto-discovery walks up to the sibling checkout and
  prefers the CAPTURES worktree over the main one, which is what you want: the capture runs
  land in `captures-rs1a-parser` and only reach the main checkout when that lane merges, so
  pointing the variable at `<n8n checkout>/n8n-workflows-init/tests/fixtures` DOWNGRADES the
  corpus and reddens `test_s6c_answer_lane.py::test_full_corpus_has_at_least_one_capture[miss-suggest-result]`.
  An explicit `CHATBOT_FIXTURES_DIR` beats both, so a wrong one is worse than none.

  ```bash
  # what the loader actually found, and how much of it there is
  venv/bin/python -c "from tests.chatbot import _corpus; print(_corpus.corpus_root())"
  venv/bin/pytest tests/chatbot/test_replay.py --collect-only -q   # 1871 in this lane

  # then the kill test itself - a SKIP here means the corpus is missing, not that the
  # fixture agrees
  venv/bin/pytest "tests/chatbot/test_replay.py::test_full_corpus_replay[output_exchange/sub-semantic-parser/parser-15157067]" -q
  ```

  If you do set the variable, point it at the CAPTURES worktree
  (`sorento_crm_n8n/.claude/worktrees/captures-rs1a-parser/n8n-workflows-init/tests/fixtures`),
  which is what auto-discovery would have chosen.

## Generalisation eval (CHATBOT-EVAL)

The console check proves named cases still pass. The eval measures how well the chatbot
generalises: ~320 real, masked conversations from a prod snapshot, each with a rubric (facts from
the DB, clarify options, must-not), plus 5 rewordings per case (typo, language mix, reorder, extra
words, follow-up phrasing). Plan and contract: `documentation/plans/chatbot/PLAN-chatbot-eval-10oct.md`,
`CONTRACT-chatbot-eval-runner.md`.

- The cases are NOT in this repo (it is public). They live in the crew-only state dir; the runner
  refuses a data dir or `--out` inside the work tree.
- Split: `dev` (a third; lanes may read and run it) and `hidden` (two thirds; crew only).
- A lane runs the dev split against its own stack (backend on an eval clone named `sorento_eval_*`):

```bash
cd sorento_crm_backend
EXTERNAL_API_KEY=<lane key> EVAL_TRACE_DB_URL=<eval clone dsn> EVAL_RESET_DB_URL=<same dsn> \
  venv/bin/python -m scripts.chatbot_eval --data-dir "$EVAL_DATA_DIR" --split dev --variants one \
  --engine http --base-url http://localhost:<be port> --budget-usd 3 --out <dir outside the repo>
# --prompt-version <ai_prompt_versions.id> pins an unpromoted parser prompt (dry run only)
# --engine pkg.module:Class runs the same cases through another engine
```

- The PR gate is crew's: it runs all 214 hidden originals (~389 live calls incl. replayed history,
  ~$2, ~5 min, on crew's budget) with the runner from a trusted checkout and posts
  `crew-note: eval hidden ...` with the delta against the main baseline. Only aggregates leave the
  crew dir. Rewordings (the overfit check) run weekly or on owner request.
- Live-call rules: estimate calls x tokens before a run; a lane's own dev runs count toward its
  300 calls/day cap (a turn is about one parser call, ~40k prompt tokens).
- Verdicts: correct, good_clarify, wrong, stuck, error. Underfit = pass rate on originals; overfit
  gaps = dev minus hidden, originals minus variants. A fix that only passes the reported phrasing
  shows up as a variant gap.
- Judge: Claude (`claude-sonnet-5-5`, `--judge anthropic`) scores wording only for cases the fixed
  checks cannot settle, and only once it agrees with human verdicts (`calibration/*.json`) on at least
  90%. It has its own spend cap (`--judge-budget-usd`, default $2) and sends the masked case text and
  the bot reply to Anthropic. The key is never in the repo; crew's gate loads it at runtime.
- Known gaps (v1): media (photo) turns and ideation turns are excluded; relative-date asks ("this
  month") carry no period numbers because the replay date is not the ask date.
