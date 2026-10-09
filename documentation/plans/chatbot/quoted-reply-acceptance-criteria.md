# UAC: QUOTED-REPLY

Plan: `PLAN-quoted-reply-07oct.md`. Owner rulings 7 Oct 2026: Q1 id route (photo link fixed on our
side, text equality on the bot's stored reply only as fallback), Q2 (a) parser-interpreted, Q3 (a),
Q4 (b) any quoted bot message is the context, Q5 (a) all kinds.

- AC-QR-1 (column): `chat_histories` has a nullable `chatbot_turn_id` (uuid string of
  `chatbot.turns.id`); one alembic migration, single head; incoming rows and proactive sends keep it NULL.
- AC-QR-2 (stamp by n8n turn id): an OUTGOING ingest whose `turn_id` equals an incoming row's `turn_id`
  for the same contact, and that incoming row's `message_id` names a live engine turn of that contact,
  stores that turn's id in `chatbot_turn_id`.
- AC-QR-3 (stamp by stored reply, the photo path): an OUTGOING ingest with no incoming row for its
  `turn_id` stores the id of the latest live engine turn of the same contact that finished within
  120 s before `sent_at` and whose `response.reply.text` equals the message ignoring whitespace. A turn
  of another contact, a dry-run turn, a turn outside the window, or a turn whose reply differs is never
  stamped (NULL).
- AC-QR-4 (explicit wins): an OUTGOING ingest carrying `chatbot_turn_id` in the payload stores it as
  given; the duplicate-mirror conflict fills the column only when NULL.
- AC-QR-5 (resolve by id): an inbound message whose `message.message.replyTo` has
  `sender.source != "contact"` resolves the quoted engine turn through `replyTo.id` + contact ->
  `chat_histories.chatbot_turn_id`. Trace `received` facts carry
  `quoted_turn: {found: true, via: "id", turn_id}`.
- AC-QR-6 (resolve by stored reply, fallback): when the row is absent or its `chatbot_turn_id` is
  NULL, the quoted turn is the latest engine turn of the same contact and the same `is_test` world,
  created before this message, whose `response.reply.text` equals `replyTo.message.text` ignoring
  whitespace; `via: "text"`. Nothing in the dealer's own text is read.
- AC-QR-7 (context = quoted state): when a quoted turn resolves, the turn runs with the quoted turn's
  stored `focus` and open question as its state: the parser block's `Open question:` and
  `Current subject:` lines are built from them, and the verdict is applied to them.
- AC-QR-8a (non-answer lanes): a quoted message answered on a canned, low-signal, clarify or
  out-of-scope lane ("ok thanks" quoting an older question) leaves the live open question and task
  exactly as they were; the quoted state is never written as the live state by those lanes.
- AC-QR-8 (owner case): live open stock task for product C; two earlier turns asked "How many units
  of A?" and "How many units of B?"; a message quoting the A question with "1 unit" answers A x 1 and
  mentions neither B nor C; a following message quoting the B question with "1 unit" answers B x 1;
  after each, the open stock task the next turn loads is still C, unanswered, and no reply re-asks C.
- AC-QR-9 (non-question quote): quoting a bot message that asked nothing (E2: "I have SRTBF11841.
  What would you like me to know?") with "have stock? or eta date?" answers about SRTBF11841 (the
  quoted turn's focus product): the stock tool is called for it, never for the live task's product,
  and the reply asks only about SRTBF11841. When that answer opens a stock ask of its own, this
  turn's own ask is the one stock task written (one task per kind, as on main when a fresh ask
  replaces an open one); the live task is replaced, never merged into it (reviewer finding, 7 Oct
  2026: a merged task made the next bare number fill BOTH products with a quantity the dealer never
  gave). Crew ruling 7 Oct 2026: (a), the own ask replaces the live one, as a fresh ask does on main.
- AC-QR-10 (already answered, parser-interpreted): quoting "How many units of X?" after X was
  answered at 10 and typing a bare number N answers X x N; "1" vs "1 unit" over a quoted numbered
  pick is whatever the parser declares in `open_question_answer` for the quoted question object; no
  new regex or keyword over the inbound text anywhere in the diff.
- AC-QR-11 (quoted == live): when the quoted turn's state equals the live state, parser context and
  reply are byte-identical to main; facts `same_as_live: true`.
- AC-QR-12 (fallbacks = main): no `replyTo`; `replyTo.sender.source == "contact"`; nothing resolves;
  the quoted turn has no `state` event and no `memory` event. In each the turn runs exactly as on main
  and facts carry `quoted_turn.found == false`.
- AC-QR-13 (expired offer): a quoted turn whose open question is an escalation offer, quoted three or
  more turns later, does not revive the offer; the turn runs on the quoted focus with no pending.
- AC-QR-14 (never loaded from the quoted turn): access levels, profile, reply language and grants are
  the live contact's; a quoted turn of another contact or the other `is_test` world is never used even
  when `replyTo.id` or the text matches.
- AC-QR-15 (trace `state` event): every turn's trace carries one `state` event `{focus, open_question,
  turn_no}` in the wire shapes of `turn/state.py:focus_to_wire` and `turn/pending.py:to_wire`; the
  resolver reads it first and the `memory` event only when it is absent.
- AC-QR-16 (console): `scripts/chatbot_console_check.py` accepts `quote: <text>` on a case and puts a
  bot-sourced `replyTo` with that text on the envelope; a YAML file replays AC-QR-8 and AC-QR-9 against
  the live parser.
- AC-QR-17 (no new wording): every sentence printed exists on main today.

## Console "Reply to" control (crew audit, 7 Oct 2026: the hand test must exercise a quote)

- AC-QR-18 (console backend): `POST /api/v1/system/chatbot/console/turn` accepts an optional
  `reply_to: {id: str, text: str}`; the dry-run envelope then carries
  `message.message.replyTo = {id, sender: {source: "n8n"}, message: {text}}`, exactly the WhatsApp
  webhook shape; with `reply_to` absent or null the envelope carries no `replyTo`. The field is
  declared on the request schema (it is `extra="forbid"`).
- AC-QR-19 (console UI): the composer has a clearable "Reply to" picker (`SearchableSelect`) listing
  the thread's earlier BOT bubbles (newest first, label = the bubble text truncated, greeting lines
  excluded). With a bubble chosen, Send posts `reply_to: {id: <bubble id>, text: <bubble text>}`
  with the typed text; the customer bubble shows the quoted text above the typed text; the picker
  clears after the send; with nothing chosen the request carries no `reply_to`.
- AC-QR-20 (browser pass): on the crew test copy at 1280 px, with two quantity questions open,
  choosing the OLDER question in "Reply to" and sending "1 unit" answers that product and the newer
  question stays open; a non-question bubble chosen as "Reply to" makes its product the subject.

## Own-message quotes (owner on the :3114 hand test, 7 Oct 2026: "can i reply to my own text?")

- AC-QR-21 (resolver, own message by id): when `replyTo.sender.source == "contact"`, the quoted message is
  the dealer's own; `resolve_quoted_turn` resolves it to the engine turn that PROCESSED that message
  (`chatbot.turns` where `contact_respond_id` and `message_id == replyTo.id`, same `is_test` world; no
  `chat_histories` hop, no text route) and returns that turn's stored state (`state` event, else `memory`),
  `via: "own"`. Another contact's turn, the other world, or no such turn -> None.
- AC-QR-22 (engine, own quote = that turn's context): a message quoting the dealer's own earlier message runs
  on the state that turn LEFT (its focus and open question), exactly as quoting the bot reply of that turn
  would; R4 and the non-answer rule apply unchanged. Fallback (today's behaviour, facts found false) only
  when no such turn exists. Example: own "SRTBF11841 have stock?" (bot asked its quantity), then another
  product's ask, then quote the own message + "and eta?" -> incoming stock for SRTBF11841, not the live
  product.
- AC-QR-23 (console): `ConsoleTurnResponse` carries the turn's `message_id` (the dry-run respond message id
  of the customer message); the hook stores it on the customer bubble; the "Reply to" picker lists customer
  bubbles too (newest first, interleaved with bot bubbles by recency, greeting lines excluded); choosing a
  customer bubble sends `reply_to: {id: <that message_id>, text, source: "contact"}` and the envelope carries
  `replyTo = {id, sender: {source: "contact"}, message: {text}}`; a bot bubble sends `source: "n8n"` (the
  default when `source` is absent). `source` accepts only `n8n` and `contact` (422 otherwise).

## Chunked bot replies (owner question on the hand test, 7 Oct 2026)

Measured (n8n export `sub-sendmsg`, code node "Code in JavaScript": LIMIT 3900 chars, 1024 with quick replies,
BYTE_CAP 1000; each part is its own respond.io message; 6 Oct prod copy since 1 Oct: 146 turns sent 2 rows,
29 sent 3, 8 sent 4, up to 93; 535 rows of multi-row sends match the engine's stored reply as a SUBSTRING,
0 as whole-text equality, 97 not at all).

- AC-QR-24 (every chunk is linked): at the outgoing ingest the n8n `turn_id` route stamps EVERY chunk row of
  one send with the same `chatbot_turn_id`; when that route has nothing (the photo path), the stored-reply
  route accepts a chunk: the row's text, whitespace-squashed, EQUAL to the stored reply or a SUBSTRING of it
  when the squashed text has at least 40 characters (a chunk is never shorter in practice; the floor keeps a
  one-word message from matching every reply), newest matching turn wins.
- AC-QR-25 (quoting a chunk): at quote time the text fallback applies the same rule, so quoting the 2nd chunk
  of a long numbered list resolves to the turn that produced the list, `via: "text"`; a quoted chunk shorter
  than the floor matches only by equality. Console case: a long list reply, the quote is its 2nd chunk.

## A quoted answer's follow-up question replaces the live one (owner ruling on the :3114 hand test, 7 Oct 2026)

Trace (copy, contact 487555417, 15:52-15:53 MYT): live question = the CKS105 picker (turn 9c938744); the owner
quoted the OLDER SRTWC286 picker and typed "4" (turn cc159fb4): the bot asked "How many units of
SRTWC286-SH-NEW?" but the state written kept the CKS105 picker as the open question beside the new task (R4
restored `live.pending` because the quantity ask is a task question, not a pending); "10" (turn c24bba32)
then fired `stock_pick_takes_quantity` on the CKS105 picker ("CKS105 x 10: which one? 1-6"). Owner: "if we
reply to, then the picker should be replaced with the reply to, so it feels more natural".

- AC-QR-26 (follow-up replaces the live question): when the turn that answered a quoted question opens a
  follow-up question of its own (a new open question of any kind, or an open stock task that is not the live
  open task), the previously live open question or picker is DROPPED, exactly as a fresh ask drops it today
  (same mechanism as AC-QR-9 (a)); the written open question is this turn's own (none, for a quantity ask
  carried by the task). A later bare number then fills the quoted thread's product. Q3 "newer stays live"
  holds only when the quoted answer opens nothing (AC-QR-8: a plain fill keeps the live task).
  Example: live CKS105 picker; quote the SRTWC286 picker + "4" -> "How many units of SRTWC286-SH-NEW?" and
  no CKS105 picker remains; "10" -> "SRTWC286-SH-NEW x 10: ..." (never "CKS105 x 10: which one?").
