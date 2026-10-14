# UAC: PV2 scenario battery + live runner (PV2-EVAL, 14 Oct 2026)

Plan: `PLAN-pv2-eval-battery-14oct.md` (beside this file). Behaviour card answered by crew 14 Oct (ask a74982394):
Q1 (b), Q2 (a) with v1 pinned to the prod v52 text, Q3 (a) Opus judge calibrated first, Q4 (a) own copy, Q5 (a).

Test data + harness only. No chatbot product code changes.

## UAC-1 Battery files

- Location: `sorento_crm_backend/tests/chatbot/pv2/battery/<domain>.yaml`, plus `contacts.yaml` and
  `calibration.yaml` in the same folder.
- File shape:

```yaml
domain: stock                      # file default, may be overridden per conversation
conversations:
- id: stock-r01                    # unique across the battery, [a-z0-9-]+
  source: real                     # real | improvised
  origin: "crew/state/handtest/PV2-owner-loo-r7-20261014.tsv T1-T5"   # required when source = real
  domain: stock                    # one of DOMAINS
  audience: staff                  # one of AUDIENCES
  shapes: [reply_to_question, number_pick, particle]                  # one or more of SHAPES
  turns:
  - text: "check stock srtwc286"
    expect:
      kind: answer                 # answer | ask | handover | refuse | any
      about: "stock for the SRTWC286 family"     # required: what must be answered or asked (judge input)
      must: ["SRTWC286"]           # optional: every item is a case-insensitive substring of the reply
      must_any: [["ETA", "incoming"]]            # optional: each group needs at least one hit
      must_not: ["Which \"mou\""]  # optional: none may appear (case-insensitive)
      attachments: true            # optional: true = files must be sent, false = no files may be sent
      lang: ms                     # optional: expected reply language (judge)
      judge: "extra note"          # optional
      allow_repeat: false          # optional, default false (see stuck check)
```

- DOMAINS: `stock, eta, attachments, order_status, outstanding, reports, promotions, ideas, handover, mixed`.
- AUDIENCES: `staff, dealer, dealer_nogrant, unlinked`.
- SHAPES: `refine, reply_to_question, number_pick, yes_no, free_text_reply, topic_switch, switch_back,
  point_into_answer, multi_product, mixed_types, qty_carried, language_mix, typo, particle, small_talk, unclear,
  permission`.
- Coverage (enforced by a test): at least 60 conversations; at least 15 `real`; every domain except `mixed` has at
  least 5 conversations; every audience at least 5; every shape at least 3; `point_into_answer` includes a DO (or
  SO) with exactly 1 product and one with 3 or more products.
- Expectations follow the owner rules (`documentation/plans/chatbot/PV2-owner-rulings.md`, crew memory): clarify,
  never assume; never codes/UUIDs; the bot states only data it holds; never "has no Product Photos" when the files
  are sent; resolve-then-reuse. Expectations name subjects and behaviour, never dev numbers that drift.
- No phone numbers, no person names (masked as roles), no raw chat dumps. Business data (company, customer,
  product names, codes, quantities, dates) verbatim. Contact ids appear only in `contacts.yaml`.

## UAC-2 contacts.yaml

`audiences: {<audience>: {respond_id: "<id>", note: "<why this contact fits, no names>"}}` for all four audiences,
each a contact on the shared dev DB `sorento_cagent_stack` with a real inbound webhook turn (the console borrows
it, `console_service.py:119-160`).

## UAC-3 calibration.yaml

At least 20 hand-labelled turns (at least 8 `pass`, at least 8 `fail`) from real bot replies with crew verdicts
(Mr Loo session tsv, PV2-FIX live-check tsvs). Each: `{id, history: [{user, bot}], text, reply, expect: {...same as
UAC-1...}, label: pass|fail, why}`.

## UAC-4 Deterministic checks (pure functions, `scripts/pv2_battery/checks.py`)

`run_checks(turn, expect, prev_reply) -> list[CheckFail]`, `CheckFail(check: str, detail: str)`. A turn record holds
`text, reply, actions, mode ('v2'|'v1'), pv2_event (bool), prompt_version, expected_version, tool_errors`.
Checks (name: fails when):

- `uuid_leak`: the reply contains a UUID.
- `internal_key_leak`: the reply contains an internal key: an UPPER_SNAKE token with 2 or more underscores
  (e.g. `DISPLAY_STANDEE_APPLICATION_FORM_MOCHA`), a lower snake_case identifier with an underscore
  (e.g. `branch_kind`, `stock_check`), a template brace `{{` / `{code}`, or a bare `None` / `null` / `undefined`.
  Product codes (e.g. `SRTWC286-SH`, `CKS1052-BL`) and anything inside a URL are not internal keys.
- `silent`: no `send_message` action with non-blank text.
- `false_gap`: the turn sends at least one file (`send_attachments`) and the reply holds a gap sentence
  (`<CODE> has no ...`, `<CODE> tiada ...`, `<CODE> 没有...`) for a CODE that occurs (case-insensitive) in any sent
  file's url/name/label.
- `stuck`: the reply equals the previous turn's reply (after whitespace trim) and `allow_repeat` is not true.
- `must`, `must_any`, `must_not`: as UAC-1.
- `attachments`: `true` and no file sent, or `false` and a file sent.
- `v1_fallback`: mode is v2 and the turn trace has no `pv2_turn` event.
- `version_mismatch`: the turn's recorded prompt version differs from the pinned one.
- `tool_error`: the turn recorded tool errors.

## UAC-5 Judge (`scripts/pv2_battery/judge.py`)

- Model `claude-opus-5-5`. Rubric lives in the file (a module constant) and names the owner rules.
- Input per turn: the conversation so far (user + bot), the user text, the reply (+ file count), the expectation,
  the turn's facts (resolved entities and tool results digest from the trace), the audience.
- Output JSON `{verdict: pass|fail, classes: [...], reason}`; classes from: `wrong_subject, lost_ask,
  should_clarify, needless_clarify, invented_data, wrong_language, leak, false_gap, permission, handover_wrong,
  stuck, tone, other`. Unparseable output = `judge_error`, never a pass.
- The judge runs only when the deterministic checks pass (a deterministic fail is the verdict).
- Calibration: `--calibrate` runs the judge over `calibration.yaml` and prints agreement; a baseline table states
  the agreement and is marked PROVISIONAL below 0.9.

## UAC-6 Runner safety (`scripts/pv2_battery/guard.py`) - nothing leaves

- Every turn goes through `console_service.run_console_turn` with the envelope patched: `parser_mode` forced by
  patching `pv2_flag.parser_mode` in the runner process, and `console_origin` removed (so `_chat_console` is false
  and the stock-ask notify returns early, `engine.py:9803`). Envelope stays `is_test` with a fresh `run_id` per
  conversation.
- Installed before the first turn: any RQ enqueue in the process raises `OutboundBlocked` (patched at
  `rq.Queue.enqueue_call` and `app`'s own enqueue helper); the run refuses to start unless `HTTPS_PROXY` points at
  the dead proxy and the database is a dev DB (host localhost, name not containing `prod`).
- A test fails if a notify or any RQ enqueue is attempted after the guard is installed, and if the envelope the
  runner builds carries `console_origin` or lacks `is_test`.

## UAC-7 Prompt pins

- v2: the version id the `staging` label points at for `chatbot_semantic_parser_v2` at run start (or `--v2-version`),
  sent as `prompt_overrides.chatbot_semantic_parser_v2`.
- v1: `f3dc8c35-4fc8-41b1-89eb-a6ef14e71cea` by default (dev v91, template md5 `f447ad501b72a8e2fa6abd142b51e80e`,
  the prod v52 text; `--v1-version` overrides), sent as `prompt_overrides.chatbot_semantic_parser`.
- Both ids, version numbers and the content md5 are printed in the table header.

## UAC-8 Output and CLI

- `python -m scripts.pv2_battery run --name NAME [--mode v2|v1|both] [--ids a,b] [--domain d] [--repeat N]
  [--parallel N] [--no-judge] [--out DIR]`; `python -m scripts.pv2_battery calibrate`; `python -m scripts.pv2_battery
  check` (validate the battery only, no model calls).
- Run dir (default `/Users/tehjayson/Documents/foundryx/crew/state/eval/pv2-battery/<NAME>-<ts>/`, never in the repo):
  `results.jsonl` (one row per turn: conversation id, mode, rep, turn, text, reply, files, parse, turn_id, version,
  checks, judge), `table.md` (per conversation: tags, v2 result, v1 result, first failing turn + class; totals by
  domain, audience, source), `failures.md` (each failing turn: conversation, turn, text, reply excerpt, reasons,
  turn_id).
- A conversation passes only when every turn passes. Repeats: pass in all reps = pass, mixed = flaky.

## UAC-9 Baseline

One run on crew/pv2 head (0690ef90a or later), v2 and v1, judge on; the table posted as a `crew-note:` on the
draft PR into crew/pv2; the failure list handed to PV2-FIX through a crew report.
