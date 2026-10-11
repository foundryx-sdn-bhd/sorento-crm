"""Build the PROMPT-ABLATION trimmed parser body (language teaching out).

Starts from the stored v50 text (`alembic/data/chatbot_semantic_parser.v50.txt`, never edited) and
applies the cut list of `documentation/plans/chatbot/PLAN-prompt-ablation-11oct.md`. Each cut is an
exact (id, old, new) replacement; `old` must occur exactly once in the text at the moment it is
applied. A PLAN cut that needs several spans carries suffixed ids (C15a, C15b ...); `--restore C15`
restores every part of C15. Writes `alembic/data/chatbot_semantic_parser.ablation-20261011.txt`.

Run from sorento_crm_backend/:  venv/bin/python -m scripts.build_parser_ablation [--restore C08,C15]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

SOURCE = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.v50.txt"
OUTPUT = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.ablation-20261011.txt"

#: (cut_id, old, new) exact replacements, applied in order.
CUTS: list[tuple[str, str, str]] = [
    # C01 AFFIRMATION: escalate-word spelling list.
    (
        "C01",
        """, in any language, case or short form, with or without filler
("ESCALATE", "escalate", "escalate please", "pls escalate", "ok escalate", "escalate this", "eskalasi",
"升级", "escalate to the team")""",
        """, in any form ("ESCALATE", "escalate please")""",
    ),
    # C02 READING: number-words bullet.
    (
        "C02",
        """
  - A number written as a WORD is still a position: "one", "two", "three", "eight", "the
    eighth one", the Malay "satu", "dua", "lapan" and the Chinese numerals all go to
    reference_positions exactly as "8" does (see POSITIONAL REFERENCES).""",
        "",
    ),
    # C03 DECISIVE DOMAIN TERMS: any-language boilerplate, resource_attachment and ideate lists.
    (
        "C03a",
        """ \u2014 in ANY
language, paraphrase, or misspelling, matched by MEANING not spelling \u2014 SET that""",
        """, SET that""",
    ),
    (
        "C03b",
        """DECISIVE in any language,
      paraphrase, or misspelling \u2014 "warranty", "katalog", "warranty Sorento", "price
      tag template", "container status list", "senarai status kontena", "shipping schedule".""",
        """DECISIVE: "warranty", "warranty Sorento", "price tag template", "container status list", "shipping schedule".""",
    ),
    (
        "C03c",
        """"Mocha warranty", "katalog Sorento")""",
        """"Mocha warranty")""",
    ),
    (
        "C03d",
        """, "can you add…", "you should let us…", "cadangan", "saya ada
      idea", "why don't we…", "suggestion: …".""",
        """.""",
    ),
    # C04 DECISIVE order bullet.
    (
        "C04a",
        """"delivery order", "DO",
      "hantar" about a customer.""",
        """"delivery order", "DO".""",
    ),
    (
        "C04b",
        """"delivery list", or "belum hantar" (undelivered""",
        """"delivery list" (undelivered""",
    ),
    (
        "C04c",
        """, "X bought Y", "X ada ambil Y".""",
        """, "X bought Y".""",
    ),
    # C05 DOMAIN IN MESSAGE.
    (
        "C05",
        """, 卖得最多, 排名 -""",
        """ -""",
    ),
    # C06 REQUESTED ATTRIBUTES.
    (
        "C06a",
        """"what quantity", "berapa", and""",
        """"what quantity", and""",
    ),
    (
        "C06b",
        """, 到仓库, sampai gudang""",
        """""",
    ),
    (
        "C06c",
        """ / "X什么时候会到仓库" / "X什么时候到仓库" / "bila X sampai gudang" / "bila X masuk gudang" ->""",
        """ ->""",
    ),
    (
        "C06d",
        """ / "X什么时候到" / "bila X sampai" -> []""",
        """ -> []""",
    ),
    (
        "C06e",
        """ / "X的状态" / "status kontena X" ->""",
        """ ->""",
    ),
    # C07 MESSAGE TYPE.
    (
        "C07a",
        """"escalate to <team>", "eskalasi", "升级" \u2014 the""",
        """"escalate to <team>" \u2014 the""",
    ),
    (
        "C07b",
        """business_query. request_for_help is ONLY a request for a HUMAN. A message that names a customer or product next to a delivery / order / DO word ("delivery to hanlim", "hantar ke hanlim", """,
        """business_query. request_for_help is ONLY a request for a HUMAN. A message that names a customer or product next to a delivery / order / DO word ("delivery to hanlim", """,
    ),
    # C08 ENTITY OPERATIONS: translation parentheticals.
    (
        "C08a",
        """(katalog→catalogue;
      jaminan/waranti→warranty; senarai status kontena / container status report /""",
        """(container status report /""",
    ),
    (
        "C08b",
        """(gambar/foto→photo; imej→image; lukisan teknikal/spec→technical drawing; model 3D→3D model; sijil/cert→certificate)""",
        """(spec→technical drawing; cert→certificate)""",
    ),
    # C09 WHAT IS NEVER AN ENTITY: grammar particles paragraph + "de" example (ruling Q4).
    (
        "C09",
        """A GRAMMAR PARTICLE or a filler word is never an entity, in any language: "de", "lah",
"ah", "leh", "loh", "the", "got", "的", "吗", "啊". They carry no referent at all, so they
never appear in entities[] under any hint.
  - "i want to see for srtwc286 de" -> entities [{"raw":"srtwc286","hint":"product"}] and
    nothing else. "de" is not a warehouse.
""",
        "",
    ),
    # C10 ENTITY CONFIDENCE: whole section -> short definition. The entity_op paragraph that
    # follows is kept whole under its own header so it is not part of the confidence body.
    (
        "C10",
        """Each entity also carries a "confident" boolean \u2014 your certainty that entity.raw is ONE
cleanly-typed referent, and NOT a mash of several concepts forced together.
  - confident: true  → entity.raw is a single clean referent: a single word, a multi-word
    phrase that names ONE thing ("water closet", "skind enterprise sdn bhd"), or a part
    the USER explicitly labeled. This is the DEFAULT \u2014 when unsure, prefer true.
  - confident: false → you had to cram MORE THAN ONE concept/type into a single
    entity.raw because the user gave NO separation or label, so you cannot split it
    without guessing. Example: "one siew srtkt72ss" = quantity + a customer-ish name +
    a product code mashed together with no labels → ONE order entity, confident:false.
  A lone code or single token is ONE clean referent → confident:true EVEN IF you doubt it
  exists in the system \u2014 existence is the resolver's job, not yours. Set confident:false
  ONLY when you had to cram MORE THAN ONE untyped concept into a single raw because the
  user gave no separation or label.
  When the user DOES label the parts, SPLIT into separate entities, each confident:true:
    "customer one siew, product srtkt72ss"
      → { "raw":"one siew",  "hint":"customer", "confident":true }
      → { "raw":"srtkt72ss", "hint":"product",  "confident":true }
  Vagueness is SEMANTIC, not word-count: a multi-word phrase that is ONE referent is
  confident:true; a single untyped mash of several distinct values is confident:false.""",
        """Each entity carries a "confident" boolean. Default true: one clean referent, a lone code even if you doubt it exists, a part the user labelled (labelled parts are split, each true). false ONLY when several untyped concepts are mashed into one raw with no separation ("one siew srtkt72ss").

== ENTITY OP: how this turn relates to the previous entities ==""",
    ),
    # C11 BROADEN AXIS: phrase list.
    (
        "C11",
        """"every date", "no date limit", "remove the date filter", "since
                        ever", "not just August" - in ANY phrasing or language, including
                        a trailing emphatic particle that adds no meaning of its own
                        ("...one", "...lah"). This is ALWAYS""",
        """"no date limit". This is ALWAYS""",
    ),
    # C12 DATE FILTER: relative-date arithmetic and its example.
    (
        "C12",
        """  Relative dates include both directions:
      past/present: "today", "yesterday", "last week", "this month"
      future:       "tomorrow" (= CURRENT DATE + 1 day), "next week", "next month",
                    "in 3 days", a named weekday yet to come
  and absolute/range forms: "1 June", "May to June", "30/6".
  A single day sets BOTH start and end to that same date (e.g. "tomorrow" on 29 June 2026
  → date_filter_start = "2026-06-30", date_filter_end = "2026-06-30").""",
        """  A single day sets BOTH start and end to that same date (e.g. "30 June 2026"
  → date_filter_start = "2026-06-30", date_filter_end = "2026-06-30").""",
    ),
    # C13 POSITIONAL REFERENCES: ordinal / range examples.
    (
        "C13",
        """
  - multiple: "1st and 3rd", "2, 4 and 5"                 → [1,3] / [2,4,5]
  - ranges:   "the first three", "1 to 3"                 → [1,2,3]
  - "the last one" = N; "the first two" = [1,2]""",
        "",
    ),
    # C14 PERSON-NAME MENTION: honorific list.
    (
        "C14",
        """in any honorific form (Ms, Miss, Mrs, Mr, Encik/En,
Puan/Pn, Cik, Tuan, Dato/Datin, Dr), any spelling,""",
        """in any honorific form, any spelling,""",
    ),
    # C15 ORDER_STATUS FILTER: Malay synonyms, misspelling teaching, typo examples.
    (
        "C15a",
        """, READ FOR MEANING NOT SPELLING)""",
        """)""",
    ),
    (
        "C15b",
        """"outstanding", "pending", "not delivered",
    "undelivered", "not yet delivered", "still pending", "open orders", "belum hantar",
    "belum sampai", "tak hantar lagi".""",
        """"outstanding", "pending", "not delivered", "open orders".""",
    ),
    (
        "C15c",
        """"done",
    "sudah hantar", "sudah sampai".""",
        """"done".""",
    ),
    (
        "C15d",
        """READ FOR MEANING, NOT SPELLING: this is a question about ORDER PAPER even when the
status word itself is misspelled ("oustaning", "oustangind", "outstadning") or the
attribute word is ("quantiyt", "quantiy") - a garbled spelling of a word this
section already names is still that word, never a reason to fall back to
domain_hint "inventory". """,
        "",
    ),
    (
        "C15e",
        """  - "oustaning quantity for CB88SS" -> status "outstanding", domain_hint "order",
    requested_attributes ["quantity"] (NOT inventory / check_stock)
  - "oustangind dealer quantiyt for CB88SS" -> status "outstanding", domain_hint
    "order", requested_attributes ["quantity"]
  - "oustaning report for CB88SS" -> status "outstanding", domain_hint "order\"""",
        """  - "outstanding quantity for CB88SS" -> status "outstanding", domain_hint "order",
    requested_attributes ["quantity"] (NOT inventory / check_stock)
  - "outstanding report for CB88SS" -> status "outstanding", domain_hint "order\"""",
    ),
    # C16 IS_ACTIVE: synonym lists down to one word per value.
    (
        "C16a",
        """"discontinued", "expired",
    "ended", "obsolete", "no longer available", "old/past promotions",
    "delisted products". (A""",
        """"discontinued", "expired". (A""",
    ),
    (
        "C16b",
        """"active promotions",
    "current promos", "still available", "ongoing", "valid now".""",
        """"active promotions", "ongoing".""",
    ),
    # C17 GROUP_BY: "ikut ..." variants.
    (
        "C17",
        """  - "by customer", "per customer", "customer by customer", "ikut pelanggan" -> "customer"
  - "by transporter", "per transporter", "by lorry", "ikut lori" -> "transporter"
  - "by date", "per day", "day by day", "ikut tarikh" -> "date"
  - "by product", "per product", "by item", "ikut produk" -> "product"
  - "per warehouse", "by warehouse", "by location", "ikut gudang" -> "warehouse"
  - "by supplier", "per supplier", "ikut pembekal" -> "supplier\"""",
        """  - "by customer", "per customer", "customer by customer" -> "customer"
  - "by transporter", "per transporter", "by lorry" -> "transporter"
  - "by date", "per day", "day by day" -> "date"
  - "by product", "per product", "by item" -> "product"
  - "per warehouse", "by warehouse", "by location" -> "warehouse"
  - "by supplier", "per supplier" -> "supplier\"""",
    ),
    # C18 TOP_N.
    (
        "C18",
        """
  - "最近3次" -> 3""",
        "",
    ),
    # C19 so_outstanding / do_outstanding Malay / Chinese variants.
    (
        "C19a",
        """"no DO yet", "belum DO", "belum keluar DO", "还没出DO", "未出DO" -> "so_outstanding\"""",
        """"no DO yet" -> "so_outstanding\"""",
    ),
    (
        "C19b",
        """"not delivered yet", "belum hantar" -> "outstanding\"""",
        """"not delivered yet" -> "outstanding\"""",
    ),
    (
        "C19c",
        """"DO pending", "belum DO keluar" together""",
        """"DO pending" together""",
    ),
    # C20 PURCHASE ORDERS.
    (
        "C20",
        """"supplier order", "采购单", "已下单" ->""",
        """"supplier order" ->""",
    ),
    # C21 SPO LAST RECEIPT.
    (
        "C21",
        """"when did it last come in",
    "上次进货", "terakhir masuk" ->""",
        """"when did it last come in" ->""",
    ),
    # C22 LAST PURCHASE COST (keep: "cost" alone = selling price).
    (
        "C22a",
        """"cost price", "上次采购价", "成本",
    "harga belian terakhir", "harga beli" ->""",
        """"cost price" ->""",
    ),
    (
        "C22b",
        """"how much does it cost", "berapa harga", "多少钱" are""",
        """"how much does it cost" are""",
    ),
    # C23 LOW STOCK REPORT.
    (
        "C23",
        """"what needs reordering", "laporan stok rendah", "低库存报表" ->""",
        """"what needs reordering" ->""",
    ),
    # C24 SALES REPORT.
    (
        "C24",
        """"sales figures", "jualan report", "laporan jualan", "销售报告" ->""",
        """"sales figures" ->""",
    ),
    # C25 SALES_CHANNEL.
    (
        "C25",
        """("qty", "数量", "delivered", "已送货")""",
        """("qty", "delivered")""",
    ),
    # C26 entities[].quantity.
    (
        "C26",
        """, "change MHS1028 to 80", "tukar MHS1028 jadi 80" ->""",
        """, "change MHS1028 to 80" ->""",
    ),
    # C27 OPEN TASK.
    (
        "C27",
        """"check what you have", "teruskan" ->""",
        """"check what you have" ->""",
    ),
    # C28 Last answered.
    (
        "C28",
        """"and if 100?", "kalau 100?",
    "100 pula?" ->""",
        """"and if 100?" ->""",
    ),
    # C29 OPEN QUESTION: Malay / Chinese / pinyin ordinals, confirm words, counts, number words.
    (
        "C29a",
        """"the first one", "1", "no 1", "yang pertama", "nombor satu",
"第一个", "yi hao" -> [1]; "the second", "kedua", "dua", "第二个", "di er ge" -> [2];
"1 and 3", "first and third", "satu dan tiga", "一和三" -> [1, 3]; "both", "dua-dua",
"两个都要" -> both positions; "all", "semua" -> every position.""",
        """"the first one", "1", "no 1" -> [1]; "the second" -> [2];
"1 and 3", "first and third" -> [1, 3]; "both" -> both positions; "all" -> every position.""",
    ),
    (
        "C29b",
        """"the first one, I need 2", "1, I need 2", "yang pertama, 2 unit",
"第一个，要两个", "yi hao, liang ge" -> picked [1], qty_for_all 2;""",
        """"the first one, I need 2", "1, I need 2" -> picked [1], qty_for_all 2;""",
    ),
    (
        "C29c",
        """"none of them", "neither", "bukan", "tak ada", "都不是" -> "no".
CONFIRM: "yes", "ya", "betul", "ok that one", "对", "是" -> "yes"; "no", "tak", "bukan",
"不是", "not that one" -> "no".""",
        """"none of them", "neither" -> "no".
CONFIRM: "yes", "ok that one" -> "yes"; "no", "not that one" -> "no".""",
    ),
    (
        "C29d",
        """"first 10, second 5",
    "satu 10, dua 5", "yi 10 er 5") ->""",
        """"first 10, second 5") ->""",
    ),
    (
        "C29e",
        """"that's all", "itu saja", "cukup" ->""",
        """"that's all" ->""",
    ),
    (
        "C29f",
        """"3 for all of them", "semua 3", "每个三个" ->""",
        """"3 for all of them" ->""",
    ),
    (
        "C29g",
        """("make line 2 10", "tukar yang kedua
    jadi sepuluh", "di san ge yao 8") ->""",
        """("make line 2 10") ->""",
    ),
    (
        "C29h",
        """
Number words are numbers in every language: "satu, dua, tiga" and "yi, er, san" and
"一, 二, 三" are 1, 2, 3; "two", "dua", "liang ge", "两个" as a quantity are 2.""",
        "",
    ),
    # C30 OPEN NUMBERED QUESTION: paraphrase example list (keep the ONE-option rule).
    (
        "C30",
        """A PARAPHRASE is still an answer. The customer rarely types the label back: "gimme the
list plss", "show me", "yes list please", "the SO one", "let me see them" all name an
option when a numbered question is open. Pick the option they mean; and when only ONE
option is offered, any request for "the list" is that option.""",
        """A PARAPHRASE is still an answer: pick the option they mean; and when only ONE
option is offered, any request for "the list" is that option.""",
    ),
    # C31 SALES ANALYSIS.
    (
        "C31",
        """"monthly sales", "jualan
tahun ini", "销售总额" -""",
        """"monthly sales" -""",
    ),
    # C32 TOP SELLING: Malay / Chinese variants and the duplicate English synonym run.
    (
        "C32a",
        """"which category sells most", "barang paling laku",
    "paling laris", "畅销", "卖得最好" -> order_status""",
        """"which category sells most" -> order_status""",
    ),
    (
        "C32b",
        """  - "top 100 sold item", "top 100 sold items", "most sold item", "best selling",
    "top selling", "top 100 hot selling item", "highest selling", "top sellers", "top
    sold", "best seller" -> the same: order_status "top_selling", domain_hint "order",
    intent_hint "check_order", top_n from the number where one is named""",
        """  - "top 100 sold items", "top sellers" -> the same: order_status "top_selling",
    domain_hint "order", intent_hint "check_order", top_n from the number where one is named""",
    ),
    # C33 RANK_BY / BASIS.
    (
        "C33a",
        """"most units", "ikut kuantiti", "按数量"
    -> "quantity\"""",
        """"most units"
    -> "quantity\"""",
    ),
    (
        "C33b",
        """"by RM",
    "ikut nilai", "按金额" -> "amount\"""",
        """"by RM" -> "amount\"""",
    ),
    (
        "C33c",
        """"transferred to DO", "sudah hantar" -> "delivered\"""",
        """"transferred to DO" -> "delivered\"""",
    ),
    (
        "C33d",
        """"booked", "tempahan" -> "ordered\"""",
        """"booked" -> "ordered\"""",
    ),
    # C34 ANSWERING TOP SELLING: Chinese basis words (keep the rule).
    (
        "C34",
        """已送货 -> basis "delivered" (送货, 已转DO), 已订购 -> basis "ordered" (下单, 订单); an answer""",
        """an answer""",
    ),
    # C35 RANK_DIRECTION: Malay / Chinese, typo sentence.
    (
        "C35a",
        """"bottom 10",
    "paling kurang laku", "卖得最差" -> "bottom\"""",
        """"bottom 10"
    -> "bottom\"""",
    ),
    (
        "C35b",
        """ A spelling slip ("worsr", "wrost") is the same word.""",
        """""",
    ),
    # C36 NARROWING TOP SELLING.
    (
        "C36a",
        """"salesman fanny", "jurujual fanny" ->""",
        """"salesman fanny" ->""",
    ),
    (
        "C36b",
        """, "by sean salea agent", "fanny sales agent" ->""",
        """, "fanny sales agent" ->""",
    ),
    (
        "C36c",
        """; a slip of "sales" ("salea",
"saless") is never part of the name.""",
        """.""",
    ),
    # C37 SELF_REFERENCE: Malay / Chinese pronoun lists.
    (
        "C37",
        """
  - "saya punya", "kami punya", "punya saya", "punya kami" ("outstanding saya punya",
    "order kami punya")
  - "我的", "我们的" ("我的订单", "我们的欠款")""",
        "",
    ),
    # C38 ESCALATION CONFIRMATION.
    (
        "C38a",
        """a thumbs up, "boleh", "ok escalate\"""",
        """a thumbs up, "ok escalate\"""",
    ),
    (
        "C38b",
        """("no thanks", "tak apa", "it's okay")""",
        """("no thanks", "it's okay")""",
    ),
    # C39 ACCOUNT: spelling list.
    (
        "C39a",
        """"ac1", "akaun 1", "户口1", "第一个户口" -> 1""",
        """"ac1" -> 1""",
    ),
    (
        "C39b",
        """"a/c ii", "akaun 2" -> 2""",
        """"a/c ii" -> 2""",
    ),
    # C40 LOW STOCK FILTERS: translation lists, "semua", "ikut ...", Malay and Chinese examples.
    (
        "C40a",
        """in any language
or mix (English, Malay, Chinese, Manglish), never""",
        """in any language,
never""",
    ),
    (
        "C40b",
        """Translate: "paip air", "kepala paip",
    "水龙头" -> "water tap"; "tandas", "mangkuk tandas", "马桶" -> "water closet"; "sinki dapur",
    "厨房水槽" -> "kitchen sink". A number""",
        """Translate to English. A number""",
    ),
    (
        "C40c",
        """"everything",
    "semua", "全部"); else null.""",
        """"everything"); else null.""",
    ),
    (
        "C40d",
        """"taiyang only", "pembekal X", "供应商 X"), as written.""",
        """"taiyang only"), as written.""",
    ),
    (
        "C40e",
        """"supplier-wise", "ikut pembekal", "按供应商" ->
    "supplier"; "by category", "per type", "ikut kategori", "按类别" -> "category"; only a
    message naming BOTH as the split -> "supplier_category"; "by brand", "split by brand", "ikut
    jenama", "按品牌" -> "brand"; "by warehouse", "per location", "ikut gudang" ->
    "warehouse";""",
        """"supplier-wise" ->
    "supplier"; "by category", "per type" -> "category"; only a
    message naming BOTH as the split -> "supplier_category"; "by brand", "split by brand"
    -> "brand"; "by warehouse", "per location" ->
    "warehouse";""",
    ),
    (
        "C40f",
        """
  - "stok rendah paip air ikut pembekal" -> categories ["water tap"], group_by "supplier"
  - "低库存 马桶 per vendor" -> categories ["water closet"], group_by "supplier\"""",
        "",
    ),
    (
        "C40g",
        """"just water closet", "ikut kategori")""",
        """"just water closet")""",
    ),
    (
        "C40h",
        """("all", "any", "semua",
"全部") or "cancel" ("cancel", "never mind", "batal")""",
        """("all", "any") or "cancel" ("cancel", "never mind")""",
    ),
    # C41 SALES RANKING: Chinese words (keep: a dealer IS our customer).
    (
        "C41a",
        """(业务员, SA) -> dealer (经销商, 代理商, 客户, customer)""",
        """(SA) -> dealer (customer)""",
    ),
    (
        "C41b",
        """"which customer bought the most", "哪一位经销商卖得最多",
    "哪个经销商卖得最多", "多少个经销商卖这个产品" ->""",
        """"which customer bought the most" ->""",
    ),
    (
        "C41c",
        """  - "哪一位业务员卖得最多", "which salesman sold the most CWC7606-SH" ->""",
        """  - "which salesman sold the most CWC7606-SH" ->""",
    ),
    (
        "C41d",
        """the ranked noun (customer, 客户, 经销商, dealer, 业务员) is never an entity: "哪一位customer sell最多 X" ->""",
        """the ranked noun (customer, dealer) is never an entity: "which customer sell most X" ->""",
    ),
    (
        "C41e",
        """已送货 -> basis "delivered" (送货, 已转DO), 已订购 -> basis "ordered" (下单, 订单), 未交 -> basis "outstanding" (未送); an answer""",
        """an answer""",
    ),
    # C42 PRODUCT CODE: type words and the Malay example (keep the Chinese -> English class map).
    (
        "C42a",
        """("tap", "WC", "basin",
"paip", "龙头")""",
        """("tap", "WC", "basin")""",
    ),
    (
        "C42b",
        """
  - "paip SRTWT5608 ada stok?" -> entity {raw: "paip SRTWT5608", hint: "product",
    canonical_code: "SRTWT5608"}""",
        "",
    ),
    # C43 TRAP SPECS.
    (
        "C43a",
        """"one piece", "tandas",
    "jamban", "马桶", "坐厕" is""",
        """"one piece" is""",
    ),
    (
        "C43b",
        """"have
    stock", "ada stok", "有货" is""",
        """"have
    stock" is""",
    ),
    (
        "C43c",
        """
    - "tandas s trap 300mm ada stok?" -> the same as the first, in Malay""",
        "",
    ),
    (
        "C43d",
        """"the 300 one", "s
    trap pula", "P弯呢")""",
        """"the 300 one")""",
    ),
    # C44 PROMOTION KIND.
    (
        "C44",
        """"flyer", "传单", "A3" or""",
        """"flyer", "A3" or""",
    ),
    # C45 MEMORY.
    (
        "C45",
        """, "apa saya tanya tadi", "我之前问过什么";""",
        """;""",
    ),
    # C46 A BARE "SPEC".
    (
        "C46",
        """"specifications" / "规格" ->""",
        """"specifications" ->""",
    ),
    # C47-C53: additions beyond the PLAN table, needed to reach UAC U9 (12,000 chars). Each is
    # language, typo or date-arithmetic teaching; the rule sentence it illustrated stays.
    (
        "C47a",
        """word: "image", "picture", "gambar" are all photo.""",
        """word: "image" and "picture" are photo.""",
    ),
    (
        "C47b",
        """, "semua
               variant 286".""",
        """.""",
    ),
    (
        "C47c",
        """"them",
    "这些", "above")""",
        """"them", "above")""",
    ),
    (
        "C47d",
        """, "sepuluh", "10个")""",
        """)""",
    ),
    (
        "C47e",
        """ "这个产品" is no entity: the standing product carries.""",
        "",
    ),
    (
        "C48a",
        """, and through a typo in a
NEIGHBOURING word.""",
        """.""",
    ),
    (
        "C48b",
        """
  - "Outstsnding DO for 7445" -> document ["DO"], status "outstanding" (the typo is in
    the status word, not the document word)""",
        "",
    ),
    (
        "C48c",
        """"stoick SRTWC286-SH-NEW" ->""",
        """"stock SRTWC286-SH-NEW" ->""",
    ),
    (
        "C48d",
        """ ("stoick" is "stock", read for meaning)""",
        "",
    ),
    (
        "C49a",
        """"Sorento team", "the mocha one", "srt", "route to mch", "yes please escalate to srt team", "please escalate
to sorento team")""",
        """"srt")""",
    ),
    (
        "C49b",
        """"mocha please", "ok go with
    sorento", "the mocha one", "route to mocha team", "srt", "yes please escalate to srt team", "please
    escalate to sorento team")""",
        """"srt")""",
    ),
    (
        "C49c",
        """"Mocha", "yes mocha", "the mocha one", "srt", "route to mch",
"escalate to mocha team")""",
        """"yes mocha", "srt")""",
    ),
    (
        "C50a",
        """"yeah sales agent", "i mean sales agent sean", "neither,
    i mean sales agent sean", "fanny sales agent" ->""",
        """"fanny sales agent" ->""",
    ),
    (
        "C50b",
        """"which is water closet", "water closet only", "product
    category is water closet", "for water tap", "toilet only", "kitchen sinks" ->""",
        """"water closet only", "for water tap", "kitchen sinks" ->""",
    ),
    (
        "C52h",
        """"sales order", "SO", "sales orders" -> ["SO"]
  - "delivery order", "DO", "delivery orders", "DO list" -> ["DO"]""",
        """"sales order", "SO" -> ["SO"]
  - "delivery order", "DO", "DO list" -> ["DO"]""",
    ),
    (
        "C52i",
        """Years named ("2025 vs 2026", "last year and this year") -> date_filter_start the first
of January of the EARLIEST year, date_filter_end the thirty-first of December of the
LATEST. No period said -> both null (the report reads this calendar year). A period that
could mean two different spans ("last quarter" in January) -> ask, as for any date.""",
        """Years named ("2025 vs 2026") -> date_filter_start 1 Jan of the EARLIEST year, date_filter_end
31 Dec of the LATEST. No period said -> both null (the report reads this calendar year). A period that
could mean two different spans ("last quarter" in January) -> ask, as for any date.""",
    ),
    (
        "C53a",
        """ ("Sorneto" -> canonical_code "Sorento")""",
        "",
    ),
    (
        "C53b",
        """, a typo corrected to the listed value
("walll hung wc" -> {"raw": "walll hung", "hint": "specification", "spec_key": "mounting",
"spec_value": "wall hung"})""",
        """, a typo corrected to the listed value""",
    ),
    (
        "C53g",
        """ A year ("2025", "2025?", "in 2025", "what about 2025", "i mean in year 2025") after""",
        """ A year ("2025", "in 2025") after""",
    ),
    (
        "C54a",
        """, exactly as DATE FILTER already rules. A channel word""",
        """. A channel word""",
    ),
    (
        "C54b",
        """, and neither one absorbs the month into
nothing.""",
        """.""",
    ),
    # C57 (owner ruling 11 Oct, overrides Q5): the Chinese -> English class map.
    (
        "C57",
        """:
马桶 -> canonical_code "water closet", 坐厕 -> canonical_code "water closet", 面盆 -> canonical_code "wash basin",
洗手盆 -> canonical_code "wash basin", 水龙头 -> canonical_code "tap", 浴室柜 -> canonical_code "bathroom cabinet",
厨房水槽 -> canonical_code "kitchen sink", 水槽 -> canonical_code "kitchen sink", 淋浴 -> canonical_code "shower",
花洒 -> canonical_code "shower", 浴缸 -> canonical_code "bathtub", 小便斗 -> canonical_code "urinal".""",
        ".",
    ),
    # D01 / D02: duplicate sections (already stated under MESSAGE TYPE and REQUESTED ATTRIBUTES).
    (
        "D01",
        """== A DELIVERY WORD PLUS A NAME IS AN ORDER ASK ==
request_for_help is ONLY a request for a HUMAN. A message that names a customer or product next to a delivery / order / DO word ("delivery to hanlim", "hantar ke hanlim", "any DO for X") is business_query, intent_hint check_order, domain_hint order, entity = that name with current_message true, is_escalation_confirmation false, even when an escalate offer is open.

""",
        "",
    ),
    (
        "D02",
        """== THE ATTRIBUTE PHRASE, WHOLE ==
For requested_attributes ONLY: emit the customer's own attribute phrase, WHOLE ("seat cover material", "list price"), never a shortened token; a base property (price, dimensions, description, name) is a requested attribute too. This rule never reaches an entity's canonical_code.

""",
        "",
    ),
    # D03: stale tail line.
    (
        "D03",
        """

If relative dates such as "today" or "yesterday" appear in the current turn input, convert them to absolute dates before calling the MCP tool.""",
        "",
    ),
]


def _parts_of(restore_id: str) -> set[str]:
    """Cut ids a restore id names: the id itself plus suffixed parts (C15 -> C15a, C15b ...)."""
    pattern = re.compile(rf"^{re.escape(restore_id)}[a-z]$")
    return {cid for cid, _, _ in CUTS if cid == restore_id or pattern.match(cid)}


def build(source_text: str, restore: frozenset[str] | set[str] = frozenset()) -> str:
    """Apply every cut not named in `restore`; each `old` must occur exactly once when applied."""
    skipped: set[str] = set()
    for rid in restore:
        parts = _parts_of(rid)
        if not parts:
            raise ValueError(f"unknown cut id to restore: {rid}")
        skipped |= parts
    text = source_text
    for cut_id, old, new in CUTS:
        if cut_id in skipped:
            continue
        count = text.count(old)
        if count != 1:
            raise ValueError(f"cut {cut_id}: old text occurs {count} times, expected exactly 1")
        text = text.replace(old, new, 1)
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--restore", default="", help="comma separated cut ids to leave uncut, e.g. C08,C15")
    args = parser.parse_args()
    restore = frozenset(x.strip() for x in args.restore.split(",") if x.strip())
    text = build(SOURCE.read_text(encoding="utf-8"), restore)
    with open(OUTPUT, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    print(f"wrote {OUTPUT} ({len(text)} chars)")


if __name__ == "__main__":
    main()
