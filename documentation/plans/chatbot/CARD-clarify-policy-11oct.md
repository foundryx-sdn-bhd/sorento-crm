# CARD - one clarify policy, stored as data, plus the carried-subject ask (CLARIFY-POLICY)

Status: Q1-Q5 RULED by the owner (11 Oct 2026); two new owner requirements added; Q6, Q7, Q8 (a)
ruled by crew (11 Oct); Q9 and Q10 ruled by the owner (11 Oct): ask when unclear, carry when clear; an elliptical follow-up continues the held subject; no time-based expiry ever. Track: full, now L (adds a table + seed migration). LEAD pattern. PR #1570 (draft).
Base: origin/main fd3eb5a90. Paths below are relative to `sorento_crm_backend/app/services/chatbot/`
unless they start with `app/`, `alembic/` or `tests/`.

## Owner rulings (11 Oct 2026)

- Q1 DO / SO period ask: RETAIN today's behaviour (the ask stays).
- Q2 Promotion access-level ask: KEEP today's behaviour.
- Q3 Attachment type: NO default to "the types the product has". The parser reads what the user
  means ("drawing" = the technical drawing type) and the bot sends THAT type; if it cannot tell
  which type, it asks. Never a guessed type, never false data.
- Q4 Loop breaker: never assume. On the 3rd identical question, NO best-effort guessed answer:
  offer the handover, or say plainly what is missing.
- Q5 Generic menus: YES (a). A message that names a subject gets an answer for it plus
  "Something else? Tell me what you need about X."; the menu only when nothing is named.
- NEW R1 Configurable: the owner sets, per question kind, what is asked and what is defaulted. The
  policy is DATA (DB rows), seeded with TODAY's behaviour, not code constants. DB rows now; a
  Settings UI later, after a Lavish mock (owner rule).
- NEW R2 Carried subject: when a subject carried from an earlier question would combine with a
  newly named subject, ASK ("Delivery for [customer]: only SRT391-GM (from earlier) or all
  products?"). Configurable like the rest. Coordinate with INTENT-FRAMES (#1473).

## Why

Crew failure attribution of the hidden eval (11 Oct): 5 of 47 hidden failures are clarify-policy
errors, plus loops. Today 29 ask sites each decide on their own; nothing compares the new question
with the one the bot just asked (`previous_reply` is read at `engine.py:4168` but only fed to the
parser as context, `turn/context.py:90, 316-321`).

## Rules after the rulings

P1. ASK OR DEFAULT PER KIND, FROM DATA. Each question kind has one row in a new table
`chatbot_clarify_policy`; the bot reads its mode there. Seed = today's behaviour, so nothing a
contact sees changes from the seed alone, except P3, P4 and R2 below.

P2. NO GUESSING. A default is only ever a real value from data (newest rows, "both"); never a
guessed type, product or customer. An unsure parser means ask (Q3).

P3. LOOP BREAKER. A clarification is fingerprinted by what WE asked (kind + parameter + subject),
never by the user's text. The same fingerprint at most `repeat_limit` times in a row (seed 2). The
next one becomes: the handover offer (escalation allowed) or "Please refer to your salesman.", with
one plain line saying what is still missing ("I still need the attachment type for CB1512ASS.").
Never a guessed answer (Q4).

P4. GENERIC MENUS (Q5). A subject but no domain: answer the subject's default domain plus
"Something else? Tell me what you need about X."; menu only with no subject.

P5. CARRIED SUBJECT (R2 + Q9). A subject carried from an earlier question (kind A) that the new ask
could use is CLEAR when the parser reads an explicit reference to it (`anaphora.backward_reference`:
"this", "that", "these", "它", "这个", "the same", any language), reads the message as a follow-up
on the same subject (`continuation`, Q10: "eta?" right after "SRT391-GM stock"), or the turn REFINEs
the held question. The carried subject never expires by time or turn count (owner, Q10).
Clear: use the carry, no question. Not clear: ask before fetching.
- New subject of another kind B named, no reference (row `carry_combine`): "Delivery for <B>: only
  <A> (from earlier) or all <A plural>?"
- No new subject, no reference (row `carry_unclear`): "Delivery today: for <A> (from earlier) or all
  <A plural>?"
The answer is read by the parser as a pick (1 / 2, or in words), never by keyword in code.

P6. Semantic only; scope and permissions unchanged.

## The policy table (R1)

`chatbot_clarify_policy`, one row per question kind:

| Column | Meaning |
| --- | --- |
| `ask_kind` (PK, text) | Stable key, e.g. `do_period`, `attachment_type`, `carry_combine` |
| `label` | Plain words for the owner ("Which period, for delivery orders") |
| `mode` | What the bot does: `ask`, `default`, `collapse`, or for `carry_combine`: `ask`, `carry`, `drop` |
| `allowed_modes` | The modes the code supports for this kind; a row can only be set to one of them (DB CHECK + service check) |
| `default_note` | What "default" means for this kind, in plain words ("newest 20, no window") |
| `repeat_limit` | Loop breaker limit for this kind (seed 2) |
| `updated_at`, `updated_by` | Audit |

Same shape as the existing `chatbot_domains` / `chatbot_entity_kinds` (`app/models/chatbot_policy.py:31, 60`):
seeded by migration from ONE constant list, `turn/policy.py`-style fallback to that list when the
table is empty (blank-schema test fixtures). Edited today by crew SQL on the owner's word; a
System Management page next to "Chatbot domains" (`sorento_crm_frontend/app/(protected)/system-management/chatbot-domains/`)
later, after a Lavish mock.

Only kinds whose alternative already exists in code get more than one allowed mode (Q7):

| Kind | Seed (today) | Other allowed modes, existing code reused |
| --- | --- | --- |
| `do_period`, `so_period` | `ask` | `default` = newest 20, no window: the per-contact skip switch path (`do_ask.py:101`, `so_status.py:350`) |
| `outstanding_scope` | `ask` | `default` = both (`lanes/business/__init__.py:1963`) |
| `carry_combine` (NEW, R2) | `ask` (owner's rule) | `carry` (today), `drop` |
| `carry_unclear` (NEW, Q9) | `ask` (owner's rule) | `carry` (today), `drop` |
| `generic_menu` | `default` (Q5: answer the subject) | `ask` (today's menu) |
| every other kind | its one current mode | none yet; a default gets built when the owner asks for one |

## Every ask site today, with its seeded mode

| # | Ask (kind key) | Decision site | Seed mode | Note |
| --- | --- | --- | --- | --- |
| 1 | Promotion access level (`promo_tier`) | `lanes/business/tier_gate.py:127-148, 282`; `engine.py:5825-5874, 6197-6214`; copy `lanes/business/answer.py:338-352` | ask | Q2 keep. "All empty" already collapses (`answer.py:333-344`) |
| 2 | Generic tier pick (`tier_pick`) | `turn/narrow.py:410-429` | ask | Q2 keep |
| 3 | Promotion subject ask-back (`promo_subject`) | `turn_runtime.py:647-717` | ask | No subject, nothing to fetch |
| 4 | Attachment type, text ask (`attachment_type`) | `lanes/business/gate.py:495-530`; `lanes/business/answer.py:3134-3139, 3215-3224` | ask | Q3: parser picks the type; unsure = ask |
| 5 | Attachment kind (`attachment_kind`) | `turn/narrow.py:500-506`; `turn/compose.py:997` | ask | Q3 |
| 6 | Purchase cost product (`cost_product`) | `turn/narrow.py:500-506`; `turn/policy_rows.py:323` | ask | |
| 7 | DO period (`do_period`) | `do_ask.py:104-153`; `lanes/business/__init__.py:2202-2212` | ask | Q1 keep; `default` allowed |
| 8 | SO list period (`so_period`) | `lanes/business/__init__.py:1520-1548`; `so_status.py:295-335` | ask | Q1 keep; `default` allowed |
| 9 | Ranking period / metric / basis / top N (`ranking_fields`) | `lanes/business/report_ask.py:225-243, 564-638` | ask | Owner rule, no default (`ranking_ask.py:3-4`) |
| 10 | Ranking drill scope (`ranking_drill`) | `report_ask.py:783-797` | ask | Gives up after 1 miss already |
| 11 | Top selling grain / metric / who (`top_selling`) | `lanes/business/__init__.py:2050-2081` | ask | Owner rule |
| 12 | Low stock category / supplier / grouping (`low_stock`) | `lanes/business/low_stock_ask.py:215-243` | ask | Optional fields become "no filter" after 2 misses already |
| 13 | Ideation idea (`idea_text`) | `lanes/ideate.py:50-62` | ask | |
| 14 | Outstanding SO / DO / Both (`outstanding_scope`) | `lanes/business/__init__.py:1947-1963` | ask | `default` = both allowed |
| 15 | Outstanding report customer (`outstanding_customer`) | `lanes/business/__init__.py:1902-1913` | ask | |
| 16 | Product picker (`product_pick`) | `lanes/business/gate.py:569-974` | collapse | Today: one option holds the data = answered |
| 17 | Customer picker (`customer_pick`) | `lanes/business/gate.py:994-1047` | ask | |
| 18 | Roster pick (`roster_pick`) | `turn/narrow.py:301-479` | ask | |
| 19 | Kind pick (`kind_pick`) | `turn/reconcile.py:112-113`; `turn/apply.py:1797-1817` | ask | |
| 20 | Account N customer (`account_customer`) | `engine.py:5157-5169` | ask | |
| 21 | Stock quantity (`stock_qty`) | `engine.py:6564, 7071-7150`; `turn/task.py:381-400` | ask | |
| 22 | Stock family pick (`stock_family`) | `turn/task.py:871-895` | ask | |
| 23 | N for all or one (`qty_scope`) | `turn/apply.py:3040-3092` | ask | |
| 24 | Stock denial demand qty (`demand_qty`) | `engine.py:5492-5493, 7894-7896` | ask | |
| 25 | Did-you-mean (`did_you_mean`) | `dealer_stock.py:77-129`; `lanes/business/answer.py:5139-5255` | ask | |
| 26 | Domain menu (`generic_menu`) | `turn/apply.py:2094, 2112-2113`; `app/services/chatbot_reply_copy.py:35-46` | default | Q5 |
| 27 | LLM clarifier (`llm_clarify`) | `turn/apply.py:2114-2127`; `lanes/casual.py:259-330` | ask | |
| 28 | Escalation team / company / member (`escalation_pick`) | `lanes/escalation.py:564, 639, 1269` | ask | |
| 29 | "What would you like me to know?" (`entities_only`) | `turn/compose.py:1170-1214` | default | Q5, same as #26 |
| 30 | NEW carried subject + new subject (`carry_combine`) | seam `turn/apply.py:3412-3494` (after `decide`, before `_focus_rules`) | ask | R2 |
| 31 | NEW carried subject, no reference (`carry_unclear`) | same seam | ask | Q9 |

Every row gets the loop breaker (P3) with `repeat_limit` 2, including asks that store no pending
today (#4, #7, #8, #26, #27; `do_ask.py:14-17`).

## Carried subject today (R2), measured in code

- The new turn's entities are grouped by kind at `turn/apply.py:1050-1055`. Any kind the new message
  does not name keeps its carried rows (`:1073-1075`, `:1105`).
- A carried kind is cleared only when `decision.starts_fresh` (`turn/apply.py:1108-1126`). That is
  true only for NEW_ASK with `entity_op_replace` or `domain_in_message` (`turn/decide.py:114-131`).
- REFINE, anaphora and the pre-key `None` reading keep the carry (`turn/decide.py:539-598`).
- A carried product the parser echoes back (`current_message:false`) still lands in the new kind
  map, because `:1044-1049` filters on `confident` only. So even a fresh ask can keep it.
- The parser's `continuation` field is not read there (UNVERIFIED beyond `apply.py:1992`).
- INTENT-FRAMES (#1473, draft, CONFLICTING, last updated 4 Oct) replaces this with frames owned by
  their intent: the subject carries across intents only on `continuation` or
  `anaphora.backward_reference`. R2 still applies there: when the frame DOES carry a subject into a
  new intent next to a newly named one, ask.
- The seam: after `decide()` (`turn/apply.py:3412`) and the reconcile step, before `_focus_rules`
  (`:3494`). Return a Pending like the kind pick does (`turn/apply.py:1814-1817`;
  `turn/pending.py:89`). The parser answers it as a position pick (`head/parser.py:417-434`
  `open_question_answer`, read at `turn/apply.py:2806`); a new arm in `_answer_pending`
  (`turn/apply.py:721`) applies it.

## Real DEV examples (eval DEV split, contacts masked)

1. cand-0258 (dealer). "size?" then a photo; each got "Please provide the attachment type for
   product CB1512ASS - e.g. product image, technical drawing, or certificate."; then "I want the
   DESCRIPTION SIZE!!!". P3: the third ask becomes "I still need the attachment type for CB1512ASS."
   plus the handover offer (Q4: no guess).
2. cand-0265 (staff). "CGB1032B promotion": prod menu of three "- no promotion" lines. Main already
   says "No current promotion for CGB1032B." (`answer.py:333-344`). Regression test.
3. cand-0036 (staff). "how can i ask marketing about bidet seat cover?" got a generic topic menu.
   P4: answer for the named subject / team instead.
4. cand-0230 (staff, R2). "[customer] 7/10/2026 有送货吗？", then "SRT391-GM几时会安排送货？", then
   "十月": the reply was filtered by BOTH "客户: [customer]" and "产品: SRT391-GM". R2: ask "Delivery for
   [customer]: only SRT391-GM (from earlier) or all products?".
5. cand-0220 (staff, R2). "SRT359C 有货吗？" then "[customer] 今天有送货吗？": the label wants that customer's deliveries,
   no SRT359C. R2 makes this an ask; crew relabels it "good clarify" (Q9).

## Edge cases

- Seed changes nothing visible except P3, P4, R2.
- A row set to a mode not in `allowed_modes` is refused (DB CHECK and the service); a missing or
  unreadable table falls back to the seed constant, never to "no ask".
- Clear cases, no question: an explicit reference ("delivery for this"), a REFINE of the held
  question, a restated subject, an answer to a held question.
- R2 does not fire when the new message restates the carried subject, when the parser reads the
  turn as an answer to a held question, or when the carried kind and the new kind are the same
  (a new customer replaces the old one, today's rule).
- R2 answer "all" drops the carried kind for this ask only; "only" keeps it.
- "Could apply" = A is a kind the new ask's domain accepts (the domain's allowed entity kinds,
  `lanes/business/gate.py` ALLOWED); a carried warehouse never triggers a sales-figure question.
- Q10: "eta?" right after "SRT391-GM stock" answers the ETA for SRT391-GM, no question. The parser's
  `continuation` field carries this. On main it is declared but means "more / next" and nothing
  reads it (`head/parser.py:118-123`). This lane gives it INTENT-FRAMES' meaning, word for word
  (#1473 `INTENT_FRAMES_ADDENDUM`, "SAME SUBJECT OR A NEW ONE"), as a new unlabelled parser version
  graded on DEV before the owner promotes it.
- No time expiry: main has none on the carried subject today (only the escalation offer's 3-turn
  clock, `turn/pending.py:303`, which is not a subject). A test pins that.
- R2 and P3 together: the carried-subject ask repeated twice then gets the handover line.
- Default and scope: a default runs after the scope filters; it never widens what a contact sees.
- Handover respects the contact's escalation switch.

## Questions (Q6-Q8 ruled (a) by crew 11 Oct; Q9, Q10 ruled by the owner 11 Oct)

Q6. R2 and INTENT-FRAMES (#1473, stale since 4 Oct, conflicting with main).
(a) Build R2 on main at the `apply.py:3412` seam as one function; post a crew-note on #1473 so
whichever lane merges second calls it from its carry point (frame.py there).
(b) Wait for #1473 to merge first. Recommend (a): #1473 is not moving, and the check is one call.

Q7. Which modes are switchable now.
(a) Only kinds whose alternative already exists in code (table above); the rest show their one mode
until the owner asks for a default and one is built.
(b) Build a default for all 29 now. Recommend (a): a switch with no behaviour behind it is a lie,
and simplest-thing.

Q8. Where the policy is edited.
(a) DB rows now (crew SQL on the owner's word, audited by `updated_by`), Settings page later after a
Lavish mock. (b) Mock the Settings page now in this lane. Recommend (a) (owner's own words).

Q9. RULED (owner, 11 Oct): ask when unclear, carry when clear. "Delivery for this" with products in
focus = clear, use them. "Delivery today" with no reference = ask "Delivery today: for SRT391-GM (from
earlier) or all products?". Carried product + newly named customer = ask. Clear = the parser's
explicit reference or a REFINE, never a keyword list. Row `carry_unclear`, default ask. Eval: cases
labelled "answer, no carry" with no reference (cand-0220) become "clarify acceptable"; crew relabels.

Q10. RULED (owner, 11 Oct): a LEGIT follow-up; answer the ETA for SRT391-GM, no question. An
elliptical follow-up with no new subject continues the held subject. No time-based expiry of the
carried subject, ever. Ask only per Q9. (Options as filed, for the record:) Elliptical follow-ups ("eta?" straight after "SRT391-GM stock", no reference word).
(a) Teach the parser that a bare follow-up about the same thing is a backward reference (new
unlabelled parser version, graded on DEV before promotion), so it stays clear and is not asked.
(b) Ask, as the rule reads literally. Recommend (a): otherwise every short follow-up gets a question,
which is the loop the lane is fixing.
