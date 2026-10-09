"""CA-2 taxonomy: the "Top asks" report (tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca2-taxonomy-09oct.md, "Mock v1 APPROVED
... build contract", section "Top asks"; UAC U9-U16 (documentation/plans/chatbot/
ca2-taxonomy-acceptance-criteria.md). Key `chat_top_asks`, permission `chatbot.analytics.view`,
module `chatbot`.

Names the plan leaves open and these tests pin (the coder uses them, or asks the captain):
* params: `period`, `contact_type`, `outcome`, `subject` (multi; values are subject codes),
  `count_by` (`topics` default / `products`), `products` (`asked` default / `shown`).
* ask dataset dimensions `subject`, `topic`, `language`, `month`; measure `asks`.
* the products grain keeps the measure key `asks` (item rows after the role filter).
* the note reads `Topics labelled: <n>% of asks (taxonomy v<k>).` with <n> a whole percent.

Every row is seeded here on a blank Postgres schema. Fake names only.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tests._pg_fixture import blank_session

UTC = timezone.utc
KEY = "chat_top_asks"
PERMISSION = "chatbot.analytics.view"
SEPTEMBER = {"kind": "custom", "from": "2026-09-01", "to": "2026-10-31"}
NOT_LABELLED = "(not labelled yet)"
SORENTO = "00000000-0000-0000-0000-000000000001"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _uid() -> str:
    return str(uuid.uuid4())


def _dec(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


def _definition():
    import app.services.reports  # noqa: F401
    from app.services.reports import registry as reg

    definition = reg.get(KEY)
    assert definition is not None, "the chat_top_asks report is not registered by app.services.reports"
    return definition


@pytest.fixture
def db():
    with blank_session() as session:
        from app.models.base import set_company_scope

        set_company_scope(session, None)
        yield session


# --------------------------------------------------------------------------- seeding


def _seed_taxonomy(db):
    from app.models.chat_facts import ChatTopic

    for order, (code, label) in enumerate(
        (("inventory", "Stock"), ("order", "Orders"), ("general", "General chat"))
    ):
        db.add(ChatTopic(code=code, label=label, definition=label, version=1, sort_order=order))
    db.flush()
    for order, (code, parent, label) in enumerate(
        (
            ("inventory.availability", "inventory", "Availability"),
            ("inventory.other", "inventory", "Other stock"),
            ("order.status", "order", "Order status"),
            ("general.noise", "general", "Noise"),
        )
    ):
        db.add(ChatTopic(code=code, parent_code=parent, label=label, definition=label, version=2, sort_order=order))
    db.flush()


def _fact(db, *, when: datetime, domain=None, topic=None, version=None, language=None,
          outcome="answered", contact_type="dealer", audience="dealer", customer_id=None, **extra):
    from app.models.chat_facts import ChatAskFact

    row = ChatAskFact(
        id=_uid(), source="trace", turn_id=_uid(), ask_seq=1,
        contact_respond_id=f"ZZT-ta-{_uid()[:8]}", audience=audience, contact_type=contact_type,
        asked_at=when, channel="whatsapp", outcome=outcome, is_media=False, phantom_dropped=False,
        domain=domain, topic_code=topic, taxonomy_version=version, language=language,
        customer_id=customer_id, **{"tools": [], **extra},
    )
    db.add(row)
    db.flush()
    return row


def _day(month: int, day: int, hour: int = 3) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=UTC)


def _seed_asks(db):
    """3 availability (2 Sep, 1 Oct), 1 order status, 1 other stock, 2 unlabelled; 1 noise."""
    _seed_taxonomy(db)
    _fact(db, when=_day(9, 8), domain="inventory", topic="inventory.availability", version=2, language="en")
    _fact(db, when=_day(9, 9), domain="inventory", topic="inventory.availability", version=2, language="ms")
    _fact(db, when=_day(10, 2), domain="inventory", topic="inventory.availability", version=2, language="en")
    _fact(db, when=_day(9, 10), domain="order", topic="order.status", version=2, language="en")
    _fact(db, when=_day(10, 3), domain="inventory", topic="inventory.other", version=2, language="zh")
    _fact(db, when=_day(9, 11), domain="inventory")  # unlabelled
    _fact(db, when=_day(10, 4), domain="order")  # unlabelled
    _fact(db, when=_day(9, 12), domain=None, topic="general.noise", version=2, language="en", outcome="noise")


def _run(db, params=None, view=None):
    from app.schemas.report import ReportViewConfig
    from app.services.reports import engine

    merged = {"period": SEPTEMBER}
    merged.update(params or {})
    return engine.run(db, _definition(), merged, ReportViewConfig.model_validate(view) if view else None)


def _row_asks(summary) -> dict:
    labels = summary.row_value_labels or {}
    return {labels.get(v, v): _dec(t.get("asks")) for v, t in summary.row_totals.items()}


# --------------------------------------------------------------------------- definition


def test_the_definition_is_registered_with_its_contract(db):
    definition = _definition()
    assert definition.key == KEY
    assert definition.permission == PERMISSION
    assert definition.module_key == "chatbot"
    assert definition.dataset.scope == "none"
    assert all(d.scope == "none" for d in definition.datasets())


def test_the_permission_is_in_the_registry():
    from app.rbac.permission_registry import PERMISSION_REGISTRY

    assert any(p["slug"] == PERMISSION for p in PERMISSION_REGISTRY)


def test_the_default_view_is_topic_by_month_counting_asks(db):
    view = _definition().default_view
    assert view["pivot"] == {"rows": "topic", "cols": "month", "measures": ["asks"]}
    for key in ("asked_at", "subject", "topic", "language", "outcome", "customer"):
        assert key in view["detail"]["columns"], key
    assert _definition().pivot.chart == "bar"


def test_the_params_and_their_defaults(db):
    definition = _definition()
    by_key = {p.key: p for p in definition.params}
    assert {"period", "contact_type", "outcome", "subject", "count_by", "products"} <= set(by_key)
    assert by_key["period"].resolved_default()["from"] == "2026-09-05"
    assert tuple(by_key["count_by"].default) == ("topics",)
    assert {v for v, _l in by_key["count_by"].options(db)} == {"topics", "products"}
    assert tuple(by_key["products"].default) == ("asked",)
    assert {v for v, _l in by_key["products"].options(db)} == {"asked", "shown"}
    assert by_key["subject"].multi is True
    assert set(by_key["contact_type"].default) == {"dealer", "not_allowed", "unknown"}
    assert "noise" not in set(by_key["outcome"].default)


def test_the_subject_options_are_the_subject_rows_of_the_taxonomy(db):
    _seed_taxonomy(db)
    param = next(p for p in _definition().params if p.key == "subject")
    assert dict(param.options(db)) == {"inventory": "Stock", "order": "Orders", "general": "General chat"}


def test_the_dataset_follows_count_by(db):
    definition = _definition()
    assert definition.datasets_by is not None and definition.datasets_by[0] == "count_by"
    assert definition.dataset_for({"count_by": ["topics"]}) is not definition.dataset_for({"count_by": ["products"]})


def test_the_catalog_carries_the_new_dimensions_and_no_free_text(db):
    keys = {c.key for c in _definition().catalog()}
    assert {"subject", "topic", "language", "month", "asks"} <= keys
    for key in keys:
        for fragment in ("message", "text", "summary", "phone", "last_user"):
            assert fragment not in key.lower(), key


# --------------------------------------------------------------------------- topics grain


def test_topic_by_month_counts_asks_under_the_taxonomy_label(db):
    _seed_asks(db)

    summary = _run(db).layouts.summary

    assert _row_asks(summary) == {
        "Availability": 3,
        "Order status": 1,
        "Other stock": 1,
        NOT_LABELLED: 2,
    }
    assert _dec(summary.grand_total["asks"]) == 7, "noise is out by default"
    assert set(summary.col_dim.values) == {"2026-09", "2026-10"}


def test_the_month_columns_split_the_counts(db):
    _seed_asks(db)

    summary = _run(db).layouts.summary

    availability = next(v for v in summary.row_values if (summary.row_value_labels or {}).get(v, v) == "Availability")
    by_month = {m: _dec(c["asks"]) for m, c in summary.cells[availability].items()}
    assert by_month == {"2026-09": 2, "2026-10": 1}


def test_an_unlabelled_ask_is_counted_never_dropped(db):
    _seed_taxonomy(db)
    _fact(db, when=_day(9, 8), domain="inventory")

    summary = _run(db).layouts.summary

    assert _row_asks(summary) == {NOT_LABELLED: 1}


def test_the_subject_filter_narrows_the_counts(db):
    _seed_asks(db)

    inventory = _run(db, {"subject": ["inventory"]}).layouts.summary

    assert _row_asks(inventory) == {"Availability": 3, "Other stock": 1, NOT_LABELLED: 1}
    both = _run(db, {"subject": ["inventory", "order"]}).layouts.summary
    assert _dec(both.grand_total["asks"]) == 7


def test_a_null_domain_is_the_general_subject(db):
    _seed_asks(db)

    general = _run(db, {"subject": ["general"], "outcome": ["noise"]}).layouts.summary

    assert _row_asks(general) == {"Noise": 1}


def test_the_subject_dimension_prints_subject_words(db):
    _seed_asks(db)
    view = {
        "params": {}, "detail": {"columns": [], "order": []},
        "pivot": {"rows": "subject", "cols": "month", "measures": ["asks"]},
    }

    summary = _run(db, None, view).layouts.summary

    assert _row_asks(summary) == {"Stock": 5, "Orders": 2}


def test_the_language_dimension_is_available(db):
    _seed_asks(db)
    view = {
        "params": {}, "detail": {"columns": [], "order": []},
        "pivot": {"rows": "language", "cols": "month", "measures": ["asks"]},
    }

    summary = _run(db, None, view).layouts.summary

    printed = _row_asks(summary)
    assert printed["en"] == 3 and printed["ms"] == 1 and printed["zh"] == 1


def test_the_contact_type_and_outcome_filters_still_apply(db):
    _seed_asks(db)
    _fact(db, when=_day(9, 13), domain="inventory", topic="inventory.availability", version=2,
          contact_type="office", audience="staff")

    default = _run(db).layouts.summary
    office = _run(db, {"contact_type": ["office"]}).layouts.summary
    technical = _run(db, {"outcome": ["technical"]}).layouts.summary

    assert _dec(default.grand_total["asks"]) == 7
    assert _row_asks(office) == {"Availability": 1}
    assert technical.row_totals == {}


def test_the_note_states_the_labelled_share_and_the_taxonomy_version(db):
    _seed_asks(db)

    note = _run(db).note

    # 5 of the 7 counted asks are labelled: 71%.
    assert "Topics labelled: 71% of asks (taxonomy v2)." in note
    assert "5 Sep 2026" in note, "the coverage line stays"


def test_the_note_on_an_empty_table_does_not_divide_by_zero(db):
    note = _run(db).note
    assert "Topics labelled: 0% of asks" in note


def test_the_detail_reads_words_and_the_customer_name_never_an_id_or_the_message(db):
    from app.models.order import Customer

    _seed_taxonomy(db)
    customer = Customer(id=_uid(), customer_code=f"ZZTC{_uid()[:6]}", customer_name="ZZT Hardware Sdn Bhd", company_id=SORENTO)
    db.add(customer)
    db.flush()
    _fact(db, when=_day(9, 8), domain="inventory", topic="inventory.availability", version=2,
          language="en", customer_id=customer.id)

    detail = _run(db).layouts.detail

    assert {"asked_at", "subject", "topic", "language", "outcome", "customer"} <= {c.key for c in detail.columns}
    row = detail.rows[0]
    assert row["subject"] == "Stock"
    assert row["topic"] == "Availability"
    assert row["language"] == "en"
    assert row["outcome"] == "Answered"
    assert row["customer"] == "ZZT Hardware Sdn Bhd"
    for value in row.values():
        assert not UUID_RE.search(str(value)), f"a UUID reached the detail row: {value!r}"


# --------------------------------------------------------------------------- products grain


def _seed_catalogue(db):
    from app.models.product import Product, ProductCategory, UnitOfMeasure

    category, uom = _uid(), _uid()
    db.add(ProductCategory(id=category, category_code=f"ZZTCAT{_uid()[:6]}", category_name="ZZT", company_id=SORENTO))
    db.add(UnitOfMeasure(id=uom, uom_code=f"ZZTU{_uid()[:6]}", uom_name="ZZT", company_id=SORENTO))
    db.flush()
    out = {}
    for code in ("ZZTTA-A", "ZZTTA-B"):
        row = Product(id=_uid(), product_code=code, product_name=f"ZZT {code}", category_id=category,
                      base_uom_id=uom, list_price=10, is_active=True, company_id=SORENTO)
        db.add(row)
        out[code] = row.id
    db.flush()
    return out


def _item(db, fact, ref_id: str, role: str):
    from app.models.chat_facts import ChatAskItem

    db.add(ChatAskItem(id=_uid(), ask_id=fact.id, kind="product", ref_id=ref_id, role=role,
                       resolved_by="parser" if role == "named" else "reply"))
    db.flush()


PRODUCT_VIEW = {
    "params": {}, "detail": {"columns": [], "order": []},
    "pivot": {"rows": "product", "cols": "month", "measures": ["asks"]},
}


def _seed_items(db):
    p = _seed_catalogue(db)
    a, b = p["ZZTTA-A"], p["ZZTTA-B"]
    f1 = _fact(db, when=_day(9, 8), domain="inventory")
    _item(db, f1, a, "named")
    _item(db, f1, b, "shown")
    f2 = _fact(db, when=_day(9, 9), domain="inventory")
    _item(db, f2, a, "asked")
    _item(db, f2, a, "shown")
    f3 = _fact(db, when=_day(10, 2), domain="inventory")
    _item(db, f3, b, "shown")
    # named AND asked for one product in one ask must count once.
    f4 = _fact(db, when=_day(10, 3), domain="inventory")
    _item(db, f4, b, "named")
    _item(db, f4, b, "asked")
    return p


def test_products_asked_counts_named_and_asked_roles_once_per_ask_and_product(db):
    _seed_items(db)

    summary = _run(db, {"count_by": ["products"], "products": ["asked"]}, PRODUCT_VIEW).layouts.summary

    assert _row_asks(summary) == {"ZZTTA-A": 2, "ZZTTA-B": 1}


def test_products_shown_counts_the_shown_role_only(db):
    _seed_items(db)

    summary = _run(db, {"count_by": ["products"], "products": ["shown"]}, PRODUCT_VIEW).layouts.summary

    assert _row_asks(summary) == {"ZZTTA-A": 1, "ZZTTA-B": 2}


def test_products_default_to_asked(db):
    _seed_items(db)

    default = _run(db, {"count_by": ["products"]}, PRODUCT_VIEW).layouts.summary
    asked = _run(db, {"count_by": ["products"], "products": ["asked"]}, PRODUCT_VIEW).layouts.summary

    assert _row_asks(default) == _row_asks(asked)


def test_the_products_grain_splits_by_month_and_honours_the_subject_filter(db):
    products = _seed_items(db)
    other = _fact(db, when=_day(9, 14), domain="order")
    _item(db, other, products["ZZTTA-A"], "named")

    inventory = _run(db, {"count_by": ["products"], "subject": ["inventory"]}, PRODUCT_VIEW).layouts.summary

    assert set(_row_asks(inventory)) == {"ZZTTA-A", "ZZTTA-B"}
    assert _dec(inventory.grand_total["asks"]) == 3
    a_cells = next(c for v, c in inventory.cells.items() if (inventory.row_value_labels or {}).get(v, v) == "ZZTTA-A")
    assert set(a_cells) == {"2026-09"}


def test_the_products_grain_prints_codes_not_ids(db):
    ids = _seed_items(db)
    summary = _run(db, {"count_by": ["products"]}, PRODUCT_VIEW).layouts.summary
    printed = {(summary.row_value_labels or {}).get(v, v) for v in summary.row_values}
    assert not printed & set(ids.values())


# --------------------------------------------------------------------------- routes


@pytest.fixture
def api(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.models.user import User
    from app.services.user_service import UserPermissionService

    me = {"id": _uid(), "email": "top-asks@zzt.test", "name": "Top Asks Caller"}
    db.add(User(id=me["id"], email=me["email"], name=me["name"], status="ACTIVE"))
    db.flush()
    allow = {PERMISSION}

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: me
    app.dependency_overrides[get_current_user_or_api_key] = lambda: me
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in allow)
    try:
        yield TestClient(app), allow
    finally:
        for dep in (get_db, get_current_user, get_current_user_or_api_key):
            app.dependency_overrides.pop(dep, None)


def test_the_catalog_lists_top_asks_for_a_holder_and_hides_it_otherwise(api):
    client, allow = api
    entry = next((r for r in client.get("/api/v1/reports").json()["reports"] if r["key"] == KEY), None)
    assert entry == {"key": KEY, "title": "Top asks", "permission": PERMISSION}
    allow.clear()
    allow.add("system.chat_history.view")
    assert [r for r in client.get("/api/v1/reports").json()["reports"] if r["key"] == KEY] == []
    assert client.get(f"/api/v1/reports/{KEY}").status_code == 403


def test_run_is_403_without_the_permission_and_returns_the_pivot_with_it(api, db):
    client, allow = api
    _seed_asks(db)
    body = {"params": {"period": SEPTEMBER}, "view": None}
    ok = client.post(f"/api/v1/reports/{KEY}/run", json=body)
    assert ok.status_code == 200, ok.text
    assert _dec(ok.json()["layouts"]["summary"]["grand_total"]["asks"]) == 7
    allow.clear()
    assert client.post(f"/api/v1/reports/{KEY}/run", json=body).status_code == 403


# --------------------------------------------------------------------------- round 2: Count by picks the pivot rows


def test_the_definition_names_the_pivot_rows_per_count_by(db):
    assert _definition().pivot_rows_by == {"topics": "topic", "products": "product"}


def test_meta_carries_pivot_rows_by_for_top_asks_and_null_for_chat_overview(api):
    client, _allow = api
    top = client.get(f"/api/v1/reports/{KEY}")
    assert top.status_code == 200, top.text
    assert top.json()["pivot_rows_by"] == {"topics": "topic", "products": "product"}
    assert top.json()["dataset_param"] == "count_by"

    overview = client.get("/api/v1/reports/chat_overview")
    assert overview.status_code == 200, overview.text
    assert "pivot_rows_by" in overview.json(), "the field is always present"
    assert overview.json()["pivot_rows_by"] is None


def test_the_summary_title_is_neutral_for_topic_and_product_rows(db):
    assert _definition().pivot.title == "Asks by month"
    _seed_items(db)

    topics = _run(db, {"count_by": ["topics"]})
    products = _run(db, {"count_by": ["products"]}, PRODUCT_VIEW)

    assert topics.layouts.summary.title == "Asks by month"
    assert products.layouts.summary.title == "Asks by month"
