-- MULTI-ASK-PLAN2 (PR #1542): the dev copy of migration maskp_0001_parser_sub_asks.
-- A NEW UNLABELLED chatbot_semantic_parser version = the PROMPT-ABLATION base that
-- pab_0001_parser_ablation published (dev v56) with SUB_ASKS_ADDENDUM right before its
-- MEMORY block. No label moves. Idempotent: a version already holding the result inserts nothing.
-- Precondition: pab_0001's version is on the database (the first SELECT returns one row).
SELECT version, length(template) FROM ai_prompt_versions
WHERE name = 'chatbot_semantic_parser' AND commit_message = 'pab_0001_parser_ablation: v50 minus language teaching and the class map (PROMPT-ABLATION, eval v56 fe4f3509), unlabelled';

WITH base AS (
  SELECT replace(template, $q$

== MEMORY ==
$q$, $q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUB ASKS AND FAMILY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Two more OUTPUT keys, on every object:
  "sub_asks": [{"measure", "group_by", "date_from", "date_to", "basis", "label"}] or null
  "family": true | false | null

sub_asks: when the CURRENT message asks TWO OR MORE different things, one object per thing, in
the order asked, whatever they are. One thing asked: null. The date and quantity of the last
receipt are ONE last_receipt ask.
  measure: "received_qty" (how many already came in over a period, never goods still on the
    way) | "last_receipt" (when it last came in: the last SPO line's delivery date) |
    "incoming" (only goods not yet arrived) | "stock" (on hand now) | "supplier" |
    "last_cost" (purchase price) | "sold_total" (how many sold) | "sold_by" (sold per group) |
    "other" (anything else asked, such as margin or profit; label says what).
  group_by: sold_by only, "sales_agent" | "customer" | "product" | "brand" | "category" |
    "location" | "channel" | "month"; else null.
  date_from / date_to: YYYY-MM-DD, only a period the CURRENT message names; it covers every
    received_qty, sold_total, sold_by and incoming ask. Other measures: null. None named:
    null, never this calendar year by default.
  basis: sold_total / sold_by only, "delivered" or "ordered" when the message says which; a
    plain "sold" is null, never the top selling default.
  label: the asker's own words for this ask, short.
A message that only names a product and asks nothing about it: domain_hint, intent_hint, asks
and sub_asks null.

family: true when the message's own words ask for the code's whole family of variants, false
when they ask for only that exact code; a code typed with neither said is null (nearly always).

A reply that chooses an option of the variant question ("just this code" / "all variants") or
the sold basis question ("delivered" / "ordered") is open_question_answer mode "pick" with that
option's position, never yes, no or a new question.
$q$ || $q$

== MEMORY ==
$q$) AS template
  FROM ai_prompt_versions
  WHERE name = 'chatbot_semantic_parser' AND commit_message = 'pab_0001_parser_ablation: v50 minus language teaching and the class map (PROMPT-ABLATION, eval v56 fe4f3509), unlabelled'
  ORDER BY version DESC LIMIT 1
)
INSERT INTO ai_prompt_versions (id, name, version, type, template, variables, config_json, commit_message)
SELECT gen_random_uuid(), 'chatbot_semantic_parser',
       (SELECT max(version) + 1 FROM ai_prompt_versions WHERE name = 'chatbot_semantic_parser'),
       'text', base.template, '["current_date"]'::jsonb, NULL, 'MULTI-ASK-PLAN2: the sub_asks and family keys on the PROMPT-ABLATION base (one entry per thing a message asks; exact code or family). Unlabelled, promote from the Chatbot Console.'
FROM base
WHERE NOT EXISTS (
  SELECT 1 FROM ai_prompt_versions v WHERE v.name = 'chatbot_semantic_parser' AND v.template = base.template
);

SELECT version, commit_message FROM ai_prompt_versions
WHERE name = 'chatbot_semantic_parser' AND commit_message = 'MULTI-ASK-PLAN2: the sub_asks and family keys on the PROMPT-ABLATION base (one entry per thing a message asks; exact code or family). Unlabelled, promote from the Chatbot Console.';
