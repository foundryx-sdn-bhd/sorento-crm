"""The LIVE n8n semantic-parser system message, verbatim (AC-104).

Lives OUTSIDE `app/services/chatbot/` on purpose. A prompt fallback belongs to the
prompt registry, which owns every other one, and the registry is core - it must not
import the module package (AC-002). The engine never reads this constant either: it
calls `ai_prompt_registry.render`, which resolves the published version and falls back
here, so there is one resolution path and one copy of the text.

This is the fallback for prompt registry key `chatbot_semantic_parser`.

**Which body this is (corrected 5 Sep 2026).** It is the message the LIVE
`sub-semantic-parser` workflow carries, fetched read-only from the n8n instance:
46,942 characters, sha256 `90c0741997...bdf87b66`, vendored in that session's scratchpad
as `sub-semantic-parser.systemMessage.live.txt`. S1 originally took the message from the
working-tree EXPORT instead, which is 49,318 characters and flagged `locally_edited` in
its own MANIFEST: it carries an UNPROMOTED lane change (B-TEAM-1', which adds
`routing.team_source` and a `resource_attachment` routing row among other things). A CRM
turn must parse the way a live n8n turn parses, so the live body is the only correct
source. `tests/chatbot/test_parser_prompt_is_live.py` asserts the sha so this can never
drift back silently.

Three edits, and only three:

* n8n's leading `=` (its "this field is an expression" marker) is dropped - it is n8n
  syntax, not prompt text;
* the one n8n template expression, `{{ $now.toUTC(8*60).format('cccc, dd MMMM yyyy') }}`,
  becomes the registry's own `{{current_date}}` variable, rendered by
  `ai_prompt_registry.render`. Same value, same position, the registry's syntax;
* the 7 Sep 2026 warehouse-arrival vocabulary and its WORKED EXAMPLES paragraph, both
  inside REQUESTED ATTRIBUTES (migration `487_chatbot_warehouse_cue`): the live n8n body
  under-specifies a bare container ask (owner report), so `warehouse_arrival_date` got
  its own cue and the paragraph got two more worked cases. Unlike the first two, this is
  NOT a mechanical transform of the live text - it is new prompt content, published as a
  new UNLABELLED version rather than promoted straight to `production` (same
  immutable-versions-plus-movable-labels split as migration 475). Because it is content
  and not a transform, `test_parser_prompt_is_live.py`'s reproducibility check slices this
  one block out of both sides before comparing, rather than expecting a byte-for-byte
  match across it (see that test's own docstring for why).

Everything else, outside that one block, is byte-identical, INCLUDING the "Companies
OFFERED in the pending offer" line, which is an n8n `{{ (() => ...)() }}` expression the
registry does not evaluate and therefore passes through literally. That is faithful to
the source and is a known defect of the CRM path, not an edit: it is inventoried for the
S1b slim-down.

The user turn is built by `head/parser.build_user_block`, from the live `AI Agent` node's
own two-line template (`sub-semantic-parser.agent-node.live.json`), plus the one S1
addition R3 allows: the persisted `pending` marker.

D16 / S1b slimmed this prompt by moving every deterministic rule into
`output_exchange.py`. That result was a second body, published as a NEW immutable
version with no label by migration `475_chatbot_parser_prompt_slim` (AC-154) - RETIRED
from this module by the chatbot turn re-architecture (S0, AC-1506): the parser schema is
unified to one (v3) shape and no new prompt is published from that slim lineage. The
retired body's text is preserved, byte-identical, in `alembic/_legacy_prompt_bodies.py` -
imported only by the migrations that already published labelled versions of it
(475, 480, 487, 490, 513, 514, 517), never by anything live. This constant
(`SEMANTIC_PARSER_PROMPT` below) stays the key's `fallback()`, version 1, the
`production` label - unaffected by the retirement.

**AMENDED 7 Sep 2026 (owner rules R-a / R-c, console pass 4) - the retired slim body
only**, kept here as history: `routing.suggested_team` carried the customer's OWN team
word verbatim when that word named several catalogue teams or none ("marketing",
"sales"), and was null ONLY when the customer named no team at all. That null is what
the escalation lane needs to tell "escalate to marketing" (ask which marketing team)
apart from "I want to talk to a human" (assign the default) - two messages that were
IDENTICAL at the lane under the old contract, which is why the lane's inheritance
premise fired on both. D11 forbids the lane reading the two messages to tell them apart,
so the discriminator had to be produced in the prompt. The lane's `_catalogue_teams` is
the reader.

The LIVE body below is NOT amended: it is version 1, it carries the `production` label,
and its sha is pinned by `tests/chatbot/test_parser_prompt_is_live.py`.
"""

# sha256 of the LIVE file EXACTLY as fetched, leading `=` included. The test asserts that
# this constant is reproducible from that file by the two MECHANICAL edits and nothing
# more, once the one CONTENT edit (the warehouse-arrival block) is sliced out of both
# sides first - see `test_parser_prompt_is_live.py`.
LIVE_SYSTEM_MESSAGE_SHA256 = "90c074199796b606643782465f5be897a21dfce8398a40d51674c44ebdf87b66"

# Growth r1 (`PLAN-chatbot-growth-r1.md` Slice A, AC-905 / AC-907 / AC-908 / AC-910).
# ADDITIVE ONLY: two new OUTPUT keys (`group_by`, `top_n`) and the vocabulary for three
# answers the tools can now give but the parser had no words for - the SO-outstanding
# bucket, PO placed, and the SPO last receipt. Appended to BOTH prompt bodies rather than
# woven into either, for two reasons: the FULL body is a mechanical derivation of the live
# n8n message (`test_parser_prompt_is_live.py` proves the derivation, and a second
# interleaved content edit would need a second slice-out), and the dialogue lane is
# restructuring the middle of the SLIM body in parallel - a trailing block is the smallest
# thing to rebase.
#
# Published as new UNLABELLED versions of `chatbot_semantic_parser` by migration
# `490_chatbot_parser_growth_r1`, same immutable-versions-plus-movable-labels split as
# 475 / 480 / 487. Nothing reaches a customer until the owner moves the `production` label.
#
# The strict provider schema (`head/parser.PARSE_OUTPUT_JSON_SCHEMA`) is what GUARANTEES
# the two keys arrive; this text is what tells the model when they are not null.
GROWTH_R1_ADDENDUM = "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nTWO MORE OUTPUT KEYS, AND THE SO / PO / SPO VOCABULARY\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nEverything below is IN ADDITION to the OUTPUT object above. Emit these two keys on every\nobject, exactly as if they were listed there:\n\n  \"group_by\": \"customer|transporter|date|product|warehouse|supplier|null\",\n  \"top_n\": an integer, or null\n\n== GROUP_BY ==\nThe axis the user wants the answer BROKEN DOWN by. Set it from the CURRENT message's own\nwords only, in any language; never infer it from context. Default null.\n  - \"by customer\", \"per customer\", \"customer by customer\", \"ikut pelanggan\" -> \"customer\"\n  - \"by transporter\", \"per transporter\", \"by lorry\", \"ikut lori\" -> \"transporter\"\n  - \"by date\", \"per day\", \"day by day\", \"ikut tarikh\" -> \"date\"\n  - \"by product\", \"per product\", \"by item\", \"ikut produk\" -> \"product\"\n  - \"per warehouse\", \"by warehouse\", \"by location\", \"ikut gudang\" -> \"warehouse\"\n  - \"by supplier\", \"per supplier\", \"ikut pembekal\" -> \"supplier\"\nA plain \"list the outstanding DO\" has no axis word at all -> null.\n\n== TOP_N ==\nHow many rows the user asked for, as an integer, from the CURRENT message only, else null.\n  - \"last 3\" -> 3\n  - \"top 5\" -> 5\n  - \"last 2 in\" -> 2\n  - \"最近3次\" -> 3\nA message that names no count -> null. Never carry it over.\n\n== ORDER_STATUS GAINS \"so_outstanding\" ==\n\"so_outstanding\" is a SALES ORDER that is ordered but has NO delivery order yet. It is NOT\nthe same as \"outstanding\", which is a DO already created and not yet delivered.\n  - \"SO outstanding\", \"outstanding SO\", \"outstanding sales order\", \"ordered but no DO\",\n    \"no DO yet\", \"belum DO\", \"belum keluar DO\", \"还没出DO\", \"未出DO\" -> \"so_outstanding\"\n  - \"outstanding DO\", \"not delivered yet\", \"belum hantar\" -> \"outstanding\" (unchanged)\n  - \"outstanding SO\", \"SO outstanding\", \"outstanding sales order\" is order_status \"so_outstanding\" under domain_hint \"order\" / intent_hint \"check_order\", NEVER purchase_order / check_po; check_po needs a PO or supplier word.\nThe full set is now: outstanding|delivered|so_outstanding|do_outstanding|outstanding_both|null. Order domain only, and\nstill only from the current message's explicit words.\n\n== ORDER_STATUS GAINS \"do_outstanding\" AND \"outstanding_both\" ==\nThese two name WHICH document an outstanding ask means, over the SAME SO backlog / DO\npending report as \"so_outstanding\" above - never a new tool, just which block(s) it\nreturns.\n  - \"delivery order outstanding\", \"DO outstanding\", \"outstanding DO\" when paired with\n    the word \"outstanding\" (never a bare \"outstanding DO\" alone, which stays\n    \"outstanding\" - the OLD undelivered-order bucket), \"pending delivery order\",\n    \"pending DO\", \"DO pending\", \"belum DO keluar\" together with an explicit\n    outstanding/pending word -> \"do_outstanding\"\n  - \"both\" / \"both SO and DO\" / \"sales order and delivery order outstanding\" said\n    together with an outstanding/backlog/pending word -> \"outstanding_both\"\n  - A bare \"outstanding\", \"o/s\", \"os\", \"backlog\" with NO document word (no \"sales\n    order\"/\"SO\"/\"delivery order\"/\"DO\"/\"both\") stays order_status \"outstanding\" -\n    unchanged, and it is the CRM (never the parser) that asks which document it means.\nTHE DOCUMENT WORD DECIDES THE SCOPE WHEREVER IT SITS IN THE MESSAGE - beside the\nproduct, after the customer's name, before a location, in the middle of a question.\nA long sentence does not make the word disappear, and the CRM must never ask which\ndocument a message has already named.\n  - \"Srtwc8518-SH dealer delivery order outstanding how many at BRW?\" ->\n    \"do_outstanding\" (product SRTWC8518-SH, warehouse BRW) - NOT bare \"outstanding\"\n  - \"SRTWT7443 sales order outstanding for IB\" -> \"so_outstanding\" (product\n    SRTWT7443, warehouse IB)\n  - \"how much SRTWT7445 outstanding both at BRW-IB\" -> \"outstanding_both\"\n  - \"what is outstanding for SRTWT7445 at BRW\" -> bare \"outstanding\" (no document\n    word anywhere in it, so the CRM asks)\n\n== AN OPEN NUMBERED QUESTION IS ANSWERED WITH reference_positions ==\nWhen the user block says the assistant is waiting for a reply AND lists\n\"Open question options:\", the assistant has just asked a numbered question. If the\ncurrent message ANSWERS it - by number (\"2\"), or by words naming one of those\noptions (\"delivery order\", \"the DO list\", \"sales\", \"both\", \"all\", \"everything\") -\nemit that option's number in reference_positions, and fill every other field as the message itself says:\ndomain_hint, intent_hint, order_status, domain_in_message, entities and message_type\nexactly as you would for the same words with no question open. A pick blanks nothing:\nthe assistant reads the position and the domain fields together and decides where the\nanswer goes.\n  - options \"1. SRTWC286-SH ... 4. SRTWC286-SH-NEW ...\" + \"4\" -> reference_positions [4],\n    domain_in_message false (no domain word)\n  - the same options + \"4 stock\" or \"stock for the 4th\" -> reference_positions [4],\n    domain_hint \"inventory\", intent_hint \"check_stock\", domain_in_message true\n  - the same options + \"check stock\" (no number, no option named) -> domain_hint\n    \"inventory\", intent_hint \"check_stock\", domain_in_message true, reference_positions []\n  - options \"1. Sales orders, 2. Delivery orders, 3. Both\" + \"all\" ->\n    reference_positions [3]\n  - options \"1. Sales order list, 2. Delivery order list\" + \"DO list\" ->\n    reference_positions [2]\nA PARAPHRASE is still an answer. The customer rarely types the label back: \"gimme the\nlist plss\", \"show me\", \"yes list please\", \"the SO one\", \"let me see them\" all name an\noption when a numbered question is open. Pick the option they mean; and when only ONE\noption is offered, any request for \"the list\" is that option.\n  - options \"1. Sales order list\" + \"gimme the list plss\" -> reference_positions [1]\n  - options \"1. Sales order list, 2. Delivery order list\" + \"show me the DO one\" ->\n    reference_positions [2]\n  - options \"1. Sales orders, 2. Delivery orders, 3. Both\" + \"just give me everything\"\n    -> reference_positions [3]\n\"ALL\" WITH NO \"ALL\" OPTION IS EVERY NUMBER. When the message asks for all of them and no\noption offers \"both\" / \"all\" / \"everything\", emit EVERY option's number in\nreference_positions - one answer, every row, not a widening of the search.\n  - options \"1. CHIN CHUN HARDWARE, 2. CHIN CHUN HOMEMART, 3. CHIN CHUN TIMBER, 4. JIMMY\"\n    + \"All\" -> reference_positions [1,2,3,4]\nA message that asks something NEW is NOT an answer to that question, however much it\nsounds like one: a product code or a customer name that is NOT one of the options, an\norder number, or any other topic is a fresh business_query - emit its entities and its domain_hint as usual and\nleave reference_positions EMPTY.\n  - options \"1. Sales order list, 2. Delivery order list\" + \"delivery to hanlim\" ->\n    entities [hanlim as customer], reference_positions []\nA CODE OR NAME FROM THE LIST IS ITS POSITION. When the message types one of the offered\noptions back (its code, or its name as listed), emit that option's number in\nreference_positions AND the code or name in entities as usual: the typed option is both\nthe entity the message names and the position it picks. The domain fields follow the\nmessage's own words, as above.\n  - options \"1. A, 2. B\" + \"B\" -> reference_positions [2], entities [B]\n  - options \"1. SRTWC286-SH ... 4. SRTWC286-SH-NEW ...\" + \"stoick SRTWC286-SH-NEW\" ->\n    reference_positions [4], entities [SRTWC286-SH-NEW], domain_hint \"inventory\",\n    domain_in_message true (\"stoick\" is \"stock\", read for meaning)\n\n== IN AN ORDER OR OUTSTANDING ASK, A SHORT TOKEN IS A LOCATION ==\nUnder domain_hint \"order\" (a DO / SO / outstanding question), a SHORT token that is not a\nproduct code and that follows \"for\", \"at\", \"in\", or stands alone beside the product, is a\nWAREHOUSE word. Emit it as an entity with hint \"warehouse\" - NEVER \"customer\".\nSHORT means one of exactly two shapes, and nothing else counts:\n  (a) at most 4 characters, letters or letters with one digit: \"IB\", \"BB\", \"IR\", \"ACTS\",\n      \"SMC\", \"NTC\", \"RSV\", \"DFCT\", \"BILL\", \"BRW\", \"MWH\", \"RSW\", \"DC1\", \"WH3\"\n  (b) a hyphenated site code of at most 10 characters: \"BRW-IB\", \"MWH-IB\", \"BRW-ACTS\"\nExamples:\n  - \"SRTWT7443 sales order outstanding for IB\" -> SRTWT7443 is the product, \"IB\" is a\n    warehouse entity\n  - \"any DO for SRTWT7445 at BRW\", \"outstanding in BRW-IB\", \"o/s at MWH\", \"backlog BB\"\n    -> \"BRW\", \"BRW-IB\", \"MWH\", \"BB\" are warehouse entities\nAnything LONGER than that is a CUSTOMER name, however it is capitalised: \"FULLSHUN\",\n\"IBORNGROUP\", \"Hing Seng\", \"FULLSHUN SANITARYWARE\" are customer entities, never\nwarehouses. A product code carries digits (\"SRTWC8517\", \"MSK11A-QT\") and is neither.\n\n== PURCHASE ORDERS: domain_hint \"purchase_order\", intent_hint \"check_po\" ==\nA question about what WE ordered from a SUPPLIER - never about what a customer ordered\nfrom us.\n  - \"PO\", \"PO placed\", \"purchase order\", \"PO for this item\", \"ordered from supplier\",\n    \"supplier order\", \"采购单\", \"已下单\" -> domain_hint \"purchase_order\",\n    intent_hint \"check_po\"\n  - \"by supplier\" / \"by product\" on such a question sets group_by as well.\n\"DO\", \"SO\", \"delivery order\", \"sales order\" and a customer's name are NEVER\npurchase_order: those stay domain_hint \"order\", intent_hint \"check_order\".\n\"PO\" or \"purchase order\" ALONE, with or without a product, is purchase_order. It is NEVER\npurchase_cost: only the words of COST (\"cost\", \"last purchase cost\", \"how much did we pay\")\nmake a message purchase_cost.\n  - \"stock, incoming and PO\" -> asks [{\"domain\":\"inventory\",\"intent\":\"check_stock\"},\n    {\"domain\":\"incoming\",\"intent\":\"check_incoming\"},\n    {\"domain\":\"purchase_order\",\"intent\":\"check_po\"}], domain_hint \"inventory\" - the FIRST\n    of the asks, never the last one and never purchase_cost.\n\n== SPO LAST RECEIPT: intent_hint \"check_spo\", domain_hint \"spo_allocation\" ==\n\"when did this last come in, and how much\" is a receiving question, not a stock question.\n  - \"last in\", \"last incoming qty\", \"last received\", \"when did it last come in\",\n    \"上次进货\", \"terakhir masuk\" -> intent_hint \"check_spo\",\n    domain_hint \"spo_allocation\"\n  - \"last 3 in\" sets top_n = 3 as well.\n\"how much stock do we have\" is still check_stock / inventory; \"when is it arriving\" is\nstill check_incoming / incoming.\n\n\n== A BARE \"SPEC\" ASKS FOR ALL OF THEM ==\nOrder matters here. Ask FIRST: does the message name a PARTICULAR property (steel grade,\nmaterial, finish, width, length, height, wattage, capacity, colour, mounting, ...)?\n  - YES -> requested_attributes is that property, exactly as before. \"steel grade of\n    SRTKT73SS\" -> [\"steel_grade\"]. \"SRTKT73SS dimensions\" -> the dimension keys. This rule\n    changes NOTHING about a named property, and a named property always wins.\n  - NO, and the only attribute word is \"spec\" / \"specs\" / \"specification\" /\n    \"specifications\" / \"规格\" -> requested_attributes is EMPTY []. That is a request for the\n    WHOLE spec list, not for a property called \"spec\".\n    \"SRTKT73SS spec\", \"spec for SRTKT73SS\", \"specification SRTKT73SS\" -> []\nBoth are domain_hint \"master_products\", intent_hint \"check_product\".\n\n== IN A PO ASK, THE CODE IS THE PRODUCT ==\n\"PO for SRTWC8517\", \"PO SRTWC8517\", \"purchase order of SRTWC8517\": the token after \"for\" /\n\"of\", or a bare code beside the PO word, is the PRODUCT. Emit it as an entity with\nhint \"product\" - NEVER \"order\", \"order_number\" or \"customer_order\".\nA PO / SO NUMBER on this system looks like \"202602-S0002\" (digits, a hyphen, then a\nletter); only a token of THAT shape is an order entity. A product code starts with\nletters (\"SRTWC8517\", \"C-FH14\", \"MSK11A-QT\").\nThe same holds for the SPO / last-in ask: \"last in for SRTWC8517\" names a PRODUCT.\n\n== A NEW QUESTION IS NEVER A \"YES\" ==\nWhen the previous reply offered to escalate (\"Would you like me to escalate to ... team?\")\nand the current message ASKS SOMETHING - a product, stock, a PO, an SO, a last-in, with or\nwithout a code - it is a business_query. Set escalation.is_escalation_confirmation = FALSE\nand emit the entity the customer named THIS turn.\n  - \"PO for SRTWC8517\" after an escalate offer -> business_query, check_po,\n    purchase_order, entities [SRTWC8517 as product, current_message true],\n    is_escalation_confirmation false\n  - \"yes\", \"yes escalate\", \"ok please escalate\" -> THAT is the confirmation\nOnly a message that answers the offer is a confirmation. A message that ignores it and\nasks something else leaves the offer alone.\n\n== A DELIVERY WORD PLUS A NAME IS AN ORDER ASK ==\nrequest_for_help is ONLY a request for a HUMAN. A message that names a customer or product next to a delivery / order / DO word (\"delivery to hanlim\", \"hantar ke hanlim\", \"any DO for X\") is business_query, intent_hint check_order, domain_hint order, entity = that name with current_message true, is_escalation_confirmation false, even when an escalate offer is open.\n\n== THE ATTRIBUTE PHRASE, WHOLE ==\nFor requested_attributes ONLY: emit the customer's own attribute phrase, WHOLE (\"seat cover material\", \"list price\"), never a shortened token; a base property (price, dimensions, description, name) is a requested attribute too. This rule never reaches an entity's canonical_code.\n"

# PLAN-chatbot-last-purchase-cost.md, item 9. ADDITIVE ONLY: the vocabulary for one new
# answer the tools can now give but the parser had no words for - the last purchase cost
# per product per location. Appended to BOTH prompt bodies as its own block, the same
# reason GROWTH_R1_ADDENDUM is one: the FULL body is a mechanical derivation of the live
# n8n message and a second interleaved content edit would need a second slice-out.
#
# Published as a new UNLABELLED version of `chatbot_semantic_parser` by migration
# `513_chatbot_parser_last_cost`, same immutable-versions-plus-movable-labels split as
# 475 / 480 / 487 / 490. Nothing reaches a customer until the owner moves the
# `production` label.
LAST_COST_ADDENDUM = "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nLAST PURCHASE COST\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n== LAST PURCHASE COST: intent_hint \"check_po_cost\", domain_hint \"purchase_cost\" ==\n\"what did we pay a supplier for this, last time\" is a COST question, never a\nselling-price question - it is what WE PAID, not what WE CHARGE.\n  - \"last purchase cost\", \"last cost\", \"what did we pay\", \"what did we pay for\",\n    \"buying price\", \"purchase price\", \"cost price\", \"上次采购价\", \"成本\",\n    \"harga belian terakhir\", \"harga beli\" -> intent_hint \"check_po_cost\",\n    domain_hint \"purchase_cost\"\n  - \"last 3 purchase cost\" sets top_n = 3 as well; \"by warehouse\" / \"by location\" sets\n    group_by \"warehouse\".\n\"how much do we sell it for\", \"selling price\", \"what do we charge\", \"how much does it cost\", \"berapa harga\", \"多少钱\" are NEVER purchase_cost: those stay domain_hint\n\"master_products\" (list price), \"promotion\" (a promo price), or plain check_stock,\nexactly as PRICE TERMINOLOGY already states - \"cost\" alone is the everyday word for\nthe SELLING price, not a signal for this domain.\nThe same code-is-the-product rule as PO / SPO applies: \"last purchase cost for\nSRTWC8517\" names a PRODUCT, never an order.\n"

# PLAN-low-stock-report.md, S7 (AC-70). ADDITIVE ONLY, and appended for the same reason
# `LAST_COST_ADDENDUM` above is: the FULL body is a mechanical derivation of the live n8n
# message, and a trailing block is the only edit shape that keeps that derivation true.
# The words alone are new - the intent (`low_stock_report`) is declared on the inventory
# row of `contracts.DOMAIN_SPEC`, and the lane refuses the ask outright for a contact
# without the `scm.low_stock_report` reveal key.
LOW_STOCK_ADDENDUM = '\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nLOW STOCK REPORT\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n== LOW STOCK REPORT: intent_hint "low_stock_report", domain_hint "inventory" ==\n"which items are below their reorder level" is a REPORT the buyer runs, not a stock\nbalance question about one product.\n  - "low stock report", "low stock", "reorder report", "stock below level",\n    "what needs reordering", "laporan stok rendah", "低库存报表" ->\n    intent_hint "low_stock_report", domain_hint "inventory"\n  - A LOCATION word in the same message ("low stock report BRW", "low stock for IB")\n    is a warehouse entity - hint "warehouse" - never a product, even though a bare\n    token under this domain usually is one.\n  - A PRODUCT CODE ("low stock for SRTWT7408") is a product entity - hint "product".\n  - A DATE PHRASE ("until 30 Nov", "from September") is the plan\'s horizon: fill\n    date_filter_start / date_filter_end, exactly as any other date window.\n"how many CB100 in BRW" is NOT this intent - that is plain check_stock. This one is\nasked about the BOOK ("what is low", "what should I order"), never about one item\'s\nquantity.\n'


# PLAN-chatbot-sales-report.md, S4 wiring point 1 (captain ruling 5). ADDITIVE
# ONLY, appended for the same reason `LOW_STOCK_ADDENDUM` above is: the FULL body
# is a mechanical derivation of the live n8n message, and a trailing block is the
# only edit shape that keeps that derivation true. Teaches `order_status:
# "sales_report"` and the new nullable `sales_channel` output key.
#
# Published as a new UNLABELLED version of `chatbot_semantic_parser` by migration
# `519_chatbot_sales_report_vocab`, same immutable-versions-plus-movable-labels
# split as 475 / 480 / 487 / 490 / 513 / 514 / 517. Nothing reaches a customer
# until the owner moves the `production` label.
SALES_REPORT_ADDENDUM = '\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nSALES REPORT\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nEverything below is IN ADDITION to the OUTPUT object above. Emit this key on every\nobject, exactly as if it were listed there:\n\n  "sales_channel": "dealer|project|null"\n\n== SALES REPORT: order_status "sales_report", domain_hint "order" ==\n"sales report", "sales performance", "how much did X buy", "how much did X order",\n"sales figures for X" is a REPORT of confirmed vs outstanding sales by month - a\nDIFFERENT ask from "outstanding" (SO/DO backlog) above, even naming the same\ncustomer or product.\n  - "sales report", "sales performance", "how much did X buy", "how much did X\n    order", "sales figures", "jualan report", "laporan jualan", "销售报告" ->\n    order_status "sales_report"\nA SALES REPORT ask carries domain_hint "order" and intent_hint "check_order", ALWAYS -\nthe SAME pairing every other order_status value already uses, never "master_products"\nor "inventory", even when the ONLY entity in the sentence is a product code and the\nONLY requested_attributes entry is "quantity" (that pairing belongs to domain_hint\n"order" in the REQUESTED_ATTRIBUTES table above - "master_products" has no "quantity"\nrow at all). A product code by itself names the SUBJECT of the report, never a\nproduct-master lookup - the report words ("sales", "sale quantity", "total sales",\n"sales performance", "how much did X buy/sell") are what decide the domain, exactly as\nthey decide order_status just above.\n  - "SRTWC286 total sale quantity in November" -> domain_hint "order", intent_hint\n    "check_order", order_status "sales_report", requested_attributes ["quantity"],\n    entities [SRTWC286 as product]\n  - "how much did KIM HUAT sell this year" -> domain_hint "order", intent_hint\n    "check_order", order_status "sales_report", entities [KIM HUAT as customer]\n  - "project SRTKT39SS sales performance" -> domain_hint "order", intent_hint\n    "check_order", order_status "sales_report", sales_channel "project", entities\n    [SRTKT39SS as product]\n\n== SALES_CHANNEL: a NEW nullable output key, "dealer" | "project" | null ==\nEmitted ONLY when order_status is "sales_report" this turn; every other ask leaves\nit null, always - never infer it from context.\n  - "dealer sales report", "dealer sales", "dealer sales performance" ->\n    sales_channel "dealer"\n  - "project sales report", "project sales" -> sales_channel "project"\n  - a sales report naming neither word -> sales_channel null (every channel counts)\nTHE WORD "dealer" INSIDE AN OUTSTANDING ASK IS NEVER A CHANNEL - it is not this\nfield at all there. "outstanding dealer quantity for hanlim" keeps order_status\n"outstanding" (or whichever outstanding bucket the rest of the message names) and\nsales_channel stays null; "dealer" in that sentence names nothing here.\n"dealer" / "project" in a SALES REPORT ask is the CHANNEL, never a signal that turns\nthe NEXT token into a customer - the word answers WHICH CHANNEL, and whatever follows it\nkeeps its own ordinary hint, decided the same way it would be anywhere else in the message.\nA PRODUCT-CODE-SHAPED token (letters mixed with digits, e.g. "SRT5674-N", "SRTWC286") is\nhinted "product" WHEREVER it sits in the sentence, even directly after "dealer"/"project" -\nnever "customer" merely because of where it sits.\n  - "dealer Srt5674-N August total sale quantity" -> sales_channel "dealer", entity {raw:\n    "Srt5674-N", hint: "product"} (a product-code shape, never a customer, however close it\n    sits to "dealer"), date_filter_start/date_filter_end set to August this year (DATE\n    FILTER above governs this exactly as anywhere else in the message - see the rule\n    right below: the channel word and the product code beside "August" never absorb it)\n  - "dealer sales report for hanlim" -> sales_channel "dealer", entity {raw: "hanlim", hint:\n    "customer"} (a COMPANY NAME after "dealer" is still a customer - the product-code-shape\n    rule above never applies to a name)\n  - "project SRTWC286 sales this year" -> sales_channel "project", entity {raw: "SRTWC286",\n    hint: "product"}\n\n== A BARE MONTH NAME IS STILL A DATE FILTER, EVEN BESIDE A CHANNEL WORD AND A CODE ==\nDATE FILTER above is domain-independent and reads the CURRENT message only, with no\nexception for a sales report ask: a bare month name ANYWHERE in the sentence sets\ndate_filter_start/date_filter_end to that month (the current year unless the message\nnames one), exactly as DATE FILTER already rules. A channel word ("dealer"/"project")\nand a product code or customer name sitting beside that month are SEPARATE fields -\nextracting one never means skipping the other, and neither one absorbs the month into\nnothing.\n  - "project SRTWC912 November total sale quantity" -> sales_channel "project", entity\n    {raw: "SRTWC912", hint: "product"}, date_filter_start "2026-11-01", date_filter_end\n    "2026-11-30"\n  - "dealer sales report for KIM HUAT March" -> sales_channel "dealer", entity {raw:\n    "KIM HUAT", hint: "customer"}, date_filter_start "2026-03-01", date_filter_end\n    "2026-03-31"\n  - "SRTKT40AA sale quantity in June" -> sales_channel null, entity {raw: "SRTKT40AA",\n    hint: "product"}, date_filter_start "2026-06-01", date_filter_end "2026-06-30"\n\n== A NARROWING REPLY UNDER AN OPEN SALES REPORT / OUTSTANDING DETAIL OFFER ==\nWhen "Pending" states the assistant is waiting for a sales_report_detail or\noutstanding_detail reply, read the CURRENT message against what is already on the\ncustomer\'s screen. A message that only NARROWS that screen - a product code, a\nlocation word, a month or date range, a channel word, typically carried by "only",\n"just", or "for" - and asks no question of its own is a REFINEMENT, not a new ask:\nmessage_type stays "business_query" (or "casual" for a bare date phrase, exactly as\nDATE FILTER already has it), domain_hint is null, order_status is null, and the\nnarrowing value goes into entities, or into date_filter_start/date_filter_end, or\ninto sales_channel, exactly as it would for any other turn - never\nreference_positions.\n  - "just SRTWC286" -> business_query, domain_hint null, entities [SRTWC286 as\n    product]\n  - "only BB warehouse" -> business_query, domain_hint null, entities [BB as\n    warehouse]\n  - "for project only" -> business_query, domain_hint null, sales_channel\n    "project"\n  - "august 2026 only" -> casual, domain_hint null, date_filter_start/\n    date_filter_end set to August 2026\nA message that asks its OWN question is NEVER a refinement, however much it names a\nvalue already on the narrower screen - it is a new ask and keeps its own domain:\n"stock for SRTWC286" -> business_query, domain_hint "inventory"; "delivery status\nfor total home" -> business_query, domain_hint "order"; "price of SRTWC286" ->\nbusiness_query, domain_hint "master_products". The test is whether the message\nASKS something, not whether it names a value: naming a value with no question of\nits own narrows the report already on screen; naming that SAME value inside a\nquestion starts a different one.\n'
ACCOUNT_LEDGER_ADDENDUM = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CUSTOMER ACCOUNT NUMBER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Every entity object carries one more key, exactly as if it were listed there:

  "account": an integer, or null

== ACCOUNT: which numbered account of a customer the message names ==
A customer can have several accounts, numbered 1, 2, 3 ... Set "account" on the CUSTOMER
entity when the CURRENT message names an account number for it, in any spelling or
language. Roman numerals become integers.
  - "account 1", "acc 1", "a/c 1", "A/C I", "ac1", "akaun 1", "户口1", "第一个户口" -> 1
  - "account 2", "acc2", "A/C II", "a/c ii", "akaun 2" -> 2
  - "A/C III" -> 3, "A/C IV" -> 4
The account words are NOT part of the name: leave them out of raw and canonical_code.
  - "Soon Heng account 1 outstanding" -> entities [{"raw": "Soon Heng", "hint":
    "customer", "account": 1}]
  - "hanlim acc 2 sales" -> entities [{"raw": "hanlim", "hint": "customer", "account": 2}]
  - "Hanlim A/C II and 1 Living A/C I" -> two customer entities, account 2 and account 1
An account number with NO customer name in the current message ("account 2 outstanding")
-> emit ONE entity {"raw": null, "hint": "customer", "account": 2, "current_message":
true}. Never copy a customer name from earlier in the conversation to fill it.
"my account" with no number names no account: "account" stays null and self_reference
is true as usual. "my account 2" -> self_reference true AND the entity {"raw": null,
"hint": "customer", "account": 2}.
Every non-customer entity, and every customer entity without an account number, has
"account": null. Never guess a number.
"""

# LOWSTOCK-SEMANTIC (owner, 4 Oct 2026: "remove the rules entirely, this is hard coded"):
# the low stock report's filters, read HERE and nowhere else. The lane used to read the
# grouping out of the message with regexes, re-label a product word without digits as a
# category and offer the leftover words to the supplier list
# (`documentation/plans/chatbot/lowstock-semantic-behaviour-card.md`); all of that is gone,
# and `lanes/business/low_stock_ask.py` only resolves what this key carries against the
# master data. ADDITIVE ONLY, beneath ACCOUNT_LEDGER_ADDENDUM (MEMORY_ADDENDUM stays the
# tail), published as a new UNLABELLED version by migration `lss_0001_parser_vocab`.
LOW_STOCK_FILTERS_ADDENDUM = """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOW STOCK REPORT FILTERS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

One more OUTPUT key, on every object:
  "low_stock": {"group_by": "supplier|category|supplier_category|brand|warehouse|none"|null,
    "categories": [], "all_categories": true|null, "brands": [], "suppliers": []}
Fill it ONLY when intent_hint is "low_stock_report"; on every other message group_by null,
all_categories null and the three lists []. Read the CURRENT message only, in any language
or mix (English, Malay, Chinese, Manglish), never a word from an earlier turn. The top-level
"group_by" stays null on a low stock ask.
  - categories: each KIND of product named ("water tap", "tap", "water closet", "basin
    mixer", "kitchen sink", "shower", or a category code like "SRT-FT"), in English, without
    the brand and without "only", "for", "category". Translate: "paip air", "kepala paip",
    "水龙头" -> "water tap"; "tandas", "mangkuk tandas", "马桶" -> "water closet"; "sinki dapur",
    "厨房水槽" -> "kitchen sink". A number inside a category name keeps it a category. A
    product TYPE is never a product entity.
  - all_categories: true for every category ("all", "all categories", "everything",
    "semua", "全部"); else null.
  - brands: brand words ("sorento", "mocha", "cabana", or one from Known brands), as written.
  - suppliers: a supplier, vendor or factory NAME to filter by ("from jinbaichuan",
    "taiyang only", "pembekal X", "供应商 X"), as written. The word "supplier" or "vendor"
    alone is never a name.
  - group_by: how the report is split into sections. "by supplier", "per vendor", "group
    them by supplier", "split by supplier", "supplier-wise", "ikut pembekal", "按供应商" ->
    "supplier"; "by category", "per type", "ikut kategori", "按类别" -> "category"; supplier
    and category together -> "supplier_category"; "by brand", "split by brand", "ikut
    jenama", "按品牌" -> "brand"; "by warehouse", "per location", "ikut gudang" ->
    "warehouse"; "no grouping", "one list" -> "none"; nothing said -> null. A supplier NAME
    is a filter, never a grouping.
A word placed in low_stock is NEVER also an entity. Entities on a low stock ask are only a
SPECIFIC product (a code like "SRTWT7408") {hint: "product"} and a location {hint:
"warehouse"}.
  - "low stock report for sorento water tap" -> categories ["water tap"], brands ["sorento"],
    entities []
  - "low stock water closet, group them by supplier" -> categories ["water closet"],
    group_by "supplier"
  - "stok rendah paip air ikut pembekal" -> categories ["water tap"], group_by "supplier"
  - "低库存 马桶 per vendor" -> categories ["water closet"], group_by "supplier"
  - "low stock report water closet taiyang" -> categories ["water closet"], suppliers
    ["taiyang"]
REFINING THE REPORT JUST SHOWN: when "Previous response" starts "Low stock report (", a
message that only names a filter or a grouping ("taiyang only", "by supplier", "sorento
only", "just water closet", "ikut kategori") is the same ask: intent_hint
"low_stock_report", domain_hint "inventory", domain_in_message false, and only the
low_stock fields it names. A message naming the report itself ("low stock report",
"low stock for basin") is a new ask: domain_in_message true.
ANSWERING THE BOT'S LOW STOCK QUESTION: an Open question with "about": "low_stock_report"
asks for the filter named in "owed". A message that answers it: intent_hint
"low_stock_report", domain_hint "inventory", message_type "business_query", the low_stock
fields it names, and open_question_answer mode "fill" (a category, supplier or grouping
named), "pick" with picked (a number or an option named), "all" ("all", "any", "semua",
"全部") or "cancel" ("cancel", "never mind", "batal"). "water tap by supplier" fills both
fields. A message that asks something else ("stock for CB100", "my outstanding") is mode
null, read as usual; "clear", "start over", "reset" -> mode null, topic_reset true.
"""

MEMORY_ADDENDUM = '\n\n== MEMORY ==\nTwo more OUTPUT keys: "message_type" gains "history_question"; "profile_statements": up to 3 {"key","value"} items, or null.\nThe user block may carry "About this contact", "Recent conversations" and "Earlier in this conversation" (oldest first). Use ONLY to resolve a reference ("that one", "the usual", "same as last time"); never override a code, customer or domain the CURRENT message names.\n"history_question": the dealer\'s OWN PAST with the bot, in any wording: "what did I ask", "what do I normally ask about", "what products do I usually ask about", "what did I check last week", "apa saya tanya tadi", "我之前问过什么"; never "clarification"; domain_hint, intent_hint null and entities [] (the past ask is not a live one). A bare number answering a numbered list in the Previous response re-runs that line: its domain and codes (current_message false). A discount, credit or price-exception request: "request_for_help", intent_hint "commercial_request". "profile_statements": up to 3 things the dealer states about THEMSELVES - key language|role|usual_brands|usual_sites|project|about, their own words.\n\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nCURRENT DATE\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nCURRENT DATE: {{current_date}}\n\nIf relative dates such as "today" or "yesterday" appear in the current turn input, convert them to absolute dates before calling the MCP tool.'

# #1262 slice 5 (F4), owner ruling 1: "i don't want hard code, the parser supposed
# to be able to identify the quantity right?" - ADDITIVE ONLY, appended for the same
# reason `SALES_REPORT_ADDENDUM` above is. Teaches the new per-entity `quantity` key
# and the shape a customer's quantity actually takes - never a regex the CODE
# strips, the parser's own read.
#
# Published as a new UNLABELLED version of `chatbot_semantic_parser` by a migration
# following 475 / 480 / 487 / 490 / 513 / 514 / 517 / 519 / 520 / 521's own split.
# Nothing reaches a customer until the owner moves the `production` label.
QUANTITY_ADDENDUM = '\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nQUANTITY\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nEverything below is IN ADDITION to the OUTPUT object above. Emit this key on every\nentity, exactly as if it were listed there:\n\n  "quantity": a number, or null\n\n== QUANTITY: entities[].quantity ==\nA leading or trailing count beside a product code - "x3", "X5", "3 pcs", "5 units",\n"3x" - is that PRODUCT\'s own quantity, emitted in "quantity". It is NEVER part of\nthat entity\'s "raw" or "canonical_code" - strip it out of raw before emitting; the\ncode alone is the raw, whichever side of it the count sits on.\n  - "M210-GM x5" -> entity {raw: "M210-GM", hint: "product", quantity: 5}\n  - "X3 SRTBF 11502" -> entity {raw: "SRTBF 11502", hint: "product", quantity: 3}\n  - "3 pcs SRTWC286", "SRTWC286 3 units" -> entity {raw: "SRTWC286", hint: "product",\n    quantity: 3}\nA CAPTION LINE that is ONLY a quantity, with no product code of its own (a photo\ncaptioned just "X5", the code(s) named separately in the photo\'s own body text),\nnames that quantity for the product(s) the photo names - emit it on each of those\nentities, never as an entity of its own.\nA message or caption naming no quantity at all leaves "quantity" null, for every\nentity - never guessed, never carried over from an earlier turn, never assumed to\nbe 1.\n'

# #1262 slice 9 (F1a), round 3 section 6 step 3: replaces the ENTITY OPERATIONS
# list's own hard-coded "brand -> Sorento, Mocha, or Cabana" (that bullet is
# edited IN PLACE in the body above, not left standing beside this addendum -
# AC-S9-2's own test asserts the fixed phrase is gone from the published
# template, which an addendum stacked outside the body cannot make true). This
# addendum is the ADDITIVE half: it teaches the model to read the "Known
# brands:" line the user block now carries (`build_user_block`'s own new
# parameter), fresh every turn, rather than a name baked into training.
#
# Published as a new UNLABELLED version of `chatbot_semantic_parser` by a
# migration on top of `chatbot_quantity_vocab`, same split as every addendum
# before it. Nothing reaches a customer until the owner moves the `production`
# label.
KNOWN_BRANDS_ADDENDUM = '\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nKNOWN BRANDS\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n== KNOWN BRANDS: entities[].hint "brand" ==\nThe user block carries a "Known brands:" line, built fresh every turn from the live\ncatalogue - the ONLY valid brand names are the ones it lists THIS turn, never a name\nfrom earlier training and never carried over from an earlier turn. A token matching\none of those names, or the code in parentheses beside it, is a brand entity: "raw" is\nthe word the customer typed, "canonical_code" is the brand name exactly AS LISTED on\nthe Known brands line - never the parenthesised code, never the customer\'s own\nspelling or case.\n  - Known brands: Sorento (SRT), Mocha (MCH) - "brand Sorento" -> entity {raw:\n    "Sorento", hint: "brand", canonical_code: "Sorento"}\n  - Same line, "MCH stock" -> entity {raw: "MCH", hint: "brand", canonical_code:\n    "Mocha"}\nA word that is NOT on the Known brands line is never hinted "brand" - read it as\nwhatever it would otherwise be (a product code, a customer name, a description).\nNo "Known brands:" line at all this turn (nothing active) means no brand entity can\nbe named at all - fall back to whatever other hint the word would otherwise carry.\n'


# Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not merged, owner ruling
# 24 Sep 2026) for chatbot-stock-ask-v2 S3 (D13, D15, D16, D21): the two keys the
# per-product quantity loop needs, and the OPEN TASK the user block now states as a
# fact. `entities[].quantity` is what makes "MWT5727SS-CR 5, MHS1028 60" answerable at
# all - `demand_qty` is ONE number for the whole turn, so a multi-product ask could
# never be judged against the dealer's own quantities, and an `availability` contact was
# asked "How many units do you need?" on every turn and never answered. `proceed_anyway`
# is generic: "go ahead with what you have" closes ANY open task, not only a stock check.
STOCK_TASK_ADDENDUM = """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THE OPEN TASK, PER-PRODUCT QUANTITIES, AND PROCEEDING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Everything below is IN ADDITION to the OUTPUT object above. Emit this key on every
object, exactly as if it were listed there, and emit "quantity" on every entity:

  "proceed_anyway": true|false|null
  entities[].quantity: a number, or null

== entities[].quantity ==
The quantity the message states FOR THAT ENTITY, as a number, else null. A message may
state one per product, and each belongs to the product it is written beside.
  - "stock for MWT5727SS-CR 5, MHS1028 60, MSK11A-QT and ABC123?" -> MWT5727SS-CR
    quantity 5, MHS1028 quantity 60, MSK11A-QT quantity null, ABC123 quantity null
  - "MSK11A-QT 110, ABC123 20" -> quantity 110 and 20 on those two entities
  - "make MHS1028 80", "change MHS1028 to 80", "tukar MHS1028 jadi 80" -> MHS1028
    quantity 80
  - "I need 50" with no product named -> entities empty, demand_qty 50
demand_qty stays what it always was: the ONE quantity a message states with no product
of its own beside it. A message that states a quantity per product fills
entities[].quantity and leaves demand_qty null.

== AN OPEN TASK IS A FACT, AND THE MESSAGE MAY BE ABOUT IT ==
When the user block carries a line beginning "Open task:", the assistant is still
collecting something - a stock check waiting on a quantity per product. That line
names what is noted and what is still needed.
  - A quantity given for a product the open task names sets entities[].quantity for
    that product, WHATEVER the current subject is. "MSK11A-QT 110" typed two turns
    after a promotion question is still that product's quantity.
  - "add SRTWC8517", "also check SRTWC8517" -> that product as a new entity, normally.
  - "drop ABC123", "skip ABC123", "forget ABC123" -> entity_op "modify" with ABC123 as
    the entity, exactly as any other removal.
  - "make MHS1028 80" -> that product with quantity 80; it REPLACES the earlier one.
  - "just proceed", "go ahead", "check what you have", "teruskan" -> proceed_anyway
    true, entities empty.
  - "never mind the stock check", "forget the stock check" -> topic_reset true with
    domain_hint "inventory". A "never mind" aimed at something ELSE keeps its own
    domain.
  - "back to the stock check", "what were we checking" -> domain_hint "inventory",
    intent_hint "check_stock", entities empty, no quantity.
proceed_anyway is null on every turn that does not say it. It is never inferred from a
short reply, from silence, or from a number.

== A LINE BEGINNING "Last answered:" ==
When the user block carries a line like "Last answered: SRTGV332-DIY x 20.", the
assistant has just answered that stock check. A message that only states a NEW
quantity for it is the SAME product at the new quantity: entities empty, demand_qty
the new number, correction true, domain_hint "inventory". The number is a quantity,
never a product code.
  - "how about 100?", "what about 100", "100 instead", "and if 100?", "kalau 100?",
    "100 pula?" -> entities [], demand_qty 100, correction true
  - "how about SRTKT1631SS?" names a product of its own -> that product as an entity,
    exactly as any new stock question
  - A bare number on its own ("2", "5 pcs", "make it 2") is that new quantity too:
    entities [], demand_qty 2, reference_positions []. It is NEVER a position on a
    list an earlier reply printed: the stock check is the question being answered, so
    the number is its quantity, even while that list stays on screen for a later pick.
  - Another product is a new stock question: "check stock SRTWC286" or a code of
    its own, read exactly as any new stock question.

== THE OPEN QUESTION AND open_question_answer ==
A line "Open question: {...}" is the ONE question the assistant is waiting on, as an
object: "kind", "options" (position, code, and label when it differs) or "items" (the
stock question's numbered lines: position, code, qty; qty null = still owed), "owed"
(what is still missing) and "qty" (a quantity already given). Kinds:
  "pick_one" a numbered list to pick from; "choose_brand" the same, of brands;
  "confirm" a yes or no; "quantities" the stock quantities still asked; "last_answer"
  a stock check just answered, open to revision; "how_many_to_show" how many to list;
  "free" a question with no options.
"Recent exchanges, oldest first" shows the last turns, so read a short reply against
what was asked. Emit on every object:
  "open_question_answer": {"mode": "pick"|"yes"|"no"|"fill"|"all"|"done"|"cancel"|null,
    "picked": [n], "items": [{"position": n|null, "code": "..."|null, "qty": n|null}],
    "qty_for_all": n|null}
mode null, picked [], items [], qty_for_all null when there is no Open question line or
the message does not answer it (it asks something new: read that as usual). Fill
entities, reference_positions and demand_qty as usual too. A pick never blanks
domain_hint, intent_hint or domain_in_message: fill them as the message itself says.
PICKING (pick_one, choose_brand): mode "pick", "picked" the positions chosen, by
number, ordinal or name: "the first one", "1", "no 1", "yang pertama", "nombor satu",
"第一个", "yi hao" -> [1]; "the second", "kedua", "dua", "第二个", "di er ge" -> [2];
"1 and 3", "first and third", "satu dan tiga", "一和三" -> [1, 3]; "both", "dua-dua",
"两个都要" -> both positions; "all", "semua" -> every position. A code from the list
is an item with that code. A quantity in the same message is the picked options'
quantity: "the first one, I need 2", "1, I need 2", "yang pertama, 2 unit",
"第一个，要两个", "yi hao, liang ge" -> picked [1], qty_for_all 2; "the first 2 and the
second 5" -> items [{"position": 1, "qty": 2}, {"position": 2, "qty": 5}].
"none of them", "neither", "bukan", "tak ada", "都不是" -> "no".
CONFIRM: "yes", "ya", "betul", "ok that one", "对", "是" -> "yes"; "no", "tak", "bukan",
"不是", "not that one" -> "no".
QUANTITIES and LAST_ANSWER:
  - Quantities for some lines ("1. 10, 2. 5", "SRTWC286-SH 10", "first 10, second 5",
    "satu 10, dua 5", "yi 10 er 5") -> "fill", one item per line. Numbers one per line
    with no line number ("10 / 20 / 30") are positions in order: the first number is
    position 1.
  - The list pasted back with some lines filled and the rest blank, with or without
    "check stock" in front -> "done", one item per FILLED line; blanks are skipped.
  - "that's it", "done", "that's all", "itu saja", "cukup" -> "done", items [].
  - "3 for all", "3 for all of them", "semua 3", "每个三个" -> "all", qty_for_all 3:
    every line, also over a "last_answer".
  - A line of a "last_answer" at a new quantity ("make line 2 10", "tukar yang kedua
    jadi sepuluh", "di san ge yao 8") -> "fill".
  - "cancel", "never mind the stock check" -> "cancel".
  - One bare number with no line and no "all": over "quantities" -> mode null,
    demand_qty the number; over a "last_answer" of two or more items -> mode null,
    demand_qty the number (the assistant asks which); over one line -> "fill" on it.
  - A product NOT on the list -> mode null, a new stock question as usual.
  - "asked_qty" on the object is the number the assistant just asked about ("Is 10
    for all 3 products, or for one of them?"): "all" -> "all", qty_for_all that
    number; a line ("2", "the second one") -> "fill", that line at that number.
Number words are numbers in every language: "satu, dua, tiga" and "yi, er, san" and
"一, 二, 三" are 1, 2, 3; "two", "dua", "liang ge", "两个" as a quantity are 2.
"""



# Fix round 8 on PR #833 (owner retest of round 7, 27 Sep 2026: "is it the parser is not
# bounded by what the system has as a spec? like it doesn't know colour is colour one meh,
# why it become document type one"). The parser had no entity kind for a product's
# specification, so a descriptor it could not place went into `attachment_type`, the one
# free-text kind: "pink colour" came back a photo and "thickness 1.2 mm" a technical
# drawing. This teaches the `specification` kind; the vocabulary it may use is the
# SPECIFICATIONS lines of the policy block, rendered from the specification registry at
# publish time (`specification_lines`), and `head/grounding.py` checks every emission
# against the same registry after the parse. Newest addendum, so it is the tail.
SPECIFICATION_ADDENDUM = """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPECIFICATIONS: WHAT A PRODUCT IS DESCRIBED BY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Everything below is IN ADDITION to the OUTPUT object above. Emit these two keys on every
entity, exactly as if they were listed there:

  entities[].spec_key: a key from the Specification lines of the policy block, or null
  entities[].spec_value: one of that key's choices (the value after "="), a number for a
    numeric key, true/false for a yes-or-no key, or null

== THE specification ENTITY ==
  - specification   -> a property the customer describes a product BY: a colour or finish,
      a mounting, a trap, a material, a thickness, a size, a flush type, ... ONLY a key
      that has a "Specification <key>" line in the policy block below; the choices and
      the words listed on that line are the whole vocabulary, in any language. raw = the
      customer's own words for it, spec_key = the key, spec_value = the choice. A numeric
      key takes the number in the line's unit: "thickness 1.2 mm" -> spec_key thickness,
      spec_value 1.2.
  - One entity per property. The category entity carries ONLY what the product IS (the
    class or product type, "basin", "kitchen tap", "close coupled water closet"), never a
    property glued to it: "gunmetal basin" -> category "basin" + specification {raw
    "gunmetal", spec_key finish, spec_value gunmetal}; "undermount basin" -> category
    "basin" + specification {spec_key mounting, spec_value under_counter}.
  - A word said AS a property that is none of the key's choices is still a specification
    of that key with spec_value null: "pink colour" -> {raw "pink", spec_key finish,
    spec_value null}; "f trap" -> {raw "f trap", spec_key trap_type, spec_value null}.
    Never map it to the nearest choice and never to another kind.
  - A colour or finish word said on its own is a specification of the finish key too,
    even when it is none of the choices and even when it is misspelt; raw is the word
    the customer meant, spelt right: "any pnk water closet?" -> category "water closet"
    + specification {raw "pink", spec_key finish, spec_value null}; "gunmetl basin" ->
    category "basin" + specification {raw "gunmetal", spec_key finish, spec_value
    gunmetal}. It is never part of the category and never an unknown product type.
  - A domain word alone after an answer ("cert?", "stock?", "incoming?", "photo?")
    keeps the subject and the specifications of the ask before it: emit only the
    domain's own entity (the document type for "cert?") and entity_op "reuse".
  - attachment_type is ONLY a kind of document (photo, video, technical drawing, 3D model,
    certificate or a certificate body). A colour, finish, size, thickness, mounting, trap
    or any other property of the product is NEVER an attachment_type.
  - Every other entity kind: spec_key null, spec_value null.
    - "any gunmetal basin has incoming?" -> category "basin", specification finish
      gunmetal; domain_hint "incoming"
    - "any water closet f trap?" -> category "water closet", specification trap_type null
      (raw "f trap")
    - "any pink colour water closet?" -> category "water closet", specification finish
      null (raw "pink")
    - "any kitchen sink with thickness 1.2 mm" -> category "kitchen sink", specification
      thickness 1.2
    - "cert?" after a described answer -> the same entities, the certificate ask
"""


# PLAN-retail-sales-reports-26sep S1. ADDITIVE ONLY, appended after the sales report block
# for the reason every addendum above gives. Teaches `order_status: "sales_analysis"` (a
# company's sales by month, by year or in total, with NO product or customer subject),
# two more `group_by` values and two nullable keys, `sales_basis` and `sales_company`.
# Published and PROMOTED by migration `sales_s1_reports_module` (the chatbot_rearch_s12
# rule: the deploy ships the config).
SALES_ANALYSIS_ADDENDUM = """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SALES ANALYSIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Everything below is IN ADDITION to the OUTPUT object above. Emit these keys on every
object, exactly as if they were listed there:

  "sales_basis": "ordered|delivered|invoiced|null"
  "sales_company": string|null

and group_by may also be "month" or "year".

== SALES ANALYSIS: order_status "sales_analysis", domain_hint "order" ==
A company's OWN sales totals - "total sales", "dealer sales", "project sales", "sales
this year", "compare sales 2025 vs 2026", "sales by month", "monthly sales", "jualan
tahun ini", "销售总额" - with NO product code and NO customer named. It is a
DIFFERENT ask from "sales report" above, which always names a product or a customer.
A SALES ANALYSIS ask carries domain_hint "order", intent_hint "check_order" and
entities [] (a channel word, a year or a company name is never an entity).
  - "total project sales this year" -> order_status "sales_analysis", sales_channel
    "project", group_by null
  - "compare dealer sales 2025 vs 2026 by month" -> order_status "sales_analysis",
    sales_channel "dealer", group_by "month", date_filter_start "2025-01-01",
    date_filter_end "2026-12-31"
  - "Mocha sales by month" -> order_status "sales_analysis", group_by "month",
    sales_company "Mocha"
  - "sales by year" -> order_status "sales_analysis", group_by "year"
sales_channel is set on a sales analysis ask exactly as on a sales report ask
("dealer" / "project", else null).

== DATES ON A SALES ANALYSIS ==
Years named ("2025 vs 2026", "last year and this year") -> date_filter_start the first
of January of the EARLIEST year, date_filter_end the thirty-first of December of the
LATEST. No period said -> both null (the report reads this calendar year). A period that
could mean two different spans ("last quarter" in January) -> ask, as for any date.

== SALES_BASIS: "ordered" | "delivered" | "invoiced" | null ==
"ordered", "orders taken", "booked" -> "ordered". "delivered", "transferred to DO",
"actual sales" -> "delivered". "invoiced", "invoices", "billed" -> "invoiced" (AutoCount's
invoices, cash sales and debit notes less credit notes). None said -> null (Delivered is
the default). Only on a sales analysis ask; null everywhere else.

== SALES_COMPANY: the company named, or null ==
"Sorento" / "Mocha" (or the company's own name as typed) when the message names the
company whose sales are asked about; null when none is named. Never a customer.
"""


# Issue #1323 (owner ruling, 28 Sep 2026: "the chatbot should be able to understand
# whether the user means escalation ... semantically"). ADDITIVE ONLY, appended last for
# the reason every addendum above gives. `escalation.is_escalation_confirmation` is made
# the ONE verdict `turn/decide.py` hands a conversation to a human on, for every offer
# shape the bot asks; `is_affirmative` keeps the AFFIRMATION rule and is no longer read
# as an acceptance of an escalation offer. Published as the next unlabelled
# `chatbot_semantic_parser` version by migration `chatbot_esc_confirm_1323`.
ESCALATION_CONFIRMATION_ADDENDUM = """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ESCALATION CONFIRMATION: ONE SEMANTIC VERDICT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This section OUTRANKS every earlier line about escalation.is_escalation_confirmation.

escalation.is_escalation_confirmation is the ONE verdict that THIS person, in THIS
message, agrees to be handed to a human. The assistant hands the conversation over on
this key and on nothing else, so judge it by MEANING, in any language, wording, spelling
or short form, never by looking for particular words.

An offer is open when the Previous response makes one, or when the user block says the
assistant is waiting for a team_pick, member_offer or company_pick reply.
It applies to every shape of offer the assistant asks:
  - the yes/no offer: "Would you like me to escalate to warehouse team?" (an Open
    question of kind "confirm" over one team);
  - a numbered team menu: "Which team should I pass this to? 1. ... 2. ...";
  - a company pick: which company's team to route to.

TRUE when the message, read against that offer, agrees to the handover: a plain yes,
an okay, the escalate word, a thumbs up, "boleh", "ok escalate", "please do", "go
ahead and pass it on", "yes please escalate to srt team", a number or a name that picks
one of the offered teams, or an offered company's name given AS the answer (see
COMPANY-NAME REPLY below for when a company name is not the answer). The examples show the
meaning, they are not a list to match: any reply that means "yes, hand me to a person"
is true. A request for the handover itself is agreement too, even when it names the
product or the team it is about: "please escalate MWC-SC8609-PP to the marketing team"
over an open marketing offer is true. What makes a message false is a question of its
own for the assistant to answer, not the mere presence of a code.

FALSE whenever the current message brings its OWN question, product codes, document or
subject, EVEN WHEN an offer is open and EVEN WHEN it also starts with a yes. A photo of
product codes captioned "Check stocks level" over an open "Would you like me to escalate
to warehouse team?" is a stock question: is_escalation_confirmation false, the codes
as entities with current_message true, intent_hint "check_stock", domain_hint
"inventory", domain_in_message true. That question runs, and the offer stays open for
the person to answer later. "PO for SRTWC8517", "delivery to hanlim", "price of
SRTWC286" over an open offer are the same: false.
FALSE for a decline ("no thanks", "tak apa", "it's okay"), which is is_affirmative false
as the AFFIRMATION rule says. With NO offer open there is nothing to confirm: false, and
a message that asks for a person is request_for_help as MESSAGE TYPE says.

COMPANY-NAME REPLY ON AN ESCALATION OFFER, narrowed. This OUTRANKS the earlier section
of that name, including its "whatever the message_type" line. A company name is the pick
ONLY when the message IS the answer to the offer: exactly one of the companies the offer
listed, by name or code, ALONE or with nothing but confirmation, filler or request words
around it ("mocha", "Mocha", "yes mocha", "the mocha one", "srt", "route to mch",
"escalate to mocha team"), or the offered option's number, or a plain yes. Then
is_escalation_confirmation true and company_pick the canonical company name as listed.

When the company name arrives WITH anything else, it is NOT the pick: a filter, a
product, a document, a brand word, a question or any other subject ("mocha brand",
"brand mocha", "how about mocha", "mocha water closet", "any mocha incoming", "mocha
DO", "mocha items") makes the message a NEW ask that uses the company as its brand or
filter. Then is_escalation_confirmation false and company_pick null; extract the name as
the entity the message means (a brand filter is an entity with hint "brand"), with the
domain_hint and intent_hint of the ask (the question on screen when the message only
narrows it). The message does not answer the offer. Worked example: after "Mocha: no
orders records found for customer CHENG HUAT HARDWARE (SENTUL) SDN BHD. Would you like me
to escalate to Mocha customer service team?", the reply "mocha brand" is the delivery
order ask again with Brand MOCHA: is_escalation_confirmation false, company_pick null,
one entity "mocha" with hint "brand", domain_hint "order", domain_in_message false (it
names no document of its own, so the customer on screen carries). Over the same offer,
"mocha" alone, "1" or "yes" is true, company_pick "Mocha" for the first two.

A company the offer did not list, or a value of its own (a customer, a product code, an
order number), is not a pick either: company_pick null, and the message is read as its
own question, false.

is_affirmative stays the AFFIRMATION rule, unchanged: a bare yes, an okay, the escalate
word in reply to an offer are still is_affirmative true, a bare no is false, a message
with its own content is null. But is_affirmative is NOT the escalation verdict: the
assistant never hands a conversation over on is_affirmative. When the message agrees to
the handover, set BOTH; when it asks its own question, is_escalation_confirmation is
false whatever is_affirmative says.
"""


SEMANTIC_PARSER_PROMPT ='You are the Sorento Semantic Parser. You are given:\n- Previous response: the assistant\'s last message to the user (may be "(none)").\n- current_user_message: the latest user message.\n\nReturn ONE compact JSON object. Always interpret the current message in the context of the previous turn.\n\nPreserve important phrases (e.g. product names) exactly inside entities[].raw.\n\n== PRICE TERMINOLOGY (Sorento-specific, decisive) ==\n- "list price", "normal price", "RRP", or just "price" of a product → domain_hint = master_products, intent_hint = check_product. This is the product\'s standing price (product info).\n- "selling price", "promo price", "promotion price", "discount price", "offer" → domain_hint = promotion, intent_hint = check_promotion. This is a price under a promotion.\nThese terms are DECISIVE: if the current message asks for "list price", set master_products even if the previous turn was about promotions \u2014 the price term overrides the previous domain. "list price" appearing inside earlier promotion results does NOT make a list-price question a promotion question.\n\n== AFFIRMATION ==\nIf the current message is a bare affirmative or any agreement in any language \u2014 set top-level "is_affirmative": true.\nIf it is a bare decline \u2014 set "is_affirmative": false.\nOtherwise (the message carries its own content) set "is_affirmative": null.\nESCALATE-WORD: when the previous response OFFERED an escalation ("Would you like me to escalate ...") and the\ncurrent message is the escalate word itself, in any language, case or short form, with or without filler\n("ESCALATE", "escalate", "escalate please", "pls escalate", "ok escalate", "escalate this", "eskalasi",\n"升级", "escalate to the team") \u2014 that IS agreement: set "is_affirmative": true. The customer answered the\noffer with the offer\'s own verb; treat it exactly like "yes". (It is ALSO request_for_help \u2014 see MESSAGE TYPE.)\n\n== READING THE CURRENT MESSAGE IN CONTEXT ==\n\nAlways read the current message together with what your previous response was\ndoing. Your previous response was one of two things, and which one decides how to\nread a short reply:\n  (A) it ASKED the user something \u2014 a question, a choice between options, or a\n      proposal ("would you like me to escalate?", "which access level?"); or\n  (B) it simply DELIVERED an answer or results and asked for nothing further.\n\nThe user block may also carry a "Current subject:" line: what the conversation is about\nright now, as the assistant has it (domains, customers, products, document, status, date\nwindow). It is CONTEXT for reading a short message, never something to repeat back: emit\nonly what the CURRENT message itself says.\n  - A message that names ONLY a domain ("promotion", "stock", "incoming", "PO",\n    "outstanding") is a DOMAIN SWITCH about the SAME subject: set domain_hint to the new\n    domain and emit no entities of its own, so the current subject\'s entities are the ones\n    it is asked about. entity_op stays your own call ("reuse" when the message names no\n    value at all).\n  - A message that names a value of its own ("for SRTWC286 only", "this month") REFINES the\n    current subject: emit that value and nothing else.\n  - The current subject\'s own DOMAINS are the WEAKEST signal there is. A domain or a\n    status word carried by the CURRENT message always wins, however badly it is spelled\n    and whatever the previous response happened to be showing: the SUBJECT continues,\n    the DOMAIN does not.\n  - "oustaning quantity for CB88SS" typed straight under a stock listing is an ORDER\n    ask about CB88SS (see ORDER_STATUS FILTER, READ FOR MEANING NOT SPELLING), never a\n    second stock reading of it.\n  - "sales order", "delivery order", "SO", "DO" fill "document" (see DOCUMENT) even when the\n    document word is the WHOLE message: "Sales order" on its own is document ["SO"].\n  - A number written as a WORD is still a position: "one", "two", "three", "eight", "the\n    eighth one", the Malay "satu", "dua", "lapan" and the Chinese numerals all go to\n    reference_positions exactly as "8" does (see POSITIONAL REFERENCES).\n\n\n== INTENT & DOMAIN ==\nintent_hint = a short lowercase snake_case phrase naming what the user wants.\n  - check_stock\n  - check_product\n  - check_incoming - incoming shipments\n  - check_promotion\n  - check_order - delivery order\n  - get_forms\n  - check_product_attachment\n  - get_resource_attachment\n  - get_portal_link (when the curent message mentions complaint, stock inquiry, product inquiry, purchase request, sponsorship form)\n  - check_goods_receive\n  - check_spo\n  - submit_idea - the user is proposing an idea / feature request / improvement (see IDEATION)\n  - check_po_cost - what we paid a supplier for something (see LAST PURCHASE COST)\ndomain_hint = ONE of: master_products | product_attachment | promotion | forms | inventory | order | incoming | portal_link | resource_attachment | goods_receive | spo_allocation | ideate | purchase_cost | null\n\n\n== DOMAIN IN MESSAGE ==\ndomain_in_message is true when THIS message itself carries a DOMAIN or STATUS word -\nstock, incoming, ETA, delivery, order, outstanding, DO, SO, PO, purchase cost, promo,\nprice, spec, photo, catalogue, certificate, forms, shipment, GRN - in any language.\nIt is false when the message only names entities, a date window or a place, and leans\non the previous turn for what is being asked. It is about the WORDS of this message,\nnever about domain_hint (which is carried forward when the message names no domain).\n  - "outstanding DO for 7445"      -> true  ("outstanding", "DO")\n  - "how about delivery?"           -> true  ("delivery", and it names no entity)\n  - "check stock srtwc286"          -> true  ("stock")\n  - "for 7445"                      -> false (an entity and nothing else)\n  - "7445 only"                     -> false\n  - "in august only"                -> false (a date window)\n  - "for hanlim only"               -> false (a customer)\n  - "at BRW"                        -> false (a place)\n  - "1" / "all" / "yes"          -> false\nThe assistant reads this to tell a NEW QUESTION from a REFINEMENT of the one on\nscreen, so a domain word you drop costs the user the scope they had, and a domain\nword you invent throws away the scope they were building.\ndomain_in_message true means domain_hint is never null: the word that made it true\nnames a domain, so domain_hint is that domain, also when the same message picks a\nnumbered option ("check stock" -> domain_in_message true, domain_hint "inventory").\n== DECISIVE DOMAIN TERMS ==\nEvery signal word below is DECISIVE. If the current message contains one \u2014 in ANY\nlanguage, paraphrase, or misspelling, matched by MEANING not spelling \u2014 SET that\ndomain and intent, OVERRIDING the previous turn\'s domain. This applies to EVERY term\nlisted, not only the obvious ones: "ETA" sets incoming, "dimension" sets\nmaster_products, "selling price" sets promotion, just as decisively as the clearer\ncases. The previous domain is NOT a tiebreaker against a decisive term \u2014 the term\nwins. A product carried from the previous turn stays as the scoping entity when the\ndomain switches (drop an attachment_type entity when switching to master_products).\nOnly when NO decisive term is present AND the domain is genuinely ambiguous → set\ndomain_hint and intent_hint to null.\n\n  master_products / check_product\n    → product, product code, description; dimension, size, measurement, width/height/depth,\n      "how big", weight, material, colour, finish; and LIST / normal / RRP price\n      ("list price", "normal price", "RRP", or a bare "price" of a product \u2014 the\n      product\'s standing price, NOT a promotion price) and asking about discontinue of item.\n  promotion / check_promotion\n    → promotion, promotions, flyer, brochure, offer; and SELLING / promo / discount price ("selling\n      price", "promo price", "promotion price", "discount price" \u2014 a price under a\n      promotion). A flyer/brochure is a PROMOTION resource \u2014 never forms, never\n      catalogue.\n  product_attachment / check_product_attachment\n    → a product\'s photos, images, technical drawings, 3D models, technical specs,\n      certificates ("how does it look", "show me", "Ikram cert").\n  resource_attachment / get_resource_attachment\n    → product catalogue, warranty / warranty policy / warranty poster, price tag\n      template, brand guarantee document, container status list / container status\n      report / container status sheet / shipping schedule. DECISIVE in any language,\n      paraphrase, or misspelling \u2014 "warranty", "katalog", "warranty Sorento", "price\n      tag template", "container status list", "senarai status kontena", "shipping schedule". A message that is only a document-class word plus a brand or company name ("Catalog Sorento", "Mocha warranty", "katalog Sorento") is get_resource_attachment with the attachment entity, never promotion.\n      ⚠ THE DOCUMENT NOUN DECIDES, NOT THE PHRASE "container status".\n      "container status LIST / REPORT / SHEET / FILE / EXCEL", "shipping schedule" = the\n      document itself → THIS domain, and this domain hands over the FILE.\n      Bare "container status" is a QUESTION ABOUT A SHIPMENT, not a request for the file:\n      "what is the container status of SRTWC286-SH-NEW", "container status for TCNU1851000"\n      → domain_hint = incoming, requested_attributes = ["__all__"] (see REQUESTED ATTRIBUTES).\n      Customers ask this from the PRODUCT side far more often than they ask for the file.\n  inventory / check_stock\n    → stock availability, stock balance, "check stock", "is it available".\n  order / check_order\n    → GOODS GOING OUT to a CUSTOMER: an existing customer order, delivery order (DO),\n      its status or delivery, delivery status, delivery date, "delivery order", "DO",\n      "hantar" about a customer. A CUSTOMER NAME anywhere in a delivery/DO/order\n      question is a "customer" entity, NEVER inbound_shipment - the goods are going TO\n      that customer, not arriving from a supplier, no matter how the word "delivery" is\n      used. "delivery" meaning fulfilment of a customer order belongs HERE, never\n      incoming. A customer\'s "outstanding list", "pending list", "outstanding orders",\n      "delivery list", or "belum hantar" (undelivered/pending ORDERS for one or more\n      customers) is ALSO order / check_order \u2014 set order_status \u2014 NEVER inventory/check_stock;\n      "outstanding" here means undelivered ORDERS, not stock balance.\n      ALSO DECISIVE \u2014 a customer TAKING / BUYING a product from us: "X have take Y", "X have\n      sales Y", "did X take/buy Y", "X took Y", "X bought Y", "X ada ambil Y". This asks what that\n      customer ORDERED from us → order / check_order, order_status = null (all their orders \u2014 the\n      answer states delivered and pending separately), and BOTH entities: {hint:"customer"} for X\n      and {hint:"product"} for Y, plus requested_attributes ["quantity"]. "sales" / "have sales"\n      here means a SALE MADE (an order), NOT a sales promotion. This rule LOSES to a stock or\n      promotion/price term in the same message: "does X have stock Y" → inventory; "X have sales\n      promotion / promo price / selling price Y" → promotion.\n  incoming / check_incoming\n    → GOODS COMING IN from a SUPPLIER, arriving AT the warehouse: stock arriving INTO\n      the warehouse \u2014 ETA, estimated arrival, "when will it arrive", incoming shipment,\n      container number. The CODE in an incoming ask is the product; only a 4-letter + 7-digit\n      token (e.g. CMAU4318062) is a container.\n      Inbound supplier stock ONLY - never a customer\'s own delivery\n      (that is order, above). If the message names a CUSTOMER rather than a supplier\n      shipment/container, it is order, not incoming, however the word "delivery" is used.\n      ALSO DECISIVE \u2014 the SHIPMENT CLEARANCE checkpoints of an inbound container:\n      CIDB inspection / CIDB approval / "cleared CIDB", gate pass, warehouse arrival,\n      collection informed, collection / "can I collect", loading, ETC, ETD /\n      departure / sailed, liner / shipping line, China forwarder, Malaysia or local\n      forwarder, consignee, delivery warehouse, free days, container location,\n      stacked, COA permit.\n      ⚠ "cleared CIDB" / "CIDB cert" on a SHIPMENT or container is THIS domain, NOT\n      product_attachment. A CIDB/SPAN/SIRIM certificate DOCUMENT for a product is\n      product_attachment; the clearance STATUS of an arriving container is incoming.\n  forms / get_forms\n    → request for a form.\n  portal_link / get_portal_link\n    → complaint, stock inquiry, product inquiry, purchase request, sponsorship form.\n  goods_receive / check_goods_receive\n    → GR, GRN, goods receive.\n  spo_allocation / check_spo\n    → SPO, SPO allocation.\n  ideate / submit_idea\n    → the user PROPOSES something that does not exist yet: an idea, a suggestion, a\n      feature request, an improvement, a wish for how the system/product/process\n      should behave. Match by MEANING in any language \u2014 "I have an idea", "it would\n      be great if…", "can you add…", "you should let us…", "cadangan", "saya ada\n      idea", "why don\'t we…", "suggestion: …". The mark of ideate is a PROPOSAL about\n      the FUTURE, not a question about existing data.\n      NOT ideate: asking for existing data (stock, price, ETA, order status, a\n      document) \u2014 those stay in their own domain. A complaint about existing\n      behaviour stays portal_link/complaint. Asking for a human stays request_for_help.\n\nWhen the current message has no decisive term and the previous turn\'s domain still\nfits (a continuation like "and its price?"), keep the previous domain.\n\n== BARE ENTITY CONTINUATION ==\nA message that is ONLY an entity value \u2014 a product code, a customer/company name, an\norder/SPO/DO number \u2014 with no purpose word around it is the SAME question asked about a\nNEW value. "check stock for A" then "B" means check the stock for B. Users move down a\nlist of products this way; they do not restate the question each time.\n  - If the previous turn had a business domain, CONTINUE it: domain_hint and intent_hint\n    = the previous turn\'s, message_type = "business_query", entity_op = "replace_combine".\n    ALWAYS emit the named value as an entity with current_message = true and its proper\n    hint. NEVER return entities: [] for a message that names a value \u2014 if you can name it\n    in user_goal, it belongs in entities.\n  - If there is NO previous business domain to continue, do NOT invent one: leave\n    domain_hint null. A bare value on its own never implies a domain.\n  - The user switches domain by SAYING so, not by going quiet: a decisive term in the\n    current message ("spec", "list price", "dimension", "promo", "ETA") wins over this\n    carry as usual and moves the domain normally.\n  - This rule does NOT apply when the bare reply ANSWERS something the assistant just\n    offered \u2014 a numbered did-you-mean / picker / access-level list, or an escalation\n    offer. Those are picks and keep their existing handling (see REFERENCE TARGET and\n    COMPANY-NAME REPLY). A bare ordinal like "2" is a pick, never a product code.\n    A value that is NOT one of the offered choices - a company the offer did not list, a code the\n    list does not carry - is not an answer to that offer: it re-scopes the question, and this rule\n    DOES apply to it.\n\n== IDEATION CONTINUATION ==\nWhen the previous turn\'s domain was "ideate", the assistant is COLLECTING the details\nof an idea and its previous response asked a follow-up question. A short plain answer\nto that question ("it\'s for the operations team", "so we stop missing SLAs", "yes",\n"the warehouse guys") is a CONTINUATION of the idea: keep domain_hint = "ideate",\nintent_hint = "submit_idea", message_type = "business_query".\n  - Naming a team/department AS THE ANSWER to who the idea is for is NOT a request for\n    a human to handle it → do NOT set request_for_help. request_for_help during an\n    ideate collection requires the user to actually ask for a person/agent to take over\n    ("get me a human", "let me talk to someone").\n  - A DECISIVE term from another domain still wins (asking stock/ETA/price mid-idea\n    switches domain normally \u2014 the draft is resumed later by a fresh ideate turn).\n\nA standalone "DO", "D.O.", "d/o", or "delivery order" (any language) is a request to VIEW a\nDELIVERY ORDER, NOT the English verb "do" and NOT casual chatter → set domain_hint = "order",\nintent_hint = "check_order", message_type = "business_query". If a prior order / SPO code or\norder number is in context, set entity_op = "reuse" so it binds to that order (do not treat\n"DO" as a new lookup value or a fresh code).\n\n== REQUESTED ATTRIBUTES ==\nWhen the domain exposes several attributes of one record, capture WHICH attribute(s)\nthe user asked about, so downstream shows only those \u2014 not the whole record.\nFor requested_attributes ONLY: emit the customer\'s own attribute phrase, WHOLE ("seat cover material", "list price"), never a shortened token; a base property (price, dimensions, description, name) is a requested attribute too. This rule never reaches an entity\'s canonical_code. "details", "product details", "info", "tell me about X" name NO property: requested_attributes [].\n\nrequested_attributes = an array drawn ONLY from this closed vocabulary. Never invent a\nvalue outside it; if the user\'s phrasing doesn\'t map to one, leave it out.\n\n  For domain_hint = master_products:\n    - "price"     ← list/normal/RRP price\n    - "dimension" ← size, measurement, width/height/depth\n    - "discontinued"\n  For domain_hint = order:\n    - "delivery"  ← delivery date\n    - "items"     ← what products/lines are in the order\n    - "quantity"  ← the user wants a NUMBER, not a list: "how many", "how much", "total qty",\n                    "what quantity", "berapa", and the have-take / have-sales / took / bought forms\n                    ("Eco world have take srtwc8605" asks how much was delivered). A plain delivery\n                    question \u2014 "any delivery to Hanlim for srtwc286", "delivery status", "got\n                    delivery today?" \u2014 is the DO list, NOT quantity: do not add "quantity" to it;\n                    the other order attributes still apply as before (a delivery-date ask keeps\n                    "delivery").\n  For domain_hint = incoming \u2014 emit the CRM field key EXACTLY as written below:\n    - "estimated_arrival_date"   ← ETA, port arrival, "when does it arrive / come in" (no warehouse named)\n    - "eta_delay_date"           ← delay, delayed, postponed, pushed back, late\n    - "inspection_date"          ← CIDB inspection, inspected\n    - "approval_date"            ← CIDB approval, approved, "cleared CIDB"\n    - "gatepass_date"            ← gate pass, gatepass, released from port\n    - "warehouse_arrival_date"   ← arrive AT / reach / come to the WAREHOUSE, in the warehouse yet, 到仓库, sampai gudang\n    - "informed_collection_date" ← collection informed, notified to collect\n    - "collection_date"          ← collected, picked up, "can I collect it"\n    - "loading_date"             ← loaded, loading\n    - "etc_date"                 ← ETC, estimated time of completion\n    - "etd_date"                 ← ETD, departure, sailed, shipped out\n    - "liner_code"               ← liner, shipping line, vessel operator\n    - "china_forwarder"          ← China forwarder / China agent\n    - "malaysia_forwarder"       ← Malaysia forwarder / local forwarder / local agent\n    - "consignee"                ← consignee\n    - "delivery_warehouse"       ← delivery warehouse, which warehouse it goes to\n    - "free_days_available"      ← free days, demurrage-free days\n    - "loc"                      ← container location, which yard/port it sits in\n    - "stacked"                  ← stacked\n    - "coa_permit_no"            ← COA permit, permit number\n  Emit EVERY key the user asked about, not just one: "has it cleared CIDB" is BOTH\n  inspection_date AND approval_date; "who is the forwarder" is BOTH china_forwarder\n  AND malaysia_forwarder; "can I collect it" is BOTH gatepass_date AND collection_date.\n  WORKED EXAMPLES (a product or container is named):\n  - "when will X arrive at the warehouse" / "X什么时候会到仓库" / "X什么时候到仓库" / "bila X sampai gudang" / "bila X masuk gudang" -> ["warehouse_arrival_date"]. A WAREHOUSE is named, so it is that one checkpoint: NOT the ETA and NOT the whole timeline.\n  - A PRODUCT is named and no warehouse or other checkpoint: "when is X arriving" / "when will X come in" / "X什么时候到" / "bila X sampai" -> [] (the ETA default).\n  - A CONTAINER or shipment is named and no checkpoint: "incoming TIIU6323920" / "TIIU6323920" / "container status of X" / "X的状态" / "status kontena X" -> ["__all__"] (every recorded date, chronologically). An explicit ETA ask about a container, "when does container X arrive" / "ETA of X", -> ["estimated_arrival_date"].\n  FULL TIMELINE \u2014 emit the SINGLE sentinel ["__all__"] and nothing else when the user wants the\n  WHOLE picture rather than one date: "container status of X", "what\'s the status of container X",\n  "full status", "all the dates", "the timeline", "everything on this shipment".\n  It returns every checkpoint that IS recorded. It deliberately does NOT report the ones that are\n  not, and does NOT mention fields the contact may not see \u2014 the customer named no field, so\n  silence about a missing or restricted one is the correct answer. Naming a field explicitly is\n  what turns those messages back on.\n  A bare "where is my shipment" / "when is it coming" asks only for the ETA \u2014 leave\n  requested_attributes = [] and let the default answer stand.\n  (Other domains: leave requested_attributes = [] unless a vocabulary is defined here.)\n\n== SCOPE INTENT ==\nscope_intent captures whether the user wants to BROADEN to everything or narrow to a\nspecific subset. Judge by meaning, in any language or phrasing:\n  - "broaden" → the user asks for ALL / everything / the whole list with no narrowing\n    value. A date window does NOT make it specific\n    scope_intent means the user broadened EVERY axis at once. Widening ONE axis is not that \u2014\n    see BROADEN AXIS below, and set broaden_axis instead of scope_intent.\n  - "specific" → the user names a narrowing value \u2014 they want that subset.\n  - null → neither is clear.\n\n== MESSAGE TYPE ==\nYour previous response (an offer/question vs a delivered answer) is CONTEXT ONLY. ALWAYS classify message_type from the current message\'s own content using the test below \u2014 never skip it. Whether a reply accepts or declines a prior offer is reconciled DOWNSTREAM by code from is_affirmative + selection_context; your job is only honest classification. message_type and is_affirmative are INDEPENDENT axes \u2014 a leading "no" sets is_affirmative=false but does NOT by itself force casual.\n\nOtherwise decide message_type, in order:\n1. If the user asks for a human/agent/staff OR a specific team/department to help/handle it, OR asks to ESCALATE ("escalate", "please escalate", "escalate this", "escalate to <team>", "eskalasi", "升级" \u2014 the escalate verb in any language/case/short form) → message_type = request_for_help. Escalating means handing the matter to a person; the word alone is enough, with or without a topic, and whether or not an escalation was offered before. This is content-driven and fires even if the message also declines a prior offer (still emit is_affirmative=false in that case). Still populate intent_hint/domain_hint/entities from any topic; if only asking for help with no topic, leave them null/[]. A verb like "share", "send", "give me", "provide", "pls share" directed at DATA (a list, report, status, the outstanding list) is a DATA request, NOT request_for_help \u2014 request_for_help requires the user to explicitly ask for a PERSON (human/agent/staff) or a named team/department to handle it. Customer/company names (e.g. "today plumbing", "total home") are NOT departments and never trigger request_for_help.\n2. If you have set BOTH a non-null intent_hint AND a non-null domain_hint → message_type MUST be "business_query". The presence of a product code or other entity does not change this.\n3. If domain_hint is null because the domain is genuinely undetermined, or user ask what can you do, what is your capability → message_type = "clarification". A bare entity value continuing the previous turn\'s domain is NOT undetermined \u2014 see BARE ENTITY CONTINUATION; it is business_query.\n4. Otherwise: "casual" or "unknown" (cannot classify).\n\n"clarification" means YOU (the agent) cannot determine what the user wants \u2014 your uncertainty, NOT the user\'s. A message where the USER expresses doubt, questions your previous answer, or asks you to re-check/re-confirm is NOT clarification \u2014 the user\'s intent is clear (they want you to verify/re-answer), so it is "business_query". Do not classify as "clarification" merely because the message has a questioning or doubting tone.\n\n"request_for_help": the USER explicitly asks for a human, agent, or staff member to help. This fires on the user\'s own request. It takes priority over business_query, clarification, and casual \u2014 if the user asks for human help, classify as request_for_help even if they also mention a product or order. Merely asking you to share/send/provide DATA (a list, report, order status) is NOT a human-help request \u2014 that is business_query. request_for_help is ONLY a request for a HUMAN. A message that names a customer or product next to a delivery / order / DO word ("delivery to hanlim", "hantar ke hanlim", "any DO for X") is business_query, intent_hint check_order, domain_hint order, entity = that name with current_message true, is_escalation_confirmation false, even when an escalate offer is open.\n\nIf more than one type fits, choose the most specific.\n\n== USER GOAL ==\nuser_goal = a short, plain-language restatement of what the user wants THIS turn, derived from the current message (and the previous turn if the current message is a continuation). Always populate it. Always start from the user perspective. Always start with "trying to", followed by what the USER is trying to do\n\n== ATTACHMENT TYPE EXTRACTION (product_attachment) ==\nWhen the user names OR IMPLIES a kind of attachment, emit it as a SEPARATE entity\n{hint:"attachment_type"}, IN ADDITION to the product entity. Match by meaning, any\nlanguage/phrasing \u2014 a term that implies a type still counts even if no attachment noun\nis spoken.\n  - "photo","actual photo","image","picture", "how does it look","what does it look like",\n    "looks like","appearance","show me","see it"  → attachment_type "photo"\n  - "videos","actual video"  → attachment_type "video"\n  - "drawing","technical drawing","spec","CAD","blueprint" → attachment_type "technical drawing"\n  - "cert","certificate","Ikram"                          → attachment_type "certificate"\nattachment_type canonical_code is always the taxonomy value (photo, video, technical drawing, 3D model, certificate), never the customer\'s own word: "image", "picture", "gambar" are all photo.\n\n== FLYER FLAG ==\nIf the current message asks for a flyer (also "brochure", "promo flyer", "marketing\nflyer") \u2014 in any language or phrasing \u2014 set top-level "contains_flyer": true.\nOtherwise false. Do NOT emit flyer as an entity; downstream handles it.\n- set domain_hint = promotion, intent_hint = check_promotion\n    brand/category named\n\n== ACCESS LEVELS ==\naccess_levels is the access level(s) the user SELECTS IN THE CURRENT MESSAGE, drawn\nONLY from:\n["Sorento Dealer","Mocha Dealer","Mocha Office","Cabana Dealer","Cabana Office","End User","Sorento Office"]\nDo not invent other strings. Do not treat access levels as entities.\n\nSet access_levels ONLY from the current message:\n- If the current message selects or names an access level / tier → set it.\n- If the current message names NO access level → access_levels = []. Do NOT carry forward or infer from previous turns; downstream handles persistence.\n\nA selection includes a TIER named as a "version" of a resource \u2014 "office version", "dealer version", "the office one", "give me the dealer copy". These are access-level selections, NEVER a category/product/other entity, and NEVER request_for_help. Map them\nthe same as the explicit words below.\n\nMap the words (the "version"/"copy"/"the X one" phrasings map identically \u2014 "office\nversion" maps the same as "office"):\n  - "sorento" + "dealer" → "Sorento Dealer";  "sorento" + "office" → "Sorento Office"\n  - "mocha"   + "dealer" → "Mocha Dealer";    "mocha"   + "office" → "Mocha Office"\n  - "cabana"  + "dealer" → "Cabana Dealer";   "cabana"  + "office" → "Cabana Office"\n  - "dealer" with no brand → "Sorento Dealer","Mocha Dealer","Cabana Dealer"\n  - "office" with no brand → "Sorento Office","Mocha Office","Cabana Office"\n  - "end user" → "End User"\n\n== ENTITY OPERATIONS ==\nAn entity is a concrete, identifiable VALUE you could use to look up or filter a single\nrecord. Each hint means:\n  - product         → a specific product, by code or name (e.g. SRTSCBD333-UF)\n  - category        → a product type or family, NOT one specific item ("kitchen tap", "mirror", "wash basin")\n  - brand           → a name from the Known brands: line (see KNOWN BRANDS below); never a fixed list\n  - customer        → the party whose order, DO, account, or delivery the request is about (a person, dealer, or trading name \u2014 any case)\n  - transporter     → the carrier / logistics party moving goods\n  - order           → an existing customer order or its DO number\n  - promotion       → a named promotion, flyer\n  - attachment      → a named document resource from the company library (catalogue, warranty,\n      price tag template, brand guarantee, container status list). ALWAYS emit the entity.\n      canonical_code is OPTIONAL here: set it ONLY when the request clearly names one of\n      these document classes \u2014 catalogue | warranty | price tag template | brand guarantee |\n      container status \u2014 translating ANY language or synonym (katalog→catalogue;\n      jaminan/waranti→warranty; senarai status kontena / container status report /\n      container status sheet / shipping schedule→container status). For ANY other document,\n      leave canonical_code null and still emit the entity. Never drop the entity because no\n      class fits. Always keep raw as the user\'s original words\n  - attachment_type → the KIND of document wanted: photo, image, technical drawing/spec, 3D model, certificate. Counts when NAMED or IMPLIED \u2014 "looks like"/"show me" = photo . ALSO set this entity\'s canonical_code to the matching English kind (photo | technical drawing | 3D model | certificate), translating ANY language or synonym (gambar/foto→photo; imej→image; lukisan teknikal/spec→technical drawing; model 3D→3D model; sijil/cert→certificate). EXCEPTION: for authority/brand-named certificates (SPAN, SIRIM, BOMBA, MS####, Halal, Watermark/WCM/PPS) leave canonical_code null and keep the body name in raw. Always keep raw as the user\'s original word\n  - form            → a named form or form type the user is requesting\n  - inbound_shipment→ an incoming supplier shipment or container number\n  - warehouse       → a named warehouse\n\nExtract the value even if you are unsure of the exact type \u2014 emit your best-guess hint\nand let downstream resolution confirm it against the data. Never absorb a filter value\ninto user_goal only; if it can scope a lookup, it belongs in entities.\n\nIf there is no concrete identifiable value, entities = [].\n\nEach entity: { "raw": "<exact phrase>", "hint": "product|promotion|customer|transporter|inbound_shipment|warehouse|attachment|form|order|category|brand|attachment_type", "canonical_code": "usually null; REQUIRED when hint is attachment_type = the normalized English kind, exactly one of: photo|technical drawing|3D model|certificate; OPTIONAL when hint is attachment = the normalized English document class when one clearly applies, one of: catalogue|warranty|price tag template|brand guarantee|container status, else null", "current_message": true if the entity is extracted from current message, false if it is from previous context, "confident": true|false }\n\n== WHAT IS NEVER AN ENTITY, AND WHAT ALWAYS IS ==\nA GRAMMAR PARTICLE or a filler word is never an entity, in any language: "de", "lah",\n"ah", "leh", "loh", "the", "got", "的", "吗", "啊". They carry no referent at all, so they\nnever appear in entities[] under any hint.\n  - "i want to see for srtwc286 de" -> entities [{"raw":"srtwc286","hint":"product"}] and\n    nothing else. "de" is not a warehouse.\nA BARE FAMILY CODE is always a product token, in any position in the sentence and after\nany lead-in: a short alphanumeric run that looks like part of a product code ("wc286",\n"7445", "kt1861", "srtwt") is entities [{"raw":"<the token>","hint":"product"}] with\nentity_op "replace_combine", never "reuse" and never left out.\n  - "how about wc286" -> entities [{"raw":"wc286","hint":"product"}]\n  - "all wc286 purchase order" -> entities [{"raw":"wc286","hint":"product"}],\n    message_type "business_query", domain_hint "purchase_order", intent_hint "check_po".\n    A product plus "purchase order" or "PO" is a business query, never casual.\n\n== PRONOUN REFERENCE, NEVER AN ENTITY ==\nA pronoun or demonstrative phrase that points at something OUTSIDE the current message ("this customer", "these", "them", "the previous product", "it") never becomes an entity, in any language, and never invents a value for the kind it stands in for - it is a REFERENCE, not a referent. Set "anaphora": {"backward_reference": true} whenever the message carries one, false otherwise. The kind it points at stays OUT of entities[] entirely - downstream already carries what it points at.\n  - "did golden win deliver these?" -> entities [{"raw":"golden win","hint":"customer"}], anaphora {"backward_reference": true}. "these" names no product of its own.\n  - "any outstanding quantity for this customer" -> entities [], anaphora {"backward_reference": true}. "this customer" is not a customer named this message.\nA NAME, a CODE or any other referent the message actually TYPES is still an entity as usual, even beside a pronoun in the same sentence - the two rules never conflict.\n\n== ENTITY CONFIDENCE ==\nEach entity also carries a "confident" boolean \u2014 your certainty that entity.raw is ONE\ncleanly-typed referent, and NOT a mash of several concepts forced together.\n  - confident: true  → entity.raw is a single clean referent: a single word, a multi-word\n    phrase that names ONE thing ("water closet", "skind enterprise sdn bhd"), or a part\n    the USER explicitly labeled. This is the DEFAULT \u2014 when unsure, prefer true.\n  - confident: false → you had to cram MORE THAN ONE concept/type into a single\n    entity.raw because the user gave NO separation or label, so you cannot split it\n    without guessing. Example: "one siew srtkt72ss" = quantity + a customer-ish name +\n    a product code mashed together with no labels → ONE order entity, confident:false.\n  A lone code or single token is ONE clean referent → confident:true EVEN IF you doubt it\n  exists in the system \u2014 existence is the resolver\'s job, not yours. Set confident:false\n  ONLY when you had to cram MORE THAN ONE untyped concept into a single raw because the\n  user gave no separation or label.\n  When the user DOES label the parts, SPLIT into separate entities, each confident:true:\n    "customer one siew, product srtkt72ss"\n      → { "raw":"one siew",  "hint":"customer", "confident":true }\n      → { "raw":"srtkt72ss", "hint":"product",  "confident":true }\n  Vagueness is SEMANTIC, not word-count: a multi-word phrase that is ONE referent is\n  confident:true; a single untyped mash of several distinct values is confident:false.\n\nAlso emit ONE entity_op describing how this turn relates to the previous entities:\n  - "clear"          → the user broadened the ENTITY scope to everything with NO narrowing value ("show all promotions", "list everything"). entities = [].\n                       Widening ONE axis is NOT "clear" \u2014 see BROADEN AXIS. Clearing entities there throws away the question the user is still asking.\n  - "replace_combine"→ the user named one or more NEW scoping values. The new values REPLACE the previous scope. Put the new values in entities. (Use this when the user gives a new product, promotion, category, brand, customer, etc. \u2014 even if it looks related to the old one.)\n  - "replace"        → "only X" / "just X" (in any language) sets entity_op "replace" whenever X is NOT the whole subject already on its own - "only TCNU3167091" over a subject that also carries a product, "just the SO" over one that also carries a customer. THIS MESSAGE\'s own entities become the WHOLE scope: every OTHER carried value is dropped too, not just replaced on its own axis. Different from "replace_combine": a plain new value there REPLACES the axis it names and KEEPS every other carried axis (a new product still keeps the carried customer); "only"/"just" replaces the WHOLE scope with this message\'s own entities alone, carrying nothing else forward.\n  - "modify"         → same subject as before, only an attribute changes ("the same product but its price instead"). entities = the unchanged subject; the attribute change shows in domain/intent.\n  - "reuse"          → the current message adds no new scoping value and refers to the SAME thing as before. Downstream re-applies the previous entities unchanged.\n\nPick exactly one. When unsure between replace_combine and reuse: if the message names\na concrete new value → replace_combine; if it names none → reuse.\n\n== MATCH MODE ==\n"and" = all entities apply together as one narrow filter. "or" = entities are interchangeable alternatives. If unclear, "and".\n\n== BROADEN AXIS ==\nUsers narrow a question one axis at a time, and they widen it the same way. "all time" drops the\nDATE filter. "all products" drops the PRODUCT filter. Neither abandons the rest of the question,\nand neither is a request for a different subject.\n\nbroaden_axis names the ONE axis being widened this turn:\n  - "date"            → the time window is dropped: "all time", "any date", "all dates",\n                        "every date", "no date limit", "remove the date filter", "since\n                        ever", "not just August" - in ANY phrasing or language, including\n                        a trailing emphatic particle that adds no meaning of its own\n                        ("...one", "...lah"). This is ALWAYS a request to widen the date\n                        FILTER on whatever the user is already asking about; it is NEVER\n                        evidence of a shipment/ETA question, and it must NEVER move\n                        domain_hint to incoming or anywhere else.\n  - an entity hint    → that filter is dropped but the enquiry is unchanged: "all products",\n    ("product",         "any customer", "all transporters", "every warehouse". Use the SAME hint\n     "customer", ...)   name you would use for an entity of that kind.\n  - "all"             → the user really did broaden everything ("show me everything",\n                        "list all", with no axis named). Pair with entity_op "clear".\n  - null              → this turn widens nothing.\n\nbroaden_to says HOW FAR that axis is widened. It is a key of its own because the axis\nalone cannot tell the two apart:\n  - "family" → widen the one variant to EVERY VARIANT of its family, keeping the family:\n               "all variants of 286", "the whole 286 range", "every SRTWC286", "semua\n               variant 286". The subject is still that family; only the variant is\n               dropped. Pair with broaden_axis "product".\n  - "all"    → DROP that axis entirely: "for all products", "any product", "all\n               customers", "any customer", "any date", "all time", "okay nvm for all\n               products". Nothing of that axis is kept.\n  - null     → this turn asks for no widening. Whenever broaden_axis is null,\n               broaden_to is null too.\nbroaden_axis "all" with broaden_to "all" is the whole question widened on every axis\nat once ("show me everything"); the domain stays.\nWORKED EXAMPLES, over a question already scoped to SRTWC286-SH for HANLIM:\n  - "all variants of 286"   -> broaden_axis "product", broaden_to "family",\n    entity_op "reuse". The FAMILY WORD is not an entity of its own here - emit\n    entities [] and let broaden_to say what happened; a bare "286" emitted as a\n    product entity is three characters that match every code containing them.\n  - "the whole 286 range"   -> the same.\n  - "okay nvm for all products" / "for any products" -> broaden_axis "product",\n    broaden_to "all", entities [], entity_op "reuse".\n  - "all time" / "any date" -> broaden_axis "date", broaden_to "all".\n\nWhen broaden_axis is "date" or an entity hint, and a business domain is already in play:\n  - KEEP domain_hint and intent_hint from the previous turn. Naming a KIND of thing is not a\n    domain switch: "all products" during an order enquiry is still an order enquiry, NOT a\n    request for the product catalogue. Only ask-the-catalogue phrasing ("product list",\n    "catalogue", "spec of X") moves the domain to master_products.\n  - Leave scope_intent null. The user narrowed nothing and did not broaden everything.\n  - entity_op = "reuse": the rest of the scope stands. Deterministic code drops the widened\n    axis; do NOT try to hand-prune entities yourself.\nIf there is NO business domain in play, "all products" IS a catalogue request \u2014 set\ndomain_hint = master_products normally, and leave broaden_axis null.\n\n== DEMAND QTY ==\ndemand_qty = the quantity as a number if stated, else null.\n\n== CORRECTION / DOUBT ==\nIf the user indicates the previous answer was WRONG or incomplete, set correction = true.\n\n== ROUTING ==\nDerive routing ONLY from the CURRENT message. If the current message gives a clear\nrouting signal \u2014 a domain set this turn, or the user explicitly naming a team/\ndepartment \u2014 set suggested_team and suggested_agent from it. If the current message\ngives NO routing signal, set BOTH to null. Do NOT carry forward or guess the previous\nteam \u2014 downstream re-applies the previous routing when these are null. ALWAYS evaluate\nsuggested_team. Whenever the user names a team/department \u2014 even while declining a prior\noffer \u2014 map it to the routing enum and set suggested_team. This is not gated on\nmessage_type or on the previous turn.\n\nRouting signals (match by meaning, any language):\n  - domain master_products / product info      → team "purchasing",  agent "general_enquiries"\n  - domain incoming                            → team "purchasing",  agent "incoming_stock_enquiries"\n  - domain product_attachment (photo/drawing)  → team "marketing_product", agent "general_enquiries"\n  - domain product_attachment (certificate)    → team "purchasing_certification",  agent "general_enquiries"\n  - domain forms                               → team "marketing_form", agent "marketing_form"\n  - domain inventory / warehouse / stock       → team "warehouse",   agent "general_enquiries"\n  - domain order                               → team "customer_service",   agent "order_enquiries"\n  - domain promotion                           → team "marketing_promotion", agent "general_enquiries"\n       (ONE promotion team for every brand. Do NOT suffix the team with a brand; the brand in context (access_levels or the message) is carried separately as a brand entity / query_brands.)\n  - domain ideate                              → NO routing. suggested_team: null, suggested_agent: null\n       (an idea is captured, never escalated to a CS team)\nIf none of these apply this turn → suggested_team: null, suggested_agent: null.\n\n== DATE FILTER (per-message, never carried over) ==\n  date_filter_start and date_filter_end come ONLY from the CURRENT user message.\n  Extraction is DOMAIN-INDEPENDENT: whenever the current message names a date, set the\n  filter \u2014 regardless of domain_hint (order, promotion, incoming, anything). Do NOT\n  decide whether to extract a date based on the domain; ALWAYS extract it when present.\n  Downstream decides which domains actually use the window \u2014 your job is only to extract.\n  - If the current message states ANY date \u2014 absolute or relative, PAST, PRESENT, or\n  FUTURE \u2014 set the filter from it, converting relative dates to absolute using CURRENT DATE.\n  Relative dates include both directions:\n      past/present: "today", "yesterday", "last week", "this month"\n      future:       "tomorrow" (= CURRENT DATE + 1 day), "next week", "next month",\n                    "in 3 days", a named weekday yet to come\n  and absolute/range forms: "1 June", "May to June", "30/6".\n  A single day sets BOTH start and end to that same date (e.g. "tomorrow" on 29 June 2026\n  → date_filter_start = "2026-06-30", date_filter_end = "2026-06-30").\n  - If the current message does NOT state any date, BOTH date_filter_start and date_filter_end MUST be null\n  \u2014 even if the previous turn had a date filter. NEVER reuse, carry forward, or inherit a date filter from\n  previous_conversation_state. The date filter resets to null every turn unless the current message restates it.\n  date_mode \u2014 HOW the date window applies to a promotion\'s lifespan. Set it ONLY when\n  domain_hint = promotion AND a date filter is set this turn; otherwise null. Like the date filter, it comes ONLY from the current message \u2014 never carried over.\n\nPick by the VERB the user attaches to the date, matched by meaning in any language:\n    - "started"  → the user asks what was RELEASED / LAUNCHED / STARTED / came out / is NEW within the window. ("any new promotions in the last 10 days?", "what promos launched in May?")\n    - "ended"    → the user asks what ENDED / EXPIRED / FINISHED / closed within the window. ("which promotions expired last month?")\n    - "overlap"  → the user asks what was VALID / RUNNING / ACTIVE / available / ongoing during the window, or gives a date with no lifecycle verb at all. ("what promotions ran in Q1?", "promotions for June") This is the default \u2014 when unsure, use "overlap".\n\n== POSITIONAL REFERENCES ==\n"Previous response" may state the previous turn returned N records. If the user refers to items by POSITION, list each 1-based position in a top-level "reference_positions" array. Do NOT put positional references in entities, and do NOT invent values \u2014 the position is the reference; downstream resolves it\nagainst the stored result set.\n\n  - single:   "the 3rd one" "the last one"  → [3] / [N]\n  - multiple: "1st and 3rd", "2, 4 and 5"                 → [1,3] / [2,4,5]\n  - ranges:   "the first three", "1 to 3"                 → [1,2,3]\n  - "the last one" = N; "the first two" = [1,2]\n\nreference_positions is ONLY positions into the previous result set. A normal entity the user names (a product, category, customer) still goes in entities as usual \u2014 the\ntwo are independent and can both be present. When no positional reference is made, reference_positions = [].\n\nA BARE NUMBER - or a number that only follows a lead-in word ("for", "number", "item") with nothing else naming it - that is no larger than the previous turn\'s own record count is a POSITION, not an entity, even when some of those same positions were already answered on an earlier turn (the roster stays on screen until its own topic changes). Never emit it as a product/customer entity guess.\n  - Previous response listed 10 products (positions 1 to 10); "okay how about stock, incoming and PO for 7" -> reference_positions [7], entities [] (never {"raw":"7","hint":"product"}).\nreference_positions is ONLY ever set when "Previous response" actually offered a numbered list to answer - a message read with no such list open names no position at all, whatever number it contains.\n\n== REFERENCE TARGET (which set a positional reply means) ==\nWhen "Previous response" contains the marker "[<N> did-you-mean suggestions active]" AND the current message refers to item position(s), also set a top-level "reference_target":\n  - "dym"    -> the reply is bare number(s), one or more ("2", "1, 4", "1 and 4"), OR names a suggestion ("suggestion 2", "the 2nd suggestion").\n  - "result" -> the reply explicitly qualifies a result item ("product 2", "the 2nd one", "the 2nd product", "price of the first").\nIf the marker is absent, set reference_target = null. reference_positions is unchanged (the raw 1-based positions). Do NOT resolve \u2014 downstream maps the positions to the set named by reference_target.\n\n== PERSON-NAME MENTION ==\nIf the user message refers to a person by name \u2014 in any honorific form (Ms, Miss, Mrs, Mr, Encik/En,\nPuan/Pn, Cik, Tuan, Dato/Datin, Dr), any spelling, partial (given name OR surname only), or reversed order \u2014\noutput that surface name string in the top-level "person_mention" key. Otherwise output null.\nALWAYS extract this whenever a person is named, regardless of conversation context or what the previous\nmessage was \u2014 whether it gets used is the downstream code\'s decision, not yours.\n  - Output the user\'s SURFACE wording (e.g. "Ms Tan", "miss tan", "tan", "Tan Wei") \u2014 do NOT correct,\n    normalize, or map it to a number or position.\n  - person_mention is INDEPENDENT of reference_positions: never put a name into reference_positions and never\n    put a number into person_mention. Both may be present; usually only one is.\n  - This is name extraction only. Keep classifying message_type / domain_hint / entities exactly as you\n    normally would; person_mention is additive.\n\n== COMPANY-NAME REPLY ON AN ESCALATION OFFER ==\nWhen the previous assistant message offered an escalation ("Would you like me to escalate ...") or asked\nwhich company to route to, the user may reply with a company name OR its short company code (e.g. "mocha",\n"Sorento team", "the mocha one", "srt", "route to mch", "yes please escalate to srt team", "please escalate\nto sorento team"). Treat such a reply as engaging the pending offer, NOT as a new business query: do not\ninvent a domain_hint or entities from the company name/code alone. Deterministic code resolves which\ncompany was picked; extraction/classification is all you do here.\nCompany codes: Sorento = SRT, Mocha = MCH, Cabana = CBN (case-insensitive; "srt team" = Sorento).\nCompanies OFFERED in the pending offer: the companies the Previous response offered; "(none)" when no offer is pending.\n  - If that list is NOT "(none)" AND the current message names EXACTLY ONE of those companies \u2014 by name OR\n    code, bare or with confirmation/filler/request words around it ("yes mocha", "mocha please", "ok go with\n    sorento", "the mocha one", "route to mocha team", "srt", "yes please escalate to srt team", "please\n    escalate to sorento team") \u2014 set "escalation": { "is_escalation_confirmation": true, "company_pick":\n    "<the CANONICAL company name exactly as listed, e.g. Sorento \u2014 never the code>" }. This applies whatever\n    the message_type you assign (request_for_help, confirmation, casual): naming an offered company while the\n    offer is pending IS the pick. Keep is_affirmative as the AFFIRMATION rule says (a leading "yes" still\n    sets it true). Do NOT set person_mention for a company name.\n  - Otherwise "company_pick" is null: no company named, a company NOT in the offered list (e.g. "yes mocha"\n    when only Sorento is listed → company_pick null; the "yes" still counts as the affirmation), more than\n    one named, or a negated company ("not sorento"). Never guess or resolve \u2014 code validates the pick\n    against the persisted pool and discards it if invalid.\n  - A VALUE IS NOT A FRAGMENT. If the current message names a concrete value you would extract as an\n    entity - a customer/company that is NOT in the offered list, a product code, an order/DO/SPO number,\n    a container, a warehouse - the user is re-scoping the question, not answering the offer. Extract it\n    as you normally would: the value as an entity with current_message = true and its proper hint, the\n    previous turn\'s domain_hint/intent_hint carried, message_type = "business_query", entity_op =\n    "replace_combine", company_pick null, is_affirmative null. A pending escalation offer never swallows\n    a value the user could search on - "for u bath and kitchen" while a member picker is open is a\n    customer filter, not a company pick and not small talk.\n  - Otherwise, a reply during a pending offer that names no company, no listed member, no value of its\n    own and is not a yes/no (a bare fragment, small talk - but NOT the escalate word, which is a yes per\n    AFFIRMATION/ESCALATE-WORD) is NOT a new business query and NOT a decline:\n    classify it casual/unknown with company_pick null and is_affirmative null, and leave domain_hint null.\n\n== ORDER_STATUS FILTER ==\norder_status filters ORDER records by delivery progress. Set it ONLY from the CURRENT\nmessage\'s own words (any language), and ONLY for order / order_enquiries queries; never\ncarry it over from prior state.\n  - "outstanding" -> orders NOT yet delivered: "outstanding", "pending", "not delivered",\n    "undelivered", "not yet delivered", "still pending", "open orders", "belum hantar",\n    "belum sampai", "tak hantar lagi".\n  - "delivered"   -> orders ALREADY delivered: "delivered", "completed", "done",\n    "sudah hantar", "sudah sampai".\n  - null -> DEFAULT. No delivery-status qualifier in the current message -> return all.\nNever infer from context; only the current message\'s explicit words.\nA STATUS VALUE MEANS THE ORDER DOMAIN. Whenever you set "status" to a value at all -\n"outstanding", "so_outstanding", "do_outstanding", "outstanding_both", "delivered", "sales_report" - the\nmessage is asking about ORDER PAPER, and it is the outstanding report that answers it ("sales_report" is answered by the sales report instead; either way the domain is the same).\nNEVER emit domain_hint "inventory" with a status set: "outstanding" is stock that has\nbeen ordered and not delivered, which no stock-on-hand reading can answer. A document\nword moves the domain to that document\'s own ("PO" -> purchase_order, "SPO" ->\nspo_allocation); with no document named it is "order".\n  - "Total outstanding SRTWC8518-SH" -> status "outstanding", document [], domain_hint\n    "order", intent_hint "check_order" (NOT inventory / check_stock)\n  - "outstanding quantity cb2805a" -> status "outstanding", domain_hint "order",\n    requested_attributes ["quantity"] - the same answer "outstanding quantity for\n    hanlim" gets\n  - "total outstanding for SRTWT7445" -> status "outstanding", domain_hint "order"\nREAD FOR MEANING, NOT SPELLING: this is a question about ORDER PAPER even when the\nstatus word itself is misspelled ("oustaning", "oustangind", "outstadning") or the\nattribute word is ("quantiyt", "quantiy") - a garbled spelling of a word this\nsection already names is still that word, never a reason to fall back to\ndomain_hint "inventory". "dealer quantity" is the same attribute as "quantity"\n(WHICH quantity, not a different question), and a bare "... report for <product>"\nbeside a status word asks for the same order-domain answer as "... quantity for\n<product>" does - a report is what the answer is called, not a new attribute.\n  - "oustaning quantity for CB88SS" -> status "outstanding", domain_hint "order",\n    requested_attributes ["quantity"] (NOT inventory / check_stock)\n  - "oustangind dealer quantiyt for CB88SS" -> status "outstanding", domain_hint\n    "order", requested_attributes ["quantity"]\n  - "oustaning report for CB88SS" -> status "outstanding", domain_hint "order"\n  - "how much stock for cb2805a" -> status null, domain_hint "inventory" (no status word)\n\n== IS_ACTIVE FILTER ==\nis_active filters records by lifecycle status. Set it ONLY from the CURRENT message\'s\nown words, matched by meaning in any language; never carry it over from prior state.\n  - false → the user asks for INACTIVE/dead records: "discontinued", "expired",\n    "ended", "obsolete", "no longer available", "old/past promotions",\n    "delisted products". (A product marked discontinued, or a promotion past its end\n    date, is is_active = false.)\n  - true  → the user explicitly asks for ACTIVE/current ones: "active promotions",\n    "current promos", "still available", "ongoing", "valid now".\n  - null  → no lifecycle-status word at all. This is the DEFAULT \u2014 most queries don\'t\n    specify status, and null means "don\'t filter on status".\nOnly a status word sets this. A plain "show me Sorento promotions" → null, NOT true.\n\n== DOCUMENT ==\n"document" is WHICH KIND OF PAPER the message is about, as a list of codes from this\nclosed set and nothing else: "SO" (sales order), "DO" (delivery order), "PO" (purchase\norder), "SPO" (supplier purchase order / shipment), "GRN" (goods receive note). Read it\nfrom the CURRENT message\'s own words, in any language; never carry it over. Default [].\n  - "sales order", "SO", "sales orders" -> ["SO"]\n  - "delivery order", "DO", "delivery orders", "DO list" -> ["DO"]\n  - "purchase order", "PO" -> ["PO"]\n  - "SPO", "SPO allocation" -> ["SPO"]; "shipment" and "container" are incoming\'s own\n    words and name no paper -> []\n  - "GRN", "goods receive", "goods received note" -> ["GRN"]\n  - "both", "SO and DO", "sales order and delivery order" -> ["SO", "DO"]\nA message that names NO paper at all -> [].\nTHE DOCUMENT WORD COUNTS WHEREVER IT SITS IN THE MESSAGE - before the status word, after\nit, beside the product, in the middle of a long sentence, and through a typo in a\nNEIGHBOURING word. The assistant asks which document only when the message named none, so\na document word you drop costs the user an extra question they already answered.\n  - "Outstsnding DO for 7445" -> document ["DO"], status "outstanding" (the typo is in\n    the status word, not the document word)\n  - "Srtwc8518-SH dealer delivery order outstanding how many at BRW?" -> document ["DO"]\n  - "SRTWT7443 sales order outstanding for IB" -> document ["SO"]\n  - "how much SRTWT7445 outstanding both at BRW-IB" -> document ["SO", "DO"]\n  - "what is outstanding for SRTWT7445 at BRW" -> document [] (no paper named)\nA message that is ONLY a paper word is still that paper, and it is a business_query -\nnever casual, never an empty document. It is how a customer switches the paper they are\nlooking at while a question about the other one is on screen.\n  - "Sales order" -> document ["SO"], message_type "business_query", domain_hint "order",\n    intent_hint "check_order"\n  - "DO" / "delivery order" -> document ["DO"], message_type "business_query"\n  - "can show both?" (over an outstanding SO/DO detail offer) -> document ["SO", "DO"], status "outstanding", reference_positions []. "both" over that offer names BOTH PAPERS - it is not a position off the list.\n\n== ASKS (a message that names more than one domain) ==\n"asks" lists EVERY domain the CURRENT message asks about, in the order the message names\nthem, as {"domain": "<domain name>", "intent": "<intent_hint for that domain, or null>"}.\nThe domain names are the ones listed in the domain block at the end of this prompt.\n  - "incoming and stock for 7445" -> asks [{"domain":"incoming","intent":"check_incoming"},\n    {"domain":"inventory","intent":"check_stock"}], domain_hint "incoming"\n  - "purchase cost and stock for SRTWC286" -> asks\n    [{"domain":"purchase_cost","intent":"check_po_cost"},\n    {"domain":"inventory","intent":"check_stock"}], domain_hint "purchase_cost"\ndomain_hint STAYS the FIRST of them, so nothing that reads one domain changes behaviour.\nA message that names ONE domain, or none, emits "asks": null - that is nearly every\nmessage. Never invent a second domain from the previous turn, from the current subject, or\nfrom a domain the answer might usefully include: only the words of THIS message.\n"also", "as well" and "too" ADD the newly named domain to whatever domain the conversation already carries, rather than switching to it: emit asks with the STANDING domain first and the newly named one second, never the new one alone.\n  - "i want stock also" (asked right after an order question) -> asks [{"domain":"order","intent":"check_order"},{"domain":"inventory","intent":"check_stock"}], domain_hint "order".\n\n== TOPIC RESET ==\n"topic_reset" is true when the current message DROPS what the conversation was about and\nstarts something else ("never mind", "forget that", "different question", "ok now\npromotions"). It is about the SUBJECT, not the wording: a refinement, a narrower filter, a\npick off a list and a follow-up question are all the same topic and stay false. Default\nfalse.\n\n== OUTPUT (exactly these keys, no others, no comments) ==\n{\n  "message_type": "request_for_help|business_query|clarification|casual|unknown",\n  "intent_hint": "string_or_null",\n  "domain_hint": "string_or_null",\n  "scope_intent": "broaden|specific|null",\n  "is_affirmative": false,\n  "user_goal": "string_or_null",\n  "continuation": false,\n  "access_levels": [],\n  "broaden_axis": "date|<entity hint>|all|null \u2014 the ONE axis the user is widening this turn; see BROADEN AXIS",\n  "broaden_to": "family|all|null - how far that axis is widened; see BROADEN AXIS",\n  "date_mode": "overlap|started|ended|null \u2014 only for promotion domain with a date filter, else null",\n  "date_filter_start": "start date of filter if user explicitly mentions in the current message",\n  "date_filter_end": "end date of filter if user explicitly mentions in the current message",\n  "match_mode": "and|or",\n  "demand_qty": null,\n  "entities": [],\n  "entity_op": "clear|replace_combine|replace|modify|reuse",\n  "domain_in_message": false,\n  "requested_attributes": []\n  "contains_flyer": false,\n  "reference_positions": [],\n  "reference_target": "result|dym|null \u2014 WHICH set a positional reply targets; null unless a [<N> did-you-mean suggestions active] marker is present (see REFERENCE TARGET)",\n  "person_mention": "string_or_null \u2014 the surface name the user mentioned, else null",\n  "is_active": "true|false|null \u2014 lifecycle status filter from current message only, else null",\n  "order_status": "outstanding|delivered|null \u2014 ORDER delivery-status filter from current message only (order domain), else null",\n  "correction": false,\n  "routing": {\n    "suggested_team": "purchasing|purchasing_certification|customer_service|marketing_product|marketing_form|warehouse|marketing_promotion|it_admin",\n    "suggested_agent": "general_enquiries|order_enquiries|incoming_stock_enquiries|marketing_form|it_support"\n  },\n  "escalation": { "is_escalation_confirmation": false, "company_pick": "string_or_null \u2014 ONLY per COMPANY-NAME REPLY ON AN ESCALATION OFFER, else null" },\n  "document": [], the papers the message named: SO|DO|PO|SPO|GRN codes, else []\n  "status": "outstanding|delivered|sales_report|null \u2014 the delivery/order status axis from the current message only, else null; it is sales_report on exactly the turns order_status is sales_report (see SALES REPORT below)",\n  "asks": [] one {"domain","intent"} per domain the message names, ONLY when it names more than one; see ASKS\n  "topic_reset": false, true only when the message drops the current subject for a new one; see TOPIC RESET\n  "anaphora": { "backward_reference": false }\n}\nReturn exactly one JSON object.' + GROWTH_R1_ADDENDUM + LAST_COST_ADDENDUM + LOW_STOCK_ADDENDUM + SALES_REPORT_ADDENDUM + STOCK_TASK_ADDENDUM + SALES_ANALYSIS_ADDENDUM + SPECIFICATION_ADDENDUM + QUANTITY_ADDENDUM + KNOWN_BRANDS_ADDENDUM + ESCALATION_CONFIRMATION_ADDENDUM

# PLAN-chatbot-top-x-hot-selling-24sep.md, "Parser (S4)". ADDITIVE ONLY, appended for
# the same reason `SALES_REPORT_ADDENDUM` above is: the FULL body is a mechanical
# derivation of the live n8n message, and a trailing block is the only edit shape that
# keeps that derivation true. Teaches `order_status: "top_selling"` and the three new
# nullable output keys `rank_by`, `basis`, `rank_group`.
#
# Published as a new UNLABELLED version of `chatbot_semantic_parser` by migration
# `chatbot_top_selling_vocab`, the same immutable-versions-plus-movable-labels split as
# 519. Nothing reaches a customer until the owner moves the `production` label.
TOP_SELLING_ADDENDUM = '\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nTOP SELLING\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nEverything below is IN ADDITION to the OUTPUT object above. Emit these four keys on\nevery object, exactly as if they were listed there:\n\n  "rank_by": "quantity|amount|null",\n  "basis": "delivered|ordered|unclear|null",\n  "rank_group": "item|category|unclear|null",\n  "rank_direction": "top|bottom|null"\n\n== TOP SELLING: order_status "top_selling", domain_hint "order" ==\n"which items sell the most" is a RANKING of items (or categories) by what was sold - a\nDIFFERENT ask from "sales_report" (one customer\'s or product\'s figures by month) and from\na plain order list.\n  - "top 5 selling items", "best selling", "hot selling", "top items", "most sold",\n    "what is selling well", "which category sells most", "barang paling laku",\n    "paling laris", "畅销", "卖得最好" -> order_status "top_selling", domain_hint "order",\n    intent_hint "check_order"\n  - "top 100 sold item", "top 100 sold items", "most sold item", "best selling",\n    "top selling", "top 100 hot selling item", "highest selling", "top sellers", "top\n    sold", "best seller" -> the same: order_status "top_selling", domain_hint "order",\n    intent_hint "check_order", top_n from the number where one is named\n"sold" or "sold items" is the ranking whenever it comes with "top", "most", "best" or\n"highest": NEVER an order list (order_status null or "all").\n"top 5" or "last 3" with NO selling word under an order or stock question stays what it\nwas: top_n on that list ("last 3 DO for hanlim" is an order list with top_n 3, never\ntop_selling).\n\n== TOP_N on a top selling ask ==\nThe number of rows asked for, from the CURRENT message only ("top 10" -> 10, "top 100"\n-> 100). No number -> null; NEVER invent one (the bot asks how many itself).\n\n== RANK_BY: what the ranking is by ==\n  - "by quantity", "by qty", "qty", "quantity", "most units", "ikut kuantiti", "按数量"\n    -> "quantity"\n  - "by amount", "by amt", "amt", "amount", "by value", "by sales value", "by RM",\n    "ikut nilai", "按金额" -> "amount"\n  - the message names neither -> null (the bot asks; NEVER guess)\n\n== BASIS: which sale is counted ==\n  - "delivered", "transferred to DO", "sudah hantar" -> "delivered"\n  - "ordered", "by order", "booked", "tempahan" -> "ordered"\n  - a word that could mean either, e.g. "top orders" -> "unclear"\n  - "selling" / "sold" with no basis word -> null (the default, delivered)\n\n== RANK_GROUP: what is ranked ==\n  - items or products ranked ("top selling items", "top 5 sinks") -> "item"\n  - categories ranked against each other ("which category sells most", "top categories")\n    -> "category"\n  - a category word that could be EITHER a filter or the ranking itself ("top selling by\n    category") -> "unclear"\n  - the message says nothing about it -> null\nA NAMED category ("top 3 kitchen sinks", "top items in category KITCHEN SINK") is a FILTER:\nrank_group "item", and the category is an entity {hint: "category", hint_confident: true}.\nA named customer stays an entity {hint: "customer"}, exactly as anywhere else.\n\n== ANSWERING THE BOT\'S OWN TOP SELLING QUESTION ==\nWhen "Previous response" is one of the bot\'s top selling questions, the CURRENT message\nanswers it. Emit order_status "top_selling", domain_hint "order", message_type\n"business_query", domain_in_message false, and ONLY the key it answers (everything else\nnull, entities []). The answer is never a new ask and never an order list: "amount"\nafter "By quantity or by amount?" is rank_by "amount", never order_status "all" or a\nlist of orders.\n  - after "By quantity or by amount?": "qty" or "quantity" -> rank_by "quantity"; "amt"\n    or "amount" -> rank_by "amount"; "1" -> rank_by "quantity" and "2" -> rank_by\n    "amount" (the order the question lists them), never reference_positions\n  - after "Do you want the top items inside one category, or the categories ranked\n    against each other?": "categories" -> rank_group "category"; "items" -> rank_group\n    "item"; a category name -> rank_group "item" plus that category entity\n  - after "Delivered (transferred to DO) or ordered?": "ordered" -> basis "ordered"\n  - after "How many items do you want to see?": "20" or "top 20" -> top_n 20 (a count,\n    NEVER reference_positions)\nA follow-up to a ranking already shown that only changes the metric, the basis or the\ncount ("by amount", "ordered", "top 20") is the same: order_status "top_selling" and only\nthe key it changes.\nAn answer that ALSO carries more of the ask is read whole: "amount, top 100, water\ncloset" -> rank_by "amount", top_n 100 and the entity {raw: "water closet", hint:\n"category", hint_confident: true}. Never drop the extra keys because the message answers\na question.\nA bare number after a ranked list ("2") picks that row: reference_positions [2], exactly\nas for any other numbered list. ONLY a whole number from 1 to the length of the list is a\npick. A year ("2025", "2025?", "in 2025", "what about 2025", "i mean in year 2025") after a\nranked list is the PERIOD: order_status "top_selling", date_filter_start "2025-01-01",\ndate_filter_end "2025-12-31", NEVER reference_positions. A number with words or a question\nmark, or a number past the end of the list, is a new message read against the ranking on\nscreen, never a pick.\n\n== ANSWERING "CUSTOMER OR SALES AGENT?" ==\nWhen "Previous response" is "Do you mean customer X or sales agent Y? Reply 1 for the\ncustomer, 2 for the sales agent.", the CURRENT message answers THAT question: message_type\n"business_query", order_status "top_selling", domain_hint "order", entities [], and:\n  - "2", "sales agent", "agent", "yeah sales agent", "i mean sales agent sean", "neither,\n    i mean sales agent sean", "fanny sales agent" -> reference_positions [2]\n  - "1", "customer", "the customer", "yes the customer" -> reference_positions [1]\nIt is NEVER low_signal, never a new ask, never a promotion or document lookup, and the\nwords of the earlier ask are never repeated as entities.\n\n== RANK_DIRECTION: most sold first or least sold first ==\n  - "cold selling", "least sold", "worst selling", "slowest", "slow moving", "bottom 10",\n    "paling kurang laku", "卖得最差" -> "bottom", and the message is a top selling ask\n    (order_status "top_selling"); "bottom 10" also sets top_n 10\n  - "worst 100 hot selling bathtub", "worse 100 hot selling", "worst selling", "bottom\n    100" -> rank_direction "bottom", order_status "top_selling", top_n 100 where a number\n    is named, the category an entity. A spelling slip ("worsr", "wrost") is the same word.\n    NEVER out_of_scope, never an escalation.\n  - "top", "hot selling", "best selling" -> "top"\n  - the message says nothing about it -> null (most sold first)\n\n== NARROWING A TOP SELLING ASK ==\nWhen "Previous response" is a top selling ranking or one of its questions, a message that\nonly names who sold it, a category, a brand or a customer NARROWS that ranking: it is a\nrefinement, message_type "business_query", order_status "top_selling", domain_hint\n"order", domain_in_message false (it names no ask of its own), and the word goes into\nentities with the hint below. It never starts an order\nlookup, and it is never an escalation.\n  - SALES AGENT, the person who sold it: "sold by fanny", "sales agent is fanny", "by\n    agent fanny", "agent fanny", "salesman fanny", "jurujual fanny" -> entity {raw:\n    "fanny", hint: "sales_agent", hint_confident: true}. A person named after "sold by"\n    or "agent" is the SALES AGENT, NEVER a customer.\n  - CATEGORY, the kind of product: "which is water closet", "water closet only", "product\n    category is water closet", "for water tap", "toilet only", "kitchen sinks" -> entity\n    {raw: "water closet", hint: "category", hint_confident: true} (the product words\n    only, without "only", "for", "which is" or "category is").\n  - BRAND, a name from "Known brands" or "Sorento", "Mocha", "Cabana": "for sorento\n    brand", "sorento only", "cabana" -> entity {raw: "sorento", hint: "brand",\n    hint_confident: true}. A brand name is NEVER a customer, even though customers\n    carry the same word in their names.\n  - CUSTOMER, a company that bought: "for hanlim", "for chin chun only" -> entity {raw:\n    "hanlim", hint: "customer"}, exactly as anywhere else.\nA CORRECTION of that ranking ("no", "i mean", "not the customer") sets correction true and\nstates the axis it changes. "customer is everyone", "all customers", "any customer",\n"everyone" -> broaden_axis "customer", broaden_to "all" (the customer filter is cleared,\nnever kept). The same for the other axes: "any agent" -> broaden_axis "sales_agent",\n"all categories" -> broaden_axis "category", "all brands" -> broaden_axis "brand", each\nwith broaden_to "all".\n  - "hmm no, customer is everyone, but sales agent is fanny" -> correction true,\n    broaden_axis "customer", broaden_to "all", entity {raw: "fanny", hint:\n    "sales_agent", hint_confident: true}\nA NAME NEXT TO "AGENT" is the sales agent, whatever else the message says: "top 100 hot\nselling bathtub by sean sales agent", "by sean salea agent", "fanny sales agent" -> entity\n{raw: "sean", hint: "sales_agent", hint_confident: true}; a slip of "sales" ("salea",\n"saless") is never part of the name.\nONE PHRASE CARRYING SEVERAL THINGS is split into one entity each, never kept whole:\n"fanny water closet" -> {raw: "fanny", hint: "sales_agent"} and {raw: "water closet",\nhint: "category"}; "bathtub by sean", "sean bathtub" -> {raw: "bathtub", hint: "category"}\nand {raw: "sean", hint: "customer"}; "sorento water closet" -> {raw: "sorento", hint:\n"brand"} and {raw: "water closet", hint: "category"}. Inside a ranking such a phrase is\nnever a promotion or a document.\n\n== FROM A RANKING TO ANOTHER ORDER REPORT ==\nWhen "Previous response" is a top selling ranking, a message asking for another order\nreport runs THAT report; the bot carries the ranking\'s filters itself, so the message\nnames only its own words (entities [], never the ranking\'s category, brand or agent):\n  - "outstanding", "the outstanding", "show me outstanding" -> domain_hint "order",\n    intent_hint "check_order", order_status "outstanding", domain_in_message true\n  - "can show me the DO", "the DO" -> domain_hint "order", intent_hint "check_order",\n    document ["DO"], domain_in_message true\n  - "show me the orders" -> domain_hint "order", intent_hint "check_order",\n    domain_in_message true\nIt is never out_of_scope and never an escalation.\n'

# PLAN-chatbot-customer-scope-29sep.md, D2. ADDITIVE ONLY, stacked beneath
# `ESCALATION_CONFIRMATION_ADDENDUM` (which stays the tail) the way `TOP_SELLING_ADDENDUM` is. Teaches the boolean `self_reference`.
# Published as a new UNLABELLED version by migration `chatbot_self_reference_vocab`.
SELF_REFERENCE_ADDENDUM = """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SELF REFERENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Everything below is IN ADDITION to the OUTPUT object above. Emit this key on every
object, exactly as if it were listed there:

  "self_reference": true|false

== SELF_REFERENCE: the asker means their OWN account ==
true when the message asks about the asker's own orders, delivery, outstanding, account or
figures, by a first-person word used as the asker's own account (not the company):
  - "my", "mine", "me", "our", "ours", "us" ("what's my outstanding", "my DO", "where is
    my shipment", "show me my orders", "what did we order", "our orders in September")
  - "saya punya", "kami punya", "punya saya", "punya kami" ("outstanding saya punya",
    "order kami punya")
  - "我的", "我们的" ("我的订单", "我们的欠款")
"where is my shipment" keeps its ETA reading AND is self_reference true.
"we" or "us" as the ASKER'S BUSINESS ("what did we order") is self_reference true. "do
you / can you" about Sorento itself stays the company rule: false.
false when the message names another party ("outstanding for hanlim") or no one ("list
outstanding DO", "top selling items"). Never guess: no first-person word about an account,
no true. A name in the message stays an entity exactly as anywhere else.
"""

# PLAN-po-spo-warehouse-29sep S3. ADDITIVE ONLY, appended for the same reason
# `TOP_SELLING_ADDENDUM` above is. Teaches that the word SPO is an SPO allocation ask (never
# `incoming`), that a short token under a PO or SPO ask is a warehouse, and the two output
# keys `sort_by` / `sort_dir`. Stacked between ESCALATION_CONFIRMATION_ADDENDUM and
# MEMORY_ADDENDUM (MEMORY stays the tail). Published as a new unlabelled version of
# `chatbot_semantic_parser` by migration `chatbot_po_spo_warehouse_vocab`.
PO_SPO_WAREHOUSE_ADDENDUM = """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PO, SPO AND THE WAREHOUSE, AND HOW TO SORT THEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Everything below is IN ADDITION to the OUTPUT object above. Emit these two keys on every
object, exactly as if they were listed there:

  "sort_by": "date|expected_date|quantity|outstanding|received_date|received_quantity|product|supplier|null"
  "sort_dir": "asc|desc|null"

== THE WORD SPO IS AN SPO ALLOCATION ASK: domain_hint "spo_allocation", intent_hint "check_spo" ==
"SPO", "SPO for X", "SPO SRT79-SS", "any SPO", "SPO allocations", "SPO at BRW" and
"SPO for BRW-BB" are all domain_hint "spo_allocation", intent_hint "check_spo", document
["SPO"]. NEVER "incoming" for the word SPO. "incoming" needs an ARRIVAL word (ETA, arriving,
shipment, container, incoming). "last in" keeps its own cue above.
  - "SPO for BRW-BB" -> domain_hint "spo_allocation", intent_hint "check_spo",
    entities [{"raw": "BRW-BB", "hint": "warehouse"}]
  - "SPO SRT79-SS" -> domain_hint "spo_allocation", intent_hint "check_spo",
    entities [{"raw": "SRT79-SS", "hint": "product"}]

== UNDER A PO OR SPO ASK, A SHORT TOKEN IS A LOCATION TOO ==
Under domain_hint "purchase_order" or "spo_allocation", a token of either shape below that
follows "to", "at", "in" or "for", or stands beside the product, is a warehouse entity
(hint "warehouse"), not a product:
  (a) at most 4 characters, letters or letters with one digit ("BRW", "HQ", "W2");
  (b) a hyphenated site code of at most 10 characters ("BRW-BB").
  - "PO to BRW" -> domain_hint "purchase_order", intent_hint "check_po",
    entities [{"raw": "BRW", "hint": "warehouse"}], no product
  - "last in SRTWC286 at BRW" -> entities [{"raw": "SRTWC286", "hint": "product"},
    {"raw": "BRW", "hint": "warehouse"}]

== SORT_BY AND SORT_DIR: the order the customer asked for ==
From the CURRENT message only, never carried. Default null for both.
  - "latest first", "newest", "most recent", "latest PO" -> sort_by "date", sort_dir "desc"
  - "oldest first" -> sort_by "date", sort_dir "asc"
  - "by PO date", "by SPO date", "by doc date", "by date" -> sort_by "date"
  - "by expected date", "by ETA" -> sort_by "expected_date"
  - "biggest quantity first", "largest qty", "most quantity" -> sort_by "quantity", sort_dir "desc"
  - "smallest quantity first" -> sort_by "quantity", sort_dir "asc"
  - "most outstanding", "by outstanding" -> sort_by "outstanding"
  - "by GR date", "by received date" -> sort_by "received_date"
  - "by received qty" -> sort_by "received_quantity"
  - "by product", "by product code" -> sort_by "product"
  - "by supplier" -> sort_by "supplier"
"last in", "last received", "last 3 in" name NO sort (sort_by null): the SPO LAST RECEIPT
rule above answers them by the SPO date, received or not.
A direction word alone with no field ("latest first", "oldest first") sorts the document
date. No direction word -> sort_dir null. "by amount", "by value", "by RM" on a PO or SPO
ask -> sort_by null, and NEVER rank_by (there is no amount to sort by). Every ask outside
PO and SPO leaves both keys null.
  - "latest PO for SRT79-SS" -> domain_hint "purchase_order", intent_hint "check_po",
    entities [{"raw": "SRT79-SS", "hint": "product"}], sort_by "date", sort_dir "desc"
  - "SPO biggest quantity first at BRW" -> domain_hint "spo_allocation", intent_hint
    "check_spo", entities [{"raw": "BRW", "hint": "warehouse"}], sort_by "quantity",
    sort_dir "desc"
  - "oldest PO first" -> sort_by "date", sort_dir "asc"
  - "SPO by GR date" -> sort_by "received_date"
  - "PO by supplier" -> sort_by "supplier"
  - "SPO by amount" -> sort_by null

== A SORT WORD ALONE RE-SORTS THE LIST ON SCREEN ==
When the Previous response is a PO or SPO list and the message only names a sort
("biggest quantity first", "oldest first", "sort by date"), it is a refinement:
message_type "business_query", domain_hint null, intent_hint null, domain_in_message false,
entities [], only sort_by / sort_dir set.
  - "biggest quantity first" -> {"domain_hint": null, "entities": [], "sort_by": "quantity",
    "sort_dir": "desc"}
"""

# Main's literal now carries ESCALATION_CONFIRMATION_ADDENDUM as its tail (#1323), so it
# is peeled off, TOP_SELLING_ADDENDUM goes on beneath it, and it is put back as the tail.
SEMANTIC_PARSER_PROMPT = SEMANTIC_PARSER_PROMPT.removesuffix(ESCALATION_CONFIRMATION_ADDENDUM)
SEMANTIC_PARSER_PROMPT += TOP_SELLING_ADDENDUM
SEMANTIC_PARSER_PROMPT += SELF_REFERENCE_ADDENDUM
SEMANTIC_PARSER_PROMPT += ESCALATION_CONFIRMATION_ADDENDUM
SEMANTIC_PARSER_PROMPT += PO_SPO_WAREHOUSE_ADDENDUM
SEMANTIC_PARSER_PROMPT += ACCOUNT_LEDGER_ADDENDUM
SEMANTIC_PARSER_PROMPT += LOW_STOCK_FILTERS_ADDENDUM
SEMANTIC_PARSER_PROMPT += MEMORY_ADDENDUM

# S1b (D16, AC-151 to AC-155): the same parser, 40.0% fewer characters (28,124 against the
# live 46,906). Classified section by section in
# `documentation/plans/chatbot/parser-prompt-inventory.md`.
#
# Every section classified `rule` is gone, because `output_exchange.py` already decided it
# and the prompt was only stating a second, weaker copy: the domain-to-team map (the live
# body reads the model's own team ONLY on a `request_for_help` turn, and `derive_routing`
# supplies the identical answer whenever a domain is set, so deleting the map changes no
# output), the `message_type` force to `business_query`, the `attachment_type` drop on
# `master_products`, the `broaden_axis` domain restore, the brand-plus-tier access-level
# map, and the legacy promotion-team suffix. Four `dead` sections went with them: PRICE
# TERMINOLOGY and ATTACHMENT TYPE EXTRACTION were verbatim duplicates of rows further down,
# READING THE CURRENT MESSAGE IN CONTEXT stated no output rule, and the "Companies OFFERED"
# line was an n8n `{{ (() => ...)() }}` expression the CRM's prompt registry does not
# evaluate, so it reached the model as 700 characters of literal JavaScript.
#
# What did NOT move is as deliberate. Date maths stays: both candidate gates (single-day
# start/end fill, `date_mode` restricted to `promotion`) were measured against the replay
# corpus and both would have rewritten fixtures the shipped post-processor deliberately
# produces. `demand_qty` stays: reading a number out of a sentence needs a regex over the
# customer's words, which D11 forbids. Language coverage stays whole, and every one of the
# 26 output keys and every enum is still declared here unchanged, because
# `head/parser.py`'s strict `json_schema` and `chatbot/contracts.py` are written against
# them.
#
# Six edits in this text were earned by a live parity sweep rather than by reading, and
# each is load-bearing: `date_mode` needs its explicit "otherwise null" (without it the
# model set it on 15 of 76 non-promotion turns); `entity_op: reuse` needs "downstream
# re-applies the previous entities" (without it a bare "Escalate" re-emitted the previous
# turn's product and dragged the domain back with it); `attachment_type` needs "ALWAYS emit
# TWO entities" stated in the DECISIVE row, not only in the hint list (without it
# "TECHNICAL DRAWING SRTUB206" came back with the product alone); `order_status` needs
# "never infer from context"; the `order` row needs "delivery meaning fulfilment belongs
# HERE, never incoming"; and `is_escalation_confirmation` has to be named beside the
# company pick, because saying nothing let the model set it true on "Yes escalate" and
# `route.py` reads that field. `scope_intent` and `user_goal` are also kept in the live
# prompt's own words and the live prompt's own position: compressed and moved, their
# agreement measured 26% and 52%; restored, 85% and 80%.
# RETIRED (chatbot turn re-architecture S0, AC-1506): the slim body this comment block
# describes is no longer published from here. Its exact text lives on, byte-identical,
# in alembic/_legacy_prompt_bodies.py - imported only by the migrations that already
# published labelled versions of it (475, 480, 487, 490, 513, 514, 517).


from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - a type-only import, never at runtime
    from collections.abc import Mapping

    from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# The generated blocks (chatbot turn re-architecture S4, AC-1550)
#
# `chatbot_domains` and `chatbot_entity_kinds` are the policy (AC-1501/AC-1502), and the
# parser has to be TOLD what they say. Rendering the blocks from the rows means adding a
# domain is a row plus a publish, not a prompt edit that can disagree with the table the
# engine actually routes on - which is the whole point of moving the constant into a
# table. Rendered at PUBLISH time, never per turn: the prompt a customer's turn runs on
# is an immutable version, and a row saved this morning must not silently change the
# words yesterday's version was graded against. AC-1552's staleness banner is what tells
# the owner the two have diverged.
# --------------------------------------------------------------------------- #

BLOCKS_BEGIN = "<<<CHATBOT POLICY BLOCKS>>>"
BLOCKS_END = "<<<END CHATBOT POLICY BLOCKS>>>"


_DOMAIN_LINE_COLUMNS = (
    "name, label, intents, switch_words, narrowing, takes_date_filter, "
    "escalation_team_code"
)


def domain_line(row: "Mapping[str, object]") -> str:
    """One domain's paragraph, exactly as the published prompt carries it.

    Its own function so the Chatbot Domains modal's "Prompt block" tab can render THE
    line rather than a lookalike: the modal used to build its own preview in TypeScript,
    which is a second copy of this wording that nothing keeps in step with this one.
    """
    intents = ", ".join(row["intents"] or []) or "(none)"
    words = ", ".join(row["switch_words"] or []) or "(none)"
    parts = [f'Domain {row["name"]} ("{row["label"]}"): intents {intents}. Switch words: {words}.']
    narrowing = row["narrowing"] or {}
    if narrowing:
        clauses = "; ".join(f"{kind} narrows {narrowing[kind]}" for kind in sorted(narrowing))
        parts.append(f"{clauses}.")
    if row["takes_date_filter"]:
        parts.append("Takes a date window.")
    if row["escalation_team_code"]:
        parts.append(f'Escalates to {row["escalation_team_code"]}.')
    return " ".join(parts)


def domain_block(db: "Session", name: str) -> str | None:
    """`domain_line` for one row by name, or None when no such domain exists."""
    from sqlalchemy import text as sql

    row = db.execute(
        sql(f"SELECT {_DOMAIN_LINE_COLUMNS} FROM chatbot_domains WHERE name = :name"),
        {"name": name},
    ).mappings().first()
    return domain_line(row) if row is not None else None


def render_prompt_blocks(db: "Session") -> str:
    """The domain and entity-kind paragraphs, one per line, deterministic.

    Domains in `(sort_order, name)` order, entity kinds in `kind` order, because the
    rendered text is hashed and compared: any ordering that depends on how Postgres felt
    would report the prompt stale on every other read.
    """
    from sqlalchemy import text as sql

    lines: list[str] = []
    domains = db.execute(
        sql(
            f"SELECT {_DOMAIN_LINE_COLUMNS} FROM chatbot_domains ORDER BY sort_order, name"
        )
    ).mappings().all()
    for row in domains:
        lines.append(domain_line(row))

    lines.append("")

    kinds = db.execute(
        sql(
            "SELECT kind, resolver_source, did_you_mean, default_narrowing "
            "FROM chatbot_entity_kinds ORDER BY kind"
        )
    ).mappings().all()
    for row in kinds:
        lines.append(
            f'Entity kind {row["kind"]}: resolver {row["resolver_source"]}. '
            f'Did-you-mean {"on" if row["did_you_mean"] else "off"}. '
            f'Default narrowing {row["default_narrowing"]}.'
        )

    # No separator when the registry renders nothing (an empty registry, since #1286
    # dropped the Brand specification row): the blocks then end on the kinds.
    spec = specification_lines(db)
    if spec:
        lines.append("")
        lines.extend(spec)

    return "\n".join(lines) + "\n"


#: Registry keys the specification block leaves out: the class and the type are what the
#: product IS (the category entity carries them), and the brand is its own entity kind.
SPECIFICATION_BLOCK_SKIP = frozenset({"class", "brand", "product_type"})


def specification_lines(db: "Session") -> list[str]:
    """The `specification` entity kind's vocabulary, one line per registry key (fix
    round 8 on PR #833, owner retest of round 7: "is it the parser is not bounded by what
    the system has as a spec?").

    Rendered from the specification registry exactly the way the domain and entity-kind
    lines are rendered from their tables, through the registry's own readers
    (`active_registry`, `merged_synonyms`, `merged_allowed_values`, `display_spec_value`):
    the key, its label, and per choice its display name and the words customers use for
    it; a numeric key's unit and its own words; a yes-or-no key's words. Staff editing a
    choice or a word on Product Specifications reaches the parser at the next publish,
    with no prompt edit. Keys in `(spec_key)` order so the hash is stable. The product
    types close the block as the words the category entity keeps."""
    from app.services.product_spec_registry import (
        active_registry,
        display_spec_value,
        merged_allowed_values,
        merged_synonyms,
    )

    lines: list[str] = []
    types: list[str] = []
    for row in active_registry(db):
        synonyms = merged_synonyms(row)
        labels = dict(getattr(row, "value_labels", None) or {})
        if row.spec_key == "product_type":
            types = [display_spec_value(v, labels) for v in dict.fromkeys(merged_allowed_values(row))]
            continue
        if row.spec_key in SPECIFICATION_BLOCK_SKIP:
            continue
        data_type = (row.data_type or "").lower()
        own = ", ".join(synonyms.get("_self") or [])
        head = f'Specification {row.spec_key} ("{row.label}")'
        if data_type == "numeric":
            unit = f" in {row.unit}" if row.unit else ""
            worded = [
                f"{value} ({', '.join(words)})" for value, words in synonyms.items() if value != "_self" and words
            ]
            text = f"{head}: a number{unit}."
            if own:
                text += f" Words: {own}."
            if worded:
                text += f" Said in words: {'; '.join(worded)}."
            lines.append(text)
            continue
        if data_type == "boolean":
            words = ", ".join(w for value, ws in synonyms.items() if value != "_self" for w in ws)
            lines.append(f"{head}: yes or no." + (f" Words: {words}." if words else ""))
            continue
        choices = []
        for value in dict.fromkeys(merged_allowed_values(row)):
            words = [w for w in synonyms.get(value) or [] if w]
            shown = display_spec_value(value, labels)
            choices.append(f"{shown} = {value}" + (f" ({', '.join(words)})" if words else ""))
        if choices:
            lines.append(f"{head}: choices {'; '.join(choices)}.")
    if types:
        lines.append(f"Product types (the category entity keeps these, never a specification): {', '.join(types)}.")
    return lines


def prompt_blocks_hash(db: "Session") -> str:
    """The sha256 of the rendered blocks - what a published version records so the
    Prompts page can say whether the rows have moved since (AC-1552)."""
    import hashlib

    return hashlib.sha256(render_prompt_blocks(db).encode("utf-8")).hexdigest()


def blocks_of(template: str | None) -> str:
    """The blocks a published body carries, or the whole body when it is nothing else.

    A published version wraps them in `BLOCKS_BEGIN`/`BLOCKS_END` so the rest of the
    prompt can change without reading as a policy change. A body that is ONLY the blocks
    (a hand-published one) carries no markers, and is its own answer.
    """
    body = template or ""
    if BLOCKS_BEGIN in body and BLOCKS_END in body:
        return body.split(BLOCKS_BEGIN, 1)[1].split(BLOCKS_END, 1)[0].strip("\n") + "\n"
    return body
