"""Synthetic seed for the DO-SUMMARY-CAP cloud console pass. Sandbox DB only (no customer
data). Adapted from the DO-LIST-REPLY-LABEL harness seed (#1515) and re-shaped to the prod
case this lane fixes: ONE dealer whose outstanding delivery orders span MORE products than
the cap, most of them 1-unit lines.

Run from sorento_crm_backend with SORENTO_ENV_FILE=.env.browse and CLOUD_ADMIN_PASSWORD set.
Writes: an admin login, an integration key (printed; the MCP server AND the backend's
EXTERNAL_API_KEY must both carry it), the default respond workspace, the chatbot contact
900000009 "Cap Dealer" (chatbot allowed, every suggested agent granted, the
`sales_orders.outstanding` reveal granted), a borrowable envelope, the one `system_settings`
row the console runner flips the lane switches on, and the DO rows:

  customer CAP HARDWARE SDN BHD, 26 products SRTWC9001..SRTWC9026 on outstanding DOs:
    SRTWC9001 12 units, SRTWC9002 8, SRTWC9003 5, SRTWC9004 3, SRTWC9005 2, the other 21 one
    unit each - 51 units over 9 DOs, two warehouses (BRW-IB, BRW-KL)
  one DELIVERED DO (status Delivered + actual delivery date) as the control

So the customer-subject summary ("CAP HARDWARE delivery order outstanding") has a 26-row
`*_By product_*` group: 20 rows print, then `... and 6 more products (reply 1 for the full
list)`. A product-subject ask on SRTWC9001 has one customer: no trailer.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import date

import bcrypt
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, ".")
from app.database import engine  # noqa: E402

DEFAULT_COMPANY_ID = "00000000-0000-0000-0000-000000000001"
SPACE_ID = "364817"
CONTACT = os.environ.get("SEED_CONTACT", "900000009")
PHONE = "+60000000009"
ADMIN_EMAIL = "cloud.admin@example.com"
ADMIN_PASSWORD = os.environ["CLOUD_ADMIN_PASSWORD"]  # throwaway, sandbox DB only


def u() -> str:
    return str(uuid.uuid4())


# This writes an admin login and an integration key, so it refuses any database that is not
# plainly a sandbox one. The shared dev copy is neither.
_db_name = (engine.url.database or "").lower()
if not os.environ.get("SORENTO_ENV_FILE") or not (_db_name.endswith("_browse") or _db_name.endswith("_ci")):
    sys.exit(
        f"refusing to seed {_db_name!r}: run with SORENTO_ENV_FILE=.env.browse against a "
        "database named *_browse or *_ci (sandbox only, never the shared dev copy)"
    )

Session = sessionmaker(bind=engine)
db = Session()

# --- admin login ---------------------------------------------------------------------
from app.models.user import User, UserRoleAssignment  # noqa: E402

admin = db.query(User).filter(User.email == ADMIN_EMAIL).one_or_none()
if admin is None:
    admin = User(id=u(), email=ADMIN_EMAIL, name="Cloud Admin", status="ACTIVE",
                 password=bcrypt.hashpw(ADMIN_PASSWORD.encode(), bcrypt.gensalt()).decode())
    db.add(admin)
    db.flush()
    role = db.execute(text("SELECT id FROM user_roles WHERE slug IN ('superadmin','super_admin') "
                           "OR name='Super Admin' LIMIT 1")).scalar()
    db.add(UserRoleAssignment(user_id=admin.id, role_id=role))
    db.flush()

# --- integration key for the MCP server ---------------------------------------------------
from app.models.integration import Integration  # noqa: E402
from app.services.integration_key_service import IntegrationKeyService  # noqa: E402

integ = Integration(name=f"cloud-mcp-{u()[:6]}", type="autocount_esb", act_as_user_id=admin.id, is_active=True)
db.add(integ)
db.flush()
key = IntegrationKeyService(db).issue_key(integ)

# --- the one system_settings row (the console runner UPDATEs it with no WHERE) -----------
from app.models.user import SystemSetting  # noqa: E402

if db.query(SystemSetting).first() is None:
    db.add(SystemSetting())
    db.flush()

# --- workspace, contact, access ------------------------------------------------------------
db.execute(text("INSERT INTO respond_workspaces (id, space_id, name, api_key_ciphertext, is_default) "
                "VALUES (gen_random_uuid(), :s, 'Cloud workspace', 'x', true) ON CONFLICT DO NOTHING"),
           {"s": SPACE_ID})
ws = db.execute(text("SELECT id FROM respond_workspaces WHERE space_id=:s"), {"s": SPACE_ID}).scalar()
cid = db.execute(text("SELECT id FROM respond_contacts WHERE respond_io_id=:c"), {"c": CONTACT}).scalar()
if cid is None:
    cid = u()
    db.execute(text("INSERT INTO respond_contacts (id, respond_io_id, phone_number, name, session_vars, "
                    "workspace_id, chatbot_allowed) "
                    "VALUES (:id, :c, :ph, 'Cap Dealer', CAST(:sv AS jsonb), :ws, true)"),
               {"id": cid, "c": CONTACT, "ph": PHONE, "sv": json.dumps({"variables": {}}), "ws": ws})
    db.execute(text("INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
                    "VALUES (gen_random_uuid(), :c, :co)"), {"c": cid, "co": DEFAULT_COMPANY_ID})
db.execute(text("UPDATE users SET respond_contact_id=:c WHERE id=:u"), {"c": cid, "u": admin.id})

from app.models.access import AccessAgent, ContactAgentAccess  # noqa: E402
from app.services.chatbot.contracts import SUGGESTED_AGENTS  # noqa: E402

for code in SUGGESTED_AGENTS:
    agent = db.query(AccessAgent).filter(AccessAgent.code == code).one_or_none()
    if agent is None:
        agent = AccessAgent(id=u(), code=code, name=code.replace("_", " ").title(), is_active=True)
        db.add(agent)
        db.flush()
    db.add(ContactAgentAccess(id=u(), respond_contact_id=cid, respond_contact_phone=PHONE,
                              agent_id=agent.id, is_allowed=True))
db.flush()

# The reveal the outstanding report gates on (`crm_outstanding_report.restricted_fields`).
from app.services.contact_field_reveal_service import set_granted_keys  # noqa: E402

set_granted_keys(db, cid, ["sales_orders.outstanding"], actor_id=admin.id, commit=False)

# --- the borrowable envelope (the console borrows the contact's last real inbound) --------
sys.path.insert(0, "tests")
from tests.chatbot.test_engine import _envelope  # noqa: E402
from app.models.chatbot_turn import ChatbotTurn  # noqa: E402

env = _envelope()
env.contact["id"] = CONTACT
env.message["contact"]["id"] = CONTACT
env.message["message"]["contactId"] = CONTACT
db.add(ChatbotTurn(contact_respond_id=CONTACT, message_id=f"cloud-seed-{u()[:8]}", ingress="webhook",
                   envelope=json.loads(env.model_dump_json()), is_test=False, status="done",
                   stage="sent", branch_kind="business_query"))

# --- warehouses, customers, products, delivery orders ---------------------------------------
from tests._mc_lookup_seed import customer, warehouse  # noqa: E402
from app.models.order import Order, OrderLine  # noqa: E402
from app.models.product import Product, ProductCategory, UnitOfMeasure  # noqa: E402

wh_ib = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="BRW-IB")
wh_kl = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="BRW-KL")
cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="CAP HARDWARE SDN BHD")

cat = ProductCategory(id=u(), category_code="DSC-CAT", category_name="Water Closets",
                      class_label="Water Closet", search_synonyms=[], company_id=DEFAULT_COMPANY_ID,
                      chatbot_max_qty=100)
uom = UnitOfMeasure(id=u(), uom_code="DSC-UOM", uom_name="Each", company_id=DEFAULT_COMPANY_ID)
db.add_all([cat, uom])
db.flush()

CODES = [f"SRTWC{9000 + i:04d}" for i in range(1, 27)]
QTY = {CODES[0]: 12, CODES[1]: 8, CODES[2]: 5, CODES[3]: 3, CODES[4]: 2}  # the rest: 1 each

products: dict[str, Product] = {}
for code in CODES:
    p = Product(id=u(), product_code=code, product_name=f"Water Closet {code}", category_id=cat.id,
                base_uom_id=uom.id, list_price=10, is_active=True, is_discontinued=False,
                company_id=DEFAULT_COMPANY_ID)
    db.add(p)
    db.flush()
    products[code] = p

status_id = {
    str(row["status_code"]).upper(): row["id"]
    for row in db.execute(text("SELECT id, status_code FROM order_statuses")).mappings().all()
}


def _do(number: str, *, order_date: date, status: str, lines: list[tuple[str, str, int]],
        delivered_on: date | None = None) -> None:
    o = Order(id=u(), order_number=number, customer_id=cust.id, order_date=order_date,
              actual_delivery_date=delivered_on, order_status_id=status_id.get(status),
              company_id=DEFAULT_COMPANY_ID)
    db.add(o)
    db.flush()
    for seq, (code, wh, qty) in enumerate(lines, start=1):
        db.add(OrderLine(id=u(), line_sequence=seq, order_id=o.id, product_id=products[code].id,
                         warehouse_id=wh, quantity=qty, company_id=DEFAULT_COMPANY_ID))
    db.flush()


# Nine outstanding DOs spread the 26 products: the five volume codes on the first three, the
# 21 one-unit codes three or four to a DO after that.
_do("DO-26-0310", order_date=date(2026, 3, 10), status="PROCESSING",
    lines=[(CODES[0], wh_ib.id, 12), (CODES[1], wh_ib.id, 8)])
_do("DO-26-0418", order_date=date(2026, 4, 18), status="SHIPPED",
    lines=[(CODES[2], wh_kl.id, 5), (CODES[3], wh_kl.id, 3)])
_do("DO-26-0522", order_date=date(2026, 5, 22), status="PENDING",
    lines=[(CODES[4], wh_ib.id, 2)])
singles = CODES[5:]
chunks = [singles[i:i + 4] for i in range(0, len(singles), 4)]  # 6 DOs: 4,4,4,4,4,1
for n, chunk in enumerate(chunks, start=1):
    wh = wh_ib if n % 2 else wh_kl
    _do(f"DO-26-06{n:02d}", order_date=date(2026, 6, n), status="NEW",
        lines=[(code, wh.id, 1) for code in chunk])
# The control: fully delivered, must appear in no figure, no block and no row.
_do("DO-26-0105-DLV", order_date=date(2026, 1, 5), status="DELIVERED",
    lines=[(CODES[0], wh_ib.id, 9)], delivered_on=date(2026, 1, 9))

db.commit()
print(f"ADMIN {ADMIN_EMAIL}")
print(f"CONTACT {CONTACT} (respond_contacts.id {cid})")
print("MCP_KEY issued (pass it to the MCP server as EXTERNAL_API_KEY and write it into "
      ".env.browse's EXTERNAL_API_KEY; not printed in logs kept)")
print(key)
