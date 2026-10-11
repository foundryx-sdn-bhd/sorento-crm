# UAC - CLARIFY-POLICY (default-and-disclose, collapse, loop breaker)

Card: `CARD-clarify-policy-11oct.md`. Plan: `PLAN-clarify-policy-11oct.md`.
Status: DRAFT on the card's recommendations (Q1-Q5 all (a)), pending owner rulings. An AC tagged
with a Q is rewritten if that Q is ruled otherwise.

Fake names and codes only in tests (`ZZT...`, "Test Dealer"). Every reply quoted below is exact
unless marked "contains".

## Loop breaker and never-stuck (P3, P4; Q4 (a))

- AC-1 The same clarification (same ask kind, same parameter, same subject code) may be sent at most
  twice in a row to one contact. When the turn would send it a third time, the reply is instead a
  best-effort answer from what is known plus a one-line handover offer; it is not flagged as a
  clarification and arms no pending question for that ask.
- AC-2 "Same clarification" is decided from what the BOT asked (a fingerprint the bot stores with its
  own reply), never from the user's text and never from the reply wording: two asks with different
  wording but the same kind / parameter / subject count as the same.
- AC-3 An ask for the same parameter about a DIFFERENT subject is a new clarification (count restarts).
- AC-4 A turn the parser reads as an answer to the open question, or any non-clarification reply in
  between, resets the count.
- AC-5 Covers asks that store no pending today: the DO / SO period ask, the attachment-type ask, the
  domain menu, the LLM clarifier (`do_ask.py:14-17`, `lanes/business/answer.py:3215-3224`,
  `chatbot_reply_copy.py:35-46`, `lanes/casual.py:259-330`).
- AC-6 The handover line respects the contact's escalation switch: with escalation allowed it offers
  the team that owns the topic (today's offer copy); without it, "Please refer to your salesman."
- AC-7 Replay of DEV cand-0258 (prod history: attachment-type ask sent twice, user then writes "I want
  the DESCRIPTION SIZE!!!"): the reply does not contain "provide the attachment type".

## Promotion access level (Q2 (a))

- AC-8 A contact entitled to more than one level but whose own audience is dealer or end user gets
  that level's promotions directly, never the "Which access level" menu. (UNVERIFIED: how a contact's
  own level is derived; coder confirms from `turn/state.py` Profile.tier / access types and cites it.)
- AC-9 Staff (office) with several levels: when exactly one level has a live promotion for the
  subject, the reply is that level's promotion, with one disclose line naming the level
  ("Dealer promotion shown. Another level? Reply office or end user." contains the level name).
- AC-10 Staff, two or more levels have DIFFERENT live promotions: the menu stays (cand-0267 shape).
- AC-11 Staff, two or more levels with promotions where every level resolves to the SAME promotion
  rows: no menu; the rows are sent once.
- AC-12 Regression: every level empty still replies "No current promotion for <CODE>." (cand-0265,
  `answer.py:333-344`); the availability map empty `{}` with a named subject also replies that,
  never a menu of "- no promotion" lines.

## Attachment type (Q3 (a))

- AC-13 A product attachment ask with no attachment type, for a resolved product that has exactly one
  attachment type on record: the files of that type are sent, with a one-line disclose naming the
  type.
- AC-14 Same, product has several types: the reply lists the types the product HAS (by display name),
  e.g. contains "ZZT100 has:" and each type, and ends "Reply with the one you need."; it never says
  "e.g. product image, technical drawing, or certificate" and never lists a type the product lacks.
- AC-15 Same, product has none: says the product has no files on record and gives the refer /
  handover line; no type question.
- AC-16 Scope unchanged: types counted only over attachments the contact may see (existing access
  level filter).

## DO / SO period (Q1 (a))

- AC-17 A DO list ask that names a subject (product, customer or SO) and carries no dates fetches
  with no date window, newest first, at most 5 rows, and the reply ends with the disclose line
  "Another month? Reply with the month." (translated per reply language).
- AC-18 Same for the SO list (newest 5, same disclose line).
- AC-19 A bare DO / SO list ask with no subject and no dates still asks "Which period?" (today's copy).
- AC-20 A range over 31 days still gets today's over-31-days ask (unchanged, `do_ask.py:135-153`).
- AC-21 The per-contact `chatbot_skip_period_questions` switch keeps its behaviour (newest 20, no
  disclose line needed).
- AC-22 Customer scope applies before the default: the default never shows a DO / SO outside the
  contact's linked customers (`lanes/business/__init__.py:2195-2199` stays before the period step).
- AC-23 A default that finds nothing replies "No delivery orders found for <subject>." plus the
  disclose line, not the period question.

## Collapse pickers (P2)

- AC-24 Product picker (`lanes/business/gate.py:970`) and stock family pick (`turn/task.py:871-895`):
  when the options are annotated with whether they hold the asked data and exactly ONE option holds
  it, the bot answers for that option and adds one line naming the others ("Also matched: A, B.").
- AC-25 Outstanding with no document named: default "Both" with a disclose line, no SO / DO / Both
  question (`lanes/business/__init__.py:1947-1955`).
- AC-26 Must-ask rows from the card table (#3, #6, #9, #11, #13, #15, #17, #20, #21, #24, #28) are
  unchanged apart from the loop breaker.

## Generic menus (Q5 (a))

- AC-27 A message whose parse carries a subject (a resolved code or a named team) but no domain gets
  an answer for the subject's default domain plus "Something else? Tell me what you need about
  <subject>." instead of the topic menu or "What would you like me to know?".
- AC-28 A message with no subject and no domain keeps today's menu.

## Never

- AC-29 No regex / keyword test on user text is added anywhere (reviewer greps the diff).
- AC-30 No permission or scope change: every default runs after the existing scope filters.
