"""Builders shared by the CA-1 chat-facts tests (not itself a test file).

CA-1 plan: documentation/plans/chatbot/PLAN-chat-analytics-ca1-facts-09oct.md.

Two groups:

* **Trace builders** (pure): the stage records and kind events a closed turn's `trace`
  holds, in the shapes the engine writes (stage records carry `stage`/`status`/`facts`/
  `raw` and NO `kind`; events carry `kind` and `at`).
* **Seeders** (Postgres, the `tests/chatbot/conftest.py::session_factory` scratch schema):
  a contact with or without a customer link or a staff user, a product, a brand, and a
  `chatbot.turns` row. Everything is seeded by the test; fake codes only (prefix ZZT).

The CA-1 modules do not exist when these tests are written, so every import of them is
LAZY (a function call inside a test body): the red is then a per-test
`ModuleNotFoundError`, and one missing module never aborts collection of the rest of the
suite. The one eager import below only registers the new tables on `Base.metadata` when
the model file exists, because `tests/_pg_fixture.blank_schema_engine` emits the scratch
schema once per process from whatever is registered at that moment.
"""
from __future__ import annotations

import importlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

try:  # pragma: no cover - red phase: the file does not exist yet
    import app.models.chat_facts  # noqa: F401
except ModuleNotFoundError:
    pass

SORENTO = "00000000-0000-0000-0000-000000000001"

#: Customer words that must never reach a fact row, an item, or an episode row.
CUSTOMER_WORDS = "ZZT-SECRET-CUSTOMER-WORDS please send the secret price list"
REPLY_WORDS = "ZZT-SECRET-REPLY-WORDS here is the list"


# --------------------------------------------------------------------------- lazy modules


def outcome_mod():
    return importlib.import_module("app.services.chatbot.analytics.outcome")


def facts_mod():
    return importlib.import_module("app.services.chatbot.analytics.facts")


def models_mod():
    return importlib.import_module("app.models.chat_facts")


# --------------------------------------------------------------------------- trace builders


def stage(name: str, *, status: str = "ok", facts: dict | None = None, raw: dict | None = None) -> dict:
    return {"stage": name, "status": status, "ms": 5, "facts": facts or {}, "raw": raw or {}}


def event(kind: str, **payload: Any) -> dict:
    return {"kind": kind, "at": "2026-09-10T02:00:00+00:00", **payload}


def apply_event(
    *,
    domain_hint: str | None = None,
    intent_hint: str | None = None,
    entities: list[dict] | None = None,
    topic_reset: bool = False,
    domains: list[str] | None = None,
    fetch: list | None = None,
    denied: list | None = None,
    ask: Any = None,
    lane: str | None = None,
) -> dict:
    return event(
        "apply",
        verdict={
            "domain_hint": domain_hint,
            "intent_hint": intent_hint,
            "entities": entities or [],
            "topic_reset": topic_reset,
        },
        plan={
            "domains": domains or [],
            "fetch": fetch or [],
            "denied": denied or [],
            "ask": ask,
            "lane": lane,
        },
    )


def entity_dict(hint: str | None, canonical_code: str | None, raw: str | None = None) -> dict:
    return {"hint": hint, "raw": raw if raw is not None else canonical_code, "canonical_code": canonical_code}


def looked_up(*, sections: int | None = 1, missed: list | None = None, domains: list | None = None) -> dict:
    facts: dict[str, Any] = {"domains": domains or [], "missed": missed or []}
    if sections is not None:
        facts["sections"] = sections
    return stage("looked_up", facts=facts)


def replied(
    *,
    rows_offered: int | None = None,
    result_set: list[dict] | None = None,
    lane: str = "business",
    reply_text: str = REPLY_WORDS,
) -> dict:
    facts: dict[str, Any] = {"lane": lane, "quick_replies": 0}
    if rows_offered is not None:
        facts["rows_offered"] = rows_offered
    return stage(
        "replied",
        facts=facts,
        raw={
            "reply": {
                "text": reply_text,
                "quick_replies": [],
                "result_set": result_set or [],
                "attachments_src": None,
            }
        },
    )


def turn_dict(*records: dict, status: str = "done", branch_kind: str | None = None, **extra: Any) -> dict:
    """The `memory._turn_to_digest_dict` shape plus `status` (what `turn_outcome` takes)."""
    return {
        "id": str(uuid.uuid4()),
        "created_at": datetime(2026, 9, 10, 2, 0, tzinfo=timezone.utc),
        "branch_kind": branch_kind,
        "status": status,
        "message": CUSTOMER_WORDS,
        "trace": list(records),
        "result_refs": [],
        **extra,
    }


def answered_trace(
    *,
    domain: str = "inventory",
    intent: str = "stock",
    named: tuple[tuple[str, str], ...] = (),
    shown: tuple[tuple[str, str], ...] = (),
    rows: int | None = 3,
    tools: tuple[str, ...] = ("crm_stock",),
) -> list[dict]:
    """A normal answered business turn: apply, tool events, looked_up, replied."""
    entities = [entity_dict(hint, code) for hint, code in named]
    records: list[dict] = [
        apply_event(
            domain_hint=domain,
            intent_hint=intent,
            entities=entities,
            domains=[domain],
            fetch=[domain],
            lane="business",
        )
    ]
    records += [event("tool", name=name, args={}, envelope={}, ms=4) for name in tools]
    records.append(looked_up(sections=1, domains=[domain]))
    records.append(
        replied(
            rows_offered=rows,
            result_set=[
                {"idx": i + 1, "label": f"ZZT label {i}", "code": code, "entity_type": kind}
                for i, (kind, code) in enumerate(shown)
            ],
        )
    )
    return records


# --------------------------------------------------------------------------- seeders


def scoped_all(db):
    """A session allowed to write company-owned rows (the seeding side only)."""
    from app.models.base import set_company_scope

    set_company_scope(db, None)
    return db


def seed_contact(
    session_factory,
    respond_id: str,
    *,
    customer: bool = False,
    staff: bool = False,
    extra_nonprimary_customer: bool = False,
) -> dict[str, Any]:
    """A `respond_contacts` row (respond_io_id = `respond_id`), optionally linked to a
    primary customer and/or a staff user. Returns the ids the assertions need."""
    from app.models.access import RespondContactCustomer
    from app.models.order import Customer
    from app.models.user import User

    db = scoped_all(session_factory())
    contact_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (:id, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {
            "id": contact_id,
            "cid": respond_id,
            "phone": f"+6019{uuid.uuid4().hex[:8]}",
            "sv": json.dumps({"variables": {}}),
        },
    )
    out: dict[str, Any] = {"contact_id": contact_id, "customer_id": None, "user_id": None}
    if customer:
        if extra_nonprimary_customer:
            other = Customer(
                id=str(uuid.uuid4()),
                customer_code=f"ZZT-C-{uuid.uuid4().hex[:8]}",
                customer_name="ZZT Other Account",
                company_id=SORENTO,
            )
            db.add(other)
            db.flush()
            db.add(
                RespondContactCustomer(
                    id=str(uuid.uuid4()),
                    contact_id=contact_id,
                    customer_id=other.id,
                    is_primary=False,
                    company_id=SORENTO,
                )
            )
        primary = Customer(
            id=str(uuid.uuid4()),
            customer_code=f"ZZT-C-{uuid.uuid4().hex[:8]}",
            customer_name="ZZT Primary Account",
            company_id=SORENTO,
        )
        db.add(primary)
        db.flush()
        db.add(
            RespondContactCustomer(
                id=str(uuid.uuid4()),
                contact_id=contact_id,
                customer_id=primary.id,
                is_primary=True,
                company_id=SORENTO,
            )
        )
        out["customer_id"] = primary.id
    if staff:
        user_id = str(uuid.uuid4())
        db.add(
            User(
                id=user_id,
                email=f"zzt-{user_id[:8]}@zzt.test",
                name="ZZT Staff",
                status="ACTIVE",
                respond_contact_id=contact_id,
            )
        )
        out["user_id"] = user_id
    db.commit()
    return out


def seed_catalogue(session_factory, *, product_codes: tuple[str, ...] = (), brand_codes: tuple[str, ...] = ()) -> dict:
    """Real products (own category and uom) and brands. Returns {code: id} per kind."""
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    db = scoped_all(session_factory())
    category_id, uom_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(
        ProductCategory(
            id=category_id, category_code=f"ZZTCAT{uuid.uuid4().hex[:6]}", category_name="ZZT Cat",
            company_id=SORENTO,
        )
    )
    db.add(
        UnitOfMeasure(
            id=uom_id, uom_code=f"ZZTU{uuid.uuid4().hex[:6]}", uom_name="ZZT Each", company_id=SORENTO
        )
    )
    db.flush()
    products: dict[str, str] = {}
    brands: dict[str, str] = {}
    for code in brand_codes:
        brand = Brand(
            id=str(uuid.uuid4()), brand_code=code, brand_name=f"ZZT Brand {code}", company_id=SORENTO
        )
        db.add(brand)
        brands[code] = brand.id
    db.flush()
    for code in product_codes:
        product = Product(
            id=str(uuid.uuid4()),
            product_code=code,
            product_name=f"ZZT {code}",
            category_id=category_id,
            base_uom_id=uom_id,
            list_price=10,
            is_active=True,
            company_id=SORENTO,
        )
        db.add(product)
        products[code] = product.id
    db.commit()
    return {"products": products, "brands": brands}


def envelope_with_text(words: str = CUSTOMER_WORDS) -> dict:
    return {
        "contact": {"id": "ZZT"},
        "message": {"message": {"message": {"type": "text", "text": words}}},
    }


def seed_turn(
    session_factory,
    respond_id: str,
    *,
    records: list[dict] | None = None,
    status: str = "done",
    branch_kind: str | None = None,
    created_at: datetime | None = None,
    reply_ms: int | None = 1500,
    message_id: str | None = None,
    attempt: int = 1,
    ingress: str = "webhook",
    is_test: bool = False,
    stage_name: str | None = None,
) -> str:
    """One `chatbot.turns` row. `status="processing"` leaves it open (no finished_at)."""
    from app.models.chatbot_turn import ChatbotTurn

    created = created_at or datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc)
    closed = status in ("done", "failed")
    turn_id = str(uuid.uuid4())
    db = session_factory()
    db.add(
        ChatbotTurn(
            id=turn_id,
            contact_respond_id=respond_id,
            message_id=message_id or f"ZZT-msg-{uuid.uuid4().hex[:10]}",
            ingress=ingress,
            envelope=envelope_with_text(),
            is_test=is_test,
            status=status,
            stage=stage_name,
            branch_kind=branch_kind,
            attempt=attempt,
            trace=records if records is not None else answered_trace(),
            response={"reply": {"text": REPLY_WORDS}} if closed else None,
            created_at=created,
            started_at=created,
            finished_at=(created + timedelta(milliseconds=reply_ms)) if closed and reply_ms is not None else None,
        )
    )
    db.commit()
    return turn_id


def fact_rows(session_factory, **filters: Any) -> list[Any]:
    models = models_mod()
    q = session_factory().query(models.ChatAskFact)
    for key, value in filters.items():
        q = q.filter(getattr(models.ChatAskFact, key) == value)
    return q.order_by(models.ChatAskFact.asked_at.asc()).all()


def item_rows(session_factory, ask_id: str) -> list[Any]:
    models = models_mod()
    return (
        session_factory()
        .query(models.ChatAskItem)
        .filter(models.ChatAskItem.ask_id == ask_id)
        .all()
    )


def episode_rows(session_factory, **filters: Any) -> list[Any]:
    models = models_mod()
    q = session_factory().query(models.ChatEpisodeFact)
    for key, value in filters.items():
        q = q.filter(getattr(models.ChatEpisodeFact, key) == value)
    return q.all()


def register_company_scope() -> None:
    """The scope listeners are process-global; some suites run without them. Installing
    them makes the record/sweep tests prove the writer works on a session that has NO
    scope set (a script, a nightly job), which is fail-closed for company-owned tables."""
    from app.services.company_scope import register_company_scope_listeners

    register_company_scope_listeners()
