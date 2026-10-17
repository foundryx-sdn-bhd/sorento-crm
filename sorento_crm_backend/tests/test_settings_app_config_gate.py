"""`GET /api/v1/user-management/settings/` returned the whole system-settings
singleton to any authenticated session: three n8n webhook URLs (bearer-capability
URLs - holding one is holding the permission), SMTP host/port/username/from, both
default-approver names and emails, `health_notify_role_ids` / `_user_ids`, and every
role id and name in the tenant. It is now gated on `user_management.settings.view`,
which is Q2 of documentation/plans/security/PLAN-user-management-read-gates.md.

Gating it alone would have been a silent degradation: `hooks/useCurrencyFormat.ts`,
`hooks/use-excel-accept.ts` and `PurchaseRequestDetail.tsx` read it from procurement
screens whose roles hold no `user_management.*` slug. So the gate ships alongside
`GET /settings/app-config`, a six-field projection open to every authenticated user.

These tests cover both halves:

1. `/settings/` denies without the slug (naming it) and allows with it.
2. `/app-config` answers a caller holding ZERO permissions.
3. `/app-config` leaks nothing. The settings row is seeded with recognisable
   NON-NULL values in every sensitive field first, so the absence assertions prove
   suppression rather than passing because the row was empty - the failure mode this
   test exists to catch is a future dict-builder line, and against an empty row that
   line would be invisible.
4. `/app-config` still answers when the singleton row does not exist at all.

Dependency-override pattern from tests/test_user_respond_users_permission.py
(overrides get_db / get_current_user, monkeypatches
UserPermissionService.check_user_has_permission against an `allow` set of granted
slugs). Postgres `blank_session()` for the DB, per PRINCIPLES.md - never sqlite.

Run: pytest tests/test_settings_app_config_gate.py -v
"""
from __future__ import annotations

import uuid

import pytest

from tests._pg_fixture import TEST_PREFIX, blank_session

SETTINGS_ENDPOINT = "/api/v1/user-management/settings/"
APP_CONFIG_ENDPOINT = "/api/v1/user-management/settings/app-config"
PERMISSION = "user_management.settings.view"
_USER = {"id": str(uuid.uuid4()), "email": "settings-caller@zzt.test"}

# The whole contract of the projection. Anything outside this set reaching the wire
# is the leak this endpoint was built to prevent.
EXPECTED_FIELDS = {
    "currency",
    "currency_format",
    "purchase_request_default_approver_user_id",
    "purchase_request_default_approver_email",
    "sponsorship_form_default_approver_user_id",
    "sponsorship_form_default_approver_email",
    # PLAN-price-tag-r9-review-loop.md D10: the CRM price tag detail card shows
    # when an untouched hand-over auto-collects, and marketing does not hold
    # `user_management.settings.view` - a day count is not sensitive.
    "price_tag_auto_collect_days",
    # PLAN-oi-request-cs-reserve.md section 6c F1: the reserve dialog's default pool
    # must reach every reserve-permission holder (Eling, CS), who does not hold
    # `user_management.settings.view` either - a warehouse id is not sensitive.
    "oi_reserve_default_pool_warehouse_id",
    # PLAN-sample-request-core-15oct.md (SR-CORE) ruling 3: the staff sample request
    # form must know whether a registered project is required, and the salespeople
    # filling it do not hold `user_management.settings.view`. One boolean, not
    # sensitive. (The quotation switch was removed 16 Oct.)
    "sample_request_require_project",
}

# Named individually rather than derived, so the test reads as the list of things
# that must never appear and fails loudly if one is added back.
FORBIDDEN_FIELDS = [
    "smtp",
    "smtp_host",
    "smtp_password",
    "n8n_attachment_webhook_url",
    "n8n_crm_chat_outbound_webhook_url",
    "n8n_stock_inquiry_revise_webhook_url",
    "roles",
    "health_notify_role_ids",
    "health_notify_user_ids",
    "purchase_request_default_approver_name",
    "sponsorship_form_default_approver_name",
]


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture
def api(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user
    from app.main import app
    from app.services.user_service import UserPermissionService

    allow: set[str] = set()

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: _USER
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in allow,
    )
    client = TestClient(app)
    try:
        yield client, allow
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def _seed_settings(db):
    """A settings row whose sensitive columns are all non-null and recognisable,
    plus the two default approvers as real `users` rows (the FK is enforced on
    Postgres). Returns (pr_user, sf_user)."""
    from app.models.user import SystemSetting, User

    pr_user = User(
        id=str(uuid.uuid4()),
        email=f"{TEST_PREFIX}-pr-approver@zzt.test".lower(),
        name=f"{TEST_PREFIX} PR Approver",
        avatar_storage_provider="s3",
    )
    sf_user = User(
        id=str(uuid.uuid4()),
        email=f"{TEST_PREFIX}-sf-approver@zzt.test".lower(),
        name=f"{TEST_PREFIX} SF Approver",
        avatar_storage_provider="s3",
    )
    db.add_all([pr_user, sf_user])
    db.flush()

    db.add(
        SystemSetting(
            id=str(uuid.uuid4()),
            name=f"{TEST_PREFIX} Co",
            currency="MYR",
            currency_format="RM {value}",
            smtp_host="zzt-smtp.example.test",
            smtp_port="587",
            smtp_username="zzt-smtp-user",
            smtp_password="zzt-smtp-secret",
            smtp_from="zzt-noreply@example.test",
            n8n_attachment_webhook_url="https://n8n.zzt.test/webhook/attachment-CAPABILITY",
            n8n_crm_chat_outbound_webhook_url="https://n8n.zzt.test/webhook/chat-CAPABILITY",
            n8n_stock_inquiry_revise_webhook_url="https://n8n.zzt.test/webhook/revise-CAPABILITY",
            health_notify_role_ids=[str(uuid.uuid4())],
            health_notify_user_ids=[str(uuid.uuid4())],
            purchase_request_default_approver_user_id=pr_user.id,
            sponsorship_form_default_approver_user_id=sf_user.id,
        )
    )
    db.commit()
    return pr_user, sf_user


def test_settings_denied_without_permission(api, db):
    client, _allow = api
    _seed_settings(db)

    resp = client.get(SETTINGS_ENDPOINT)

    assert resp.status_code == 403
    assert PERMISSION in resp.json()["detail"]


def test_settings_returned_with_permission(api, db):
    client, allow = api
    allow.add(PERMISSION)
    _seed_settings(db)

    resp = client.get(SETTINGS_ENDPOINT)

    assert resp.status_code == 200
    body = resp.json()
    # Unchanged shape: the gate is the only thing this PR did to this route.
    assert body["settings"]["smtp"]["smtp_host"] == "zzt-smtp.example.test"
    assert "roles" in body


def test_app_config_open_to_a_caller_with_zero_permissions(api, db):
    client, allow = api
    assert allow == set(), "the point of this test is a caller holding nothing"
    pr_user, sf_user = _seed_settings(db)

    resp = client.get(APP_CONFIG_ENDPOINT)

    assert resp.status_code == 200
    assert resp.json() == {
        "currency": "MYR",
        "currency_format": "RM {value}",
        "purchase_request_default_approver_user_id": pr_user.id,
        "purchase_request_default_approver_email": pr_user.email,
        "sponsorship_form_default_approver_user_id": sf_user.id,
        "sponsorship_form_default_approver_email": sf_user.email,
        # r9 D10: `_seed_settings` does not set this column, so it carries the
        # model's own default (7), the same value the route falls back to.
        "price_tag_auto_collect_days": 7,
        # section 6c F1: `_seed_settings` does not set this column either, so it
        # carries null (no configured default) the same as every other unset id here.
        "oi_reserve_default_pool_warehouse_id": None,
        # SR-CORE: the project switch ships OFF (soft transition).
        "sample_request_require_project": False,
    }


def test_app_config_projects_exactly_the_expected_fields_and_leaks_nothing(api, db):
    client, _allow = api
    _seed_settings(db)

    body = client.get(APP_CONFIG_ENDPOINT).json()

    assert set(body) == EXPECTED_FIELDS
    for field in FORBIDDEN_FIELDS:
        assert field not in body, f"{field!r} leaked into the app-config projection"
    # Belt and braces: no capability URL or credential anywhere in the payload,
    # under any key a future refactor might invent.
    serialized = str(body)
    for secret in ("CAPABILITY", "zzt-smtp"):
        assert secret not in serialized


def test_ac_sr_20_app_config_has_no_quotation_switch(api, db):
    """AC-SR-20 (16 Oct): one switch only; the quotation key is gone from app-config."""
    client, _allow = api
    _seed_settings(db)
    body = client.get(APP_CONFIG_ENDPOINT).json()
    assert "sample_request_require_project" in body
    assert "sample_request_require_quotation" not in body


def test_app_config_returns_nulls_when_no_settings_row_exists(api, db):
    client, _allow = api
    from app.models.user import SystemSetting

    assert db.query(SystemSetting).first() is None, "blank schema, nothing seeded"

    resp = client.get(APP_CONFIG_ENDPOINT)

    assert resp.status_code == 200
    expected = {field: None for field in EXPECTED_FIELDS}
    # r9 D10: unlike every other field here, a MISSING settings row still
    # answers the shipped default (7) for `price_tag_auto_collect_days` - the
    # route's own fallback is `7`, not `None`, whether or not a row exists,
    # because the sweep's default is a real number rather than "nothing
    # configured yet".
    expected["price_tag_auto_collect_days"] = 7
    # SR-CORE: a missing row reads the switches OFF, the same as their column default,
    # so a fresh install never demands a project.
    expected["sample_request_require_project"] = False
    assert resp.json() == expected


# --------------------------------------------------------------------------- #
# The WRITE half. Reading the blob required `user_management.settings.view`
# while PUT/POST of the same blob required only authentication, so any
# authenticated session - a sales rep, a warehouse clerk - could rewrite it.
# The chatbot slice made that load-bearing: `chatbot_unsupported_domains`,
# `chatbot_completed_lanes` and `chatbot_stock_denial_enabled` all live here and
# all change what production turns do for real customers.
# --------------------------------------------------------------------------- #

GENERAL_ENDPOINT = "/api/v1/user-management/settings/general"
EDIT_PERMISSION = "user_management.settings.edit"


def test_general_put_denies_without_the_edit_slug(api, db):
    client, allow = api
    _seed_settings(db)
    allow.add(PERMISSION)  # read is not enough

    resp = client.put(GENERAL_ENDPOINT, json={"currency": "SGD"})

    assert resp.status_code == 403
    assert EDIT_PERMISSION in str(resp.json())


def test_general_post_denies_without_the_edit_slug(api, db):
    client, allow = api
    _seed_settings(db)
    allow.add(PERMISSION)

    resp = client.post(GENERAL_ENDPOINT, json={"chatbot_unsupported_domains": []})

    assert resp.status_code == 403


def test_general_put_allows_with_the_edit_slug(api, db):
    client, allow = api
    _seed_settings(db)
    allow.add(EDIT_PERMISSION)

    resp = client.put(GENERAL_ENDPOINT, json={"currency": "SGD"})

    assert resp.status_code == 200
    from app.models.user import SystemSetting

    db.expire_all()
    assert db.query(SystemSetting).first().currency == "SGD"


def test_general_post_allows_with_the_edit_slug(api, db):
    client, allow = api
    _seed_settings(db)
    allow.add(EDIT_PERMISSION)

    resp = client.post(GENERAL_ENDPOINT, json={"chatbot_unsupported_domains": ["forms"]})

    assert resp.status_code == 200
    from app.models.user import SystemSetting

    db.expire_all()
    assert db.query(SystemSetting).first().chatbot_unsupported_domains == ["forms"]
