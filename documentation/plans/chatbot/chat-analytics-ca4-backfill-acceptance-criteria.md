# UAC: Chat analytics CA-4, history backfill

Plan: `PLAN-chat-analytics-ca4-backfill-09oct.md`. Every criterion is tested on synthetic rows
(prefix ZZT); no real message, name or number is committed.

## Storage
- AC-1 A fact can name a `chat_histories` row instead of a turn: exactly one of `turn_id`,
  `history_message_id` is set (the database refuses both or neither).
- AC-2 One fact per (`history_message_id`, `ask_seq`); a second write of the same message adds
  nothing.
- AC-3 `chatbot.chat_backfill_runs` records era, status, eligible count, messages read, asks
  written, calls, failed calls, tokens in / out / cached, model, prompt and taxonomy version,
  progress cursor. No column holds a customer's words or a reply.

## Selection
- AC-4 Only incoming rows inside the era's Malaysia dates are read.
- AC-5 A message with a non-test engine turn (same contact + `message_id`) is never backfilled.
- AC-6 Exact duplicates collapse to one ask (message id, or text + second when there is none).
- AC-7 A message that already has a fact is not eligible again.

## Reply and outcome
- AC-8 The reply is the outgoing rows of the same n8n execution id; failing that, the outgoing
  rows of the same contact before that contact's next incoming row.
- AC-9 No reply rows -> outcome `no_reply`, not counted as failed. Staff rows only -> `handover`.
- AC-10 Otherwise the outcome is the model's, from the closed list; anything else drops the row
  (it is retried next run). `cannot_do` sets `failure_reason`.

## Labels
- AC-11 Saved parser output (a JSON object, domain a known subject) fixes the subject; the model
  cannot change it.
- AC-12 With no saved parser output, the model's subject is used when it is a known subject;
  otherwise the row is left out.
- AC-13 Leaf, ask split, language and named products follow the CA-2 rules (leaf under its
  subject, exact resolver hit, confidence >= 0.8).
- AC-14 Shown products come from reply `result` items whose `uuid` is a product id.
- AC-15 The fact never stores message or reply text.

## Run
- AC-16 `--estimate` counts eligible messages and projected tokens and makes no model call and no
  write to facts.
- AC-17 `--limit N` reads at most N messages. One commit per batch of 40; a failed model call
  skips its batch and is counted.
- AC-18 A taxonomy without leaves runs nothing (as CA-2).

## Reports
- AC-19 Chat overview, Top asks and Bot failures print one coverage line per era the period
  touches: text era with % read, or "not loaded yet"; traces from 5 Sep.
- AC-20 The default period opens on the earliest fact, or 5 Sep with none before it.
- AC-21 `no_reply` is an outcome option with its own words, not a failure.
