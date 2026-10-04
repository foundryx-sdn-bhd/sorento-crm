"""IDEATION-IN-CRM: the public customer track-page proxy (AC-H-01..H-03, H-05..H-07).

Red tests, written BEFORE the implementation. Routes under test (no auth, token is the
credential):

    GET  /api/v1/public/portal/ideas/{token}            -> ss GET  /public/ideas/{token}
    GET  /api/v1/public/portal/ideas/{token}/comments   -> ss GET  /public/ideas/{token}/comments
    POST /api/v1/public/portal/ideas/{token}/comments   -> ss POST /public/ideas/{token}/comments

CONTRACT for the coder:

* ss base = the resolved embed ``ideation_shared_service_url`` (same config as the gateway);
  sync ``httpx.Client`` (the boundary ``tests/_ideation_ss_fake.py`` patches). No embed
  session is minted for these calls.
* Rate limiting uses ``app.services.rate_limit.hit(bucket, ident, limit=, window_seconds=)``
  called as ``rate_limit.hit(...)`` (module attribute, so it can be patched): one bucket with
  ``limit=20, window_seconds=900`` keyed by client IP, one with ``limit=5, window_seconds=900``
  keyed by the token. The raw token must not be the redis key identity (hash it).
* Client IP = ``request.client.host`` (no ``X-Forwarded-For`` in the request) and is forwarded
  to ss as ``X-Forwarded-For``.

Postgres only for the (unused) db dependency, no network.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

# MUST be the first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.services import rate_limit
from tests._ideation_ss_fake import FakeSS, configure_embed_settings, message_of
from tests._pg_fixture import blank_session

BASE = "/api/v1/public/portal/ideas"
TOKEN = "Ab3dEf9hJk2LmN0p"  # 16 chars, valid shape
_IDEA_PATH = f"/public/ideas/{TOKEN}"
_COMMENTS_PATH = f"/public/ideas/{TOKEN}/comments"
_NOT_FOUND_BODY = {"error": {"code": "not_found", "message": "Not found."}}

_IDEA = {
    "title": "Faster quotes",
    "status": "In review",
    "ideaNumber": "IDEA-0042",
    "statusColor": "#2563eb",
    "productName": "Sorento CRM",
    "problem": "Quotes take too long",
    "proposedSolution": "Templates",
    "impact": "Saves a day a week",
    "department": "Sales",
    "submitterFirstName": "Jane",
    "submittedAt": "2026-09-30T08:15:00Z",
    "upvotes": 7,
    "nextStep": "We are reviewing it.",
    "timeline": [{"label": "New", "color": "#999", "state": "done"}],
    "mergedInto": None,
}
_IDEA_LEAKS = {
    "submitterEmail": "jane.leak@example.test",
    "submitterPhone": "+60100000001",
    "id": "idea-internal-id-123",
    "tenantId": "tenant-internal-id-456",
    "rawTranscript": "internal transcript text",
}

_COMMENT_ALLOWED = {"id", "parentId", "authorName", "isSubmitter", "body", "isDeleted", "createdAt", "editedAt"}


def _ss_comment(cid: str, *, kind: str = "embed", name: str = "Alex Staff", **extra) -> dict:
    return {
        "id": cid,
        "ideaId": "idea-internal-id-123",
        "parentId": None,
        "authorName": name,
        "authorKind": kind,
        "body": "Looks good",
        "isDeleted": False,
        "isMine": False,
        "canEdit": False,
        "canDelete": False,
        "createdAt": "2026-10-01T09:00:00Z",
        "editedAt": None,
        "authorId": "embed-user:secret-author-id",
        "authorEmail": "alex.staff@example.test",
        **extra,
    }


@pytest.fixture
def pub(monkeypatch):
    from app.database import get_db

    configure_embed_settings(monkeypatch)
    fake = FakeSS().install(monkeypatch)

    # In-memory fixed-window counter with rate_limit.peek / record semantics (check, then charge).
    counts: dict[tuple, int] = {}
    hits: list[dict] = []

    def _peek(bucket, ident, *, limit, window_seconds):
        hits.append({"bucket": bucket, "ident": ident, "limit": limit, "window": window_seconds})
        if counts.get((bucket, ident), 0) >= limit:
            return rate_limit.RateResult(allowed=False, retry_after_seconds=window_seconds)
        return rate_limit.RateResult(allowed=True)

    def _record(bucket, ident, *, window_seconds):
        counts[(bucket, ident)] = counts.get((bucket, ident), 0) + 1

    monkeypatch.setattr(rate_limit, "peek", _peek)
    monkeypatch.setattr(rate_limit, "record", _record)

    with blank_session() as db:
        def _db():
            yield db

        app.dependency_overrides[get_db] = _db
        try:
            client = TestClient(app)
            client.fake = fake  # type: ignore[attr-defined]
            client.hits = hits  # type: ignore[attr-defined]
            client.counts = counts  # type: ignore[attr-defined]
            yield client
        finally:
            app.dependency_overrides.clear()


def _script_idea(fake: FakeSS, **extra) -> None:
    fake.route("GET", _IDEA_PATH, json_body={**_IDEA, **extra})


def _no_ss(pub) -> bool:
    return not pub.fake.calls and not pub.fake.session_calls


# --------------------------------------------------------------------------- #
# H-01 / H-04 data                                                              #
# --------------------------------------------------------------------------- #
def test_h01_idea_is_proxied_from_ss_public_route_without_a_session(pub):
    _script_idea(pub.fake)
    resp = pub.get(f"{BASE}/{TOKEN}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("title", "status", "ideaNumber", "statusColor", "problem", "upvotes", "submittedAt", "nextStep"):
        assert body[key] == _IDEA[key], key
    assert [(c["method"], c["path"]) for c in pub.fake.calls] == [("GET", _IDEA_PATH)]
    assert not pub.fake.session_calls  # public: no embed session is minted
    assert "authorization" not in pub.fake.calls[0]["headers"]


def test_h01_comments_are_proxied_from_ss_public_route(pub):
    pub.fake.route("GET", _COMMENTS_PATH, json_body=[_ss_comment("c-1")])
    resp = pub.get(f"{BASE}/{TOKEN}/comments")
    assert resp.status_code == 200, resp.text
    assert [c["id"] for c in resp.json()] == ["c-1"]
    assert [(c["method"], c["path"]) for c in pub.fake.calls] == [("GET", _COMMENTS_PATH)]


# --------------------------------------------------------------------------- #
# H-02 token shape and uniform 404                                              #
# --------------------------------------------------------------------------- #
_BAD_TOKENS = ["short", "x" * 65, "abcdefghijklmnop.q", "abcdefghijklmnop!!", "abcdefghij klmnop"]


def _route_exists(pub, method: str, suffix: str, kw: dict) -> None:
    """Prove the route is mounted (a valid token is served) so a 404 below can only be the
    handler's own token rejection, never a missing route. Resets the fake afterwards."""
    if method == "POST":
        pub.fake.route("POST", _COMMENTS_PATH, status=201, json_body=_ss_comment("c-ok", kind="public"))
    elif suffix:
        pub.fake.route("GET", _COMMENTS_PATH, json_body=[])
    else:
        _script_idea(pub.fake)
    ok = pub.request(method, f"{BASE}/{TOKEN}{suffix}", **kw)
    assert ok.status_code in (200, 201), f"route not mounted: {ok.status_code} {ok.text}"
    pub.fake.calls.clear()


@pytest.mark.parametrize("token", _BAD_TOKENS)
@pytest.mark.parametrize(
    "method, suffix, kw",
    [("GET", "", {}), ("GET", "/comments", {}), ("POST", "/comments", {"json": {"body": "hello"}})],
    ids=["idea", "comments-get", "comments-post"],
)
def test_h02_malformed_token_is_404_and_ss_is_never_called(pub, token, method, suffix, kw):
    _route_exists(pub, method, suffix, kw)
    resp = pub.request(method, f"{BASE}/{token}{suffix}", **kw)
    assert resp.status_code == 404, resp.text
    assert not pub.fake.calls and not pub.fake.session_calls


@pytest.mark.parametrize(
    "method, suffix, kw, ss_method, ss_path",
    [
        ("GET", "", {}, "GET", _IDEA_PATH),
        ("GET", "/comments", {}, "GET", _COMMENTS_PATH),
        ("POST", "/comments", {"json": {"body": "hello"}}, "POST", _COMMENTS_PATH),
    ],
    ids=["idea", "comments-get", "comments-post"],
)
def test_h02_ss_404_is_a_uniform_404_identical_to_a_malformed_token(pub, method, suffix, kw, ss_method, ss_path):
    pub.fake.route(ss_method, ss_path, status=404, json_body={"error": {"code": "not_found", "message": "Idea 4411 gone, tenant X"}})
    unknown = pub.request(method, f"{BASE}/{TOKEN}{suffix}", **kw)
    assert len(pub.fake.calls) == 1, "a well-formed token must reach ss; only ss can say it is unknown"
    malformed = pub.request(method, f"{BASE}/short{suffix}", **kw)
    assert unknown.status_code == malformed.status_code == 404
    assert unknown.json() == malformed.json(), "unknown token and malformed token must be indistinguishable"
    assert "4411" not in unknown.text and "tenant" not in unknown.text.lower()


# --------------------------------------------------------------------------- #
# H-03 headers                                                                  #
# --------------------------------------------------------------------------- #
def _assert_private_headers(resp) -> None:
    assert resp.headers.get("cache-control") == "no-store"
    assert resp.headers.get("x-robots-tag") == "noindex"
    assert resp.headers.get("referrer-policy") == "no-referrer"


def test_h03_headers_on_idea_comments_and_post(pub):
    _script_idea(pub.fake)
    pub.fake.route("GET", _COMMENTS_PATH, json_body=[])
    pub.fake.route("POST", _COMMENTS_PATH, status=201, json_body=_ss_comment("c-9", kind="public"))
    _assert_private_headers(pub.get(f"{BASE}/{TOKEN}"))
    _assert_private_headers(pub.get(f"{BASE}/{TOKEN}/comments"))
    posted = pub.post(f"{BASE}/{TOKEN}/comments", json={"body": "hi"})
    assert posted.status_code == 201, posted.text
    _assert_private_headers(posted)


def test_h03_headers_on_the_404_too(pub):
    _assert_private_headers(pub.get(f"{BASE}/short"))


# --------------------------------------------------------------------------- #
# H-05 client-sent identity is never forwarded                                  #
# --------------------------------------------------------------------------- #
def test_h05_client_sent_name_is_not_forwarded_to_ss(pub):
    pub.fake.route("POST", _COMMENTS_PATH, status=201, json_body=_ss_comment("c-9", kind="public", name="Jane"))
    resp = pub.post(
        f"{BASE}/{TOKEN}/comments",
        json={
            "body": "My comment",
            "parentId": "c-1",
            "authorName": "Mallory",
            "name": "Mallory",
            "authorKind": "embed",
            "email": "mallory@example.test",
            "isSubmitter": False,
        },
    )
    assert resp.status_code == 201, resp.text
    sent = pub.fake.calls[0]["json"]
    assert sent["body"] == "My comment"
    assert sent.get("parentId") == "c-1"
    assert set(sent) <= {"body", "parentId"}, f"only body and parentId may be forwarded, got {set(sent)}"
    assert "Mallory" not in json.dumps(sent) and "mallory" not in json.dumps(sent)


# --------------------------------------------------------------------------- #
# H-06 allow-list                                                               #
# --------------------------------------------------------------------------- #
def test_h06_idea_fields_outside_the_documented_set_are_stripped(pub):
    _script_idea(pub.fake, **_IDEA_LEAKS)
    resp = pub.get(f"{BASE}/{TOKEN}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for leak_key, leak_value in _IDEA_LEAKS.items():
        assert leak_key not in body
        assert leak_value not in resp.text
    assert body["title"] == "Faster quotes"  # documented fields survive


def test_h06_comment_fields_are_exactly_the_allow_list_and_is_submitter_is_derived(pub):
    pub.fake.route(
        "GET", _COMMENTS_PATH,
        json_body=[_ss_comment("c-1", kind="public", name="Jane"), _ss_comment("c-2", kind="embed", name="Alex Staff")],
    )
    resp = pub.get(f"{BASE}/{TOKEN}/comments")
    assert resp.status_code == 200, resp.text
    by_id = {c["id"]: c for c in resp.json()}
    for c in by_id.values():
        assert set(c) == _COMMENT_ALLOWED
    assert by_id["c-1"]["isSubmitter"] is True
    assert by_id["c-2"]["isSubmitter"] is False
    assert by_id["c-1"]["authorName"] == "Jane"
    for forbidden in ("embed-user:secret-author-id", "alex.staff@example.test", "idea-internal-id-123"):
        assert forbidden not in resp.text


def test_h06_no_value_with_an_at_sign_survives_for_a_seeded_email_author_name(pub):
    pub.fake.route(
        "GET", _COMMENTS_PATH,
        json_body=[_ss_comment("c-1", name="alex.staff@example.test"), _ss_comment("c-2", name="Plain Name")],
    )
    resp = pub.get(f"{BASE}/{TOKEN}/comments")
    assert resp.status_code == 200, resp.text
    assert "@" not in resp.text
    assert "example.test" not in resp.text
    assert {c["id"] for c in resp.json()} == {"c-1", "c-2"}  # the comment is kept, only the name is masked


def test_h06_posted_comment_response_is_allow_listed_too(pub):
    pub.fake.route("POST", _COMMENTS_PATH, status=201, json_body=_ss_comment("c-9", kind="public", name="Jane"))
    resp = pub.post(f"{BASE}/{TOKEN}/comments", json={"body": "hi"})
    assert resp.status_code == 201, resp.text
    assert set(resp.json()) == _COMMENT_ALLOWED
    assert resp.json()["isSubmitter"] is True
    assert "secret-author-id" not in resp.text and "@" not in resp.text


# --------------------------------------------------------------------------- #
# H-07 rate limit                                                               #
# --------------------------------------------------------------------------- #
def _post(pub, token: str = TOKEN):
    pub.fake.route("POST", f"/public/ideas/{token}/comments", status=201, json_body=_ss_comment("c-9", kind="public"))
    return pub.post(f"{BASE}/{token}/comments", json={"body": "hi"})


def test_h07_sixth_post_on_one_token_is_429_with_retry_after_and_ss_not_called(pub):
    for _ in range(5):
        assert _post(pub).status_code == 201
    resp = _post(pub)
    assert resp.status_code == 429, resp.text
    assert "Too many comments. Try again later." in message_of(resp)
    assert int(resp.headers["retry-after"]) > 0
    assert len([c for c in pub.fake.calls if c["method"] == "POST"]) == 5  # checked BEFORE ss


def test_h07_another_token_is_not_blocked_by_the_first_tokens_budget(pub):
    for _ in range(6):
        _post(pub)
    other = "Zz9yXw8vUt7sRq6p"
    assert _post(pub, other).status_code == 201


def test_h07_global_ceiling_of_200_across_all_tokens_is_429_with_retry_after(pub):
    """AC-H-07: one ceiling across every token (no per-IP bucket any more)."""
    for i in range(200):
        assert _post(pub, f"Tok{i:03d}AbCdEfGhIjK").status_code == 201
    resp = _post(pub, "Zz9yXw8vUt7sRq6p")
    assert resp.status_code == 429, resp.text
    assert "Too many comments. Try again later." in message_of(resp)
    assert int(resp.headers["retry-after"]) > 0
    assert len([c for c in pub.fake.calls if c["method"] == "POST"]) == 200


def test_h07_limits_are_5_per_token_and_200_global_per_15_minutes_and_no_ip_bucket(pub):
    _post(pub)
    limits = {(h["bucket"], h["limit"], h["window"]) for h in pub.hits}
    assert any(limit == 5 and window == 900 for _, limit, window in limits)
    assert any(limit == 200 and window == 900 for _, limit, window in limits)
    assert not any(limit == 20 for _, limit, _ in limits), "the per-IP bucket is gone"
    assert all(TOKEN not in str(h["ident"]) for h in pub.hits), "the raw token must not be a redis key"
    assert all(h["ident"] != "testclient" for h in pub.hits)


def test_h07_no_client_ip_is_forwarded_to_ss_even_when_the_client_sends_x_forwarded_for(pub):
    pub.fake.route("POST", _COMMENTS_PATH, status=201, json_body=_ss_comment("c-9", kind="public"))
    resp = pub.post(f"{BASE}/{TOKEN}/comments", json={"body": "hi"}, headers={"X-Forwarded-For": "6.6.6.6"})
    assert resp.status_code == 201, resp.text
    assert "x-forwarded-for" not in pub.fake.calls[0]["headers"]


def test_h07_body_over_2000_characters_is_refused_before_ss(pub):
    resp = pub.post(f"{BASE}/{TOKEN}/comments", json={"body": "x" * 2001})
    assert resp.status_code == 422, resp.text
    assert not pub.fake.calls


@pytest.mark.parametrize(
    "method, suffix, kw, ss_method, ss_path",
    [
        ("GET", "", {}, "GET", _IDEA_PATH),
        ("GET", "/comments", {}, "GET", _COMMENTS_PATH),
        ("POST", "/comments", {"json": {"body": "hi"}}, "POST", _COMMENTS_PATH),
    ],
    ids=["idea", "comments-get", "comments-post"],
)
def test_h07_a_non_json_2xx_from_ss_is_a_502_that_keeps_the_private_headers(
    pub, method, suffix, kw, ss_method, ss_path
):
    pub.fake.route(ss_method, ss_path, content=b"<html>proxy page</html>", headers={"content-type": "text/html"})
    resp = pub.request(method, f"{BASE}/{TOKEN}{suffix}", **kw)
    assert resp.status_code == 502, resp.text
    _assert_private_headers(resp)


def test_h07_random_unknown_tokens_do_not_consume_the_global_bucket(pub):
    """N1: ss 404s a well-formed but unknown token, which must cost the caller nothing."""
    for i in range(210):
        t = f"Rnd{i:03d}AbCdEfGhIjK"
        pub.fake.route("POST", f"/public/ideas/{t}/comments", status=404, json_body=_NOT_FOUND_BODY)
        assert pub.post(f"{BASE}/{t}/comments", json={"body": "x"}).status_code == 404
    assert not any(v for v in pub.counts.values()), "nothing was charged for unknown tokens"
    assert _post(pub).status_code == 201  # a real customer still gets through


def test_h07_a_successful_post_charges_both_buckets(pub):
    assert _post(pub).status_code == 201
    token_buckets = {b: n for (b, i), n in pub.counts.items() if b.endswith("token")}
    global_buckets = {b: n for (b, i), n in pub.counts.items() if b.endswith("global")}
    assert sum(token_buckets.values()) == 1 and sum(global_buckets.values()) == 1


def test_h07_a_failed_ss_post_is_not_charged(pub):
    pub.fake.route("POST", _COMMENTS_PATH, status=422, json_body={"detail": "Comment cannot be empty."})
    assert pub.post(f"{BASE}/{TOKEN}/comments", json={"body": "x"}).status_code == 422
    assert not any(pub.counts.values())
