# CARD: parser prompt ablation, language teaching out, business rules kept (11 Oct 2026)

Lane PROMPT-ABLATION. Source: `ai_prompt_versions` row `c63439ac-91f4-46e2-94f7-a8c5ecddf0e9`
(`chatbot_semantic_parser` v50, 148,593 chars, about 37.9k tokens) on `sorento_eval_base`.
Line numbers below are lines of that template.

Owner rule (11 Oct): business facts may be taught; teaching the model to read language is not wanted.

## What counts as what

- BUSINESS: something the model cannot know: our codes, output keys and their values, our domains
  and teams, which word means which of OUR things when the plain-English reading is ambiguous
  ("cost" = selling price, "outstanding DO" vs "DO outstanding"), owner rulings, known-bug guards.
- LANGUAGE: how to read English / Malay / Chinese: synonym and translation lists, misspelling
  teaching, number words, ordinal words, honorifics, pronouns, grammar particles, relative-date
  arithmetic, long paraphrase example lists.
- MIXED: a business rule wrapped in language examples. Only the language part is a cut candidate.

## The table (86 `== X ==` sections, plus 4 un-headed blocks)

Sizes are approximate tokens (chars / 4). "Cut" is what the ablation would remove.

| # | Section (line) | Tok | Class | Cut candidate |
|---|---|---|---|---|
| 0 | PRICE TERMINOLOGY (9) | 177 | BUSINESS | - |
| 1 | AFFIRMATION (14) | 200 | MIXED | the 9 escalate-word spellings (20-21) |
| 2 | READING THE CURRENT MESSAGE (24) | 549 | MIXED | number-words bullet (53-55) |
| 3 | INTENT & DOMAIN (58) | 214 | BUSINESS | - |
| 4 | DOMAIN IN MESSAGE (76) | 391 | BUSINESS | - |
| 5 | DECISIVE DOMAIN TERMS (98) | 1781 | MIXED | "any language, paraphrase, misspelling" boilerplate (99-101, 126-128), ideate phrasings (185-187) |
| 6 | BARE ENTITY CONTINUATION (196) | 423 | BUSINESS | - |
| 7 | IDEATION CONTINUATION (219) | 330 | BUSINESS | - |
| 8 | REQUESTED ATTRIBUTES (238) | 1257 | MIXED | Chinese/Malay copies of the English examples (285-287) |
| 9 | SCOPE INTENT (299) | 144 | BUSINESS | - |
| 10 | MESSAGE TYPE (309) | 868 | MIXED | escalate spellings (313); paragraph 320 duplicates 313 + section 46 |
| 11 | USER GOAL (324) | 82 | BUSINESS | - |
| 12 | ATTACHMENT TYPE EXTRACTION (327) | 229 | MIXED | "how does it look / looks like / see it" phrasings (332-333) |
| 13 | FLYER FLAG (339) | 84 | BUSINESS | - |
| 14 | ACCESS LEVELS (346) | 356 | BUSINESS | - |
| 15 | ENTITY OPERATIONS (368) | 832 | MIXED | attachment / attachment_type translation lists (382-384, 387) |
| 16 | WHAT IS NEVER AN ENTITY (400) | 259 | MIXED | grammar particles part (401-405) LANGUAGE; bare family code part BUSINESS |
| 17 | PRONOUN REFERENCE (415) | 254 | BUSINESS | - (anaphora key) |
| 18 | ENTITY CONFIDENCE (421) | 843 | LANGUAGE | shrink to the 2-line definition of `confident` |
| 19 | MATCH MODE (453) | 36 | BUSINESS | - |
| 20 | BROADEN AXIS (456) | 960 | MIXED | "all time / any date / since ever / lah" phrasing list (462-466) |
| 21 | DEMAND QTY (511) | 19 | BUSINESS | - |
| 22 | CORRECTION / DOUBT (514) | 28 | BUSINESS | - |
| 23 | ROUTING (517) | 481 | BUSINESS | - |
| 24 | DATE FILTER (541) | 594 | MIXED | relative-date arithmetic teaching (547-555); keep per-message, domain-free, date_mode |
| 25 | POSITIONAL REFERENCES (567) | 419 | MIXED | ordinal / range examples (571-574) |
| 26 | REFERENCE TARGET (583) | 179 | BUSINESS | - |
| 27 | PERSON-NAME MENTION (589) | 252 | MIXED | honorific list (590-591) |
| 28 | COMPANY-NAME REPLY (602) | 804 | MIXED | repeated example phrasings (604-605, 612-614) |
| 29 | ORDER_STATUS FILTER (636) | 725 | MIXED | Malay synonyms (640-644); "READ FOR MEANING NOT SPELLING" + typo examples (660-672) |
| 30 | IS_ACTIVE FILTER (675) | 210 | MIXED | synonym list (678-683) |
| 31 | DOCUMENT (688) | 561 | BUSINESS | - (owner-reported misses) |
| 32 | ASKS (719) | 359 | BUSINESS | - |
| 33 | TOPIC RESET (735) | 93 | BUSINESS | - |
| 34 | OUTPUT (742) | 754 | BUSINESS | - |
| 35 | GROUP_BY (793) | 174 | MIXED | Malay "ikut ..." variants |
| 36 | TOP_N (804) | 58 | MIXED | Chinese example |
| 37 | ORDER_STATUS so_outstanding (812) | 217 | MIXED | Malay/Chinese variants |
| 38 | ORDER_STATUS do_outstanding (822) | 441 | BUSINESS | - |
| 39 | OPEN NUMBERED QUESTION (848) | 892 | MIXED | "a PARAPHRASE is still an answer" list (868-876) |
| 40 | SHORT TOKEN IS A LOCATION (898) | 288 | BUSINESS | - |
| 41 | PURCHASE ORDERS (915) | 270 | MIXED | Chinese variants |
| 42 | SPO LAST RECEIPT (932) | 125 | MIXED | Chinese/Malay variants |
| 43 | A BARE "SPEC" (942) | 208 | BUSINESS | - |
| 44 | IN A PO ASK, THE CODE IS THE PRODUCT (954) | 145 | BUSINESS | - |
| 45 | A NEW QUESTION IS NEVER A "YES" (963) | 189 | BUSINESS | - |
| 46 | A DELIVERY WORD PLUS A NAME (975) | 104 | DUPLICATE | whole (same text as 320) |
| 47 | THE ATTRIBUTE PHRASE, WHOLE (978) | 101 | DUPLICATE | whole (same text as 241) |
| 48 | LAST PURCHASE COST (986) | 296 | MIXED | Chinese/Malay variants; keep "cost alone = selling price" |
| 49 | LOW STOCK REPORT (1007) | 321 | MIXED | Chinese/Malay variants |
| 50 | SALES REPORT (1033) | 443 | MIXED | Malay/Chinese variants |
| 51 | SALES_CHANNEL (1059) | 554 | BUSINESS | - |
| 52 | BARE MONTH NAME (1089) | 287 | BUSINESS | - |
| 53 | NARROWING REPLY UNDER SALES/OUTSTANDING OFFER (1106) | 541 | BUSINESS | - |
| 54 | entities[].quantity (1145) | 202 | MIXED | Malay example |
| 55 | AN OPEN TASK IS A FACT (1158) | 343 | MIXED | phrasing lists ("teruskan", ...) |
| 56 | "Last answered:" (1179) | 283 | MIXED | Malay phrasings |
| 57 | OPEN QUESTION / open_question_answer (1196) | 1040 | MIXED | ordinal/confirm words in 3 languages, number-words paragraph (1214-1247 parts) |
| 58 | SALES ANALYSIS (1262) | 288 | MIXED | Malay/Chinese variants |
| 59-61 | DATES / SALES_BASIS / SALES_COMPANY (1280-1294) | 367 | BUSINESS | - |
| 62 | THE specification ENTITY (1308) | 795 | BUSINESS | - (policy-block vocabulary) |
| 63 | QUANTITY (1358) | 287 | BUSINESS | - |
| 64 | KNOWN BRANDS (1380) | 378 | BUSINESS | - |
| 65 | TOP SELLING (1410) | 300 | MIXED | phrasing list, Malay/Chinese variants (1414-1421) |
| 66 | TOP_N on top selling (1428) | 49 | BUSINESS | - |
| 67-68 | RANK_BY / BASIS (1432-1443) | 163 | MIXED | Malay/Chinese variants |
| 69 | RANK_GROUP (1445) | 160 | BUSINESS | - |
| 70 | ANSWERING TOP SELLING QUESTION (1456) | 611 | BUSINESS | - |
| 71 | "CUSTOMER OR SALES AGENT?" (1489) | 174 | MIXED | reply phrasing list |
| 72 | RANK_DIRECTION (1499) | 180 | MIXED | Malay/Chinese + typo ("worsr", "wrost") |
| 73 | NARROWING A TOP SELLING ASK (1510) | 743 | MIXED | "salesman / jurujual" phrasings, "salea" typo sentence |
| 74 | FROM A RANKING TO ANOTHER REPORT (1551) | 250 | BUSINESS | - |
| 75 | SELF_REFERENCE (1573) | 1656* | MIXED | Malay/Chinese pronoun lists (1576-1580). *includes ESCALATION CONFIRMATION block, BUSINESS |
| 76 | THE WORD SPO (1676) | 173 | BUSINESS | - |
| 77 | UNDER A PO/SPO, SHORT TOKEN (1686) | 170 | BUSINESS | - |
| 78 | SORT_BY / SORT_DIR (1697) | 464 | BUSINESS | - |
| 79 | A SORT WORD ALONE (1726) | 168 | BUSINESS | - |
| 80 | ACCOUNT (1742) | 1341* | MIXED | account spelling list (1746-1748). *includes LOW STOCK REPORT FILTERS: translation lists (1777-1779), Malay/Chinese examples (1801-1802) |
| 81 | SALES RANKING (1820) | 2074* | MIXED | Chinese example lines (1835-1839, 1845); keep "dealer IS our customer". *includes PO REFS (BUSINESS) |
| 82 | PRODUCT CODE canonical_code (1927) | 740 | BUSINESS | Chinese -> class-name map kept: it names OUR category taxonomy |
| 83 | PROMOTION KIND (1970) | 137 | BUSINESS | - |
| 84 | PROMOTION SUBJECT (1978) | 1720* | BUSINESS | *includes TRAP SPECS (owner ruling 6 Oct) + FRAGMENT rules; only "tandas / jamban / ada stok / 有货" words cut |
| 85 | MEMORY (2072) | 345 | MIXED | Malay/Chinese example |
| - | CURRENT DATE tail (2084) | 30 | STALE | "before calling the MCP tool" (the parser calls no tool) |

Brief's candidates, checked: SALES RANKING 2.2k, PROMOTION SUBJECT 1.9k and ACCOUNT 1.4k are big only
because each swallows an un-headed block (PO REFS, TRAP SPECS + FRAGMENT, LOW STOCK FILTERS). Their
language part is about 0.4k together. ENTITY CONFIDENCE is generic NLU (cut). MESSAGE TYPE and DATE
FILTER are MIXED: their order of rules, the per-message reset and date_mode are business.

Totals (section sums, chars / 4): BUSINESS 14.1k, MIXED 21.9k (it includes the business blocks
swallowed by rows 75, 80, 81), LANGUAGE 0.8k, duplicate 0.2k. The language part of MIXED is an
estimate of about 4k (UNVERIFIED until the trimmed text is cut and counted). Expected cut: about
5k tokens (13%). Because about 96% of the prompt
is cached, 5k fewer cached tokens saves about $0.0004 per turn (about 6% of $0.0059). The win is
small in dollars; the point is the owner rule, and fewer conflicting examples.

## Measurement plan

- A DEV run = 107 cases = 214 live parser turns (history replays included), about $1.2.
  The 300-call/day cap allows ONE DEV run a day.
- DEV has 2 Malay, 11 Chinese and 4 mixed cases. Most cuts are Malay/Chinese wording, so DEV
  barely sees them; the hidden gate (crew) is the real test for language.
- Topics hold 1 to 27 cases each, so "a topic worse by more than 2-3%" cannot be read off one
  run: one case is 4 to 100 points of a topic. Aggregate run-to-run noise is 0.7 pt with 7/214
  cases flipping (CHATBOT-EVAL report).

## Questions (recommendation first)

Q1. Order of runs given one DEV run per day.
 (a) ONE run with every cut at once; restore a block only where flipped cases trace back to it.
 (b) one run per group (G1 language lists, G2 generic NLU blocks, G3 duplicates/stale): 3 days.
 (c) one run per section (about 40 runs).
 Recommendation (a): the parser JSON of each turn is in the trace, so a flip can be traced to the
 field and block that changed it for free, then only the flipped cases re-run.

Q2. What "a topic got worse" means.
 (a) a case that passes on v50 and fails on the trim, fails again on a re-run of that case, and
     its parser JSON changed in a field a cut block governs. Keep the cut if no such case exists.
 (b) per-topic pass rate drop over 3 pt on one run.
 Recommendation (a): (b) fires on a single noisy case in any small topic.

Q3. Budget: raise today's cap to about 450 calls (one 214 run + re-runs of flipped cases on BOTH
 v50 and the trim, so noise is measured not assumed)? (a) yes (b) no, stay at 300.
 Recommendation (a).

Q4. WHAT IS NEVER AN ENTITY's grammar particles ("de", "lah", "的") were added for a live bug
 ("de" read as a warehouse). Language by the owner rule, but a known-bug guard.
 (a) cut it and let the run decide (b) keep it as a bug guard.
 Recommendation (a): the owner rule says the model reads particles itself; the run will show it.

Q5. PRODUCT CODE's Chinese -> English class map (马桶 -> "water closet", 水龙头 -> "tap") picks
 OUR category names, so I classed it BUSINESS and keep it. (a) keep (b) cut.
 Recommendation (a): the model cannot know we call it "wash basin", not "basin".
