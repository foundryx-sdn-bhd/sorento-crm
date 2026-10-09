-- MULTI-ASK-PLAN r4 addendum (head a3552726). A NEW UNLABELLED chatbot_semantic_parser
-- version = the production text with SUB_ASKS_ADDENDUM after PO_REFS_ADDENDUM, else before the
-- MEMORY block, else before the policy blocks. Idempotent per addendum text; no label moves;
-- the last statement RAISES if no version carries this addendum.
SELECT v.version, position($q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PO REFS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

One more OUTPUT key, on every object:
  "po_refs": ["..."] or null
Fill it whenever the CURRENT message gives a PO number, in any language or mix: "status of
PO 4500123", "PO no 4500123", "our PO 4500123", "my PO is 4500123", "PO#4500123", "PO
4500-123". Put the number as typed WITHOUT the word PO or "no" or "#" ("4500123",
"4500-123"); one string per number. Do NOT decide who the PO belongs to or what it means
(you do not know the asker's audience): fill po_refs the same way whoever asks, and keep
every other key as you would without it. A message with no PO number: null. Never invent
a number, never take one from an earlier turn.
$q$ IN v.template) AS po_refs_at, position($q$

== MEMORY ==
$q$ IN v.template) AS memory_at, position($q$

<<<CHATBOT POLICY BLOCKS>>>$q$ IN v.template) AS blocks_at
FROM ai_prompt_versions v JOIN ai_prompt_labels l ON l.version_id = v.id
WHERE l.name = 'chatbot_semantic_parser' AND l.label = 'production';

INSERT INTO ai_prompt_versions (id, name, version, type, template, variables, config_json, commit_message)
SELECT gen_random_uuid(), v.name,
       (SELECT max(version) + 1 FROM ai_prompt_versions WHERE name = 'chatbot_semantic_parser'),
       v.type,
       CASE WHEN position($q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PO REFS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

One more OUTPUT key, on every object:
  "po_refs": ["..."] or null
Fill it whenever the CURRENT message gives a PO number, in any language or mix: "status of
PO 4500123", "PO no 4500123", "our PO 4500123", "my PO is 4500123", "PO#4500123", "PO
4500-123". Put the number as typed WITHOUT the word PO or "no" or "#" ("4500123",
"4500-123"); one string per number. Do NOT decide who the PO belongs to or what it means
(you do not know the asker's audience): fill po_refs the same way whoever asks, and keep
every other key as you would without it. A message with no PO number: null. Never invent
a number, never take one from an earlier turn.
$q$ IN v.template) > 0 THEN replace(v.template, $q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PO REFS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

One more OUTPUT key, on every object:
  "po_refs": ["..."] or null
Fill it whenever the CURRENT message gives a PO number, in any language or mix: "status of
PO 4500123", "PO no 4500123", "our PO 4500123", "my PO is 4500123", "PO#4500123", "PO
4500-123". Put the number as typed WITHOUT the word PO or "no" or "#" ("4500123",
"4500-123"); one string per number. Do NOT decide who the PO belongs to or what it means
(you do not know the asker's audience): fill po_refs the same way whoever asks, and keep
every other key as you would without it. A message with no PO number: null. Never invent
a number, never take one from an earlier turn.
$q$, $q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PO REFS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

One more OUTPUT key, on every object:
  "po_refs": ["..."] or null
Fill it whenever the CURRENT message gives a PO number, in any language or mix: "status of
PO 4500123", "PO no 4500123", "our PO 4500123", "my PO is 4500123", "PO#4500123", "PO
4500-123". Put the number as typed WITHOUT the word PO or "no" or "#" ("4500123",
"4500-123"); one string per number. Do NOT decide who the PO belongs to or what it means
(you do not know the asker's audience): fill po_refs the same way whoever asks, and keep
every other key as you would without it. A message with no PO number: null. Never invent
a number, never take one from an earlier turn.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUB ASKS AND FAMILY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Two more OUTPUT keys, on every object:
  "sub_asks": [{"measure", "group_by", "date_from", "date_to", "basis", "label"}] or null
  "family": true | false | null

== SUB_ASKS ==
When the CURRENT message asks TWO OR MORE different things, list each, in the order asked.
ONE thing asked: null (every other key as usual), even with a period, family or basis word.
The date AND the quantity of one event are ONE thing: "last in date and quantity",
"最后一次进库日期和数量" is ONE ask (null).
Each object:
  measure:
    "received_qty" - how many ALREADY came in over a period: goods received, 入库, 进库,
      进了多少, 收货, "received", "came in", "diterima". Past.
    "last_receipt" - WHEN it last came in: 最后一次进库/入库, "last in", "last received".
    "incoming" - goods NOT yet arrived: on the way, ETA, 到货时间, 在途, "arriving". Future.
      "2026 入库了多少" is received_qty, NEVER incoming.
    "stock" (on hand now) | "supplier" (who supplies it) | "last_cost" (purchase price,
    采购价) | "sold_total" (how many SOLD in all) | "sold_by" (sold per salesman / customer
    / ...).
    "other" - anything else asked (margin, profit, 毛利, lead time): still an ask, label
      says what.
  group_by: sold_by only - "sales_agent" (salesman, 业务员) | "customer" | "product" |
    "brand" | "category" | "location" | "channel" | "month"; else null.
  date_from / date_to: YYYY-MM-DD, the period THIS ask is about ("2026" -> 2026-01-01 /
    2026-12-31). A period named once covers each ask that is about a period
    (received_qty, sold_total, sold_by, incoming). last_receipt, supplier, last_cost, stock
    and other are NEVER given one, even when "2026" is in the message.
  basis: sold_total / sold_by only - "delivered" when the message says delivered / shipped
    / 已出货, "ordered" when it says ordered / 下单; a plain "sold" / "卖了" is null.
  label: the asker's own words for this ask, short.
Examples:
  "X,2026 年 入库了多少数量, 供应商是谁, 采购价是多少钱, 最后一次进库是什么时候, 2026 总共卖了多少个,
  2026年每位业务员共卖了多少个" -> received_qty (2026), supplier, last_cost, last_receipt (no
  period), sold_total (2026, basis null), sold_by (sales_agent, 2026, basis null).
  "Incoming and stock for 7445" -> incoming, stock.
  "SRTWT7445 margin and last cost" -> other (label "margin"), last_cost.
  "SRTWT7445 系列 2026 卖了多少" -> null (one ask), family true.

== FAMILY ==
A code the message names means that EXACT code (false: "only this", "just X", 只要这个) or
every code starting with it (true: "all variants", 全部, 系列), from the message's own words;
not said: null (nearly always).

== REPLYING TO THE VARIANT OR SOLD-BASIS QUESTION ==
Its Open question options have the codes "just this code" / "all variants" or "delivered" /
"ordered". A reply that chooses one, in any words ("只要这个", "just that one", "全部",
"all", "已出货", "按下单", "delivered"), is open_question_answer mode "pick" with that
option's position: never "yes" or "no", never a new question.
$q$)
            WHEN position($q$

== MEMORY ==
$q$ IN v.template) > 0 THEN overlay(v.template PLACING $q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUB ASKS AND FAMILY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Two more OUTPUT keys, on every object:
  "sub_asks": [{"measure", "group_by", "date_from", "date_to", "basis", "label"}] or null
  "family": true | false | null

== SUB_ASKS ==
When the CURRENT message asks TWO OR MORE different things, list each, in the order asked.
ONE thing asked: null (every other key as usual), even with a period, family or basis word.
The date AND the quantity of one event are ONE thing: "last in date and quantity",
"最后一次进库日期和数量" is ONE ask (null).
Each object:
  measure:
    "received_qty" - how many ALREADY came in over a period: goods received, 入库, 进库,
      进了多少, 收货, "received", "came in", "diterima". Past.
    "last_receipt" - WHEN it last came in: 最后一次进库/入库, "last in", "last received".
    "incoming" - goods NOT yet arrived: on the way, ETA, 到货时间, 在途, "arriving". Future.
      "2026 入库了多少" is received_qty, NEVER incoming.
    "stock" (on hand now) | "supplier" (who supplies it) | "last_cost" (purchase price,
    采购价) | "sold_total" (how many SOLD in all) | "sold_by" (sold per salesman / customer
    / ...).
    "other" - anything else asked (margin, profit, 毛利, lead time): still an ask, label
      says what.
  group_by: sold_by only - "sales_agent" (salesman, 业务员) | "customer" | "product" |
    "brand" | "category" | "location" | "channel" | "month"; else null.
  date_from / date_to: YYYY-MM-DD, the period THIS ask is about ("2026" -> 2026-01-01 /
    2026-12-31). A period named once covers each ask that is about a period
    (received_qty, sold_total, sold_by, incoming). last_receipt, supplier, last_cost, stock
    and other are NEVER given one, even when "2026" is in the message.
  basis: sold_total / sold_by only - "delivered" when the message says delivered / shipped
    / 已出货, "ordered" when it says ordered / 下单; a plain "sold" / "卖了" is null.
  label: the asker's own words for this ask, short.
Examples:
  "X,2026 年 入库了多少数量, 供应商是谁, 采购价是多少钱, 最后一次进库是什么时候, 2026 总共卖了多少个,
  2026年每位业务员共卖了多少个" -> received_qty (2026), supplier, last_cost, last_receipt (no
  period), sold_total (2026, basis null), sold_by (sales_agent, 2026, basis null).
  "Incoming and stock for 7445" -> incoming, stock.
  "SRTWT7445 margin and last cost" -> other (label "margin"), last_cost.
  "SRTWT7445 系列 2026 卖了多少" -> null (one ask), family true.

== FAMILY ==
A code the message names means that EXACT code (false: "only this", "just X", 只要这个) or
every code starting with it (true: "all variants", 全部, 系列), from the message's own words;
not said: null (nearly always).

== REPLYING TO THE VARIANT OR SOLD-BASIS QUESTION ==
Its Open question options have the codes "just this code" / "all variants" or "delivered" /
"ordered". A reply that chooses one, in any words ("只要这个", "just that one", "全部",
"all", "已出货", "按下单", "delivered"), is open_question_answer mode "pick" with that
option's position: never "yes" or "no", never a new question.


== MEMORY ==
$q$ FROM position($q$

== MEMORY ==
$q$ IN v.template) FOR length($q$

== MEMORY ==
$q$))
            ELSE overlay(v.template PLACING $q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUB ASKS AND FAMILY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Two more OUTPUT keys, on every object:
  "sub_asks": [{"measure", "group_by", "date_from", "date_to", "basis", "label"}] or null
  "family": true | false | null

== SUB_ASKS ==
When the CURRENT message asks TWO OR MORE different things, list each, in the order asked.
ONE thing asked: null (every other key as usual), even with a period, family or basis word.
The date AND the quantity of one event are ONE thing: "last in date and quantity",
"最后一次进库日期和数量" is ONE ask (null).
Each object:
  measure:
    "received_qty" - how many ALREADY came in over a period: goods received, 入库, 进库,
      进了多少, 收货, "received", "came in", "diterima". Past.
    "last_receipt" - WHEN it last came in: 最后一次进库/入库, "last in", "last received".
    "incoming" - goods NOT yet arrived: on the way, ETA, 到货时间, 在途, "arriving". Future.
      "2026 入库了多少" is received_qty, NEVER incoming.
    "stock" (on hand now) | "supplier" (who supplies it) | "last_cost" (purchase price,
    采购价) | "sold_total" (how many SOLD in all) | "sold_by" (sold per salesman / customer
    / ...).
    "other" - anything else asked (margin, profit, 毛利, lead time): still an ask, label
      says what.
  group_by: sold_by only - "sales_agent" (salesman, 业务员) | "customer" | "product" |
    "brand" | "category" | "location" | "channel" | "month"; else null.
  date_from / date_to: YYYY-MM-DD, the period THIS ask is about ("2026" -> 2026-01-01 /
    2026-12-31). A period named once covers each ask that is about a period
    (received_qty, sold_total, sold_by, incoming). last_receipt, supplier, last_cost, stock
    and other are NEVER given one, even when "2026" is in the message.
  basis: sold_total / sold_by only - "delivered" when the message says delivered / shipped
    / 已出货, "ordered" when it says ordered / 下单; a plain "sold" / "卖了" is null.
  label: the asker's own words for this ask, short.
Examples:
  "X,2026 年 入库了多少数量, 供应商是谁, 采购价是多少钱, 最后一次进库是什么时候, 2026 总共卖了多少个,
  2026年每位业务员共卖了多少个" -> received_qty (2026), supplier, last_cost, last_receipt (no
  period), sold_total (2026, basis null), sold_by (sales_agent, 2026, basis null).
  "Incoming and stock for 7445" -> incoming, stock.
  "SRTWT7445 margin and last cost" -> other (label "margin"), last_cost.
  "SRTWT7445 系列 2026 卖了多少" -> null (one ask), family true.

== FAMILY ==
A code the message names means that EXACT code (false: "only this", "just X", 只要这个) or
every code starting with it (true: "all variants", 全部, 系列), from the message's own words;
not said: null (nearly always).

== REPLYING TO THE VARIANT OR SOLD-BASIS QUESTION ==
Its Open question options have the codes "just this code" / "all variants" or "delivered" /
"ordered". A reply that chooses one, in any words ("只要这个", "just that one", "全部",
"all", "已出货", "按下单", "delivered"), is open_question_answer mode "pick" with that
option's position: never "yes" or "no", never a new question.


<<<CHATBOT POLICY BLOCKS>>>$q$ FROM position($q$

<<<CHATBOT POLICY BLOCKS>>>$q$ IN v.template) FOR length($q$

<<<CHATBOT POLICY BLOCKS>>>$q$))
       END,
       v.variables, '{"wording_edit": true}'::jsonb, $q$MULTI-ASK-PLAN r4: the sub_asks and family keys (one entry per thing a message asks; exact code or family). Unlabelled, promote from the Prompts page.$q$
FROM ai_prompt_versions v JOIN ai_prompt_labels l ON l.version_id = v.id
WHERE l.name = 'chatbot_semantic_parser' AND l.label = 'production'
  AND (position($q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PO REFS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

One more OUTPUT key, on every object:
  "po_refs": ["..."] or null
Fill it whenever the CURRENT message gives a PO number, in any language or mix: "status of
PO 4500123", "PO no 4500123", "our PO 4500123", "my PO is 4500123", "PO#4500123", "PO
4500-123". Put the number as typed WITHOUT the word PO or "no" or "#" ("4500123",
"4500-123"); one string per number. Do NOT decide who the PO belongs to or what it means
(you do not know the asker's audience): fill po_refs the same way whoever asks, and keep
every other key as you would without it. A message with no PO number: null. Never invent
a number, never take one from an earlier turn.
$q$ IN v.template) > 0 OR position($q$

== MEMORY ==
$q$ IN v.template) > 0 OR position($q$

<<<CHATBOT POLICY BLOCKS>>>$q$ IN v.template) > 0)
  AND NOT EXISTS (SELECT 1 FROM ai_prompt_versions x WHERE x.name = 'chatbot_semantic_parser'
                  AND position($q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUB ASKS AND FAMILY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Two more OUTPUT keys, on every object:
  "sub_asks": [{"measure", "group_by", "date_from", "date_to", "basis", "label"}] or null
  "family": true | false | null

== SUB_ASKS ==
When the CURRENT message asks TWO OR MORE different things, list each, in the order asked.
ONE thing asked: null (every other key as usual), even with a period, family or basis word.
The date AND the quantity of one event are ONE thing: "last in date and quantity",
"最后一次进库日期和数量" is ONE ask (null).
Each object:
  measure:
    "received_qty" - how many ALREADY came in over a period: goods received, 入库, 进库,
      进了多少, 收货, "received", "came in", "diterima". Past.
    "last_receipt" - WHEN it last came in: 最后一次进库/入库, "last in", "last received".
    "incoming" - goods NOT yet arrived: on the way, ETA, 到货时间, 在途, "arriving". Future.
      "2026 入库了多少" is received_qty, NEVER incoming.
    "stock" (on hand now) | "supplier" (who supplies it) | "last_cost" (purchase price,
    采购价) | "sold_total" (how many SOLD in all) | "sold_by" (sold per salesman / customer
    / ...).
    "other" - anything else asked (margin, profit, 毛利, lead time): still an ask, label
      says what.
  group_by: sold_by only - "sales_agent" (salesman, 业务员) | "customer" | "product" |
    "brand" | "category" | "location" | "channel" | "month"; else null.
  date_from / date_to: YYYY-MM-DD, the period THIS ask is about ("2026" -> 2026-01-01 /
    2026-12-31). A period named once covers each ask that is about a period
    (received_qty, sold_total, sold_by, incoming). last_receipt, supplier, last_cost, stock
    and other are NEVER given one, even when "2026" is in the message.
  basis: sold_total / sold_by only - "delivered" when the message says delivered / shipped
    / 已出货, "ordered" when it says ordered / 下单; a plain "sold" / "卖了" is null.
  label: the asker's own words for this ask, short.
Examples:
  "X,2026 年 入库了多少数量, 供应商是谁, 采购价是多少钱, 最后一次进库是什么时候, 2026 总共卖了多少个,
  2026年每位业务员共卖了多少个" -> received_qty (2026), supplier, last_cost, last_receipt (no
  period), sold_total (2026, basis null), sold_by (sales_agent, 2026, basis null).
  "Incoming and stock for 7445" -> incoming, stock.
  "SRTWT7445 margin and last cost" -> other (label "margin"), last_cost.
  "SRTWT7445 系列 2026 卖了多少" -> null (one ask), family true.

== FAMILY ==
A code the message names means that EXACT code (false: "only this", "just X", 只要这个) or
every code starting with it (true: "all variants", 全部, 系列), from the message's own words;
not said: null (nearly always).

== REPLYING TO THE VARIANT OR SOLD-BASIS QUESTION ==
Its Open question options have the codes "just this code" / "all variants" or "delivered" /
"ordered". A reply that chooses one, in any words ("只要这个", "just that one", "全部",
"all", "已出货", "按下单", "delivered"), is open_question_answer mode "pick" with that
option's position: never "yes" or "no", never a new question.
$q$ IN x.template) > 0);

DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM ai_prompt_versions x WHERE x.name = 'chatbot_semantic_parser'
                 AND position($q$

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUB ASKS AND FAMILY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Two more OUTPUT keys, on every object:
  "sub_asks": [{"measure", "group_by", "date_from", "date_to", "basis", "label"}] or null
  "family": true | false | null

== SUB_ASKS ==
When the CURRENT message asks TWO OR MORE different things, list each, in the order asked.
ONE thing asked: null (every other key as usual), even with a period, family or basis word.
The date AND the quantity of one event are ONE thing: "last in date and quantity",
"最后一次进库日期和数量" is ONE ask (null).
Each object:
  measure:
    "received_qty" - how many ALREADY came in over a period: goods received, 入库, 进库,
      进了多少, 收货, "received", "came in", "diterima". Past.
    "last_receipt" - WHEN it last came in: 最后一次进库/入库, "last in", "last received".
    "incoming" - goods NOT yet arrived: on the way, ETA, 到货时间, 在途, "arriving". Future.
      "2026 入库了多少" is received_qty, NEVER incoming.
    "stock" (on hand now) | "supplier" (who supplies it) | "last_cost" (purchase price,
    采购价) | "sold_total" (how many SOLD in all) | "sold_by" (sold per salesman / customer
    / ...).
    "other" - anything else asked (margin, profit, 毛利, lead time): still an ask, label
      says what.
  group_by: sold_by only - "sales_agent" (salesman, 业务员) | "customer" | "product" |
    "brand" | "category" | "location" | "channel" | "month"; else null.
  date_from / date_to: YYYY-MM-DD, the period THIS ask is about ("2026" -> 2026-01-01 /
    2026-12-31). A period named once covers each ask that is about a period
    (received_qty, sold_total, sold_by, incoming). last_receipt, supplier, last_cost, stock
    and other are NEVER given one, even when "2026" is in the message.
  basis: sold_total / sold_by only - "delivered" when the message says delivered / shipped
    / 已出货, "ordered" when it says ordered / 下单; a plain "sold" / "卖了" is null.
  label: the asker's own words for this ask, short.
Examples:
  "X,2026 年 入库了多少数量, 供应商是谁, 采购价是多少钱, 最后一次进库是什么时候, 2026 总共卖了多少个,
  2026年每位业务员共卖了多少个" -> received_qty (2026), supplier, last_cost, last_receipt (no
  period), sold_total (2026, basis null), sold_by (sales_agent, 2026, basis null).
  "Incoming and stock for 7445" -> incoming, stock.
  "SRTWT7445 margin and last cost" -> other (label "margin"), last_cost.
  "SRTWT7445 系列 2026 卖了多少" -> null (one ask), family true.

== FAMILY ==
A code the message names means that EXACT code (false: "only this", "just X", 只要这个) or
every code starting with it (true: "all variants", 全部, 系列), from the message's own words;
not said: null (nearly always).

== REPLYING TO THE VARIANT OR SOLD-BASIS QUESTION ==
Its Open question options have the codes "just this code" / "all variants" or "delivered" /
"ordered". A reply that chooses one, in any words ("只要这个", "just that one", "全部",
"all", "已出货", "按下单", "delivered"), is open_question_answer mode "pick" with that
option's position: never "yes" or "no", never a new question.
$q$ IN x.template) > 0) THEN
    RAISE EXCEPTION 'MULTI-ASK-PLAN: no version was published: the production text has none of PO_REFS_ADDENDUM, the MEMORY block or <<<CHATBOT POLICY BLOCKS>>>';
  END IF;
END
$do$;
