-- MULTI-ASK-PLAN2 (PR #1542): the dev copy of migrations maskp_0001_parser_sub_asks and
-- maskp_0002_measure_words.
-- 1. The sub-ask measure glossary: chatbot_status_words rows tagged sub_ask_measures, which the
--    new prompt reads through {{sub_ask_measures}}. Insert-only; an existing value is kept.
INSERT INTO chatbot_status_words (id, domain, value, label, trigger_words, sort_order, prompt_lists) VALUES (gen_random_uuid(), 'goods_receive', 'received_qty', 'how many already came INTO the warehouse over a period, whatever the word; never goods still on the way, never incoming', ARRAY['received', 'came in', 'goods received', '入库', '进库', '进仓', '进仓库', '入仓', '收货', '到了多少', 'diterima', 'masuk gudang']::text[], 100, ARRAY['sub_ask_measures']::text[]) ON CONFLICT (value) DO NOTHING;
INSERT INTO chatbot_status_words (id, domain, value, label, trigger_words, sort_order, prompt_lists) VALUES (gen_random_uuid(), 'spo_allocation', 'last_receipt', 'when it last came in: the last SPO line''s delivery date, with its quantity', ARRAY['last in', 'last received', 'last incoming qty', '最后一次进库', '最后一次入库']::text[], 101, ARRAY['sub_ask_measures']::text[]) ON CONFLICT (value) DO NOTHING;
INSERT INTO chatbot_status_words (id, domain, value, label, trigger_words, sort_order, prompt_lists) VALUES (gen_random_uuid(), 'incoming', 'incoming', 'only goods NOT yet arrived (on the way, ETA); goods that went into the warehouse are received_qty', ARRAY['incoming', 'on the way', 'arriving', 'ETA', '到货', '在途', '在路上']::text[], 102, ARRAY['sub_ask_measures']::text[]) ON CONFLICT (value) DO NOTHING;
INSERT INTO chatbot_status_words (id, domain, value, label, trigger_words, sort_order, prompt_lists) VALUES (gen_random_uuid(), 'inventory', 'stock', 'on hand now', ARRAY['stock', 'on hand', '库存', 'stok']::text[], 103, ARRAY['sub_ask_measures']::text[]) ON CONFLICT (value) DO NOTHING;
INSERT INTO chatbot_status_words (id, domain, value, label, trigger_words, sort_order, prompt_lists) VALUES (gen_random_uuid(), 'purchase_cost', 'supplier', 'who supplies it', ARRAY['supplier', '供应商', 'pembekal']::text[], 104, ARRAY['sub_ask_measures']::text[]) ON CONFLICT (value) DO NOTHING;
INSERT INTO chatbot_status_words (id, domain, value, label, trigger_words, sort_order, prompt_lists) VALUES (gen_random_uuid(), 'purchase_cost', 'last_cost', 'the purchase price', ARRAY['last cost', 'purchase price', 'cost price', '采购价', '进价', 'harga beli']::text[], 105, ARRAY['sub_ask_measures']::text[]) ON CONFLICT (value) DO NOTHING;
INSERT INTO chatbot_status_words (id, domain, value, label, trigger_words, sort_order, prompt_lists) VALUES (gen_random_uuid(), 'sales', 'sold_total', 'how many were sold in all, in any language; a sale is never other', ARRAY['sold', '卖了', '卖出', 'jual', 'jualan', 'terjual']::text[], 106, ARRAY['sub_ask_measures']::text[]) ON CONFLICT (value) DO NOTHING;
INSERT INTO chatbot_status_words (id, domain, value, label, trigger_words, sort_order, prompt_lists) VALUES (gen_random_uuid(), 'sales', 'sold_by', 'how many were sold per group (salesman, customer, ...)', ARRAY['per salesman', 'per customer', '每位业务员', '业务员', 'ikut salesman']::text[], 107, ARRAY['sub_ask_measures']::text[]) ON CONFLICT (value) DO NOTHING;
INSERT INTO chatbot_status_words (id, domain, value, label, trigger_words, sort_order, prompt_lists) VALUES (gen_random_uuid(), 'sales', 'other', 'anything else asked; the label says what', ARRAY['margin', 'profit', 'lead time', '毛利', '利润']::text[], 108, ARRAY['sub_ask_measures']::text[]) ON CONFLICT (value) DO NOTHING;

-- 2.
-- A NEW UNLABELLED chatbot_semantic_parser version = the v56 text with CHAT_REPORT and
-- REF_KINDS that rkind_0002_parser_ref_kinds_v56 published, with SUB_ASKS_ADDENDUM right
-- before its MEMORY block, then the DEV-FAILURES and portal_form addenda appended as
-- devf_0001_parser_data_rules appends them. No label moves. Idempotent: a version already holding the result inserts nothing.
-- Precondition: rkind_0002's version is on the database (the first SELECT returns one row).
SELECT version, length(template) FROM ai_prompt_versions
WHERE name = 'chatbot_semantic_parser' AND commit_message = 'rkind_0002_parser_ref_kinds_v56: v56 ablation text plus CHAT_REPORT and REF_KINDS (REF-PROBES), unlabelled';

WITH base AS (
  SELECT rtrim(replace(template, $q$

== MEMORY ==
$q$, $q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUB ASKS AND FAMILY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Two more OUTPUT keys, on every object:
  "sub_asks": [{"measure", "group_by", "date_from", "date_to", "basis", "label"}] or null
  "family": true | false | null

sub_asks: count the measures below that the CURRENT message asks about, however short the
message: each one is one thing asked. TWO OR MORE: one object per thing, in the order asked,
whatever they are ("stock and purchase price" is stock then last_cost). One thing asked: null.
A count AND its breakdown are two things: "how many sold, by salesman" is sold_total then
sold_by. The date and quantity of the last receipt are ONE last_receipt ask.
  measure, ONE of:
    "received_qty": HOW MANY came in, a quantity received over a period; never goods still
      on the way.
    "last_receipt": WHEN it last came in, a date (the last SPO line's delivery date), with
      that delivery's quantity.
    "incoming": only goods NOT yet arrived (on the way, ETA).
    "stock": how many are on hand now.
    "supplier": WHO supplies it.
    "last_cost": the purchase price.
    "sold_total": how many were sold in all.
    "sold_by": how many were sold per group (salesman, customer, ...).
    "other": anything else asked, such as margin or profit; the label says what.
  Words people use for each measure, in any language:
{{sub_ask_measures}}
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
$q$), E'\n')
         || $q$
== WHICH RECORDS ANSWER THE QUESTION ==
- WHEN a product will have stock is an arrival question: incoming / check_incoming. Example: "when will SRTXX100 have stock".
- Stock and last receipt of a product in one message are two asks (inventory / check_stock and spo_allocation / check_spo), ordered as ASKS says.
- Whether a customer ordered a product is the "did X take/buy Y" order ask: both entities, requested_attributes ["quantity"].
- WHEN a product will be delivered or sent out is order / check_order with order_status and status "outstanding", not a requested attribute.
- A promotion word decides the domain over a generic paper word: promotion / check_promotion, and the paper word is no attachment entity.
- Requesting price tags (not the template file) is portal_link / get_portal_link with "portal_form" null.
- A portal_link ask starts a new subject: topic_reset true, no entity from earlier turns.
- Two brands joined by "and" are two brand entities.

Emit one more key on every object: "portal_form": "complaint|stock_inquiry|purchase_request|sponsorship_form|null".
== PORTAL FORM ==
- Asking for one of these four forms is portal_link / get_portal_link, never forms, and "portal_form" is that form; else null.
$q$ AS template
  FROM ai_prompt_versions
  WHERE name = 'chatbot_semantic_parser' AND commit_message = 'rkind_0002_parser_ref_kinds_v56: v56 ablation text plus CHAT_REPORT and REF_KINDS (REF-PROBES), unlabelled'
  ORDER BY version DESC LIMIT 1
)
INSERT INTO ai_prompt_versions (id, name, version, type, template, variables, config_json, commit_message)
SELECT gen_random_uuid(), 'chatbot_semantic_parser',
       (SELECT max(version) + 1 FROM ai_prompt_versions WHERE name = 'chatbot_semantic_parser'),
       'text', base.template, '["current_date"]'::jsonb, NULL, 'MULTI-ASK-PLAN2: the sub_asks and family keys on the v56 REF-KINDS base plus DEV-FAILURES (one entry per thing a message asks; exact code or family). Unlabelled, promote from the Chatbot Console.'
FROM base
WHERE NOT EXISTS (
  SELECT 1 FROM ai_prompt_versions v WHERE v.name = 'chatbot_semantic_parser' AND v.template = base.template
);

-- 3. The PRODUCTION line (crew 13 Oct 2026: the version to pin is on prod's own line, v52):
-- the production-labelled text with SUB_ASKS_ADDENDUM right before its MEMORY block, else
-- right before its policy blocks. No label moves. Idempotent on the exact template.
WITH prod AS (
  SELECT v.template, v.variables FROM ai_prompt_versions v JOIN ai_prompt_labels l ON l.version_id = v.id
  WHERE l.name = 'chatbot_semantic_parser' AND l.label = 'production'
), built AS (
  SELECT CASE
           WHEN position($q$

== MEMORY ==
$q$ IN template) > 0
             THEN replace(template, $q$

== MEMORY ==
$q$, $q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUB ASKS AND FAMILY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Two more OUTPUT keys, on every object:
  "sub_asks": [{"measure", "group_by", "date_from", "date_to", "basis", "label"}] or null
  "family": true | false | null

sub_asks: count the measures below that the CURRENT message asks about, however short the
message: each one is one thing asked. TWO OR MORE: one object per thing, in the order asked,
whatever they are ("stock and purchase price" is stock then last_cost). One thing asked: null.
A count AND its breakdown are two things: "how many sold, by salesman" is sold_total then
sold_by. The date and quantity of the last receipt are ONE last_receipt ask.
  measure, ONE of:
    "received_qty": HOW MANY came in, a quantity received over a period; never goods still
      on the way.
    "last_receipt": WHEN it last came in, a date (the last SPO line's delivery date), with
      that delivery's quantity.
    "incoming": only goods NOT yet arrived (on the way, ETA).
    "stock": how many are on hand now.
    "supplier": WHO supplies it.
    "last_cost": the purchase price.
    "sold_total": how many were sold in all.
    "sold_by": how many were sold per group (salesman, customer, ...).
    "other": anything else asked, such as margin or profit; the label says what.
  Words people use for each measure, in any language:
{{sub_ask_measures}}
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
$q$)
           ELSE replace(template, $q$

<<<CHATBOT POLICY BLOCKS>>>$q$, $q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUB ASKS AND FAMILY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Two more OUTPUT keys, on every object:
  "sub_asks": [{"measure", "group_by", "date_from", "date_to", "basis", "label"}] or null
  "family": true | false | null

sub_asks: count the measures below that the CURRENT message asks about, however short the
message: each one is one thing asked. TWO OR MORE: one object per thing, in the order asked,
whatever they are ("stock and purchase price" is stock then last_cost). One thing asked: null.
A count AND its breakdown are two things: "how many sold, by salesman" is sold_total then
sold_by. The date and quantity of the last receipt are ONE last_receipt ask.
  measure, ONE of:
    "received_qty": HOW MANY came in, a quantity received over a period; never goods still
      on the way.
    "last_receipt": WHEN it last came in, a date (the last SPO line's delivery date), with
      that delivery's quantity.
    "incoming": only goods NOT yet arrived (on the way, ETA).
    "stock": how many are on hand now.
    "supplier": WHO supplies it.
    "last_cost": the purchase price.
    "sold_total": how many were sold in all.
    "sold_by": how many were sold per group (salesman, customer, ...).
    "other": anything else asked, such as margin or profit; the label says what.
  Words people use for each measure, in any language:
{{sub_ask_measures}}
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

<<<CHATBOT POLICY BLOCKS>>>$q$)
         END AS template, variables
  FROM prod
  WHERE position($q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUB ASKS AND FAMILY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Two more OUTPUT keys, on every object:
  "sub_asks": [{"measure", "group_by", "date_from", "date_to", "basis", "label"}] or null
  "family": true | false | null

sub_asks: count the measures below that the CURRENT message asks about, however short the
message: each one is one thing asked. TWO OR MORE: one object per thing, in the order asked,
whatever they are ("stock and purchase price" is stock then last_cost). One thing asked: null.
A count AND its breakdown are two things: "how many sold, by salesman" is sold_total then
sold_by. The date and quantity of the last receipt are ONE last_receipt ask.
  measure, ONE of:
    "received_qty": HOW MANY came in, a quantity received over a period; never goods still
      on the way.
    "last_receipt": WHEN it last came in, a date (the last SPO line's delivery date), with
      that delivery's quantity.
    "incoming": only goods NOT yet arrived (on the way, ETA).
    "stock": how many are on hand now.
    "supplier": WHO supplies it.
    "last_cost": the purchase price.
    "sold_total": how many were sold in all.
    "sold_by": how many were sold per group (salesman, customer, ...).
    "other": anything else asked, such as margin or profit; the label says what.
  Words people use for each measure, in any language:
{{sub_ask_measures}}
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
$q$ IN template) = 0
    AND (position($q$

== MEMORY ==
$q$ IN template) > 0 OR position($q$

<<<CHATBOT POLICY BLOCKS>>>$q$ IN template) > 0)
)
INSERT INTO ai_prompt_versions (id, name, version, type, template, variables, config_json, commit_message)
SELECT gen_random_uuid(), 'chatbot_semantic_parser',
       (SELECT max(version) + 1 FROM ai_prompt_versions WHERE name = 'chatbot_semantic_parser'),
       'text', built.template, built.variables, '{"wording_edit": true}'::jsonb, 'MULTI-ASK-PLAN2 (production line): the production parser text plus the sub_asks and family keys. Unlabelled, promote from the Chatbot Console.'
FROM built
WHERE NOT EXISTS (
  SELECT 1 FROM ai_prompt_versions v WHERE v.name = 'chatbot_semantic_parser' AND v.template = built.template
);

SELECT version, commit_message FROM ai_prompt_versions
WHERE name = 'chatbot_semantic_parser' AND commit_message IN ('MULTI-ASK-PLAN2: the sub_asks and family keys on the v56 REF-KINDS base plus DEV-FAILURES (one entry per thing a message asks; exact code or family). Unlabelled, promote from the Chatbot Console.', 'MULTI-ASK-PLAN2 (production line): the production parser text plus the sub_asks and family keys. Unlabelled, promote from the Chatbot Console.')
ORDER BY version;
