"""#1340: the scheduled reorder run takes its scope from the config page.

UAC: documentation/plans/scm/scm-reorder-run-scheduled-scope-acceptance-criteria.md
Plan: documentation/plans/scm/PLAN-scm-reorder-run-scheduled-scope.md

Written before the implementation (tests red first). Both new symbols this file exercises -
``app.schemas.scheduled_task.ScmReorderRunTaskMetadata`` and
``app.scheduler.task_scheduler._scm_reorder_run_kwargs`` - do not exist yet, so the whole
module fails to IMPORT right now. That is deliberate: every test below is red for the same
honest reason (the feature does not exist), not a fixture bug.

Four groups:
  A/B - the pure helper ``_scm_reorder_run_kwargs`` (defaults, per-key threading, the
        relative window, and the two refusals it shares with the model).
  C   - the handler ``_handler_scm_reorder_run`` end to end, with the planning pipeline
        stubbed exactly as ``tests/scm/test_low_stock_report_ready_trigger.py`` stubs it.
  D   - the ``ScmReorderRunTaskMetadata`` model in isolation.
  E   - ``update_task`` validating the MERGED metadata for the ``scm_reorder_run`` key,
        both as a direct service call and through the PATCH route.

Postgres only, via ``tests/_pg_fixture.py`` (never sqlite). Every test seeds its own chain -
never a row this suite assumes already exists.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_or_api_key
from app.main import app
from app.models.scheduled_task import ScheduledTask
from app.schemas.scheduled_task import ScmReorderRunTaskMetadata
from app.scheduler.task_scheduler import _handler_scm_reorder_run, _scm_reorder_run_kwargs
from app.services.error_handler import AppException
from app.services.scheduled_task_service import update_task
from tests._pg_fixture import blank_session
from tests.scm.conftest import requires_pg
from tests.scm.test_product_grain_summary import db  # noqa: F401 - Postgres session fixture

pytestmark = requires_pg


# --------------------------------------------------------------------------------------- #
# Group A/B - the pure helper
# --------------------------------------------------------------------------------------- #

RUN_DAY = date(2026, 9, 28)

DEFAULT_KWARGS = {
    "warehouse_codes": [],
    "buy_scope": "warehouse",
    "include_market": False,
    "product_codes": None,
    "plan_horizon_start": None,
    "plan_horizon_date": None,
    "demand_class": None,
    "enqueue": False,
}


def test_kwargs_default_with_empty_metadata():
    assert _scm_reorder_run_kwargs({}, RUN_DAY) == DEFAULT_KWARGS


def test_kwargs_default_with_none_metadata():
    assert _scm_reorder_run_kwargs(None, RUN_DAY) == DEFAULT_KWARGS


def test_kwargs_warehouse_codes_pass_through():
    kwargs = _scm_reorder_run_kwargs({"warehouse_codes": ["WH1", "WH2"]}, RUN_DAY)
    assert kwargs["warehouse_codes"] == ["WH1", "WH2"]


@pytest.mark.parametrize("value", ["project", "retail"])
def test_kwargs_demand_class_pass_through(value):
    kwargs = _scm_reorder_run_kwargs({"demand_class": value}, RUN_DAY)
    assert kwargs["demand_class"] == value


def test_kwargs_product_codes_pass_through():
    kwargs = _scm_reorder_run_kwargs({"product_codes": ["ZZT-P1", "ZZT-P2"]}, RUN_DAY)
    assert kwargs["product_codes"] == ["ZZT-P1", "ZZT-P2"]


def test_kwargs_empty_product_codes_becomes_none():
    kwargs = _scm_reorder_run_kwargs({"product_codes": []}, RUN_DAY)
    assert kwargs["product_codes"] is None


def test_kwargs_include_market_pass_through():
    kwargs = _scm_reorder_run_kwargs({"include_market": True}, RUN_DAY)
    assert kwargs["include_market"] is True


def test_kwargs_relative_window_resolves_against_run_day():
    kwargs = _scm_reorder_run_kwargs(
        {"horizon_start_days": -7, "horizon_end_days": 30}, RUN_DAY,
    )
    assert kwargs["plan_horizon_start"] == RUN_DAY - timedelta(days=7)
    assert kwargs["plan_horizon_date"] == RUN_DAY + timedelta(days=30)


def test_kwargs_only_start_set_leaves_end_unbounded():
    kwargs = _scm_reorder_run_kwargs({"horizon_start_days": -3}, RUN_DAY)
    assert kwargs["plan_horizon_start"] == RUN_DAY - timedelta(days=3)
    assert kwargs["plan_horizon_date"] is None


def test_kwargs_only_end_set_leaves_start_unbounded():
    kwargs = _scm_reorder_run_kwargs({"horizon_end_days": 14}, RUN_DAY)
    assert kwargs["plan_horizon_start"] is None
    assert kwargs["plan_horizon_date"] == RUN_DAY + timedelta(days=14)


def test_kwargs_refuses_unknown_demand_class():
    with pytest.raises((ValueError,)):
        _scm_reorder_run_kwargs({"demand_class": "dealer"}, RUN_DAY)


def test_kwargs_refuses_start_after_end():
    with pytest.raises((ValueError,)):
        _scm_reorder_run_kwargs(
            {"horizon_start_days": 10, "horizon_end_days": 5}, RUN_DAY,
        )


def test_kwargs_refuses_negative_end():
    with pytest.raises((ValueError,)):
        _scm_reorder_run_kwargs({"horizon_end_days": -1}, RUN_DAY)


# --------------------------------------------------------------------------------------- #
# Group C - the handler, planning pipeline stubbed exactly like the sibling low-stock suite
# --------------------------------------------------------------------------------------- #

class _Task:
    """Stands in for a `ScheduledTask` row - the handler only ever reads these two
    attributes off its `task` argument, so a plain object is enough (no DB row needed;
    the `db` argument itself is only threaded through to the stubs below)."""

    def __init__(self, metadata=None, timezone="UTC"):
        self.metadata_ = metadata
        self.timezone = timezone


def _stub_pipeline(monkeypatch, run_id: str) -> dict:
    """Stubs `create_run`/`run_reorder`/`apply_run_budget`/`dispatch_ready` and returns a
    dict of lists the test reads back: `create_run_kwargs` (the LAST call's kwargs),
    `budget_calls` (positional args after db, rid) and `dispatch_calls` (run ids)."""
    from app.services.scm import low_stock_report_service as lsr
    from app.services.scm import reorder_run_service as reorder_svc

    captured: dict = {"create_run_kwargs": None, "budget_calls": [], "dispatch_calls": []}

    def _create_run(db, **kwargs):
        captured["create_run_kwargs"] = kwargs
        return {"run_id": run_id}

    def _apply_run_budget(db, rid, budget, full=False):
        captured["budget_calls"].append({"run_id": rid, "budget": budget, "full": full})
        return {"funded": 1}

    monkeypatch.setattr(reorder_svc, "create_run", _create_run)
    monkeypatch.setattr(reorder_svc, "run_reorder", lambda rid, db=None: None)
    monkeypatch.setattr(reorder_svc, "apply_run_budget", _apply_run_budget)
    monkeypatch.setattr(
        lsr, "dispatch_ready",
        lambda db, rid: captured["dispatch_calls"].append(rid) or {"fired": 0},
    )
    return captured


def test_handler_with_no_metadata_reaches_todays_default_kwargs(db, monkeypatch):
    run_id = str(uuid.uuid4())
    captured = _stub_pipeline(monkeypatch, run_id)

    result = _handler_scm_reorder_run(db, _Task(metadata=None))

    assert result["run_id"] == run_id
    kwargs = captured["create_run_kwargs"]
    assert kwargs is not None
    assert kwargs.get("warehouse_codes") == []
    assert kwargs.get("buy_scope") == "warehouse"
    assert kwargs.get("include_market") is False
    assert kwargs.get("product_codes") is None
    assert kwargs.get("plan_horizon_start") is None
    assert kwargs.get("plan_horizon_date") is None
    assert kwargs.get("demand_class") is None
    assert kwargs.get("enqueue") is False
    assert len(captured["dispatch_calls"]) == 1


def test_handler_with_every_key_threads_them_all_to_create_run(db, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    run_id = str(uuid.uuid4())
    captured = _stub_pipeline(monkeypatch, run_id)
    task = _Task(
        metadata={
            "warehouse_codes": ["ZZT-WH1", "ZZT-WH2"],
            "product_codes": ["ZZT-P1"],
            "demand_class": "project",
            "horizon_start_days": -7,
            "horizon_end_days": 30,
            "budget": 1234.5,
            "include_market": True,
        },
        timezone="Asia/Kuala_Lumpur",
    )
    expected_run_day = datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).date()

    result = _handler_scm_reorder_run(db, task)

    assert result["run_id"] == run_id
    kwargs = captured["create_run_kwargs"]
    assert kwargs.get("warehouse_codes") == ["ZZT-WH1", "ZZT-WH2"]
    assert kwargs.get("product_codes") == ["ZZT-P1"]
    assert kwargs.get("demand_class") == "project"
    assert kwargs.get("plan_horizon_start") == expected_run_day - timedelta(days=7)
    assert kwargs.get("plan_horizon_date") == expected_run_day + timedelta(days=30)
    assert kwargs.get("include_market") is True
    # Numeric budget: apply_run_budget(float), never full=True.
    assert len(captured["budget_calls"]) == 1
    budget_call = captured["budget_calls"][0]
    assert budget_call["budget"] == pytest.approx(1234.5)
    assert budget_call["full"] is False
    assert len(captured["dispatch_calls"]) == 1


@pytest.mark.parametrize("tz", ["Pacific/Kiritimati", "Etc/GMT+12"])
def test_handler_run_day_is_the_tasks_own_local_day(db, monkeypatch, tz):
    """UTC+14 and UTC-12: at any moment at least one of them sits on a different calendar
    day from UTC, so a handler that resolved the window in UTC fails one of these whenever
    the suite runs (review K1). The real task fires at 06:00 KL, 22:00 UTC the day before."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    captured = _stub_pipeline(monkeypatch, str(uuid.uuid4()))
    task = _Task(metadata={"horizon_start_days": 0, "horizon_end_days": 0}, timezone=tz)
    expected = datetime.now(ZoneInfo(tz)).date()

    _handler_scm_reorder_run(db, task)

    kwargs = captured["create_run_kwargs"]
    assert kwargs["plan_horizon_start"] == expected
    assert kwargs["plan_horizon_date"] == expected


def test_handler_absent_budget_funds_in_full(db, monkeypatch):
    run_id = str(uuid.uuid4())
    captured = _stub_pipeline(monkeypatch, run_id)

    _handler_scm_reorder_run(db, _Task(metadata={}))

    budget_call = captured["budget_calls"][0]
    assert budget_call["budget"] is None
    assert budget_call["full"] is True


# --------------------------------------------------------------------------------------- #
# Group D - the model
# --------------------------------------------------------------------------------------- #

def test_model_defaults_all_none():
    m = ScmReorderRunTaskMetadata()
    assert m.warehouse_codes is None
    assert m.product_codes is None
    assert m.demand_class is None
    assert m.horizon_start_days is None
    assert m.horizon_end_days is None
    assert m.budget is None
    assert m.include_market is None


def test_model_extra_keys_pass_through():
    m = ScmReorderRunTaskMetadata(company_ids=["ZZT-CO1"], grace_percent=10)
    dumped = m.model_dump()
    assert dumped.get("company_ids") == ["ZZT-CO1"]
    assert dumped.get("grace_percent") == 10


def test_model_seeded_metadata_validates():
    ScmReorderRunTaskMetadata(**{"budget": None, "include_market": False})
    ScmReorderRunTaskMetadata(**{})


@pytest.mark.parametrize("bad", ["ZZT-WH1", 5])
def test_model_warehouse_codes_rejects_non_list(bad):
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(warehouse_codes=bad)


@pytest.mark.parametrize("bad", ["ZZT-P1", 5])
def test_model_product_codes_rejects_non_list(bad):
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(product_codes=bad)


def test_model_demand_class_accepts_project_and_retail():
    ScmReorderRunTaskMetadata(demand_class="project")
    ScmReorderRunTaskMetadata(demand_class="retail")


def test_model_demand_class_rejects_unknown():
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(demand_class="dealer")


def test_model_horizon_start_days_range():
    ScmReorderRunTaskMetadata(horizon_start_days=-3650)
    ScmReorderRunTaskMetadata(horizon_start_days=3650)
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(horizon_start_days=-3651)
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(horizon_start_days=3651)


def test_model_horizon_start_days_strict_int():
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(horizon_start_days=1.5)
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(horizon_start_days="10")


def test_model_horizon_end_days_range_and_negative_refused():
    ScmReorderRunTaskMetadata(horizon_end_days=0)
    ScmReorderRunTaskMetadata(horizon_end_days=3650)
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(horizon_end_days=-1)
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(horizon_end_days=3651)


def test_model_start_after_end_refused():
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(horizon_start_days=10, horizon_end_days=5)


def test_model_start_equal_end_allowed():
    ScmReorderRunTaskMetadata(horizon_start_days=5, horizon_end_days=5)


def test_model_budget_accepts_int_and_float():
    ScmReorderRunTaskMetadata(budget=100)
    ScmReorderRunTaskMetadata(budget=100.5)


def test_model_budget_rejects_negative():
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(budget=-1)


def test_model_budget_rejects_string():
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(budget="100")


def test_model_budget_rejects_bool():
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(budget=True)


def test_model_include_market_strict_bool():
    ScmReorderRunTaskMetadata(include_market=True)
    ScmReorderRunTaskMetadata(include_market=False)
    with pytest.raises(ValidationError):
        ScmReorderRunTaskMetadata(include_market="yes")


# --------------------------------------------------------------------------------------- #
# Group E - update_task validates the MERGED metadata for key scm_reorder_run
# --------------------------------------------------------------------------------------- #

def _task_row(db, *, key: str = "scm_reorder_run", metadata=None, name: str | None = None) -> ScheduledTask:
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        key=key,
        name=name or f"ZZT probe {key}",
        enabled=True,
        interval_unit="hours",
        interval_value=1,
        timezone="UTC",
        metadata_=metadata,
    )
    db.add(task)
    db.flush()
    return task


def test_service_rejects_invalid_metadata_and_stores_nothing():
    with blank_session() as db:
        task = _task_row(db, metadata={"budget": None})
        db.commit()
        task_id = task.id

        with pytest.raises(AppException) as exc:
            update_task(db, task_id, metadata={"demand_class": "dealer"})
        assert exc.value.status_code == 422
        assert exc.value.detail.get("code") == "invalid_task_metadata"

        stored = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).one()
        assert stored.metadata_ == {"budget": None}


def test_service_saves_valid_metadata_for_scm_reorder_run():
    with blank_session() as db:
        task = _task_row(db, metadata={})
        db.commit()

        updated = update_task(
            db,
            task.id,
            metadata={
                "warehouse_codes": ["ZZT-WH1"],
                "demand_class": "retail",
                "horizon_end_days": 30,
                "budget": 5000,
                "include_market": True,
            },
        )

        assert updated.metadata_.get("warehouse_codes") == ["ZZT-WH1"]
        assert updated.metadata_.get("demand_class") == "retail"
        assert updated.metadata_.get("horizon_end_days") == 30
        assert updated.metadata_.get("budget") == 5000
        assert updated.metadata_.get("include_market") is True


def test_service_null_clears_a_key():
    with blank_session() as db:
        task = _task_row(db, metadata={"warehouse_codes": ["ZZT-WH1"], "budget": 100})
        db.commit()

        updated = update_task(db, task.id, metadata={"warehouse_codes": None})

        assert "warehouse_codes" not in (updated.metadata_ or {})
        assert updated.metadata_.get("budget") == 100


def test_service_merge_refuses_when_patch_makes_start_after_stored_end():
    """Stored horizon_end_days=5; a PATCH of horizon_start_days=10 alone must validate
    against the MERGED map (start=10, end=5), not the patch in isolation."""
    with blank_session() as db:
        task = _task_row(db, metadata={"horizon_end_days": 5})
        db.commit()
        task_id = task.id

        with pytest.raises(AppException) as exc:
            update_task(db, task_id, metadata={"horizon_start_days": 10})
        assert exc.value.status_code == 422

        stored = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).one()
        assert stored.metadata_ == {"horizon_end_days": 5}


def test_service_other_task_key_metadata_is_not_validated():
    with blank_session() as db:
        task = _task_row(db, key="system_health_watchdog", metadata={})
        db.commit()

        updated = update_task(db, task.id, metadata={"demand_class": "dealer", "anything": 1})

        assert updated.metadata_.get("demand_class") == "dealer"
        assert updated.metadata_.get("anything") == 1


@pytest.fixture
def http_client(monkeypatch):
    """A TestClient authenticated as an admin principal, `get_db` pointed at a blank
    scratch schema. The scheduled-tasks writes take the admin role (ROUTE-PERM-GATES), so
    only the role lookup is pinned; the module guard short-circuits with
    `module_guard_strict` off (the default)."""
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"admin"})
    with blank_session() as db:
        def _override_get_db():
            yield db

        principal = {"id": "zzt-scm-scope-caller", "email": "zzt-scm-scope-caller@test.com"}
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = lambda: principal
        app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
        try:
            yield db, TestClient(app)
        finally:
            app.dependency_overrides.clear()


def test_patch_route_rejects_invalid_metadata_with_422(http_client):
    db, client = http_client
    task = _task_row(db, metadata={})
    db.commit()

    response = client.patch(
        f"/api/v1/system/scheduled-tasks/{task.id}",
        json={"metadata": {"demand_class": "dealer"}},
    )

    assert response.status_code == 422, response.text
    assert response.json().get("code") == "invalid_task_metadata"

    stored = db.query(ScheduledTask).filter(ScheduledTask.id == task.id).one()
    assert stored.metadata_ == {}


def test_patch_route_accepts_valid_metadata_and_echoes_it(http_client):
    db, client = http_client
    task = _task_row(db, metadata={})
    db.commit()

    response = client.patch(
        f"/api/v1/system/scheduled-tasks/{task.id}",
        json={
            "metadata": {
                "warehouse_codes": ["ZZT-WH1"],
                "demand_class": "project",
                "horizon_start_days": -7,
                "horizon_end_days": 30,
                "budget": 2500,
                "include_market": False,
            }
        },
    )

    assert response.status_code == 200, response.text
    metadata = response.json()["metadata"]
    assert metadata["warehouse_codes"] == ["ZZT-WH1"]
    assert metadata["demand_class"] == "project"
    assert metadata["horizon_start_days"] == -7
    assert metadata["horizon_end_days"] == 30
    assert metadata["budget"] == 2500


def test_patch_route_null_clears_a_key(http_client):
    db, client = http_client
    task = _task_row(db, metadata={"warehouse_codes": ["ZZT-WH1"], "budget": 100})
    db.commit()

    response = client.patch(
        f"/api/v1/system/scheduled-tasks/{task.id}",
        json={"metadata": {"warehouse_codes": None}},
    )

    assert response.status_code == 200, response.text
    metadata = response.json()["metadata"]
    assert "warehouse_codes" not in metadata
    assert metadata.get("budget") == 100
