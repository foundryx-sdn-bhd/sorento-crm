# UAC - CLARIFY-POLICY (policy as data, loop breaker, generic menus, carried subject)

Card: `CARD-clarify-policy-11oct.md` (+ `.html`). Plan: `PLAN-clarify-policy-11oct.md`.
Status: v3 after owner rulings Q1-Q5, R1/R2, Q9 and crew rulings Q6-Q8 (a) (11 Oct 2026). and Q10. v1 AC numbers are retired, not reused.

Fake names and codes only in tests (`ZZT...`, "Test Dealer"). Quoted replies are exact unless
marked "contains".

## Unchanged by ruling (regression guards)

- AC-101 (Q1) A DO list ask with no dates, with or without a named subject, still asks "Which
  period..." and fetches nothing (today's `do_ask.range_reply`); same for the SO list.
- AC-102 (Q2) The promotion access-level menu is shown exactly as today; "every level empty" still
  replies "No current promotion for <CODE>." (cand-0265).
- AC-103 (Q3) A product attachment ask whose parse carries no attachment type still asks for the
  type; the bot never sends files of a type the parser did not name. A parse that names a type sends
  that type.
- AC-104 The per-contact `chatbot_skip_period_questions` switch keeps its behaviour.

## Policy as data (R1, Q7, Q8)

- AC-110 A table `chatbot_clarify_policy` exists with columns `ask_kind` (PK), `label`, `mode`,
  `allowed_modes`, `default_note`, `repeat_limit`, `updated_at`, `updated_by`; added by an additive
  migration that seeds 31 rows from ONE constant list in code (the card's table), each with today's
  mode, except `generic_menu` / `entities_only` = `default` (Q5) and `carry_combine` /
  `carry_unclear` = `ask` (R2, Q9).
- AC-111 `mode` must be one of the row's `allowed_modes` (DB CHECK or trigger plus a service-level
  check); an UPDATE to a mode outside it fails.
- AC-112 Allowed modes: `do_period`, `so_period` = {ask, default}; `outstanding_scope` = {ask,
  default}; `carry_combine`, `carry_unclear` = {ask, carry, drop}; `generic_menu`, `entities_only` = {ask, default};
  every other kind = {its seeded mode} only.
- AC-113 The chatbot reads the policy once per turn through one function. An empty or missing table
  (blank-schema tests) falls back to the seed constant; a read error falls back to the seed too,
  never to "no ask".
- AC-114 `do_period` / `so_period` set to `default`: a DO / SO list ask with no dates behaves exactly
  like the skip switch path (newest 20, no window) for every contact. Seed `ask` = today.
- AC-115 `outstanding_scope` set to `default`: no "Outstanding for which document?" question, scope =
  both. Seed `ask` = today.
- AC-116 `repeat_limit` per row drives the loop breaker (AC-120); seed 2.

## Loop breaker (P3, Q4)

- AC-120 The same clarification (same ask kind, same parameter, same subject code) is sent at most
  `repeat_limit` times in a row to one contact. The next turn that would send it again instead
  replies with one plain line naming what is missing (contains the subject code and the missing
  thing, e.g. "I still need the attachment type for ZZT100.") plus the handover offer when the
  contact's escalation is allowed, or "Please refer to your salesman." when it is not. It contains
  NO fetched data and no guessed value; it is not flagged as a clarification and arms no pending
  for that ask.
- AC-121 "Same clarification" is decided from a fingerprint the bot stores with its own reply, never
  from the user's text or the reply wording.
- AC-122 Same parameter, different subject = a new clarification (count restarts).
- AC-123 A turn read as an answer to the open question, or any non-clarification reply in between,
  resets the count.
- AC-124 Covers asks that store no pending today (DO / SO period, attachment-type text ask, domain
  menu, LLM clarifier).
- AC-125 Replay of DEV cand-0258 (attachment-type ask sent twice, then "I want the DESCRIPTION
  SIZE!!!"): the reply does not contain "provide the attachment type".

## Generic menus (Q5)

- AC-130 With `generic_menu` = `default` (seed): a message whose parse carries a resolved subject
  but no domain gets an answer for the subject's default domain plus "Something else? Tell me what
  you need about <subject>." instead of the topic menu or "What would you like me to know?".
- AC-131 A message with no subject and no domain keeps today's reply.
- AC-132 With the row set to `ask`, today's menu returns.

## Carried subject (R2, Q6, Q9)

Terms. "Carried A": the focus holds a subject of kind A from an earlier question, the new message has
no `current_message: true` entity of kind A, and A is a kind the new ask's domain accepts. "Clear":
the parser sets `anaphora.backward_reference` true, or `continuation` true (Q10, same subject
follow-up, AC-147), or the turn is a REFINE of the held question
(`turn/decide.py`), or it answers a held question. Nothing else counts; no keyword reading.

- AC-140 (`carry_combine` = ask, seed) Carried A, NOT clear, and the message names a subject of a
  different kind B: the bot fetches NOTHING and replies exactly
  "<Ask label> for <B>: only <A> (from earlier) or all <A plural>?\n1. Only <A>\n2. All <A plural>"
  (e.g. "Delivery for ZZT TRADING: only ZZT391 (from earlier) or all products?").
- AC-141 (`carry_unclear` = ask, seed) Carried A, NOT clear, no new subject: the bot fetches NOTHING
  and replies exactly "<Ask label>: for <A> (from earlier) or all <A plural>?\n1. <A>\n2. All
  <A plural>" (e.g. "Delivery today: for ZZT391 (from earlier) or all products?"). The ask label
  carries the parsed date words the parser already resolves (e.g. "today"), from parsed fields.
- AC-142 Carried A and CLEAR ("delivery for this" with ZZT391 in focus, parser backward_reference
  true): no question; the ask runs with A (today's carry).
- AC-143 Answer position 1 (or the parser's pick of it) runs the ask with A kept; position 2 or a
  parser `all` runs it without A (dropped from this ask's focus). No keyword reading of the reply.
- AC-144 No question when: the message restates A; A and B are the same kind (a new customer
  replaces the old one); no carried subject; A is not a kind the new domain accepts.
- AC-145 Mode `carry` on either row = today's behaviour (kept, no ask); `drop` = A dropped, no ask.
- AC-146 Replay of the DEV cand-0230 shape (customer delivery, then product delivery, then a bare
  month): the bot asks instead of listing rows filtered by both. The carry asks count for the loop
  breaker (AC-120).
- AC-147 (Q10) An elliptical follow-up the parser reads as `continuation` true ("eta?" right after a
  stock answer for ZZT391) answers for ZZT391 with no question.
- AC-148 (Q10) No time or turn expiry of the carried subject: a carried subject is still carried on a
  turn whose previous turn is days old, and after several turns in between (no clock anywhere).
- AC-149 (Q10) A new unlabelled `chatbot_semantic_parser` version carries the "SAME SUBJECT OR A
  NEW ONE" `continuation` definition word for word from INTENT-FRAMES (#1473); the `production`
  label does not move. Live DEV grading of the new version on the follow-up / fresh-question
  phrasings (crew runs it, dev key) before the owner promotes it.

## Never

- AC-150 No regex / keyword test on user text anywhere in the diff (reviewer greps).
- AC-151 No permission or scope change; defaults run after the existing scope filters.
- AC-152 No guessed value in any reply (Q3, Q4).
