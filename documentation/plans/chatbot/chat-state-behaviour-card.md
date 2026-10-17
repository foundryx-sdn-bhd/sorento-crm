# CHAT-STATE behaviour card (16 Oct)

Step 1 of the 16 Oct chatbot scale design. One conversation state per contact, always shown to the parser
in full; the parser outputs CHANGES to it; code applies them and logs before/after. Paths below are under
`sorento_crm_backend/app/services/chatbot/` unless noted. Code read at origin/main e46d0b0ae.

## What exists today (repair, not build)

| Piece of the state | Today | Defect |
|---|---|---|
| Subject | `Focus` (`turn/state.py:132`: products, customers, brands, domains, document, status, sales_channel, date_window, sort, sales_ranking_frame, low_stock, tasks) in `respond_contacts.session_vars.focus` (`app/models/access.py:279`, per contact) | Shown as one "Current subject" line (`head/parser.py:814-850`, brands/tier/sort not printed). L2 cap 350 tokens (`turn/context.py:29`) trims ONLY that line (`:426-431`) while the vocabulary lines (~629 tokens p50) stay whole: subject cut on 39% of turns, 56 of 57 sampled follow-ups with a focus |
| Open question + options | `Pending` (`turn/pending.py:76`) in `session_vars.open_question`; `question.open_question` (`turn/question.py:129`) | Three stores (Pending, `Focus.tasks`, `Focus.required_ask`); a delivered answer is shown twice (as "Last answered" and as an open question, `turn/task.py:411-417`) |
| Last list shown | Same `Pending` when the kind is a roster (`pending.is_roster` `:53`) | No record of a numbered answer list that is not a picker; a bare number is caught by a regex on the raw text (`engine.py:801`, `_BARE_POSITIONS` `:391`) |
| Carry decision | Parser fields (`continuation`, `anaphora.backward_reference`, `topic_reset`, `open_question_answer`, `domain_in_message`, `broaden_axis`; `head/parser.py:115-575`) THEN ~70 code functions: `apply._focus_rules` (`turn/apply.py:1020`), `decide._keeps_subject` (`turn/decide.py:371`), `turn_runtime.with_carried_entities` (`:4825`), `outstanding_carry` (`:3360`), engine raw-text verdicts (`engine.py:489`, `:801`, `:2872`) | Two writers; carry "grew back per lane"; 20 of ~40 post-parse rewrites re-read the raw message (owner ban 4 Oct) |
| Before/after log | `_record_memory_trace` (`engine.py:8372-8456`) into `chatbot.turns.trace` | `focus.before` is the raw stored session, not the state the parser saw; no list of changes |

## Carry / reset rules (the parser decides which applies; code only applies it)

| # | User does | State change | Ruling (memory / source) |
|---|---|---|---|
| R1 | Refines with a fragment: a brand, filter, spec, channel, period ("sorento", "dealer sales only", "under counter") | Keep subject; add or replace that one axis | feedback-live-check-follow-ups (refine last answer); #1720 probe: "sorento" after a set narrows the set |
| R2 | Code-less domain ask ("stock", "eta", "have incoming?", "size?") | Keep products and customer; switch domain; the held period stays (R16); status, sort and report frame belong to the old domain and drop | feedback-live-check-follow-ups (topic switch); real traffic top code-less follow-ups `stock` 76, `eta` 53 (CHATBOT-SCALE-C:271); owner 16 Oct ~23:00 (period retained in follow-ups) |
| R3 | "and X?" (swaps one subject item) | Replace that item; keep the ask: domain, qty, period, filters | 15 Oct "qty carries" (#1617: "5件" then "SRTWC286-SH-NEW 呢？" -> 5) |
| R4 | New full ask that names its own subject and measure ("top 3 sales agents" again, "any stock for sorento undermount basin") | New subject = only what this message names; basis / measure / channel / period / filters only from this message. Customer still carries (R6) | #1721 step 7 + REPORT-ENGINE-L AC-HT8 (no carried channel); feedback-chatbot-clarify-not-assume |
| R5 | Answers the bot's open question (a position, an offered name, yes/no, a value) | Fills ONLY the asked slot; the question closes | feedback-live-check-follow-ups (reply to bot question); REPORT-ENGINE-L ("an answer fills only the asked slot"); #1572 "sorento" |
| R6 | Customer first, then a product | The held customer stays held. The parser says whether it applies (R14). When the parser cannot tell, the held entity's TYPE from the registry settles it (R15): a customer (debtor) means delivery to that customer (SO/DO); a clearly ETA/incoming ask is incoming, product-level | feedback-chatbot-clarify-not-assume (owner 15 Oct); owner 16 Oct ~21:45, ~22:45, ~23:00 |
| R7 | Product first, then a customer named with no reference to the product | Ask "same product or all products?" with real names. Explicit reference ("delivery for this") = use the held product | feedback-chatbot-clarify-not-assume (Q9), feedback-chatbot-rules-know-dont-ask |
| R8 | States a quantity on the held item ("HAVE 10??" after an answer) | Quantity attaches to the held item; it rides on R3 and on a pick | 15 Oct "qty carries" (#1617 QTY-CARRIED) |
| R9 | Points at the last list ("2", "the 2nd one", a label from it) | That item from the last list shown; a short reply to a bot list keeps the held item | IDEA-CAPTURE G1 (brief); #1352 (roster survives "check stock") |
| R10 | Small talk, thanks, greeting | State unchanged | (today's casual arm) |
| R11 | Unclear and the data cannot settle it (no reference, could be the held subject or a new one; an entity of both or neither type) | Ask once with real-name options, never guess; loop breaker = hand-over offer, never a guess | feedback-chatbot-clarify-not-assume, feedback-chatbot-never-leak-ids; owner 16 Oct ~23:00 ("check whether the entity is customer or supplier first before we ask") |
| R12 | (mechanism) | Parser outputs the change in registry values / list positions; code validates and keeps a registry-valid value; no word, regex or fuzzy rules on the message | feedback-semantic-no-hardcoded-rules, feedback-no-code-fuzzy-matching, feedback-trust-parser-registry-output |
| R13 | (truth + scope) | State holds only items an answer actually showed to this contact; replies never show ids/UUIDs | feedback-truthful-no-fabricated-replies, feedback-chatbot-never-leak-ids, SEC-MCP-AUTH #1742 |
| R14 | (held slots, owner 16 Oct ~22:45) | When the State holds a customer or a period, the parser states for each, per message: `keep` (applies to this ask), `skip` (does not apply to this ask; stays held), `clear` (drop it), or `unclear`. Code validates and applies; no per-domain table, no word rule. A held slot the parser says nothing about applies as today | owner 16 Oct ~22:45 verbatim: "delivery can be both customer scope and product scope"; ~21:45 "ETA is like the incoming" |
| R15 | Parser says `unclear` for a held slot | First settle by data: a held customer (registry kind `customer`) on an unclear delivery ask = delivery to that customer, no question; a supplier/creditor would mean incoming/PO (no supplier kind exists in `chatbot_entity_kinds` today, so this branch has no data yet). Only when the type cannot settle it (e.g. an unclear period): ask ONCE with real names; ask vs assume default = one named constant `ask` (crew ruling); a second unresolved turn offers the hand-over | owner 16 Oct ~23:00 verbatim: "if check is customer then is delivery, if supplier then is incoming or PO"; crew ~23:10 |
| R16 | Period (owner 16 Oct ~23:00, closes Q3) | A follow-up keeps the held period; a message that states a new period uses the new one; on a brand-new topic with no period the parser judges follow-up vs new topic (`skip` / `clear` period) | owner verbatim: "The period should retain in follow up question, new questions if state new period then use new period" |

## Real examples (target behaviour)

1. Owner probe on #1720, 16 Oct (CHAT-ADVISOR-SPIKE-owner-probes-1720.md rows 4-6): "any stock for undermount basin"
   (20 products) -> "any stock for sorento undermount basin" -> bot printed 20 "No seat cover is on record" lines
   (carried 20 codes, `lanes/business/find.py:249-251`). Target R4 (or R1): subject = Sorento under-counter basins,
   one stock set answer. Then "i mean any stock for sorento undrmount basin" = same subject, correction.
2. Owner flow on #1572, 13 Oct: top-5 ranking -> "i want to see dealer sales only" -> bot "Sorento or Mocha?" ->
   "sorento" -> bot "Hello Mr". Target R1 then R5: channel=dealer added, brand slot filled, ranking re-runs.
3. #1721 step 7 (CHAT-PRB-HT.md:19-22): after an "ordered" ranking the user retypes "top 3 sales agents"; the bot ran
   the table with carried basis/measure/channel. Target R4: ask "By quantity or by amount?"; "qty" -> R5, by-quantity table.
4. Prod, #1617 (QTY-CARRIED.md): "SRT5733 HAVE 5 NOS??" got the type ask ~10 times; after a photo, "HAVE 10??"
   re-sent the photo. Target R8: stock check for 10 units of the held item.
5. Owner 15 Oct: "HANLIM 7/10 有送货吗" -> "SRT391-GM几时送货" ("when is SRT391-GM delivered"). Target (owner 16 Oct
   ~23:00, R15 + R16): HANLIM is a customer, so this is delivery to HANLIM (SO/DO) of SRT391-GM, no question; it is a
   follow-up, so the held 7/10 period is kept unless the parser judges it a new topic. FLAG for the owner: a "when"
   question asks for a date, so a kept date filter can hide a delivery on another day; the parser decides.
   A clearly ETA/incoming phrasing ("SRT391-GM eta") is incoming stock, product-level, no customer (owner ~21:45).
   Reverse order (product, then "delivery for <customer>") -> R7 ask.

Note: reading `sorento_prod_20261015` was refused by this session's permission guard (PII), so the examples are the
owner's own probes and prod incidents already in crew reports. Crew can attach more if needed.

## Edge cases

- A number after "how many units?" is a quantity; after a list it is a position. Both the open question (with its
  kind) and the last list are in the state block; the parser says which (`open_question_answer.mode`).
- A position outside the last list ("14" when 3 rows were shown) -> not a pick; the parser reads it as qty or code;
  if neither validates -> R11 ask.
- A question already answered two turns ago (G124 stale pick) is closed; the last list stays pickable.
- Long lists (33 location rows, 46 size buckets) are shown in full, one short line per position.
- Topic switch and back: "SRT1 stock" -> "promo?" -> "eta" -> still SRT1 (R2 twice).
- Two messages in quick succession: today's per-key merge (`turn/tail.py:140`) stays; the state write is one row.
- Prod keeps today's prompt label until release: a parse with no state change falls back to today's carry path, so
  prod behaviour is byte-identical until crew moves the label (also what the L1 replay checks).
- Multi-company contact: the state never holds items from a company the contact cannot see (built from scoped answers).

## Open questions (5; recommendation first)

1. (crew, repo) State store. (a) keep `respond_contacts.session_vars` (focus + open_question exist; add `last_list`
   and a `version`), before/after in `chatbot.turns.trace`; (b) new table with history rows. Rec (a): no migration,
   one row per contact already, row G log already lands in the trace (`engine.py:8372`).
2. (crew) Prompt chain + measurement. Prompts-as-code is not on main (prompt = DB `ai_prompt_versions` + wording
   layer, `app/services/chatbot_prompt_vars.py:737`); the follow-up TRAIN slice needs `replay.py live` (#1745) and a
   TRAIN-only seed export (`crew/state/eval/cp0/items/` mixes splits). Rec: base on the current prod-label version,
   the delta kept as a repo file, published to a lane label on the dev copy only; crew exports TRAIN-only follow-up
   seeds; the lane runs 3 TRAIN runs per checkpoint (~$0.7 each) plus the $0 replay gate on every slice.
3. (owner) Period on a new ask while the customer carries ("HANLIM 7/10 有送货吗" -> "SRT391-GM几时送货"):
   (a) the period carries with the customer; (b) the period comes only from the current message. Rec (b): it is part
   of the ask, same as basis/measure in #1721 step 7.
4. (owner) Open question vs last list lifetime (today's collision: 4 Oct "a new ask drops stale pending" vs #1352
   "a roster survives 'check stock' so '7' still picks"). (a) split them: the bot's open question closes on any new
   ask; the last list stays pickable until a new list replaces it; (b) both close on a new ask. Rec (a): it satisfies
   both rulings.
5. (crew) Transition. When the parse carries a state change, it is the ONLY carry writer and today's carry functions
   skip; when it does not (prod label, replay corpus), today's path runs unchanged. Deleting the old carry code is
   step 2, each removal proven by replay. Rec yes (keeps first asks byte-identical and prod untouched).

## Answers

- Crew, 16 Oct: Q1 (a) keep `respond_contacts.session_vars`, add `last_list` + `version`; before/after state in
  `chatbot.turns.trace`. Q2 as recommended: base on the current prod-label prompt, delta kept as a repo file,
  published only to a lane label on the dev copy (never move `production`); EVAL-BENCH exports TRAIN-only follow-up
  seeds to `crew/state/eval/cp0/train_followups/`; 3 TRAIN runs per checkpoint (~$0.7 each). Q5 yes: dual path; old
  carry code is removed only in step 2, each removal proven by replay. No reads of the prod copy by the lane; crew
  attaches masked real conversations on request.
- Owner, 16 Oct: Q4 (a) CONFIRMED. Q3: asked why (b); crew explained (period carries on refines and on "and X?";
  on a new full ask it comes only from the new message, customer carries). (b) is the working rule until he answers;
  no code on the period rule until then.
- Owner, 16 Oct ~21:45: ETA is incoming stock arrival, a product-level answer; a held customer (and a held period) does
  not apply to it; the customer stays in state. Made general as R14 (slot applies by domain data). Q3 (b) stays the
  working rule for customer-scoped asks.
- Owner, 16 Oct ~22:45: DROP the per-domain scope table ("delivery can be both customer scope and product scope").
  The parser states per message which held slots apply (R14 rewritten); unclear -> ask once with real names, default
  ask vs assume is a configurable policy (R15). Pending with the owner: period rule (refine keeps it, a new question
  uses its own) and the unclear default (crew rec: ask once). Build with `ask`; S3b stays open.
- Owner, 16 Oct ~23:00 (closes Q3, replaces the unclear-customer ask): period kept on follow-ups, a new stated period
  replaces it, brand-new topic = parser judges (R16); an unclear held customer is settled by the entity's type from
  the registry (customer = delivery, supplier = incoming/PO), ask only when the type cannot settle it (R15, R11).
- Lead ruling 17 Oct (applying R16 to the reviewer's B2): a domain switch is a follow-up, so the held period carries
  unless the parser says `skip` / `clear` period; a period the message states replaces it even without a `set` op.
  A dry-run mock emission turns the state path on only when it carries the `state_changes` key.
- Lead ruling 17 Oct (live diagnostic of owner CP0 c13/c05, PR #1747 comment): VALUES come from the parse fields:
  a current-message entity or a stated scalar applies as stated, with or without a `set` op; ops decide only what of
  the HELD state carries. Round 8's drop of unnamed current-message entities is reverted (it dropped a stated
  "video"); a reference to the held subject is kept out by the prompt rule and the drop of current_message:false
  entities.
- Crew, 16 Oct ~23:10: the default is ONE named constant `ask` in code, one place, pinned by a test; storage and UI
  come later behind the clarify-policy work (#1570). An owner change of default is a one-line change.
