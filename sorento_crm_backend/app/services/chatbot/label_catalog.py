"""CHAT-LANGUAGE: the label catalog and the `Localizer` (plan `PLAN-chat-language-2oct.md`).

Only labels and fixed sentences are translated, and only the ones listed here (an allowlist):
a value, a code, a number or an uncatalogued label comes back unchanged. The key is the ENGLISH
source text, the same key `translation_memory.source_text` uses, so staff correct the wording on
the Translations page and a `manual` row wins. Pure apart from `resolve`, the one DB seam.
"""
from __future__ import annotations

import logging
import re
from typing import Any

# The one spelling of the refer line (CUSTOMER-ASKS-REFER-ONLY guards): the verdict keys below
# are built from it, never spelled out.
from app.services.chatbot.turn.task import REFER_TO_SALESMAN

# The placeholder helpers live in a dependency-free core module (a staff edit is checked there
# too); the chatbot package may import core, never the reverse (AC-002).
from app.services.text_tokens import _TOKEN, tokens, tokens_match  # noqa: F401

logger = logging.getLogger(__name__)

LANGUAGES = ("en", "ms", "zh")

#: English source text -> {"ms": ..., "zh": ...}. `{name}` placeholders must survive translation.
# The presenter's own truncation sentence (`summary_intro`), which carries an em dash; escaped
# here so this source stays dash-free.
_NOT_EVERY_BREAKDOWN = "Not every breakdown is shown \u2014 add a customer, a product or a date range."
# `output_structurer`'s denied-field note (one per line), escaped the same way.
_CANT_SHARE = "I can't share the {label} \u2014 please check with the office."

LABELS: dict[str, dict[str, str]] = {
    "Company": {"ms": "Syarikat", "zh": "公司"},
    "Product Code": {"ms": "Kod Produk", "zh": "产品代码"},
    "Product Name": {"ms": "Nama Produk", "zh": "产品名称"},
    "Warehouse": {"ms": "Gudang", "zh": "仓库"},
    "System Location": {"ms": "Lokasi Sistem", "zh": "系统位置"},
    "Quantity On Hand": {"ms": "Kuantiti Ada", "zh": "现有数量"},
    "Outstanding": {"ms": "Tertunggak", "zh": "未交货"},
    "Total": {"ms": "Jumlah", "zh": "总数"},
    "Stock summary for the requested products.": {
        "ms": "Ringkasan stok untuk produk yang diminta.",
        "zh": "所请求产品的库存摘要。",
    },
    "Stock details found for the requested products.": {
        "ms": "Butiran stok untuk produk yang diminta.",
        "zh": "所请求产品的库存详情。",
    },
    "How many units do you need?": {
        "ms": "Berapa unit yang anda perlukan?",
        "zh": "您需要多少件？",
    },
    f"yes, we have stock. {REFER_TO_SALESMAN}": {
        "ms": "ya, stok ada. Sila rujuk jurujual anda.",
        "zh": "有库存。请联系您的销售员。",
    },
    f"no stock and no incoming at the moment. {REFER_TO_SALESMAN}": {
        "ms": "tiada stok dan tiada barang masuk buat masa ini. Sila rujuk jurujual anda.",
        "zh": "目前没有库存，也没有到货。请联系您的销售员。",
    },
    f"the quantity is more than what I can confirm here. {REFER_TO_SALESMAN}": {
        "ms": "kuantiti ini melebihi apa yang boleh saya sahkan di sini. Sila rujuk jurujual anda.",
        "zh": "这个数量超出我在这里可以确认的范围。请联系您的销售员。",
    },
    "no stock at the moment, ETA {eta}.": {
        "ms": "tiada stok buat masa ini, ETA {eta}.",
        "zh": "目前没有库存，ETA {eta}。",
    },
    # AVAIL-MODE-REPLIES (#1430) verdict wording; the leading status mark is not text and
    # stays as printed (`Localizer.tail`).
    f"No stock and no incoming. {REFER_TO_SALESMAN}": {
        "ms": "Tiada stok dan tiada stok masuk. Sila rujuk jurujual anda.",
        "zh": "没有库存，也没有到货。请联系您的销售员。",
    },
    # AVAIL-RENDER (owner, 5 Oct 2026): the tick says got stock and does not refer.
    "Got stock.": {"ms": "Stok ada.", "zh": "有库存。"},
    # AVAIL-ONHAND-BASIS hand test (owner, 6 Oct 2026): the partial line is "Only N got
    # stock." (was "Got stock, N available."). The owner gave the en and zh; the ms is the
    # lane's rendering of the same sentence, flagged in PR #1510 for the owner to confirm.
    "Only {available} got stock.": {
        "ms": "Hanya {available} ada stok.",
        "zh": "只有 {available} 件有库存。",
    },
    # AVAIL-RENDER fix round (owner hand test, 6 Oct 2026): the incoming line says the stock
    # state too; the bare "ETA {eta}." row it replaces is printed by nothing now.
    "No stock, ETA {eta}.": {"ms": "Tiada stok, ETA {eta}.", "zh": "没有库存，ETA {eta}。"},
    "No ETA": {"ms": "Tiada ETA", "zh": "暂无 ETA"},
    "Data last updated: {ts}": {
        "ms": "Data dikemas kini: {ts}",
        "zh": "数据更新时间: {ts}",
    },
    "No stock found for {codes}.": {
        "ms": "Tiada stok ditemui untuk {codes}.",
        "zh": "未找到 {codes} 的库存。",
    },
    "Here are the results.": {"ms": "Berikut ialah hasilnya.", "zh": "以下是结果。"},
    "PRODUCT DISCONTINUED": {"ms": "PRODUK DIHENTIKAN", "zh": "产品已停产"},
    # DISCONTINUED-ASK (owner Q4 a, 8 Oct 2026): the status line both ways, the card's field
    # line beside another property (Q1 a), and the stored alternative (Q5 a, #1533).
    "{code}: ⚠️ Discontinued.": {"ms": "{code}: ⚠️ Dihentikan.", "zh": "{code}：⚠️ 已停产。"},
    "{code}: ✅ Not discontinued.": {"ms": "{code}: ✅ Tidak dihentikan.", "zh": "{code}：✅ 未停产。"},
    # Whole lines, so no other "Yes" / "No" in any reply is touched.
    "*Discontinued:* ⚠️ Yes": {"ms": "*Dihentikan:* ⚠️ Ya", "zh": "*已停产:* ⚠️ 是"},
    "*Discontinued:* ✅ No": {"ms": "*Dihentikan:* ✅ Tidak", "zh": "*已停产:* ✅ 否"},
    "Alternative": {"ms": "Alternatif", "zh": "替代型号"},
    # PRODUCT-ALTERNATIVES (card Q4, owner 8 Oct 2026): the two refer-bearing verdicts without
    # their refer (it drops when an alternative printed under the line has stock); the
    # `Alternative` label itself is DISCONTINUED-ASK's entry above. The ms and zh are the
    # lane's rendering, flagged on PR #1533 for the owner to confirm.
    "No stock and no incoming.": {
        "ms": "Tiada stok dan tiada stok masuk.",
        "zh": "没有库存，也没有到货。",
    },
    # PRODUCT-ALTERNATIVES round B (owner, 9 Oct 2026): what an alternative's own search
    # found when it found nothing, on the incoming line (the one localised tool it reaches).
    "No incoming stock.": {"ms": "Tiada stok masuk.", "zh": "没有到货。"},
    "the quantity is more than what I can confirm here.": {
        "ms": "kuantiti ini melebihi apa yang boleh saya sahkan di sini.",
        "zh": "这个数量超出我在这里可以确认的范围。",
    },
    # Slice 2: PO / SO / SPO / orders rows. PO, SO, SPO and GR stay as printed (owner Q3).
    "Order Number": {"ms": "No. Pesanan", "zh": "订单号"},
    "Customer": {"ms": "Pelanggan", "zh": "客户"},
    "Branch": {"ms": "Cawangan", "zh": "分店"},
    "Order Date": {"ms": "Tarikh Pesanan", "zh": "订单日期"},
    "Actual Delivery Date": {"ms": "Tarikh Penghantaran Sebenar", "zh": "实际送货日期"},
    "Status": {"ms": "Status", "zh": "状态"},
    "Pickup Time": {"ms": "Masa Pengambilan", "zh": "提货时间"},
    "Transporter": {"ms": "Pengangkut", "zh": "运输商"},
    "Driver": {"ms": "Pemandu", "zh": "司机"},
    "Lorry Plate": {"ms": "No. Plat Lori", "zh": "车牌号"},
    "Products": {"ms": "Produk", "zh": "产品"},
    "SO Number": {"ms": "No. SO", "zh": "SO 编号"},
    "Outstanding Qty": {"ms": "Kuantiti Tertunggak", "zh": "未交货数量"},
    "Requested Delivery Date": {"ms": "Tarikh Penghantaran Diminta", "zh": "要求送货日期"},
    "PO Number": {"ms": "No. PO", "zh": "PO 编号"},
    "Ordered Qty": {"ms": "Kuantiti Dipesan", "zh": "订购数量"},
    "PO Date": {"ms": "Tarikh PO", "zh": "PO 日期"},
    "Location": {"ms": "Lokasi", "zh": "位置"},
    "Supplier": {"ms": "Pembekal", "zh": "供应商"},
    "SPO Number": {"ms": "No. SPO", "zh": "SPO 编号"},
    "Container Number": {"ms": "No. Kontena", "zh": "货柜号"},
    "SPO Quantity": {"ms": "Kuantiti SPO", "zh": "SPO 数量"},
    "GR Quantity": {"ms": "Kuantiti GR", "zh": "GR 数量"},
    "SPO Date": {"ms": "Tarikh SPO", "zh": "SPO 日期"},
    "SPO Date (recorded)": {"ms": "Tarikh SPO (direkodkan)", "zh": "SPO 日期（已记录）"},
    "GR Date": {"ms": "Tarikh GR", "zh": "GR 日期"},
    "PO Quantity": {"ms": "Kuantiti PO", "zh": "PO 数量"},
    "Cost / unit": {"ms": "Kos / unit", "zh": "单位成本"},
    "Discount / unit": {"ms": "Diskaun / unit", "zh": "单位折扣"},
    "Cost after discount / unit": {"ms": "Kos selepas diskaun / unit", "zh": "折后单位成本"},
    "Here are the orders I found.": {"ms": "Berikut ialah pesanan yang saya temui.", "zh": "以下是我找到的订单。"},
    "Here is the PO placed I found.": {"ms": "Berikut ialah PO yang telah dibuat.", "zh": "以下是已下的 PO。"},
    "Here is the last SPO line per product.": {"ms": "Berikut ialah baris SPO terakhir bagi setiap produk.", "zh": "以下是每个产品最近的 SPO 记录。"},
    "Here is the last purchase cost per product and location.": {"ms": "Berikut ialah kos belian terakhir bagi setiap produk dan lokasi.", "zh": "以下是每个产品和位置最近一次的采购成本。"},
    "Here is the outstanding SO I found.": {"ms": "Berikut ialah SO tertunggak yang saya temui.", "zh": "以下是我找到的未交货 SO。"},
    "Here are the outstanding orders I found.": {"ms": "Berikut ialah pesanan tertunggak yang saya temui.", "zh": "以下是我找到的未交货订单。"},
    "Here are the delivered orders I found.": {"ms": "Berikut ialah pesanan yang telah dihantar.", "zh": "以下是我找到的已送货订单。"},
    "No matching results found.": {"ms": "Tiada hasil yang sepadan ditemui.", "zh": "未找到匹配的结果。"},
    "No matching results found for {companies}.": {"ms": "Tiada hasil yang sepadan ditemui untuk {companies}.", "zh": "在 {companies} 中未找到匹配的结果。"},
    "Here are the results I found.": {"ms": "Berikut ialah hasil yang saya temui.", "zh": "以下是我找到的结果。"},
    "EXPIRED": {"ms": "TAMAT TEMPOH", "zh": "已过期"},
    # The order summary block (presenter `_SUMMARY_FIELDS`) and its truncation notice. SO and DO
    # stay as printed (owner Q3).
    # MULTI-ASK-PLAN R8 (owner, 9 Oct 2026): the product total row's customer COUNT. Was
    # "Customers" -> "客户", so "客户: 33" read as a name. A new key, so the new wording is
    # seeded fresh instead of losing to the stored `translation_memory` row of the old one.
    "Customer count": {"ms": "Bil. pelanggan", "zh": "客户数"},
    "SO": {"ms": "SO", "zh": "SO"},
    "SO Date": {"ms": "Tarikh SO", "zh": "SO 日期"},
    "Ordered": {"ms": "Dipesan", "zh": "订购"},
    "Transferred to DO": {"ms": "Dipindahkan ke DO", "zh": "已转 DO"},
    "SO Outstanding": {"ms": "SO Tertunggak", "zh": "SO 未交货"},
    "DO": {"ms": "DO", "zh": "DO"},
    "DO Date": {"ms": "Tarikh DO", "zh": "DO 日期"},
    "Delivered": {"ms": "Dihantar", "zh": "已送货"},
    "Delivery Date": {"ms": "Tarikh Penghantaran", "zh": "送货日期"},
    "DO Outstanding": {"ms": "DO Tertunggak", "zh": "DO 未送达"},
    _NOT_EVERY_BREAKDOWN: {
        "ms": "Tidak semua pecahan dipaparkan. Tambah pelanggan, produk atau julat tarikh.",
        "zh": "并非所有明细都已显示，请加上客户、产品或日期范围。",
    },
    # compose's "No <domain label> found for {codes}." (policy_rows.py labels: orders :155,
    # last in :269, outstanding purchase orders :293, last purchase cost :307; stock :134 is
    # the slice 1 entry above).
    "No orders found for {codes}.": {
        "ms": "Tiada pesanan ditemui untuk {codes}.",
        "zh": "未找到 {codes} 的订单。",
    },
    "No last in found for {codes}.": {
        "ms": "Tiada SPO ditemui untuk {codes}.",
        "zh": "未找到 {codes} 的 SPO。",
    },
    "No outstanding purchase orders found for {codes}.": {
        "ms": "Tiada PO ditemui untuk {codes}.",
        "zh": "未找到 {codes} 的 PO。",
    },
    "No last purchase cost found for {codes}.": {
        "ms": "Tiada kos belian ditemui untuk {codes}.",
        "zh": "未找到 {codes} 的采购成本。",
    },
    # Slice 3: the outstanding report and top selling (finished text, `Localizer.lines`). The
    # singular titles are the presenter's `Top 1 selling item` forms.
    "Sales orders": {"ms": "Pesanan jualan", "zh": "销售订单"},
    "Order date range": {"ms": "Julat tarikh pesanan", "zh": "订单日期范围"},
    "Delivery orders": {"ms": "Pesanan penghantaran", "zh": "送货单"},
    "DO qty": {"ms": "Kuantiti DO", "zh": "DO 数量"},
    "DO date range": {"ms": "Julat tarikh DO", "zh": "DO 日期范围"},
    "Product": {"ms": "Produk", "zh": "产品"},
    "Order date": {"ms": "Tarikh pesanan", "zh": "订单日期"},
    "Brand": {"ms": "Jenama", "zh": "品牌"},
    "DO Number": {"ms": "No. DO", "zh": "DO 编号"},
    "DO Qty": {"ms": "Kuantiti DO", "zh": "DO 数量"},
    "Category": {"ms": "Kategori", "zh": "类别"},
    "Sales agent": {"ms": "Ejen jualan", "zh": "销售代理"},
    # REPORT-DRILLDOWN: the sales ranking header's own labels (one fact per line).
    "Period": {"ms": "Tempoh", "zh": "期间"},
    "Customers assigned to": {"ms": "Pelanggan di bawah", "zh": "所属客户的业务员"},
    "Channel": {"ms": "Saluran", "zh": "渠道"},
    "Delivery date": {"ms": "Tarikh penghantaran", "zh": "送货日期"},
    "Ranked by": {"ms": "Disusun mengikut", "zh": "排名依据"},
    "Basis": {"ms": "Asas", "zh": "统计口径"},
    "Items with sales": {"ms": "Item dengan jualan", "zh": "有销售的项目"},
    "Categories with sales": {"ms": "Kategori dengan jualan", "zh": "有销售的类别"},
    "all": {"ms": "semua", "zh": "全部"},
    "{from} to {to}": {"ms": "{from} hingga {to}", "zh": "{from} 至 {to}"},
    "Amount": {"ms": "Amaun", "zh": "金额"},
    "Quantity": {"ms": "Kuantiti", "zh": "数量"},
    "Delivered (transferred to DO)": {"ms": "Dihantar (dipindahkan ke DO)", "zh": "已送货（已转 DO）"},
    "Sales order outstanding": {"ms": "Pesanan jualan tertunggak", "zh": "未交货销售订单"},
    "Delivery order outstanding": {"ms": "Pesanan penghantaran tertunggak", "zh": "未送达送货单"},
    "By location": {"ms": "Mengikut lokasi", "zh": "按位置"},
    "By customer": {"ms": "Mengikut pelanggan", "zh": "按客户"},
    "By product": {"ms": "Mengikut produk", "zh": "按产品"},
    "By month": {"ms": "Mengikut bulan", "zh": "按月份"},
    "Sales order list": {"ms": "Senarai pesanan jualan", "zh": "销售订单列表"},
    "Delivery order list": {"ms": "Senarai pesanan penghantaran", "zh": "送货单列表"},
    "Both lists": {"ms": "Kedua-dua senarai", "zh": "两个列表"},
    "No open sales order.": {"ms": "Tiada pesanan jualan terbuka.", "zh": "没有未完成的销售订单。"},
    "No outstanding delivery order.": {"ms": "Tiada pesanan penghantaran tertunggak.", "zh": "没有未送达的送货单。"},
    "Sales order figures are not enabled for your account.": {"ms": "Angka pesanan jualan tidak diaktifkan untuk akaun anda.", "zh": "您的账户未开通销售订单数据。"},
    "Reply with a number for detail:": {"ms": "Balas dengan nombor untuk butiran:", "zh": "回复数字查看详情："},
    "Reply 1 for the sales order list.": {"ms": "Balas 1 untuk senarai pesanan jualan.", "zh": "回复 1 查看销售订单列表。"},
    "Reply 1 for the delivery order list.": {"ms": "Balas 1 untuk senarai pesanan penghantaran.", "zh": "回复 1 查看送货单列表。"},
    # DO-SUMMARY-CAP (7 Oct 2026): the trailer under a capped By product / By customer group
    # (`presenters._outstanding_capped_group`), four forms: the noun, singular for K = 1. R3
    # (owner ruling, ask a69045557): a count only, no pointer; the offer line below it points.
    "... and {k} more products": {"ms": "... dan {k} lagi produk", "zh": "... 还有 {k} 个产品"},
    "... and {k} more product": {"ms": "... dan {k} lagi produk", "zh": "... 还有 {k} 个产品"},
    "... and {k} more customers": {"ms": "... dan {k} lagi pelanggan", "zh": "... 还有 {k} 个客户"},
    "... and {k} more customer": {"ms": "... dan {k} lagi pelanggan", "zh": "... 还有 {k} 个客户"},
    "No sales found.": {"ms": "Tiada jualan ditemui.", "zh": "未找到销售记录。"},
    "By quantity or by amount?": {"ms": "Mengikut kuantiti atau amaun?", "zh": "按数量还是按金额？"},
    "Do you want the top items inside one category, or the categories ranked against each other?": {"ms": "Anda mahu item teratas dalam satu kategori, atau kategori disusun antara satu sama lain?", "zh": "您要看某一类别内的热销项目，还是各类别之间的排名？"},
    "Delivered (transferred to DO) or ordered?": {"ms": "Dihantar (dipindahkan ke DO) atau dipesan?", "zh": "已送货（已转 DO）还是已订购？"},
    "Sorry, I can only share sales figures for your own account.": {"ms": "Maaf, saya hanya boleh berkongsi angka jualan untuk akaun anda sendiri.", "zh": "抱歉，我只能提供您本人账户的销售数据。"},
    "Items with no sale in this period are not ranked.": {"ms": "Item tanpa jualan dalam tempoh ini tidak disenaraikan.", "zh": "此期间没有销售的项目不参与排名。"},
    "Categories with no sale in this period are not ranked.": {"ms": "Kategori tanpa jualan dalam tempoh ini tidak disenaraikan.", "zh": "此期间没有销售的类别不参与排名。"},
    "Reply with a rank number to see that category's top items.": {"ms": "Balas dengan nombor kedudukan untuk melihat item teratas kategori itu.", "zh": "回复排名数字查看该类别的热销项目。"},
    "Reply with a rank number to see that item's customers and months.": {"ms": "Balas dengan nombor kedudukan untuk melihat pelanggan dan bulan bagi item itu.", "zh": "回复排名数字查看该项目的客户和月份。"},
    "Sales report is not enabled for your account.": {"ms": "Laporan jualan tidak diaktifkan untuk akaun anda.", "zh": "您的账户未开通销售报告。"},
    "Note: only {pct}% of sales orders in this period carry a sales agent.": {"ms": "Nota: hanya {pct}% pesanan jualan dalam tempoh ini mempunyai ejen jualan.", "zh": "注：此期间只有 {pct}% 的销售订单带有销售代理。"},
    "Top {n} selling items": {"ms": "{n} item paling laris", "zh": "最畅销的 {n} 个项目"},
    "Top {n} selling categories": {"ms": "{n} kategori paling laris", "zh": "最畅销的 {n} 个类别"},
    "Bottom {n} selling items": {"ms": "{n} item paling kurang laris", "zh": "最滞销的 {n} 个项目"},
    "Bottom {n} selling categories": {"ms": "{n} kategori paling kurang laris", "zh": "最滞销的 {n} 个类别"},
    "Top selling items": {"ms": "Item paling laris", "zh": "最畅销项目"},
    "Top selling categories": {"ms": "Kategori paling laris", "zh": "最畅销类别"},
    "Least sold items": {"ms": "Item paling kurang dijual", "zh": "销量最少的项目"},
    "Least sold categories": {"ms": "Kategori paling kurang dijual", "zh": "销量最少的类别"},
    "{code}: customers and months": {"ms": "{code}: pelanggan dan bulan", "zh": "{code}：客户和月份"},
    "How many items do you want to see? Reply with a number from 1 to {max}.": {"ms": "Berapa banyak item yang anda mahu lihat? Balas dengan nombor dari 1 hingga {max}.", "zh": "您想看多少个项目？请回复 1 到 {max} 之间的数字。"},
    "How many categories do you want to see? Reply with a number from 1 to {max}.": {"ms": "Berapa banyak kategori yang anda mahu lihat? Balas dengan nombor dari 1 hingga {max}.", "zh": "您想看多少个类别？请回复 1 到 {max} 之间的数字。"},
    "I can list at most the top {n} in one reply.": {"ms": "Saya boleh senaraikan paling banyak {n} teratas dalam satu balasan.", "zh": "我一次最多只能列出前 {n} 个。"},
    "Top {n} selling item": {"ms": "{n} item paling laris", "zh": "最畅销的 {n} 个项目"},
    "Top {n} selling category": {"ms": "{n} kategori paling laris", "zh": "最畅销的 {n} 个类别"},
    "Bottom {n} selling item": {"ms": "{n} item paling kurang laris", "zh": "最滞销的 {n} 个项目"},
    "Bottom {n} selling category": {"ms": "{n} kategori paling kurang laris", "zh": "最滞销的 {n} 个类别"},
    # Slice 4: the final reply pass (composer sentences, escalate offer, canned copy, dealer
    # questions) and the domain label words. Sentences are listed in INLINE below.
    REFER_TO_SALESMAN: {"ms": "Sila rujuk jurujual anda.", "zh": "请联系您的销售员。"},
    " or ": {"ms": " atau ", "zh": "或"},
    " and ": {"ms": " dan ", "zh": "和"},
    'stock': {"ms": 'stok', "zh": '库存'},
    'orders': {"ms": 'pesanan', "zh": '订单'},
    'incoming stock': {"ms": 'stok masuk', "zh": '到货库存'},
    'promotions': {"ms": 'promosi', "zh": '促销'},
    'forms': {"ms": 'borang', "zh": '表格'},
    'product information': {"ms": 'maklumat produk', "zh": '产品信息'},
    'product attachments': {"ms": 'lampiran produk', "zh": '产品附件'},
    'resource attachments': {"ms": 'lampiran sumber', "zh": '资料附件'},
    'goods receive': {"ms": 'penerimaan barang', "zh": '收货'},
    'last in': {"ms": 'kemasukan terakhir', "zh": '最近入库'},
    'outstanding purchase orders': {"ms": 'pesanan belian tertunggak', "zh": '未完成采购订单'},
    'last purchase cost': {"ms": 'kos belian terakhir', "zh": '最近采购成本'},
    'this request': {"ms": 'permintaan ini', "zh": '此请求'},
    '*{label}* for {codes}:': {"ms": '*{label}* untuk {codes}:', "zh": '{codes} 的*{label}*：'},
    'I could not fetch {label} just now, please try again.': {"ms": 'Saya tidak dapat mendapatkan {label} sekarang, sila cuba lagi.', "zh": '暂时无法获取{label}，请稍后再试。'},
    '*{label}*: this is not enabled for your account.': {"ms": '*{label}*: ini tidak diaktifkan untuk akaun anda.', "zh": '*{label}*：您的账户未开通此功能。'},
    'Nothing on {names} either.': {"ms": 'Tiada juga untuk {names}.', "zh": '{names} 也没有。'},
    'Not checked: {names}.': {"ms": 'Tidak disemak: {names}.', "zh": '未检查：{names}。'},
    'I could not find {names}.': {"ms": 'Saya tidak dapat menemui {names}.', "zh": '找不到 {names}。'},
    'I have attached the file(s) below.': {"ms": 'Saya telah melampirkan fail di bawah.', "zh": '我已在下方附上文件。'},
    'Would you like me to escalate to {team} team?': {"ms": 'Adakah anda mahu saya rujuk kepada pasukan {team}?', "zh": '需要我转交给 {team} 团队吗？'},
    'Would you like me to escalate?': {"ms": 'Adakah anda mahu saya rujuk kepada pasukan kami?', "zh": '需要我转交给相关团队吗？'},
    'Would you like me to escalate to *{company}* {team} team?': {"ms": 'Adakah anda mahu saya rujuk kepada pasukan {team} *{company}*?', "zh": '需要我转交给 *{company}* 的 {team} 团队吗？'},
    'I am sorry the provided answer does not meet your requirements. Would you like me to escalate to {team} team?': {"ms": 'Maaf, jawapan yang diberikan tidak memenuhi keperluan anda. Adakah anda mahu saya rujuk kepada pasukan {team}?', "zh": '抱歉，所提供的答案未能满足您的需求。需要我转交给 {team} 团队吗？'},
    'I am sorry the provided answer does not meet your requirements. Would you like me to escalate this to our team?': {"ms": 'Maaf, jawapan yang diberikan tidak memenuhi keperluan anda. Adakah anda mahu saya rujuk perkara ini kepada pasukan kami?', "zh": '抱歉，所提供的答案未能满足您的需求。需要我转交给我们的团队吗？'},
    "No it's okay": {"ms": 'Tidak mengapa', "zh": '不用了'},
    'Yes, escalate': {"ms": 'Ya, rujuk', "zh": '是，转交'},
    "No, it's okay": {"ms": 'Tidak, tidak mengapa', "zh": '不，不用了'},
    'Which team should take this?': {"ms": 'Pasukan mana yang patut uruskan ini?', "zh": '应由哪个团队处理？'},
    'Which company do you mean?': {"ms": 'Syarikat mana yang anda maksudkan?', "zh": '您指的是哪家公司？'},
    'Who should take this?': {"ms": 'Siapa yang patut uruskan ini?', "zh": '应由谁处理？'},
    'Outstanding for which document?': {"ms": 'Tertunggak untuk dokumen mana?', "zh": '查看哪种单据的未完成数量？'},
    'Which list would you like?': {"ms": 'Senarai mana yang anda mahu?', "zh": '您要哪个列表？'},
    'Which one do you mean?': {"ms": 'Yang mana satu anda maksudkan?', "zh": '您指的是哪一个？'},
    'Which kind of file do you need?': {"ms": 'Jenis fail apa yang anda perlukan?', "zh": '您需要哪种文件？'},
    'Which item do you mean? Reply with a rank number from 1 to {count}.': {"ms": 'Item mana yang anda maksudkan? Balas dengan nombor kedudukan dari 1 hingga {count}.', "zh": '您指的是哪个项目？请回复 1 到 {count} 之间的排名数字。'},
    'Please reply with a number from {min} to {max}.': {"ms": 'Sila balas dengan nombor dari {min} hingga {max}.', "zh": '请回复 {min} 到 {max} 之间的数字。'},
    'How many units for each?': {"ms": 'Berapa unit untuk setiap satu?', "zh": '每个需要多少件？'},
    'How many units of {code}?': {"ms": 'Berapa unit {code}?', "zh": '{code} 需要多少件？'},
    'Which one do you need {qty} of?': {"ms": 'Yang mana satu anda perlukan sebanyak {qty}?', "zh": '您需要 {qty} 件的是哪一个？'},
    'Which one?': {"ms": 'Yang mana satu?', "zh": '哪一个？'},
    '{typed} x {qty}: which one?': {"ms": '{typed} x {qty}: yang mana satu?', "zh": '{typed} x {qty}：哪一个？'},
    '{typed} matches {total} products. Which one?': {"ms": '{typed} sepadan dengan {total} produk. Yang mana satu?', "zh": '{typed} 匹配到 {total} 个产品。哪一个？'},
    "Couldn't find {shown}. Did you mean {label}?": {"ms": 'Tidak dapat menemui {shown}. Adakah anda maksudkan {label}?', "zh": '找不到 {shown}。您是指 {label} 吗？'},
    "Couldn't find {shown}. Did you mean:": {"ms": 'Tidak dapat menemui {shown}. Adakah anda maksudkan:', "zh": '找不到 {shown}。您是指：'},
    'Sorry, you are not allowed to access {team}': {"ms": 'Maaf, anda tidak dibenarkan mengakses {team}', "zh": '抱歉，您无权访问 {team}'},
    'Please specify your demand quantity': {"ms": 'Sila nyatakan kuantiti yang anda perlukan', "zh": '请说明您需要的数量'},
    'Okay, noted.': {"ms": 'Baik, dicatat.', "zh": '好的，已记录。'},
    'Escalation declined.': {"ms": 'Rujukan dibatalkan.', "zh": '已取消转交。'},
    'Sorry, I ran into a problem understanding that. Please try again in a moment.': {"ms": 'Maaf, saya menghadapi masalah memahami mesej itu. Sila cuba lagi sebentar lagi.', "zh": '抱歉，我暂时无法理解您的信息，请稍后再试。'},
    "Sorry, I didn't get that. What would you like to change in the ranking?": {"ms": 'Maaf, saya tidak faham. Apa yang anda mahu ubah dalam senarai kedudukan?', "zh": '抱歉，我没听明白。您想如何调整排名？'},
    'Low stock report is not enabled for your account.': {"ms": 'Laporan stok rendah tidak diaktifkan untuk akaun anda.', "zh": '您的账户未开通低库存报告。'},
    'Could not run the low stock report right now.': {"ms": 'Laporan stok rendah tidak dapat dijalankan sekarang.', "zh": '暂时无法生成低库存报告。'},
    'Both': {"ms": 'Kedua-duanya', "zh": '两者'},
    'Which customer is this outstanding report for?': {"ms": 'Laporan tertunggak ini untuk pelanggan mana?', "zh": '这份未完成报告是哪个客户的？'},
    'Which category do you mean? Reply with one code: {codes}': {"ms": 'Kategori mana yang anda maksudkan? Balas dengan satu kod: {codes}', "zh": '您指的是哪个类别？请回复一个代码：{codes}'},
    'I could not match any product code in that photo.': {"ms": 'Saya tidak dapat memadankan sebarang kod produk dalam gambar itu.', "zh": '我无法在那张照片中匹配到任何产品代码。'},
    'What would you like me to do with it?': {"ms": 'Apa yang anda mahu saya lakukan dengannya?', "zh": '您希望我怎么处理？'},
    'Ask again with the correct code.': {"ms": 'Sila tanya semula dengan kod yang betul.', "zh": '请用正确的代码再问一次。'},
    'I read {codes} from that photo.': {"ms": 'Saya membaca {codes} daripada gambar itu.', "zh": '我从那张照片中读到 {codes}。'},
    # The answer.py offer suffixes (whole sentences as the builders print them).
    'Reply with a number to continue.': {"ms": 'Balas dengan nombor untuk meneruskan.', "zh": '回复数字继续。'},
    'Reply with a number to continue, or would you like me to escalate to {team} team?': {"ms": 'Balas dengan nombor untuk meneruskan, atau adakah anda mahu saya rujuk kepada pasukan {team}?', "zh": '回复数字继续，或需要我转交给 {team} 团队吗？'},
    'Reply with a number to check its incoming.': {"ms": 'Balas dengan nombor untuk menyemak stok masuknya.', "zh": '回复数字查看其到货情况。'},
    "Reply with a number to check its incoming, or reply 'yes' to escalate to {team} team.": {"ms": "Balas dengan nombor untuk menyemak stok masuknya, atau balas 'yes' untuk rujuk kepada pasukan {team}.", "zh": "回复数字查看其到货情况，或回复 'yes' 转交给 {team} 团队。"},
    'Reply a number to pick.': {"ms": 'Balas nombor untuk memilih.', "zh": '回复数字选择。'},
    "Reply a number to pick, or 'yes' to escalate to {team}.": {"ms": "Balas nombor untuk memilih, atau 'yes' untuk rujuk kepada {team}.", "zh": "回复数字选择，或回复 'yes' 转交给 {team}。"},
    "Reply 'all dates' to search without the date filter.": {"ms": "Balas 'all dates' untuk mencari tanpa penapis tarikh.", "zh": "回复 'all dates' 可不限日期搜索。"},
    "Reply 'all dates' to search without the date filter, or would you like me to escalate to {team} team?": {"ms": "Balas 'all dates' untuk mencari tanpa penapis tarikh, atau adakah anda mahu saya rujuk kepada pasukan {team}?", "zh": "回复 'all dates' 可不限日期搜索，或需要我转交给 {team} 团队吗？"},
    # Slice 4 fix round: escalation lane copy, short questions, entities-only copy, top selling who-ask.
    # WA-MSG-TRIM (owner, 3 Oct 2026): the escalation lane's ONE customer message.
    'Routed to your PIC, they will reply shortly.': {"ms": 'Telah diserahkan kepada PIC anda, mereka akan membalas tidak lama lagi.', "zh": '已转交给您的负责人（PIC），他们会尽快回复。'},
    '{header} I found {count}, please type a little more of the name.': {"ms": '{header} Saya menemui {count}, sila taip lebih sedikit daripada nama itu.', "zh": '{header}我找到 {count} 个，请多输入一些名称。'},
    'and {n} others, reply with the full code.': {"ms": 'dan {n} lagi, balas dengan kod penuh.', "zh": '还有 {n} 个，请回复完整代码。'},
    "Couldn't find {names}.": {"ms": 'Tidak dapat menemui {names}.', "zh": '找不到 {names}。'},
    'I have {names}.': {"ms": 'Saya ada {names}.', "zh": '我已记下 {names}。'},
    'What would you like me to know?': {"ms": 'Apa yang anda mahu saya tahu?', "zh": '您想让我了解什么？'},
    "Sorry, we don't support direct goods receive & SPO at the moment. You may ask about incoming stock for a specific product or container": {"ms": 'Maaf, kami belum menyokong penerimaan barang terus & SPO buat masa ini. Anda boleh bertanya tentang stok masuk bagi produk atau kontena tertentu', "zh": '抱歉，我们目前暂不支持直接收货和 SPO 查询。您可以询问特定产品或货柜的到货库存'},
    'Do you mean customer {customer} or sales agent {agent}? Reply 1 for the customer, 2 for the sales agent.': {"ms": 'Adakah anda maksudkan pelanggan {customer} atau ejen jualan {agent}? Balas 1 untuk pelanggan, 2 untuk ejen jualan.', "zh": '您是指客户 {customer} 还是销售代理 {agent}？回复 1 选择客户，回复 2 选择销售代理。'},
    "Do you mean a customer named '{word}' or sales agent {agent}? Reply 1 for the customer, 2 for the sales agent.": {"ms": "Adakah anda maksudkan pelanggan bernama '{word}' atau ejen jualan {agent}? Balas 1 untuk pelanggan, 2 untuk ejen jualan.", "zh": "您是指名为 '{word}' 的客户还是销售代理 {agent}？回复 1 选择客户，回复 2 选择销售代理。"},
    "PENDING ALLOCATION": {"ms": "MENUNGGU PERUNTUKAN", "zh": "待分配"},
    "PARTIAL ALLOCATION": {"ms": "PERUNTUKAN SEBAHAGIAN", "zh": "部分分配"},
    # Crew-tester finding 1 (3 Oct): the incoming answer and the cross-domain block under a stock
    # miss. ETA, ETC and ETD are printed as they are (Q3) and have no entry.
    "Container": {"ms": "Kontena", "zh": "货柜"},
    "Shipment Container": {"ms": "Kontena Penghantaran", "zh": "货运货柜"},
    "Estimated Arrival Date": {"ms": "Tarikh Anggaran Tiba", "zh": "预计到达日期"},
    "Batch": {"ms": "Kelompok", "zh": "批次"},
    "Incoming Quantity": {"ms": "Kuantiti Masuk", "zh": "到货数量"},
    "Warehouse Allocations": {"ms": "Peruntukan Gudang", "zh": "仓库分配"},
    "Unallocated Quantity": {"ms": "Kuantiti Belum Diperuntukkan", "zh": "未分配数量"},
    "Shipment": {"ms": "Penghantaran", "zh": "货运"},
    "Total Incoming Quantity": {"ms": "Jumlah Kuantiti Masuk", "zh": "到货总数量"},
    "Distinct Products": {"ms": "Produk Berbeza", "zh": "产品种类"},
    "ETA Delay": {"ms": "Kelewatan ETA", "zh": "ETA 延误"},
    "CIDB Inspection": {"ms": "Pemeriksaan CIDB", "zh": "CIDB 检验"},
    "CIDB Approval": {"ms": "Kelulusan CIDB", "zh": "CIDB 批准"},
    "Gatepass": {"ms": "Pas Keluar", "zh": "出闸许可"},
    "Warehouse Arrival": {"ms": "Tiba di Gudang", "zh": "到达仓库"},
    "Collection Informed": {"ms": "Pengambilan Dimaklumkan", "zh": "已通知提货"},
    "Collection": {"ms": "Pengambilan", "zh": "提货"},
    "Loading": {"ms": "Pemuatan", "zh": "装货"},
    "Liner": {"ms": "Syarikat Perkapalan", "zh": "船公司"},
    "China Forwarder": {"ms": "Ejen Penghantaran China", "zh": "中国货代"},
    "Malaysia Forwarder": {"ms": "Ejen Penghantaran Malaysia", "zh": "马来西亚货代"},
    "Consignee": {"ms": "Penerima", "zh": "收货人"},
    "Delivery Warehouse": {"ms": "Gudang Penghantaran", "zh": "送货仓库"},
    "Free Days Available": {"ms": "Hari Percuma Berbaki", "zh": "剩余免费天数"},
    "Stacked": {"ms": "Disusun", "zh": "已堆放"},
    "COA Permit No.": {"ms": "No. Permit COA", "zh": "COA 许可证号"},
    "PO date": {"ms": "Tarikh PO", "zh": "PO 日期"},
    "Here is the incoming stock I found.": {"ms": "Berikut ialah stok masuk yang ditemui.", "zh": "以下是找到的到货库存。"},
    "Here are the incoming shipments I found.": {"ms": "Berikut ialah penghantaran masuk yang ditemui.", "zh": "以下是找到的到货货运。"},
    "But there is INCOMING stock (ETA) for the requested products:": {"ms": "Tetapi ada stok MASUK (ETA) untuk produk yang diminta:", "zh": "但所请求的产品有到货库存（ETA）："},
    "But here are the stock details for the requested products:": {"ms": "Tetapi berikut ialah butiran stok untuk produk yang diminta:", "zh": "但以下是所请求产品的库存详情："},
    "No stock for {codes}.": {"ms": "Tiada stok untuk {codes}.", "zh": "{codes} 没有库存。"},
    "No incoming for {codes}.": {"ms": "Tiada stok masuk untuk {codes}.", "zh": "{codes} 没有到货。"},
    "No stock and no incoming for {codes}.": {"ms": "Tiada stok dan tiada stok masuk untuk {codes}.", "zh": "{codes} 没有库存，也没有到货。"},
    "No incoming and no stock for {codes}.": {"ms": "Tiada stok masuk dan tiada stok untuk {codes}.", "zh": "{codes} 没有到货，也没有库存。"},
    "Stock is 0 at every location and no incoming for {codes}.": {"ms": "Stok 0 di setiap lokasi dan tiada stok masuk untuk {codes}.", "zh": "{codes} 在所有位置的库存均为 0，且没有到货。"},
    "No incoming and stock is 0 at every location for {codes}.": {"ms": "Tiada stok masuk dan stok 0 di setiap lokasi untuk {codes}.", "zh": "{codes} 没有到货，且所有位置的库存均为 0。"},
    # The PO rung (`answer._apply_crossdomain_rung`): four lead/trail pairs, two headers.
    "No stock and no incoming for {codes}, but PO is placed:": {"ms": "Tiada stok dan tiada stok masuk untuk {codes}, tetapi PO telah dibuat:", "zh": "{codes} 没有库存，也没有到货，但已下 PO："},
    "No incoming and no stock for {codes}, but PO is placed:": {"ms": "Tiada stok masuk dan tiada stok untuk {codes}, tetapi PO telah dibuat:", "zh": "{codes} 没有到货，也没有库存，但已下 PO："},
    "Stock is 0 at every location and no incoming for {codes}, but PO is placed:": {"ms": "Stok 0 di setiap lokasi dan tiada stok masuk untuk {codes}, tetapi PO telah dibuat:", "zh": "{codes} 在所有位置的库存均为 0，且没有到货，但已下 PO："},
    "No incoming and stock is 0 at every location for {codes}, but PO is placed:": {"ms": "Tiada stok masuk dan stok 0 di setiap lokasi untuk {codes}, tetapi PO telah dibuat:", "zh": "{codes} 没有到货，且所有位置的库存均为 0，但已下 PO："},
    "No stock and no incoming for {codes}, but stock is on order from the supplier:": {"ms": "Tiada stok dan tiada stok masuk untuk {codes}, tetapi stok sedang dipesan daripada pembekal:", "zh": "{codes} 没有库存，也没有到货，但已向供应商订货："},
    "No incoming and no stock for {codes}, but stock is on order from the supplier:": {"ms": "Tiada stok masuk dan tiada stok untuk {codes}, tetapi stok sedang dipesan daripada pembekal:", "zh": "{codes} 没有到货，也没有库存，但已向供应商订货："},
    "Stock is 0 at every location and no incoming for {codes}, but stock is on order from the supplier:": {"ms": "Stok 0 di setiap lokasi dan tiada stok masuk untuk {codes}, tetapi stok sedang dipesan daripada pembekal:", "zh": "{codes} 在所有位置的库存均为 0，且没有到货，但已向供应商订货："},
    "No incoming and stock is 0 at every location for {codes}, but stock is on order from the supplier:": {"ms": "Tiada stok masuk dan stok 0 di setiap lokasi untuk {codes}, tetapi stok sedang dipesan daripada pembekal:", "zh": "{codes} 没有到货，且所有位置的库存均为 0，但已向供应商订货："},
    "No stock, no incoming and nothing on order for {codes}.": {"ms": "Tiada stok, tiada stok masuk dan tiada pesanan untuk {codes}.", "zh": "{codes} 没有库存、没有到货，也没有在订货。"},
    "No incoming, no stock and nothing on order for {codes}.": {"ms": "Tiada stok masuk, tiada stok dan tiada pesanan untuk {codes}.", "zh": "{codes} 没有到货、没有库存，也没有在订货。"},
    "Stock is 0 at every location, no incoming and nothing on order for {codes}.": {"ms": "Stok 0 di setiap lokasi, tiada stok masuk dan tiada pesanan untuk {codes}.", "zh": "{codes} 在所有位置的库存均为 0、没有到货，也没有在订货。"},
    "No incoming, stock is 0 at every location and nothing on order for {codes}.": {"ms": "Tiada stok masuk, stok 0 di setiap lokasi dan tiada pesanan untuk {codes}.", "zh": "{codes} 没有到货、所有位置的库存均为 0，也没有在订货。"},
    "Couldn't find: {names}.": {"ms": "Tidak dapat menemui: {names}.", "zh": "找不到：{names}。"},
    "Couldn't find some items:": {"ms": "Tidak dapat menemui beberapa item:", "zh": "找不到部分项目："},
    # Slice 5 (3 Oct): required fields (#1445) and the attachment gap line (#1437).
    'Which product category? Reply with a category (e.g. water tap) or "all".': {"ms": 'Kategori produk yang mana? Balas dengan kategori (cth. water tap) atau "all".', "zh": '请问是哪个产品类别？请回复类别（例如 water tap）或 "all"。'},
    "Which supplier?": {"ms": "Pembekal yang mana?", "zh": "请问是哪个供应商？"},
    "Which period? For example this month, September, 2026, or 1 to 15 Sep.": {"ms": "Tempoh yang mana? Contohnya bulan ini, September, 2026, atau 1 hingga 15 Sep.", "zh": "请问是哪个时间段？例如本月、九月、2026 年，或 9 月 1 日至 15 日。"},
    "How many? For example top 5.": {"ms": "Berapa banyak? Contohnya 5 teratas.", "zh": "要看多少个？例如前 5 名。"},
    "I don't know '{word}' as a brand.": {"ms": "Saya tidak mengenali '{word}' sebagai jenama.", "zh": "我不认识 '{word}' 这个品牌。"},
    "I don't know '{word}' as a sales agent.": {"ms": "Saya tidak mengenali '{word}' sebagai ejen jualan.", "zh": "我不认识 '{word}' 这个业务员。"},
    "I don't know '{word}' as a location.": {"ms": "Saya tidak mengenali '{word}' sebagai lokasi.", "zh": "我不认识 '{word}' 这个地点。"},
    "Delivered, ordered or outstanding?": {"ms": "Dihantar, dipesan atau tertunggak?", "zh": "按已送货、已订购还是未交货统计？"},
    "I don't know '{word}' as a period.": {"ms": "Saya tidak mengenali '{word}' sebagai tempoh.", "zh": "我看不懂 '{word}' 这个时间段。"},
    "I don't know '{word}' as a basis.": {"ms": "Saya tidak mengenali '{word}' sebagai asas.", "zh": "我不认识 '{word}' 这个统计基准。"},
    "I don't know '{word}' as a number.": {"ms": "Saya tidak mengenali '{word}' sebagai nombor.", "zh": "我看不懂 '{word}' 这个数字。"},
    "I don't know '{word}' as a measure.": {"ms": "Saya tidak mengenali '{word}' sebagai ukuran.", "zh": "我不认识 '{word}' 这个衡量方式。"},
    "I couldn't read how many, so here is the top 10.": {"ms": "Saya tidak dapat memahami berapa banyak, jadi ini 10 teratas.", "zh": "我没能看懂要多少个，所以这里显示前 10 名。"},
    "I don't know '{word}' as a category.": {"ms": "Saya tidak mengenali '{word}' sebagai kategori.", "zh": "我不认识 '{word}' 这个类别。"},
    "I don't know '{word}' as a supplier.": {"ms": "Saya tidak mengenali '{word}' sebagai pembekal.", "zh": "我不认识 '{word}' 这个供应商。"},
    'Which category do you mean? Reply with a number or "all":': {"ms": 'Kategori mana yang anda maksudkan? Balas dengan nombor atau "all":', "zh": '您指的是哪个类别？请回复编号或 "all"：'},
    "Which category do you mean? Reply with a number:": {"ms": "Kategori mana yang anda maksudkan? Balas dengan nombor:", "zh": "您指的是哪个类别？请回复编号："},
    'Which supplier do you mean? Reply with a number or "all":': {"ms": 'Pembekal mana yang anda maksudkan? Balas dengan nombor atau "all":', "zh": '您指的是哪个供应商？请回复编号或 "all"：'},
    "Which supplier do you mean? Reply with a number:": {"ms": "Pembekal mana yang anda maksudkan? Balas dengan nombor:", "zh": "您指的是哪个供应商？请回复编号："},
    "Low stock report cancelled.": {"ms": "Laporan stok rendah dibatalkan.", "zh": "低库存报告已取消。"},
    "I still can't place '{word}'. Ask for the low stock report again with a category or \"all\".": {"ms": "Saya masih tidak dapat mengenali '{word}'. Minta laporan stok rendah sekali lagi dengan kategori atau \"all\".", "zh": "我仍然无法识别 '{word}'。请再次索取低库存报告，并提供类别或 \"all\"。"},
    "{code} has no {types}.": {"ms": "{code} tiada {types}.", "zh": "{code} 没有{types}。"},
    # LOWSTOCK-SEMANTIC (4 Oct): the grouping pick (crew Q2) and the blank give-up.
    "I can group the low stock report by supplier, by category, or both. Which one? Reply with a number:": {"ms": "Saya boleh mengasingkan laporan stok rendah mengikut pembekal, mengikut kategori, atau kedua-duanya. Yang mana satu? Balas dengan nombor:", "zh": "低库存报告可以按供应商、按类别或两者分组。请问要哪一种？请回复编号："},
    "Supplier x category": {"ms": "Pembekal x kategori", "zh": "供应商 x 类别"},
    "No grouping": {"ms": "Tanpa pengasingan", "zh": "不分组"},
    "How should the low stock report be grouped?": {"ms": "Bagaimana laporan stok rendah patut diasingkan?", "zh": "低库存报告要如何分组？"},
    "I still can't tell which one you mean. Ask for the low stock report again with a category or \"all\".": {"ms": "Saya masih tidak pasti yang mana satu anda maksudkan. Minta laporan stok rendah sekali lagi dengan kategori atau \"all\".", "zh": "我仍然无法确定您指的是哪一个。请再次索取低库存报告，并提供类别或 \"all\"。"},
    # CHAT-PRODUCT-LABELS (4 Oct): product info replies (presenter `_products`,
    # `_promotion_products`, `_product_attachments` and their intros) and the text
    # `output_structurer` adds to them. Spec registry labels and product data stay as printed.
    "Description": {"ms": "Keterangan", "zh": "描述"},
    "List Price": {"ms": "Harga Senarai", "zh": "标价"},
    "Dimensions": {"ms": "Dimensi", "zh": "尺寸"},
    "Not defined": {"ms": "Tidak ditetapkan", "zh": "未设定"},
    "Promotion": {"ms": "Promosi", "zh": "促销"},
    "Selling Price": {"ms": "Harga Jualan", "zh": "售价"},
    "Attachment Type": {"ms": "Jenis Lampiran", "zh": "附件类型"},
    "File Name": {"ms": "Nama Fail", "zh": "文件名"},
    "Certificate Number": {"ms": "No. Sijil", "zh": "证书编号"},
    "Valid Until": {"ms": "Sah Sehingga", "zh": "有效期至"},
    "Validity": {"ms": "Kesahan", "zh": "有效状态"},
    "File Link": {"ms": "Pautan Fail", "zh": "文件链接"},
    "Specs": {"ms": "Spesifikasi", "zh": "规格"},
    "not recorded yet": {"ms": "belum direkodkan", "zh": "尚未记录"},
    _CANT_SHARE: {
        "ms": "Saya tidak boleh berkongsi {label}. Sila semak dengan pejabat.",
        "zh": "我无法提供 {label}，请向办公室查询。",
    },
    "Here are the matching products.": {"ms": "Berikut ialah produk yang sepadan.", "zh": "以下是匹配的产品。"},
    "Here are the matching promotion products.": {"ms": "Berikut ialah produk promosi yang sepadan.", "zh": "以下是匹配的促销产品。"},
    "Here are the product files I found.": {"ms": "Berikut ialah fail produk yang saya temui.", "zh": "以下是我找到的产品文件。"},
    # The presenter's `_VALIDITY_LABELS` words and `FILE_LINK_UNAVAILABLE`: values of one label
    # each (`LABEL_VALUES`), so an order status that reads "Expired" stays as printed.
    "Valid": {"ms": "Sah", "zh": "有效"},
    "Expiring soon": {"ms": "Akan tamat tempoh", "zh": "即将到期"},
    "Expired": {"ms": "Tamat tempoh", "zh": "已过期"},
    "Not yet valid": {"ms": "Belum sah", "zh": "尚未生效"},
    "Unknown": {"ms": "Tidak diketahui", "zh": "未知"},
    "(file link unavailable right now)": {
        "ms": "(pautan fail tidak tersedia buat masa ini)",
        "zh": "（文件链接暂时无法获取）",
    },
}

# CHAT-FIND: the generic find tool's presenter lines live with their filter in the registry
# (`app/services/product_find.py`), so a filter registered there is translated with no edit
# here. A label already catalogued above keeps its own wording.
from app.services.product_find import catalog_labels as _find_labels  # noqa: E402
from app.services.product_find import fit_keys as _find_fit_keys  # noqa: E402

for _english, _entry in _find_labels().items():
    LABELS.setdefault(_english, _entry)

#: Presenter field key -> English label (slices 1 and 2).
FIELD_KEYS: dict[str, str] = {
    "company_name": "Company",
    "product_code": "Product Code",
    "product_name": "Product Name",
    "warehouse": "Warehouse",
    "system_location": "System Location",
    "quantity_on_hand": "Quantity On Hand",
    "open_so_qty": "Outstanding",
    "total_on_hand": "Total",
    # Slice 2 summary block (`order_date` is a DO Date there; by-label lookup covers it).
    "customers": "Customer count",
    "so_count": "SO",
    "so_date": "SO Date",
    "so_ordered_qty": "Ordered",
    "so_transferred_qty": "Transferred to DO",
    "so_outstanding_qty": "SO Outstanding",
    "order_count": "DO",
    "delivered_quantity": "Delivered",
    "delivered_between": "Delivery Date",
    "pending_quantity": "DO Outstanding",
    # Slice 2.
    "so_number": "SO Number",
    "outstanding_qty": "Outstanding Qty",
    "order_date": "Order Date",
    "customer": "Customer",
    "requested_delivery_date": "Requested Delivery Date",
    "po_number": "PO Number",
    "ordered_qty": "Ordered Qty",
    "po_date": "PO Date",
    "location": "Location",
    "supplier": "Supplier",
    "spo_number": "SPO Number",
    "container_number": "Container Number",
    "spo_quantity": "SPO Quantity",
    "gr_quantity": "GR Quantity",
    "spo_date": "SPO Date",
    "gr_date": "GR Date",
    "po_quantity": "PO Quantity",
    "unit_cost": "Cost / unit",
    "discount_per_unit": "Discount / unit",
    "unit_cost_after_discount": "Cost after discount / unit",
    # Incoming (crew-tester finding 1).
    "shipping_container_number": "Container",
    "batch_number": "Batch",
    "remaining_incoming_quantity": "Incoming Quantity",
    "warehouse_allocations": "Warehouse Allocations",
    "unallocated_quantity": "Unallocated Quantity",
}

FOOTER = "Data last updated: {ts}"



def footer_leads(localizer: Any = None) -> tuple[str, ...]:
    """The footer's lead-in ("Data last updated:") in every language, for the text matchers
    that must recognise it wherever it prints. `localizer` adds its own (staff-edited) wording."""
    texts = [FOOTER, *(LABELS[FOOTER][lang] for lang in LANGUAGES if lang != "en")]
    if localizer is not None and FOOTER in localizer.table:
        texts.append(localizer.table[FOOTER])
    leads = (t.split("{ts}")[0].rstrip() for t in texts)
    return tuple(dict.fromkeys(lead for lead in leads if lead))


def refer_sentences(localizer: Any = None) -> tuple[str, ...]:
    """The refer line in every language: the English constant, and the last sentence of each
    translated verdict that ends with it. For the text matchers that ask "was it printed?".
    `localizer` adds its own (staff-edited) wording of the same verdicts."""
    found = [REFER_TO_SALESMAN]
    for english, entry in LABELS.items():
        if english.endswith(REFER_TO_SALESMAN):
            texts = [entry[lang] for lang in LANGUAGES if lang in entry]
            if localizer is not None and english in localizer.table:
                texts.append(localizer.table[english])
            for text in texts:
                pieces = [p for p in re.split(r"(?<=[.。])\s*", text.strip()) if p]
                found.append(pieces[-1])
    return tuple(dict.fromkeys(found))


#: CHAT-COMPOSER: the catalogue entries only the lane's gate-on sentences use. Kept apart from `LABELS`
#: so a gate-off table never holds them; `defaults()` and `resolve()` add them when the gate is on.
#: `{noun}` is a lower-case spec word, so it never takes the official-type wording of `{what}`.
LANE_LABELS: dict[str, dict[str, str]] = {
    "Name one to see them.": {"ms": "Sebut satu untuk melihatnya.", "zh": "请指定一个品牌查看。"},
    'Reply "more" to see the next {n}.': {"ms": 'Balas "more" untuk lihat {n} seterusnya.', "zh": '回复 "more" 查看接下来的 {n} 个。'},
    "That is all {total}.": {"ms": "Itu sahaja, {total}.", "zh": "就这些，共 {total} 个。"},
    "But no stock matched these.": {"ms": 'Tiada stok yang sepadan.', "zh": '没有匹配的库存。'},
    "But no product matched these.": {"ms": 'Tiada produk yang sepadan.', "zh": '没有匹配的产品。'},
    "But no SPO line matched these.": {"ms": 'Tiada baris SPO yang sepadan.', "zh": '没有匹配的 SPO 行。'},
    "No {what} on record for {product}.": {"ms": 'Tiada {what} dalam rekod untuk {product}.', "zh": '{product} 没有{what}记录。'},
    "No {noun} on record for {product}.": {"ms": 'Tiada {noun} direkodkan untuk {product}.', "zh": '没有 {product} 的{noun}记录。'},
    "No list price on record for {product}.": {"ms": 'Tiada harga senarai direkodkan untuk {product}.', "zh": '没有 {product} 的标价记录。'},
    "No dimensions on record for {product}.": {"ms": 'Tiada dimensi direkodkan untuk {product}.', "zh": '没有 {product} 的尺寸记录。'},
    # Lists L5: the tail `_named` counts the names it left off; filled into the `{names}` value only.
    " and {n} others": {"ms": " dan {n} lagi", "zh": " 等另外 {n} 个"},
    # Lists L1: the range a cut code-lookup list adds to its intro; it states no total (the probe
    # behind such a list may have stopped short of its end).
    "(showing {first} to {last})": {
        "ms": "(menunjukkan {first} hingga {last})",
        "zh": "（显示第 {first} 至 {last} 个）",
    },
    "Those are the first {n} matches. Send more of the code to narrow it.": {
        "ms": "Itulah {n} padanan pertama. Hantar lebih banyak kod untuk mengecilkan carian.",
        "zh": "以上是前 {n} 个匹配项。请发送更完整的编码以缩小范围。",
    },
}


#: CHAT-COMPOSER: the catalogue keys the lane added. They belong to gate-on sentences only, so with
#: the gate off the table neither seeds nor uses them (ms/zh replies stay as main prints them).
LANE_KEYS = frozenset(LANE_LABELS)


def _lane_on() -> bool:
    from app.services.chatbot import composers

    return composers.enabled()


def defaults(lang: str) -> dict[str, str]:
    """English -> `lang` for every catalog entry whose translation keeps the English tokens."""
    if lang == "en":
        return {}
    entries = {**LABELS, **LANE_LABELS} if _lane_on() else LABELS
    return {
        english: entry[lang]
        for english, entry in entries.items()
        if lang in entry and tokens_match(english, entry[lang])
    }


#: The sentences `Localizer.reply` replaces INLINE: wherever one starts a line or follows ". ",
#: "? " or "! " and runs through its own final punctuation. Composer sentences, the escalate offer,
#: dealer questions, canned copy (the slice 4 table). Domain label words and the quick-reply labels
#: are not here: they are replaced as whole lines or as tokens, never inside a sentence.
INLINE = frozenset(
    {
        "*{label}* for {codes}:",
        "I could not fetch {label} just now, please try again.",
        "*{label}*: this is not enabled for your account.",
        "Nothing on {names} either.",
        "Not checked: {names}.",
        "I could not find {names}.",
        "I have attached the file(s) below.",
        "Would you like me to escalate to {team} team?",
        "Would you like me to escalate?",
        "Would you like me to escalate to *{company}* {team} team?",
        "I am sorry the provided answer does not meet your requirements. Would you like me to escalate to {team} team?",
        "I am sorry the provided answer does not meet your requirements. Would you like me to escalate this to our team?",
        "Which team should take this?",
        "Which company do you mean?",
        "Who should take this?",
        "Outstanding for which document?",
        "Which list would you like?",
        "Which one do you mean?",
        "Which kind of file do you need?",
        "Which item do you mean? Reply with a rank number from 1 to {count}.",
        "Please reply with a number from {min} to {max}.",
        "How many units for each?",
        "How many units of {code}?",
        "Which one do you need {qty} of?",
        "Which one?",
        "{typed} x {qty}: which one?",
        "{typed} matches {total} products. Which one?",
        "Couldn't find {shown}. Did you mean {label}?",
        "Couldn't find {shown}. Did you mean:",
        REFER_TO_SALESMAN,
        "Sorry, you are not allowed to access {team}",
        "Please specify your demand quantity",
        "Okay, noted.",
        "Escalation declined.",
        "Sorry, I ran into a problem understanding that. Please try again in a moment.",
        "Sorry, I didn't get that. What would you like to change in the ranking?",
        "Low stock report is not enabled for your account.",
        "Could not run the low stock report right now.",
        "Which customer is this outstanding report for?",
        "Which category do you mean? Reply with one code: {codes}",
        "I could not match any product code in that photo.",
        "What would you like me to do with it?",
        "Ask again with the correct code.",
        "I read {codes} from that photo.",
        'Routed to your PIC, they will reply shortly.',
        '{header} I found {count}, please type a little more of the name.',
        'and {n} others, reply with the full code.',
        "Couldn't find {names}.",
        # CHAT-COMPOSER S3a: the miss sentence with a customer word, not a table name.
        "But no stock matched these.",
        "But no product matched these.",
        "But no SPO line matched these.",
        "No {what} on record for {product}.",
        "That is all {total}.",
        "Name one to see them.",
        'What would you like me to know?',
        "Sorry, we don't support direct goods receive & SPO at the moment. You may ask about incoming stock for a specific product or container",
        'Do you mean customer {customer} or sales agent {agent}? Reply 1 for the customer, 2 for the sales agent.',
        "Do you mean a customer named '{word}' or sales agent {agent}? Reply 1 for the customer, 2 for the sales agent.",
        'Reply with a number to continue.',
        'Reply with a number to continue, or would you like me to escalate to {team} team?',
        'Reply with a number to check its incoming.',
        "Reply with a number to check its incoming, or reply 'yes' to escalate to {team} team.",
        'Reply a number to pick.',
        "Reply a number to pick, or 'yes' to escalate to {team}.",
        "Reply 'all dates' to search without the date filter.",
        "Reply 'all dates' to search without the date filter, or would you like me to escalate to {team} team?",
        # The cross-domain absence sentences: two can share a line.
        "No stock for {codes}.",
        "No incoming for {codes}.",
        "No stock and no incoming for {codes}.",
        "No incoming and no stock for {codes}.",
        "Stock is 0 at every location and no incoming for {codes}.",
        "No incoming and stock is 0 at every location for {codes}.",
        "No stock, no incoming and nothing on order for {codes}.",
        "No incoming, no stock and nothing on order for {codes}.",
        "Stock is 0 at every location, no incoming and nothing on order for {codes}.",
        "No incoming, stock is 0 at every location and nothing on order for {codes}.",
        "Couldn't find: {names}.",
        # Slice 5 (3 Oct): required fields (#1445) and the attachment gap line (#1437).
        'Which product category? Reply with a category (e.g. water tap) or "all".',
        "Which supplier?",
        "I don't know '{word}' as a category.",
        "I don't know '{word}' as a supplier.",
        'Which category do you mean? Reply with a number or "all":',
        "Which category do you mean? Reply with a number:",
        'Which supplier do you mean? Reply with a number or "all":',
        "Which supplier do you mean? Reply with a number:",
        "Low stock report cancelled.",
        "I still can't place '{word}'. Ask for the low stock report again with a category or \"all\".",
    }
)

#: Sentences too generic to match inside running text ("I have checked with the warehouse."): the
#: composer that owns one fills it with `Localizer.fill`.
# MULTI-ASK-PLAN S4 (owner card 9 Oct 2026): the per-ask runner's labels, values and two
# questions (`turn/sub_asks.py`). "Supplier" is already catalogued above.
LABELS.update(
    {
        "Received {window}": {"ms": "Diterima {window}", "zh": "{window} 入库数量"},
        "Received": {"ms": "Diterima", "zh": "入库数量"},
        "Last in": {"ms": "Terakhir masuk", "zh": "最后一次进库"},
        "Last in: none on record": {"ms": "Terakhir masuk: tiada rekod", "zh": "最后一次进库: 没有记录"},
        "Checked in: {companies}": {"ms": "Disemak di: {companies}", "zh": "已查询: {companies}"},
        "Last cost": {"ms": "Kos terakhir", "zh": "采购价"},
        "Stock": {"ms": "Stok", "zh": "库存"},
        "Incoming": {"ms": "Barang masuk", "zh": "到货"},
        "Incoming {window}": {"ms": "Barang masuk {window}", "zh": "{window} 到货"},
        "Sold {window} ({basis})": {"ms": "Terjual {window} ({basis})", "zh": "{window} 总共卖出（{basis}）"},
        "Sold {axis} {window} ({basis})": {"ms": "Terjual {axis} {window} ({basis})", "zh": "{window} {axis}卖出（{basis}）"},
        "per salesman": {"ms": "mengikut jurujual", "zh": "每位业务员"},
        "per customer": {"ms": "mengikut pelanggan", "zh": "每位客户"},
        "per product": {"ms": "mengikut produk", "zh": "每个产品"},
        "per brand": {"ms": "mengikut jenama", "zh": "每个品牌"},
        "per category": {"ms": "mengikut kategori", "zh": "每个类别"},
        "per location": {"ms": "mengikut lokasi", "zh": "每个仓库"},
        "per channel": {"ms": "mengikut saluran", "zh": "每个渠道"},
        "per month": {"ms": "mengikut bulan", "zh": "每月"},
        "delivered": {"ms": "dihantar", "zh": "已出货"},
        "ordered": {"ms": "ditempah", "zh": "下单"},
        "not shared with you": {"ms": "tidak dikongsi dengan anda", "zh": "您没有查看权限"},
        "not available yet": {"ms": "belum tersedia", "zh": "暂时无法提供"},
        "none on record": {"ms": "tiada rekod", "zh": "没有记录"},
        "couldn't check right now": {"ms": "tidak dapat disemak sekarang", "zh": "暂时无法查询"},
        "too many sales reports just now, try again in a few minutes": {
            "ms": "terlalu banyak laporan jualan sebentar tadi, cuba lagi dalam beberapa minit",
            "zh": "销售报表查询太频繁，请几分钟后再试",
        },
        "{code} has {n} variants. Just {code}, or all {n}?": {
            "ms": "{code} ada {n} jenis. Hanya {code}, atau semua {n}?",
            "zh": "{code} 有 {n} 个款式。只要 {code}，还是全部 {n} 款？",
        },
        "Just {code}": {"ms": "Hanya {code}", "zh": "只要 {code}"},
        "All {n}": {"ms": "Semua {n}", "zh": "全部 {n} 款"},
        "{code} (all {n} variants)": {"ms": "{code} (semua {n} jenis)", "zh": "{code}（全部 {n} 款）"},
        "Sold as delivered or as ordered?": {
            "ms": "Terjual mengikut yang dihantar atau yang ditempah?",
            "zh": "卖出按已出货还是按下单算？",
        },
        # Round 6 (owner hand test, 9 Oct 2026): the prefix-only scope question, the sold
        # period question and the "what about it?" reply to a vague message.
        "{code} has {n} variants. Which one?": {"ms": "{code} ada {n} jenis. Yang mana?", "zh": "{code} 有 {n} 个款式，要哪个？"},
        "Or type the full code.": {"ms": "Atau taip kod penuh.", "zh": "或者输入完整的代码。"},
        "Sold over which period?": {"ms": "Jualan untuk tempoh bila?", "zh": "卖出要算哪个时间段？"},
        "What would you like to know about {name}? Stock, incoming, cost or sales?": {
            "ms": "Apa yang anda ingin tahu tentang {name}? Stok, barang masuk, kos atau jualan?",
            "zh": "你想了解 {name} 的什么？库存、到货、采购价，还是销售？",
        },
        "As delivered": {"ms": "Yang dihantar", "zh": "按已出货"},
        "As ordered": {"ms": "Yang ditempah", "zh": "按下单"},
        "{supplier} ({po}, {date})": {"ms": "{supplier} ({po}, {date})", "zh": "{supplier}（{po}，{date}）"},
        "{currency} {cost} / unit ({po}, {date})": {
            "ms": "{currency} {cost} / unit ({po}, {date})",
            "zh": "{currency} {cost} / 件（{po}，{date}）",
        },
        "{date} ({qty}, {spo})": {"ms": "{date} ({qty}, {spo})", "zh": "{date}（{qty}，{spo}）"},
        "{qty}, ETA {date}": {"ms": "{qty}, ETA {date}", "zh": "{qty}，ETA {date}"},
    }
)
#: The per-ask runner's keys are whole labels and values of ITS reply, filled by key
#: (`Localizer.fill`), never matched inside another reply's running text: "delivered" inside
#: an order answer must stay exactly as that answer wrote it. "Stock" is not here: CHAT-FIND
#: (#1601) translates a "Stock:" line in any reply, with the same words.
_SUB_ASK_KEYS = frozenset(
    {
        "Received {window}", "Received", "Last in", "Last cost", "Incoming", "Incoming {window}",
        "Sold {window} ({basis})", "Sold {axis} {window} ({basis})", "per salesman",
        "per customer", "per product", "per brand", "per category", "per location",
        "per channel", "per month", "delivered", "ordered", "not shared with you",
        "not available yet", "none on record", "couldn't check right now",
        "too many sales reports just now, try again in a few minutes",
        "{code} has {n} variants. Just {code}, or all {n}?", "Just {code}", "All {n}",
        "{code} (all {n} variants)", "Sold as delivered or as ordered?", "As delivered",
        "{code} has {n} variants. Which one?", "Or type the full code.", "Sold over which period?",
        "What would you like to know about {name}? Stock, incoming, cost or sales?",
        "As ordered", "{supplier} ({po}, {date})", "{currency} {cost} / unit ({po}, {date})",
        "{date} ({qty}, {spo})", "{qty}, ETA {date}",
    }
)
DIRECT_ONLY = (
    frozenset(
        {
            "I have {names}.",
            "{code} has no {types}.",
            " and {n} others",
            "(showing {first} to {last})",
        }
    )
    | _SUB_ASK_KEYS
    | _find_fit_keys()
)
_NAMES_TAIL = " and {n} others"
_OTHERS = re.compile(r"(.*) and (\d[\d,]*) others")

#: Report value words `Localizer.lines` translates after a catalogued label. An explicit set, never
#: "any catalog key", so data that happens to read like a label (a status named "Status") is safe.
VALUE_WORDS = frozenset(
    {"all", "Amount", "Quantity", "Ordered", "Delivered (transferred to DO)", "Not defined", "not recorded yet"}
)
#: Value words that translate only after ONE English label (the product attachment presenter's).
LABEL_VALUES: dict[str, frozenset[str]] = {
    "Validity": frozenset({"Valid", "Expiring soon", "Expired", "Not yet valid", "Unknown"}),
    "File Link": frozenset({"(file link unavailable right now)"}),
}
#: Those words are never a sentence, a flag or a label of their own: a status card that prints
#: "Unknown" or "Expired" alone on a line keeps it as printed (#1448 owner Q3).
_VALUE_ONLY = frozenset().union(*LABEL_VALUES.values())
RANGE = "{from} to {to}"
#: A `{token}` matches at most this many characters of one line, and a line longer than
#: `_INLINE_LINE_MAX` gets `lines()` only (the inline pass is skipped for it).
_TOKEN_MAX = 200
_INLINE_LINE_MAX = 1000
_DATE_RANGE = re.compile(r"(?P<from>\d{2}/\d{2}/\d{4}) to (?P<to>\d{2}/\d{2}/\d{4})")
_BREAKDOWN_VALUE = re.compile(r"\d[\d,]* \(O/S: ")
#: A numbered row's `"<n>. "` or a cross-domain row's `"- "`; only the rest of the line is read.
_NUMBERED = re.compile(r"\d+\. |- ")
#: A flag line (`"⚠️  *(PRODUCT DISCONTINUED)*"`): the flag inside translates, the mark stays.
_FLAG = re.compile(r"(\S+ +\*\()(.+)(\)\*)")
_WRAPPERS = (("*_", "_*"), ("*", "*"), ("_", "_"))
#: Tokens that are always a number: a template opening with one must not swallow the
#: "<code> x <qty>: " prefix of the line it is matched against.
_NUMERIC_TOKENS = frozenset({"available"})


def _token_pattern(name: str) -> str:
    if name == "noun":
        body = f"[a-z][^\\n]{{0,{_TOKEN_MAX - 1}}}?"  # a spec word starts lower-case
    else:
        body = r"\d[\d,]*" if name in _NUMERIC_TOKENS else f"[^\\n]{{1,{_TOKEN_MAX}}}?"
    return f"(?P<{name}>{body})"


#: The availability verdict's status mark (#1430: tick, cross, no-entry sign) and its space.
_STATUS_MARK = re.compile("[\u2705\u274c\U0001F6AB]\ufe0f? +")


def _inline_pattern(english: str, target: str) -> tuple[re.Pattern[str], str, int, tuple[str, ...]]:
    """A start-anchored, whole-sentence regex for an INLINE key: tokens are lazy and stay on one
    line, and the match must run to its final punctuation followed by a space or the line end."""
    pattern = ""
    pos = 0
    for m in _TOKEN.finditer(english):
        pattern += re.escape(english[pos : m.start()]) + _token_pattern(m.group(1))
        pos = m.end()
    pattern += re.escape(english[pos:])
    fixed = len(_TOKEN.sub("", english))
    regex = re.compile(r"(?:^|(?<=[.?!] ))" + pattern + r"(?= |$)", re.MULTILINE)
    return regex, target, fixed, tuple(_TOKEN.findall(english))


#: CHAT-COMPOSER S4: the generic "no <what> on record" sentence, and the product-find none lines it
#: must never rewrite (a kind with no noun translation is said in English, not garbled into
#: `{what}` = "mirror is").
_GENERIC_MISSING = "No {what} on record for {product}."
_GENERIC_NOUN_MISSING = "No {noun} on record for {product}."


def _find_none_patterns() -> list[re.Pattern[str]]:
    from app.services import product_find

    lines = [f(k) for k in product_find.KINDS for f in (product_find.none_line, product_find.no_host_line)]
    return [re.compile(re.escape(x).replace(re.escape("{code}"), ".+?")) for x in lines]


class Localizer:
    """Rewrites catalogued labels and sentences for `language`; everything else is untouched."""

    def __init__(self, language: str, table: dict[str, str]) -> None:
        self.language = language
        if not _lane_on():
            table = {k: v for k, v in table.items() if k not in LANE_KEYS}
        self.table = table
        # `{token}` entries as (whole-string regex over the English shape, target).
        self._templates: list[tuple[re.Pattern[str], str, int, bool]] = []  # most fixed text wins
        # The INLINE sentences the table carries: (regex for a start-anchored match, target,
        # fixed characters, token names). Longest fixed text wins where two start together.
        self._inline: list[tuple[re.Pattern[str], str, int, tuple[str, ...]]] = []
        self._find_none = _find_none_patterns() if _GENERIC_MISSING in table else []
        self._generic_targets = {table[k] for k in (_GENERIC_MISSING, _GENERIC_NOUN_MISSING) if k in table}
        for english, target in table.items():
            if english in INLINE and english not in DIRECT_ONLY:
                self._inline.append(_inline_pattern(english, target))
        for english, target in table.items():
            if not tokens(english) or english == RANGE or english in DIRECT_ONLY:
                continue  # the date range is a VALUE template, applied by `lines` only
            pattern = ""
            pos = 0
            for m in _TOKEN.finditer(english):
                pattern += re.escape(english[pos : m.start()]) + _token_pattern(m.group(1))
                pos = m.end()
            pattern += re.escape(english[pos:])
            self._templates.append(
                (re.compile(pattern), target, len(_TOKEN.sub("", english)), english in INLINE)
            )

    def label(self, field: dict) -> str:
        """The field's label: by its key when it has a catalogued one and still carries that
        English label, else by the exact label text. A value is never read."""
        label = field.get("label")
        if not isinstance(label, str):
            return label
        key = field.get("key")
        english = FIELD_KEYS.get(key) if isinstance(key, str) else None
        if english is not None and label == english:
            return self.table.get(english, label)
        return self.table.get(label, label)

    def text(self, s: str) -> str:
        return self._text(s, inline=True)

    def fill(self, key: str, **values: str) -> str:
        """The catalog sentence for `key` with its tokens filled in (English if not catalogued).
        For the few sentences too generic to be matched in running text (`DIRECT_ONLY`)."""
        return _TOKEN.sub(lambda t: values.get(t.group(1), t.group(0)), self.table.get(key, key))

    def _is_find_none(self, span: str) -> bool:
        return any(r.fullmatch(span) for r in self._find_none)

    def _text(self, s: str, *, inline: bool) -> str:
        if s in _VALUE_ONLY:
            return s
        if s in self.table and s not in DIRECT_ONLY:
            return self.table[s]
        best = None
        for regex, target, fixed, is_inline in self._templates:
            if is_inline and not inline:
                continue  # INLINE sentences are replaced by `reply()` at their own boundaries
            m = regex.fullmatch(s)
            if m and target in self._generic_targets and self._is_find_none(s):
                continue
            if m and (best is None or fixed > best[0]):
                best = (fixed, m.groupdict(), target)
        if best is None:
            return s
        values = self._names(best[1])
        return _TOKEN.sub(lambda t: values.get(t.group(1), t.group(0)), best[2])

    def lines(self, text: str) -> str:
        """Finished report text, line by line: the first rule that matches wins.

        1. a catalogued sentence, bare or inside one wrapper (`*x*`, `*_x_*`, `_x_`);
        2. a flag line (`"⚠️  *(<flag>)*"`) whose flag is catalogued, then `"<n>. <sentence>"`
           (or `"- <sentence>"`);
        3. a label line (`"<label>: <value>"`, bold `"*<label>:* <value>"`, optionally led by
           `"<n>. "` or `"- "`) whose label is catalogued: the label translates, and the value only when
           it is exactly a `VALUE_WORDS` entry, one of that label's `LABEL_VALUES`, or a
           `dd/mm/yyyy to dd/mm/yyyy` range;
        4. anything else, unchanged (rank lines, months, codes, `Unassigned`).
        The join is byte-exact."""
        if not self.table or not text:
            return text
        return "\n".join(self._line(line) for line in text.split("\n"))

    def reply(self, text: str) -> str:
        """The final reply pass: `lines()`, then each INLINE sentence replaced where it starts a
        line or follows ". ", "? " or "! " and runs through its final punctuation. Tokens come back
        verbatim and never span a line; a line over `_INLINE_LINE_MAX` is left to `lines()`."""
        if not self.table or not text:
            return text
        text = self.lines(text)
        if not self._inline:
            return text
        return "\n".join(
            line if len(line) > _INLINE_LINE_MAX else self._inline_line(line) for line in text.split("\n")
        )

    def _names(self, values: dict[str, str]) -> dict[str, str]:
        """`values` with a `{names}` tail "... and 3 others" said in the table's wording, when it has one."""
        tail, m = self.table.get(_NAMES_TAIL), _OTHERS.fullmatch(values.get("names") or "")
        if tail is None or m is None:
            return values
        return {**values, "names": m.group(1) + tail.replace("{n}", m.group(2))}

    def _inline_line(self, text: str) -> str:
        found: list[tuple[int, int, int, str]] = []
        for regex, target, fixed, names in self._inline:
            for m in regex.finditer(text):
                values = self._names(m.groupdict())
                if target in self._generic_targets and self._is_find_none(m.group(0)):
                    continue
                found.append((m.start(), m.end(), fixed, _TOKEN.sub(lambda t: values.get(t.group(1), t.group(0)), target)))
        found.sort(key=lambda f: (f[0], -f[2]))
        out, pos = [], 0
        for start, end, _fixed, replacement in found:
            if start < pos:
                continue
            out.append(text[pos:start])
            out.append(replacement)
            pos = end
            if self.language == "zh" and replacement[-1:] in "。？！" and text[end : end + 1] == " ":
                pos = end + 1  # full-width stops carry no space before the next sentence
        out.append(text[pos:])
        return "".join(out)

    def _line(self, line: str) -> str:
        for left, right in _WRAPPERS:
            if len(line) > len(left) + len(right) and line.startswith(left) and line.endswith(right):
                inner = line[len(left) : len(line) - len(right)]
                done = self._text(inner, inline=False)
                if done != inner:
                    return left + done + right
        done = self._text(line, inline=False)
        if done != line:
            return done
        flag = _FLAG.fullmatch(line)
        if flag and flag.group(2) in self.table and flag.group(2) not in DIRECT_ONLY and flag.group(2) not in _VALUE_ONLY and not tokens(flag.group(2)):
            return flag.group(1) + self.table[flag.group(2)] + flag.group(3)
        number = _NUMBERED.match(line)
        prefix, rest = (number.group(0), line[number.end() :]) if number else ("", line)
        if prefix:
            done = self._text(rest, inline=False)
            if done != rest:
                return prefix + done
        for left, sep in (("*", ":* "), ("", ": ")):
            if not rest.startswith(left):
                continue
            head, found, value = rest[len(left) :].partition(sep)
            if not found or head not in self.table or head in DIRECT_ONLY or head in _VALUE_ONLY or _BREAKDOWN_VALUE.match(value):
                continue  # a breakdown row (`5 (O/S: 2)`) whose name collides with a label is data
            return prefix + left + self.table[head] + sep + self._value(value, head)
        return line

    def _value(self, value: str, label: str) -> str:
        if (value in VALUE_WORDS or value in LABEL_VALUES.get(label, ())) and value in self.table:
            return self.table[value]
        m = _DATE_RANGE.fullmatch(value)
        if m and RANGE in self.table:
            values = m.groupdict()
            return _TOKEN.sub(lambda t: values.get(t.group(1), t.group(0)), self.table[RANGE])
        return value

    def sentence(self, s: str) -> str:
        """`text`, under a name `turn/` source may use (the apply-purity grep bans `.text`)."""
        return self.text(s)

    def tail(self, title: str) -> str:
        """`"<code> x <qty>: <sentence>"`: only the sentence is translated, after any leading
        status mark (the #1430 verdicts open with one), which stays as printed."""
        prefix, sep, sentence = title.partition(": ")
        if sep:
            mark = _STATUS_MARK.match(sentence)
            lead, rest = (mark.group(0), sentence[mark.end() :]) if mark else ("", sentence)
            done = self.text(rest)
            if done != rest:
                return prefix + sep + lead + done
        return self.text(title)


IDENTITY = Localizer("en", {})


def resolve(db: Any, lang: str, *, dry_run: bool = False) -> Localizer:
    """One read of this language's memory rows, a catalog default for each gap.

    A memory row wins (staff `manual` or a stored `ai` default) when it keeps the English
    tokens; a missing default is inserted as `ai`, never overwriting. A chat turn must never
    fail on this, so a database error logs and the code defaults serve.

    The read runs in a savepoint, so a failed SELECT never aborts the turn's transaction. The
    seed is written on its own short session and committed at once: the turn's session may not
    commit before the reply, and no lock may be held across the turn. A dry run seeds nothing.
    """
    if lang not in LANGUAGES or lang == "en":
        return IDENTITY
    from sqlalchemy.orm import Session

    from app.models.translation_memory import TranslationMemory

    base = defaults(lang)
    table = dict(base)
    try:
        with db.begin_nested():
            rows = (
                db.query(TranslationMemory)
                .filter(
                    TranslationMemory.source_lang == "en",
                    TranslationMemory.target_lang == lang,
                    TranslationMemory.source_text.in_(list(LABELS) + (list(LANE_LABELS) if _lane_on() else [])),
                )
                .all()
            )
            have = {row.source_text for row in rows}
            for row in rows:
                if tokens_match(row.source_text, row.target_text):
                    table[row.source_text] = row.target_text
        missing = {english: target for english, target in base.items() if english not in have}
        if missing and not dry_run:
            _seed(Session(bind=db.get_bind()), lang, missing)
    except Exception:
        logger.warning("label_catalog.resolve failed for %s; using code defaults", lang, exc_info=True)
    return Localizer(lang, table)


def _seed(seed_db: Any, lang: str, missing: dict[str, str]) -> None:
    """Insert catalog defaults as `ai` rows on `seed_db`, never overwriting, then commit."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.translation_memory import SOURCE_AI, TranslationMemory

    try:
        seed_db.execute(
            pg_insert(TranslationMemory)
            .values(
                [
                    {
                        "source_text": english,
                        "source_lang": "en",
                        "target_lang": lang,
                        "target_text": target,
                        "source": SOURCE_AI,
                    }
                    for english, target in missing.items()
                ]
            )
            .on_conflict_do_nothing(constraint="uq_translation_memory_phrase")
        )
        seed_db.commit()
    finally:
        seed_db.close()
