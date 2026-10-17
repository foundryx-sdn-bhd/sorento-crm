"""Run in a FRESH process by test_replay_scope.py (never loads tests/conftest.py, which installs the listener).

Imports the replay module, runs its command setup, and prints whether a plain product query on a
company-scoped session came out with a WHERE (scoped) or without (every company)."""
import json

from sqlalchemy import event

from scripts.chatbot_eval import replay as replay_mod

replay_mod._register_scope()

from app.database import SessionLocal, engine  # noqa: E402
from app.models.base import set_company_scope  # noqa: E402
from app.models.product import Product  # noqa: E402

seen: list[str] = []


@event.listens_for(engine, "before_cursor_execute")
def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    seen.append(statement)


db = SessionLocal()
set_company_scope(db, frozenset({"00000000-0000-0000-0000-0000000000aa"}))
db.query(Product).limit(1).all()
db.rollback()
db.close()
sql = " ".join(" ".join(s.split()) for s in seen if "products" in s).lower()
print(json.dumps({"scoped": " where " in sql, "sql": sql[:300]}))
