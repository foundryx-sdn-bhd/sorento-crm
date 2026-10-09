"""CA-1 facts: the "Chat overview" report (tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca1-facts-09oct.md, "Report":
`app/services/reports/datasets/chat_asks.py` (ask grain) + `chat_ask_items.py` (item grain) +
`definitions/chat_overview.py`, key `chat_overview`, title "Chat overview", module `chatbot`,
permission `chatbot.analytics.view`, dataset scope `none`, `datasets_by` on the `count_by`
param.

Every fact is seeded here on a blank Postgres schema (CI's database is empty; the local one
is a prod copy). `app.models.chat_facts` and the definition do not exist yet, so each test
fails with `ModuleNotFoundError` or "not registered".

NAMES THE PLAN LEAVES OPEN AND THESE TESTS PIN (the coder must use them, or ask the captain
to change the tests):
* params: `date_basis` (basis key `asked_at`), `period`, `contact_type` (multi select, default
  dealer + unknown, so staff are out), `count_by` (single select; `asks` default, `products`).
* ask dataset: dimensions `outcome`, `week`; measures `asks` (every ask) and `failed`
  (technical + cannot_do + missed). `week` is the Malaysia-clock Monday week.
* item dataset: dimension `product` (its label is the product code), measures `named` and
  `shown`; it also carries `outcome`, `week`, `asks`, `failed` because the engine validates
  the default view (outcome x week, asks + failed) against EVERY dataset of a report.
* the pivot formats every measure through `_money`, so a count reads "3.00"; the tests
  compare with Decimal.
* noise is excluded by default (no parameter is pinned for opting in).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tests._pg_fixture import blank_session

UTC = timezone.utc
KEY = "chat_overview"
PERMISSION = "chatbot.analytics.view"
SEPTEMBER = {"kind": "custom", "from": "2026-09-01", "to": "2026-09-30"}

_ME = {"id": str(uuid.uuid4()), "email": "chat-overview@zzt.test", "name": "Overview Caller"}


def _uid() -> str:
    return str(uuid.uuid4())


def _definition():
    import app.services.reports  # noqa: F401  registers every definition, as the routes' import does
    from app.services.reports import registry as reg

    definition = reg.get(KEY)
    assert definition is not None, "the chat_overview report is not registered by app.services.reports"
    return definition


def _dec(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


# --------------------------------------------------------------------------- seeding


def _fact(db, *, outcome: str, when: datetime, contact_type: str = "dealer", audience: str = "dealer", **extra):
    from app.models.chat_facts import ChatAskFact

    row = ChatAskFact(
        id=_uid(),
        source="trace",
        turn_id=_uid(),
        ask_seq=1,
        contact_respond_id=f"ZZT-ov-{_uid()[:8]}",
        audience=audience,
        contact_type=contact_type,
        asked_at=when,
        channel="whatsapp",
        outcome=outcome,
        is_media=False,
        phantom_dropped=False,
        **{"tools": [], **extra},
    )
    db.add(row)
    db.flush()
    return row


def _day(month: int, day: int, hour: int = 3) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=UTC)


def _seed_overview(db):
    """Week 1 = Mon 7 Sep, week 2 = Mon 14 Sep (Malaysia clock)."""
    # week 1
    _fact(db, outcome="answered", when=_day(9, 8))
    _fact(db, outcome="answered", when=_day(9, 9), audience="unknown", contact_type="unknown")
    _fact(db, outcome="technical", when=_day(9, 10), failure_reason="technical")
    _fact(db, outcome="refused_policy", when=_day(9, 11))
    _fact(db, outcome="noise", when=_day(9, 11, 4))  # excluded: noise
    _fact(db, outcome="answered", when=_day(9, 12), audience="staff", contact_type="office")  # excluded by default: office
    # week 2
    _fact(db, outcome="cannot_do", when=_day(9, 15), failure_reason="cannot_do")
    _fact(db, outcome="missed", when=_day(9, 16), failure_reason="missed")
    _fact(db, outcome="clarified", when=_day(9, 17))
    _fact(db, outcome="answered_empty", when=_day(9, 17, 5))
    _fact(db, outcome="handover", when=_day(9, 18))
    _fact(db, outcome="answered", when=_day(9, 19))
    # Sun 13 Sep 17:00 UTC is Mon 14 Sep 01:00 in Kuala Lumpur: week 2, not week 1.
    _fact(db, outcome="answered", when=datetime(2026, 9, 13, 17, 0, tzinfo=UTC))


def _run(db, params=None, view=None):
    from app.services.reports import engine
    from app.schemas.report import ReportViewConfig

    definition = _definition()
    merged = {"period": SEPTEMBER}
    merged.update(params or {})
    return engine.run(db, definition, merged, ReportViewConfig.model_validate(view) if view else None)


@pytest.fixture
def db():
    with blank_session() as session:
        from app.models.base import set_company_scope

        set_company_scope(session, None)
        yield session


# --------------------------------------------------------------------------- the definition


def test_the_definition_is_registered_with_its_contract(db):
    definition = _definition()
    assert definition.key == KEY
    assert definition.title == "Chat overview"
    assert definition.permission == PERMISSION
    assert definition.module_key == "chatbot"


def test_every_dataset_declares_scope_none(db):
    definition = _definition()
    assert definition.dataset.scope == "none"
    assert all(d.scope == "none" for d in definition.datasets())


def test_the_dataset_follows_the_count_by_param(db):
    from app.services.reports import registry as reg

    definition = _definition()
    assert definition.datasets_by is not None and definition.datasets_by[0] == "count_by"
    assert "products" in definition.datasets_by[1]
    param = next(p for p in definition.params if p.key == "count_by")
    assert isinstance(param, reg.SelectParam) and param.multi is False
    assert {value for value, _label in param.options(db)} >= {"asks", "products"}
    assert tuple(param.default) == ("asks",)
    ask_ds = definition.dataset_for({"count_by": ["asks"]})
    item_ds = definition.dataset_for({"count_by": ["products"]})
    assert ask_ds is not item_ds


def test_the_contact_type_filter_defaults_to_everyone_but_the_office(db):
    definition = _definition()
    param = next(p for p in definition.params if p.key == "contact_type")
    assert set(param.default) == {"dealer", "not_allowed", "unknown"}
    assert {value for value, _label in param.options(db)} == {"dealer", "office", "not_allowed", "unknown"}
    assert dict(param.options(db))["not_allowed"] == "Not allowed"
    assert all(p.key != "audience" for p in definition.params), "audience is a dimension only"


def test_the_date_basis_is_when_the_customer_asked(db):
    definition = _definition()
    assert definition.dataset.basis("asked_at") is not None
    assert all(d.basis("asked_at") is not None for d in definition.datasets())


def test_no_catalog_column_can_carry_customer_words(db):
    keys = [c.key for c in _definition().catalog()]
    assert keys
    for key in keys:
        for fragment in ("message", "text", "summary", "phone", "last_user"):
            assert fragment not in key.lower(), f"catalog column {key!r} looks like free text"
    assert {"outcome", "week", "asks", "failed", "product", "named", "shown"} <= set(keys)


def test_the_permission_is_in_the_registry():
    from app.rbac.permission_registry import PERMISSION_REGISTRY

    entry = next((p for p in PERMISSION_REGISTRY if p["slug"] == PERMISSION), None)
    assert entry is not None, f"{PERMISSION} is not in PERMISSION_REGISTRY"
    assert entry["name"] and entry["description"]


# --------------------------------------------------------------------------- ask grain


def test_the_pivot_counts_asks_by_outcome_and_failed_only_the_failure_outcomes(db):
    _seed_overview(db)

    summary = _run(db).layouts.summary

    asks = {outcome: _dec(totals.get("asks")) for outcome, totals in summary.row_totals.items()}
    assert asks == {
        "Answered": 4,  # 8 Sep, 9 Sep (unknown), 19 Sep, and the 13 Sep UTC edge that is Monday in Malaysia
        "Technical failure": 1,
        "Refused by policy": 1,
        "Cannot do": 1,
        "Missed": 1,
        "Asked back": 1,
        "Answered, nothing found": 1,
        "Handed over": 1,
    }
    failed = {outcome: _dec(totals.get("failed")) for outcome, totals in summary.row_totals.items()}
    assert failed["Technical failure"] == failed["Cannot do"] == failed["Missed"] == 1
    for not_failed in ("Answered", "Refused by policy", "Asked back", "Answered, nothing found", "Handed over"):
        assert failed[not_failed] == 0, not_failed
    assert _dec(summary.grand_total["asks"]) == 11
    assert _dec(summary.grand_total["failed"]) == 3


def test_noise_is_not_counted_by_default(db):
    _seed_overview(db)
    summary = _run(db).layouts.summary
    assert "Noise" not in summary.row_values
    assert "Noise" not in summary.row_totals


def test_the_weeks_follow_the_malaysia_clock(db):
    _seed_overview(db)

    summary = _run(db).layouts.summary

    by_week = sorted(summary.cells["Answered"].items())
    assert [_dec(cell["asks"]) for _week, cell in by_week] == [2, 2], (
        "week 1 holds 8 and 9 Sep; week 2 holds 19 Sep and the 13 Sep 17:00 UTC ask (Mon 01:00 in Kuala Lumpur)"
    )


def test_staff_are_excluded_by_default_and_included_when_chosen(db):
    _seed_overview(db)

    default = _run(db).layouts.summary
    staff_only = _run(db, {"contact_type": ["office"]}).layouts.summary
    everyone = _run(db, {"contact_type": ["dealer", "office", "not_allowed", "unknown"]}).layouts.summary

    assert _dec(default.grand_total["asks"]) == 11
    assert _dec(staff_only.grand_total["asks"]) == 1
    assert _dec(everyone.grand_total["asks"]) == 12


def test_the_default_period_opens_on_the_first_day_of_the_traces(db):
    param = next(p for p in _definition().params if p.key == "period")
    assert param.resolved_default()["from"] == "2026-09-05"


def test_the_pivot_rows_and_the_detail_cells_read_words_not_codes(db):
    _fact(db, outcome="technical", when=_day(9, 10), failure_reason="technical", audience="dealer", contact_type="dealer", branch_kind="not_supported")
    _fact(db, outcome="refused_policy", when=_day(9, 11), audience="unknown", contact_type="not_allowed", branch_kind="access_denied")

    result = _run(db)

    assert set(result.layouts.summary.row_values) == {"Technical failure", "Refused by policy"}
    detail = result.layouts.detail
    keys = {c.key for c in detail.columns}
    assert {"outcome", "audience", "contact_type", "failure_reason", "branch_kind"} <= keys
    by_outcome = {row["outcome"]: row for row in detail.rows}
    technical = by_outcome["Technical failure"]
    assert technical["audience"] == "Dealers"
    assert technical["contact_type"] == "Dealer"
    assert technical["failure_reason"] == "Technical failure"
    assert technical["branch_kind"] == "Not supported"
    assert by_outcome["Refused by policy"]["audience"] == "Unknown contacts"
    assert by_outcome["Refused by policy"]["contact_type"] == "Not allowed"
    assert by_outcome["Refused by policy"]["branch_kind"] == "Access refused"


def test_off_topic_counts_as_an_ask_and_not_as_failed(db):
    _fact(db, outcome="off_topic", when=_day(9, 10))

    summary = _run(db).layouts.summary

    assert _dec(summary.row_totals["Off topic"]["asks"]) == 1
    assert _dec(summary.row_totals["Off topic"].get("failed")) == 0


def test_the_detail_carries_rows_offered_reply_seconds_and_tools(db):
    _fact(db, outcome="answered", when=_day(9, 10), rows_offered=3, reply_ms=2400)
    _fact(db, outcome="answered", when=_day(9, 11), tools=["crm_stock", "crm_price"])

    detail = _run(db).layouts.detail

    assert {"rows_offered", "reply_seconds", "tools"} <= {c.key for c in detail.columns}
    rows = detail.rows
    first = next(r for r in rows if r["rows_offered"] == 3)
    assert first["reply_seconds"] == 2
    assert next(r for r in rows if r["tools"]) ["tools"] == "crm_stock, crm_price"


def test_the_row_count_is_the_filtered_ask_count(db):
    _seed_overview(db)
    assert _run(db).row_count == 11


def test_a_period_before_the_traces_begin_returns_nothing(db):
    _seed_overview(db)

    result = _run(db, {"period": {"kind": "custom", "from": "2026-08-01", "to": "2026-09-04"}})

    assert result.row_count == 0
    assert result.layouts.summary.cells == {}
    assert result.layouts.summary.grand_total in ({}, {"asks": "0.00", "failed": "0.00"})


def test_an_empty_table_runs_to_an_empty_report(db):
    result = _run(db)
    assert result.row_count == 0 and result.layouts.summary.cells == {}


# --------------------------------------------------------------------------- item grain


def _seed_catalogue(db):
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    sorento = "00000000-0000-0000-0000-000000000001"
    category, uom = _uid(), _uid()
    db.add(ProductCategory(id=category, category_code=f"ZZTCAT{_uid()[:6]}", category_name="ZZT", company_id=sorento))
    db.add(UnitOfMeasure(id=uom, uom_code=f"ZZTU{_uid()[:6]}", uom_name="ZZT", company_id=sorento))
    db.flush()
    brand = Brand(id=_uid(), brand_code="ZZTOVBR", brand_name="ZZT Overview Brand", company_id=sorento)
    db.add(brand)
    db.flush()
    products = {}
    for code in ("ZZTOV-A", "ZZTOV-B"):
        row = Product(
            id=_uid(), product_code=code, product_name=f"ZZT {code}", category_id=category, base_uom_id=uom,
            list_price=10, is_active=True, company_id=sorento,
        )
        db.add(row)
        products[code] = row.id
    db.flush()
    return products, brand.id


def _item(db, fact, kind: str, ref_id: str, role: str):
    from app.models.chat_facts import ChatAskItem

    db.add(
        ChatAskItem(
            id=_uid(), ask_id=fact.id, kind=kind, ref_id=ref_id, role=role,
            resolved_by="parser" if role == "named" else "reply",
        )
    )
    db.flush()


PRODUCT_VIEW = {
    "params": {},
    "detail": {"columns": [], "order": []},
    "pivot": {"rows": "product", "cols": "week", "measures": ["named", "shown"]},
}


def _seed_items(db):
    products, brand_id = _seed_catalogue(db)
    a, b = products["ZZTOV-A"], products["ZZTOV-B"]
    f1 = _fact(db, outcome="answered", when=_day(9, 8))
    _item(db, f1, "product", a, "named")
    _item(db, f1, "brand", brand_id, "named")
    f2 = _fact(db, outcome="answered", when=_day(9, 9))
    _item(db, f2, "product", a, "named")
    _item(db, f2, "product", b, "shown")
    f3 = _fact(db, outcome="answered", when=_day(9, 16))
    _item(db, f3, "product", a, "shown")
    f_staff = _fact(db, outcome="answered", when=_day(9, 10), audience="staff", contact_type="office")
    _item(db, f_staff, "product", a, "named")
    f_noise = _fact(db, outcome="noise", when=_day(9, 11))
    _item(db, f_noise, "product", b, "named")
    return products


def _by_code(summary, totals_key: str = "row_totals") -> dict:
    """Row totals keyed by what the screen prints for the row."""
    labels = summary.row_value_labels or {}
    return {labels.get(value, value): totals for value, totals in getattr(summary, totals_key).items()}


def test_count_by_products_counts_named_and_shown_per_product_code(db):
    _seed_items(db)

    summary = _run(db, {"count_by": ["products"]}, PRODUCT_VIEW).layouts.summary

    totals = _by_code(summary)
    assert set(totals) == {"ZZTOV-A", "ZZTOV-B"}, "one row per product, labelled with its code, brands left out"
    a, b = totals["ZZTOV-A"], totals["ZZTOV-B"]
    assert (_dec(a.get("named")), _dec(a.get("shown"))) == (2, 1)
    assert (_dec(b.get("named")), _dec(b.get("shown"))) == (0, 1)


def test_the_item_grain_applies_the_contact_type_and_noise_filters_through_the_ask(db):
    _seed_items(db)

    default = _by_code(_run(db, {"count_by": ["products"]}, PRODUCT_VIEW).layouts.summary)
    staff = _by_code(
        _run(db, {"count_by": ["products"], "contact_type": ["office"]}, PRODUCT_VIEW).layouts.summary
    )

    # The staff ask names A once; the noise ask names B once. Neither shows by default.
    assert _dec(default["ZZTOV-A"].get("named")) == 2
    assert _dec(default["ZZTOV-B"].get("named")) == 0
    assert set(staff) == {"ZZTOV-A"} and _dec(staff["ZZTOV-A"].get("named")) == 1


def test_a_brand_is_not_a_product_row(db):
    _seed_items(db)
    summary = _run(db, {"count_by": ["products"]}, PRODUCT_VIEW).layouts.summary
    printed = {(summary.row_value_labels or {}).get(v, v) for v in summary.row_values}
    assert "ZZTOVBR" not in printed
    assert printed == {"ZZTOV-A", "ZZTOV-B"}


def test_the_item_grain_has_no_ask_id_or_uuid_on_the_screen(db):
    products = _seed_items(db)
    summary = _run(db, {"count_by": ["products"]}, PRODUCT_VIEW).layouts.summary
    printed = [(summary.row_value_labels or {}).get(v, v) for v in summary.row_values]
    assert not set(printed) & set(products.values()), "the row label is the product code, never its id"


# --------------------------------------------------------------------------- routes


@pytest.fixture
def api(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.models.user import User
    from app.services.user_service import UserPermissionService

    db.add(User(id=_ME["id"], email=_ME["email"], name=_ME["name"], status="ACTIVE"))
    db.flush()
    _definition()
    allow = {PERMISSION}

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: _ME
    app.dependency_overrides[get_current_user_or_api_key] = lambda: _ME
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in allow
    )
    client = TestClient(app)
    try:
        yield client, allow
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_or_api_key, None)


def test_the_catalog_lists_chat_overview_for_a_holder_of_the_permission(api):
    client, _allow = api
    resp = client.get("/api/v1/reports")
    assert resp.status_code == 200, resp.text
    entry = next((r for r in resp.json()["reports"] if r["key"] == KEY), None)
    assert entry == {"key": KEY, "title": "Chat overview", "permission": PERMISSION}


def test_the_catalog_hides_chat_overview_from_someone_without_the_permission(api):
    client, allow = api
    allow.clear()
    allow.add("system.chat_history.view")  # reading the trace is not the analytics grant
    resp = client.get("/api/v1/reports")
    assert resp.status_code == 200
    assert [r for r in resp.json()["reports"] if r["key"] == KEY] == []


def test_meta_and_run_are_403_without_the_permission(api):
    client, allow = api
    allow.clear()
    assert client.get(f"/api/v1/reports/{KEY}").status_code == 403
    body = {"params": {"period": SEPTEMBER}, "view": None}
    assert client.post(f"/api/v1/reports/{KEY}/run", json=body).status_code == 403


def test_export_and_view_creation_are_403_without_the_permission(api):
    client, allow = api
    allow.clear()
    allow.add("system.chat_history.view")
    body = {"params": {"period": SEPTEMBER}, "view": None}
    assert client.post(f"/api/v1/reports/{KEY}/export", json=body).status_code == 403
    view = {
        "params": {}, "detail": {"columns": [], "order": []},
        "pivot": {"rows": "outcome", "cols": "week", "measures": ["asks"]},
    }
    assert client.post(f"/api/v1/reports/{KEY}/views", json={"name": "ZZT mine", "view": view}).status_code == 403


def test_run_returns_the_pivot_to_a_holder(api, db):
    client, _allow = api
    _seed_overview(db)

    resp = client.post(f"/api/v1/reports/{KEY}/run", json={"params": {"period": SEPTEMBER}, "view": None})

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["row_count"] == 11
    assert _dec(payload["layouts"]["summary"]["grand_total"]["asks"]) == 11
    assert _dec(payload["layouts"]["summary"]["grand_total"]["failed"]) == 3


def test_the_pivot_reads_whole_units_so_counts_do_not_print_as_money(api):
    client, _allow = api
    body = {"params": {"period": SEPTEMBER}, "view": None}
    resp = client.post(f"/api/v1/reports/{KEY}/run", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["layouts"]["summary"]["whole_units"] is True
