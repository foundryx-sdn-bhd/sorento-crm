"""OI reject notify: email + in-app to the OI raiser, one per OI per reject action
(`documentation/plans/scm/PLAN-oi-reject-notify-13oct.md`, AC-RJ-1 to AC-RJ-10).

Same two harnesses as `test_order_inquiry_handover_automation.py`, for the same reasons:

* the reject -> queue -> post-commit drain contract reuses the handshake suite's `world` /
  `api` fixtures on the REAL database, with `AutomationService.dispatch_event` and
  `NotificationService.create_in_app_only` monkeypatched - the drain's fresh
  `SessionLocal()` cannot see this suite's rolled-back savepoint, so a real write there
  would point at users and rows that do not exist for it;
* the trigger catalog, seed migration and template use a blank scratch schema.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.project_so import OrderInquiry
from app.models.user import User
from app.services.automation_triggers import build_order_inquiry_link

from ._pg_fixture import blank_session
from .test_order_inquiry_handshake import (
    LIST,
    _as_purchasing,
    _raise_one_row,
    _raise_two_rows,
    api,
    world,
)

__all__ = ["api", "world"]  # re-exported fixtures

TRIGGER = "order_inquiry_rejected"
TEMPLATE_CODE = "order_inquiry_rejected_default"
AUTOMATION_NAME = "Order inquiry rejected"


# --------------------------------------------------------------------------- #
# harness                                                                     #
# --------------------------------------------------------------------------- #


def _capture(monkeypatch) -> dict:
    captured: dict = {"dispatches": [], "in_app": []}

    def _fake_dispatch(self, trigger_type, *, context, source_kind, source_id):
        captured["dispatches"].append(
            {
                "trigger_type": trigger_type,
                "context": context,
                "source_kind": source_kind,
                "source_id": source_id,
            }
        )
        return {"trigger_type": trigger_type, "fired": 0, "results": []}

    def _fake_in_app(self, user_id, type, title, body=None, **kwargs):
        captured["in_app"].append(
            {"user_id": user_id, "type": type, "title": title, "body": body, **kwargs}
        )
        return None

    monkeypatch.setattr(
        "app.services.automation_service.AutomationService.dispatch_event",
        _fake_dispatch,
    )
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.create_in_app_only",
        _fake_in_app,
    )
    return captured


def _register() -> None:
    from app.services.project_order_inquiry_service import (
        register_order_inquiry_post_commit_dispatch,
    )

    register_order_inquiry_post_commit_dispatch()


def _rejected(captured) -> list[dict]:
    return [c for c in captured["dispatches"] if c["trigger_type"] == TRIGGER]


def _handover(captured) -> list[dict]:
    return [c for c in captured["dispatches"] if c["trigger_type"] == "order_inquiry_handover"]


def _activate(world, user_id: str, *, status: str = "ACTIVE") -> None:
    user = world.db.query(User).filter(User.id == user_id).one()
    user.status = status
    world.db.commit()


def _inquiry_of(world, row) -> OrderInquiry:
    return world.db.query(OrderInquiry).filter(OrderInquiry.id == row.order_inquiry_id).one()


def _reject_one(world, row, reason="No stock at supplier"):
    with _as_purchasing(world) as buyer:
        resp = buyer.post(f"{LIST}/{row.id}/reject", json={"reason": reason})
    world.db.commit()
    return resp


def _reject_batch(world, rows, reason="Supplier discontinued this finish"):
    with _as_purchasing(world) as buyer:
        resp = buyer.post(
            f"{LIST}/reject",
            json={"row_ids": [str(r.id) for r in rows], "reason": reason},
        )
    world.db.commit()
    return resp


# --------------------------------------------------------------------------- #
# catalog                                                                     #
# --------------------------------------------------------------------------- #


def test_trigger_in_catalog():
    from app.services import automation_triggers

    types = {spec.type for spec in automation_triggers.list_specs()}
    assert TRIGGER in types


# --------------------------------------------------------------------------- #
# AC-RJ-1 / AC-RJ-4 / AC-RJ-5: single reject                                  #
# --------------------------------------------------------------------------- #


def test_single_reject_dispatches_one_email_and_one_in_app(api, monkeypatch):
    client, world = api
    _register()
    _activate(world, world.cs_user)
    fixture = _raise_one_row(api, qty="10")
    row = fixture["row"]
    inquiry = _inquiry_of(world, row)
    assert str(inquiry.raised_by) == str(world.cs_user)
    captured = _capture(monkeypatch)

    resp = _reject_one(world, row, reason="No stock at supplier")
    assert resp.status_code == 200, resp.text

    calls = _rejected(captured)
    assert len(calls) == 1
    call = calls[0]
    assert call["source_kind"] == "order_inquiry"
    assert call["source_id"] == str(inquiry.id)
    ctx = call["context"]
    rej = ctx["reject"]
    assert rej["inquiry_no"] == inquiry.inquiry_no
    assert rej["so_number"] == fixture["core_so"].so_number
    assert "customer" in rej and "project" in rej
    assert rej["reason"] == "No stock at supplier"
    assert rej["link"] == build_order_inquiry_link(str(inquiry.id))
    assert rej["line_count"] == 1
    [line] = rej["lines"]
    assert line["item_code"] == row.item_code
    assert line["qty"] == "10"
    assert line["delivery_date"] == row.delivery_date.strftime("%d/%m/%Y")
    # Rejected by + when: the buyer, in Malaysia time, dd/mm/yyyy HH:MM.
    assert ctx["actor"]["email"].startswith(str(world.buyer))
    world.db.refresh(row)
    assert rej["rejected_at"] == (
        row.rejected_at.replace(tzinfo=timezone.utc)
        .astimezone(ZoneInfo("Asia/Kuala_Lumpur"))
        .strftime("%d/%m/%Y %H:%M")
    ), "the stored UTC stamp, shown in Malaysia time"
    # Recipient: the raiser.
    assert ctx["raiser"]["email"].startswith(str(world.cs_user))

    [notice] = captured["in_app"]
    assert notice["user_id"] == str(world.cs_user)
    assert inquiry.inquiry_no in notice["title"]
    assert "1 line rejected" in notice["title"]
    assert "No stock at supplier" in (notice["body"] or "")
    assert notice["data"]["link"] == f"/project-sales/order-inquiries/{inquiry.id}"
    assert notice["source_entity_type"] == "order_inquiry"
    assert notice["source_entity_id"] == str(inquiry.id)
    assert " · " not in notice["title"] and " · " not in (notice["body"] or "")


# --------------------------------------------------------------------------- #
# AC-RJ-2: batch of one OI -> one                                             #
# --------------------------------------------------------------------------- #


def test_batch_of_one_inquiry_sends_one_listing_both_lines(api, monkeypatch):
    client, world = api
    _register()
    _activate(world, world.cs_user)
    fixture = _raise_two_rows(api, first_qty="10", second_qty="6")
    first, second = fixture["first"]["row"], fixture["second"]["row"]
    assert first.order_inquiry_id == second.order_inquiry_id
    captured = _capture(monkeypatch)

    resp = _reject_batch(world, [first, second])
    assert resp.status_code == 200, resp.text

    calls = _rejected(captured)
    assert len(calls) == 1, "one email per OI per reject action"
    rej = calls[0]["context"]["reject"]
    assert rej["line_count"] == 2
    assert sorted(line["qty"] for line in rej["lines"]) == ["10", "6"]
    assert rej["reason"] == "Supplier discontinued this finish"
    assert len(captured["in_app"]) == 1
    assert "2 lines rejected" in captured["in_app"][0]["title"]


# --------------------------------------------------------------------------- #
# AC-RJ-3: batch spanning two OIs -> one each                                 #
# --------------------------------------------------------------------------- #


def test_batch_spanning_two_inquiries_sends_one_each(api, monkeypatch):
    client, world = api
    _register()
    _activate(world, world.cs_user)
    a = _raise_one_row(api, qty="10")["row"]
    b = _raise_one_row(api, qty="4")["row"]
    assert a.order_inquiry_id != b.order_inquiry_id
    captured = _capture(monkeypatch)

    resp = _reject_batch(world, [a, b])
    assert resp.status_code == 200, resp.text

    calls = _rejected(captured)
    assert sorted(c["source_id"] for c in calls) == sorted(
        [str(a.order_inquiry_id), str(b.order_inquiry_id)]
    )
    by_source = {c["source_id"]: c["context"]["reject"] for c in calls}
    assert [l["qty"] for l in by_source[str(a.order_inquiry_id)]["lines"]] == ["10"]
    assert [l["qty"] for l in by_source[str(b.order_inquiry_id)]["lines"]] == ["4"]
    assert len(captured["in_app"]) == 2


# --------------------------------------------------------------------------- #
# AC-RJ-7: raiser = rejecter / raiser inactive                                #
# --------------------------------------------------------------------------- #


def test_raiser_is_rejecter_gets_nothing(api, monkeypatch):
    client, world = api
    _register()
    _activate(world, world.cs_user)
    row = _raise_one_row(api, qty="10")["row"]
    inquiry = _inquiry_of(world, row)
    inquiry.raised_by = world.buyer
    world.db.commit()
    _activate(world, world.buyer)
    captured = _capture(monkeypatch)

    resp = _reject_one(world, row)
    assert resp.status_code == 200, resp.text

    [call] = _rejected(captured)
    assert call["context"]["raiser"] is None
    assert captured["in_app"] == []


@pytest.mark.parametrize("status,trashed", [("INACTIVE", False), ("BLOCKED", False), ("ACTIVE", True)])
def test_raiser_not_active_gets_nothing(api, monkeypatch, status, trashed):
    client, world = api
    _register()
    row = _raise_one_row(api, qty="10")["row"]
    user = world.db.query(User).filter(User.id == world.cs_user).one()
    user.status = status
    user.is_trashed = trashed
    world.db.commit()
    captured = _capture(monkeypatch)

    resp = _reject_one(world, row)
    assert resp.status_code == 200, resp.text

    [call] = _rejected(captured)
    assert call["context"]["raiser"] is None
    assert captured["in_app"] == []


# --------------------------------------------------------------------------- #
# AC-RJ-8: refused reject sends nothing                                       #
# --------------------------------------------------------------------------- #


def test_refused_reject_sends_nothing(api, monkeypatch):
    client, world = api
    _register()
    _activate(world, world.cs_user)
    row = _raise_one_row(api, qty="10")["row"]
    assert _reject_one(world, row).status_code == 200
    captured = _capture(monkeypatch)

    resp = _reject_one(world, row)
    assert resp.status_code == 422, resp.text
    assert _rejected(captured) == []
    assert captured["in_app"] == []


def test_all_or_nothing_batch_refusal_sends_nothing(api, monkeypatch):
    client, world = api
    _register()
    _activate(world, world.cs_user)
    done = _raise_one_row(api, qty="10")["row"]
    fresh = _raise_one_row(api, qty="4")["row"]
    assert _reject_one(world, done).status_code == 200
    captured = _capture(monkeypatch)

    resp = _reject_batch(world, [fresh, done])
    assert resp.status_code == 422, resp.text
    assert _rejected(captured) == []
    assert captured["in_app"] == []


def test_reject_rolled_back_after_queue_sends_nothing(api, monkeypatch):
    """AC-RJ-8: the notice is queued, then the request fails and rolls back. Nothing is
    sent then, and nothing leaks into the session's next, unrelated commit."""
    client, world = api
    _register()
    _activate(world, world.cs_user)
    row = _raise_one_row(api, qty="10")["row"]
    captured = _capture(monkeypatch)

    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    def _boom(self, rows):
        raise RuntimeError("serialize failed after the queue step")

    monkeypatch.setattr(ProjectOrderInquiryService, "serialize_rows", _boom)
    with _as_purchasing(world) as buyer:
        resp = buyer.post(f"{LIST}/{row.id}/reject", json={"reason": "No stock"})
    assert resp.status_code == 500, resp.text
    world.db.commit()

    assert _rejected(captured) == []
    assert captured["in_app"] == []


def test_savepoint_release_does_not_fire_before_the_commit(api, monkeypatch):
    """`after_commit` fires on a savepoint release too: a release after the notice is
    queued must not send it mid-transaction. It goes out once, at the real commit."""
    client, world = api
    _register()
    _activate(world, world.cs_user)
    row = _raise_one_row(api, qty="10")["row"]
    captured = _capture(monkeypatch)

    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    original = ProjectOrderInquiryService.serialize_rows
    seen_at_release: list[int] = []

    def _with_savepoint(self, rows):
        with self.db.begin_nested():
            pass
        seen_at_release.append(len(_rejected(captured)))
        return original(self, rows)

    monkeypatch.setattr(ProjectOrderInquiryService, "serialize_rows", _with_savepoint)
    resp = _reject_one(world, row)
    assert resp.status_code == 200, resp.text

    assert seen_at_release == [0], "nothing is sent at a savepoint release"
    assert len(_rejected(captured)) == 1


def test_reject_again_after_new_raise_is_a_fresh_notice(api, monkeypatch):
    """Dedup is per reject ACTION: two separate rejects on the same OI give two
    in-app notices with different dedup keys, never one swallowing the other."""
    client, world = api
    _register()
    _activate(world, world.cs_user)
    fixture = _raise_two_rows(api)
    first = fixture["first"]["row"]
    captured = _capture(monkeypatch)

    assert _reject_one(world, first).status_code == 200
    # The reject re-confirmed the order minus line 1; line 2's live row may have been
    # re-raised, so read it fresh.
    from .test_order_inquiry_handshake import _order_row

    second = _order_row(world, fixture["second"]["line"])
    assert _reject_one(world, second).status_code == 200

    assert len(captured["in_app"]) == 2
    keys = {n["dedup_key"] for n in captured["in_app"]}
    assert len(keys) == 2


# --------------------------------------------------------------------------- #
# AC-RJ-9: no handover email from the reject's re-confirm                     #
# --------------------------------------------------------------------------- #


def test_reject_on_two_line_order_sends_no_handover(api, monkeypatch):
    client, world = api
    _register()
    _activate(world, world.cs_user)
    fixture = _raise_two_rows(api)
    captured = _capture(monkeypatch)

    resp = _reject_one(world, fixture["first"]["row"])
    assert resp.status_code == 200, resp.text

    assert _handover(captured) == [], "a reject must not send the handover email"
    assert len(_rejected(captured)) == 1


# --------------------------------------------------------------------------- #
# AC-RJ-6 / AC-RJ-10: seed migration + template                               #
# --------------------------------------------------------------------------- #


def _load_seed_migration():
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    path = versions / "oirj_0001_seed_reject_automation.py"
    assert path.exists(), f"missing seed migration {path.name}"
    spec = importlib.util.spec_from_file_location("zzt_oirj_seed_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, db, fn: str) -> None:
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        getattr(module, fn)()


def test_seed_migration_idempotent_and_enabled_with_include_raiser():
    module = _load_seed_migration()
    with blank_session() as db:
        _run(module, db, "upgrade")
        _run(module, db, "upgrade")
        assert db.execute(
            sa.text("SELECT count(*) FROM email_templates WHERE code = :c"),
            {"c": TEMPLATE_CODE},
        ).scalar() == 1
        rows = db.execute(
            sa.text(
                "SELECT enabled, group_matches, recipient_config FROM automations "
                "WHERE trigger_type = :t AND name = :n"
            ),
            {"t": TRIGGER, "n": AUTOMATION_NAME},
        ).fetchall()
        assert len(rows) == 1
        enabled, group_matches, cfg = rows[0]
        assert enabled is True
        assert group_matches is False
        cfg = cfg if isinstance(cfg, dict) else json.loads(cfg)
        assert cfg["include_raiser"] is True
        assert cfg["one_email"] is True
        assert cfg.get("role_ids") == [] and cfg.get("user_ids") == []
        assert not cfg.get("include_actor")


def test_seed_migration_downgrade_removes_both():
    module = _load_seed_migration()
    with blank_session() as db:
        _run(module, db, "upgrade")
        _run(module, db, "downgrade")
        assert db.execute(
            sa.text("SELECT count(*) FROM email_templates WHERE code = :c"),
            {"c": TEMPLATE_CODE},
        ).scalar() == 0
        assert db.execute(
            sa.text("SELECT count(*) FROM automations WHERE trigger_type = :t"),
            {"t": TRIGGER},
        ).scalar() == 0


def _render(context: dict) -> dict:
    from app.models.email_template import EmailTemplate
    from app.services.email_template_service import EmailTemplateService

    module = _load_seed_migration()
    with blank_session() as db:
        _run(module, db, "upgrade")
        template = (
            db.query(EmailTemplate).filter(EmailTemplate.code == TEMPLATE_CODE).one()
        )
        return EmailTemplateService(db).render(template, context)


_CTX = {
    "reject": {
        "inquiry_no": "OI-2610-0007",
        "so_number": "SO-422517",
        "customer": "ABC Builders Sdn Bhd",
        "project": "Residensi Yotu",
        "reason": "Supplier discontinued this finish",
        "rejected_at": "13/10/2026 14:05",
        "link": "https://crm.example/project-sales/order-inquiries/abc",
        "line_count": 2,
        "lines": [
            {"item_code": "WC-1234-WH", "qty": "10", "delivery_date": "25/10/2026"},
            {"item_code": "BS-5678-CH", "qty": "6", "delivery_date": None},
        ],
    },
    "actor": {"name": "Joey", "email": "joey@example.com"},
    "raiser": {"name": "Eling", "email": "eling@example.com"},
    "today": "13/10/2026",
}


def test_template_renders_every_fact():
    out = _render(_CTX)
    assert out["subject"] == "OI rejected: OI-2610-0007 SO-422517"
    for body in (out["body_html"], out["body_text"]):
        for needle in (
            "OI-2610-0007",
            "SO-422517",
            "ABC Builders Sdn Bhd",
            "Residensi Yotu",
            "WC-1234-WH",
            "BS-5678-CH",
            "25/10/2026",
            "Supplier discontinued this finish",
            "Joey",
            "13/10/2026 14:05",
            "https://crm.example/project-sales/order-inquiries/abc",
        ):
            assert needle in body, (needle, body)
        assert "None" not in body
        assert " · " not in body
    assert "border:1px solid #d0d0d5" in out["body_html"]


def test_template_blank_so_and_customer_print_empty():
    ctx = json.loads(json.dumps(_CTX))
    ctx["reject"]["so_number"] = None
    ctx["reject"]["customer"] = None
    ctx["reject"]["project"] = None
    out = _render(ctx)
    assert "None" not in out["subject"]
    assert "None" not in out["body_html"]
    assert "None" not in out["body_text"]
