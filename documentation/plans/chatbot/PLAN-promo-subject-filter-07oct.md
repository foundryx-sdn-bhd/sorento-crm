# PLAN: promotion asks filter by subject; unresolved subject asks back; tier menu always stamped (PROMO-SUBJECT-FILTER)

Status: BUILDING (7 Oct 2026). Track: full M (chatbot behaviour + backend/MCP param; no
migration, no auth change, no UI). LEAD pattern: tester red first, coder green, reviewer.
Card: `promo-subject-filter-behaviour-card.md`. UAC: `promo-subject-filter-acceptance-criteria.md`.
Q5 (a) by the owner 7 Oct: R6 brand echo IN. Q4 re-asked (A3 flyer is a promotion KIND, promotion_types); R5 held.

## Journey

A dealer asks "any basin cabinet promo". Today the bot shows an unstamped Office/Dealer/End user
menu, then sends every Office promotion (a kitchen sink flyer first). After: an unknown subject
asks back; a known subject ("bathroom furniture") shows a stamped menu and sends only promotions
linked to products of that class.

## Design (facts, origin/main 292a366ee)

- Subject = the grounded class (product_specifications.values.class), the same membership the
  resolver uses: `filter_specs` / `membership_clause` (services/product_spec_search.py:886-1058).
  Promotion link = `promotion_products` (models/marketing.py:175-209), the same link
  `_leg_promotion` uses (services/product_predicate_service.py:383-413).
- Not product_ids: classes hold 1k-7k products (product_class_signal.py:52-61) and the MCP call
  is a GET; the existing caps (200) would drop promotions silently.

## Slices

1. Backend: promotions list (api/v1/marketing/promotions.py:56-226, marketing_service.py:448-530)
   takes a subject param (scope terms / class labels), filters
   `EXISTS(promotion_products pp JOIN product_specifications ps ON ps.product_id = pp.product_id
   WHERE <membership clause>)`; no inactive fallback when it is set (AC-1..3).
2. MCP: expose the param on the promotions tool (sorento_crm_mcp catalog.py:242-291) (AC-4).
3. Chatbot fetch: TYPE_TO_PARAM (lanes/business/fetch.py:311-338) maps grounded product_type /
   category entities to the param on the probe (fetch.py:218-305) and main fetch; subject carried
   across the tier pick (turn/apply.py:814-826, turn_runtime.py:2966-3007, 3594-3671) (AC-4..6).
4. Chatbot flow: unresolved subject (spec value None / unknown_values) on a promotion ask beats
   the tier bridge (engine.py:5520-5557) with the ask-back reply (AC-7, AC-8); the bridge path
   runs the probe before any menu, all-zero -> no-promotion line, probe failure -> could-not-check
   (AC-9..11, answer.py:256-330, answer_bridge.py:636-676).
5. Brand echo (AC-16): access_level_choice_message (answer.py:303-312) names the resolved brand.
6. Prompt: no parser prompt change expected for R1-R4 (subject comes from existing grounding). If
   live eval shows the parser not emitting the subject entity, publish a new UNLABELLED dev
   version via `chatbot_prompt_vars.publish_wording_edit` (chatbot_prompt_vars.py:700-734).

## Not in scope / named triggers

- "basin cabinet" resolving to Bathroom Furniture is vocabulary DATA (a class synonym), not code;
  this round it asks back (Q1). Adding the synonym is an owner data call.
- item_types link: unused by search today (models/product.py:240-245); not used here.
