"""OI-EMAIL-PRINT print pass from the code: rows -> handover_remark -> _build_handover_context
-> the template oihr_0005 writes (blank schema) -> EmailTemplateService.render. Masked names."""
import importlib.util, sys
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
sys.path.insert(0, ".")
from app.models.email_template import EmailTemplate
from app.services.email_template_service import EmailTemplateService
from app.services.project_order_inquiry_service import (
    IV_ORDER, IV_ORDER_BACK, IV_DELAY, IV_CHANGE_SO, IV_PRE_ORDERED, _build_handover_context, _format_handover_was,
    _handover_fmt_date, _handover_verb_keys, handover_remark,
)
from tests._pg_fixture import blank_session
from tests.test_email_layout_migrations import _run

out = sys.argv[1]
spec = importlib.util.spec_from_file_location("m5", "alembic/versions/oihr_0005_print_one_page.py")
m5 = importlib.util.module_from_spec(spec); spec.loader.exec_module(m5)

PLANNING = ("Planning keeps this Buy: 140 landed for this line on SPO-2026/09-0036 stay linked "
            "to this line; adjust the linkage if the Buy replaces them")

def item(so, so_date, pso, customer, project, kind, line_no, **row):
    was = row.pop("was", None)
    r = SimpleNamespace(**{"verb": None, "qty": None, "delivery_date": None, "item_code": None,
                          "cited_document": None, "note": None, "stock_location": None, **row})
    return {
        "pso_id": pso, "so_number": so, "customer": customer, "project": project,
        "stock_location": r.stock_location, "line_no": line_no, "order_inquiry_id": "oi-1",
        "row_id": f"{so}-{line_no}", "verb_keys": _handover_verb_keys(kind, r, was),
        "line": {
            "so_date": so_date, "so_number": so, "customer": customer, "project": project,
            "item_code": r.item_code, "qty": "0" if kind == "cancelled" else str(r.qty), "delivery_date": _handover_fmt_date(r.delivery_date),
            "location": r.stock_location or "", "remark": handover_remark(kind, r, was),
            "was": _format_handover_was(was),
        },
        "actor": {"name": "CS Staff", "email": "cs@example.com"},
    }

D = date
cases = {
    "case1": [  # SO418869 shape: Buy kept over landed goods (planning note) + two DELAYs
        item("SO418869", "12/09/2026", "p1", "CUSTOMER A TRADING (PROJECT)", "PROJECT E", "raised", 1,
             verb=IV_ORDER, qty=140, delivery_date=D(2026, 10, 30), item_code="WC-TB2140-WHT",
             stock_location="JB-WH", note=PLANNING),
        item("SO418869", "12/09/2026", "p1", "CUSTOMER A TRADING (PROJECT)", "PROJECT E", "settled", 2,
             verb=IV_ORDER, qty=24, delivery_date=D(2026, 11, 13), item_code="WC-BS3311-CHR",
             stock_location="JB-WH", was={"delivery_date": D(2026, 10, 30)}),
        item("SO418869", "12/09/2026", "p1", "CUSTOMER A TRADING (PROJECT)", "PROJECT E", "settled", 3,
             verb=IV_ORDER, qty=36, delivery_date=D(2026, 11, 13), item_code="WC-MX0870-BLK",
             stock_location="JB-WH", was={"delivery_date": D(2026, 10, 30)}),
    ],
    "case2": [  # two orders, qty + date change, ORDER BACK with a borrow note, released supply
        item("SO402757", "18/08/2026", "p2", "CUSTOMER B SDN BHD", "PROJECT R", "settled", 1,
             verb=IV_ORDER, qty=436, delivery_date=D(2026, 9, 25), item_code="SRTWT6808",
             stock_location="KL-WH", was={"qty": 372, "delivery_date": D(2026, 10, 1)}),
        item("SO402757", "18/08/2026", "p2", "CUSTOMER B SDN BHD", "PROJECT R", "raised", 2,
             verb=IV_ORDER_BACK, qty=16, delivery_date=D(2026, 9, 25), item_code="SRTWT6808",
             stock_location="KL-WH", cited_document="SPO-2026/08-0061",
             note="Order-back: SO401990 line 3 lent 16 to SO402757 line 2"),
        item("SO402757", "18/08/2026", "p2", "CUSTOMER B SDN BHD", "PROJECT R", "raised", 3,
             verb=IV_ORDER, qty=20, delivery_date=D(2026, 10, 2), item_code="CWCX604-S-RL-NEW",
             stock_location="KL-WH", note="Replaces 20 used; SPO-2026/08-0012 received 02 Sep 2026 into KL-WH"),
        item("SO402761", "19/08/2026", "p3", "CUSTOMER C ENTERPRISE", "PROJECT T", "settled", 1,
             verb=IV_ORDER, qty=8, delivery_date=D(2026, 10, 9), item_code="SRTBS2201",
             stock_location="PG-WH", was={"qty": 12}),
    ],
    "case4": [  # owner's case: AutoCount swapped the product; worst width (NEW QTY + NEW DATE)
        item("SO419012", "02/10/2026", "p5", "CUSTOMER F SDN BHD", "PROJECT K", "settled", 1,
             verb=IV_ORDER, qty=30, delivery_date=D(2026, 11, 6), item_code="SRT382-6-DIY",
             stock_location="KL-WH", was={"item_code": "SRT382-6", "qty": 24, "delivery_date": D(2026, 10, 30)}),
        item("SO419012", "02/10/2026", "p5", "CUSTOMER F SDN BHD", "PROJECT K", "raised", 2,
             verb=IV_ORDER, qty=12, delivery_date=D(2026, 11, 6), item_code="SRT382-6-DIY",
             stock_location="KL-WH", note="Replaces 12 used; Was item SRT382-6", was={"item_code": "SRT382-6"}),
        item("SO419012", "02/10/2026", "p5", "CUSTOMER F SDN BHD", "PROJECT K", "settled", 3,
             verb=IV_ORDER, qty=8, delivery_date=D(2026, 11, 13), item_code="CWCX604-S-RL-NEW",
             stock_location="KL-WH", was={"delivery_date": D(2026, 11, 6)}),
    ],
    "case5": [  # remaining wordings: CANCEL ALL, MOVED TO, CHANGE SO NO, ORDER BACK (no doc)
        item("SO419100", "05/10/2026", "p6", "CUSTOMER G SDN BHD", "PROJECT N", "cancelled", 1,
             verb=IV_ORDER, qty=36, delivery_date=D(2026, 11, 2), item_code="SRTFV1001",
             stock_location="KL-WH", was={"qty": 36}),
        item("SO419100", "05/10/2026", "p6", "CUSTOMER G SDN BHD", "PROJECT N", "raised", 2,
             verb=IV_CHANGE_SO, qty=12, delivery_date=D(2026, 11, 2), item_code="SRTUB206-BI",
             stock_location="KL-WH", note="Moved to SO419101"),
        item("SO419100", "05/10/2026", "p6", "CUSTOMER G SDN BHD", "PROJECT N", "raised", 3,
             verb=IV_CHANGE_SO, qty=4, delivery_date=D(2026, 11, 2), item_code="SRTBS2201",
             stock_location="KL-WH"),
        item("SO419100", "05/10/2026", "p6", "CUSTOMER G SDN BHD", "PROJECT N", "raised", 4,
             verb=IV_ORDER_BACK, qty=16, delivery_date=D(2026, 11, 2), item_code="SRTWT6808",
             stock_location="KL-WH", cited_document="SPO-2026/08-0061"),
        item("SO419100", "05/10/2026", "p6", "CUSTOMER G SDN BHD", "PROJECT N", "raised", 5,
             verb=IV_PRE_ORDERED, qty=6, delivery_date=D(2026, 11, 2), item_code="SRTMX0405",
             stock_location="KL-WH"),
    ],
    "case6": [  # owner's hand test SO421287: one line, 5 columns, "ORDER, NEW ITEM CODE"
        item("SO421287", "06/10/2026", "p7", "CUSTOMER H SDN BHD", "PROJECT Q", "raised", 1,
             verb=IV_ORDER, qty=12, delivery_date=D(2026, 11, 6), item_code="SRT382-6-DIY",
             stock_location="KL-WH", was={"item_code": "SRT382-6"}),
    ],
    "case7": [  # 7 columns + the long combo ORDER BACK, NEW ITEM CODE with a 16-char code
        item("SO421300", "07/10/2026", "p8", "CUSTOMER J SDN BHD", "PROJECT W", "raised", 1,
             verb=IV_ORDER_BACK, qty=16, delivery_date=D(2026, 11, 6), item_code="CWCX604-S-RL-NEW",
             stock_location="KL-WH", cited_document="SPO-2026/08-0061", was={"item_code": "CWCX604-S-RL"}),
        item("SO421300", "07/10/2026", "p8", "CUSTOMER J SDN BHD", "PROJECT W", "settled", 2,
             verb=IV_ORDER, qty=30, delivery_date=D(2026, 11, 13), item_code="SRT382-6-DIY",
             stock_location="KL-WH", was={"item_code": "SRT382-6", "qty": 24, "delivery_date": D(2026, 11, 6)}),
    ],
    "case3": [  # long order: 42 lines
        item("SO418900", "01/10/2026", "p4", "CUSTOMER D SDN BHD", "PROJECT M", "raised", i,
             verb=IV_ORDER, qty=10 + i, delivery_date=D(2026, 11, 20), item_code=f"SRTWT{6800 + i}",
             stock_location="KL-WH", note=PLANNING if i % 7 == 0 else None)
        for i in range(1, 43)
    ],
}
with blank_session() as db:
    _run(m5, db)
    template = db.query(EmailTemplate).filter(EmailTemplate.code == m5.TEMPLATE_CODE).one()
    svc = EmailTemplateService(db)
    for name, pending in cases.items():
        context, _ = _build_handover_context(pending)
        context["today"] = "09/10/2026"
        context["actor"] = pending[0]["actor"]
        rendered = svc.render(template, context)
        assert "Planning keeps" not in rendered["body_html"] and "Planning keeps" not in rendered["body_text"]
        open(f"{out}/{name}.html", "w").write(rendered["body_html"])
        open(f"{out}/{name}.txt", "w").write(rendered["subject"] + "\n\n" + rendered["body_text"])
        print(name, rendered["subject"], "| remarks:", sorted({l["remark"] for l in context["handover"]["lines"]}))
