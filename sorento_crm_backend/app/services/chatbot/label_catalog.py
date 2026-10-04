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
    f"{{available}} available. {REFER_TO_SALESMAN}": {
        "ms": "{available} ada. Sila rujuk jurujual anda.",
        "zh": "有 {available} 件。请联系您的销售员。",
    },
    "ETA {eta}.": {"ms": "ETA {eta}.", "zh": "ETA {eta}。"},
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
    # Slice 2: PO / SO / SPO / orders rows. PO, SO, SPO and GR stay as printed (owner Q3).
    "Order Number": {"ms": "No. Pesanan", "zh": "订单号"},
    "Customer": {"ms": "Pelanggan", "zh": "客户"},
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
    "Customers": {"ms": "Pelanggan", "zh": "客户"},
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
    'Your request is out of the scope of my ability and require human assistance. We are directing your enquiry to the correct person. Please wait for a moment.': {"ms": 'Permintaan anda di luar kemampuan saya dan memerlukan bantuan kakitangan. Kami sedang menghubungkan pertanyaan anda kepada orang yang betul. Sila tunggu sebentar.', "zh": '您的请求超出了我的能力范围，需要人工协助。我们正在将您的询问转交给相关负责人，请稍候。'},
    'This inquiry has been routed to the respective person-in-charge (PIC) from {team} team. We will get back to you soon. Thanks for your patience.': {"ms": 'Pertanyaan ini telah diserahkan kepada pegawai bertanggungjawab (PIC) daripada pasukan {team}. Kami akan menghubungi anda tidak lama lagi. Terima kasih atas kesabaran anda.', "zh": '此询问已转交给 {team} 团队的相关负责人（PIC）。我们会尽快回复您，感谢您的耐心等待。'},
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
    "I don't know '{word}' as a category.": {"ms": "Saya tidak mengenali '{word}' sebagai kategori.", "zh": "我不认识 '{word}' 这个类别。"},
    "I don't know '{word}' as a supplier.": {"ms": "Saya tidak mengenali '{word}' sebagai pembekal.", "zh": "我不认识 '{word}' 这个供应商。"},
    'Which category do you mean? Reply with a number or "all":': {"ms": 'Kategori mana yang anda maksudkan? Balas dengan nombor atau "all":', "zh": '您指的是哪个类别？请回复编号或 "all"：'},
    "Which category do you mean? Reply with a number:": {"ms": "Kategori mana yang anda maksudkan? Balas dengan nombor:", "zh": "您指的是哪个类别？请回复编号："},
    'Which supplier do you mean? Reply with a number or "all":': {"ms": 'Pembekal mana yang anda maksudkan? Balas dengan nombor atau "all":', "zh": '您指的是哪个供应商？请回复编号或 "all"：'},
    "Which supplier do you mean? Reply with a number:": {"ms": "Pembekal mana yang anda maksudkan? Balas dengan nombor:", "zh": "您指的是哪个供应商？请回复编号："},
    "Low stock report cancelled.": {"ms": "Laporan stok rendah dibatalkan.", "zh": "低库存报告已取消。"},
    "I still can't place '{word}'. Ask for the low stock report again with a category or \"all\".": {"ms": "Saya masih tidak dapat mengenali '{word}'. Minta laporan stok rendah sekali lagi dengan kategori atau \"all\".", "zh": "我仍然无法识别 '{word}'。请再次索取低库存报告，并提供类别或 \"all\"。"},
    "{code} has no {types}.": {"ms": "{code} tiada {types}.", "zh": "{code} 没有{types}。"},
}

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
    "customers": "Customers",
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


def defaults(lang: str) -> dict[str, str]:
    """English -> `lang` for every catalog entry whose translation keeps the English tokens."""
    if lang == "en":
        return {}
    return {
        english: entry[lang]
        for english, entry in LABELS.items()
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
        'Your request is out of the scope of my ability and require human assistance. We are directing your enquiry to the correct person. Please wait for a moment.',
        'This inquiry has been routed to the respective person-in-charge (PIC) from {team} team. We will get back to you soon. Thanks for your patience.',
        '{header} I found {count}, please type a little more of the name.',
        'and {n} others, reply with the full code.',
        "Couldn't find {names}.",
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
DIRECT_ONLY = frozenset({"I have {names}.", "{code} has no {types}."})

#: Report value words `Localizer.lines` translates after a catalogued label. An explicit set, never
#: "any catalog key", so data that happens to read like a label (a status named "Status") is safe.
VALUE_WORDS = frozenset({"all", "Amount", "Quantity", "Ordered", "Delivered (transferred to DO)"})
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


class Localizer:
    """Rewrites catalogued labels and sentences for `language`; everything else is untouched."""

    def __init__(self, language: str, table: dict[str, str]) -> None:
        self.language = language
        self.table = table
        # `{token}` entries as (whole-string regex over the English shape, target).
        self._templates: list[tuple[re.Pattern[str], str, int, bool]] = []  # most fixed text wins
        # The INLINE sentences the table carries: (regex for a start-anchored match, target,
        # fixed characters, token names). Longest fixed text wins where two start together.
        self._inline: list[tuple[re.Pattern[str], str, int, tuple[str, ...]]] = []
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

    def _text(self, s: str, *, inline: bool) -> str:
        if s in self.table:
            return self.table[s]
        best = None
        for regex, target, fixed, is_inline in self._templates:
            if is_inline and not inline:
                continue  # INLINE sentences are replaced by `reply()` at their own boundaries
            m = regex.fullmatch(s)
            if m and (best is None or fixed > best[0]):
                best = (fixed, m.groupdict(), target)
        if best is None:
            return s
        values = best[1]
        return _TOKEN.sub(lambda t: values.get(t.group(1), t.group(0)), best[2])

    def lines(self, text: str) -> str:
        """Finished report text, line by line: the first rule that matches wins.

        1. a catalogued sentence, bare or inside one wrapper (`*x*`, `*_x_*`, `_x_`);
        2. a flag line (`"⚠️  *(<flag>)*"`) whose flag is catalogued, then `"<n>. <sentence>"`
           (or `"- <sentence>"`);
        3. a label line (`"<label>: <value>"`, bold `"*<label>:* <value>"`, optionally led by
           `"<n>. "` or `"- "`) whose label is catalogued: the label translates, and the value only when
           it is exactly a `VALUE_WORDS` entry or a `dd/mm/yyyy to dd/mm/yyyy` range;
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

    def _inline_line(self, text: str) -> str:
        found: list[tuple[int, int, int, str]] = []
        for regex, target, fixed, _names in self._inline:
            for m in regex.finditer(text):
                values = m.groupdict()
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
        if flag and flag.group(2) in self.table and not tokens(flag.group(2)):
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
            if not found or head not in self.table or _BREAKDOWN_VALUE.match(value):
                continue  # a breakdown row (`5 (O/S: 2)`) whose name collides with a label is data
            return prefix + left + self.table[head] + sep + self._value(value)
        return line

    def _value(self, value: str) -> str:
        if value in VALUE_WORDS and value in self.table:
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
                    TranslationMemory.source_text.in_(list(LABELS)),
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
