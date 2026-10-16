# PLAN: EVAL-BENCH CP0, the bench check against the owner's targets (16 Oct)

Status: Plan (card to crew). Parent: `PLAN-eval-bench-16oct.md`. No real customer data in this file; real items,
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
