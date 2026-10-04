"""IDEATION-CAPTURE slice: CRM plumbing for "my ideas" (red tests, written before the code).

Plan: documentation/plans/ideation/PLAN-ideation-capture-02oct.md (sections 1 and 2).

CONTRACT:

* ``ideation_embed_service.mint_embed_assertion(user, *, secret, connection_id, phone=None)``:
  a non-empty ``phone`` becomes the ``phone`` claim; None or "" means NO ``phone`` key.
* The gateway resolves the caller's phone from ``users.respond_contact_id`` ->
  ``respond_contacts.phone_number`` and passes it to the mint; no contact means no claim.
* ``GET /api/v1/ideation/ideas`` forwards ``mine=true`` to ss only for the exact string "true".

Postgres only; ss faked at httpx via tests/_ideation_ss_fake.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from jose import jwt

# MUST be the first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402,F401

from app.config import settings
from app.models.access import RespondContact
from app.models.user import User
from app.services.ideation_embed_service import mint_embed_assertion
from tests._ideation_ss_fake import CONNECTION_ID, SIGNING_SECRET
from tests.test_ideation_gateway import env  # noqa: F401  (fixture)

PHONE = "+60123456789"


def _decode(token: str) -> dict:
    return jwt.decode(token, SIGNING_SECRET, algorithms=[settings.jwt_algorithm], audience="ideation-embed")


def _mint(**kw) -> dict:
    user = {"id": str(uuid.uuid4()), "email": "zzt-mint@example.test", "name": "Mint Tester"}
    return _decode(mint_embed_assertion(user, secret=SIGNING_SECRET, connection_id=CONNECTION_ID, **kw))


# ---- mint_embed_assertion ----------------------------------------------------------------------
def test_mint_puts_the_phone_claim_when_given():
    assert _mint(phone=PHONE)["phone"] == PHONE


@pytest.mark.parametrize("blank", [None, ""])
def test_mint_omits_the_phone_key_when_none_or_empty(blank):
    claims = _mint(phone=blank)
    assert "phone" not in claims
    assert claims["typ"] == "assertion"  # and the rest of the assertion is still minted


# ---- gateway: phone claim from the user's respond contact ---------------------------------------
def _link_contact(e, phone: str | None) -> None:
    contact_id = None
    if phone is not None:
        contact_id = str(uuid.uuid4())
        e.db.add(RespondContact(id=contact_id, phone_number=phone, name="ZZT Contact"))
        e.db.commit()
    e.db.query(User).filter(User.id == e.user["id"]).update({"respond_contact_id": contact_id})
    e.db.commit()


def _set_user_phone(e, *, contact_number: str | None, verified: bool) -> None:
    e.db.query(User).filter(User.id == e.user["id"]).update(
        {
            "contact_number": contact_number,
            "phone_verified_at": datetime.now(timezone.utc) if verified else None,
        }
    )
    e.db.commit()


def _new_phone() -> str:
    return f"+6011{uuid.uuid4().int % 10**7:07d}"


def _assertion_claims(e) -> dict:
    assert e.req("GET", "/ideas").status_code == 200
    assert len(e.fake.session_calls) == 1
    return _decode(e.fake.session_calls[-1]["assertion"])


def test_gateway_assertion_carries_the_phone_of_the_users_respond_contact(env):  # noqa: F811
    # AC: verified + matching (different but equivalent format) -> phone claim present
    phone = _new_phone()
    _link_contact(env, phone)
    _set_user_phone(env, contact_number=phone.lstrip("+"), verified=True)
    assert _assertion_claims(env)["phone"] == phone


def test_gateway_assertion_omits_phone_when_user_phone_is_not_verified(env):  # noqa: F811
    phone = _new_phone()
    _link_contact(env, phone)
    _set_user_phone(env, contact_number=phone, verified=False)
    assert "phone" not in _assertion_claims(env)


def test_gateway_assertion_omits_phone_when_verified_number_differs_from_contact(env):  # noqa: F811
    phone = _new_phone()
    other = f"+6012{(uuid.uuid4().int % 10**7) :07d}"
    assert other != phone
    _link_contact(env, phone)
    _set_user_phone(env, contact_number=other, verified=True)
    assert "phone" not in _assertion_claims(env)


def test_gateway_assertion_omits_phone_when_user_contact_number_is_null(env):  # noqa: F811
    _link_contact(env, _new_phone())
    _set_user_phone(env, contact_number=None, verified=True)
    assert "phone" not in _assertion_claims(env)


def test_gateway_assertion_has_no_phone_when_the_user_has_no_respond_contact(env):  # noqa: F811
    _link_contact(env, None)
    assert env.req("GET", "/ideas").status_code == 200
    assert len(env.fake.session_calls) == 1
    assert "phone" not in _decode(env.fake.session_calls[-1]["assertion"])


# ---- gateway: the mine filter ---------------------------------------------------------------------
def test_list_forwards_mine_true_alongside_filter_and_search(env):  # noqa: F811
    resp = env.req("GET", "/ideas?mine=true&filter=archived&search=quotes")
    assert resp.status_code == 200, resp.text
    assert env.fake.calls[0]["path"] == "/embed/ideas"
    q = env.fake.calls[0]["query"]
    assert q.get("mine") == "true"
    assert q.get("filter") == "archived" and q.get("search") == "quotes"


@pytest.mark.parametrize("value", ["false", "1", "yes", "True", "TRUE", ""])
def test_list_does_not_forward_mine_unless_it_is_exactly_true(env, value):  # noqa: F811
    resp = env.req("GET", f"/ideas?mine={value}&filter=archived")
    assert resp.status_code == 200, resp.text
    q = env.fake.calls[0]["query"]
    assert "mine" not in q
    assert q.get("filter") == "archived"


# ---- ideas_manage claim (ss#111 section 4) ---------------------------------------------------------
from tests.test_ideation_gateway import MANAGE, VIEW  # noqa: E402


def test_assertion_carries_ideas_manage_true_for_a_manage_holder(env):  # noqa: F811
    env.allow.clear()
    env.allow.update({VIEW, MANAGE})
    assert _assertion_claims(env)["ideas_manage"] is True


def test_assertion_carries_ideas_manage_false_for_a_view_only_user(env):  # noqa: F811
    env.allow.clear()
    env.allow.add(VIEW)
    claims = _assertion_claims(env)
    assert "ideas_manage" in claims
    assert claims["ideas_manage"] is False


def test_ideas_manage_is_part_of_the_token_cache_key(env):  # noqa: F811
    env.allow.clear()
    env.allow.update({VIEW, MANAGE})
    assert env.req("GET", "/ideas").status_code == 200
    # same grant: the cached token is reused, no second assertion
    assert env.req("GET", "/ideas").status_code == 200
    assert len(env.fake.session_calls) == 1
    # the manage grant is withdrawn: a fresh assertion with the new value
    env.allow.discard(MANAGE)
    assert env.req("GET", "/ideas").status_code == 200
    assert len(env.fake.session_calls) == 2
    assert _decode(env.fake.session_calls[0]["assertion"])["ideas_manage"] is True
    assert _decode(env.fake.session_calls[1]["assertion"])["ideas_manage"] is False
    # and granted again: another fresh one, true
    env.allow.add(MANAGE)
    assert env.req("GET", "/ideas").status_code == 200
    assert _decode(env.fake.session_calls[-1]["assertion"])["ideas_manage"] is True
