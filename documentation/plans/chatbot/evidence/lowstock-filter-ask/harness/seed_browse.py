"""Synthetic seed for the LOWSTOCK-FILTER-ASK cloud browser pass. Sandbox DB only.

Run from sorento_crm_backend with SORENTO_ENV_FILE=.env.browse.
Writes: an admin login, an integration key (printed for the MCP server), the chatbot
contact 900000008 (access agent + low stock + supplier reveal keys + a borrowable
envelope), categories / suppliers / brands mirroring the owner's cases.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import bcrypt
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, ".")
from app.database import engine  # noqa: E402

DEFAULT_COMPANY_ID = "00000000-0000-0000-0000-000000000001"
SPACE_ID = "364817"
CONTACT = "900000008"
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
                    "VALUES (:id, :c, '+60000000009', 'Cloud Buyer', CAST(:sv AS jsonb), :ws)"),
               {"id": cid, "c": CONTACT, "sv": json.dumps({"variables": {}}), "ws": ws})
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
    db.add(ContactAgentAccess(id=u(), respond_contact_id=cid, respond_contact_phone="+60000000009",
                              agent_id=agent.id, is_allowed=True))

from app.services.contact_field_reveal_service import set_granted_keys  # noqa: E402

set_granted_keys(db, cid, ["scm.low_stock_report", "purchase_orders.supplier"], actor_id=None)

# --- the borrowable envelope (the console borrows the contact's last real inbound) --------
sys.path.insert(0, "tests")
from tests.chatbot.test_top_selling_round6 import _envelope  # noqa: E402
from app.models.chatbot_turn import ChatbotTurn  # noqa: E402

db.add(ChatbotTurn(contact_respond_id=CONTACT, message_id=f"cloud-seed-{u()[:8]}", ingress="webhook",
                   envelope=json.loads(_envelope().model_dump_json()), is_test=False, status="done",
                   stage="sent", branch_kind="business_query"))

# --- master data mirroring the owner's cases -----------------------------------------------
from app.models.procurement import Supplier  # noqa: E402
from app.models.product import ProductCategory  # noqa: E402
from app.services.product_class_signal import CLASS_SYNONYMS  # noqa: E402

for code, label, brand in (
    ("SRT-WC", "Water Closet", "Sorento"), ("CB-WC", "Water Closet", "Cabana"),
    ("M-WC", "Water Closet", "Mocha"), ("BRT-WC", "Water Closet", "Bravat"),
    ("IDC-WC", "Water Closet", "IDC"), ("SRT-FT", "Tap", "Sorento"), ("CB-FT", "Tap", "Cabana"),
):
    if db.query(ProductCategory).filter(ProductCategory.category_code == code).first() is None:
        db.add(ProductCategory(id=u(), category_code=code, category_name=code, class_label=label,
                               brand_hint=brand, search_synonyms=CLASS_SYNONYMS[label],
                               company_id=DEFAULT_COMPANY_ID))
for code, name in (
    ("400-X006", "XIAMEN TAIYANG TECHNOLOGY CO.,LTD"), ("400-X008", "XIAMEN TAIYANG TECHNOLOGY CO.,LTD"),
    ("JBC", "JINBAICHUAN TRADING"), ("JBCH", "JINBAICHUAN HARDWARE"),
):
    if db.query(Supplier).filter(Supplier.supplier_code == code).first() is None:
        db.add(Supplier(id=u(), supplier_code=code, supplier_name=name, company_id=DEFAULT_COMPANY_ID,
                        is_active=True))

db.commit()
print(f"ADMIN {ADMIN_EMAIL}")
print("MCP_KEY issued (pass it to the MCP server as EXTERNAL_API_KEY; not printed in logs kept)")
print(key)
