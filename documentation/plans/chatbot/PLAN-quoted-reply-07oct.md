# PLAN: a quoted reply answers the quoted question (QUOTED-REPLY)

Status: draft, awaiting the owner's answers to the behaviour card (crew ask a43821571, 7 Oct 2026); track: feature M, backend only (no migration, no auth/RBAC change, no new ingest surface, no UI); LEAD pattern (tester -> coder -> reviewer)

UAC: `quoted-reply-acceptance-criteria.md` (alongside). Lane: `crew/quoted-reply`, base origin/main e4158ab52.

## Report (owner, 7 Oct 2026, production, WhatsApp via respond.io)

The bot sent two separate questions, one per photo:

- "I read WDI-K5130 from that photo. How many units of WDI-K5130?"
- "I read WDI-K301-D from that photo. How many units of WDI-K301-D?"

The dealer used WhatsApp reply-to (quote) on each bot message and typed "1 unit". Both answers
came back as "SRTBF11840 x 1: No stock and no incoming" (an older product). Typing
"WDI-K5130 check stock x 1 unit" later worked.

## Scout (origin/main e4158ab52, prod copy `sorento_ai_automation_20261006`)

Measured facts, each cited in the behaviour card (`crew/state/files/QUOTED-REPLY-card.md`):

1. `replyTo.id` is read (`engine.py:987-990`) and used only to load `referenced_state`
   (`engine.py:1159-1178`, `conversation_variables_service.py:137-201`). Dead: `state_trace.after`
   is null on every `chat_histories` incoming row since 14 Sep 2026. Nothing swaps the question:
   `turn/question.py:129-143` always returns the live one; `engine.py:4205` passes `reply_to=None`.
2. The quoted turn's own state IS stored: `chatbot.turns.trace`, event `kind=memory`,
   `open_question.after` (pending wire) and `focus.after.tasks` (stock question wire). That event is
   31,953 bytes at its largest since 1 Oct against the 32,768 byte `_cap` (`trace.py:89-100`): a
   bigger focus drops the whole event, so a resolver that reads only this event has a known miss.
3. Resolving `replyTo.id` to the quoted engine turn, on the 43 live turns since 1 Sep that quoted a
   bot QUESTION (qty ask 11, numbered pick 14, yes/no 18):
   - id chain (`replyTo.id` -> `chat_histories` outgoing row -> `turn_id` -> incoming row ->
     `message_id` -> `chatbot.turns`): 37/43. Fails for EVERY photo question: the photo's incoming
     row carries no `turn_id` (8/8 photo asks since 25 Sep), and that is the reported case.
   - text equality (the quoted text the webhook carries, `replyTo.message.text`, against
     `chatbot.turns.response.reply.text` of the same contact, whitespace-insensitive, latest before
     this message): 42/43, photo asks 8/8.
4. Why SRTBF11840 stayed live on 7 Oct: UNVERIFIED (the copy ends 7 Oct 02:44 MYT, no WDI-K5130
   row anywhere). Hypothesis in the card: the two photo replies left on n8n's `media-route` arm
   without an engine turn (`engine.py:1573-1596`), so the focus kept an older stock task; the second
   "1 unit" is then the answered task's revision (`turn/task.py:667-708`).

## Rules (card recommendations; revised on the owner's answers)

R1 Target. A message that quotes a bot message resolves the QUOTED TURN: id chain first, else exact
   text equality on the bot's own stored reply (same contact, same `is_test` world as the envelope,
   H57), latest before this message. The quoted question is that turn's stored open question
   (`open_question.after`), else its open stock task (`focus.after.tasks`). Nothing is read from the
   dealer's words.
R2 Answer. When a quoted question exists and differs from the live open question, the parser's
   `Open question:` object (`engine.py:4185-4209`) is built from the QUOTED question and APPLY answers
   it through the seams it uses today (`turn/apply.py:3298-3310` pick / yes-no, `:3396` task fill).
   The live open question is left untouched and not re-asked in the reply (card Q3 (a)).
R3 Fallback = today's behaviour: no quote; quote of the dealer's own message
   (`replyTo.sender.source == "contact"`); quoted turn not found; quoted turn stored no question;
   quoted message is not a question (card Q4 (a), issue filed separately). The `received` trace
   record gains `quoted_question: {found, kind, via: chain|text|none}`.
R4 An expired escalation offer (`turn/pending.py:301-339`, TTL 3) is not revived by a quote.
R5 A quoted question that was already answered: the quoted turn's task names the product; a bare
   number revises it (`turn/task.py:667-708`), card Q2 (a).

## Design (the narrowest seams)

- `app/services/chatbot/quoted.py` (new, pure + one DB read): `resolve_quoted_question(db, *,
  contact_respond_id, reply_to_id, quoted_text, is_test, before_message_id) -> QuotedQuestion | None`
  with `pending: Pending | None`, `task: Task | None`, `via`, `turn_id`. Reads `chatbot.turns`
  (`ChatbotTurn`) and `chat_histories` only; decodes with `pending.from_wire` / `task_from_wire`.
- Storage: the tail writes one small `question` trace event per turn (`turn_trace.add("question",
  {"open_question": <pending wire>, "task": <stock task wire>})`, a few hundred bytes) so the resolver
  never depends on the 32 KB memory event; the memory event stays the fallback for turns recorded
  before this ships.
- `engine.py` at `state_in` (after `turn_runtime.load_state`, `engine.py:3961`): when
  `_reply_to_message_id(envelope)` is set and the quoted sender is not the contact, resolve; when the
  quoted question is not the live one, substitute `state_in.pending` / `state_in.focus.tasks` for
  this turn's parser context and APPLY, keep the live question aside, and restore it on the way out
  unless this turn opened a new question of its own (R2). Trace facts on `received`.
- Console: `scripts/chatbot_console_check.py` gains a case key `quote: <text>` that puts
  `message.message.replyTo = {id: <previous dry-run reply id or synthetic>, sender: {source: n8n},
  message: {text}}` on the envelope so a YAML case can quote an earlier turn of the same run.
- No parser prompt change. No new wording.

## Tickets (one lane, slices land as commits)

1. `test(red)`: tester writes the red tests from the UAC (resolver + engine substitution + fallback
   + console key), commits them alone, posts `crew-note: red-proof`.
2. Coder: `quoted.py`, the `question` trace event, the engine seam, the console key, until green.
3. Kill test, reviewer verdict, security-reviewer verdict (reads a stored turn by contact id and
   `is_test` world: cross-contact and cross-world leakage are the two checks), live console pass.
4. Hand test script `laneboard/scripts/<PR>.md`, `crew report --handtest`.

## Open (owner)

Card Q1 to Q5. Step-1 verification needs the 7 Oct trace of the reporting contact.
