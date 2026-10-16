"""Sandbox: seed the label catalog into translation_memory the way a live (non dry-run)
first ms/zh turn does, because console turns are dry runs and seed nothing."""
import sys; sys.path.insert(0, ".")
from app.database import SessionLocal
from app.services.chatbot import label_catalog
db = SessionLocal()
for lang in ("ms", "zh"):
    label_catalog.resolve(db, lang, dry_run=False)
print("done")
