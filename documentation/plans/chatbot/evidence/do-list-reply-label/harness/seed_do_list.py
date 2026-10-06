"""Synthetic seed for the DO-LIST-REPLY-LABEL cloud console replay. Sandbox DB only (no
customer data). Copied from the AVAIL-ONHAND-BASIS harness seed and re-pointed at the
outstanding report's own tables.

Run from sorento_crm_backend with SORENTO_ENV_FILE=.env.browse and CLOUD_ADMIN_PASSWORD set.
Writes: an admin login, an integration key (printed; the MCP server AND the backend's
EXTERNAL_API_KEY must both carry it, see the sandbox notes in ../console-replay-06oct.md),
the default respond workspace, the chatbot contact 900000008 "DO List Dealer" (chatbot
allowed, every suggested agent granted, the `sales_orders.outstanding` reveal granted), a
borrowable envelope, the one `system_settings` row the console runner flips the lane
switches on, and the DO rows the summary and the list are rendered from:

  SRTWT7445   five delivery orders still outstanding (non-delivered status, no actual
              delivery date), spread over two warehouses and two customers, dated 2026
  SRTWT7446   rides on two of those DOs, so the summary's "By product" block has two rows
  one DELIVERED DO of SRTWT7445 (status Delivered + actual delivery date): the control that
              must appear nowhere
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
CONTACT = os.environ.get("SEED_CONTACT", "900000008")
PHONE = "+60000000008"
ADMIN_EMAIL = "cloud.admin@example.com"
ADMIN_PASSWORD = os.environ["CLOUD_ADMIN_PASSWORD"]  # throwaway, sandbox DB only


def u() -> str:
    return str(uuid.uuid4())


# Reviewer should-fix (6 Oct 2026): this writes an admin login and an integration key, so it
# refuses any database that is not plainly a sandbox one. The shared dev copy is neither.
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
                    "VALUES (:id, :c, :ph, 'DO List Dealer', CAST(:sv AS jsonb), :ws, true)"),
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
cust_a = customer(db, company_id=DEFAULT_COMPANY_ID, name="HANLIM HARDWARE SDN BHD")
cust_b = customer(db, company_id=DEFAULT_COMPANY_ID, name="JOO SENG HARDWARE SDN BHD")

cat = ProductCategory(id=u(), category_code="DLRL-CAT", category_name="Water Taps",
                      class_label="Tap", search_synonyms=[], company_id=DEFAULT_COMPANY_ID,
                      chatbot_max_qty=100)
uom = UnitOfMeasure(id=u(), uom_code="DLRL-UOM", uom_name="Each", company_id=DEFAULT_COMPANY_ID)
db.add_all([cat, uom])
db.flush()

products: dict[str, Product] = {}
for code in ("SRTWT7445", "SRTWT7446"):
    p = Product(id=u(), product_code=code, product_name=f"Water Tap {code}", category_id=cat.id,
                base_uom_id=uom.id, list_price=10, is_active=True, is_discontinued=False,
                company_id=DEFAULT_COMPANY_ID)
    db.add(p)
    db.flush()
    products[code] = p

status_id = {
    str(row["status_code"]).upper(): row["id"]
    for row in db.execute(text("SELECT id, status_code FROM order_statuses")).mappings().all()
}


def _do(number: str, *, customer_id: str, order_date: date, status: str, lines: list[tuple[str, str, int]],
        delivered_on: date | None = None) -> None:
    o = Order(id=u(), order_number=number, customer_id=customer_id, order_date=order_date,
              actual_delivery_date=delivered_on, order_status_id=status_id.get(status),
              company_id=DEFAULT_COMPANY_ID)
    db.add(o)
    db.flush()
    for seq, (code, wh, qty) in enumerate(lines, start=1):
        db.add(OrderLine(id=u(), line_sequence=seq, order_id=o.id, product_id=products[code].id,
                         warehouse_id=wh, quantity=qty, company_id=DEFAULT_COMPANY_ID))
    db.flush()


# Five outstanding DOs (the summary's population), two warehouses, two customers.
_do("DO-26-0101", customer_id=cust_a.id, order_date=date(2026, 1, 12), status="PROCESSING",
    lines=[("SRTWT7445", wh_ib.id, 4)])
_do("DO-26-0215", customer_id=cust_a.id, order_date=date(2026, 2, 15), status="SHIPPED",
    lines=[("SRTWT7445", wh_ib.id, 2), ("SRTWT7446", wh_ib.id, 1)])
_do("DO-26-0330", customer_id=cust_b.id, order_date=date(2026, 3, 30), status="PENDING",
    lines=[("SRTWT7445", wh_kl.id, 6)])
_do("DO-26-0607", customer_id=cust_b.id, order_date=date(2026, 6, 7), status="PROCESSING",
    lines=[("SRTWT7445", wh_kl.id, 1), ("SRTWT7446", wh_kl.id, 3)])
_do("DO-26-0921", customer_id=cust_a.id, order_date=date(2026, 9, 21), status="NEW",
    lines=[("SRTWT7445", wh_ib.id, 5)])
# The control: fully delivered, must appear in no figure, no block and no row.
_do("DO-26-0105-DLV", customer_id=cust_a.id, order_date=date(2026, 1, 5), status="DELIVERED",
    lines=[("SRTWT7445", wh_ib.id, 9)], delivered_on=date(2026, 1, 9))

db.commit()
print(f"ADMIN {ADMIN_EMAIL}")
print(f"CONTACT {CONTACT} (respond_contacts.id {cid})")
print("MCP_KEY issued (pass it to the MCP server as EXTERNAL_API_KEY and write it into "
      ".env.browse's EXTERNAL_API_KEY; not printed in logs kept)")
print(key)
