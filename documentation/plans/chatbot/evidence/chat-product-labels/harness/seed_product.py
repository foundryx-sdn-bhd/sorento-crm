"""Synthetic product for the CHAT-PRODUCT-LABELS cloud browser pass. Sandbox DB only.
Run from sorento_crm_backend with SORENTO_ENV_FILE=.env.browse.
SRTWC286: name, description, list price 0 (column is NOT NULL), NO dimensions, one spec (Width 500 mm),
one certificate attachment (valid until 2027-12-31) linked to it."""
import sys, uuid
from datetime import date
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, ".")
from app.database import engine

CO = "00000000-0000-0000-0000-000000000001"
db = sessionmaker(bind=engine)()
u = lambda: str(uuid.uuid4())

if db.execute(text("SELECT 1 FROM products WHERE product_code='SRTWC286'")).scalar():
    print("already seeded"); sys.exit(0)

cat = db.execute(text("SELECT id FROM product_categories WHERE category_code='SRT-WC'")).scalar()
uom = db.execute(text("SELECT id FROM units_of_measure WHERE uom_code='PCS'")).scalar()
if uom is None:
    uom = u()
    db.execute(text("INSERT INTO units_of_measure (id, uom_code, uom_name) VALUES (:i,'PCS','Pieces')"), {"i": uom})
pid = u()
db.execute(text(
    "INSERT INTO products (id, product_code, product_name, description, category_id, base_uom_id, "
    "list_price, is_active, is_discontinued, currency, company_id, created_at, updated_at) "
    "VALUES (:id,'SRTWC286','Wall Hung Basin','Basin that has no hole',:cat,:uom,0,true,false,'MYR',:co,now(),now())"),
    {"id": pid, "cat": cat, "uom": uom, "co": CO})

# one populated spec
rid = db.execute(text("SELECT id FROM product_spec_registry WHERE spec_key='width'")).scalar()
if rid is None:
    db.execute(text("INSERT INTO product_spec_registry (id, spec_key, label, data_type, unit) "
                    "VALUES (:i,'width','Width','numeric','mm')"), {"i": u()})
db.execute(text("INSERT INTO product_specifications (id, product_id, values, provenance, status) "
                "VALUES (:i,:p, CAST(:v AS jsonb), CAST(:pv AS jsonb), 'verified')"),
           {"i": u(), "p": pid, "v": '{"width": {"value": 500, "unit": "mm"}}',
            "pv": '{"width": {"source": "human", "confidence": 1}}'})

# a certificate attachment linked to the product
atid = db.execute(text("SELECT id FROM attachment_types WHERE type_name='Certificate'")).scalar()
if atid is None:
    atid = u()
    db.execute(text("INSERT INTO attachment_types (id, code, type_name, allowed_extensions, max_file_size_mb, is_direct_access) "
                    "VALUES (:i,'CERT','Certificate','pdf',10,true)"), {"i": atid})
aid = u()
db.execute(text(
    "INSERT INTO attachments (id, attachment_type_id, original_filename, stored_filename, file_path, mime_type, "
    "entity_type, entity_id, company_id, valid_until, is_deleted, uploaded_at, created_at) "
    "VALUES (:id,:t,'SRTWC286-cert.pdf','SRTWC286-cert.pdf','http://localhost:8000/__sandbox_files/SRTWC286-cert.pdf',"
    "'application/pdf','product',:p,:co,'2027-12-31',false,now(),now())"),
    {"id": aid, "t": atid, "p": pid, "co": CO})
db.execute(text("INSERT INTO product_attachments (id, product_id, attachment_id, company_id, created_at) "
                "VALUES (:i,:p,:a,:co,now())"), {"i": u(), "p": pid, "a": aid, "co": CO})
cid, rev = u(), u()
db.execute(text("INSERT INTO certificates (id, attachment_type_id, scheme, certificate_number, status, company_id, created_at) "
                "VALUES (:i,:t,'WaterMark','WM-286-2026','active',:co,now())"), {"i": cid, "t": atid, "co": CO})
db.execute(text("INSERT INTO certificate_revisions (id, certificate_id, revision_no, attachment_id, valid_until, is_current, source, created_at) "
                "VALUES (:i,:c,1,:a,'2027-12-31',true,'manual',now())"), {"i": rev, "c": cid, "a": aid})
db.execute(text("UPDATE certificates SET current_revision_id=:r WHERE id=:c"), {"r": rev, "c": cid})
db.execute(text("INSERT INTO certificate_products (id, certificate_id, product_id, source, created_at) "
                "VALUES (:i,:c,:p,'manual',now())"), {"i": u(), "c": cid, "p": pid})
db.commit(); print("seeded SRTWC286")
