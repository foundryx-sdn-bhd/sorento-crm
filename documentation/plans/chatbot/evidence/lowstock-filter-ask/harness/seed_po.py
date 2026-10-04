"""One fully received past PO per product, naming its supplier (the workbook's Supplier
column reads the last PO). Received in full, so it adds no open on-order quantity."""
import sys, uuid
from datetime import date
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from app.database import engine
from app.models.procurement import PurchaseOrder, PurchaseOrderLine
db = sessionmaker(bind=engine)()
wid = db.execute(text("SELECT id FROM warehouses WHERE warehouse_code='BRW'")).scalar()
CO = "00000000-0000-0000-0000-000000000001"
for code, sup in (("SRTWC1001", "400-X006"), ("SRTWC1002", "JBC"), ("CBWC2001", "400-X008"),
                  ("MWC3001", "JBCH"), ("SRTFT4001", "400-X006"), ("CBFT5001", "JBC")):
    pid = db.execute(text("SELECT id FROM products WHERE product_code=:c"), {"c": code}).scalar()
    sid = db.execute(text("SELECT id FROM suppliers WHERE supplier_code=:c"), {"c": sup}).scalar()
    num = f"PO-CLOUD-{code}"
    if db.execute(text("SELECT 1 FROM purchase_orders WHERE po_number=:n"), {"n": num}).scalar():
        continue
    po = PurchaseOrder(id=str(uuid.uuid4()), po_number=num, supplier_id=str(sid), status="completed",
                       issue_date=date(2026, 6, 1), expected_date=date(2026, 7, 1), company_id=CO)
    db.add(po); db.flush()
    db.add(PurchaseOrderLine(id=str(uuid.uuid4()), purchase_order_id=str(po.id), product_id=pid,
                             warehouse_id=wid, qty_ordered=10, qty_received=10, line_status="received",
                             expected_date=date(2026, 7, 1), source_ref="1", company_id=CO))
db.commit(); print("pos seeded")
