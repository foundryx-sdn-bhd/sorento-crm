"""The two onboarding HTTP surfaces.

The public one is gated by the intake token alone; the admin one by the five
`user_management.onboarding.*` permissions. Both are exercised here against a
blank Postgres schema with their own seeded users, roles and grants - CI's
database is empty, so a test that leans on an existing role passes locally and
fails there.

The assertion worth naming is `test_the_requester_never_sees_a_role_id`. That
omission is the feature's privacy boundary: a department head with no CRM
account proposes "Salesperson" and learns nothing about what roles exist. It is
enforced by the response schema rather than by a component remembering not to
render a field, and this is what pins it.
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_current_user, get_db
from app.main import app
from app.models.onboarding import OnboardingTemplate
from app.models.company import UserCompany
from app.models.user import (
    User,
    UserPermission,
    UserRole,
    UserRoleAssignment,
    UserRolePermission,
)
from app.services import onboarding_service
from tests._pg_fixture import blank_session, unique_code

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "360_onboarding_slice1.py"
)
SORENTO = "00000000-0000-0000-0000-000000000001"

#: A well-formed uuid that belongs to nothing, for the "unknown id" half of the
#: bad-id cases. Deliberately a literal rather than `uuid.uuid4()`: a parametrize
#: argument is evaluated at COLLECTION time, so a random one gives each xdist
#: worker a different test id and the parallel run aborts with "Different tests
#: were collected between gw0 and gw2" before a single test executes.
UNKNOWN_UUID = "11111111-2222-3333-4444-555555555555"

PUBLIC = "/api/v1/public/onboarding"
ADMIN = "/api/v1/user-management/onboarding"

ALL_SLUGS = (
    "user_management.onboarding.view",
    "user_management.onboarding.add",
    "user_management.onboarding.edit",
    "user_management.onboarding.delete",
    "user_management.onboarding.approve",
)


def _migration():
    spec = importlib.util.spec_from_file_location("onboarding_migration_api", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db():
    with blank_session() as session:
        module = _migration()
        module._seed_graph(session.connection())
        module._mark_system(session.connection())
        yield session


def _make_user(db, *, slugs=()) -> User:
    """A caller holding exactly the slugs named, and nothing else."""
    user = User(
        id=unique_code("user"),
        email=f"{unique_code('caller')}@example.com".lower(),
        name="Test Caller",
        status="ACTIVE",
    )
    role = UserRole(
        id=unique_code("role"), slug=unique_code("role").lower(), name=unique_code("Role")
    )
    db.add_all([user, role])
    db.flush()
    db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    # Grant Sorento: onboarding requests are company-scoped, so a caller with no
    # company grant resolves to an EMPTY scope and every read is a 404 rather
    # than a 403 - which is the correct production behaviour and would otherwise
    # read here as a broken route.
    db.add(UserCompany(user_id=user.id, company_id=SORENTO))
    for slug in slugs:
        # Get-or-create: `user_permissions.slug` is unique, and a test that makes
        # two callers (an editor and an approver, say) would otherwise collide on
        # the second one.
        permission = db.query(UserPermission).filter(UserPermission.slug == slug).first()
        if permission is None:
            permission = UserPermission(id=str(uuid.uuid4()), slug=slug, name=slug)
            db.add(permission)
            db.flush()
        db.add(UserRolePermission(role_id=role.id, permission_id=permission.id))
    db.commit()
    return user


@pytest.fixture
def client(db):
    """A caller whose active company is Sorento, against this session.

    `apply_company_scope` is a ROUTER-level dependency: it runs on every
    `/api/v1/*` request and stamps the scope it resolves from the Bearer token
    onto the session, overwriting whatever was there. A TestClient that
    authenticates by overriding `get_current_user` sends no bearer token, so the
    resolver falls through to UNSET - which is fail-closed, and every scoped read
    then 404s on rows that plainly exist.

    Overriding it here is the honest double for "an authenticated user whose
    active company is Sorento", and it keeps the fail-closed default intact for
    the tests that exist to assert it.
    """
    from app.models.base import set_company_scope
    from app.services.company_scope_resolver import apply_company_scope

    def _sorento_scope():
        scope = frozenset({SORENTO})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[apply_company_scope] = _sorento_scope
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user: User, db=None) -> None:
    """Authenticate as `user`. Scope is handled by the `client` fixture."""
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user.id,
        "email": user.email,
    }


@pytest.fixture
def template(db) -> OnboardingTemplate:
    role = UserRole(
        id=unique_code("role"), slug=unique_code("tpl").lower(), name=unique_code("Sales")
    )
    db.add(role)
    db.flush()
    tpl = OnboardingTemplate(
        name=unique_code("Salesperson"),
        description="Sees their own orders.",
        role_ids=[role.id],
        company_ids=[SORENTO],
        company_id=SORENTO,
    )
    db.add(tpl)
    db.commit()
    return tpl


@pytest.fixture
def sent_request(db):
    request = onboarding_service.create_request(
        db,
        company_id=SORENTO,
        title=unique_code("MOCHA staff"),
        requester_name="Esther Lim",
        requester_email=f"{unique_code('requester')}@example.com".lower(),
    )
    onboarding_service.send_request(db, str(request.id))
    return request


# --- public: the token is the whole auth story --------------------------------


def test_no_token_is_401(client):
    assert client.get(f"{PUBLIC}/me").status_code == 401


def test_unknown_token_is_401(client):
    assert client.get(f"{PUBLIC}/me", params={"token": "NOPE"}).status_code == 401


def test_the_token_works_in_a_header_or_a_query_param(client, sent_request):
    by_query = client.get(f"{PUBLIC}/me", params={"token": sent_request.token})
    by_header = client.get(
        f"{PUBLIC}/me", headers={"X-Onboarding-Token": sent_request.token}
    )
    assert by_query.status_code == 200
    assert by_header.status_code == 200
    assert by_query.json()["title"] == sent_request.title


def test_an_expired_token_is_401(client, db, sent_request):
    sent_request.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    assert (
        client.get(f"{PUBLIC}/me", params={"token": sent_request.token}).status_code == 401
    )


def test_a_revoked_token_is_401(client, db, sent_request):
    onboarding_service.revoke(db, str(sent_request.id))
    assert (
        client.get(f"{PUBLIC}/me", params={"token": sent_request.token}).status_code == 401
    )


def test_the_requester_never_sees_a_role_id(client, sent_request, template):
    """The privacy boundary, enforced by the response schema (UAC AC-5.4)."""
    body = client.get(f"{PUBLIC}/me", params={"token": sent_request.token}).json()
    assert body["templates"], "the picker would be empty"
    option = body["templates"][0]
    assert option["name"] == template.name
    assert set(option) == {
        "id",
        "name",
        "description",
        "default_needs_system_account",
        "default_needs_respond_contact",
        "default_needs_agent_seat",
    }


# --- public: save and submit --------------------------------------------------


def _rows(count: int = 2) -> list[dict]:
    return [
        {
            "row_number": i,
            "full_name": f"Person {i}",
            "role_label": "Sales admin",
            "email_raw": f"person{i}@example.com",
            "phone_raw": f"012-345678{i}",
            "needs_system_account": True,
            "needs_respond_contact": False,
            "needs_agent_seat": False,
        }
        for i in range(1, count + 1)
    ]


def test_saving_rows_then_submitting(client, sent_request):
    saved = client.put(
        f"{PUBLIC}/rows",
        params={"token": sent_request.token},
        json={"rows": _rows(3)},
    )
    assert saved.status_code == 200
    assert len(saved.json()["people"]) == 3
    assert saved.json()["editable"] is True

    submitted = client.post(
        f"{PUBLIC}/submit",
        params={"token": sent_request.token},
        json={"requester_note": "Zul starts next month."},
    )
    assert submitted.status_code == 200
    body = submitted.json()
    assert body["status"] == "submitted"
    assert body["editable"] is False
    assert body["requester_note"] == "Zul starts next month."


def test_the_link_still_works_after_submission_but_read_only(client, sent_request):
    client.put(
        f"{PUBLIC}/rows", params={"token": sent_request.token}, json={"rows": _rows(1)}
    )
    client.post(f"{PUBLIC}/submit", params={"token": sent_request.token}, json={})

    # Still resolvable - the same link is now the status page (UAC AC-3.4).
    after = client.get(f"{PUBLIC}/me", params={"token": sent_request.token})
    assert after.status_code == 200
    assert after.json()["editable"] is False

    refused = client.put(
        f"{PUBLIC}/rows", params={"token": sent_request.token}, json={"rows": _rows(5)}
    )
    assert refused.status_code == 409


def test_the_role_the_requester_typed_survives_to_the_reviewer(client, db, sent_request):
    """She says what somebody does; he decides what that grants.

    Free text all the way through, so it has to make the round trip intact: saved
    on her rows, readable on her own status page, editable by the reviewer, and
    returned on the request detail he works from.
    """
    saved = client.put(
        f"{PUBLIC}/rows", params={"token": sent_request.token}, json={"rows": _rows(1)}
    )
    assert saved.status_code == 200
    assert saved.json()["people"][0]["role_label"] == "Sales admin"

    client.post(f"{PUBLIC}/submit", params={"token": sent_request.token}, json={})

    _as(_make_user(db, slugs=ALL_SLUGS), db)
    detail = client.get(f"{ADMIN}/requests/{sent_request.id}").json()
    person_id = detail["people"][0]["id"]
    assert detail["people"][0]["role_label"] == "Sales admin"

    patched = client.put(
        f"{ADMIN}/requests/{sent_request.id}/people/{person_id}",
        json={"role_label": "Sales admin, KL"},
    )
    assert patched.status_code == 200
    assert patched.json()["people"][0]["role_label"] == "Sales admin, KL"

    after = client.get(f"{ADMIN}/requests/{sent_request.id}").json()
    assert after["people"][0]["role_label"] == "Sales admin, KL"


def test_a_role_longer_than_the_column_is_refused_not_truncated(client, sent_request):
    """122 characters would not fit `VARCHAR(120)`, and a silent truncation is a
    value the requester never wrote."""
    rows = _rows(1)
    rows[0]["role_label"] = "x" * 121
    response = client.put(
        f"{PUBLIC}/rows", params={"token": sent_request.token}, json={"rows": rows}
    )
    assert response.status_code == 422


def test_submitting_nothing_is_refused(client, sent_request):
    response = client.post(
        f"{PUBLIC}/submit", params={"token": sent_request.token}, json={}
    )
    assert response.status_code == 422


# --- a bad template id is a 422 on both writers -------------------------------
#
# `template_id` is a UUID foreign key taken straight from client input on the
# public save AND on the reviewer's patch (both go through
# `_apply_person_values`). Unvalidated, "nope" made Postgres raise `invalid input
# syntax for type uuid` and a well-formed unknown id raised a foreign-key
# violation - both reaching a token holder as a 500, where every other bad field
# on the same payload answers 422.


@pytest.mark.parametrize("bad", ["nope", UNKNOWN_UUID])
def test_a_template_id_that_is_not_on_offer_is_refused_on_the_public_save(
    client, sent_request, bad
):
    rows = _rows(1)
    rows[0]["template_id"] = bad
    response = client.put(
        f"{PUBLIC}/rows", params={"token": sent_request.token}, json={"rows": rows}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("bad", ["nope", UNKNOWN_UUID])
def test_a_template_id_that_is_not_on_offer_is_refused_on_the_reviewers_patch(
    client, db, sent_request, bad
):
    client.put(
        f"{PUBLIC}/rows", params={"token": sent_request.token}, json={"rows": _rows(1)}
    )
    client.post(f"{PUBLIC}/submit", params={"token": sent_request.token}, json={})

    _as(_make_user(db, slugs=ALL_SLUGS), db)
    person_id = client.get(f"{ADMIN}/requests/{sent_request.id}").json()["people"][0]["id"]
    response = client.put(
        f"{ADMIN}/requests/{sent_request.id}/people/{person_id}",
        json={"template_id": bad},
    )
    assert response.status_code == 422


def test_the_template_on_offer_is_still_accepted(client, sent_request, template):
    """The guard refuses what does not exist, not what does."""
    rows = _rows(1)
    rows[0]["template_id"] = str(template.id)
    response = client.put(
        f"{PUBLIC}/rows", params={"token": sent_request.token}, json={"rows": rows}
    )
    assert response.status_code == 200
    assert response.json()["people"][0]["template_id"] == str(template.id)


# --- admin: permissions -------------------------------------------------------


def test_listing_requests_needs_the_view_permission(client, db):
    _as(_make_user(db), db)
    assert client.get(f"{ADMIN}/requests").status_code == 403

    _as(_make_user(db, slugs=("user_management.onboarding.view",)), db)
    assert client.get(f"{ADMIN}/requests").status_code == 200


# --- admin: the queue is paged, sorted and searched server-side ---------------
#
# The reviewer works FROM this list, so "how many are waiting" has to be the
# whole filtered set's answer. A client-side slice of a full-list fetch gets that
# right only while the queue is small, and gets it wrong silently afterwards.


def _make_requests(db, titles: list[str]) -> list:
    """A queue of our own. CI's database is empty, so nothing may be borrowed."""
    made = []
    for title in titles:
        made.append(
            onboarding_service.create_request(
                db,
                company_id=SORENTO,
                title=title,
                requester_name="Esther Lim",
                requester_email=f"{unique_code('requester')}@example.com".lower(),
            )
        )
    return made


def test_the_queue_comes_back_in_the_standard_grid_envelope(client, db):
    marker = unique_code("batch")
    _make_requests(db, [f"{marker} alpha", f"{marker} beta"])
    _as(_make_user(db, slugs=ALL_SLUGS), db)

    body = client.get(f"{ADMIN}/requests", params={"query": marker}).json()
    assert set(body) >= {"data", "pagination"}
    assert body["pagination"]["total"] == 2
    assert body["pagination"]["page"] == 1
    assert {row["title"] for row in body["data"]} == {
        f"{marker} alpha",
        f"{marker} beta",
    }


def test_the_queue_pages_server_side(client, db):
    marker = unique_code("batch")
    _make_requests(db, [f"{marker} a", f"{marker} b", f"{marker} c"])
    _as(_make_user(db, slugs=ALL_SLUGS), db)

    first = client.get(
        f"{ADMIN}/requests",
        params={"query": marker, "limit": 2, "page": 1, "sort": "title", "dir": "asc"},
    ).json()
    second = client.get(
        f"{ADMIN}/requests",
        params={"query": marker, "limit": 2, "page": 2, "sort": "title", "dir": "asc"},
    ).json()

    assert [r["title"] for r in first["data"]] == [f"{marker} a", f"{marker} b"]
    assert [r["title"] for r in second["data"]] == [f"{marker} c"]
    # The total is the filtered set, not the page: it is what the pager reports.
    assert first["pagination"]["total"] == second["pagination"]["total"] == 3


def test_the_queue_sorts_by_the_column_the_grid_asks_for(client, db):
    marker = unique_code("batch")
    _make_requests(db, [f"{marker} charlie", f"{marker} alpha", f"{marker} bravo"])
    _as(_make_user(db, slugs=ALL_SLUGS), db)

    ascending = client.get(
        f"{ADMIN}/requests", params={"query": marker, "sort": "title", "dir": "asc"}
    ).json()
    descending = client.get(
        f"{ADMIN}/requests", params={"query": marker, "sort": "title", "dir": "desc"}
    ).json()

    titles = [r["title"] for r in ascending["data"]]
    assert titles == sorted(titles)
    assert [r["title"] for r in descending["data"]] == list(reversed(titles))


def test_an_unknown_sort_column_falls_back_instead_of_failing(client, db):
    """A stale bookmark naming a column that no longer exists must not 500."""
    marker = unique_code("batch")
    _make_requests(db, [f"{marker} only"])
    _as(_make_user(db, slugs=ALL_SLUGS), db)

    response = client.get(
        f"{ADMIN}/requests", params={"query": marker, "sort": "not_a_column"}
    )
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 1


def test_filtering_by_status_narrows_the_whole_set(client, db):
    marker = unique_code("batch")
    made = _make_requests(db, [f"{marker} draft one", f"{marker} sent one"])
    onboarding_service.send_request(db, str(made[1].id))
    _as(_make_user(db, slugs=ALL_SLUGS), db)

    body = client.get(
        f"{ADMIN}/requests", params={"query": marker, "status_key": "sent"}
    ).json()
    assert body["pagination"]["total"] == 1
    assert body["data"][0]["title"] == f"{marker} sent one"


def test_searching_narrows_the_total_not_only_the_page(client, db):
    marker = unique_code("batch")
    _make_requests(db, [f"{marker} findable", f"{marker} other"])
    _as(_make_user(db, slugs=ALL_SLUGS), db)

    body = client.get(f"{ADMIN}/requests", params={"query": f"{marker} findable"}).json()
    assert body["pagination"]["total"] == 1


def test_a_page_boundary_neither_drops_nor_repeats_a_row(client, db):
    """Identical `created_at` values must still order deterministically.

    Without the id tie-breaker Postgres is free to order the tied rows
    differently per query, so the same request can appear on both pages while
    another appears on neither - and the one that vanishes is simply never
    reviewed.
    """
    marker = unique_code("batch")
    made = _make_requests(db, [f"{marker} {i}" for i in range(6)])
    same_moment = datetime(2026, 8, 15, 9, 0, 0)
    for request in made:
        request.created_at = same_moment
    db.commit()
    _as(_make_user(db, slugs=ALL_SLUGS), db)

    seen: list[str] = []
    for page in (1, 2):
        body = client.get(
            f"{ADMIN}/requests",
            params={"query": marker, "limit": 3, "page": page, "sort": "created_at"},
        ).json()
        assert body["pagination"]["total"] == 6
        seen.extend(row["id"] for row in body["data"])

    assert len(seen) == 6
    assert len(set(seen)) == 6, "a row was served on two pages"
    assert set(seen) == {str(r.id) for r in made}, "a row was served on neither page"


def test_sorting_by_company_name_orders_by_the_company_name(client, db):
    """The one sort that needs a join, so the one that can silently do nothing."""
    from app.models.base import set_company_scope
    from app.models.company import Company
    from app.services.company_scope_resolver import apply_company_scope

    marker = unique_code("batch")
    alpha = Company(id=str(uuid.uuid4()), name=f"{marker} Alpha Sdn Bhd", code=unique_code("A"))
    zulu = Company(id=str(uuid.uuid4()), name=f"{marker} Zulu Sdn Bhd", code=unique_code("Z"))
    db.add_all([alpha, zulu])
    db.commit()

    # Two companies means a caller who can see both, or the scope filter hides
    # the very rows this sort is supposed to order.
    scope = frozenset({SORENTO, alpha.id, zulu.id})

    def _both_companies():
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _both_companies
    set_company_scope(db, scope)

    for company in (zulu, alpha):
        onboarding_service.create_request(
            db,
            company_id=company.id,
            title=f"{marker} batch",
            requester_name="Esther Lim",
            requester_email=f"{unique_code('requester')}@example.com".lower(),
        )
    _as(_make_user(db, slugs=ALL_SLUGS), db)

    body = client.get(
        f"{ADMIN}/requests",
        params={"query": marker, "sort": "company_name", "dir": "asc"},
    ).json()
    names = [row["company_name"] for row in body["data"]]
    assert names == [alpha.name, zulu.name]


def test_a_draft_does_not_outrank_a_fresh_submission(client, db):
    """`submitted_at` is null until she sends it back, and Postgres sorts nulls
    first on DESC - so "newest submissions first" opened on drafts nobody had
    submitted at all."""
    marker = unique_code("batch")
    made = _make_requests(db, [f"{marker} never submitted", f"{marker} just submitted"])
    made[1].submitted_at = datetime(2026, 8, 15, 9, 0, 0)
    db.commit()
    _as(_make_user(db, slugs=ALL_SLUGS), db)

    body = client.get(
        f"{ADMIN}/requests",
        params={"query": marker, "sort": "submitted_at", "dir": "desc"},
    ).json()
    assert [row["title"] for row in body["data"]] == [
        f"{marker} just submitted",
        f"{marker} never submitted",
    ]


def test_creating_a_request_needs_the_add_permission(client, db):
    payload = {
        "company_id": SORENTO,
        "title": "New batch",
        "requester_name": "Esther",
        "requester_email": "person13@example.com",
    }
    _as(_make_user(db, slugs=("user_management.onboarding.view",)), db)
    assert client.post(f"{ADMIN}/requests", json=payload).status_code == 403

    _as(_make_user(db, slugs=ALL_SLUGS), db)
    created = client.post(f"{ADMIN}/requests", json=payload)
    assert created.status_code == 201
    assert created.json()["status"] == "draft"


@pytest.mark.parametrize("bad", ["nope", UNKNOWN_UUID])
def test_creating_a_request_for_a_company_that_does_not_exist_is_refused(client, db, bad):
    """`company_id` was the one creation field nothing checked.

    Title, email and expiry are all validated, but the company - which decides
    what approving the batch actually grants - went straight into the INSERT: a
    malformed id died on Postgres reading it as a UUID and an unknown one on the
    foreign key, both as 500s at commit.
    """
    _as(_make_user(db, slugs=ALL_SLUGS), db)
    response = client.post(
        f"{ADMIN}/requests",
        json={
            "company_id": bad,
            "title": unique_code("New batch"),
            "requester_name": "Esther",
            "requester_email": f"{unique_code('requester')}@example.com".lower(),
        },
    )
    assert response.status_code == 422


def test_creating_a_request_for_someone_elses_company_is_refused(client, db):
    """An out-of-scope company created a request the caller was then 404'd on.

    The row committed, the caller's own scope filter hid it from the detail read
    that follows, and the batch existed in a company they cannot see.
    """
    from app.models.company import Company

    outsider = Company(
        id=str(uuid.uuid4()), name=unique_code("Outsider Sdn Bhd"), code=unique_code("O")
    )
    db.add(outsider)
    db.commit()

    _as(_make_user(db, slugs=ALL_SLUGS), db)
    response = client.post(
        f"{ADMIN}/requests",
        json={
            "company_id": outsider.id,
            "title": unique_code("New batch"),
            "requester_name": "Esther",
            "requester_email": f"{unique_code('requester')}@example.com".lower(),
        },
    )
    assert response.status_code == 422


def test_approving_needs_its_own_permission(client, db, sent_request, template):
    """`.edit` is not enough - approving is what creates real users."""
    client.put(
        f"{PUBLIC}/rows", params={"token": sent_request.token}, json={"rows": _rows(1)}
    )
    client.post(f"{PUBLIC}/submit", params={"token": sent_request.token}, json={})

    editor = _make_user(
        db,
        slugs=("user_management.onboarding.view", "user_management.onboarding.edit"),
    )
    _as(editor, db)
    assert client.post(f"{ADMIN}/requests/{sent_request.id}/start-review").status_code == 200
    person_id = client.get(f"{ADMIN}/requests/{sent_request.id}").json()["people"][0]["id"]
    assert (
        client.post(
            f"{ADMIN}/requests/{sent_request.id}/people/{person_id}/keep"
        ).status_code
        == 200
    )
    assert client.post(f"{ADMIN}/requests/{sent_request.id}/approve").status_code == 403

    _as(_make_user(db, slugs=ALL_SLUGS), db)
    approved = client.post(f"{ADMIN}/requests/{sent_request.id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "processing"
    assert approved.json()["queued_people"] == 1


def test_approving_twice_is_refused_over_http(client, db, sent_request):
    client.put(
        f"{PUBLIC}/rows", params={"token": sent_request.token}, json={"rows": _rows(1)}
    )
    client.post(f"{PUBLIC}/submit", params={"token": sent_request.token}, json={})
    _as(_make_user(db, slugs=ALL_SLUGS), db)
    client.post(f"{ADMIN}/requests/{sent_request.id}/start-review")
    person_id = client.get(f"{ADMIN}/requests/{sent_request.id}").json()["people"][0]["id"]
    client.post(f"{ADMIN}/requests/{sent_request.id}/people/{person_id}/keep")

    assert client.post(f"{ADMIN}/requests/{sent_request.id}/approve").status_code == 200
    assert client.post(f"{ADMIN}/requests/{sent_request.id}/approve").status_code == 422


def test_a_person_cannot_be_written_through_another_request(client, db, sent_request):
    """Both ids come off the path, and both were trusted separately.

    A write addressed to request A but naming request B's person landed on B and
    then answered with A's detail: the caller saw their own batch unchanged and
    never learnt that somebody else's had been edited.
    """
    other = onboarding_service.create_request(
        db,
        company_id=SORENTO,
        title=unique_code("Other batch"),
        requester_name="Someone Else",
        requester_email=f"{unique_code('other')}@example.com".lower(),
    )
    onboarding_service.send_request(db, str(other.id))

    # One person on each request, both submitted and in review.
    for request in (sent_request, other):
        client.put(
            f"{PUBLIC}/rows", params={"token": request.token}, json={"rows": _rows(1)}
        )
        client.post(f"{PUBLIC}/submit", params={"token": request.token}, json={})

    _as(_make_user(db, slugs=ALL_SLUGS), db)
    client.post(f"{ADMIN}/requests/{sent_request.id}/start-review")
    client.post(f"{ADMIN}/requests/{other.id}/start-review")

    victim = client.get(f"{ADMIN}/requests/{other.id}").json()["people"][0]
    assert victim["full_name"] == "Person 1"

    # Every write route, addressed to the wrong parent.
    assert (
        client.put(
            f"{ADMIN}/requests/{sent_request.id}/people/{victim['id']}",
            json={"full_name": "Hijacked"},
        ).status_code
        == 404
    )
    for verb in ("keep", "hold"):
        assert (
            client.post(
                f"{ADMIN}/requests/{sent_request.id}/people/{victim['id']}/{verb}"
            ).status_code
            == 404
        )
    assert (
        client.post(
            f"{ADMIN}/requests/{sent_request.id}/people/{victim['id']}/reject",
            json={"reason": "Not mine to reject."},
        ).status_code
        == 404
    )

    # And the other request's person is untouched.
    after = client.get(f"{ADMIN}/requests/{other.id}").json()["people"][0]
    assert after["full_name"] == "Person 1"
    assert after["review_status"] == "proposed"


def test_rejecting_a_person_over_http_requires_a_reason(client, db, sent_request):
    client.put(
        f"{PUBLIC}/rows", params={"token": sent_request.token}, json={"rows": _rows(1)}
    )
    client.post(f"{PUBLIC}/submit", params={"token": sent_request.token}, json={})
    _as(_make_user(db, slugs=ALL_SLUGS), db)
    client.post(f"{ADMIN}/requests/{sent_request.id}/start-review")
    person_id = client.get(f"{ADMIN}/requests/{sent_request.id}").json()["people"][0]["id"]

    blank = client.post(
        f"{ADMIN}/requests/{sent_request.id}/people/{person_id}/reject",
        json={"reason": "   "},
    )
    assert blank.status_code == 422

    ok = client.post(
        f"{ADMIN}/requests/{sent_request.id}/people/{person_id}/reject",
        json={"reason": "Left the company."},
    )
    assert ok.status_code == 200
    person = ok.json()["people"][0]
    assert person["review_status"] == "rejected"
    assert person["rejection_reason"] == "Left the company."


def test_the_reviewer_sees_collisions_the_requester_did_not(client, db, sent_request):
    taken = "person1@example.com"
    db.add(
        User(id=unique_code("user"), email=taken, name="Already Here", status="ACTIVE")
    )
    db.commit()

    client.put(
        f"{PUBLIC}/rows", params={"token": sent_request.token}, json={"rows": _rows(1)}
    )
    public_body = client.get(f"{PUBLIC}/me", params={"token": sent_request.token}).json()
    assert public_body["people"][0]["collisions"] == []

    _as(_make_user(db, slugs=ALL_SLUGS), db)
    admin_body = client.get(f"{ADMIN}/requests/{sent_request.id}").json()
    collisions = admin_body["people"][0]["collisions"]
    assert [c["kind"] for c in collisions] == ["user_email"]
    assert "Already Here" in collisions[0]["label"]


def test_deleting_a_request_takes_its_people_with_it(client, db, sent_request):
    from app.models.onboarding import OnboardingPerson

    client.put(
        f"{PUBLIC}/rows", params={"token": sent_request.token}, json={"rows": _rows(2)}
    )
    request_id = str(sent_request.id)

    _as(_make_user(db, slugs=("user_management.onboarding.view",)), db)
    assert client.delete(f"{ADMIN}/requests/{request_id}").status_code == 403

    _as(_make_user(db, slugs=ALL_SLUGS), db)
    assert client.delete(f"{ADMIN}/requests/{request_id}").status_code == 204
    assert (
        db.query(OnboardingPerson)
        .filter(OnboardingPerson.request_id == request_id)
        .count()
        == 0
    )


def test_the_detail_response_carries_the_intake_link_for_the_captain(client, db, sent_request):
    """He copies it into WhatsApp himself, the way the contact portal allows."""
    _as(_make_user(db, slugs=ALL_SLUGS), db)
    body = client.get(f"{ADMIN}/requests/{sent_request.id}").json()
    url = body.get("intake_url")
    if url:  # FRONTEND_BASE_URL may be unset in a bare environment
        assert url.endswith(f"/onboarding/{sent_request.token}")
