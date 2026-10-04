import sys; sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from app.database import engine
from tests.scm.test_m3_run import _mk_stock, _mk_warehouse
db = sessionmaker(bind=engine)()
root = db.execute(text("SELECT id FROM warehouses WHERE warehouse_code='BRW'")).scalar()
child = db.execute(text("SELECT id FROM warehouses WHERE warehouse_code='BRW-IB'")).scalar() or _mk_warehouse(db, "BRW-IB", pool_warehouse_id=root)
for (pid,) in db.execute(text("SELECT id FROM products WHERE product_code IN ('SRTWC1001','SRTWC1002','CBWC2001','MWC3001','SRTFT4001','CBFT5001')")).fetchall():
    if not db.execute(text("SELECT 1 FROM stock WHERE product_id=:p AND warehouse_id=:w"), {"p": pid, "w": child}).scalar():
        _mk_stock(db, pid, child, 0)
db.execute(text("UPDATE scm.reorder_policy SET pool_netting = true WHERE scope_type = 'global'"))
db.commit(); print("pooled")
