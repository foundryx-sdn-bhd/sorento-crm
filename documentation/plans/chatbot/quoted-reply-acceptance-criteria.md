# UAC: QUOTED-REPLY

Plan: `PLAN-quoted-reply-07oct.md`. Draft: AC-QR-2, AC-QR-4, AC-QR-5, AC-QR-6 track the card's
recommendations and are revised on the owner's answers (Q1 to Q5).

- AC-QR-1 (target): an inbound message whose webhook body carries `message.message.replyTo` with
  `sender.source != "contact"` resolves the quoted engine turn of the same contact and the same
  `is_test` world: first by `replyTo.id` -> `chat_histories` (outgoing row, `turn_id`) -> incoming row
  -> `message_id` -> `chatbot.turns.message_id`; else by exact, whitespace-insensitive equality of
  `replyTo.message.text` with `chatbot.turns.response.reply.text`, latest turn created before this
  message. The resolved turn's stored open question (pending wire) or open stock task (task wire)
  is the quoted question.
- AC-QR-2 (answer the quoted question): when the quoted question exists and is not the live open
  question, the parser context's `Open question:` object is the quoted one, and the verdict's
  `open_question_answer` fills the QUOTED question: a stock quantity ask answers that product
  (`<code> x <qty>: ...`), a numbered pick resolves against the quoted options, a yes/no answers the
  quoted offer. The reply names the quoted product, never the live one.
- AC-QR-3 (owner case): live stock task for product C; two older bot questions "How many units of A?"
  and "How many units of B?" each stored on their own turn; a message quoting the A question with
  "1 unit" answers A x 1; a following message quoting the B question with "1 unit" answers B x 1.
  Neither reply mentions C.
- AC-QR-4 (live question untouched): after AC-QR-2 the live open question is still the open question
  the next turn loads, unless the quoted turn's answer itself opened a new question; the reply does
  not re-ask the live question.
- AC-QR-5 (already answered): quoting "How many units of X?" after X was answered at 10 and typing a
  bare number N answers X x N (revision), no fallback to the live question.
- AC-QR-6 (fallbacks, today's behaviour byte for byte): no `replyTo`; `replyTo.sender.source ==
  "contact"`; `replyTo.id` and text both resolve nothing; the quoted turn stored no question; the
  quoted turn's stored question is an escalation offer whose TTL has lapsed. In every case the turn
  runs exactly as on main (same verdict context, same reply) and the `received` trace facts carry
  `quoted_question.found == false` with the `via` that was tried.
- AC-QR-7 (quoted == live): when the quoted question IS the live open question, nothing changes:
  same parser context, same reply, trace `quoted_question.found == true, same_as_live == true`.
- AC-QR-8 (trace): every turn's trace carries a `question` event with the open question and the
  stock task it left (wire shapes of `turn/pending.py:to_wire` and `turn/task.py:task_to_wire`),
  under 2 KB for a one-product task; the resolver reads it first and the `memory` event only when
  the `question` event is absent (turns stored before this change).
- AC-QR-9 (no cross-contact, no cross-world read): the resolver never returns a turn of another
  contact, nor a live turn for a dry-run envelope or a dry-run turn for a live envelope, even when
  the quoted text is identical.
- AC-QR-10 (no text rules on the dealer's words): no new regex or keyword over the inbound text;
  "1" vs "1 unit" on a quoted numbered pick is whatever the parser declares in
  `open_question_answer` for the quoted question object.
- AC-QR-11 (console): `scripts/chatbot_console_check.py` accepts `quote: <text>` on a case and puts a
  bot-sourced `replyTo` with that text on the envelope; a YAML file replays AC-QR-3 against the live
  parser with a PASS/FAIL table.
- AC-QR-12 (no new wording): every sentence printed exists on main today.
