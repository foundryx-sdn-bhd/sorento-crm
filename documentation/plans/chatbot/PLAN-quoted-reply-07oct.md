# PLAN: a quoted reply answers the quoted question (QUOTED-REPLY)

Status: in review, round 5 own-message quotes (owner hand test ruling 7 Oct) building; (owner answered the card 7 Oct 2026: Q1 id route with the photo link fixed on our side, Q2 (a) parser-interpreted, Q3 (a), Q4 (b) any quoted bot message is the context, Q5 (a) all kinds); track: feature L (one migration: `chat_histories.chatbot_turn_id`; backend only, no auth/RBAC change, no new ingest surface, no UI); LEAD pattern (tester -> coder -> reviewer + security-reviewer)

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

## Rules (owner rulings, 7 Oct 2026)

R1 Link (Q1). Every bot reply the engine answered is linked to its engine turn BY ID on our side:
   `chat_histories.chatbot_turn_id` (new nullable column, uuid of `chatbot.turns.id`), stamped at the
   OUTGOING ingest (`app/api/v1/external/chat_history.py`):
   (i) `payload.chatbot_turn_id` when n8n passes the engine turn id (new optional payload key; the
       sub-sendmsg save already passes `turn_id` through the same input map, so the owner can add it
       later; not part of this lane);
   (ii) else the incoming row of the same contact with the same n8n `turn_id` -> its `message_id` ->
       `chatbot.turns` (contact, message_id, is_test false): measured 37/43 question quotes;
   (iii) else the latest `chatbot.turns` row of the contact finished within 120 s before `sent_at` whose
       `response.reply.text` equals the message whitespace-insensitively (the photo path, where the
       incoming row carries no `turn_id`): the remaining 6 of 43. Time alone is not safe (39/43 agree,
       3 disagree, measured), so the text equality is the gate and the window only bounds the scan.
   Stamping is fill-if-null on the duplicate-mirror conflict like the other columns. A reply n8n
   composed alone has no engine turn and stays NULL (1/43).
R2 Resolve (Q1). An inbound message with `replyTo` whose `sender.source != "contact"` resolves the
   quoted turn: `replyTo.id` + contact -> `chat_histories` row -> `chatbot_turn_id` -> `chatbot.turns`.
   Fallback, for rows stamped NULL or written before this ships: the text equality of (iii) on
   `replyTo.message.text` (the bot's own stored reply, never the dealer's words), same contact, same
   `is_test` world, latest before this message. Nothing else.
R3 Context (Q4 (b), Q5 (a)). The quoted turn's stored state is this message's context: the turn loads
   `focus` and the open question from the quoted turn's trace (`state` event, see Design) instead of
   the live ones. The parser then reads the quoted question as `Open question:` and the quoted subject
   as `Current subject:` with no new rule, and APPLY answers through the seams it uses today
   (`turn/apply.py:3298-3310`, `:3396`). Every kind: stock quantity ask, numbered pick, yes/no, offer,
   and a bot message that asked nothing (its products / customers / document / list become the
   subject).
R4 Live question untouched (Q3 (a)). After the turn, the open question and the open stock task written
   back are the LIVE ones whenever this turn did not open a question or task of its own; the quoted
   branch's answered task is not carried over an open live one. The reply never re-asks the live
   question. The rest of the focus is what this turn left (the conversation continued from the quoted
   message).
R5 Already answered (Q2 (a)). The quoted turn's state carries the answered task; a bare number is its
   revision (`turn/task.py:667-708`); the parser decides quantity vs pick from the quoted question
   object (no code text rule).
R6 Fallback = today's behaviour: no `replyTo`; quote of the dealer's own message; nothing resolves; the
   quoted turn has no stored state (capped or pre-change trace). An escalation offer in the quoted
   state is not revived when `live turn_no - quoted turn_no >= 3` (`turn/pending.py:301-339`).
R7 Never loaded from the quoted turn: `access_levels`, profile, reply language, grants.

## Real examples (6 Oct prod copy, times MYT)

E1 266b6326: quoted "How many units of SRTWC201?" + "10 unit" -> "SRTWC201 x 10". Quoted == live. Unchanged.
E2 6207c208: quoted "I have SRTBF11841. What would you like me to know?" + "have stock? or eta date?"
   -> today "How many units of SRTWC201?" (live task). After R3: context = turn a9201085's focus
   (products [SRTBF11841], no task) -> stock and incoming for SRTBF11841; live SRTWC201 task stays (R4).
E3 534e55aa: quoted tier pick + "dealer" -> promo flyer. Unchanged.
E4 775bfd28: quoted an answered six-product verdict + "whick item discontinued?". Quoted == live
   state; behaviour governed by issue #1521, unchanged here.
E5 e772884d (the reporting dealer, 5 Oct): quoted a human relay "SRT112 still in production" + "Can
   check estimated loading date?" -> answered about the live product SRTWT6232-GM. That message has no
   engine turn (n8n sent it alone): R6, unchanged. Only n8n stamping a turn id could cover it.
E6 Owner case shape: live task C; quote "How many units of A?" + "1 unit" -> A x 1, C stays open;
   quote "How many units of B?" + "1 unit" -> B x 1.

## Design (the narrowest seams)

- Migration `qr_0001_chat_histories_chatbot_turn_id`: `ALTER TABLE chat_histories ADD COLUMN
  chatbot_turn_id VARCHAR(36) NULL`. No index: the lookup goes through the existing
  (contact_id, message_id) index first. Model `app/models/chat_history.py`.
- `app/services/chatbot/quoted.py` (new): `link_outgoing_row(db, *, contact_id, turn_id, message, sent_at,
  explicit) -> str | None` (R1) and `resolve_quoted_turn(db, *, contact_respond_id, reply_to_id,
  quoted_text, is_test, before) -> QuotedTurn | None` (R2) with `turn_id`, `via` (`id` | `text`),
  `focus` (wire), `open_question` (wire), `turn_no`. Reads `ChatHistory` and `ChatbotTurn` only.
- Ingest: `ingest_chat_message` calls `link_outgoing_row` for `type == "outgoing"` and writes the
  column (INSERT value + `COALESCE` on conflict). Schema gains optional `chatbot_turn_id`.
- Trace: the tail writes one `state` event per turn, `{focus: <focus wire>, open_question: <pending
  wire>, turn_no}` (max ~29 KB measured on focus.after since 1 Oct, under the 32 KB `_cap`; the
  `memory` event peaked at 31,953 B and is the fallback for turns recorded before this ships).
- Engine, after `turn_runtime.load_state` (`engine.py:3961`): when `_reply_to_message_id(envelope)` is
  set and the quoted sender is not the contact, resolve; when found, `state_in = State(focus=<quoted
  focus>, pending=<quoted open question, R6 TTL>, profile=live, turn_no=live, ideation=live)` and keep
  the live `pending` / open stock task aside for R4, applied where the tail builds the written state.
  `received` facts: `quoted_turn: {found, via, turn_id, same_as_live}`.
- Console: `scripts/chatbot_console_check.py` case key `quote: <text>` puts a bot-sourced `replyTo`
  with that text on the envelope (id synthetic); the text fallback resolves the dry-run turn of the
  same run (same `is_test` world).
- No parser prompt change. No new wording.

## Console "Reply to" control (crew audit, 7 Oct 2026)

The Chatbot Console had no way to quote, so the owner could not hand test the feature. Added in
this lane as a dev tool with existing components only: `reply_to: {id, text}` on the console turn
request (`app/schemas/chatbot_turn.py` ConsoleTurnRequest, `console_service._build_envelope`), and a
clearable `SearchableSelect` "Reply to" picker on the composer
(`sorento_crm_frontend/app/(protected)/system-management/chatbot-console/`) listing earlier bot
bubbles. UAC AC-QR-18 to 20. Red-first (vitest + pytest), browser pass at 1280 on the copy.

## Tickets (one lane, slices land as commits)

1. `test(red)`: tester writes the red tests from the UAC, commits them alone, red-proof posted.
2. Coder: migration + model + schema + `quoted.py` + ingest stamping + `state` event + engine seam +
   console key, until green. Worker-free; `alembic upgrade head` on the crew copy before hand test.
3. Kill test, reviewer, security-reviewer (contact and `is_test` scoping of both reads; nothing from a
   quoted turn may re-grant access), live console pass, hand test script.

## Open

None blocking. n8n passthrough of the engine turn id on the outgoing save (owner, later) retires the
text step (iii) for photo replies.

## Browser evidence run (agent-browser, crew copy :3114 on 4e2c2c16e, 7 Oct 2026, 1280 wide)

Login, sidebar System > Messaging > Chatbot Console, contact 487555417, Reset. Then:
1. "check stock WDI-K5130" -> "How many units of WDI-K5130?"
2. "check stock WDI-K301-D" -> "How many units of WDI-K301-D?"
3. "Reply to" combobox above the message box: clearable, bot replies newest first, no UUIDs, no greeting lines.
4. Reply to = the K5130 question, "1 unit" -> "WDI-K5130 x 1: the quantity is more than what I can confirm here..."
   (no K301-D); the quoted question shows above "1 unit"; picker empty after the send.
   POST /api/v1/system/chatbot/console/turn body carried reply_to {id: bot-96b4574d-...-0, text: "How many
   units of WDI-K5130?"}.
5. Reply to = the K301-D question, "1 unit" -> "WDI-K301-D x 1: ...".
6. Reset; "SRTBF11841 eta" -> "SRTBF11841: No ETA ..."; "Do you have stock of SRTWC201" -> "How many units of
   SRTWC201?"; Reply to = the "SRTBF11841: No ETA" bubble, "have stock?" -> "Carrying on from Wed 7 Oct:
   incoming stock for SRTBF11841. How many units of SRTBF11841?" (not SRTWC201).
7. Unquoted sends carried no reply_to key. Console and error listings empty.
Screenshots: `evidence/quoted-reply-console-1280.png` (after step 4), `evidence/quoted-reply-console-375.png`
(composer with the picker, nothing clips).
