"""Synthetic planning inputs: products below their reorder level in the owner's
categories, linked to the seeded suppliers, so a real run plans them. Sandbox only."""
import sys, uuid
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from app.database import engine
from app.services.scm import reorder_engine as eng
from tests.scm.test_m3_run import _link, _mk_demand, _mk_movement, _mk_product, _mk_stock, _mk_warehouse

CO = "00000000-0000-0000-0000-000000000001"
db = sessionmaker(bind=engine)()
from tests.scm.conftest import ensure_reference_data
ensure_reference_data(db)
wid = db.execute(text("SELECT id FROM warehouses WHERE warehouse_code='BRW'")).scalar() or _mk_warehouse(db, "BRW")
def sup(code): return db.execute(text("SELECT id FROM suppliers WHERE supplier_code=:c"), {"c": code}).scalar()
def cat(code): return db.execute(text("SELECT id FROM product_categories WHERE category_code=:c"), {"c": code}).scalar()
ROWS = [  # code, category, supplier, on hand, level
    ("SRTWC1001", "SRT-WC", "400-X006", 2, 10), ("SRTWC1002", "SRT-WC", "JBC", 1, 8),
    ("CBWC2001", "CB-WC", "400-X008", 0, 6), ("MWC3001", "M-WC", "JBCH", 3, 12),
    ("SRTFT4001", "SRT-FT", "400-X006", 1, 9), ("CBFT5001", "CB-FT", "JBC", 20, 5),
]
for code, c, s, onhand, level in ROWS:
    if db.execute(text("SELECT 1 FROM products WHERE product_code=:c"), {"c": code}).scalar():
        continue
    pid = _mk_product(db, code)
    db.execute(text("UPDATE products SET category_id=:cat, description=:d WHERE id=:p"),
               {"cat": cat(c), "d": f"{c} item {code}", "p": pid})
    _mk_stock(db, pid, wid, onhand)
    _mk_movement(db, pid, wid, 1, days_ago=7)
    _mk_demand(db, pid, wid, 5.0)
    _link(db, pid, sup(s), lead=30, moq=1, mult=1)
    db.execute(text("INSERT INTO scm.reorder_level (id, product_id, warehouse_id, level, source, company_id, created_at) "
                    "VALUES (:id, :p, NULL, :l, 'manual', :co, now())"), {"id": str(uuid.uuid4()), "p": pid, "l": level, "co": CO})
eng.ensure_reorder_policy_defaults(db)
db.commit()
print("seeded", len(ROWS))
