# PLAN: mention email toggle + two chat render fixes

Status: BUILT - all three slices green 30 Aug 2026 (pytest + vitest + browser runs); uncommitted, awaiting PR go

## Journey

1. A colleague @-mentions me in an internal note. I get the in-app bell; if my browser is
   subscribed I also get a push; and, unless I switched it off in My Account, an email.
   Each channel is independent. Nobody guesses whether another channel "arrived".
2. Reading a thread, a contact's quote-reply to a bot option prompt shows the prompt's
   words, not `[quick_reply]`.
3. An internal note that names its addressee inline shows that name once, not twice.

## Decisions (captain, 30 Aug 2026)

- R1 Email for mentions is a per-user opt-in column `users.notify_email_on_mention`,
  default true, edited in the SAME place as every other `notify_email_on_*` flag (My
  Account > Notification channels; admin user edit dialog). Copies the mechanism that
  earned it (`email_pref_attr` gate in `create_with_channel_preferences`).
- R2 No "email only if push failed" logic. Push accept (201) is not delivery and not
  reading; the signal does not exist, so nothing is built on it.
- R3 No WhatsApp twin for the mention event.
- R4 Web push stays as built (TCK-33, PR #287). Known limits recorded in the diagnosis
  memory: iOS needs home-screen install, `denied` is sticky, rotated subscriptions are
  pruned without re-subscribe.

## Slices

| Slice | What | State |
|---|---|---|
| A | `describeQuotedContext` reads `title` then option labels (FE) + `_line_for` title fallback (BE AI draft) | built, tests green, browser PASS |
| B | Internal note header lists only names the body does not carry inline | built, tests green, browser PASS |
| C | `notify_email_on_mention` column (migration 444), schemas, both dict builders, `_notify_mentions` gate, account matrix row, admin dialog, tests | in build |

## Batch 2 (30 Aug afternoon, same lane)

| Slice | What | State |
|---|---|---|
| D | Internal-note bodies linkify URLs (shared `lib/linkifySegments.ts`, also feeds `whatsappText`) | built, 1523 vitest pass |
| E | `subscribeToPush` returns typed failure; toasts explain push-service-blocked (Brave guidance), permission-denied, unsupported | built |
| F | Intervention drawer sm:max-w-2xl / lg:max-w-4xl, chat cap 45vh to 65vh | built |
| G | n8n `sorento-consume-main`: 5 nodes concat `replyTo.message.text` bare, quoted quick_reply = "reply to: undefined" in ticket titles; fix = text/title/[type] fallback (sub-query-reformulator already has it) | AWAITING GO (live workflow) |
| H | Mention typeahead: all active users, first 8 until typed; Kia Yee repro | investigating |

## Verification

- A/B: vitest (`respondIoChatRender.test.ts`, `RespondChatList.comments.test.tsx`), pytest
  `test_ticket_ai_draft.py`, agent-browser run on CONTACT O +60180000507 and CONTACT Y
  +60160000508 threads (screenshots in the session scratchpad).
- C: pytest `test_ticket_comment_mention_email.py` (email delivery + outbox row when on,
  none when off, API round trip); vitest on the matrix; browser: toggle in My Account,
  tag self from another account, see the outbox row under Email Outbox.
- Prod acceptance: tag a colleague, open Email Outbox, the row exists regardless of SMTP.

## Out of scope

SMTP host correctness in prod (checked by Settings > SMTP > Test), push adoption
prompts, `pushsubscriptionchange` handling.
