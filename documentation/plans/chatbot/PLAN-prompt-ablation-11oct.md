# PLAN: parser prompt ablation, language teaching out (PROMPT-ABLATION, 11 Oct 2026)

Status: building (M experiment lane, crew lead pattern). Card: `CARD-prompt-ablation-11oct.md`.
UAC: `prompt-ablation-acceptance-criteria.md`.

## Rulings (crew, 11 Oct)

Q1 (a) one DEV run with every cut at once; trace flips to blocks through the parser JSON in the turn
trace; restore only guilty blocks. Q2 (a) a cut is guilty only when a case passes on v50, fails on
the trim, fails again on a re-run, and its parser JSON changed in a field the cut governs. Q3 (a)
cap ~450 live calls today (dev key). Q4 (a) cut the grammar-particles block; if "de" comes back as
a warehouse, restore it and record that as evidence. Q5 overruled by the OWNER (11 Oct): the class map is CUT too (C57); the system has more
categories than a static prompt holds, and matching a mention in any language belongs to the
resolver (investigation in Results). Drop the 2 duplicate sections. Crew runs the hidden gate before any promotion.

## Shape (simplest thing)

- `sorento_crm_backend/alembic/data/chatbot_semantic_parser.v50.txt`: v50 exactly as stored
  (`ai_prompt_versions` c63439ac..., md5 4a79359ae71f7d4f1d45fc7dd4153d72), the source.
- `sorento_crm_backend/scripts/build_parser_ablation.py`: mirrors `scripts/build_parser_candidate.py`.
  `CUTS`: list of (cut_id, old, new) exact replacements, each `old` occurring exactly once in v50
  (raise otherwise). `--restore C08,C15` rebuilds without those cuts (the Q1 restore path).
  Writes `alembic/data/chatbot_semantic_parser.ablation-20261011.txt`.
- No migration, no label move. Crew applies `crew/state/migrations/PROMPT-ABLATION.sql` (INSERT one
  unlabelled `ai_prompt_versions` row with a fixed id) to `sorento_eval_base` only.
- Tests: `sorento_crm_backend/tests/chatbot_eval/test_parser_ablation.py`, DB-free.

## Cut list (v50 line numbers; replace with the rule alone, never the business part)

Language = synonym / translation lists, Malay and Chinese copies of English examples, misspelling
teaching, number / ordinal / yes-no words, honorifics, grammar particles, relative-date arithmetic.

| id | where (v50 lines) | cut |
|---|---|---|
| C01 | AFFIRMATION 18-22 | escalate-word spelling list; keep "the escalate word itself, any form, is agreement; also request_for_help" |
| C02 | READING 53-55 | number-words bullet |
| C03 | DECISIVE DOMAIN TERMS 99-101, 126-128, 185-187 | "in ANY language, paraphrase, or misspelling ... matched by MEANING" boilerplate; resource_attachment example list incl. katalog / senarai status kontena (keep the "document-class word + brand = get_resource_attachment, never promotion" sentence); ideate phrasing list (keep "PROPOSAL about the FUTURE") |
| C04 | DECISIVE order bullet 141, 146, 150 | "hantar", "belum hantar", "X ada ambil Y" |
| C05 | DOMAIN IN MESSAGE 79 | "卖得最多, 排名" |
| C06 | REQUESTED ATTRIBUTES 254, 266, 285-287 | "berapa"; "到仓库, sampai gudang"; Chinese/Malay copies of the worked examples |
| C07 | MESSAGE TYPE 313, 320 | escalate spelling list; "hantar ke hanlim" |
| C08 | ENTITY OPERATIONS 382-384, 387 | translation parentheticals (katalog->..., gambar/foto->...); keep "translating ANY language or synonym" and the authority-cert exception |
| C09 | WHAT IS NEVER AN ENTITY 401-405 | grammar-particles paragraph + "de" example (Q4) |
| C10 | ENTITY CONFIDENCE 421-440 | whole section -> 3-line definition of `confident` (default true; false only for an unlabelled mash of several concepts; labelled parts split, each true) |
| C11 | BROADEN AXIS 462-466 | phrase list incl. "lah"; keep "widens the date filter, NEVER moves domain_hint to incoming" |
| C12 | DATE FILTER 547-555 | relative-date arithmetic and its example; keep: any date in the current message, past or future, converted with CURRENT DATE; single day sets both |
| C13 | POSITIONAL REFERENCES 571-574 | ordinal / range examples |
| C14 | PERSON-NAME MENTION 590-591 | honorific list |
| C15 | ORDER_STATUS FILTER 640-644, 660-672 | Malay synonyms; misspelling teaching + typo examples; KEEP "dealer quantity = quantity" and "'... report for <product>' beside a status word = same order answer" |
| C16 | IS_ACTIVE 678-683 | synonym lists down to one word per value |
| C17 | GROUP_BY 796-801 | "ikut ..." variants |
| C18 | TOP_N 809 | "最近3次" |
| C19 | so_outstanding 816 | Malay / Chinese variants |
| C20 | PURCHASE ORDERS 919 | "采购单", "已下单" |
| C21 | SPO LAST RECEIPT 935 | "上次进货", "terakhir masuk" |
| C22 | LAST PURCHASE COST 990-991, 995 | Chinese / Malay variants; KEEP "'cost' alone = selling price" |
| C23 | LOW STOCK REPORT 1011 | Malay / Chinese variants |
| C24 | SALES REPORT 1039 | Malay / Chinese variants |
| C25 | SALES_CHANNEL 1067 | "数量", "已送货" (keep "a bare answer never sets sales_channel") |
| C26 | entities[].quantity 1151 | "tukar ... jadi 80" |
| C27 | OPEN TASK 1169 | "teruskan" |
| C28 | Last answered 1185-1186 | "kalau 100?", "100 pula?" |
| C29 | OPEN QUESTION 1214-1247 | Malay / Chinese / pinyin ordinals, confirm words, counts, number-words paragraph |
| C30 | OPEN NUMBERED QUESTION 868-876 | paraphrase example list; KEEP "when only ONE option is offered, any request for the list is that option" |
| C31 | SALES ANALYSIS 1264-1265 | Malay / Chinese variants |
| C32 | TOP SELLING 1414-1421 | Malay / Chinese variants and the duplicate English synonym run |
| C33 | RANK_BY / BASIS 1433-1441 | Malay / Chinese variants |
| C34 | ANSWERING TOP SELLING 1471 | Chinese basis line |
| C35 | RANK_DIRECTION 1500-1505 | Malay / Chinese, typo sentence ("worsr", "wrost") |
| C36 | NARROWING TOP SELLING 1517-1543 | "jurujual"; "salea"/"saless" slip sentence |
| C37 | SELF_REFERENCE 1578-1580 | Malay / Chinese pronoun lists |
| C38 | ESCALATION CONFIRMATION 1610, 1627 | "boleh", "tak apa" |
| C39 | ACCOUNT 1746-1748 | account spelling list -> one line (any spelling; Roman numerals are integers) |
| C40 | LOW STOCK FILTERS 1772-1818 | translation lists, "semua", "全部", "ikut ...", "按...", the Malay and Chinese examples, "batal"; KEEP "categories in English" |
| C41 | SALES RANKING 1834-1845, 1865 | Chinese words in the business-model line and examples, Chinese basis line; KEEP "a dealer IS our customer" |
| C42 | PRODUCT CODE 1931, 1940-1941 | "paip", "龙头" in the type-word list and the Malay example |
| C57 | PRODUCT CODE 1958-1961 | the Chinese -> English class map (owner 11 Oct); keep the rule "on sales_ranking / top_selling a category canonical_code is the English class word" |
| C43 | TRAP SPECS 2010-2013, 2027, 2033 | "tandas", "jamban", "马桶", "坐厕", "ada stok", "有货", the Malay example, "s trap pula", "P弯呢" |
| C44 | PROMOTION KIND 1973 | "传单" |
| C45 | MEMORY 2075 | Malay / Chinese examples |
| C46 | A BARE "SPEC" 949 | "规格" |
| D01 | A DELIVERY WORD PLUS A NAME 975-976 | whole section (duplicate of 320) |
| D02 | THE ATTRIBUTE PHRASE, WHOLE 978-979 | whole section (duplicate of 241) |
| D03 | CURRENT DATE tail 2084 | stale "before calling the MCP tool" line |

Added in build (language only, lead ruling 11 Oct after the coder's first pass): C47 leftover
Malay/Chinese words ("gambar", "semua variant", "这些", "sepuluh", "10个", "这个产品" sentence);
C48a-d typo teaching in DOCUMENT and OPEN NUMBERED QUESTION ("Outstsnding", "stoick"); C49a-c
company-pick English paraphrase lists; C50a-b sales-agent / category paraphrase lists; C52h plural
synonyms in DOCUMENT; C52i year-span wording; C53a-b typo examples ("Sorneto", "walll"; the rule
"a typo is corrected to the listed name" stays); C53g year-phrasing variants; C54a-b redundant
cross-reference prose. A multi-span cut uses suffixed ids (C15a..e); `--restore C15` restores all.
C10 inserts a header `== ENTITY OP: how this turn relates to the previous entities ==` so the
entity_op rules (unchanged) sit in their own section.
Rejected from the first pass (business worked examples or an output rule, restored): C48e (raw is
the word spelt right), C51-C52f, C53c-f, C54c-d, C55a-b, C56a-b.

Never cut: any output key, value set, domain / intent / team name, code shape rule (container
4+7, PO number shape, warehouse short-token shapes), owner rulings, company codes, Known brands /
Product types / Promotion kinds handling, the Chinese -> English class map.

## Measurement

1. Build + test (no live calls). 2. Crew applies SQL. 3. One DEV run, `--prompt-version <fixed id>`,
214 calls. 4. Flipped cases (v50 pass -> trim fail, and the reverse) re-run on both v50 and the trim
(about 2 x flips x 2 turns). 5. Guilty cuts restored with `--restore`, rebuilt, re-run on the guilty
cases only. 6. Report pass / strict per topic and language, cost/turn, cache %, p95 vs v50 OFF
(DEV 84.1% / $0.0059 / 7.96 s). 7. Crew runs the hidden gate.

## Results

### DEV run 2 (11 Oct, valid; v53 = id 882f8b23 vs v50 OFF from DP1, same DB and runner, originals)

| | v50 | trim v53 |
|---|---|---|
| pass | 84.1% (90/107) | 77.6% (83/107) |
| strict | 41.1% | 39.3% |
| prompt tokens / turn | 39,507 | 36,454 (-7.7%) |
| cache share | 96.1% | 97.6% |
| cost / turn | $0.00590 | $0.00523 (-11%) |
| latency p50 / p95 | 3.85 s / 7.96 s | 3.32 s / 7.19 s |

Per area, passes v50 -> v53: incoming 15 -> 12 / 15, inventory 34 -> 32 / 37, master_products 5 -> 4 / 5,
order 11 -> 10 / 14, purchase_order 3 -> 2 / 3, resource_attachment 1 -> 2 / 3, others equal. By language:
en 77 -> 72 / 90, zh 9 -> 8 / 11, mixed 4 -> 3 / 4, ms 0 -> 0 / 2.

8 cases lost, 1 won. Q2 re-run of the 8 on v53 (20 calls): 3 pass again (noise, excluded). 5 fail again,
4 distinct roots (case ids crew-only):
- R1 incoming, a numbered pick "no 1 & no 8" over a product list: v53 answers every row. Suspects: C13
  (positional examples, also reviewer finding 1), C29 / C30 (open-question pick words).
- R2 a quantity follow-up "14 ada stock" after a stock answer: v53 parser emits entity {raw "14", hint
  product} (trace) instead of a quantity; a later "eta?" in the same thread then lists 50 products.
  Suspects: C28 ("Last answered" Malay phrasings), C43 ("ada stok" = the stock ask).
- R3 a bare product code with no history: v53 parser domain_hint null, so the reply asks "What would you
  like me to know?". The sibling case passed on re-run, so this is borderline. Suspect: none named yet
  (needs the v50 parser JSON).
- R4 "purchasing" after a yes: both versions reply alike; v50 passed only through a memory recap that
  named the code. Likely grader variance, to confirm on v50.
Q2 is not complete: the v50 side (does v50 pass these 5 again, and what did its parser emit) needs ~14
calls; today's cap (450) is spent (449). Next (12 Oct): v50 re-run of the 5 (~14 calls), then v54 = v53
with the guilty cuts restored (`--restore C13,C28,C43` as evidence dictates), full DEV on v54 (214),
crew hidden gate.


Run 1 (11 Oct, 214 calls) was INVALID: the lane stack had no MCP server, every lookup failed with
"Connection refused", pass 50.5%. Lead's setup defect, not a prompt effect. Discarded.

### Owner ask: what translation the system already has (read-only, 11 Oct; paths under sorento_crm_backend/)

- `translation_memory` (`app/models/translation_memory.py:44-73`) + `translate()` (`app/services/translation_service.py:89`:
  memory first, AI on a miss). Used only by SCM supplier documents (`app/services/scm/supplier_document_service.py:335`,
  `app/services/scm/description_translation.py:37`) and chat reply labels (`app/services/chatbot/label_catalog.py:842-871`).
  On sorento_eval_base: en->zh 298 and en->ms 298 (chat labels), zh->en 19 (supplier phrases such as 连体马桶 ->
  "One-piece toilet"); 0 rows equal a category `class_label`.
- Category vocabulary: `ProductCategory.class_label` + `search_synonyms` (`app/models/product.py:52-56`), seeded from
  `CLASS_SYNONYMS` (`app/services/product_class_signal.py:66-93`, English and Malay, no Chinese). 120 categories, 39 with a
  class_label (11 labels), 0 with a Chinese synonym. Spec registry `synonyms / user_synonyms / value_labels`
  (`app/models/product_spec.py:66,113,154`) has no Chinese either.
- Brands and products: no translated columns (`app/models/product.py:98-153`); 0 of 12 brands and 0 of 15,390 products
  carry Chinese. Frontend i18n (`sorento_crm_frontend/i18n/messages/*.json`) is UI strings only.
- Resolver today: category mentions go through `_top_selling_category_ids` (`app/services/chatbot/lanes/business/__init__.py:529-565`)
  -> `resolve_category_token` (`.../services.py:650`, exact / whole word) -> `resolve_category_class` (`services.py:781-840`)
  -> `ClassVocabulary` (`product_class_signal.py:228-311`, exact lookup over class_label, search_synonyms, registry synonyms,
  CLASS_SYNONYMS). The parser's English canonical_code is preferred over raw at `app/services/chatbot/turn/apply.py:1509-1515`
  (top_selling) and `.../report_ask.py:284-286` (sales_ranking). Brands: `resolve_brand_token` (`services.py:764`). Products:
  `entity_resolver` exact -> prefix -> trigram (:2927) -> embedding (:2844). No resolver path reads `translation_memory`.
- Smallest hook: in `ClassVocabulary.resolve` after the exact lookup (`product_class_signal.py:300-311`), or in
  `_top_selling_category_ids` before a word is marked unknown (`__init__.py:556-561`): when the raw mention is not Latin
  script, look it up in `translation_memory` (zh->en; `translate()` fills a miss with one cached AI call) and feed the English
  result to the same exact / synonym lookup. Simpler still: add the Chinese words as `search_synonyms` / registry
  `user_synonyms` (staff-editable), which the existing exact lookup already matches with no code change.
- Gaps: no Chinese names exist for any category, brand or product; the zh->en memory rows do not land on class labels
  ("Basin" is not "Wash Basin"), so a translated word still has to match a label or synonym; an AI fill adds one call per unseen
  word (cached after). Whether customers type Chinese brand names is UNVERIFIED.
