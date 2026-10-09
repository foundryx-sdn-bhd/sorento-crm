# PLAN: Chat analytics CA-4, history backfill (9 Oct 2026)

Status: Review fix round 1 pushed (10 Oct 2026); next: crew rulings on sender sources, hand test.
Track: full pipeline (migration, new run table, LLM job). Lane: CA-4-BACKFILL, PR #1552.

Design (binding): `documentation/plans/chatbot/DESIGN-chat-analytics-09oct.md` (PR #1545), CA-4
row (section 8), eras A to C (section 2.6), backfill (section 3.5). Builds on CA-1
(`PLAN-chat-analytics-ca1-facts-09oct.md`, #1546) and CA-2
(`PLAN-chat-analytics-ca2-taxonomy-09oct.md`, #1551). UAC:
`chat-analytics-ca4-backfill-acceptance-criteria.md`.

The real per-era counts and the token estimate are on the PR (crew-note comments), not here:
this repository is public and the counts come from a production copy.

## What already exists (read, cited)

- Fact writer (CA-1) `app/services/chatbot/analytics/facts.py`: `_who` (identity snapshot),
  `_lookup` / `_pick` (exact code match). `chat_ask_facts.turn_id` is NOT NULL, so a fact with
  no turn needs a migration.
- Labeller (CA-2) `app/services/chatbot/analytics/labeller.py`: taxonomy load, prompt rules,
  `parse_labels`, `json_schema`, `_write_items` (exact-tier resolver, confidence >= 0.8),
  `production_chat`, `production_resolve`. Its `_pick` inner-joins `chatbot.turns`, so the
  nightly job never picks a backfill row.
- `chat_histories` (`app/models/chat_history.py`): `type` incoming / outgoing, `sent_at` naive
  UTC, `message_id`, `turn_id` (n8n execution id on both saves of one turn, alembic 290),
  `state_trace.parser_raw` (incoming, alembic 295), `result` (outgoing items), `sender_source`.
- The explicit link incoming row -> engine turn: same contact + `message_id`
  (`chat_history_turn_link.py`).
- Report notes: `datasets/chat_asks.py` (`TRACES_NOTE`, `topics_note`, `default_period`).

## Eras (Malaysia dates; `sent_at` is UTC, so an edge is the day before at 16:00 UTC)

| Era | Dates 2026 | Subject from |
|---|---|---|
| A | 17 Apr to 21 Jul | labeller |
| B | 22 Jul to 18 Aug | saved parser output where it is an object, else labeller |
| C | 19 Aug to 4 Sep | saved parser output where it is an object, else labeller |
| D-gap | 5 Sep to 22 Sep, messages with no engine turn only | labeller |

Era C ends on 4 Sep (design D:230): from 5 Sep every engine-processed message has its parser
output in the trace (D:231). D:37's "to 7 Sep" is about the saved parser column; untraced 5 to
7 Sep messages are in the D-gap slice.

## Rules (card, as answered)

- B1 An ask is an incoming `chat_histories` row in an era with no non-test `chatbot.turns` row of
  the same contact + `message_id`.
- B2 Duplicates collapse to the lowest id: same contact + `message_id`, or (no `message_id`)
  same contact + exact text + `sent_at` to the second. Exact equality only.
- B3 The reply: outgoing rows with the same `turn_id` (n8n execution id); with none, the
  outgoing rows of the same contact after the ask and before that contact's next incoming row
  (crew Q2 b). Ordering only, never text. A copy of the ask itself (B2) is not the next
  incoming row (review round 1).
- B4 Subject: the saved parser output's domain when `parser_raw` is a JSON object and the domain
  is a taxonomy subject; otherwise the labeller picks a subject. Leaf, ask split, language,
  product words: the CA-2 rules.
- B5a Senders: `user` (inbox) and `echo` (the WhatsApp Business phone) are staff, `broadcast`
  answers nobody, every other sender (`bot`, `api`, `workflow`, none) is the bot (review round 1,
  pending a crew ruling).
- B5 Outcome: the model picks one of `answered`, `answered_empty`, `clarified`,
  `refused_policy`, `cannot_do`, `handover`, `noise` from the reply. No bot reply rows: staff
  rows only -> `handover`; nothing at all -> `no_reply` (new, not a failure, crew Q3).
  `missed`, `phantom`, `technical` are never guessed from text.
- B6 Items: named = CA-2 resolver pass; shown = reply `result` items with `entity_type =
  product` whose `uuid` is a `products.id` (`resolved_by = history_result`).
- B7 Identity: CA-1 `_who`.
- B8 Storage: `turn_id` nullable, `history_id bigint`, exactly one of the two, unique
  (`history_id`, `ask_seq`), `source = history_backfill`; run table
  `chatbot.chat_backfill_runs`. No text column.
- B9 Run: `scripts/chat_backfill.py --era A|B|C|D [--estimate] [--limit N]`. Batches of 40,
  grouped by contact then time, one commit per batch with the run's progress. Re-run writes
  nothing new (a message with a fact is not eligible). A taxonomy bump: `--relabel`.
- B9a A relabel keeps ask 1's row and id (as CA-2's `_apply`), so a topic audit verdict that
  cascades from it survives; further asks and items are replaced. The topic audit screen reads a
  backfilled ask's words live from its `chat_histories` row, never stored.
- B10 Coverage line per era the period touches, "% read" = backfilled messages / eligible
  messages from the era's latest run; no run -> "not loaded yet".
- Q4 Default report period opens on the earliest fact (5 Sep until a backfill exists).

## Out of scope

The prod run (owner post-step). Re-labelling trace rows (CA-2). Staff-reply metrics (Q5 of the
design, still unmeasured).
