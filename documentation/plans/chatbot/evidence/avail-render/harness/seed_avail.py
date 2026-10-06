"""Synthetic seed for the AVAIL-RENDER cloud browser pass. Sandbox DB only (no customer data).

Run from sorento_crm_backend with SORENTO_ENV_FILE=.env.browse and CLOUD_ADMIN_PASSWORD set.
Writes: an admin login, an integration key (printed for the MCP server), the chatbot contact
900000011 "Avail Buyer" on the Availability stock policy over warehouse AVRBRW, the access
agents, a borrowable envelope, and four products in one category (max 100):

  AVR1001  discontinued, 100 on hand
  AVR1002  live, 30 on hand
  AVR1003  live, 0 on hand, no shipment
  AVR1004  discontinued, 0 on hand, inbound shipment ETA 2026-10-19
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
CONTACT = "900000011"
PHONE = "+60000000011"
ADMIN_EMAIL = "cloud.admin@example.com"
ADMIN_PASSWORD = os.environ["CLOUD_ADMIN_PASSWORD"]  # throwaway, sandbox DB only


def u() -> str:
    return str(uuid.uuid4())


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

# --- workspace, contact, access ------------------------------------------------------------
db.execute(text("INSERT INTO respond_workspaces (id, space_id, name, api_key_ciphertext, is_default) "
                "VALUES (gen_random_uuid(), :s, 'Cloud workspace', 'x', true) ON CONFLICT DO NOTHING"),
           {"s": SPACE_ID})
ws = db.execute(text("SELECT id FROM respond_workspaces WHERE space_id=:s"), {"s": SPACE_ID}).scalar()
cid = db.execute(text("SELECT id FROM respond_contacts WHERE respond_io_id=:c"), {"c": CONTACT}).scalar()
if cid is None:
    cid = u()
    db.execute(text("INSERT INTO respond_contacts (id, respond_io_id, phone_number, name, session_vars, workspace_id) "
                    "VALUES (:id, :c, :ph, 'Avail Buyer', CAST(:sv AS jsonb), :ws)"),
               {"id": cid, "c": CONTACT, "ph": PHONE, "sv": json.dumps({"variables": {}}), "ws": ws})
    db.execute(text("INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
                    "VALUES (gen_random_uuid(), :c, :co)"), {"c": cid, "co": DEFAULT_COMPANY_ID})
db.execute(text("UPDATE users SET respond_contact_id=:c WHERE id=:u"), {"c": cid, "u": admin.id})

from app.models.access import AccessAgent, ContactAgentAccess, StockVisibilityPolicy  # noqa: E402
from app.services.chatbot.contracts import SUGGESTED_AGENTS  # noqa: E402

for code in SUGGESTED_AGENTS:
    agent = db.query(AccessAgent).filter(AccessAgent.code == code).one_or_none()
    if agent is None:
        agent = AccessAgent(id=u(), code=code, name=code.replace("_", " ").title(), is_active=True)
        db.add(agent)
        db.flush()
    db.add(ContactAgentAccess(id=u(), respond_contact_id=cid, respond_contact_phone=PHONE,
                              agent_id=agent.id, is_allowed=True))

# --- the borrowable envelope (the console borrows the contact's last real inbound) --------
sys.path.insert(0, "tests")
from tests.chatbot.test_top_selling_round6 import _envelope  # noqa: E402
from app.models.chatbot_turn import ChatbotTurn  # noqa: E402

db.add(ChatbotTurn(contact_respond_id=CONTACT, message_id=f"cloud-seed-{u()[:8]}", ingress="webhook",
                   envelope=json.loads(_envelope().model_dump_json()), is_test=False, status="done",
                   stage="sent", branch_kind="business_query"))

# --- warehouse, availability policy, products, stock, shipment ----------------------------
from tests._mc_lookup_seed import stock, warehouse  # noqa: E402
from tests.test_stock_availability_block import _attachment, _incoming_line, _incoming_shipment  # noqa: E402
from app.models.product import Product, ProductCategory, UnitOfMeasure  # noqa: E402

wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="AVRBRW")
db.add(StockVisibilityPolicy(id=u(), contact_id=cid, mode="availability", warehouse_ids=[wh.id]))

cat = ProductCategory(id=u(), category_code="AVR-CAT", category_name="Avail Render Taps",
                      class_label="Tap", search_synonyms=[], company_id=DEFAULT_COMPANY_ID,
                      chatbot_max_qty=100)
uom = UnitOfMeasure(id=u(), uom_code="AVR-UOM", uom_name="Each", company_id=DEFAULT_COMPANY_ID)
db.add_all([cat, uom])
db.flush()

products = {}
for code, discontinued, on_hand in (
    ("AVR1001", True, 100),
    ("AVR1002", False, 30),
    ("AVR1003", False, 0),
    ("AVR1004", True, 0),
):
    p = Product(id=u(), product_code=code, product_name=f"Avail Render {code}", category_id=cat.id,
                base_uom_id=uom.id, list_price=10, is_active=True, is_discontinued=discontinued,
                company_id=DEFAULT_COMPANY_ID)
    db.add(p)
    db.flush()
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=on_hand)
    products[code] = p

# R5: the stock ask reads only a shipment that carries its packing list (`attachment_id`).
shipment = _incoming_shipment(db, eta=date(2026, 10, 19), attachment_id=_attachment(db).id)
_incoming_line(db, shipment_id=shipment.id, product_id=products["AVR1004"].id)

db.commit()
print(f"ADMIN {ADMIN_EMAIL}")
print("MCP_KEY issued (pass it to the MCP server as EXTERNAL_API_KEY; not printed in logs kept)")
print(key)
