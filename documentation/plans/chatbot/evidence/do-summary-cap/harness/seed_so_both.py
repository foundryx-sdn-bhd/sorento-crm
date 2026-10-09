"""Second seed for the DO-SUMMARY-CAP console pass, run AFTER `seed_do_summary_cap.py`: open
sales orders for the same dealer so a "both" ask caps BOTH blocks and the two trailers name
different offer positions (SO `reply 1`, DO `reply 2`).

CAP HARDWARE SDN BHD gets 22 open sales orders, one line each, on SRTWC9001..SRTWC9022 (the
products the first seed created), ordered 5 / delivered 0, over BRW-IB and BRW-KL, dated 2026.
So the SO block's By product has 22 rows: 20 print, then `... and 2 more products (reply 1 for
the full list)`; the DO block keeps its 26-row group, `... and 6 more products (reply 2 for the
full list)`; the offer is `1. Sales order list / 2. Delivery order list / 3. Both lists`.

Sandbox only, same guard as the first seed.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date

from sqlalchemy.orm import sessionmaker

sys.path.insert(0, ".")
from app.database import engine  # noqa: E402

DEFAULT_COMPANY_ID = "00000000-0000-0000-0000-000000000001"

_db_name = (engine.url.database or "").lower()
if not os.environ.get("SORENTO_ENV_FILE") or not (_db_name.endswith("_browse") or _db_name.endswith("_ci")):
    sys.exit(
        f"refusing to seed {_db_name!r}: run with SORENTO_ENV_FILE=.env.browse against a "
        "database named *_browse or *_ci (sandbox only, never the shared dev copy)"
    )

from app.models.order import Customer, SalesOrder, SalesOrderLine  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.inventory import Warehouse  # noqa: E402

Session = sessionmaker(bind=engine)
db = Session()

cust = db.query(Customer).filter(Customer.customer_name == "CAP HARDWARE SDN BHD").one()
wh = {w.warehouse_code: w for w in db.query(Warehouse).filter(Warehouse.warehouse_code.in_(["BRW-IB", "BRW-KL"]))}
codes = [f"SRTWC{9000 + i:04d}" for i in range(1, 23)]
products = {p.product_code: p for p in db.query(Product).filter(Product.product_code.in_(codes))}
assert len(products) == 22, sorted(products)

for n, code in enumerate(codes, start=1):
    so = SalesOrder(
        id=str(uuid.uuid4()), so_number=f"SO-26-07{n:02d}", customer_id=cust.id,
        order_date=date(2026, 7, n), status="open", company_id=DEFAULT_COMPANY_ID,
    )
    db.add(so)
    db.flush()
    db.add(SalesOrderLine(
        id=str(uuid.uuid4()), sales_order_id=so.id, product_id=products[code].id,
        warehouse_id=(wh["BRW-IB"] if n % 2 else wh["BRW-KL"]).id,
        qty_ordered=5, qty_delivered=0, line_status="open", company_id=DEFAULT_COMPANY_ID,
    ))
db.commit()
print(f"seeded 22 open sales orders for {cust.customer_name}, 110 units")
