"""IDEA-CRM-SIDE: gateway additions (AC-A-01..A-10), red tests written BEFORE the implementation.

Plan: documentation/plans/ideation/PLAN-idea-crm-side-13oct.md. UAC:
documentation/plans/ideation/idea-crm-side-13oct-acceptance-criteria.md.

CONTRACT the coder builds to:

* ``app/api/v1/ideation/ideas.py`` create and update forward ``title, problem, desiredOutcome,
  useCases, currentWorkaround, rawText`` (legacy ``proposedSolution, impact, department`` still
  forwarded when sent); anything else is dropped.
* ``GET /ideas/{id}/similar`` relays ss ``GET /embed/ideas/{id}/similar`` (view permission).
* ``POST /ideas/merge`` forwards a validated ``fields`` (see AC-A-04).
* ``ideation_gateway_service.call_portal(db, user, method, path, *, params=None, json=None)``
  exchanges the embed token at ss ``POST /portal/ideas/session/embed`` json ``{"token": <embed>}``,
  caches the portal token per user, re-exchanges once on a portal 401 (dropping portal AND embed
  token), and a second 401 is a 502. ``clear_token_cache()`` clears portal tokens too.
* Router ``/api/v1/ideation/portal`` (``app/api/v1/ideation/portal.py``).

Postgres only (tests/_pg_fixture.py via the shared ``env`` fixture), ss faked at httpx.
"""
from __future__ import annotations

import uuid

import pytest

# MUST be the first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402,F401

from tests._ideation_ss_fake import blank_embed_settings, message_of
from tests.test_ideation_gateway import (  # noqa: F401  (env is a fixture, reused as-is)
    BASE,
    COMMENT,
    IDEA,
    IDEA_B,
    MANAGE,
    UNAVAILABLE,
    UNREACHABLE,
    VIEW,
    _ss_called,
    env,
)

PORTAL = "/portal"
SS_PORTAL = "/portal/ideas"
IDEA_C = "7c1d0b7e-0000-4000-8000-00000000a003"
FIELD_KEYS = ("title", "problem", "desiredOutcome", "useCases", "currentWorkaround", "rawText")


def _control(e, method, valid_path, **kw):
    """Positive control: the route exists and works with a valid id (so a later 422 is the id
    check, not a missing route), then forget the traffic."""
    from app.services import ideation_gateway_service as gw

    assert e.req(method, valid_path, **kw).status_code < 400, "control call failed: route missing?"
    e.fake.calls.clear()
    e.fake.session_calls.clear()
    e.fake.portal_session_calls.clear()
    gw.clear_token_cache()


def _portal_calls(e):
    return [c for c in e.fake.calls if c["path"].startswith("/portal/")]


# --------------------------------------------------------------------------- #
# AC-A-01 / A-02 field whitelist                                               #
# --------------------------------------------------------------------------- #
_NEW_FIELDS = {
    "title": "Faster quotes",
    "problem": "Quotes take a day",
    "desiredOutcome": "Same hour quotes",
    "useCases": ["Dealer asks", "Rush order"],
    "currentWorkaround": "Spreadsheet",
    "rawText": "original message",
}


def test_a01_create_forwards_the_new_fields_and_drops_unknown_ones(env):
    """AC-A-01"""
    env.fake.route("POST", "/embed/ideas", status=201, json_body={"id": IDEA})
    resp = env.req("POST", "/ideas", json={**_NEW_FIELDS, "productId": "other", "foo": "bar"})
    assert resp.status_code == 201, resp.text
    assert env.fake.calls[0]["json"] == _NEW_FIELDS


def test_a01_create_still_forwards_the_legacy_fields_when_sent(env):
    """AC-A-01 (legacy clients)"""
    env.fake.route("POST", "/embed/ideas", status=201, json_body={"id": IDEA})
    sent = {"problem": "p", "proposedSolution": "s", "impact": "i", "department": "d"}
    env.req("POST", "/ideas", json={**sent, "foo": 1})
    assert env.fake.calls[0]["json"] == sent


def test_a02_patch_forwards_the_new_fields_and_drops_unknown_ones(env):
    """AC-A-02"""
    env.fake.route("PATCH", f"/embed/ideas/{IDEA}", json_body={"id": IDEA})
    resp = env.req("PATCH", f"/ideas/{IDEA}", json={**_NEW_FIELDS, "productId": "other", "foo": "bar"})
    assert resp.status_code == 200, resp.text
    assert env.fake.calls[0]["json"] == _NEW_FIELDS


def test_a02_patch_forwards_only_what_was_sent(env):
    """AC-A-02: a partial edit does not invent keys."""
    env.fake.route("PATCH", f"/embed/ideas/{IDEA}", json_body={"id": IDEA})
    env.req("PATCH", f"/ideas/{IDEA}", json={"desiredOutcome": "x"})
    assert env.fake.calls[0]["json"] == {"desiredOutcome": "x"}


# --------------------------------------------------------------------------- #
# AC-A-03 similar                                                              #
# --------------------------------------------------------------------------- #
def test_a03_similar_relays_ss_unchanged(env):
    """AC-A-03"""
    body = {"available": True, "ideas": [{"id": IDEA_B, "ideaNumber": "IDEA-0002", "title": "Same"}]}
    env.fake.route("GET", f"/embed/ideas/{IDEA}/similar", json_body=body)
    resp = env.req("GET", f"/ideas/{IDEA}/similar")
    assert resp.status_code == 200, resp.text
    assert resp.json() == body
    assert env.fake.calls[0]["path"] == f"/embed/ideas/{IDEA}/similar"
    assert env.fake.calls[0]["headers"]["authorization"] == f"Bearer {env.fake.tokens[0]}"


def test_a03_similar_relays_available_false(env):
    env.fake.route("GET", f"/embed/ideas/{IDEA}/similar", json_body={"available": False, "ideas": []})
    assert env.req("GET", f"/ideas/{IDEA}/similar").json() == {"available": False, "ideas": []}


def test_a03_similar_needs_view_and_ss_is_not_called(env):
    """AC-A-03"""
    env.allow.clear()
    assert env.req("GET", f"/ideas/{IDEA}/similar").status_code == 403
    assert not _ss_called(env)


def test_a03_similar_view_only_user_is_allowed(env):
    env.allow.discard(MANAGE)
    env.fake.route("GET", f"/embed/ideas/{IDEA}/similar", json_body={"available": True, "ideas": []})
    assert env.req("GET", f"/ideas/{IDEA}/similar").status_code == 200


@pytest.mark.parametrize("bad", ["not-a-uuid", "%2E%2E", "%3F"])
def test_a03_similar_non_uuid_is_422_and_ss_is_not_called(env, bad):
    resp = env.req("GET", f"/ideas/{bad}/similar")
    assert resp.status_code == 422, resp.text
    assert not _ss_called(env)


# --------------------------------------------------------------------------- #
# AC-A-04 merge fields                                                         #
# --------------------------------------------------------------------------- #
def _merge(env, fields=...):
    body = {"survivorId": IDEA, "ideaIds": [IDEA_B]}
    if fields is not ...:
        body["fields"] = fields
    return env.req("POST", "/ideas/merge", json=body)


def test_a04_valid_fields_are_forwarded(env):
    """AC-A-04"""
    env.fake.route("POST", "/embed/ideas/merge", json_body={"id": IDEA})
    fields = {"title": [IDEA_B], "useCases": [IDEA, IDEA_B]}
    assert _merge(env, fields).status_code == 200
    assert env.fake.calls[0]["json"] == {"survivorId": IDEA, "ideaIds": [IDEA_B], "fields": fields}


@pytest.mark.parametrize("key", FIELD_KEYS)
def test_a04_every_allowed_key_is_accepted(env, key):
    env.fake.route("POST", "/embed/ideas/merge", json_body={"id": IDEA})
    assert _merge(env, {key: [IDEA]}).status_code == 200
    assert env.fake.calls[0]["json"]["fields"] == {key: [IDEA]}


def test_a04_without_fields_the_body_is_survivor_and_ids_only(env):
    env.fake.route("POST", "/embed/ideas/merge", json_body={"id": IDEA})
    _merge(env)
    assert env.fake.calls[0]["json"] == {"survivorId": IDEA, "ideaIds": [IDEA_B]}


@pytest.mark.parametrize(
    "fields",
    [
        {"productId": [IDEA]},
        {"title": []},
        {"title": IDEA},
        {"title": [IDEA, "not-a-uuid"]},
        {"title": [123]},
        {"title": None},
        ["title"],
        "title",
        {"title": [IDEA], "productId": [IDEA]},
        {"title": [IDEA], "problem": []},
    ],
    ids=["unknown-key", "empty-list", "non-list", "non-uuid", "int-id", "null", "list-fields", "str-fields",
         "valid-plus-unknown-key", "valid-plus-empty-list"],
)
def test_a04_invalid_fields_are_422_and_ss_is_not_called(env, fields):
    resp = _merge(env, fields)
    assert resp.status_code == 422, resp.text
    assert not _ss_called(env)


# --------------------------------------------------------------------------- #
# AC-A-05 portal session                                                       #
# --------------------------------------------------------------------------- #
def test_a05_first_portal_call_exchanges_the_embed_token_and_carries_the_portal_bearer(env):
    """AC-A-05"""
    env.fake.route("GET", f"{SS_PORTAL}/roadmap", json_body={"columns": []})
    resp = env.req("GET", f"{PORTAL}/roadmap")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"columns": []}
    assert env.fake.portal_session_calls == [{"token": env.fake.tokens[0]}]
    call = _portal_calls(env)[0]
    assert call["headers"]["authorization"] == f"Bearer {env.fake.portal_tokens[0]}"


def test_a05_second_portal_call_reuses_the_cached_portal_token(env):
    env.fake.route("GET", f"{SS_PORTAL}/roadmap", json_body={"columns": []})
    env.fake.route("GET", f"{SS_PORTAL}/changelog", json_body={"items": []})
    env.req("GET", f"{PORTAL}/roadmap")
    env.req("GET", f"{PORTAL}/changelog")
    assert len(env.fake.portal_session_calls) == 1
    assert len(env.fake.session_calls) == 1
    assert {c["headers"]["authorization"] for c in _portal_calls(env)} == {f"Bearer {env.fake.portal_tokens[0]}"}


def test_a05_portal_token_is_cached_per_user(env):
    env.req("GET", f"{PORTAL}/roadmap")
    env.login(name="Bob Lim")
    env.req("GET", f"{PORTAL}/roadmap")
    assert len(env.fake.portal_session_calls) == 2


def test_a05_service_call_portal_returns_the_response_and_caches(env):
    from app.services import ideation_gateway_service as gw

    env.fake.route("GET", f"{SS_PORTAL}/roadmap", json_body={"columns": []})
    r1 = gw.call_portal(env.db, env.user, "GET", "/portal/ideas/roadmap", params={"mine": "true"})
    r2 = gw.call_portal(env.db, env.user, "GET", "/portal/ideas/roadmap")
    assert r1.status_code == r2.status_code == 200
    assert _portal_calls(env)[0]["query"] == {"mine": "true"}
    assert len(env.fake.portal_session_calls) == 1


def test_a05_clear_token_cache_also_clears_portal_tokens(env):
    from app.services import ideation_gateway_service as gw

    gw.call_portal(env.db, env.user, "GET", "/portal/ideas/roadmap")
    gw.clear_token_cache()
    gw.call_portal(env.db, env.user, "GET", "/portal/ideas/roadmap")
    assert len(env.fake.portal_session_calls) == 2


def test_a05_the_portal_token_never_reaches_a_response_body(env):
    env.fake.route("GET", f"{SS_PORTAL}/roadmap", json_body={"columns": []})
    resp = env.req("GET", f"{PORTAL}/roadmap")
    assert env.fake.portal_tokens[0] not in resp.text
    assert env.fake.portal_tokens[0] not in str(dict(resp.headers))


# --------------------------------------------------------------------------- #
# AC-A-06 portal 401                                                           #
# --------------------------------------------------------------------------- #
def test_a06_portal_401_reexchanges_once_and_retries_once(env):
    env.fake.route("GET", f"{SS_PORTAL}/roadmap", json_body={"columns": []})
    assert env.req("GET", f"{PORTAL}/roadmap").status_code == 200  # warm
    env.fake.reject_next_portal(1)
    resp = env.req("GET", f"{PORTAL}/roadmap")
    assert resp.status_code == 200, resp.text
    assert len(env.fake.portal_session_calls) == 2
    # both tokens were dropped: the embed token was minted afresh and is what was exchanged
    assert len(env.fake.session_calls) == 2
    assert env.fake.portal_session_calls[1] == {"token": env.fake.tokens[1]}
    calls = _portal_calls(env)
    assert len(calls) == 3  # warm, the 401, exactly one retry
    assert calls[1]["headers"]["authorization"] == f"Bearer {env.fake.portal_tokens[0]}"
    assert calls[2]["headers"]["authorization"] == f"Bearer {env.fake.portal_tokens[1]}"


def test_a06_second_portal_401_is_502_unreachable(env):
    env.req("GET", f"{PORTAL}/roadmap")
    env.fake.reject_next_portal(2)
    resp = env.req("GET", f"{PORTAL}/roadmap")
    assert resp.status_code == 502, resp.text
    assert UNREACHABLE in message_of(resp)
    assert len(_portal_calls(env)) == 3  # warm + 401 + one retry, no more
    # the poisoned entry is gone: the next request exchanges afresh and succeeds
    assert env.req("GET", f"{PORTAL}/roadmap").status_code == 200
    assert len(env.fake.portal_session_calls) == 3


def test_a06_failed_exchange_is_502(env):
    env.fake.portal_session_status = 500
    resp = env.req("GET", f"{PORTAL}/roadmap")
    assert resp.status_code == 502, resp.text
    assert UNREACHABLE in message_of(resp)


# --------------------------------------------------------------------------- #
# AC-A-07 portal reads                                                         #
# --------------------------------------------------------------------------- #
_READS = [
    ("roadmap", f"{PORTAL}/roadmap", f"{SS_PORTAL}/roadmap"),
    ("list", f"{PORTAL}/ideas", f"{SS_PORTAL}"),
    ("changelog", f"{PORTAL}/changelog", f"{SS_PORTAL}/changelog"),
    ("get", f"{PORTAL}/ideas/{IDEA}", f"{SS_PORTAL}/{IDEA}"),
    ("voters", f"{PORTAL}/ideas/{IDEA}/voters", f"{SS_PORTAL}/{IDEA}/voters"),
    ("activity", f"{PORTAL}/ideas/{IDEA}/activity", f"{SS_PORTAL}/{IDEA}/activity"),
    ("comments", f"{PORTAL}/ideas/{IDEA}/comments", f"{SS_PORTAL}/{IDEA}/comments"),
]
_READ_PARAMS = [pytest.param(*r, id=r[0]) for r in _READS]


@pytest.mark.parametrize("name, path, ss", _READ_PARAMS)
def test_a07_read_routes_forward_to_the_ss_portal_path(env, name, path, ss):
    env.fake.route("GET", ss, json_body={"ok": name})
    resp = env.req("GET", path)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": name}
    calls = _portal_calls(env)
    assert [(c["method"], c["path"]) for c in calls] == [("GET", ss)]


@pytest.mark.parametrize("name, path, ss", _READ_PARAMS)
def test_a07_read_routes_403_without_view_and_ss_is_not_called(env, name, path, ss):
    env.allow.clear()
    assert env.req("GET", path).status_code == 403
    assert not _ss_called(env) and not env.fake.portal_session_calls


@pytest.mark.parametrize("name, path, ss", _READ_PARAMS)
def test_a07_read_routes_work_for_a_view_only_user(env, name, path, ss):
    env.allow.discard(MANAGE)
    env.fake.route("GET", ss, json_body={})
    assert env.req("GET", path).status_code == 200


def test_a07_reads_also_accept_an_api_key_principal(env):
    from fastapi import HTTPException

    from app.dependencies import get_current_user

    def _no_session():
        raise HTTPException(status_code=401, detail="no staff session")

    app.dependency_overrides[get_current_user] = _no_session
    env.fake.route("GET", f"{SS_PORTAL}/roadmap", json_body={"columns": []})
    assert env.req("GET", f"{PORTAL}/roadmap").status_code == 200


def _capture(e, method, ss_path):
    import httpx

    seen: list[list[tuple[str, str]]] = []

    def _h(request):
        seen.append(list(request.url.params.multi_items()))
        return httpx.Response(200, json={}, request=request)

    e.fake.route(method, ss_path, status=200)
    e.fake.routes[(method, ss_path)] = _h
    return seen


def test_a07_roadmap_forwards_mine(env):
    seen = _capture(env, "GET", f"{SS_PORTAL}/roadmap")
    env.req("GET", f"{PORTAL}/roadmap?mine=true&productId=x&foo=1")
    assert seen == [[("mine", "true")]]


def test_a07_list_forwards_only_the_listed_params_and_repeats_column(env):
    seen = _capture(env, "GET", SS_PORTAL)
    env.req(
        "GET",
        f"{PORTAL}/ideas?q=quote&column=new&column=planned&sort=top&mine=true&includeClosed=true"
        "&page=2&pageSize=25&productId=other&foo=bar",
    )
    assert len(seen) == 1
    got = seen[0]
    assert sorted(k for k, _ in got) == sorted(
        ["q", "column", "column", "sort", "mine", "includeClosed", "page", "pageSize"]
    )
    assert [v for k, v in got if k == "column"] == ["new", "planned"]
    d = dict(got)
    assert d["q"] == "quote" and d["sort"] == "top" and d["mine"] == "true"
    assert d["includeClosed"] == "true" and d["page"] == "2" and d["pageSize"] == "25"


def test_a07_productid_is_never_forwarded_on_any_read(env):
    for _n, path, ss in _READS:
        seen = _capture(env, "GET", ss)
        env.req("GET", f"{path}?productId=other")
        assert seen and all(k != "productId" for k, _ in seen[0]), path


def test_a07_activity_forwards_order(env):
    seen = _capture(env, "GET", f"{SS_PORTAL}/{IDEA}/activity")
    env.req("GET", f"{PORTAL}/ideas/{IDEA}/activity?order=asc&productId=x")
    assert seen == [[("order", "asc")]]


@pytest.mark.parametrize("bad", ["not-a-uuid", "%2E%2E", "%3F", "%23"])
@pytest.mark.parametrize("suffix", ["", "/voters", "/activity", "/comments"])
def test_a07_non_uuid_idea_id_is_422_and_ss_is_not_called(env, bad, suffix):
    _control(env, "GET", f"{PORTAL}/ideas/{IDEA}{suffix}")
    resp = env.req("GET", f"{PORTAL}/ideas/{bad}{suffix}")
    assert resp.status_code in (404, 422), resp.text
    assert not _ss_called(env) and not env.fake.portal_session_calls


# --------------------------------------------------------------------------- #
# AC-A-08 portal writes (view, staff session)                                  #
# --------------------------------------------------------------------------- #
def test_a08_vote_toggles_through_the_portal(env):
    env.fake.route("POST", f"{SS_PORTAL}/{IDEA}/vote", json_body={"myVote": True, "upvotes": 3})
    resp = env.req("POST", f"{PORTAL}/ideas/{IDEA}/vote")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"myVote": True, "upvotes": 3}
    assert [(c["method"], c["path"]) for c in _portal_calls(env)] == [("POST", f"{SS_PORTAL}/{IDEA}/vote")]


def test_a08_comment_post_forwards_body_and_parent_only(env):
    env.fake.route("POST", f"{SS_PORTAL}/{IDEA}/comments", status=201, json_body={"id": COMMENT, "body": "hi"})
    resp = env.req(
        "POST", f"{PORTAL}/ideas/{IDEA}/comments",
        json={"body": "hi", "parentId": COMMENT, "authorName": "Mallory", "isMine": True},
    )
    assert resp.status_code == 201, resp.text
    assert _portal_calls(env)[0]["json"] == {"body": "hi", "parentId": COMMENT}


def test_a08_comment_post_without_parent_forwards_body_only(env):
    env.fake.route("POST", f"{SS_PORTAL}/{IDEA}/comments", status=201, json_body={"id": COMMENT})
    env.req("POST", f"{PORTAL}/ideas/{IDEA}/comments", json={"body": "hi"})
    assert _portal_calls(env)[0]["json"] == {"body": "hi"}


@pytest.mark.parametrize("payload", [{"body": ""}, {"body": "   "}, {}, {"body": 5}])
def test_a08_empty_comment_is_422_and_ss_is_not_called(env, payload):
    resp = env.req("POST", f"{PORTAL}/ideas/{IDEA}/comments", json=payload)
    assert resp.status_code == 422, resp.text
    assert not _portal_calls(env)


def test_a08_comment_author_email_is_masked(env):
    row = {"id": COMMENT, "authorName": "alex.staff@example.test", "body": "hi"}
    env.fake.route("POST", f"{SS_PORTAL}/{IDEA}/comments", status=201, json_body=row)
    resp = env.req("POST", f"{PORTAL}/ideas/{IDEA}/comments", json={"body": "hi"})
    assert resp.status_code == 201
    assert "@" not in resp.text and "example.test" not in resp.text
    assert resp.json()["authorName"] == "Sorento staff"
    assert resp.json()["body"] == "hi"


def test_a08_a_plain_author_name_is_untouched(env):
    env.fake.route("POST", f"{SS_PORTAL}/{IDEA}/comments", status=201,
                   json_body={"id": COMMENT, "authorName": "Alex Staff"})
    assert env.req("POST", f"{PORTAL}/ideas/{IDEA}/comments", json={"body": "hi"}).json()["authorName"] == "Alex Staff"


def test_a08_like_a_comment(env):
    env.fake.route("POST", f"{SS_PORTAL}/{IDEA}/comments/{COMMENT}/like", json_body={"likeCount": 1, "likedByMe": True})
    resp = env.req("POST", f"{PORTAL}/ideas/{IDEA}/comments/{COMMENT}/like")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"likeCount": 1, "likedByMe": True}
    assert _portal_calls(env)[0]["path"] == f"{SS_PORTAL}/{IDEA}/comments/{COMMENT}/like"


_WRITES = [
    pytest.param("POST", f"{PORTAL}/ideas/{IDEA}/vote", {}, id="vote"),
    pytest.param("POST", f"{PORTAL}/ideas/{IDEA}/comments", {"json": {"body": "hi"}}, id="comment"),
    pytest.param("POST", f"{PORTAL}/ideas/{IDEA}/comments/{COMMENT}/like", {}, id="like"),
]


@pytest.mark.parametrize("method, path, kw", _WRITES)
def test_a08_writes_403_without_view(env, method, path, kw):
    env.allow.clear()
    assert env.req(method, path, **kw).status_code == 403
    assert not _ss_called(env)


@pytest.mark.parametrize("method, path, kw", _WRITES)
def test_a08_writes_need_a_staff_session_not_an_api_key(env, method, path, kw):
    from fastapi import HTTPException

    from app.dependencies import get_current_user

    def _no_session():
        raise HTTPException(status_code=401, detail="no staff session")

    app.dependency_overrides[get_current_user] = _no_session
    assert env.req(method, path, **kw).status_code == 401
    assert not _portal_calls(env)


@pytest.mark.parametrize("bad", ["not-a-uuid", "%2E%2E"])
def test_a08_write_ids_must_be_uuids(env, bad):
    _control(env, "POST", f"{PORTAL}/ideas/{IDEA}/vote")
    _control(env, "POST", f"{PORTAL}/ideas/{IDEA}/comments/{COMMENT}/like")
    for method, path, kw in (
        ("POST", f"{PORTAL}/ideas/{bad}/vote", {}),
        ("POST", f"{PORTAL}/ideas/{bad}/comments", {"json": {"body": "hi"}}),
        ("POST", f"{PORTAL}/ideas/{IDEA}/comments/{bad}/like", {}),
        ("POST", f"{PORTAL}/ideas/{bad}/comments/{COMMENT}/like", {}),
    ):
        resp = env.req(method, path, **kw)
        assert resp.status_code in (404, 422), (path, resp.text)
    assert not _ss_called(env)


# --------------------------------------------------------------------------- #
# AC-A-09 manage routes                                                        #
# --------------------------------------------------------------------------- #
def test_a09_hidden_is_forwarded_for_a_manager(env):
    env.fake.route("PUT", f"{SS_PORTAL}/{IDEA}/hidden", json_body={"portalHidden": True})
    resp = env.req("PUT", f"{PORTAL}/ideas/{IDEA}/hidden", json={"hidden": True, "extra": 1})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"portalHidden": True}
    assert _portal_calls(env)[0]["json"] == {"hidden": True}


def test_a09_note_is_forwarded_for_a_manager(env):
    env.fake.route("PUT", f"{SS_PORTAL}/{IDEA}/note", json_body={"note": "Shipped"})
    resp = env.req("PUT", f"{PORTAL}/ideas/{IDEA}/note", json={"note": "Shipped", "extra": 1})
    assert resp.status_code == 200, resp.text
    assert _portal_calls(env)[0]["json"] == {"note": "Shipped"}


def test_a09_note_of_exactly_500_chars_is_accepted(env):
    env.fake.route("PUT", f"{SS_PORTAL}/{IDEA}/note", json_body={})
    assert env.req("PUT", f"{PORTAL}/ideas/{IDEA}/note", json={"note": "x" * 500}).status_code == 200


_MANAGE_WRITES = [
    pytest.param("PUT", f"{PORTAL}/ideas/{IDEA}/hidden", {"json": {"hidden": True}}, id="hidden"),
    pytest.param("PUT", f"{PORTAL}/ideas/{IDEA}/note", {"json": {"note": "n"}}, id="note"),
]


@pytest.mark.parametrize("method, path, kw", _MANAGE_WRITES)
def test_a09_403_without_manage_and_ss_not_called(env, method, path, kw):
    env.allow.discard(MANAGE)
    assert env.req(method, path, **kw).status_code == 403
    assert not _ss_called(env) and not env.fake.portal_session_calls


@pytest.mark.parametrize("method, path, kw", _MANAGE_WRITES)
def test_a09_manage_writes_need_a_staff_session(env, method, path, kw):
    from fastapi import HTTPException

    from app.dependencies import get_current_user

    def _no_session():
        raise HTTPException(status_code=401, detail="no staff session")

    app.dependency_overrides[get_current_user] = _no_session
    assert env.req(method, path, **kw).status_code == 401
    assert not _portal_calls(env)


@pytest.mark.parametrize(
    "path_suffix, payload",
    [
        ("hidden", {"hidden": "maybe"}),
        ("hidden", {"hidden": None}),
        ("hidden", {"hidden": [True]}),
        ("hidden", {}),
        ("note", {"note": "x" * 501}),
        ("note", {"note": 123}),
        ("note", {"note": None}),
        ("note", {}),
    ],
)
def test_a09_bad_types_or_too_long_are_422_and_ss_is_not_called(env, path_suffix, payload):
    resp = env.req("PUT", f"{PORTAL}/ideas/{IDEA}/{path_suffix}", json=payload)
    assert resp.status_code == 422, resp.text
    assert not _ss_called(env)


@pytest.mark.parametrize("suffix, payload", [("hidden", {"hidden": True}), ("note", {"note": "n"})])
def test_a09_non_uuid_id_is_422(env, suffix, payload):
    resp = env.req("PUT", f"{PORTAL}/ideas/not-a-uuid/{suffix}", json=payload)
    assert resp.status_code == 422, resp.text
    assert not _ss_called(env)


# --------------------------------------------------------------------------- #
# AC-A-10 dormant deployment                                                   #
# --------------------------------------------------------------------------- #
_ALL_PORTAL = [
    ("GET", f"{PORTAL}/roadmap", {}),
    ("GET", f"{PORTAL}/ideas", {}),
    ("GET", f"{PORTAL}/changelog", {}),
    ("GET", f"{PORTAL}/ideas/{IDEA}", {}),
    ("GET", f"{PORTAL}/ideas/{IDEA}/voters", {}),
    ("GET", f"{PORTAL}/ideas/{IDEA}/activity", {}),
    ("GET", f"{PORTAL}/ideas/{IDEA}/comments", {}),
    ("POST", f"{PORTAL}/ideas/{IDEA}/vote", {}),
    ("POST", f"{PORTAL}/ideas/{IDEA}/comments", {"json": {"body": "hi"}}),
    ("POST", f"{PORTAL}/ideas/{IDEA}/comments/{COMMENT}/like", {}),
    ("PUT", f"{PORTAL}/ideas/{IDEA}/hidden", {"json": {"hidden": True}}),
    ("PUT", f"{PORTAL}/ideas/{IDEA}/note", {"json": {"note": "n"}}),
]


@pytest.mark.parametrize("method, path, kw", _ALL_PORTAL, ids=[f"{m} {p}" for m, p, _ in _ALL_PORTAL])
def test_a10_unconfigured_deployment_answers_404_on_every_portal_route(env, monkeypatch, method, path, kw):
    blank_embed_settings(monkeypatch)
    resp = env.req(method, path, **kw)
    assert resp.status_code == 404, resp.text
    assert UNAVAILABLE in message_of(resp)  # not a bare "Not Found" from a missing route
    assert not _ss_called(env)


def test_a10_service_raises_404_when_not_configured(env, monkeypatch):
    from app.services import ideation_gateway_service as gw
    from app.services.error_handler import AppException

    blank_embed_settings(monkeypatch)
    with pytest.raises(AppException) as exc:
        gw.call_portal(env.db, env.user, "GET", "/portal/ideas/roadmap")
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# Fix round 1: staff-email masking on every read that returns a person's name   #
# --------------------------------------------------------------------------- #
_EMAIL = "alex.staff@example.test"


def test_fr_portal_voters_mask_an_email_name(env):
    env.fake.route("GET", f"{SS_PORTAL}/{IDEA}/voters", json_body=[
        {"name": _EMAIL, "role": "Sales", "isMe": False}, {"name": "Jane Lim", "role": None, "isMe": True}])
    resp = env.req("GET", f"{PORTAL}/ideas/{IDEA}/voters")
    assert "@" not in resp.text
    assert [v["name"] for v in resp.json()] == ["Sorento staff", "Jane Lim"]
    assert resp.json()[0]["role"] == "Sales"


def test_fr_portal_comments_mask_every_row(env):
    env.fake.route("GET", f"{SS_PORTAL}/{IDEA}/comments", json_body=[
        {"id": COMMENT, "authorName": _EMAIL, "body": "a"},
        {"id": IDEA_B, "authorName": "bob@example.test", "body": "b"},
        {"id": IDEA_C, "authorName": "Plain Name", "body": "mail me at x@y.test"}])
    resp = env.req("GET", f"{PORTAL}/ideas/{IDEA}/comments")
    assert [c["authorName"] for c in resp.json()] == ["Sorento staff", "Sorento staff", "Plain Name"]
    # Only the name keys are masked: an address inside a comment's text is the comment.
    assert [c["body"] for c in resp.json()] == ["a", "b", "mail me at x@y.test"]


def _ss_main_activity() -> list[dict]:
    """ss main 812dac2b `PortalActivityOut` (ss `modules/ideation/schemas.py:902`), crew real-SS check 13 Oct."""
    return [
        {"kind": "status", "at": "2026-10-01T00:00:00Z", "by": _EMAIL, "source": "staff",
         "fromLabel": "Captured", "toLabel": "Triaged", "toColor": "blue",
         "comment": None, "replies": [], "commentId": None},
        {"kind": "comment", "at": "2026-10-02T00:00:00Z", "by": "bob@example.test", "source": "staff",
         "fromLabel": None, "toLabel": None, "toColor": None,
         "comment": {"id": COMMENT, "authorName": "carol@example.test", "body": "hello x@y.test"},
         "replies": [{"id": IDEA_B, "authorName": "dan@example.test", "body": "re"},
                     {"id": IDEA_C, "authorName": "Plain Name", "body": "re2"}],
         "commentId": COMMENT},
        {"kind": "vote", "at": "2026-10-03T00:00:00Z", "by": "Jane Lim", "source": "portal",
         "fromLabel": None, "toLabel": None, "toColor": None, "comment": None, "replies": [], "commentId": None},
    ]


def test_crew_portal_activity_masks_by_and_nested_comment_authors(env):
    """Crew real-SS check: ss sends `by` and nested `comment.authorName` / `replies[].authorName`,
    never `actorName`; every one of them is masked, everything else relays unchanged."""
    env.fake.route("GET", f"{SS_PORTAL}/{IDEA}/activity", json_body=_ss_main_activity())
    resp = env.req("GET", f"{PORTAL}/ideas/{IDEA}/activity")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["by"] for r in rows] == ["Sorento staff", "Sorento staff", "Jane Lim"]
    assert rows[1]["comment"]["authorName"] == "Sorento staff"
    assert [r["authorName"] for r in rows[1]["replies"]] == ["Sorento staff", "Plain Name"]
    assert rows[0]["fromLabel"] == "Captured" and rows[0]["toLabel"] == "Triaged"
    assert rows[1]["comment"]["body"] == "hello x@y.test" and rows[1]["commentId"] == COMMENT


def test_crew_portal_voters_keep_a_null_dealer_name(env):
    env.fake.route("GET", f"{SS_PORTAL}/{IDEA}/voters", json_body=[{"name": None, "role": "Dealer", "isMe": False}])
    resp = env.req("GET", f"{PORTAL}/ideas/{IDEA}/voters")
    assert resp.json() == [{"name": None, "role": "Dealer", "isMe": False}]


def test_fr_embed_comments_list_masks_every_row(env):
    env.fake.route("GET", f"/embed/ideas/{IDEA}/comments", json_body=[
        {"id": COMMENT, "authorName": _EMAIL, "body": "a"}, {"id": IDEA_B, "authorName": "Plain Name"}])
    resp = env.req("GET", f"/ideas/{IDEA}/comments")
    assert resp.status_code == 200
    assert "@" not in resp.text
    assert [c["authorName"] for c in resp.json()] == ["Sorento staff", "Plain Name"]


# --------------------------------------------------------------------------- #
# Fix round 1: the portal token is stored with the claims it was minted with    #
# --------------------------------------------------------------------------- #
def test_fr_revoking_manage_between_portal_calls_re_exchanges_with_ideas_manage_false(env):
    from jose import jwt as jose_jwt

    from app.config import settings
    from tests.test_ideation_gateway import SIGNING_SECRET

    env.fake.route("GET", f"{SS_PORTAL}/roadmap", json_body={"columns": []})
    assert env.req("GET", f"{PORTAL}/roadmap").status_code == 200
    assert len(env.fake.portal_session_calls) == 1
    env.allow.discard(MANAGE)
    assert env.req("GET", f"{PORTAL}/roadmap").status_code == 200
    assert len(env.fake.portal_session_calls) == 2
    last = jose_jwt.decode(
        env.fake.session_calls[-1]["assertion"], SIGNING_SECRET,
        algorithms=[settings.jwt_algorithm], audience="ideation-embed",
    )
    assert last["ideas_manage"] is False
    # and the new portal token is cached under the new claims: no third exchange
    env.req("GET", f"{PORTAL}/roadmap")
    assert len(env.fake.portal_session_calls) == 2
