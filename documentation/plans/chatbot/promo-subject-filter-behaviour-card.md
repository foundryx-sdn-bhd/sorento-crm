# Behaviour card: promotion asks filter by subject (PROMO-SUBJECT-FILTER)

Filed 7 Oct 2026 as crew ask a79830589. Rulings (crew, 7 Oct): Q1 (a), Q2 (a) (owner rule: explicit links, no name matching), Q3 (a) (probe failure says it could not check). Q4 and Q5 with the owner; R5 and R6 held.



CAUSE (re-verified)
- Turn 1 "any basin cabinet promo": grounding turns "basin cabinet" into a spec entity product_type=unknown (chatbot/head/grounding.py:670-678); resolver gets nothing (resolve_gate.py:575-582, 643-654) so the turn exits not_found and goes through the bridge (engine.py:5539-5546, answer_bridge.py:636-676), which never runs the tier probe (probe only on the continue exit, engine.py:5606-5627). So no "- has promotion" stamps (answer.py:317). Reply "1" then calls the promotions tool with access_levels only (fetch.py:311-338 maps product and brand, nothing for type/category), backend returns the newest served set (api/v1/marketing/promotions.py:183-190): an unrelated kitchen sink promotion first.
- Turn 2 "sroento a3 flyer": brand narrowing via tier_gate.recompose (tier_gate.py:76-118); menu echoes the typed word by design (D16, answer.py:303-312). Parser field contains_flyer exists (head/parser.py:209) but never reaches the fetch. Nothing filters by promotion type.
- Data exists to filter: promotion_products links each promotion to products (models/marketing.py:175-209); products carry item_type (F/CABINET, WB-CB, KITCHEN SINK...) and brand. 206 of 214 live promotions have linked products, 8 have none. promotion_types has a3_flyer (match_markers ["a3","a3 flyer"]), 3 live rows, ALL Mocha.

REAL EXAMPLES
Kept out of this public repo (real promotion rows); they live in crew ask a79830589. Shape: a type ask matches only promotions linked to products of that item type; a brand with no promotion of the asked type gets told so.

PROPOSED RULES
R1 Subject filter: when the ask names a product type/category/product, promotions are kept only if one of their linked products matches (promotion_products join). Filter is SQL after the parser, no keyword or name matching in code.
R2 Parser grounds the subject: "basin cabinet" must ground to known item type(s) instead of "unknown product type"; done in the parser prompt (new dev prompt version) given the item type list, not in code.
R3 Unresolved subject: no tier menu, no files. Reply names what failed, one fact per line, e.g. "I could not match "basin cabinet" to a product type." / "Which product or brand do you mean?"
R4 Tier menu is always stamped: the probe runs on every path that shows the menu, using the same subject filter. Levels with zero matching promos stamp "- no promotion"; if all levels are zero, no menu: "No current promotion for basin cabinet."
R5 Format words: parser's promotion format (A3 flyer, special) filters by promotion_type. If none match, say so and name what does exist: "No Sorento A3 flyer right now." / "Mocha has an A3 flyer."
R6 Echo: when the parser resolved a brand from a typo, the question names the canonical brand ("Sorento"), not the typo.

EDGE CASES
- Promotion with no linked products (8 live) never matches a subject ask; still returned for a no-subject "any promo?" ask.
- Subject + brand ("sorento basin cabinet"): both filters AND.
- Product code ask (existing product_ids path) unchanged.
- Office level is per brand (sorento_office / cabana_office / mocha_office); unchanged.

QUESTIONS
Q1 (a, c) Unresolved subject: (a) R3, ask back, no menu, no files; (b) keep menu but stamp levels and send all promos of that level with a line "could not match X, showing all". Rec (a): owner rule "never a wrong confident answer".
Q2 (b) How to filter by subject: (a) linked products (R1), deterministic; (b) parser reads promotion names and picks; (c) both, names as fallback for the 8 unlinked. Rec (a): link data covers 206/214 and is the source of truth; (b) is NLP over file names and drifts.
Q3 (c) Unstamped tier menu: (a) never shown, probe on every path (R4); (b) allowed when probe fails. Rec (a), if probe errors say "could not check promotions right now".
Q4 (d) "a3 flyer": (a) R5 filter by promotion_type from the parser and say what exists instead; (b) ignore format words. Rec (a): type data already exists (promotion_types.a3_flyer).
Q5 (e) Typo echo: (a) R6 echo canonical brand when resolved, typed word only when it did not resolve; (b) keep raw echo (D16). Rec (a): D16 guards against naming a guessed product; a resolved brand is not a guess.
