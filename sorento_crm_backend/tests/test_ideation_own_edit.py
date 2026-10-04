"""IDEATION-CAPTURE slice E1 red tests: a submitter edits their OWN idea with only
``ideation.board.view`` (PLAN-ideation-capture-02oct section 3, Q3).

Contract: ``PATCH /api/v1/ideation/ideas/{id}``
* manage holder: forwarded to ss PATCH /embed/ideas/{id} as before, no ownership lookup.
* board.view only: the gateway first GETs ss /embed/ideas/{id} as that user; ``isMine`` exactly
  ``True`` forwards the PATCH (same field whitelist); false or missing is 403 "You can only edit
  your own ideas." with NO PATCH reaching ss. The CRM never guesses ownership.
* ss 404 on that GET is relayed as 404 and no PATCH is sent.
* neither permission: 403, ss never called.

Reuses the ``env`` harness (Postgres blank schema, FakeSS at httpx) from test_ideation_gateway.
"""
from __future__ import annotations

import pytest

from tests.test_ideation_gateway import IDEA, MANAGE, VIEW, env  # noqa: F401  (env is a fixture)
from tests._ideation_ss_fake import message_of

NOT_MINE = "You can only edit your own ideas."
SS_PATH = f"/embed/ideas/{IDEA}"


def _methods(e) -> list[str]:
    return [c["method"] for c in e.fake.calls]


def _view_only(e) -> None:
    e.allow.discard(MANAGE)
    e.allow.add(VIEW)


def test_manage_holder_is_forwarded_with_no_ownership_lookup(env):
    """Regression guard: passes today and must keep passing."""
    env.fake.route("PATCH", SS_PATH, json_body={"id": IDEA})
    resp = env.req("PATCH", f"/ideas/{IDEA}", json={"problem": "Edited"})
    assert resp.status_code == 200, resp.text
    assert _methods(env) == ["PATCH"]
    assert env.fake.calls[0]["json"] == {"problem": "Edited"}


def test_view_only_owner_patch_is_forwarded_after_an_ownership_lookup(env):
    _view_only(env)
    env.fake.route("GET", SS_PATH, json_body={"id": IDEA, "isMine": True})
    env.fake.route("PATCH", SS_PATH, json_body={"id": IDEA, "problem": "Edited"})
    resp = env.req("PATCH", f"/ideas/{IDEA}", json={"problem": "Edited"})
    assert resp.status_code == 200, resp.text
    assert _methods(env) == ["GET", "PATCH"]
    get_call, patch_call = env.fake.calls
    assert get_call["path"] == SS_PATH and patch_call["path"] == SS_PATH
    # both calls are made as the same (caller's) embed token
    assert get_call["headers"]["authorization"] == patch_call["headers"]["authorization"]
    assert patch_call["headers"]["authorization"] == f"Bearer {env.fake.tokens[0]}"
    assert patch_call["json"] == {"problem": "Edited"}


@pytest.mark.parametrize(
    "idea_body",
    [
        {"id": IDEA, "isMine": False},
        {"id": IDEA},
        {"id": IDEA, "isMine": None},
        {"id": IDEA, "isMine": "true"},
        {"id": IDEA, "isMine": 1},
    ],
    ids=["false", "missing", "null", "string-true", "int-one"],
)
def test_view_only_non_owner_is_403_and_no_patch_reaches_ss(env, idea_body):
    _view_only(env)
    env.fake.route("GET", SS_PATH, json_body=idea_body)
    resp = env.req("PATCH", f"/ideas/{IDEA}", json={"problem": "Edited"})
    assert resp.status_code == 403, resp.text
    assert NOT_MINE in message_of(resp)
    # the lookup happened (so the 403 is the ownership rule, not the old permission gate)
    assert _methods(env) == ["GET"]


def test_ss_404_on_the_ownership_lookup_is_relayed_and_no_patch_is_sent(env):
    _view_only(env)
    env.fake.route("GET", SS_PATH, status=404, json_body={"error": {"code": "not_found", "message": "Idea not found."}})
    resp = env.req("PATCH", f"/ideas/{IDEA}", json={"problem": "Edited"})
    assert resp.status_code == 404, resp.text
    assert "Idea not found." in message_of(resp)
    assert _methods(env) == ["GET"]


def test_neither_permission_is_403_and_ss_is_never_called(env):
    """Regression guard: passes today and must keep passing."""
    env.allow.clear()
    resp = env.req("PATCH", f"/ideas/{IDEA}", json={"problem": "Edited"})
    assert resp.status_code == 403, resp.text
    assert not env.fake.calls and not env.fake.session_calls


def test_view_only_owner_path_still_drops_non_whitelisted_fields(env):
    _view_only(env)
    env.fake.route("GET", SS_PATH, json_body={"id": IDEA, "isMine": True})
    env.fake.route("PATCH", SS_PATH, json_body={"id": IDEA})
    sent = {
        "problem": "p",
        "proposedSolution": "s",
        "impact": "i",
        "department": "d",
        "rawText": "r",
        "status": "closed",
        "productId": "other",
        "isMine": True,
    }
    resp = env.req("PATCH", f"/ideas/{IDEA}", json=sent)
    assert resp.status_code == 200, resp.text
    assert _methods(env) == ["GET", "PATCH"]
    assert env.fake.calls[1]["json"] == {
        "problem": "p",
        "proposedSolution": "s",
        "impact": "i",
        "department": "d",
        "rawText": "r",
    }


def test_non_json_2xx_on_the_ownership_lookup_is_502_and_no_patch_is_sent(env):
    _view_only(env)
    env.fake.route("GET", SS_PATH, status=200, content=b"<html>not json</html>",
                   headers={"content-type": "text/html"})
    resp = env.req("PATCH", f"/ideas/{IDEA}", json={"problem": "Edited"})
    assert resp.status_code == 502, resp.text
    assert _methods(env) == ["GET"]
