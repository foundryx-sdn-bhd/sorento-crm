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
- AC-QR-8 (owner case): live open stock task for product C; two earlier turns asked "How many units
  of A?" and "How many units of B?"; a message quoting the A question with "1 unit" answers A x 1 and
  mentions neither B nor C; a following message quoting the B question with "1 unit" answers B x 1;
  after each, the open stock task the next turn loads is still C, unanswered, and no reply re-asks C.
- AC-QR-9 (non-question quote): quoting a bot message that asked nothing (E2: "I have SRTBF11841.
  What would you like me to know?") with "have stock? or eta date?" answers about SRTBF11841 (the
  quoted turn's focus product), while the live open task (SRTWC201) stays open and is not re-asked.
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
