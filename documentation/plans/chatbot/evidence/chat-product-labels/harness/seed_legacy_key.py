"""Sandbox: seed the legacy shared EXTERNAL_API_KEY (from .env.browse) as an integration key,
as the deploy migration does in prod, so the company-scope resolver (which compares the
header with settings.external_api_key) recognises the MCP server's key."""
import sys; sys.path.insert(0, ".")
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.database import engine
from app.models.integration import Integration, IntegrationApiKey
from app.services.integration_key_service import hash_api_key, key_prefix
db = sessionmaker(bind=engine)()
integ = db.query(Integration).filter(Integration.name.like("cloud-mcp-%")).first()
k = settings.external_api_key
if not db.query(IntegrationApiKey).filter(IntegrationApiKey.key_hash == hash_api_key(k)).first():
    db.add(IntegrationApiKey(integration_id=integ.id, key_hash=hash_api_key(k), key_prefix=key_prefix(k)))
    db.commit()
print("ok")
