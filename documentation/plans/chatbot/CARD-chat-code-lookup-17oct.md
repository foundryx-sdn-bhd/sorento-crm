# CARD: CHAT-CODE-LOOKUP behaviour card (step 2, one crew ask before code)

Lane CHAT-CODE-LOOKUP, plan `PLAN-chat-code-lookup-17oct.md`. Bench: EVAL-BENCH TRAIN, code-lookup slice, S6 main arm
(main c2a3b162e + parser v63), graded at $0 with main's grader (`scripts/chatbot_eval/targets.py`, identical to the
pinned 230642edc). No customer names below; codes are catalogue codes.

## Root cause (step 1): 13 TRAIN fails on main, 81 / 94 judged = 86.2%

| Class | n | Items (message) | Cause, with evidence |
| --- | --- | --- | --- |
| bench / harness | 3 | cp0-eec51c85ed "X4: M488-75-PVD-GM", cp0-5b2e29483b "check stock: SRTWB8001, ...", cp0-3f2f3db64e "SRTWCFT150-OP" | Each recorded turn is a photo; the message is the photo's read text. The live runner copies the image, the turn queues a new media job inside `_rolled_back` (`scripts/chatbot_eval/replay.py:92`), nothing commits it, no worker sees it, and the turn times out (30 s) to "I could not read that photo in time". These are the only 3 media rows in S6. Not a bot bug. |
| grader / gold | 3 | cp0-986c4e40bc "Check stock for MWC7630"; cp0-76e3a8f2da "Cks903 purchase cost"; cp0-2ab2f7ccc9 "csk11b, got stock ?" | MWC7630 is a 5-member family (P-trap 180, S-trap 150/200/250/300), none held: the pick is the owner's family ruling (`turn/task.py:865-899`), gold `ambiguous` should be true. CKS903: the bot answered purchase cost via `crm_procurement_po_last_cost_list`; gold fact should be purchase cost, not price. CSK11B is not on record (dev DB): repeating the did-you-mean is right; gold needs `code_unresolved`. |
| parse | 4 | cp0-9aa22e4c4b "Check stock CBCM66631"; cp0-29d7825ee6 "Check stock chs2072 x 72 units"; cp0-39e88ea65f "SRTKT1636SS-GM check weight ?"; cp0-3fa0985bd4 "SRTKT73SS-BL when deliver to <customer> ?" | Typos: the did-you-mean is built in code (`lanes/business/answer.py:5264-5361`, trigram floor 0.30 at `entity_resolver.py:3164`); the parser never sees candidates, so nothing chooses (D2 gap). Near codes: CBMC66631 0.429 / CBFAL66631 0.400; CHS2039 0.455 / CSH2072 0.333 (dev DB `similarity()`). Weight: prompt v63 lists master_products attributes as a closed list (lines 246-252), so the parser drops "weight" and the engine's honest "not recorded" line (`lanes/business/fetch.py:2167-2195`) never runs. "When deliver": the parser sets no order status, so DO-ASK rule 3 asks "Which period?" and fetches nothing (`do_ask.py:1-16`). |
| candidate retrieval | 1 | cp0-9793ea25dc "SRTWC8608-RL P-TRAP" | SRTWC8608-RL exists only in `product_sets` (S-trap 250; the P-trap set is SRTWC8608-P-RL). The entity is relabelled product_set, which `lanes/business/gate.py:74-75` does not allow for master_products, so the turn is forced to a miss and 3 unrelated suggestions. |
| answer | 1 | cp0-3903d0aaab "Srtbf11415 ETA" | Exact code, the ETA answer is full, then a "Related products" picker of SRTBF11415 and SRTBF11415-NEW, both already marked no incoming (`lanes/business/answer.py:4804-4888`; the comment at 4849 records an earlier owner ruling "ALWAYS a numbered picker whenever at least one sibling exists"). |
| out of scope | 1 | cp0-d9971420da "CG-202609-037" | A delivery-order number, not a product code; the reply leaks another customer's DO (#1648, owned elsewhere). |

- Multi-code and family / variant: 0 fails on main. TRAIN has 13 non-exact codes (7 family prefix, 6 pass; 1 partial,
  1 pass; 4 typos, 1 pass; 1 set-only code, 0 pass); exact codes pass 71 / 78 judged.
- Unknown (not judged) 3: staff promotion asks get a price-tier question (cp0-8f15841c25, cp0-a9b0fa27b3,
  cp0-ca6802bc9e). Configured policy (`chatbot_domains.narrowing`, `turn/narrow.py:449-466`, `tier_gate.py:127-148`);
  not in this card's questions.
- #1755 arm adds 2 parse fails: a bare code after a non-stock turn carries the earlier domain (cp0-7ce4ddc2da
  "SRTWC286-SH 5unit" after an order turn; cp0-df53e90ebf "IBWC7601-ECO-S10" after an ETA turn).
- Projection: the harness and gold fixes alone move main to about 84 / 90 = 93.3% (photo rows and the DO number leave
  the slice). 97% on 90 items allows 2 fails, so the lane must fix at least 4 of the 6 real fails above.

## Rules

R1. Near code (typo, letter swap). A code word with no exact catalogue hit gets a shortlist: its near spellings (top 5
by trigram similarity, floor 0.30, the resolver's did-you-mean floor) and its prefix family. The parser chooses one or
says "none". Chosen: answer that product and name the code used. None: today's did-you-mean list. Code never picks.
- "Check stock CBCM66631" -> stock of CBMC66631, reply names CBMC66631.
- "Check stock chs2072 x 72 units" -> stock of CSH2072 for 72 units (the parser, not the higher trigram CHS2039).
- Edge: two near codes equally plausible ("csk11b": CSK11A, CSK11, CSK11A-BL) -> parser says none -> did-you-mean.

R2. Set codes are codes. A code that exists only in `product_sets` is shortlisted and resolved like a product code for
product info and stock. A code plus an attribute picks the set variant whose spec matches, chosen by the parser from the
shortlist.
- "SRTWC8608-RL P-TRAP" -> product info of SRTWC8608-P-RL, no question.

R3. A full answer leaves no picker. When the asked code was answered exactly, its siblings are named in one plain line
at most, never an open numbered picker.
- "Srtbf11415 ETA" -> the ETA answer for SRTBF11415; SRTBF11415-NEW named as related, nothing to pick.

R4. Any asked attribute is carried. The parser keeps the asked attribute even when it is not on the prompt's attribute
list; the reply gives the value or says it is not recorded (owner c09: "we need to be more human to say there is no
weight for this product").
- "SRTKT1636SS-GM check weight ?" -> "No weight is recorded for SRTKT1636SS-GM" (dev DB has no weight on any product).

R5. A held family member wins (owner c08). Held SRTWC286-SH-NEW + "Srtwc286 drawing" -> the drawing of
SRTWC286-SH-NEW, no 10-item picker. #1755's `held_family.py` acts only on spec words; a bare typed family code needs the
same branch. A family with no held member stays a pick (MWC7630, owner family ruling).

R6. The answer domain is reused; a count beside a code is a stock ask. "IBWC7601-ECO-S10" after an ETA answer -> its
ETA. "SRTWC286-SH 5unit" -> stock for 5 units.

Gate: the parser prompt marker pattern from #1755 (`gate_on`, "== CLOSED LISTS =="); gate off = byte-identical to main,
proven by the $0 L1 replay (`scripts/chatbot_eval/replay.py diff`, 0 unintended). No migration. Production prompt label
untouched; only unlabelled versions on the dev copy.

## Questions (each with a recommendation)

Q1. Sequencing with #1755. #1755 already shortlists `code X` / `near code` lines and maps a parser pick to
canonical_code (`head/candidates.py`, `head/closed_lists_prompt.py` on `claude/chat-closed-lists-x6ymb5`). (a) stack this
lane on #1755's branch and retarget when it merges; (b) wait for #1755 to merge; (c) build on main without it.
Recommendation: (a), no second mechanism, and my commits stay separable (pick validation, near-code floor, set codes,
held-family code branch).

Q2. Near-code floor. #1755 lists near codes at similarity >= 0.5, which misses both TRAIN typos (0.429, 0.333).
(a) top 5 at >= 0.30 for a code word with no exact hit (R1); (b) keep 0.5. Recommendation: (a), the floor the resolver's
did-you-mean already uses (`entity_resolver.py:3164`); the parser, not the score, decides.

Q3. Related-products picker after a full answer (`answer.py:4849`, earlier owner ruling: always a picker when a sibling
exists). (a) R3: plain related line, no picker, when the asked code was answered exactly; (b) keep the picker.
Recommendation: (a), the later rule "no needless which one after a full answer".

Q4. Set-only codes (R2). (a) set codes shortlist and resolve for product info and stock; (b) sets only through set asks.
Recommendation: (a), staff type set codes like product codes (SRTWC8608-RL).

Q5. "When deliver <code> to <customer>". (a) outstanding order status for that code and customer, no period question;
(b) keep "Which period?" (DO-ASK rule 3). Recommendation: (a), rule 3 already exempts the outstanding buckets
(`do_ask.py:4-6`) and "when deliver" asks about a delivery not made yet.

## For EVAL-BENCH (grader / gold / harness suspicions)

1. Photo rows: the live runner's media job never commits (`replay.py:92`), so photo items time out; exclude them or
   commit the media job to a worker. 3 TRAIN items.
2. cp0-986c4e40bc gold `ambiguous` should be true (5-member family, none held).
3. cp0-76e3a8f2da gold fact should be purchase cost.
4. cp0-2ab2f7ccc9 gold needs `code_unresolved` (CSK11B not on record). Grader hole: the #1755 arm passes it with an
   unhelpful reply.
5. `_attribute_answered` (`targets.py:597`) misses the engine's "*weight:* not recorded for X" line (the bold `*`
   breaks the match; offline check on the reply line returns False).
6. `code_families.py` reads `products` only, never `product_sets` (cp0-9793ea25dc is `code_unresolved` only for that).
7. Typo items with `code_unresolved` and no gold code: a flat "couldn't find" passes and a correct pick goes unchecked;
   gold should name the intended code (CBMC66631, CSH2072).
8. cp0-d9971420da is a DO number: move it out of code lookups.
9. cp0-df53e90ebf (bare code after an ETA answer): gold should accept the reused incoming domain (R6).

Request: the held-out code-lookup fails counted by failing check name (no items), so slices are weighed by class.

## Answers (crew, 17 Oct, ask a18027735)

- Q1 (a): stack on #1755's branch and rebase as it moves (it is still fixing its attachment regression).
- Q2 (a): near-code floor 0.30, top 5. Candidates only; the parser chooses.
- Q3 HOLD for the owner: `answer.py:4849` records an owner ruling ("ALWAYS a numbered picker whenever at least one
  sibling exists ... the owner reversed the earlier split"). The sibling picker stays until the owner rules.
- Q4 (a): set-only codes resolve like product codes.
- Q5 (a): "when deliver <code> to <customer>" = outstanding status, no period ask.
- The 9 grader / gold / harness suspicions go to EVAL-BENCH; crew asks it for held-out code-lookup fails by check name.
- OWNER RULING on Q3 (17 Oct): KEEP the sibling picker after a full answer (`answer.py:4846-4848` stands); R3 is
  dropped. Typo with one clearly meant code: answer it, no did-you-mean picker; ask only when two or more codes are
  equally close (R1). cp0-3903d0aaab's picker is therefore right: a grader / gold item for EVAL-BENCH.
