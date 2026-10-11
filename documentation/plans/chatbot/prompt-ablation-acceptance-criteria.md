# UAC: parser prompt ablation (PROMPT-ABLATION, 11 Oct 2026)

Plan: `PLAN-prompt-ablation-11oct.md`. Paths relative to `sorento_crm_backend/`.

## Build

- U1. `alembic/data/chatbot_semantic_parser.v50.txt` is v50 byte for byte (md5
  4a79359ae71f7d4f1d45fc7dd4153d72); nothing edits it.
- U2. `python -m scripts.build_parser_ablation` writes
  `alembic/data/chatbot_semantic_parser.ablation-20261011.txt`; the committed file equals a fresh
  build (no hand edits).
- U3. Every cut's `old` text occurs exactly once in v50; a cut whose `old` is missing or occurs twice
  makes the build raise, naming the cut id.
- U4. `build(restore={"C09"})` (function form of `--restore`) keeps that cut's original text in the
  output and applies every other cut. An unknown cut id raises.

## Business rules kept (each string present in the trimmed text)

- U5. Every one of these section headers is still present, verbatim:
  `== PRICE TERMINOLOGY (Sorento-specific, decisive) ==`, `== INTENT & DOMAIN ==`,
  `== DOMAIN IN MESSAGE ==`, `== DECISIVE DOMAIN TERMS ==`, `== BARE ENTITY CONTINUATION ==`,
  `== REQUESTED ATTRIBUTES ==`, `== MESSAGE TYPE ==`, `== ACCESS LEVELS ==`, `== ENTITY OPERATIONS ==`,
  `== ENTITY CONFIDENCE ==`, `== BROADEN AXIS ==`, `== ROUTING ==`, `== DATE FILTER (per-message, never carried over) ==`,
  `== COMPANY-NAME REPLY ON AN ESCALATION OFFER ==`, `== ORDER_STATUS FILTER ==`, `== DOCUMENT ==`,
  `== ASKS (a message that names more than one domain) ==`, `== OUTPUT (exactly these keys, no others, no comments) ==`,
  `== IN AN ORDER OR OUTSTANDING ASK, A SHORT TOKEN IS A LOCATION ==`, `== SALES_CHANNEL: a NEW nullable output key, "dealer" | "project" | null ==`,
  `== THE OPEN QUESTION AND open_question_answer ==`, `== THE specification ENTITY ==`,
  `== KNOWN BRANDS: entities[].hint "brand" ==`, `== SELF_REFERENCE: the asker means their OWN account ==`,
  `== ACCOUNT: which numbered account of a customer the message names ==`,
  `== SALES RANKING: order_status "sales_ranking", domain_hint "order" ==`,
  `== A WORD BESIDE A CODE IS STILL ONE PRODUCT ENTITY ==`, `== PROMOTION SUBJECT ==`, `== MEMORY ==`,
  `CURRENT DATE: {{current_date}}`.
- U6. These business lines are still present, verbatim:
  `Company codes: Sorento = SRT, Mocha = MCH, Cabana = CBN`;
  `"cost" alone is the everyday word for`;
  `A dealer IS our customer`;
  `马桶 -> canonical_code "water closet"`; `面盆 -> canonical_code "wash basin"`;
  `only a 4-letter + 7-digit`; `"202602-S0002"`;
  `"Sorento Dealer","Mocha Dealer","Mocha Office","Cabana Dealer","Cabana Office","End User","Sorento Office"`;
  `team "customer_service",   agent "order_enquiries"`;
  `TRAP SPECS, the most common dealer ask (owner, 6 Oct 2026)`;
  `"po_refs": ["..."] or null`; `"ranking_drill"`; `"low_stock": {"group_by"`.

## Language teaching gone (each string absent from the trimmed text)

- U7. Absent: `"eskalasi"`, `"升级"`, `"satu", "dua", "lapan"`, `"yang pertama"`, `"第一个"`, `"yi hao"`,
  `"ikut pelanggan"`, `"belum hantar"`, `"terakhir masuk"`, `"上次进货"`, `"laporan jualan"`,
  `"jurujual fanny"`, `"saya punya"`, `"我的"`, `"akaun 1"`, `"户口1"`, `"paip air"`, `"tandas"`,
  `READ FOR MEANING, NOT SPELLING`, `"oustangind`, `"worsr"`, `"salea"`, `Encik/En`,
  `"lah",`, `"tomorrow" (= CURRENT DATE + 1 day)`, `"apa saya tanya tadi"`, `"传单"`,
  `== A DELIVERY WORD PLUS A NAME IS AN ORDER ASK ==`, `== THE ATTRIBUTE PHRASE, WHOLE ==`,
  `before calling the MCP tool`.
- U8. `== ENTITY CONFIDENCE ==` section body is at most 600 characters (was about 3,400).
- U9. The trimmed text is at least 9,000 characters shorter than v50 (measured 9,413 after the lead ruled out the business example cuts; the first 12,000 was an unmeasured estimate).

## Measured (lead, not tests)

- U10. One DEV run with the trimmed version pinned by id; per topic / language pass and strict,
  cost per turn, cache %, p95 against v50 OFF (84.1% / $0.0059 / 7.96 s). Total live calls today
  under 450.
- U11. Every v50-pass -> trim-fail case re-run on both versions; a cut is restored only under the Q2
  rule; each restore and its evidence listed in the PLAN results.
