# PLAN: parser prompt ablation, language teaching out (PROMPT-ABLATION, 11 Oct 2026)

Status: building (M experiment lane, crew lead pattern). Card: `CARD-prompt-ablation-11oct.md`.
UAC: `prompt-ablation-acceptance-criteria.md`.

## Rulings (crew, 11 Oct)

Q1 (a) one DEV run with every cut at once; trace flips to blocks through the parser JSON in the turn
trace; restore only guilty blocks. Q2 (a) a cut is guilty only when a case passes on v50, fails on
the trim, fails again on a re-run, and its parser JSON changed in a field the cut governs. Q3 (a)
cap ~450 live calls today (dev key). Q4 (a) cut the grammar-particles block; if "de" comes back as
a warehouse, restore it and record that as evidence. Q5 (a) KEEP the Chinese -> English class map
(our taxonomy). Drop the 2 duplicate sections. Crew runs the hidden gate before any promotion.

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
| C42 | PRODUCT CODE 1931, 1940-1941 | "paip", "龙头" in the type-word list and the Malay example; KEEP the Chinese -> English class map (Q5) |
| C43 | TRAP SPECS 2010-2013, 2027, 2033 | "tandas", "jamban", "马桶", "坐厕", "ada stok", "有货", the Malay example, "s trap pula", "P弯呢" |
| C44 | PROMOTION KIND 1973 | "传单" |
| C45 | MEMORY 2075 | Malay / Chinese examples |
| C46 | A BARE "SPEC" 949 | "规格" |
| D01 | A DELIVERY WORD PLUS A NAME 975-976 | whole section (duplicate of 320) |
| D02 | THE ATTRIBUTE PHRASE, WHOLE 978-979 | whole section (duplicate of 241) |
| D03 | CURRENT DATE tail 2084 | stale "before calling the MCP tool" line |

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

(filled after the runs)
