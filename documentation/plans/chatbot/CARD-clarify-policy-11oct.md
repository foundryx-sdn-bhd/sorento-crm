# CARD - one clarify policy: default-and-disclose, collapse, loop breaker (CLARIFY-POLICY)

Status: CARD filed for owner rulings (11 Oct 2026). No code yet. Track: full (M), LEAD pattern.
Base: origin/main fd3eb5a90. Paths below are relative to `sorento_crm_backend/app/services/chatbot/`
unless they start with `app/` or `tests/`.

## Why

Crew failure attribution of the hidden eval (11 Oct): 5 of 47 hidden failures are clarify-policy
errors, plus loops. Today every domain decides on its own when to ask (29 ask sites below), and only
some of them have a repeat guard. Nothing compares the new question with the one the bot just asked
(`previous_reply` is read at `engine.py:4168` but only fed to the parser as context,
`turn/context.py:90, 316-321`).

## Proposed policy (one rule set, every domain)

P1. DEFAULT-AND-DISCLOSE. When a missing parameter has a safe default, answer with it and say the
assumption in one line with how to change it, e.g. "Latest delivery for SRT391-GM: DO 202610-0668,
5 Oct. Another month? Reply with the month." Ask only when no safe default exists or a wrong default
is costly (money, stock promise, sending the wrong file to a dealer).

P2. COLLAPSE. If every option of a picker leads to the same answer (all "no promotion", only one
option that has data, one option at all), answer directly; never show the menu.

P3. LOOP BREAKER. A clarification is fingerprinted by what WE asked (ask kind + parameter + subject
code), never by the user's text. If the next reply would carry the same fingerprint as the bot's last
two replies, change strategy instead: a best-effort answer with what is known (P1 default, or the
facts we hold about the subject) plus a one-line handover offer. Never a third identical question.

P4. NEVER STUCK. Every turn ends in an answer, a question with a NEW fingerprint, or a handover
offer.

P5. Semantic only. Understanding of the user's words stays with the parser; code reads parsed
fields and our own ask fingerprints. No regex or keyword rules on user text. Scope and permissions
unchanged (a default never widens what a contact may see).

## Every ask site today and the proposed policy (owner decides this table)

| # | Ask | Decision site | Proposed | Default value / reason |
| --- | --- | --- | --- | --- |
| 1 | Promotion access level "Which access level do you need for X?" | `lanes/business/tier_gate.py:127-148, 282`; `engine.py:5825-5874, 6197-6214`; copy `lanes/business/answer.py:338-352` | COLLAPSE + DEFAULT (Q2) | All empty already collapses (`answer.py:333-344`). Add: one tier has promotion = answer that tier; dealer / end-user contact = own tier |
| 2 | Generic tier pick "Which one do you mean?" | `turn/narrow.py:410-429` | DEFAULT | `profile.tier` (already used, `narrow.py:426-427`); else same as #1 |
| 3 | Promotion subject ask-back (brand / spec / product type) | `turn_runtime.py:647-717` | MUST-ASK | No subject = no safe default; loop breaker applies |
| 4 | Attachment type "Please provide the attachment type for product X" | `lanes/business/gate.py:495-530`; `lanes/business/answer.py:3134-3139, 3215-3224` | DEFAULT (Q3) | The types the product actually has: one type = send it; several = list what exists |
| 5 | Attachment kind "Which kind of file do you need?" | `turn/narrow.py:500-506`; `turn/compose.py:997` | DEFAULT (Q3) | Same as #4 |
| 6 | Purchase cost product ask | `turn/narrow.py:500-506`; `turn/policy_rows.py:323` | MUST-ASK | No product = nothing to answer |
| 7 | DO period "Which period for X?" and the over-31-days ask | `do_ask.py:104-153`, called `lanes/business/__init__.py:2202-2212` | DEFAULT (Q1) | Subject named (product / customer / SO): latest DOs for it, any month. Owner made this ask in #1433, so Q1 |
| 8 | SO list period | `lanes/business/__init__.py:1520-1548`; `so_status.py:295-335` | DEFAULT (Q1) | Same rule as #7 (newest 20 already exists for the skip switch, `so_status.py:350`) |
| 9 | Sales ranking period / metric / basis / top N | `lanes/business/report_ask.py:225-243, 564-638` | MUST-ASK (keep) | Owner rule: no default for period / metric / basis (`ranking_ask.py:3-4`); top N = 10 after 2nd miss already |
| 10 | Ranking drill scope | `report_ask.py:783-797` | KEEP | Already gives up after 1 miss (`report_ask.py:832-834`) |
| 11 | Top selling grain / metric / who / category | `lanes/business/__init__.py:2050-2081, 509-525` | MUST-ASK | Same owner rule as #9 |
| 12 | Low stock category / supplier / grouping | `lanes/business/low_stock_ask.py:215-243, 416-467` | KEEP | Optional fields already become "no filter" after 2 misses |
| 13 | Ideation idea text | `lanes/ideate.py:50-62` | MUST-ASK | Nothing to file without it |
| 14 | Outstanding "for which document?" SO / DO / Both | `lanes/business/__init__.py:1947-1953` | DEFAULT | "Both" (already the fallback, `__init__.py:1955`) |
| 15 | "Which customer is this outstanding report for?" | `lanes/business/__init__.py:1902-1913` | MUST-ASK | Wrong customer is costly |
| 16 | Product picker "Which product do you mean?" | `lanes/business/gate.py:569-974`, copy `:970` | COLLAPSE | Only one option has the asked data (e.g. "has incoming") = answer it, disclose the others in one line |
| 17 | Customer picker | `lanes/business/gate.py:994-1047` | MUST-ASK | Wrong customer is costly |
| 18 | Narrow roster `{kind}_pick` | `turn/narrow.py:301-479`; `turn/apply.py:2341-2416` | COLLAPSE | Same as #16 |
| 19 | Kind pick (one token, several kinds) | `turn/reconcile.py:112-113`; `turn/apply.py:1797-1817` | COLLAPSE | Only one kind fits the parsed domain = take it |
| 20 | "Which customer is Account N for?" | `engine.py:5157-5169` | MUST-ASK | Ledger data, wrong default costly |
| 21 | Stock quantity "How many units of X?" | `engine.py:6564, 7071-7150`; `turn/task.py:381-400` | MUST-ASK | A yes for 1 unit reads as a yes for 50 (stock promise); loop breaker = `proceed_anyway` ("Not checked", `task.py:248`) |
| 22 | Stock family pick | `turn/task.py:871-895` | COLLAPSE | Same as #16 |
| 23 | "Is N for all K products, or one?" | `turn/apply.py:3040-3092` | DEFAULT | All K (disclose) |
| 24 | Stock denial demand qty | `engine.py:5492-5493, 7894-7896` | MUST-ASK | Same as #21 |
| 25 | Did-you-mean on a miss | `dealer_stock.py:77-129`; `lanes/business/answer.py:5139-5255` | KEEP | Already answers what exists and offers handover |
| 26 | Domain menu (clarify_menu) | `turn/apply.py:2094, 2112-2113`; copy `app/services/chatbot_reply_copy.py:35-46` | Q5 | Generic topic menu |
| 27 | LLM clarifier (casual / vague) | `turn/apply.py:2114-2127`; `lanes/casual.py:259-330` | KEEP + loop breaker | One short question by design |
| 28 | Escalation team / company / member pickers | `lanes/escalation.py:564, 639, 1269` | MUST-ASK | Handover target; already re-words on a re-show (`escalation.py:427-434`) |
| 29 | Entities only "What would you like me to know?" | `turn/compose.py:1170-1214` | Q5 | Generic menu |

Loop breaker (P3) covers every row, including the asks that store no pending today (#4, #7, #8,
#26, #27; `do_ask.py:14-17` says it on purpose). Existing guards stay: `required_fields.py:85`
`MAX_MISSES=2`, `turn/narrow.py` settled carry, `_MIN_ROSTER_OPTIONS` (`answer_bridge.py:112`).

## Real DEV examples (eval DEV split, contacts masked)

1. cand-0258 (dealer, prod 6 Oct). "drawing" -> no Technical Specifications; "size?" -> "Please
   provide the attachment type for product CB1512ASS - e.g. product image, technical drawing, or
   certificate."; photo -> same ask again; "I want the DESCRIPTION SIZE!!!". Wanted: CB1512ASS
   description, no dimensions on record. P3 breaks the third ask; Q3 default removes the first.
   (Main v50 replay now answers the product card, graded `stuck` for repeating its own previous
   reply, so loop detection must key on asks, not on any repeated text.)
2. cand-0265 (staff). "CGB1032B promotion" -> prod menu "1. Office - no promotion 2. Dealer - no
   promotion 3. End user - no promotion". Main v50 already says "No current promotion for
   CGB1032B." (`answer.py:333-344`). Kept as a regression test.
3. cand-0274 (dealer). "Srtwc286 offer" -> "Which access level do you need for Srtwc286? 1. Dealer -
   has promotion 2. End user - has promotion". Expected: no live dealer promotion, say so. A dealer
   gets its own tier (Q2); the "has promotion" probe disagreeing with the file is a separate data
   defect (UNVERIFIED cause, out of scope unless Q2 (a) fixes it).
4. cand-0216 (staff, 6 Oct). "[customer] 有下单MBF97521 吗" -> "Which period for
   [customer]? - This month (Oct 2026) - Last month (Sep 2026)". Expected order
   M2609-0749 (September). A "this month" default would miss it; "latest for the subject" finds it.
5. cand-0036 (staff). "how can i ask marketing about bidet seat cover?" after "escalate marketing"
   -> prod: generic topic menu. Expected: offer the marketing handover. Main v50 passes.

## Edge cases

- Default and scope: the default is applied AFTER scope (a dealer's "latest DO" is the latest DO it
  may see). Nothing widens.
- Disclosure line is translated like every other label (ms / zh catalogue).
- A default answer that comes back empty says what was searched ("No delivery for X since <date>")
  and offers the change line; it does not fall back to the question.
- Loop fingerprint ignores wording changes (the escalation re-word at `escalation.py:427-434`
  still counts as the same ask).
- A user who answers the question (parser `open_question_answer`, `required_fields.py:330-345`)
  resets the fingerprint, even if the next ask has the same kind for a different subject.
- Quoted reply to an old question: fingerprint compares with the bot's last two replies only.
- Never-stuck handover offer respects `escalation_allowed` per contact; without it, best effort
  plus "Please refer to your salesman." (today's refer-only copy).

## Questions (owner)

Q1. DO / SO period ask (#7, #8). Owner added it for every DO and SO list in #1433.
(a) A named subject (product, customer, SO) with no dates: show its latest DOs, any month (newest 5),
and disclose "Another month? Reply with the month."; a bare "my DOs" still asks.
(b) Default "this month" for every list, disclose. (c) Keep asking (today).
Recommend (a): cand-0216 shows "this month" misses the real answer, and a bare list still needs a window.

Q2. Promotion tier (#1, #2).
(a) Dealer / end-user contacts get their own tier, never the menu; staff: if only one tier has a
promotion, answer it; menu only when 2+ tiers have DIFFERENT promotions.
(b) Staff always get every tier that has a promotion, labelled, no menu. (c) Keep today.
Recommend (a): cand-0274 / 0265; the menu stays where the choice changes the answer (cand-0267).

Q3. Attachment type (#4, #5).
(a) Default to what the product has: one type = send it; several = reply with the list of types it
has ("CB1512ASS has: Product Photos, Certification. Reply with the one you need.") which is an
answer about the product, not a blind ask; none = say so and refer.
(b) Keep asking, but list only types the product has. Recommend (a).

Q4. Loop breaker threshold and move (P3).
(a) Ask, re-ask once, then the third identical ask becomes best effort + handover offer in one reply.
(b) Break on the second: never re-ask the same thing; second time is best effort + handover.
Recommend (a): matches today's `MAX_MISSES=2` (`required_fields.py:85`) and the brief ("never a
third identical question").

Q5. Generic menus (#26 domain menu, #29 "What would you like me to know?").
(a) When the parser read a subject (codes or a team), answer the most likely reading for that
subject and add one line "Something else? Tell me what you need about X."; the menu stays only when
the message has no subject at all.
(b) Keep the menu but list only domains this contact can use, at most 4.
Recommend (a) (cand-0036). UNVERIFIED: whether the parser already returns a ranked second domain;
if not, (a) uses the subject's default domain from `turn/policy_rows.py`.
