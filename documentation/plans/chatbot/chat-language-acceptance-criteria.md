# CHAT-LANGUAGE acceptance criteria (slice 1: stock)

Plan: `PLAN-chat-language-2oct.md`. Card: `chat-language-behaviour-card.md` (Q1-Q5 written to recs).

## Contract (what the tests bind to)

`app/services/chatbot/label_catalog.py` (pure, except `resolve`):
- `LANGUAGES = ("en", "ms", "zh")`
- `LABELS: dict[str, dict[str, str]]`: English source text -> `{"ms": ..., "zh": ...}`.
- `FIELD_KEYS: dict[str, str]`: presenter field key -> English label.
- `tokens(text) -> list[str]`: sorted `{name}` placeholders. `tokens_match(a, b) -> bool`.
- `defaults(lang) -> dict[str, str]`: English -> target for every catalog entry whose
  translation passes `tokens_match`; `{}` for `en`.
- `class Localizer(language: str, table: dict[str, str])`:
  - `label(field: dict) -> str`: by `field["key"]` via `FIELD_KEYS` when the key is
    catalogued AND the field's label equals that English label; else by the exact label text;
    else the label unchanged.
  - `text(s: str) -> str`: an exact entry, else a `{token}` template whose English shape
    matches the whole string (values re-inserted verbatim); else `s` unchanged.
  - `tail(title: str) -> str`: for `"<code> x <qty>: <sentence>"` translates the sentence
    only, keeping everything before the first `": "`; else `text(title)`.
- `IDENTITY`: `Localizer("en", {})`.
- `resolve(db, lang) -> Localizer`:
  - `en`/unknown -> `IDENTITY` (no DB read).
  - Otherwise one read of `translation_memory` rows where `source_lang='en'`,
    `target_lang=lang` and `source_text` is in the catalog. A row's `target_text` wins if it
    passes `tokens_match`.
  - Every catalog default with no row is inserted as `source='ai'` (idempotent; a second
    resolve inserts nothing).
  - A `manual` row is never overwritten.

`app/services/chatbot/language.py` (pure):
- `detect(message, *, strip=()) -> "en"|"ms"|"zh"|None`: rules in the behaviour card.
- `choose(detected, conversation, saved) -> str`: the first of those in `LANGUAGES`, else `"en"`.
- `for_turn(message, session_vars: dict, saved, *, strip=()) -> str`: `choose(detect(...),
  session_vars.get("reply_language"), saved)`. It writes the result to
  `session_vars["reply_language"]` and returns it.

Rendering:
- `lanes/business/fetch.py::output_structurer(result, ctx)` reads `ctx["localizer"]`
  (absent -> identity, byte-identical to today) for: field labels, the summary-item labels,
  the intro, availability titles (`tail`), flag words, and the `Data last updated: {ts}` footer.
- `turn/compose.py::compose(..., ctx)` reads `getattr(ctx, "localizer", None)` for
  "No stock found for {codes}.".

## Slice-1 catalog content (exact)

| English | ms | zh |
|---|---|---|
| Company | Syarikat | 公司 |
| Product Code | Kod Produk | 产品代码 |
| Product Name | Nama Produk | 产品名称 |
| Warehouse | Gudang | 仓库 |
| System Location | Lokasi Sistem | 系统位置 |
| Quantity On Hand | Kuantiti Ada | 现有数量 |
| Outstanding | Tertunggak | 未交货 |
| Total | Jumlah | 总数 |
| Stock summary for the requested products. | Ringkasan stok untuk produk yang diminta. | 所请求产品的库存摘要。 |
| Stock details found for the requested products. | Butiran stok untuk produk yang diminta. | 所请求产品的库存详情。 |
| How many units do you need? | Berapa unit yang anda perlukan? | 您需要多少件？ |
| yes, we have stock. Please refer to your salesman. | ya, stok ada. Sila rujuk jurujual anda. | 有库存。请联系您的销售员。 |
| no stock and no incoming at the moment. Please refer to your salesman. | tiada stok dan tiada barang masuk buat masa ini. Sila rujuk jurujual anda. | 目前没有库存，也没有到货。请联系您的销售员。 |
| the quantity is more than what I can confirm here. Please refer to your salesman. | kuantiti ini melebihi apa yang boleh saya sahkan di sini. Sila rujuk jurujual anda. | 这个数量超出我在这里可以确认的范围。请联系您的销售员。 |
| no stock at the moment, ETA {eta}. | tiada stok buat masa ini, ETA {eta}. | 目前没有库存，ETA {eta}。 |
| Data last updated: {ts} | Data dikemas kini: {ts} | 数据更新时间: {ts} |
| No stock found for {codes}. | Tiada stok ditemui untuk {codes}. | 未找到 {codes} 的库存。 |
| Here are the results. | Berikut ialah hasilnya. | 以下是结果。 |
| PRODUCT DISCONTINUED | PRODUK DIHENTIKAN | 产品已停产 |

FIELD_KEYS (slice 1): company_name, product_code, product_name, warehouse, system_location,
quantity_on_hand, open_so_qty -> Outstanding, total_on_hand -> Total.

## Criteria

- **AC-CL01:** every catalog entry has ms and zh, and each passes `tokens_match`.
- **AC-CL02:** every stock-path label and sentence the presenter emits is catalogued. Pinned
  list: the 8 labels above, the 3 intros and the 4 verdict tails; the tails are pinned against
  `sorento_crm_mcp.presenters._AVAILABILITY_TAILS` and `REFER_TO_SALESMAN`.
- **AC-CL03:** a `Localizer` never changes a value. A location-code label (`"BRW-BB"`), a
  product code, a number or an uncatalogued label comes back unchanged.
- **AC-CL04:** a template whose translation drops or adds a `{token}` is rejected: `defaults`
  omits it, and `resolve` ignores such a memory row and falls back to the default.
- **AC-CL05:** detection table:

  | Message | Expected |
  |---|---|
  | "SRTSWT3001 有货吗" | zh |
  | "ada stok SRTSWT3001?" | ms |
  | "SRTSWT3001 ada stock tak" | ms |
  | "berapa unit kat PKL?" | ms |
  | "can check stock SRTSWT3001 ah" | en |
  | "how many SRTSWT3001 in stock" | en |
  | "SRTSWT3001" | None |
  | "1" | None |
  | "ok" | None |
  | "stock ada?" (tie) | None |
  | "ADA HARDWARE stock please" with strip ["ADA HARDWARE"] | en |

- **AC-CL06:** `choose`/`for_turn`: example E. "stock SRTSWT3001" gives en. "berapa unit kat
  PKL?" gives ms. "1" gives ms (carried). With no carry and saved "zh", "1" gives zh. With
  nothing at all, en.
- **AC-CL07:** `output_structurer` with no localizer is byte-identical to today (existing
  suite stays green).
- **AC-CL08:** `output_structurer` on a compact stock envelope with the ms localizer contains
  "Ringkasan stok untuk produk yang diminta.", "*Kod Produk:* SRTSWT3001", "*Jumlah:* 51",
  "*BRW-BB:* 30" and "_Data dikemas kini: ". It contains none of "Product Code", "Total:" or
  "Data last updated". zh is the same with the zh strings.
- **AC-CL09:** availability, answered, zh: `"SRTSWT3001 x 10: 有库存。请联系您的销售员。"`.
  Incoming: `"SRTSWT3001 x 10: 目前没有库存，ETA 15/10/2026。"`.
- **AC-CL10:** detailed staff row, zh: "*公司:*", "*产品代码:* BRBC22102W", "*仓库:* BUKIT RAJA",
  "*系统位置:* BRW-BB" and "*现有数量:* 40". A discontinued flag line reads "产品已停产".
- **AC-CL11:** `compose` with an ms localizer prints "Tiada stok ditemui untuk SRTSWT3001-GM.";
  figures are not mutated (`_codes_without_rows` still matches "Product Code").
- **AC-CL12:** `resolve(db, "ms")`:
  - The first call inserts one `ai` row per catalog entry.
  - A second call inserts 0 rows.
  - A pre-existing `manual` row with different text wins and is unchanged afterwards.
  - `resolve(db, "en")` reads nothing and returns `IDENTITY`.

# Slice 2: PO / SO / SPO / orders rows

Tools localized (added to `fetch._LOCALIZED_TOOLS`): `crm_order_management_orders_list`,
`crm_order_management_orders_by_product_list`, `crm_procurement_po_placed_list`,
`crm_procurement_spo_allocations_last_receipt_list`, `crm_procurement_po_last_cost_list`.
Presenter literals: `presenters.py` `_orders_list` (:411), `_orders_so_outstanding` (:441),
`_purchase_orders_placed` (:471), `_spo_last_receipt` (:503), `_po_last_cost` (:553),
`_orders_by_product` (:614), `_DEFAULT_INTRO` (:67-84), the intros at :1815-1836, and
`fetch.py` order-status intros (:2960-2962) plus the flag lines (:3034-3040).
Abbreviations PO / SO / SPO / GR stay as printed (owner Q3). Order STATUS values are data and
are never translated.

| English | ms | zh |
|---|---|---|
| Order Number | No. Pesanan | 订单号 |
| Customer | Pelanggan | 客户 |
| Order Date | Tarikh Pesanan | 订单日期 |
| Actual Delivery Date | Tarikh Penghantaran Sebenar | 实际送货日期 |
| Status | Status | 状态 |
| Pickup Time | Masa Pengambilan | 提货时间 |
| Transporter | Pengangkut | 运输商 |
| Driver | Pemandu | 司机 |
| Lorry Plate | No. Plat Lori | 车牌号 |
| Products | Produk | 产品 |
| SO Number | No. SO | SO 编号 |
| Outstanding Qty | Kuantiti Tertunggak | 未交货数量 |
| Requested Delivery Date | Tarikh Penghantaran Diminta | 要求送货日期 |
| PO Number | No. PO | PO 编号 |
| Ordered Qty | Kuantiti Dipesan | 订购数量 |
| PO Date | Tarikh PO | PO 日期 |
| Location | Lokasi | 位置 |
| Supplier | Pembekal | 供应商 |
| SPO Number | No. SPO | SPO 编号 |
| Container Number | No. Kontena | 货柜号 |
| SPO Quantity | Kuantiti SPO | SPO 数量 |
| GR Quantity | Kuantiti GR | GR 数量 |
| SPO Date | Tarikh SPO | SPO 日期 |
| SPO Date (recorded) | Tarikh SPO (direkodkan) | SPO 日期（已记录） |
| GR Date | Tarikh GR | GR 日期 |
| PO Quantity | Kuantiti PO | PO 数量 |
| Cost / unit | Kos / unit | 单位成本 |
| Discount / unit | Diskaun / unit | 单位折扣 |
| Cost after discount / unit | Kos selepas diskaun / unit | 折后单位成本 |
| Here are the orders I found. | Berikut ialah pesanan yang saya temui. | 以下是我找到的订单。 |
| Here is the PO placed I found. | Berikut ialah PO yang telah dibuat. | 以下是已下的 PO。 |
| Here is the last SPO line per product. | Berikut ialah baris SPO terakhir bagi setiap produk. | 以下是每个产品最近的 SPO 记录。 |
| Here is the last purchase cost per product and location. | Berikut ialah kos belian terakhir bagi setiap produk dan lokasi. | 以下是每个产品和位置最近一次的采购成本。 |
| Here is the outstanding SO I found. | Berikut ialah SO tertunggak yang saya temui. | 以下是我找到的未交货 SO。 |
| Here are the outstanding orders I found. | Berikut ialah pesanan tertunggak yang saya temui. | 以下是我找到的未交货订单。 |
| Here are the delivered orders I found. | Berikut ialah pesanan yang telah dihantar. | 以下是我找到的已送货订单。 |
| No matching results found. | Tiada hasil yang sepadan ditemui. | 未找到匹配的结果。 |
| No matching results found for {companies}. | Tiada hasil yang sepadan ditemui untuk {companies}. | 在 {companies} 中未找到匹配的结果。 |
| Here are the results I found. | Berikut ialah hasil yang saya temui. | 以下是我找到的结果。 |
| EXPIRED | TAMAT TEMPOH | 已过期 |
| Customers | Pelanggan | 客户 |
| SO | SO | SO |
| SO Date | Tarikh SO | SO 日期 |
| Ordered | Dipesan | 订购 |
| Transferred to DO | Dipindahkan ke DO | 已转 DO |
| SO Outstanding | SO Tertunggak | SO 未交货 |
| DO | DO | DO |
| DO Date | Tarikh DO | DO 日期 |
| Delivered | Dihantar | 已送货 |
| Delivery Date | Tarikh Penghantaran | 送货日期 |
| DO Outstanding | DO Tertunggak | DO 未送达 |
| Not every breakdown is shown (em dash) add a customer, a product or a date range. | Tidak semua pecahan dipaparkan. Tambah pelanggan, produk atau julat tarikh. | 并非所有明细都已显示，请加上客户、产品或日期范围。 |
| No orders found for {codes}. | Tiada pesanan ditemui untuk {codes}. | 未找到 {codes} 的订单。 |
| No last in found for {codes}. | Tiada SPO ditemui untuk {codes}. | 未找到 {codes} 的 SPO。 |
| No outstanding purchase orders found for {codes}. | Tiada PO ditemui untuk {codes}. | 未找到 {codes} 的 PO。 |
| No last purchase cost found for {codes}. | Tiada kos belian ditemui untuk {codes}. | 未找到 {codes} 的采购成本。 |
| PENDING ALLOCATION | MENUNGGU PERUNTUKAN | 待分配 |
| PARTIAL ALLOCATION | PERUNTUKAN SEBAHAGIAN | 部分分配 |

FIELD_KEYS additions: so_number, outstanding_qty -> Outstanding Qty, order_date, customer,
requested_delivery_date, po_number, ordered_qty, po_date, location, supplier, spo_number,
container_number, spo_quantity, gr_quantity, spo_date (label "SPO Date" or "SPO Date (recorded)":
by-label match covers both), gr_date, warehouse, po_quantity, unit_cost -> Cost / unit,
discount_per_unit, unit_cost_after_discount.

- **AC-CL20:** every label/intro literal of the five tools' presenters is catalogued (pinned
  list above; presenter source is read where importable, as slice 1 does).
- **AC-CL21:** a PO placed envelope rendered with the ms localizer: "*No. PO:* <po>",
  "*Kuantiti Dipesan:* <n>", "*Pembekal:*" only when granted (restricted drop unchanged), intro
  "Berikut ialah PO yang telah dibuat.". Every value byte-identical to the en render.
- **AC-CL22:** SPO last-receipt zh: "*SPO 编号:*", "*货柜号:*", "*SPO 日期（已记录）:*" for a
  recorded date; values unchanged.
- **AC-CL23:** orders list ms: "*No. Pesanan:*", "*Pelanggan:*", "*Status:* <status value
  unchanged>", the outstanding-orders intro when `order_status == "outstanding"`, and the flag
  lines EXPIRED / PENDING ALLOCATION / PARTIAL ALLOCATION translated.
- **AC-CL24:** PO last cost zh restricted cost fields: dropped without grant, translated labels
  with grant ("*单位成本:*"); money values unchanged.
- **AC-CL25:** "No matching results found for {companies}." localizes with the company names
  re-inserted verbatim.
- **AC-CL26:** tools outside slices 1-2 (outstanding report, top selling, promotions,
  attachments) still render English with an ms localizer in ctx.

# Slice 3: outstanding report + top selling (finished text)

These two reports arrive as finished text (`presenters._outstanding_report` / `_outstanding_detail` /
`_top_selling`), shown verbatim by `fetch._outstanding_report_output` (:2224) and
`fetch._top_selling_output` (:2526). The backend PARSES that English text (offer roster
`fetch.py:2050-2073`, offer block `:2148-2164`, part markers `engine.py:131-158`), so the order is:
**parse the English first, localize the outgoing text last.** The stored `filters.offer_text`
stays English; its re-print (`turn/compose.py:~818`, `lanes/business/__init__.py:~717`) is
localized with the turn's localizer.

**Contract:** `Localizer.lines(text) -> str`, applied per line (`\n`-split, joined back
byte-exactly). For each line, the first rule that matches wins:
1. **Exact sentence:** the whole line, or the line inside one formatting wrapper (`*x*`,
   `*_x_*`, `_x_`), is a catalog entry (exact or `{token}` template). The wrapper is kept.
2. **Numbered:** `"<n>. <rest>"` where `<rest>` matches rule 1. The number is kept.
3. **Label line:** the line is `"<label>: <value>"` or the bold form `"*<label>:* <value>"`,
   optionally led by `"<n>. "`. It splits at the first `": "` / `":* "`, and `<label>` must be
   catalogued. The label is translated and the wrapper and number are kept. The value is
   unchanged except in three cases:
   - It is exactly one of the value words `VALUE_WORDS = {"all", "Amount", "Quantity",
     "Ordered", "Delivered (transferred to DO)"}`, which become their catalog translation.
     This set is explicit, never "any catalog key", so data such as a status named "Status"
     is never translated.
   - It matches `dd/mm/yyyy to dd/mm/yyyy`, which gets the catalog's `{from} to {to}`
     template.
   - Anything else in the value (Qty, RM, O/S, codes, names, `Unassigned`) is left as it is.
4. **Otherwise** the line is unchanged.

**Never translated:**
- Rank/data lines such as `1. CODE: Qty 3, RM 5.00`: their "label" is a code, which is not
  catalogued.
- Month labels (`Sep 2026`), channel values (`Dealer`, `Project`), the `(k/m)` part markers and
  `Unassigned`. "Unassigned" stays English because `decide._positions_by_label` matches the
  typed answer against it.

Localized tools: `crm_outstanding_report`, `crm_top_selling_report`, at the two output functions.
The parse runs on English and only the response text is localized.

| English | ms | zh |
|---|---|---|
| Sales orders | Pesanan jualan | 销售订单 |
| Ordered | Dipesan | 订购 |
| Transferred to DO | Dipindahkan ke DO | 已转 DO |
| Order date range | Julat tarikh pesanan | 订单日期范围 |
| Delivery orders | Pesanan penghantaran | 送货单 |
| DO qty | Kuantiti DO | DO 数量 |
| Delivered | Dihantar | 已送货 |
| DO date range | Julat tarikh DO | DO 日期范围 |
| Product | Produk | 产品 |
| Order date | Tarikh pesanan | 订单日期 |
| Brand | Jenama | 品牌 |
| DO Number | No. DO | DO 编号 |
| DO Qty | Kuantiti DO | DO 数量 |
| DO Date | Tarikh DO | DO 日期 |
| Category | Kategori | 类别 |
| Sales agent | Ejen jualan | 销售代理 |
| Channel | Saluran | 渠道 |
| Delivery date | Tarikh penghantaran | 送货日期 |
| Ranked by | Disusun mengikut | 排名依据 |
| Basis | Asas | 统计口径 |
| Items with sales | Item dengan jualan | 有销售的项目 |
| Categories with sales | Kategori dengan jualan | 有销售的类别 |
| all | semua | 全部 |
| {from} to {to} | {from} hingga {to} | {from} 至 {to} |
| Amount | Amaun | 金额 |
| Quantity | Kuantiti | 数量 |
| Delivered (transferred to DO) | Dihantar (dipindahkan ke DO) | 已送货（已转 DO） |
| Sales order outstanding | Pesanan jualan tertunggak | 未交货销售订单 |
| Delivery order outstanding | Pesanan penghantaran tertunggak | 未送达送货单 |
| By location | Mengikut lokasi | 按位置 |
| By customer | Mengikut pelanggan | 按客户 |
| By product | Mengikut produk | 按产品 |
| By month | Mengikut bulan | 按月份 |
| Sales order list | Senarai pesanan jualan | 销售订单列表 |
| Delivery order list | Senarai pesanan penghantaran | 送货单列表 |
| Both lists | Kedua-dua senarai | 两个列表 |
| No open sales order. | Tiada pesanan jualan terbuka. | 没有未完成的销售订单。 |
| No outstanding delivery order. | Tiada pesanan penghantaran tertunggak. | 没有未送达的送货单。 |
| Sales order figures are not enabled for your account. | Angka pesanan jualan tidak diaktifkan untuk akaun anda. | 您的账户未开通销售订单数据。 |
| Reply with a number for detail: | Balas dengan nombor untuk butiran: | 回复数字查看详情： |
| Reply 1 for the sales order list. | Balas 1 untuk senarai pesanan jualan. | 回复 1 查看销售订单列表。 |
| Reply 1 for the delivery order list. | Balas 1 untuk senarai pesanan penghantaran. | 回复 1 查看送货单列表。 |
| No sales found. | Tiada jualan ditemui. | 未找到销售记录。 |
| By quantity or by amount? | Mengikut kuantiti atau amaun? | 按数量还是按金额？ |
| Do you want the top items inside one category, or the categories ranked against each other? | Anda mahu item teratas dalam satu kategori, atau kategori disusun antara satu sama lain? | 您要看某一类别内的热销项目，还是各类别之间的排名？ |
| Delivered (transferred to DO) or ordered? | Dihantar (dipindahkan ke DO) atau dipesan? | 已送货（已转 DO）还是已订购？ |
| Sorry, I can only share sales figures for your own account. | Maaf, saya hanya boleh berkongsi angka jualan untuk akaun anda sendiri. | 抱歉，我只能提供您本人账户的销售数据。 |
| Items with no sale in this period are not ranked. | Item tanpa jualan dalam tempoh ini tidak disenaraikan. | 此期间没有销售的项目不参与排名。 |
| Categories with no sale in this period are not ranked. | Kategori tanpa jualan dalam tempoh ini tidak disenaraikan. | 此期间没有销售的类别不参与排名。 |
| Reply with a rank number to see that category's top items. | Balas dengan nombor kedudukan untuk melihat item teratas kategori itu. | 回复排名数字查看该类别的热销项目。 |
| Reply with a rank number to see that item's customers and months. | Balas dengan nombor kedudukan untuk melihat pelanggan dan bulan bagi item itu. | 回复排名数字查看该项目的客户和月份。 |
| Sales report is not enabled for your account. | Laporan jualan tidak diaktifkan untuk akaun anda. | 您的账户未开通销售报告。 |
| Note: only {pct}% of sales orders in this period carry a sales agent. | Nota: hanya {pct}% pesanan jualan dalam tempoh ini mempunyai ejen jualan. | 注：此期间只有 {pct}% 的销售订单带有销售代理。 |
| Top {n} selling items | {n} item paling laris | 最畅销的 {n} 个项目 |
| Top {n} selling categories | {n} kategori paling laris | 最畅销的 {n} 个类别 |
| Bottom {n} selling items | {n} item paling kurang laris | 最滞销的 {n} 个项目 |
| Bottom {n} selling categories | {n} kategori paling kurang laris | 最滞销的 {n} 个类别 |
| Top selling items | Item paling laris | 最畅销项目 |
| Top selling categories | Kategori paling laris | 最畅销类别 |
| Least sold items | Item paling kurang dijual | 销量最少的项目 |
| Least sold categories | Kategori paling kurang dijual | 销量最少的类别 |
| {code}: customers and months | {code}: pelanggan dan bulan | {code}：客户和月份 |
| How many items do you want to see? Reply with a number from 1 to {max}. | Berapa banyak item yang anda mahu lihat? Balas dengan nombor dari 1 hingga {max}. | 您想看多少个项目？请回复 1 到 {max} 之间的数字。 |
| How many categories do you want to see? Reply with a number from 1 to {max}. | Berapa banyak kategori yang anda mahu lihat? Balas dengan nombor dari 1 hingga {max}. | 您想看多少个类别？请回复 1 到 {max} 之间的数字。 |
| I can list at most the top {n} in one reply. | Saya boleh senaraikan paling banyak {n} teratas dalam satu balasan. | 我一次最多只能列出前 {n} 个。 |

The presenter's singular forms ("Top 1 selling item", "Top selling item") get the same
treatment: add the singular entries alongside the plural ones (the coder lists them from the
source at `presenters.py:~2777-2783`). The "How many ... see?" sentence is ONE line in the
presenter (`:2915-2916`), so it is one catalog entry per noun.

- **AC-CL30:** every outstanding/top-selling fixed literal listed above is catalogued (pinned).
- **AC-CL31:** `Localizer.lines` rules 1-4. Wrapped headings keep their wrapper. Numbered lines
  keep the number. Label lines translate the label only. `all` and the date range translate.
  `1. SRTWC286: Qty 3, RM 1,234.50`, `Sep 2026: Qty 3, RM 5.00`, `Unassigned: 5 (O/S: 2)`
  (label part) and `(2/3)` are unchanged. Unknown lines are unchanged. The English localizer
  is the identity.
- **AC-CL32:** an outstanding report rendered ms: header lines, block lines and the offer
  sentence are translated, and every number, code and name is byte-identical to the English
  render. The detail roster (`answers`/`result_set`, the armed offer options) is IDENTICAL to
  the English render: the parse ran on English.
- **AC-CL33:** top selling zh: title, "Ranked by: 金额"/"数量", basis, notes and the trailing
  ask are translated. Rank lines are unchanged, and the `result_set` roster is identical to
  English. A part-split reply keeps its `(k/m)` markers.
- **AC-CL34:** the outstanding offer re-print (stored English `offer_text`) prints in the
  turn's language when re-asked.
- **AC-CL35:** no localizer or `en`: both reports are byte-identical to today.

## Known gaps (slice 4)

- The multi-company total-miss line `*{names}:* no {what} records found for ...`
  (`lanes/business/fetch.py` ~3168-3187) and the presenter's "A or B" company join
  (`presenters.py::_company_names`, ~1641) stay English until slice 4.
- The EXPIRED / PENDING ALLOCATION / PARTIAL ALLOCATION flag lines are translated, but no slice 2
  tool sets those flags today.
- The summary table rows above: the truncation notice key carries an em dash in the presenter
  (the catalog spells it with an escape); SO and DO labels are identity entries, kept so the
  table is explicit.

**Rulings on the tester's contract questions (2 Oct):**
- Rule 3 carries the explicit `VALUE_WORDS` set (AC-CL33's `Ranked by: 金额` stands).
- Rule 3 covers the bold detail rows of `_outstanding_detail` (`1. *SO Number:* X`, `*DO Qty:* 5`).
- AC-CL34's seams are `compose_question(pending, state, localizer=None)` and
  `_outstanding_detail_reoffer(..., localizer=None)`; the engine passes the turn's localizer
  (`engine.py:~5378`, `lanes/business/__init__.py:~1385`). The stored `filters.offer_text`
  stays English.
- Singular titles are tested by behaviour, not exact keys. The ceiling note is localized
  through the lane notes that `fetch.py:~2553` prepends.

# Slice 4: the final reply pass (composer sentences, escalate offer, canned copy, dealer questions)

**Finding (scout, 2 Oct, head bbd79ef8):** no live code decides an accepted escalation by its
English text.
- `head/output_exchange` is gone, and `session_state.offer_is_open()` reads `pending.kind`.
- Acceptance is the parser's verdict, `is_escalation_confirmation` (`turn/decide.py:699-713`).
- The English matchers that remain all run BEFORE the reply is sent:
  - stripping an offer: `dealer_stock._ESCALATION` (:27), `order_list._OFFER_SENTENCE` (:226),
    `escalation_control.strip_text`;
  - block placement: `tail/compose.MARKERS`;
  - one offer per turn: `sub_answer._ESCALATE_OFFER_RE`;
  - company insertion: `reply_ladder` / `member_offer._ESCALATE_TO_TEAM_RE`.

So card Q5's precondition ("matchers read a stored flag first") is met for acceptance. The
pre-send matchers keep working because the translation happens after them.

**Design: one final pass at the send point.** `Localizer.reply(text) -> str` runs on the turn's
final reply text and on every `send_message` action's text, after every composer, stripper and
matcher, right before the actions are returned. It:
1. applies `lines()` (rules 1-4), then
2. replaces each **inline sentence** from the catalog's `INLINE` set (exact or `{token}`
   template). A sentence matches only where it starts at the start of a line or after
   `". "`, `"? "`, `"! "`, `"\n"`, and only where the English shape matches through its final
   punctuation. Tokens are re-inserted verbatim.

Everything not in the catalog is unchanged. The pass is idempotent: an already-localized line
matches no English key.

Rules for the pass:
- **Identity:** an `en`/IDENTITY turn is byte-identical to today.
- **What the parser reads:** the stored reply (`previous_reply_text`) is the localized one. The
  parser reads the dealer's next message against it, and acceptance stays the verdict.
- **Registry-editable copy:** the English-only `chatbot_reply_copy` keys are translated only when
  the rendered English equals the shipped default. A staff-edited English registry text stays as
  edited, in English. Known and accepted: the registry is the English source.
- **Joiners:** `_join_words` / `_join_words_and` take the localizer. " or " becomes " atau " /
  "或", and " and " becomes " dan " / "和", inside compose's own sentences only, never inside a
  presenter value.
- **Domain labels** (`turn/policy_rows.py` `label=`) are catalogued as words, so
  `"*{label}* for {codes}:"`, `"I could not fetch {label} just now, please try again."`,
  `"*{label}*: this is not enabled for your account."` and `"Nothing on {x} either."` render
  with the translated label.

| English | ms | zh |
|---|---|---|
| stock | stok | 库存 |
| orders | pesanan | 订单 |
| incoming stock | stok masuk | 到货库存 |
| promotions | promosi | 促销 |
| forms | borang | 表格 |
| product information | maklumat produk | 产品信息 |
| product attachments | lampiran produk | 产品附件 |
| resource attachments | lampiran sumber | 资料附件 |
| goods receive | penerimaan barang | 收货 |
| last in | kemasukan terakhir | 最近入库 |
| outstanding purchase orders | pesanan belian tertunggak | 未完成采购订单 |
| last purchase cost | kos belian terakhir | 最近采购成本 |
| this request | permintaan ini | 此请求 |
| *{label}* for {codes}: | *{label}* untuk {codes}: | {codes} 的*{label}*： |
| I could not fetch {label} just now, please try again. | Saya tidak dapat mendapatkan {label} sekarang, sila cuba lagi. | 暂时无法获取{label}，请稍后再试。 |
| *{label}*: this is not enabled for your account. | *{label}*: ini tidak diaktifkan untuk akaun anda. | *{label}*：您的账户未开通此功能。 |
| Nothing on {names} either. | Tiada juga untuk {names}. | {names} 也没有。 |
| Not checked: {names}. | Tidak disemak: {names}. | 未检查：{names}。 |
| I could not find {names}. | Saya tidak dapat menemui {names}. | 找不到 {names}。 |
| I have attached the file(s) below. | Saya telah melampirkan fail di bawah. | 我已在下方附上文件。 |
| Would you like me to escalate to {team} team? | Adakah anda mahu saya rujuk kepada pasukan {team}? | 需要我转交给 {team} 团队吗？ |
| Would you like me to escalate? | Adakah anda mahu saya rujuk kepada pasukan kami? | 需要我转交给相关团队吗？ |
| Would you like me to escalate to *{company}* {team} team? | Adakah anda mahu saya rujuk kepada pasukan {team} *{company}*? | 需要我转交给 *{company}* 的 {team} 团队吗？ |
| I am sorry the provided answer does not meet your requirements. Would you like me to escalate to {team} team? | Maaf, jawapan yang diberikan tidak memenuhi keperluan anda. Adakah anda mahu saya rujuk kepada pasukan {team}? | 抱歉，所提供的答案未能满足您的需求。需要我转交给 {team} 团队吗？ |
| I am sorry the provided answer does not meet your requirements. Would you like me to escalate this to our team? | Maaf, jawapan yang diberikan tidak memenuhi keperluan anda. Adakah anda mahu saya rujuk perkara ini kepada pasukan kami? | 抱歉，所提供的答案未能满足您的需求。需要我转交给我们的团队吗？ |
| No it's okay | Tidak mengapa | 不用了 |
| Yes, escalate | Ya, rujuk | 是，转交 |
| No, it's okay | Tidak, tidak mengapa | 不，不用了 |
| Which team should take this? | Pasukan mana yang patut uruskan ini? | 应由哪个团队处理？ |
| Which company do you mean? | Syarikat mana yang anda maksudkan? | 您指的是哪家公司？ |
| Who should take this? | Siapa yang patut uruskan ini? | 应由谁处理？ |
| Outstanding for which document? | Tertunggak untuk dokumen mana? | 查看哪种单据的未完成数量？ |
| Which list would you like? | Senarai mana yang anda mahu? | 您要哪个列表？ |
| Which one do you mean? | Yang mana satu anda maksudkan? | 您指的是哪一个？ |
| Which kind of file do you need? | Jenis fail apa yang anda perlukan? | 您需要哪种文件？ |
| Which item do you mean? Reply with a rank number from 1 to {count}. | Item mana yang anda maksudkan? Balas dengan nombor kedudukan dari 1 hingga {count}. | 您指的是哪个项目？请回复 1 到 {count} 之间的排名数字。 |
| Please reply with a number from {min} to {max}. | Sila balas dengan nombor dari {min} hingga {max}. | 请回复 {min} 到 {max} 之间的数字。 |
| How many units for each? | Berapa unit untuk setiap satu? | 每个需要多少件？ |
| How many units of {code}? | Berapa unit {code}? | {code} 需要多少件？ |
| Which one do you need {qty} of? | Yang mana satu anda perlukan sebanyak {qty}? | 您需要 {qty} 件的是哪一个？ |
| Which one? | Yang mana satu? | 哪一个？ |
| {typed} x {qty}: which one? | {typed} x {qty}: yang mana satu? | {typed} x {qty}：哪一个？ |
| {typed} matches {total} products. Which one? | {typed} sepadan dengan {total} produk. Yang mana satu? | {typed} 匹配到 {total} 个产品。哪一个？ |
| Couldn't find {shown}. Did you mean {label}? | Tidak dapat menemui {shown}. Adakah anda maksudkan {label}? | 找不到 {shown}。您是指 {label} 吗？ |
| Couldn't find {shown}. Did you mean: | Tidak dapat menemui {shown}. Adakah anda maksudkan: | 找不到 {shown}。您是指： |
| Please refer to your salesman. | Sila rujuk jurujual anda. | 请联系您的销售员。 |
| Sorry, you are not allowed to access {team} | Maaf, anda tidak dibenarkan mengakses {team} | 抱歉，您无权访问 {team} |
| Please specify your demand quantity | Sila nyatakan kuantiti yang anda perlukan | 请说明您需要的数量 |
| Okay, noted. | Baik, dicatat. | 好的，已记录。 |
| Escalation declined. | Rujukan dibatalkan. | 已取消转交。 |
| Sorry, I ran into a problem understanding that. Please try again in a moment. | Maaf, saya menghadapi masalah memahami mesej itu. Sila cuba lagi sebentar lagi. | 抱歉，我暂时无法理解您的信息，请稍后再试。 |
| Sorry, I didn't get that. What would you like to change in the ranking? | Maaf, saya tidak faham. Apa yang anda mahu ubah dalam senarai kedudukan? | 抱歉，我没听明白。您想如何调整排名？ |
| Low stock report is not enabled for your account. | Laporan stok rendah tidak diaktifkan untuk akaun anda. | 您的账户未开通低库存报告。 |
| Could not run the low stock report right now. | Laporan stok rendah tidak dapat dijalankan sekarang. | 暂时无法生成低库存报告。 |
| Both | Kedua-duanya | 两者 |
| Which customer is this outstanding report for? | Laporan tertunggak ini untuk pelanggan mana? | 这份未完成报告是哪个客户的？ |
| Which category do you mean? Reply with one code: {codes} | Kategori mana yang anda maksudkan? Balas dengan satu kod: {codes} | 您指的是哪个类别？请回复一个代码：{codes} |
| I could not match any product code in that photo. | Saya tidak dapat memadankan sebarang kod produk dalam gambar itu. | 我无法在那张照片中匹配到任何产品代码。 |
| What would you like me to do with it? | Apa yang anda mahu saya lakukan dengannya? | 您希望我怎么处理？ |
| Ask again with the correct code. | Sila tanya semula dengan kod yang betul. | 请用正确的代码再问一次。 |
| I read {codes} from that photo. | Saya membaca {codes} daripada gambar itu. | 我从那张照片中读到 {codes}。 |

The coder copies each English key EXACTLY from source; the scout's report gives the sites:
- `turn/compose.py:35/364-881`;
- `turn/task.py:58/362-378/814-823`;
- `dealer_stock.py:101-104`;
- `lanes/business/__init__.py:91-117/554-569/648`;
- `chatbot_reply_copy.py:52-59/279-340/96`;
- the `answer.py` offer suffixes (`:1727/2201/2232/2709/3138/3711/4438/4818/4849...5121`).

The `answer.py` offer suffixes (`", or would you like me to escalate to {team} team?"`, the
`"Reply with a number to continue"` leads, `"Reply 'all dates' to search without ..."`) are
catalogued as inline templates in the same pattern. If a suffix shape cannot be matched by a
whole-sentence template, catalog the whole line its builder prints.

**Out of scope, recorded:**
- `clarify_menu` (multi-line copy with `{{user_goal}}` parser prose).
- The internal `out_of_scope` notes.
- The parser prompt's English examples. The parser is multilingual and reads the localized
  previous reply.
- The admin Translations page language filter (one query param, slice 5 if wanted).
- The required-fields helper (waits on #1445).

- **AC-CL40:** `Localizer.reply` rules: identity for en; `lines()` then inline sentences;
  sentence-boundary matching (an English catalog sentence inside a product name or after a
  non-boundary is NOT replaced); tokens verbatim; idempotent (twice = once).
- **AC-CL41:** the engine applies `reply` once, at the send point, to the reply text and every
  `send_message` text, AFTER the strip/marker/company-insert matchers. One engine-console test:
  - an ms dealer turn that misses ends with "Adakah anda mahu saya rujuk kepada pasukan
    <team>?";
  - the pending offer still arms;
  - a following "ya" (parser verdict `is_escalation_confirmation`) still escalates.
- **AC-CL42:** a barred contact's / dealer's offer stripping still works on an ms turn (the
  stripping ran on English; the refer line appears once, translated).
- **AC-CL43:** the dealer quantity questions and did-you-mean (task.py, dealer_stock) render ms/zh.
- **AC-CL44:** a staff-edited English registry copy (render differs from the shipped default)
  stays English in an ms turn.
- **AC-CL45:** an en turn's full reply and actions are byte-identical to before slice 4 (run the
  existing console/engine suites green).

## Known gap (slice 3 fix round)

- The outstanding detail offer's quick-reply buttons (`turn/compose.py` ~844) stay English: the
  typed-answer matcher `decide._positions_by_label` compares a typed answer with the English
  option labels. The printed offer text is localized; the buttons follow in a later slice.
- Rule 3 skips a value shaped like a breakdown row (`5 (O/S: 2)`), so a customer named like a
  catalog label stays untouched. A `Qty ...` value is NOT skipped: `Total: Qty 3, RM 5.00` is a real
  totals line and must translate (AC-CL31, AC-CL33).

**Slice 4 rulings on the tester's contract questions (2 Oct):**
1. **Overlap:** when several INLINE templates match at the same position, the most specific one
   wins. Most specific means the most fixed (non-token) characters, so the company-form offer
   beats `{team} team?` and the whole apology sentence beats its offer suffix.
2. **Edited registry copy:** only copy staff have FULLY edited is guaranteed to stay English. If
   an edit keeps a standard catalog sentence verbatim, that sentence is still translated by the
   inline pass. This is accepted: the sentence is the shipped wording.
3. **Token boundaries:** a `{token}` matches lazily and never spans a newline. A template match
   must end at the template's final punctuation, followed by end of line or a space.

**Slice 4 fix round (review, 2 Oct):**

- **Labels and joiners move to compose, as designed.** `turn/compose.py` passes the localizer to
  `_join_words` / `_join_words_and` (" or " becomes " atau " / "或", " and " becomes " dan " /
  "和"). It translates the domain label (`row.label`, the policy word, never a value) before
  building `"*{label}* for {codes}:"`, `"I could not fetch {label} ..."`,
  `"*{label}*: this is not enabled ..."` and `"Nothing on {names} either."`. The final pass then
  only swaps the fixed sentence around tokens it keeps verbatim. Nothing in `reply()` splits a
  token value: `_word_token` goes.
- **Messages answered ahead** (`engine.py:~2610`) keep the language their own turn chose. Each
  is localized with its own item's `reply_language`, or left as composed when that item carries
  none.
- **The trace** shows what was sent. `_localize_result` patches the trace's `sent` / `replied`
  raw records the way `_repersist_media_prefixed_reply` (`engine.py:~6344`) does.

New catalog rows:

| English | ms | zh |
|---|---|---|
| Your request is out of the scope of my ability and require human assistance. We are directing your enquiry to the correct person. Please wait for a moment. | Permintaan anda di luar kemampuan saya dan memerlukan bantuan kakitangan. Kami sedang menghubungkan pertanyaan anda kepada orang yang betul. Sila tunggu sebentar. | 您的请求超出了我的能力范围，需要人工协助。我们正在将您的询问转交给相关负责人，请稍候。 |
| This inquiry has been routed to the respective person-in-charge (PIC) from {team} team. We will get back to you soon. Thanks for your patience. | Pertanyaan ini telah diserahkan kepada pegawai bertanggungjawab (PIC) daripada pasukan {team}. Kami akan menghubungi anda tidak lama lagi. Terima kasih atas kesabaran anda. | 此询问已转交给 {team} 团队的相关负责人（PIC）。我们会尽快回复您，感谢您的耐心等待。 |
| {header} I found {count}, please type a little more of the name. | {header} Saya menemui {count}, sila taip lebih sedikit daripada nama itu. | {header}我找到 {count} 个，请多输入一些名称。 |
| and {n} others, reply with the full code. | dan {n} lagi, balas dengan kod penuh. | 还有 {n} 个，请回复完整代码。 |
| Couldn't find {names}. | Tidak dapat menemui {names}. | 找不到 {names}。 |
| I have {names}. | Saya ada {names}. | 我已记下 {names}。 |
| What would you like me to know? | Apa yang anda mahu saya tahu? | 您想让我了解什么？ |
| Sorry, we don't support direct goods receive & SPO at the moment. You may ask about incoming stock for a specific product or container | Maaf, kami belum menyokong penerimaan barang terus & SPO buat masa ini. Anda boleh bertanya tentang stok masuk bagi produk atau kontena tertentu | 抱歉，我们目前暂不支持直接收货和 SPO 查询。您可以询问特定产品或货柜的到货库存 |
| Do you mean customer {customer} or sales agent {agent}? Reply 1 for the customer, 2 for the sales agent. | Adakah anda maksudkan pelanggan {customer} atau ejen jualan {agent}? Balas 1 untuk pelanggan, 2 untuk ejen jualan. | 您是指客户 {customer} 还是销售代理 {agent}？回复 1 选择客户，回复 2 选择销售代理。 |
| Do you mean a customer named '{word}' or sales agent {agent}? Reply 1 for the customer, 2 for the sales agent. | Adakah anda maksudkan pelanggan bernama '{word}' atau ejen jualan {agent}? Balas 1 untuk pelanggan, 2 untuk ejen jualan. | 您是指名为 '{word}' 的客户还是销售代理 {agent}？回复 1 选择客户，回复 2 选择销售代理。 |

`"{header} I found ..."`: the coder renders the header through the localizer first, the same
way as labels. The offer-hold clause `" - reply a number, a name, or the company ({companies})
and I'll assign automatically."` and its no-companies twin are catalogued as the WHOLE line
`lanes/canned.py:~170` prints (lead + clause). The coder reads the lead's exact text, and if
that lead is dynamic, records it as a Known gap.

## Known gaps (slice 4 fix round)

- The offer-hold clarify line (`lanes/canned.py` ~160-170) stays English: its lead is built from
  the live team names (`"{joined} teams are listed"`, or `"More than one team is listed"`) and the
  clause is registry-editable copy (`offer_hold`, `offer_hold_no_companies`), so there is no fixed
  whole line to catalogue. The sentences inside the final pass that it shares with other builders
  still translate.

# Crew-tester round 1 fix (3 Oct): incoming answer and the cross-domain block

Finding 1 of the crew-tester pass on 1168333a. An ms / zh stock ask whose code had no stock
printed the cross-domain block under it in English. That block holds the "But there is INCOMING
stock (ETA)" lead, the `- *Company:*` rows with `*Container:*` and the clearance labels, the flag
lines, "No stock for X.", "No stock and no incoming for X." and the zero and PO-rung sentences.
The "Couldn't find: X." line stayed English too. A direct incoming answer also stayed English.

- **AC-CLI1:** every incoming label the presenter prints (`_incoming_list`, `_incoming_by_product`,
  `_incoming_shipments`, `_CLEARANCE_PAIRS`) is catalogued, except ETA, ETC and ETD (Q3).
- **AC-CLI2:** the three incoming tools are localized tools, so a direct incoming answer renders
  its labels and intro in the reply language.
- **AC-CLI3:** the final pass reads a `- ` led line the way it reads a `<n>. ` led one: a
  catalogued label translates and the value never does. An uncatalogued label, a bare code or a
  line with no label stays as it is.
- **AC-CLI4:** a flag line (`<mark>  *(<flag>)*`) translates its flag when it is catalogued; the
  mark and anything uncatalogued stay as they are.
- **AC-CLI5:** the cross-domain lead and absence sentences, the four zero and plain pairs, the
  eight PO-rung "found" headers, the four "nothing on order" sentences and "Couldn't find: {names}."
  are catalogued. Two of them can share a line (INLINE). Values stay verbatim.

Tests: `tests/chatbot/test_chat_language_incoming.py`. They render the block with the real
`crossdomain_render` and pin the rung f-strings and the presenter literals.

Finding 2 ("ya" after the Malay offer) is the parser model's verdict (#1323, no deterministic
matcher). It waits on the crew's English control run. Finding 3 (ranking) needs a contact with
the sales report grant and sales in the period. Finding 4 was a wrong Expected in the hand-test
script, corrected on the PR.

# Cloud browser pass fix (3 Oct): the Translations page tells the languages apart

The cloud pass found the page listing the chatbot's en->ms and en->zh rows beside the supplier
zh->en rows, all under a column headed "English", with no way to tell a Malay row from a
Chinese one for the same source. That is plan slice 4's "Translations admin page filter by
language pair", which had not been built.

- **AC-CLT1:** each row shows its language pair (`Chinese to English`, `English to Malay`,
  `English to Chinese`), and the editable column is headed "Translation" (input label
  `Translation for <source>`).
- **AC-CLT2:** a clearable filter narrows the list to the language a row translates into
  (`target_lang` = en / ms / zh on `GET /api/v1/system/translations`; anything else is a 422).

Tests: `tests/test_translations_routes.py::test_list_filters_by_target_language` and
`TranslationsList.test.tsx`.

# Slice 5 (3 Oct): the required-fields helper (#1445) and the attachment gap line (#1437)

#1445 landed `app/services/chatbot/required_fields.py` (LOWSTOCK-FILTER-ASK) with fixed English
replies, and #1437 added the composer line `"<code> has no <type>."`. In an ms / zh turn these
printed English. Each reply leaves through the turn's single final pass (`engine._localize_result`),
so the field question is a catalog key: the English sentence is the key, and the reply reads in the
turn's language through the same pass every other composer sentence uses.

- **AC-CL51:** every sentence `required_fields` and `low_stock_ask` print reads in ms / zh in an
  ms / zh turn. That covers the field questions (`QUESTION`, "Which supplier?"), the miss line
  `"I don't know '<word>' as a <noun>."`, the pick head (`"Which <noun> do you mean? Reply with a
  number"` with and without `or "all"`), `CANCELLED` and `GIVE_UP`. The typed word, the numbered
  option labels and `"all"` stay as printed (values).
- **AC-CL52:** an English turn is byte-identical to main.
- **AC-CL53:** the next turn still reads the answer. `reply_verdict` and the open slot never
  depend on the English text of the question (pinned with an ms turn whose answer settles the field).
- **AC-CL54:** `"<code> has no <type>."` (#1437) reads in ms / zh; the code and the type names stay
  as printed, and the joiner between two types (" or ") is localized. The composer builds the line
  with `Localizer.fill` (a `DIRECT_ONLY` key), so the final pass never matches it inside running
  text: a product name such as `Basin that has no hole` stays as printed.
- **AC-CL55:** a catalog pin fails if any of these English literals changes in the source.

Known gap, unchanged: the words a contact may TYPE (`ALL_WORDS`, `CANCEL_WORDS`) are input
matching, not reply wording, and are not part of this slice.

Known gaps (slice 5 review):

- The low stock filter header (`"Low stock report (<category>, all suppliers, by supplier / no
  grouping)"`, built by `low_stock_ask.route_filters` and printed above every low stock reply)
  stays English. It is assembled from phrase fragments around values, not fixed sentences, so it
  needs its own pass; it is left for a follow-up rather than widening this slice.
- Top selling's notes now translate `"I don't know '<word>' as a category."` (it shares the
  catalog key), but its siblings `"I don't know '<word>' as a <kind>."` for other kinds and
  `"I don't know '<word>'."` do not, so a notes line can come out mixed. That gap predates this
  slice.
