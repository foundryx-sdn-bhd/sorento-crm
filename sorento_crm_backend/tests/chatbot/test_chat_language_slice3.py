"""CHAT-LANGUAGE slice 3 (outstanding report + top selling, finished text), AC-CL30..AC-CL35.

UAC: `documentation/plans/chatbot/chat-language-acceptance-criteria.md`, "Slice 3". The two
reports arrive as FINISHED English text; the backend parses that English (offer roster, offer
block, part markers) and only the outgoing `response` is localized, line by line, by the new
`Localizer.lines`. None of `Localizer.lines`, the slice-3 catalog entries, the two tools in
`fetch._LOCALIZED_TOOLS` or the re-print seams exist yet, so these tests are red today.

Report text is the REAL one: the MCP presenter (`sorento_crm_mcp.presenters.present_response`,
imported the way the slice-1 catalog test does) renders a hand-built route body, and that
envelope goes through `fetch.output_structurer`. Every translated render is compared with the
English render: a Localizer rewrites labels and fixed sentences, never a number, code or name.

Seams the contract implies but does not name (AC-CL34), written against the most natural form:
`turn.compose.compose_question(pending, state, localizer=...)` and
`lanes.business._outstanding_detail_reoffer(filters, rows, kind=..., localizer=...)`.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import IDENTITY, Localizer
from app.services.chatbot.lanes.business import fetch

TOOL_OUTSTANDING = "crm_outstanding_report"
TOOL_TOP = "crm_top_selling_report"

REPO_ROOT = Path(__file__).resolve().parents[3]

# The slice-3 table, pinned as literals so a drifting catalog fails loudly (the UAC's table).
SLICE3: dict[str, tuple[str, str]] = {
    "Sales orders": ("Pesanan jualan", "销售订单"),
    "Ordered": ("Dipesan", "订购"),
    "Transferred to DO": ("Dipindahkan ke DO", "已转 DO"),
    "Order date range": ("Julat tarikh pesanan", "订单日期范围"),
    "Delivery orders": ("Pesanan penghantaran", "送货单"),
    "DO qty": ("Kuantiti DO", "DO 数量"),
    "Delivered": ("Dihantar", "已送货"),
    "DO date range": ("Julat tarikh DO", "DO 日期范围"),
    "Product": ("Produk", "产品"),
    "Order date": ("Tarikh pesanan", "订单日期"),
    "Brand": ("Jenama", "品牌"),
    "DO Number": ("No. DO", "DO 编号"),
    "DO Qty": ("Kuantiti DO", "DO 数量"),
    "DO Date": ("Tarikh DO", "DO 日期"),
    "Category": ("Kategori", "类别"),
    "Sales agent": ("Ejen jualan", "销售代理"),
    "Channel": ("Saluran", "渠道"),
    "Delivery date": ("Tarikh penghantaran", "送货日期"),
    "Ranked by": ("Disusun mengikut", "排名依据"),
    "Basis": ("Asas", "统计口径"),
    "Items with sales": ("Item dengan jualan", "有销售的项目"),
    "Categories with sales": ("Kategori dengan jualan", "有销售的类别"),
    "all": ("semua", "全部"),
    "{from} to {to}": ("{from} hingga {to}", "{from} 至 {to}"),
    "Amount": ("Amaun", "金额"),
    "Quantity": ("Kuantiti", "数量"),
    "Delivered (transferred to DO)": ("Dihantar (dipindahkan ke DO)", "已送货（已转 DO）"),
    "Sales order outstanding": ("Pesanan jualan tertunggak", "未交货销售订单"),
    "Delivery order outstanding": ("Pesanan penghantaran tertunggak", "未送达送货单"),
    "By location": ("Mengikut lokasi", "按位置"),
    "By customer": ("Mengikut pelanggan", "按客户"),
    "By product": ("Mengikut produk", "按产品"),
    "By month": ("Mengikut bulan", "按月份"),
    "Sales order list": ("Senarai pesanan jualan", "销售订单列表"),
    "Delivery order list": ("Senarai pesanan penghantaran", "送货单列表"),
    "Both lists": ("Kedua-dua senarai", "两个列表"),
    "No open sales order.": ("Tiada pesanan jualan terbuka.", "没有未完成的销售订单。"),
    "No outstanding delivery order.": (
        "Tiada pesanan penghantaran tertunggak.",
        "没有未送达的送货单。",
    ),
    "Sales order figures are not enabled for your account.": (
        "Angka pesanan jualan tidak diaktifkan untuk akaun anda.",
        "您的账户未开通销售订单数据。",
    ),
    "Reply with a number for detail:": ("Balas dengan nombor untuk butiran:", "回复数字查看详情："),
    "Reply 1 for the sales order list.": (
        "Balas 1 untuk senarai pesanan jualan.",
        "回复 1 查看销售订单列表。",
    ),
    "Reply 1 for the delivery order list.": (
        "Balas 1 untuk senarai pesanan penghantaran.",
        "回复 1 查看送货单列表。",
    ),
    "No sales found.": ("Tiada jualan ditemui.", "未找到销售记录。"),
    "By quantity or by amount?": ("Mengikut kuantiti atau amaun?", "按数量还是按金额？"),
    "Do you want the top items inside one category, or the categories ranked against each other?": (
        "Anda mahu item teratas dalam satu kategori, atau kategori disusun antara satu sama lain?",
        "您要看某一类别内的热销项目，还是各类别之间的排名？",
    ),
    "Delivered (transferred to DO) or ordered?": (
        "Dihantar (dipindahkan ke DO) atau dipesan?",
        "已送货（已转 DO）还是已订购？",
    ),
    "Sorry, I can only share sales figures for your own account.": (
        "Maaf, saya hanya boleh berkongsi angka jualan untuk akaun anda sendiri.",
        "抱歉，我只能提供您本人账户的销售数据。",
    ),
    "Items with no sale in this period are not ranked.": (
        "Item tanpa jualan dalam tempoh ini tidak disenaraikan.",
        "此期间没有销售的项目不参与排名。",
    ),
    "Categories with no sale in this period are not ranked.": (
        "Kategori tanpa jualan dalam tempoh ini tidak disenaraikan.",
        "此期间没有销售的类别不参与排名。",
    ),
    "Reply with a rank number to see that category's top items.": (
        "Balas dengan nombor kedudukan untuk melihat item teratas kategori itu.",
        "回复排名数字查看该类别的热销项目。",
    ),
    "Reply with a rank number to see that item's customers and months.": (
        "Balas dengan nombor kedudukan untuk melihat pelanggan dan bulan bagi item itu.",
        "回复排名数字查看该项目的客户和月份。",
    ),
    "Sales report is not enabled for your account.": (
        "Laporan jualan tidak diaktifkan untuk akaun anda.",
        "您的账户未开通销售报告。",
    ),
    "Note: only {pct}% of sales orders in this period carry a sales agent.": (
        "Nota: hanya {pct}% pesanan jualan dalam tempoh ini mempunyai ejen jualan.",
        "注：此期间只有 {pct}% 的销售订单带有销售代理。",
    ),
    "Top {n} selling items": ("{n} item paling laris", "最畅销的 {n} 个项目"),
    "Top {n} selling categories": ("{n} kategori paling laris", "最畅销的 {n} 个类别"),
    "Bottom {n} selling items": ("{n} item paling kurang laris", "最滞销的 {n} 个项目"),
    "Bottom {n} selling categories": ("{n} kategori paling kurang laris", "最滞销的 {n} 个类别"),
    "Top selling items": ("Item paling laris", "最畅销项目"),
    "Top selling categories": ("Kategori paling laris", "最畅销类别"),
    "Least sold items": ("Item paling kurang dijual", "销量最少的项目"),
    "Least sold categories": ("Kategori paling kurang dijual", "销量最少的类别"),
    "{code}: customers and months": ("{code}: pelanggan dan bulan", "{code}：客户和月份"),
    "How many items do you want to see? Reply with a number from 1 to {max}.": (
        "Berapa banyak item yang anda mahu lihat? Balas dengan nombor dari 1 hingga {max}.",
        "您想看多少个项目？请回复 1 到 {max} 之间的数字。",
    ),
    "How many categories do you want to see? Reply with a number from 1 to {max}.": (
        "Berapa banyak kategori yang anda mahu lihat? Balas dengan nombor dari 1 hingga {max}.",
        "您想看多少个类别？请回复 1 到 {max} 之间的数字。",
    ),
    "I can list at most the top {n} in one reply.": (
        "Saya boleh senaraikan paling banyak {n} teratas dalam satu balasan.",
        "我一次最多只能列出前 {n} 个。",
    ),
}

# English literal -> the fragment(s) that must still be in the presenter source (or, for the
# backend-owned line, the lane source), so the pin breaks if the presenter rewords. Entries
# built from f-string pieces name the fixed pieces. `None` means "the literal itself".
_PRESENTER_FRAGMENTS: dict[str, tuple[str, ...] | None] = {
    "Top {n} selling items": ("selling {word}*", "'Top'"),
    "Top {n} selling categories": ("selling {word}*", "'Top'"),
    "Bottom {n} selling items": ("selling {word}*", "'Bottom'"),
    "Bottom {n} selling categories": ("selling {word}*", "'Bottom'"),
    "Top selling items": ("Top selling {noun}",),
    "Top selling categories": ("Top selling {noun}",),
    "Least sold items": ("Least sold {noun}",),
    "Least sold categories": ("Least sold {noun}",),
    "{code}: customers and months": ("{code}: customers and months",),
    "Items with sales": ("with sales: ",),
    "Categories with sales": ("with sales: ",),
    "Items with no sale in this period are not ranked.": None,
    "Categories with no sale in this period are not ranked.": ("Items with no sale in this period are not ranked.",),
    "How many items do you want to see? Reply with a number from 1 to {max}.": (
        "do you want to see? ",
        "Reply with a number from 1 to ",
    ),
    "How many categories do you want to see? Reply with a number from 1 to {max}.": (
        "How many {noun} do you want to see? ",
    ),
    "Note: only {pct}% of sales orders in this period carry a sales agent.": (
        "Note: only ",
        "% of sales orders in this period carry a sales agent.",
    ),
    "{from} to {to}": (" to ",),
    "Amount": ("'Amount'",),
    "Quantity": ("'Quantity'",),
    "Ordered": ("'Ordered'",),
    "Delivered (transferred to DO)": ("'Delivered (transferred to DO)'",),
    "Sales orders": ("Sales orders: ",),
    "Delivery orders": ("Delivery orders: ",),
    "Reply 1 for the sales order list.": ("Reply 1 for the {offer[0].lower()}.", "Sales order list"),
    "Reply 1 for the delivery order list.": ("Reply 1 for the {offer[0].lower()}.", "Delivery order list"),
    "I can list at most the top {n} in one reply.": None,
}
_LANE_OWNED = {"I can list at most the top {n} in one reply."}


def _pres():
    """The real MCP presenter: the `sorento_crm_mcp` beside THIS checkout, appended so a stale
    install cannot win (the idiom of `test_chat_language_catalog.py`)."""
    mcp_root = REPO_ROOT / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        from sorento_crm_mcp import presenters
    except ImportError:  # pragma: no cover - only where the package is not on disk
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    return presenters


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


def _digits(text: str) -> list[str]:
    return re.findall(r"\d+", text)


# --------------------------------------------------------------------------- #
# AC-CL30: every listed literal is catalogued and still what the presenter writes
# --------------------------------------------------------------------------- #


def test_ac_cl30_every_slice3_literal_is_catalogued_with_the_reviewed_wording():
    for english, (ms, zh) in SLICE3.items():
        assert english in label_catalog.LABELS, english
        assert label_catalog.LABELS[english]["ms"] == ms, english
        assert label_catalog.LABELS[english]["zh"] == zh, english


def test_ac_cl30_slice3_entries_have_ms_zh_and_keep_their_tokens():
    for english in SLICE3:
        entry = label_catalog.LABELS[english]
        for lang in ("ms", "zh"):
            assert entry[lang].strip(), (english, lang)
            assert label_catalog.tokens_match(english, entry[lang]), (english, lang)
            assert label_catalog.defaults(lang)[english] == entry[lang]


def test_ac_cl30_singular_ranking_titles_are_catalogued_too():
    """The presenter writes `*Top 1 selling item*` for n = 1 (`presenters.py:~2777`): the
    coder adds the singular entries, so a one-row title translates and keeps its 1."""
    for lang in ("ms", "zh"):
        loc = _loc(lang)
        for title in (
            "*Top 1 selling item*",
            "*Top 1 selling category*",
            "*Bottom 1 selling item*",
            "*Bottom 1 selling category*",
        ):
            out = loc.lines(title)
            assert out != title, (lang, title)
            assert out.startswith("*") and out.endswith("*"), (lang, out)
            assert "1" in out, (lang, out)


def test_ac_cl30_the_presenter_still_writes_every_pinned_literal():
    source = (REPO_ROOT / "sorento_crm_mcp" / "sorento_crm_mcp" / "presenters.py").read_text()
    lane_source = (
        REPO_ROOT / "sorento_crm_backend" / "app" / "services" / "chatbot" / "lanes" / "business" / "__init__.py"
    ).read_text()
    for english in SLICE3:
        fragments = _PRESENTER_FRAGMENTS.get(english, None)
        if english in _LANE_OWNED:
            # The ceiling note is the lane's, not the presenter's (`business/__init__.py:~493`).
            assert "I can list at most the top " in lane_source, english
            continue
        if fragments is None:
            fragments = (english,)
        for fragment in fragments:
            assert fragment in source, (english, fragment)


# --------------------------------------------------------------------------- #
# AC-CL31: Localizer.lines, one test per rule
# --------------------------------------------------------------------------- #


def test_ac_cl31_rule1_whole_line_sentence():
    ms, zh = _loc("ms"), _loc("zh")
    assert ms.lines("No sales found.") == "Tiada jualan ditemui."
    assert zh.lines("No sales found.") == "未找到销售记录。"
    assert ms.lines("Reply with a number for detail:") == "Balas dengan nombor untuk butiran:"
    assert zh.lines("Reply 1 for the sales order list.") == "回复 1 查看销售订单列表。"
    assert ms.lines("No outstanding delivery order.") == "Tiada pesanan penghantaran tertunggak."


def test_ac_cl31_rule1_template_sentence_reinserts_the_values():
    ms, zh = _loc("ms"), _loc("zh")
    assert (
        ms.lines("Note: only 40% of sales orders in this period carry a sales agent.")
        == "Nota: hanya 40% pesanan jualan dalam tempoh ini mempunyai ejen jualan."
    )
    assert (
        zh.lines("How many items do you want to see? Reply with a number from 1 to 250.")
        == "您想看多少个项目？请回复 1 到 250 之间的数字。"
    )
    assert (
        ms.lines("How many categories do you want to see? Reply with a number from 1 to 9.")
        == "Berapa banyak kategori yang anda mahu lihat? Balas dengan nombor dari 1 hingga 9."
    )
    assert (
        zh.lines("I can list at most the top 1,000 in one reply.") == "我一次最多只能列出前 1,000 个。"
    )


def test_ac_cl31_rule1_a_wrapped_heading_keeps_its_wrapper():
    ms, zh = _loc("ms"), _loc("zh")
    assert ms.lines("*Sales order outstanding*") == "*Pesanan jualan tertunggak*"
    assert zh.lines("*Delivery order outstanding*") == "*未送达送货单*"
    assert ms.lines("*_By location_*") == "*_Mengikut lokasi_*"
    assert zh.lines("*_By customer_*") == "*_按客户_*"
    assert ms.lines("_By month_") == "_Mengikut bulan_"
    assert ms.lines("*Top 10 selling items*") == "*10 item paling laris*"
    assert zh.lines("*Top 10 selling items*") == "*最畅销的 10 个项目*"
    assert zh.lines("*Bottom 5 selling categories*") == "*最滞销的 5 个类别*"
    assert ms.lines("*Least sold items*") == "*Item paling kurang dijual*"
    assert ms.lines("*SRTWC286: customers and months*") == "*SRTWC286: pelanggan dan bulan*"
    assert zh.lines("*SRTWC286: customers and months*") == "*SRTWC286：客户和月份*"


def test_ac_cl31_rule2_a_numbered_line_keeps_its_number():
    ms, zh = _loc("ms"), _loc("zh")
    assert ms.lines("1. Sales order list") == "1. Senarai pesanan jualan"
    assert ms.lines("2. Delivery order list") == "2. Senarai pesanan penghantaran"
    assert zh.lines("3. Both lists") == "3. 两个列表"


def test_ac_cl31_rule3_a_label_line_translates_the_label_only():
    ms, zh = _loc("ms"), _loc("zh")
    assert ms.lines("Sales orders: 3") == "Pesanan jualan: 3"
    assert ms.lines("Transferred to DO: 4") == "Dipindahkan ke DO: 4"
    assert zh.lines("Delivery orders: 2") == "送货单: 2"
    assert ms.lines("Outstanding: 6") == "Tertunggak: 6"
    assert ms.lines("Product: SRTWC286") == "Produk: SRTWC286"
    assert ms.lines("Customer: Hanlim Trading") == "Pelanggan: Hanlim Trading"
    assert ms.lines("Location: IB (BRW-IB, MWH-IB)") == "Lokasi: IB (BRW-IB, MWH-IB)"
    assert ms.lines("Channel: Dealer") == "Saluran: Dealer"
    assert zh.lines("Items with sales: 1,204") == "有销售的项目: 1,204"
    assert ms.lines("Total: Qty 3, RM 1,234.50") == "Jumlah: Qty 3, RM 1,234.50"


def test_ac_cl31_rule3_a_value_of_exactly_all_becomes_the_catalog_word():
    assert _loc("ms").lines("Product: all") == "Produk: semua"
    assert _loc("zh").lines("Customer: all") == "客户: 全部"
    # Only an EXACT `all`: a longer value is untouched.
    assert _loc("ms").lines("Customer: all customers") == "Pelanggan: all customers"


def test_ac_cl31_rule3_a_date_range_value_takes_the_from_to_template():
    assert (
        _loc("ms").lines("Order date range: 01/09/2026 to 30/09/2026")
        == "Julat tarikh pesanan: 01/09/2026 hingga 30/09/2026"
    )
    assert (
        _loc("zh").lines("Delivery date: 01/09/2026 to 30/09/2026")
        == "送货日期: 01/09/2026 至 30/09/2026"
    )
    # A single day prints once and is not a range.
    assert _loc("ms").lines("DO date range: 05/01/2026") == "Julat tarikh DO: 05/01/2026"


def test_ac_cl31_rule3_value_translation_needs_a_catalogued_label():
    assert _loc("ms").lines("Foo: all") == "Foo: all"
    assert _loc("ms").lines("Foo: 01/09/2026 to 30/09/2026") == "Foo: 01/09/2026 to 30/09/2026"


def test_ac_cl31_rule4_data_lines_and_unknown_lines_are_unchanged():
    for lang in ("ms", "zh"):
        loc = _loc(lang)
        for line in (
            "1. SRTWC286: Qty 3, RM 1,234.50",
            "12. Unassigned: Qty 3, RM 5.00",
            "Sep 2026: Qty 3, RM 5.00",
            "Unassigned: 5 (O/S: 2)",
            "BRW-IB: 10 (O/S: 6)",
            "(2/3)",
            "Something the catalog has never heard of",
            "",
        ):
            assert loc.lines(line) == line, (lang, line)


def test_ac_cl31_the_join_is_byte_exact():
    ms = _loc("ms")
    text = "Brand: all\n\n\nBrand: all\n"
    assert ms.lines(text) == "Jenama: semua\n\n\nJenama: semua\n"
    assert ms.lines("\nBrand: all") == "\nJenama: semua"
    assert ms.lines("Brand: all\n\n") == "Jenama: semua\n\n"
    assert ms.lines("") == ""
    # Lines that change nothing keep every blank line and trailing space.
    assert ms.lines("x  \n\n y\n") == "x  \n\n y\n"


def test_ac_cl31_the_english_localizer_is_the_identity():
    text = "*Sales order outstanding*\nProduct: all\n1. Sales order list\nNo sales found.\n"
    assert IDENTITY.lines(text) == text
    assert Localizer("en", {}).lines(text) == text
    assert Localizer("en", label_catalog.defaults("en")).lines(text) == text


def test_ac_cl31_lines_reads_the_localizers_own_table():
    """A staff-edited wording (a `manual` memory row) wins, as it does for `label` / `text`."""
    table = {**label_catalog.defaults("ms"), "Brand": "Jenama Barang", "No sales found.": "Tak jumpa."}
    loc = Localizer("ms", table)
    assert loc.lines("Brand: Acme\nNo sales found.") == "Jenama Barang: Acme\nTak jumpa."


# --------------------------------------------------------------------------- #
# Real presenter output -> output_structurer
# --------------------------------------------------------------------------- #


def _outstanding_report(*, so: bool = True, do: bool = True, detail: str | None = None) -> dict:
    report: dict = {
        "product_code": "SRTWC286",
        "customer_name": None,
        "location_token": "IB",
        "warehouse_codes": ["BRW-IB", "MWH-IB"],
        "order_date_from": "2026-09-01",
        "order_date_to": "2026-09-30",
        "so": None,
        "do": None,
    }
    if so:
        report.update(
            so={
                "so_count": 3,
                "ordered_qty": 1234,
                "transferred_qty": 4,
                "outstanding_qty": 6,
                "order_date_min": "2026-09-01",
                "order_date_max": "2026-09-30",
            },
            so_by_location=[{"code": "BRW-IB", "ordered_qty": 10, "outstanding_qty": 6}],
            so_by_customer=[
                {"customer_name": None, "ordered_qty": 5, "outstanding_qty": 2},
                {"customer_name": "HANLIM TRADING", "ordered_qty": 5, "outstanding_qty": 4},
            ],
        )
    if do:
        report.update(
            do={
                "do_count": 2,
                "do_qty": 8,
                "delivered_qty": 3,
                "pending_qty": 5,
                "do_date_min": None,
                "do_date_max": None,
            },
            do_by_location=[{"code": "BRW-IB", "do_qty": 8, "pending_qty": 5}],
            do_by_customer=[{"customer_name": "HANLIM TRADING", "do_qty": 8, "pending_qty": 5}],
        )
    if detail:
        report["detail"] = detail
        report["so_rows"] = [
            {
                "so_number": "SO-001",
                "customer_name": "HANLIM TRADING",
                "product_code": "SRTWC286",
                "location": "BRW-IB",
                "ordered_qty": 10,
                "transferred_qty": 4,
                "outstanding_qty": 6,
                "order_date": "2026-09-02",
            }
        ]
        report["do_rows"] = [
            {
                "do_number": "DO-009",
                "customer_name": "HANLIM TRADING",
                "product_code": "SRTWC286",
                "location": "BRW-IB",
                "do_qty": 8,
                "delivered_qty": 3,
                "pending_qty": 5,
                "do_date": "2026-09-03",
            }
        ]
    return report


def _top_report(**over) -> dict:
    report: dict = {
        "group": "item",
        "n": 3,
        "rank_by": "amount",
        "basis": "ordered",
        "total_count": 12,
        "date_from": "2026-09-01",
        "date_to": "2026-09-30",
        "filters": {"channel": "dealer"},
        "sales_agent_fill_rate": 0.4,
        "rows": [
            {"rank": 1, "code": "SRTWC286", "quantity": 3, "amount": 1234.5},
            {"rank": 2, "code": None, "quantity": 2, "amount": 5},
        ],
    }
    report.update(over)
    return report


def _envelope(tool: str, report: dict) -> dict:
    return json.loads(_pres().present_response(tool, json.dumps(report)))


def _structure(tool: str, envelope: dict, lang: str | None, semantic_input: dict | None = None) -> dict:
    ctx: dict = {"tool": tool, "semantic_input": semantic_input or {}, "entities": []}
    if lang:
        ctx["localizer"] = _loc(lang)
    return fetch.output_structurer(copy.deepcopy(envelope), ctx)


def _rest(out: dict) -> dict:
    """Everything the structurer returns except the printed text."""
    return {k: v for k, v in out.items() if k != "response"}


# --------------------------------------------------------------------------- #
# AC-CL32: outstanding report
# --------------------------------------------------------------------------- #


def test_ac_cl32_outstanding_report_ms_translates_headers_blocks_and_offer():
    env = _envelope(TOOL_OUTSTANDING, _outstanding_report())
    lines = _structure(TOOL_OUTSTANDING, env, "ms")["response"].split("\n")
    for expected in (
        "Produk: SRTWC286",
        "Pelanggan: semua",
        "Lokasi: IB (BRW-IB, MWH-IB)",
        "Tarikh pesanan: 01/09/2026 hingga 30/09/2026",
        "*Pesanan jualan tertunggak*",
        "Pesanan jualan: 3",
        "Dipesan: 1,234",
        "Dipindahkan ke DO: 4",
        "Tertunggak: 6",
        "Julat tarikh pesanan: 01/09/2026 hingga 30/09/2026",
        "*_Mengikut lokasi_*",
        "BRW-IB: 10 (O/S: 6)",
        "*_Mengikut pelanggan_*",
        "Unassigned: 5 (O/S: 2)",
        "HANLIM TRADING: 5 (O/S: 4)",
        "*Pesanan penghantaran tertunggak*",
        "Pesanan penghantaran: 2",
        "Kuantiti DO: 8",
        "Dihantar: 3",
        "Tertunggak: 5",
        "Julat tarikh DO: semua",
        "Balas dengan nombor untuk butiran:",
        "1. Senarai pesanan jualan",
        "2. Senarai pesanan penghantaran",
        "3. Kedua-dua senarai",
    ):
        assert expected in lines, expected


def test_ac_cl32_outstanding_report_zh_translates_and_leaves_no_english_label():
    env = _envelope(TOOL_OUTSTANDING, _outstanding_report())
    text = _structure(TOOL_OUTSTANDING, env, "zh")["response"]
    for expected in ("产品: SRTWC286", "客户: 全部", "*未交货销售订单*", "*未送达送货单*", "*_按位置_*", "3. 两个列表"):
        assert expected in text.split("\n"), expected
    assert "回复数字查看详情：" in text.split("\n")
    for english in ("Sales order outstanding", "Transferred to DO", "Reply with a number", "Both lists"):
        assert english not in text, english


def test_ac_cl32_outstanding_numbers_codes_and_names_are_byte_identical_to_english():
    env = _envelope(TOOL_OUTSTANDING, _outstanding_report())
    english = _structure(TOOL_OUTSTANDING, env, None)["response"]
    for lang in ("ms", "zh"):
        translated = _structure(TOOL_OUTSTANDING, env, lang)["response"]
        assert translated != english, lang
        assert _digits(translated) == _digits(english), lang
        for token in ("SRTWC286", "BRW-IB", "MWH-IB", "HANLIM TRADING", "Unassigned", "1,234", "(O/S: 6)"):
            assert translated.count(token) == english.count(token), (lang, token)
        # Data lines (`name: total (O/S: n)`) are exactly the English ones, in the same places.
        en_lines, tr_lines = english.split("\n"), translated.split("\n")
        assert len(en_lines) == len(tr_lines), lang
        for en_line, tr_line in zip(en_lines, tr_lines):
            if "(O/S:" in en_line:
                assert tr_line == en_line, (lang, en_line)


def test_ac_cl32_the_roster_and_stored_offer_are_identical_to_the_english_render():
    env = _envelope(TOOL_OUTSTANDING, _outstanding_report())
    english = _structure(TOOL_OUTSTANDING, env, None)
    assert english["outstanding_ask"]["last_result_set"] == [
        {"idx": 1, "label": "Sales order list", "value": "so"},
        {"idx": 2, "label": "Delivery order list", "value": "do"},
        {"idx": 3, "label": "Both lists", "value": "both"},
    ]
    for lang in ("ms", "zh"):
        translated = _structure(TOOL_OUTSTANDING, env, lang)
        assert translated["response"] != english["response"], lang
        assert _rest(translated) == _rest(english), lang
        # The stored offer stays English: it is what the parse read, and what re-prints.
        offer_text = translated["outstanding_ask"]["filters"]["offer_text"]
        assert offer_text.startswith("Reply with a number for detail:"), lang
        assert "1. Sales order list" in offer_text, lang


def test_ac_cl32_single_scope_offer_sentence_translates_and_still_arms_the_roster():
    env = _envelope(TOOL_OUTSTANDING, _outstanding_report(do=False))
    english = _structure(TOOL_OUTSTANDING, env, None)
    ms = _structure(TOOL_OUTSTANDING, env, "ms")
    assert "Balas 1 untuk senarai pesanan jualan." in ms["response"].split("\n")
    assert "Reply 1 for the sales order list." not in ms["response"]
    assert ms["outstanding_ask"]["last_result_set"] == [{"idx": 1, "label": "Sales order list", "value": "so"}]
    assert _rest(ms) == _rest(english)
    zh = _structure(TOOL_OUTSTANDING, env, "zh")
    assert "回复 1 查看销售订单列表。" in zh["response"].split("\n")


def test_ac_cl32_a_miss_and_a_refused_half_translate_their_sentences():
    miss = _envelope(TOOL_OUTSTANDING, _outstanding_report())
    miss["response"] = (
        "Product: SRTWC286\nCustomer: all\nLocation: all\nOrder date: all\n\n"
        "*Sales order outstanding*\nNo open sales order.\n\n"
        "*Delivery order outstanding*\nNo outstanding delivery order."
    )
    miss["has_result"] = False
    ms = _structure(TOOL_OUTSTANDING, miss, "ms")
    assert "Tiada pesanan jualan terbuka." in ms["response"].split("\n")
    assert "Tiada pesanan penghantaran tertunggak." in ms["response"].split("\n")
    assert "Tarikh pesanan: semua" in ms["response"].split("\n")
    assert ms["has_result"] is False
    assert ms["outstanding_ask"] is None

    refused = _envelope(TOOL_OUTSTANDING, _outstanding_report(so=False))
    refused["response"] += "\n\nSales order figures are not enabled for your account."
    out = _structure(TOOL_OUTSTANDING, refused, "zh")["response"]
    assert "您的账户未开通销售订单数据。" in out.split("\n")


def test_ac_cl32_the_detail_list_header_translates_and_its_rows_keep_every_value():
    env = _envelope(TOOL_OUTSTANDING, _outstanding_report(detail="both"))
    english = _structure(TOOL_OUTSTANDING, env, None)
    ms = _structure(TOOL_OUTSTANDING, env, "ms")
    lines = ms["response"].split("\n")
    assert "Produk: SRTWC286" in lines
    assert "Tarikh pesanan: 01/09/2026 hingga 30/09/2026" in lines
    assert _digits(ms["response"]) == _digits(english["response"])
    for value in ("SO-001", "DO-009", "HANLIM TRADING", "02/09/2026", "03/09/2026"):
        assert value in ms["response"], value
    assert _rest(ms) == _rest(english)


# --------------------------------------------------------------------------- #
# AC-CL33: top selling
# --------------------------------------------------------------------------- #


def test_ac_cl33_top_selling_zh_translates_title_metric_basis_notes_and_ask():
    env = _envelope(TOOL_TOP, _top_report())
    lines = _structure(TOOL_TOP, env, "zh")["response"].split("\n")
    for expected in (
        "*最畅销的 3 个项目*",
        "排名依据: 金额",
        "统计口径: 订购",
        "有销售的项目: 12",
        "客户: 全部",
        "类别: 全部",
        "品牌: 全部",
        "销售代理: 全部",
        "注：此期间只有 40% 的销售订单带有销售代理。",
        "渠道: Dealer",
        "送货日期: 01/09/2026 至 30/09/2026",
        "回复排名数字查看该项目的客户和月份。",
    ):
        assert expected in lines, expected


def test_ac_cl33_ranked_by_quantity_and_the_delivered_basis_translate():
    env = _envelope(TOOL_TOP, _top_report(rank_by="quantity", basis="delivered"))
    zh = _structure(TOOL_TOP, env, "zh")["response"].split("\n")
    assert "排名依据: 数量" in zh
    assert "统计口径: 已送货（已转 DO）" in zh
    ms = _structure(TOOL_TOP, env, "ms")["response"].split("\n")
    assert "Disusun mengikut: Kuantiti" in ms
    assert "Asas: Dihantar (dipindahkan ke DO)" in ms


def test_ac_cl33_rank_lines_and_the_result_set_are_unchanged():
    env = _envelope(TOOL_TOP, _top_report())
    english = _structure(TOOL_TOP, env, None)
    for lang in ("ms", "zh"):
        translated = _structure(TOOL_TOP, env, lang)
        assert translated["response"] != english["response"], lang
        lines = translated["response"].split("\n")
        assert "1. SRTWC286: Qty 3, RM 1,234.50" in lines, lang
        assert "2. Unassigned: Qty 2, RM 5.00" in lines, lang
        assert _rest(translated) == _rest(english), lang
        assert translated["outstanding_ask"]["last_result_set"] == env["result_set"], lang
        assert _digits(translated["response"]) == _digits(english["response"]), lang


def test_ac_cl33_category_ranking_and_bottom_titles_translate():
    env = _envelope(TOOL_TOP, _top_report(group="category", n=None, direction="bottom", basis="delivered"))
    ms = _structure(TOOL_TOP, env, "ms")["response"].split("\n")
    assert "*Kategori paling kurang dijual*" in ms
    assert "Kategori dengan jualan: 12" in ms
    assert "Kategori tanpa jualan dalam tempoh ini tidak disenaraikan." in ms
    assert "Balas dengan nombor kedudukan untuk melihat item teratas kategori itu." in ms


def test_ac_cl33_a_one_row_title_keeps_its_number():
    env = _envelope(TOOL_TOP, _top_report(n=1, rows=[{"rank": 1, "code": "SRTWC286", "quantity": 3, "amount": 5}]))
    ms = _structure(TOOL_TOP, env, "ms")["response"].split("\n")
    assert "*Top 1 selling item*" not in ms
    assert ms[0].startswith("*") and "1" in ms[0] and "item" in ms[0]


def test_ac_cl33_how_many_ask_translates_and_arms_nothing():
    env = _envelope(TOOL_TOP, _top_report(rows=[], n=None))
    assert env["result_type"] == "top_selling_how_many"
    english = _structure(TOOL_TOP, env, None)
    zh = _structure(TOOL_TOP, env, "zh")
    assert "您想看多少个项目？请回复 1 到 12 之间的数字。" in zh["response"].split("\n")
    assert _rest(zh) == _rest(english)
    ms = _structure(TOOL_TOP, env, "ms")
    assert "Berapa banyak item yang anda mahu lihat? Balas dengan nombor dari 1 hingga 12." in ms["response"].split("\n")


def test_ac_cl33_a_miss_and_a_refusal_translate():
    miss = _envelope(TOOL_TOP, _top_report(rows=[], total_count=0))
    ms = _structure(TOOL_TOP, miss, "ms")
    assert "Tiada jualan ditemui." in ms["response"].split("\n")
    refused = _envelope(TOOL_TOP, {"code": "customer_not_permitted", "message": "x"})
    assert refused["result_type"] == "top_selling_refused"
    zh = _structure(TOOL_TOP, refused, "zh")
    assert zh["response"] == "抱歉，我只能提供您本人账户的销售数据。"
    disabled = _envelope(TOOL_TOP, {"code": "sales_report_not_enabled", "message": "x"})
    assert _structure(TOOL_TOP, disabled, "ms")["response"] == "Laporan jualan tidak diaktifkan untuk akaun anda."


def test_ac_cl33_the_detail_reply_translates_headings_and_keeps_rows_and_months():
    detail = _top_report(
        rows=[],
        n=None,
        detail={
            "code": "SRTWC286",
            "by_customer": [{"customer_name": "HANLIM TRADING", "quantity": 3, "amount": 5}],
            "by_month": [{"month": "2026-09", "quantity": 3, "amount": 5}],
        },
        totals={"quantity": 3, "amount": 5},
    )
    env = _envelope(TOOL_TOP, detail)
    english = _structure(TOOL_TOP, env, None)["response"]
    ms = _structure(TOOL_TOP, env, "ms")
    lines = ms["response"].split("\n")
    assert "*SRTWC286: pelanggan dan bulan*" in lines
    assert "*_Mengikut pelanggan_*" in lines
    assert "*_Mengikut bulan_*" in lines
    assert "Jumlah: Qty 3, RM 5.00" in lines
    assert "1. HANLIM TRADING: Qty 3, RM 5.00" in lines
    assert "Sep 2026: Qty 3, RM 5.00" in lines
    assert _digits(ms["response"]) == _digits(english)


def test_ac_cl33_a_part_split_reply_keeps_its_markers_and_still_splits():
    from app.services.chatbot import engine

    rows = [{"rank": i, "code": f"SRT{i:05d}", "quantity": i, "amount": i * 1.5} for i in range(1, 260)]
    env = _envelope(TOOL_TOP, _top_report(n=259, total_count=259, rows=rows))
    english = _structure(TOOL_TOP, env, None)["response"]
    assert "(1/" in english, "the fixture must be long enough to split into marked parts"
    for lang in ("ms", "zh"):
        translated = _structure(TOOL_TOP, env, lang)["response"]
        assert translated != english, lang
        assert re.findall(r"(?m)^\(\d+/\d+\)$", translated) == re.findall(r"(?m)^\(\d+/\d+\)$", english), lang
        en_parts = engine.split_marked_message(english)
        tr_parts = engine.split_marked_message(translated)
        assert len(tr_parts) == len(en_parts) > 1, lang
        assert tr_parts[0].startswith("(1/") or "\n(1/" in tr_parts[0], lang


def test_ac_cl33_lane_notes_above_the_ranking_are_localized_too():
    env = _envelope(TOOL_TOP, _top_report())
    notes = ["I can list at most the top 1,000 in one reply.", "I don't know 'zzz' as a category."]
    english = _structure(TOOL_TOP, env, None, {"top_selling_notes": notes})
    ms = _structure(TOOL_TOP, env, "ms", {"top_selling_notes": notes})
    lines = ms["response"].split("\n")
    assert "Saya boleh senaraikan paling banyak 1,000 teratas dalam satu balasan." in lines
    # An uncatalogued note (it carries the customer's own word) stays as written.
    assert "I don't know 'zzz' as a category." in lines
    assert _rest(ms) == _rest(english)


# --------------------------------------------------------------------------- #
# AC-CL34: the stored English offer re-prints in the turn's language
# --------------------------------------------------------------------------- #

OFFER_TWO = (
    "Reply with a number for detail:\n1. Sales order list\n2. Delivery order list\n3. Both lists"
)
OFFER_ONE = "Reply 1 for the sales order list."
ROWS_TWO = [
    {"idx": 1, "label": "Sales order list", "value": "so"},
    {"idx": 2, "label": "Delivery order list", "value": "do"},
    {"idx": 3, "label": "Both lists", "value": "both"},
]


def _pending(offer_text: str, rows: list[dict]):
    from app.services.chatbot.turn import pending as pending_mod

    options = [{"position": r["idx"], "label": r["label"], "value": r["value"]} for r in rows]
    return pending_mod.ask(
        "outstanding_detail",
        options,
        asked_at_turn=1,
        payload={"filters": {"offer_text": offer_text, "product_code": "SRTWC286"}},
    )


def test_ac_cl34_compose_question_reprints_the_stored_offer_in_the_turns_language():
    from app.services.chatbot.turn.compose import compose_question

    pend = _pending(OFFER_TWO, ROWS_TWO)
    english = compose_question(pend, None)
    assert english.text == OFFER_TWO
    ms = compose_question(pend, None, localizer=_loc("ms"))
    assert ms.text == (
        "Balas dengan nombor untuk butiran:\n1. Senarai pesanan jualan\n"
        "2. Senarai pesanan penghantaran\n3. Kedua-dua senarai"
    )
    assert ms.actions[0]["text"] == ms.text
    zh = compose_question(pend, None, localizer=_loc("zh"))
    assert zh.text == "回复数字查看详情：\n1. 销售订单列表\n2. 送货单列表\n3. 两个列表"


def test_ac_cl34_the_reprint_arms_the_same_question_and_the_same_roster():
    from app.services.chatbot.turn.compose import compose_question

    pend = _pending(OFFER_TWO, ROWS_TWO)
    english = compose_question(pend, None)
    ms = compose_question(pend, None, localizer=_loc("ms"))
    assert ms.question == english.question == pend
    assert ms.actions[0]["result_set"] == english.actions[0]["result_set"]
    assert ms.actions[0]["quick_replies"] == english.actions[0]["quick_replies"]
    # The stored offer is never rewritten.
    assert pend.payload["filters"]["offer_text"] == OFFER_TWO


def test_ac_cl34_a_single_scope_offer_reprints_localized():
    from app.services.chatbot.turn.compose import compose_question

    pend = _pending(OFFER_ONE, ROWS_TWO[:1])
    assert compose_question(pend, None, localizer=_loc("ms")).text == "Balas 1 untuk senarai pesanan jualan."
    assert compose_question(pend, None, localizer=_loc("zh")).text == "回复 1 查看销售订单列表。"


def test_ac_cl34_no_localizer_or_the_english_one_reprints_byte_identical():
    from app.services.chatbot.turn.compose import compose_question

    pend = _pending(OFFER_TWO, ROWS_TWO)
    base = compose_question(pend, None).text
    assert compose_question(pend, None, localizer=None).text == base
    assert compose_question(pend, None, localizer=IDENTITY).text == base
    assert compose_question(pend, None, localizer=Localizer("en", {})).text == base


def test_ac_cl34_the_detail_reoffer_reprints_the_stored_offer_in_the_turns_language():
    from app.services.chatbot.lanes import business

    filters = {"offer_text": OFFER_TWO, "product_code": "SRTWC286"}
    english = business._outstanding_detail_reoffer(filters, ROWS_TWO)
    assert english["fetch"]["response"] == OFFER_TWO
    ms = business._outstanding_detail_reoffer(filters, ROWS_TWO, localizer=_loc("ms"))
    assert ms["fetch"]["response"] == (
        "Balas dengan nombor untuk butiran:\n1. Senarai pesanan jualan\n"
        "2. Senarai pesanan penghantaran\n3. Kedua-dua senarai"
    )
    # The roster and the stored filters (and so the stored offer) stay English.
    assert ms["fetch"]["outstanding_ask"] == english["fetch"]["outstanding_ask"]
    assert ms["fetch"]["outstanding_ask"]["filters"]["offer_text"] == OFFER_TWO
    zh = business._outstanding_detail_reoffer(filters, ROWS_TWO, localizer=_loc("zh"))
    assert zh["fetch"]["response"].startswith("回复数字查看详情：")


def test_ac_cl34_the_reoffer_fallback_text_localizes_when_no_offer_was_stored():
    from app.services.chatbot.lanes import business

    ms = business._outstanding_detail_reoffer({}, ROWS_TWO, localizer=_loc("ms"))
    assert ms["fetch"]["response"] == (
        "Balas dengan nombor untuk butiran:\n1. Senarai pesanan jualan\n"
        "2. Senarai pesanan penghantaran\n3. Kedua-dua senarai"
    )


# --------------------------------------------------------------------------- #
# AC-CL35: no localizer or en is byte-identical to today
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tool,build",
    [
        (TOOL_OUTSTANDING, lambda: _envelope(TOOL_OUTSTANDING, _outstanding_report())),
        (TOOL_OUTSTANDING, lambda: _envelope(TOOL_OUTSTANDING, _outstanding_report(do=False))),
        (TOOL_OUTSTANDING, lambda: _envelope(TOOL_OUTSTANDING, _outstanding_report(detail="both"))),
        (TOOL_TOP, lambda: _envelope(TOOL_TOP, _top_report())),
        (TOOL_TOP, lambda: _envelope(TOOL_TOP, _top_report(rows=[], n=None))),
    ],
)
def test_ac_cl35_absent_identity_and_en_localizers_leave_the_english_text_untouched(tool, build):
    env = build()
    plain = fetch.output_structurer(copy.deepcopy(env), {"tool": tool, "semantic_input": {}, "entities": []})
    assert plain["response"] == env["response"]
    for loc in (IDENTITY, Localizer("en", {}), None):
        ctx = {"tool": tool, "semantic_input": {}, "entities": [], "localizer": loc}
        assert fetch.output_structurer(copy.deepcopy(env), ctx) == plain

