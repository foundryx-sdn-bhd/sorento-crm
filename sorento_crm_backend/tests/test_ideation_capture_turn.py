"""Slice C1 red tests - the one-message ideation capture turn.

Contract: documentation/plans/ideation/PLAN-ideation-capture-02oct.md sections 2.1-2.4, 3, 4a.

``app.services.ideation_capture_service.handle_capture_turn`` replaces the multi-turn draft
flow: access gate (linked active CRM user holding ``ideation.board.view``), a held
similar-list pointer (pick a number / NEW / anything else), a fresh message
(extractor -> similar-own lookup -> one-shot create). Reply wording lives in
``ideation_capture_replies`` and is pending the owner, so these tests assert structure and
facts (status, links, titles, numbering, missing-field names), never whole sentences.

Real Postgres on the blank scratch schema; every test seeds its own workspace, contact,
user, role and permission. The three outside seams are patched as module attributes of
the new service: ``extract_ideate_turn``, ``call_similar_own``, ``call_create_idea``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

# MUST be the first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.models.access import RespondContact
from app.models.respond_workspace import RespondWorkspace
from app.models.user import (
    User,
    UserPermission,
    UserRole,
    UserRoleAssignment,
    UserRolePermission,
)
from app.services.ideation_extractor import IdeateExtraction
from app.services.ideation_turn_service import IdeationServiceError
from app.utils.field_encryption import encrypt_secret
from tests._pg_fixture import blank_session

CRM = "https://crm.example"
PERM = "ideation.board.view"

MSG = "i have an idea, the price tag should show promo price in red"
SS_PUBLIC_LINK = "https://ss.example/public/track/abc"
PROBLEM = "Customers keep asking why the price differs from the sticker"
TITLE = "Promo price in red on price tags"


def _uid() -> str:
    return str(uuid.uuid4())


def _now_iso(delta: timedelta = timedelta(0)) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat()


# --------------------------------------------------------------------------- #
# Harness                                                                     #
# --------------------------------------------------------------------------- #
class Env:
    def __init__(self, db, svc, monkeypatch):
        self.db = db
        self.svc = svc
        self.mp = monkeypatch
        self.phone = f"+6012{uuid.uuid4().int % 10**8:08d}"
        self.rio = f"rio-{uuid.uuid4().hex[:10]}"
        self.contact: RespondContact | None = None
        self.user: User | None = None
        self.extractor_calls: list[str] = []
        self.similar_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.renders: list[tuple[str, dict, str]] = []
        self.languages: list[str | None] = []
        # message_text -> IdeateExtraction; unknown text -> no idea content
        self.extractions: dict[str, IdeateExtraction] = {}
        self.similar_result: dict | Exception = {"matches": []}
        self.create_result: dict | Exception = {}

    # ---- seeding -------------------------------------------------------- #
    def seed_workspace(self, *, product_id: str | None = "prod-1") -> None:
        self.db.add(
            RespondWorkspace(
                id=_uid(),
                space_id="zzt-space",
                api_key_ciphertext=encrypt_secret("respond-key"),
                is_default=True,
                ideation_product_id=product_id,
                ideation_shared_service_url="https://ss.test",
                ideation_intake_api_key_ciphertext=encrypt_secret("intake-key"),
            )
        )
        self.db.flush()

    def seed_contact(self, session_vars: dict | None = None) -> RespondContact:
        self.contact = RespondContact(
            id=_uid(),
            phone_number=self.phone,
            name="CONTACT A",
            respond_io_id=self.rio,
            session_vars=session_vars or {},
        )
        self.db.add(self.contact)
        self.db.flush()
        return self.contact

    def seed_user(self, *, status: str = "ACTIVE", with_perm: bool = True, link: bool = True) -> User:
        user = User(
            id=_uid(),
            email=f"zzt-{uuid.uuid4().hex[:8]}@test.com",
            name="USER A",
            status=status,
            respond_contact_id=self.contact.id if link else None,
        )
        self.db.add(user)
        self.db.flush()
        if with_perm:
            role_id = _uid()
            self.db.add(
                UserRole(
                    id=role_id,
                    slug=f"zzt_{role_id[:8]}",
                    name=f"zzt_{role_id[:8]}",
                    description="",
                    is_protected=False,
                    is_default=False,
                )
            )
            self.db.flush()
            perm = self.db.query(UserPermission).filter(UserPermission.slug == PERM).first()
            if perm is None:
                perm = UserPermission(id=_uid(), slug=PERM, name=PERM, description="")
                self.db.add(perm)
                self.db.flush()
            self.db.add(UserRoleAssignment(user_id=user.id, role_id=role_id))
            self.db.add(UserRolePermission(id=_uid(), role_id=role_id, permission_id=perm.id))
            self.db.flush()
        self.user = user
        return user

    def ready(self, *, session_vars: dict | None = None) -> None:
        """Workspace + contact + active linked user holding the permission."""
        self.seed_workspace()
        self.seed_contact(session_vars)
        self.seed_user()

    # ---- scripting ------------------------------------------------------ #
    def extraction(self, text: str, *, fields: dict | None = None, title: str = "",
                   language: str | None = None) -> None:
        ex = IdeateExtraction(fields=fields or {}, title=title)
        # set after construction so a missing dataclass field fails in the service under
        # test, not in this fixture
        ex.language = language
        self.extractions[text] = ex

    def idea_message(self, text: str = MSG, *, extra_fields: dict | None = None,
                     language: str | None = None) -> str:
        fields = {"problem": PROBLEM, **(extra_fields or {})}
        self.extraction(text, fields=fields, title=TITLE, language=language)
        return text

    def created(self, *, idea_id: str | None = None) -> str:
        idea_id = idea_id or _uid()
        self.create_result = {
            "idea_id": idea_id,
            "idea_number": "IDEA-0184",
            "status": "captured",
            "title": TITLE,
            "link": SS_PUBLIC_LINK,
        }
        return idea_id

    def sim(self, n: int) -> list[dict]:
        ideas = [
            {
                "idea_id": _uid(),
                "idea_number": f"IDEA-01{i}0",
                "title": f"Existing idea number {i}",
                "problem": "Some problem",
                "status": "new",
                "status_label": "New",
                "similarity": 0.8,
                "created_at": "2026-10-01T09:00:00Z",
                "link": f"https://ss.example/public/{i}",
            }
            for i in range(1, n + 1)
        ]
        self.similar_result = {"matches": ideas}
        return ideas

    def pointer(self, similar: list[dict], *, message: str = MSG, age: timedelta = timedelta(0),
                is_test: bool = False, language: str | None = None,
                intake_ref: str | None = None) -> dict:
        held = {
            "status": "similar_offered",
            "message_text": message,
            "fields": {"problem": PROBLEM},
            "title": TITLE,
            "similar": [
                {k: s_[k] for k in ("idea_id", "idea_number", "title")} for s_ in similar
            ],
            "updated_at": _now_iso(age),
            "is_test": is_test,
            "intake_ref": intake_ref or _uid(),
        }
        if language is not None:
            held["language"] = language
        return {"ideation": held}

    # ---- act ------------------------------------------------------------ #
    def turn(self, message: str, *, session_vars_in: dict | None = None, is_test: bool = False,
             submitter_name: str | None = None) -> dict:
        # A live turn reads the held list from the contact's DB row only (L1), so a live
        # pointer the test supplies is landed in the row, flat, the way the chatbot tail
        # persists it. A test turn still takes the caller's pointer.
        if session_vars_in is not None and not is_test:
            self.contact.session_vars = {**(self.contact.session_vars or {}), **session_vars_in}
            self.db.flush()
            session_vars_in = None
        return self.raw_turn(message, session_vars_in=session_vars_in, is_test=is_test,
                             submitter_name=submitter_name)

    def turn_ask(self, message: str, **kw) -> dict:
        return self.raw_turn(message, ask_reply=True, **kw)

    def raw_turn(self, message: str, *, session_vars_in: dict | None = None, is_test: bool = False,
                 submitter_name: str | None = None, **extra) -> dict:
        return self.svc.handle_capture_turn(
            self.db,
            **extra,
            respond_io_id=self.rio,
            message_text=message,
            submitter_name=submitter_name,
            session_vars_in=session_vars_in,
            is_test=is_test,
        )

    def persisted(self) -> dict:
        self.db.refresh(self.contact)
        return dict(self.contact.session_vars or {})


@pytest.fixture
def env(monkeypatch):
    import app.services.ideation_capture_service as svc

    with blank_session() as db:
        e = Env(db, svc, monkeypatch)

        def _extract(_db, **kw):  # noqa: ANN001
            text = kw["message_text"]
            e.extractor_calls.append(text)
            return e.extractions.get(text, IdeateExtraction())

        def _similar(base_url, api_key, payload):  # noqa: ANN001
            e.similar_calls.append(payload)
            if isinstance(e.similar_result, Exception):
                raise e.similar_result
            return e.similar_result

        def _create(base_url, api_key, payload):  # noqa: ANN001
            e.create_calls.append(payload)
            if isinstance(e.create_result, Exception):
                raise e.create_result
            return e.create_result

        from app.services.ideation_capture_replies import render_reply as _real_render

        def _render(kind, facts, *, user_message, language, db=None):  # noqa: ANN001
            e.renders.append((kind, facts, user_message))
            e.languages.append(language)
            return _real_render(kind, facts, user_message=user_message, language=language, db=db)

        monkeypatch.setattr(svc, "render_reply", _render)
        monkeypatch.setattr(svc, "extract_ideate_turn", _extract)
        monkeypatch.setattr(svc, "call_similar_own", _similar)
        monkeypatch.setattr(svc, "call_create_idea", _create)
        monkeypatch.setattr(svc.settings, "frontend_base_url", CRM)
        monkeypatch.setattr(svc.settings, "ideation_shared_service_url", "")
        monkeypatch.setattr(svc.settings, "ideation_intake_api_key", "")
        yield e


def _only_render(e: Env, kind: str) -> dict:
    """Exactly one render_reply call this turn, of ``kind``; returns its facts."""
    assert len(e.renders) == 1, e.renders
    got_kind, facts, _msg = e.renders[0]
    assert got_kind == kind
    return facts


def _no_ss(e: Env) -> None:
    assert e.similar_calls == []
    assert e.create_calls == []


# --------------------------------------------------------------------------- #
# A - access gate                                                             #
# --------------------------------------------------------------------------- #
def test_a_contact_without_linked_user_is_no_access(env):
    env.seed_workspace()
    env.seed_contact()
    self_ptr = {"ideation": {"status": "similar_offered", "message_text": "x",
                             "similar": [], "updated_at": _now_iso(), "is_test": False}}
    env.idea_message()
    out = env.turn(MSG, session_vars_in=self_ptr)
    assert out["status"] == "no_access"
    _no_ss(env)
    assert "ideation" not in out["session_vars"]


def test_a_inactive_linked_user_is_no_access(env):
    env.seed_workspace()
    env.seed_contact()
    env.seed_user(status="INACTIVE")
    env.idea_message()
    out = env.turn(MSG)
    assert out["status"] == "no_access"
    _no_ss(env)


def test_a_linked_user_without_permission_is_no_access(env):
    env.seed_workspace()
    env.seed_contact()
    env.seed_user(with_perm=False)
    env.idea_message()
    out = env.turn(MSG)
    assert out["status"] == "no_access"
    _no_ss(env)


def test_a_linked_user_with_permission_proceeds(env):
    env.ready()
    env.idea_message()
    env.created()
    out = env.turn(MSG)
    assert out["status"] == "complete"
    assert len(env.create_calls) == 1


def test_a_unconfigured_workspace_replies_unconfigured(env):
    env.seed_workspace(product_id=None)
    env.seed_contact()
    env.seed_user()
    env.idea_message()
    out = env.turn(MSG)
    assert out["status"] == "unconfigured"
    _no_ss(env)


# --------------------------------------------------------------------------- #
# B - ask-back                                                                #
# --------------------------------------------------------------------------- #
def test_b_no_idea_content_asks_back(env):
    env.ready()
    env.extraction("want to submit idea", fields={})
    out = env.turn("want to submit idea")
    assert out["status"] == "ask_idea"
    assert out["reply_text"]
    _no_ss(env)
    assert "ideation" not in out["session_vars"]


# --------------------------------------------------------------------------- #
# C - no similar: create at once                                              #
# --------------------------------------------------------------------------- #
def test_c_no_similar_creates_one_shot(env):
    env.ready()
    env.idea_message()
    idea_id = env.created()
    out = env.turn(MSG, submitter_name="WHATSAPP NAME A")

    assert len(env.similar_calls) == 1
    assert len(env.create_calls) == 1
    p = env.create_calls[0]
    assert p["product_id"] == "prod-1"
    assert p["problem"] == PROBLEM
    assert p["submitter_crm_user_id"] == env.user.id
    assert p["submitter_phone"] == env.phone
    assert "submitter_name" in p
    assert p["title"] == TITLE
    assert p["raw_transcript"] == MSG
    assert p["is_test"] is False
    uuid.UUID(p["intake_ref"])  # a uuid string
    # flat contract: none of the old wrapper keys
    for old in ("capture_now", "fields", "message_text", "crm_user_id", "submitter_contact_id"):
        assert old not in p
    # optional fields are sent only when extracted
    for opt in ("proposed_solution", "impact", "department"):
        assert opt not in p

    sp = env.similar_calls[0]
    assert sp["product_id"] == "prod-1"
    assert sp["text"] == MSG  # C4a: the raw message, not the extracted problem
    assert sp["submitter_crm_user_id"] == env.user.id
    assert sp["submitter_phone"] == env.phone
    assert sp["is_test"] is False
    for old in ("problem", "title", "crm_user_id", "submitter_contact_id"):
        assert old not in sp

    link = f"{CRM}/ideas/{idea_id}"
    assert out["status"] == "complete"
    assert out["link"] == link
    assert link in out["reply_text"]
    assert TITLE in out["reply_text"]
    # the ss public link is never relayed
    assert SS_PUBLIC_LINK not in out["reply_text"]
    assert out["link"] != SS_PUBLIC_LINK


def test_c_extracted_optional_fields_are_sent_flat(env):
    env.ready()
    env.idea_message(extra_fields={
        "proposed_solution": "Show promo price in red",
        "impact": "Fewer repeat questions",
        "department": "Sales",
    })
    env.created()
    env.turn(MSG)
    p = env.create_calls[0]
    assert p["proposed_solution"] == "Show promo price in red"
    assert p["impact"] == "Fewer repeat questions"
    assert p["department"] == "Sales"


def test_c_fresh_creates_get_distinct_intake_refs(env):
    env.ready()
    env.idea_message()
    env.created()
    env.turn(MSG)
    env.turn(MSG)
    assert env.create_calls[0]["intake_ref"] != env.create_calls[1]["intake_ref"]


def test_c_missing_list_is_computed_from_what_was_sent(env):
    env.ready()
    env.idea_message(extra_fields={"proposed_solution": "Show promo price in red"})
    env.created()
    reply = env.turn(MSG)["reply_text"]
    assert "Proposed solution" not in reply
    assert "Impact" in reply
    assert "Department" in reply
    assert "Photos or files" in reply


def test_c_all_fields_sent_still_lists_photos(env):
    env.ready()
    env.idea_message(extra_fields={
        "proposed_solution": "Show promo price in red",
        "impact": "Fewer repeat questions",
        "department": "Sales",
    })
    env.created()
    reply = env.turn(MSG)["reply_text"]
    assert "Proposed solution" not in reply
    assert "Impact" not in reply
    assert "Department" not in reply
    assert "Photos or files" in reply


def test_c_only_problem_sent_lists_all_three_plus_photos(env):
    env.ready()
    env.idea_message()
    env.created()
    reply = env.turn(MSG)["reply_text"]
    for name in ("Proposed solution", "Impact", "Department", "Photos or files"):
        assert name in reply


# --------------------------------------------------------------------------- #
# D - similar found                                                           #
# --------------------------------------------------------------------------- #
def test_d_similar_found_offers_numbered_list_and_holds(env):
    env.ready()
    env.idea_message()
    ideas = env.sim(2)
    out = env.turn(MSG)

    assert env.create_calls == []
    assert out["status"] == "similar_offered"
    reply = out["reply_text"]
    lines = reply.splitlines()
    for n, idea in enumerate(ideas, start=1):
        line = next((ln for ln in lines if ln.lstrip().startswith(f"{n}")), None)
        assert line is not None, f"no line numbered {n}"
        assert idea["title"] in line
        assert f"{CRM}/ideas/{idea['idea_id']}" in line

    held = out["session_vars"]["ideation"]
    assert held["status"] == "similar_offered"
    assert held["message_text"] == MSG
    assert [s["idea_id"] for s in held["similar"]] == [i["idea_id"] for i in ideas]
    assert held["is_test"] is False
    assert held["fields"]["problem"] == PROBLEM
    assert held["title"] == TITLE
    uuid.UUID(held["intake_ref"])
    assert env.persisted()["ideation"]["status"] == "similar_offered"


def test_d_is_test_turn_is_not_persisted(env):
    env.ready()
    env.idea_message()
    env.sim(2)
    out = env.turn(MSG, is_test=True)
    assert out["status"] == "similar_offered"
    assert out["session_vars"]["ideation"]["is_test"] is True
    assert env.similar_calls[0]["is_test"] is True
    assert "ideation" not in env.persisted()


def test_d_three_matches_add_see_all_line(env):
    env.ready()
    env.idea_message()
    env.sim(3)
    reply = env.turn(MSG)["reply_text"]
    assert f"{CRM}/ideas?view=mine" in reply


def test_d_two_matches_have_no_see_all_line(env):
    env.ready()
    env.idea_message()
    env.sim(2)
    reply = env.turn(MSG)["reply_text"]
    assert "view=mine" not in reply


# --------------------------------------------------------------------------- #
# E - pick a number                                                           #
# --------------------------------------------------------------------------- #
def test_e_pick_number_replies_that_ideas_link(env):
    env.ready()
    similar = [
        {"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "First held idea"},
        {"idea_id": _uid(), "idea_number": "IDEA-0097", "title": "Second held idea"},
    ]
    sv = env.pointer(similar)
    out = env.turn("2", session_vars_in=sv)
    assert out["status"] == "similar_picked"
    assert f"{CRM}/ideas/{similar[1]['idea_id']}" in out["reply_text"]
    assert f"{CRM}/ideas/{similar[0]['idea_id']}" not in out["reply_text"]
    _no_ss(env)
    assert "ideation" not in out["session_vars"]
    assert env.extractor_calls == []


def test_e_out_of_range_number_is_a_fresh_message(env):
    env.ready()
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Only held idea"}]
    sv = env.pointer(similar)
    env.extraction("3", fields={})
    out = env.turn("3", session_vars_in=sv)
    assert env.extractor_calls == ["3"]
    assert out["status"] == "ask_idea"
    assert "ideation" not in out["session_vars"]


# --------------------------------------------------------------------------- #
# F - NEW                                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("reply", ["NEW", " new. "])
def test_f_new_creates_from_held_message(env, reply):
    env.ready()
    held = env.idea_message("chatbot should remember what the dealer asked before")
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held similar"}]
    ref = _uid()
    sv = env.pointer(similar, message=held, intake_ref=ref)
    idea_id = env.created()
    out = env.turn(reply, session_vars_in=sv)

    assert env.similar_calls == []
    assert len(env.create_calls) == 1
    p = env.create_calls[0]
    assert p["raw_transcript"] == held
    assert p["problem"] == PROBLEM
    assert p["title"] == TITLE
    assert p["intake_ref"] == ref
    assert p["submitter_crm_user_id"] == env.user.id
    assert p["submitter_phone"] == env.phone
    assert out["status"] == "complete"
    assert f"{CRM}/ideas/{idea_id}" in out["reply_text"]
    assert "ideation" not in out["session_vars"]


def test_c4a_similar_text_is_stripped_raw_message_not_extracted_problem(env):
    env.ready()
    raw = "  i have an idea, the price tag should show promo price in red  "
    # the extractor is keyed by the text it is called with; script both forms
    env.idea_message(raw)
    env.idea_message(raw.strip())
    assert PROBLEM != raw.strip()
    env.created()
    env.turn(raw)

    assert len(env.similar_calls) == 1
    assert env.similar_calls[0]["text"] == raw.strip()
    assert env.similar_calls[0]["text"] != PROBLEM


def test_c4a_new_after_held_list_makes_no_similar_call(env):
    env.ready()
    held = env.idea_message("chatbot should remember what the dealer asked before")
    sv = env.pointer([{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held"}],
                     message=held)
    env.created()
    env.turn("NEW", session_vars_in=sv)
    assert env.similar_calls == []


# --------------------------------------------------------------------------- #
# G - another reply while held                                                #
# --------------------------------------------------------------------------- #
def test_g_other_reply_drops_hold_and_runs_fresh(env):
    env.ready()
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held similar"}]
    sv = env.pointer(similar, message="old held message")
    other = env.idea_message("actually another idea: X should change")
    env.created()
    out = env.turn(other, session_vars_in=sv)

    assert env.extractor_calls == [other]
    assert len(env.similar_calls) == 1
    assert env.similar_calls[0]["text"] == other
    assert out["status"] == "complete"
    # The create is built from THIS message, never the dropped held one.
    assert all(c.get("raw_transcript") == other for c in env.create_calls)


# --------------------------------------------------------------------------- #
# H - stale hold                                                              #
# --------------------------------------------------------------------------- #
def test_h_stale_hold_new_is_a_fresh_message(env):
    env.ready()
    held = env.idea_message("held original idea text")
    sv = env.pointer([{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held"}],
                     message=held, age=timedelta(hours=25))
    env.extraction("new", fields={})
    out = env.turn("new", session_vars_in=sv)
    assert env.extractor_calls == ["new"]
    assert env.create_calls == []
    assert out["status"] == "ask_idea"


# --------------------------------------------------------------------------- #
# I - is_test mismatch                                                        #
# --------------------------------------------------------------------------- #
def test_i_live_hold_not_used_by_test_turn(env):
    env.ready()
    held = env.idea_message("held live idea text")
    sv = env.pointer([{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held"}],
                     message=held, is_test=False)
    env.extraction("new", fields={})
    out = env.turn("new", session_vars_in=sv, is_test=True)
    assert env.extractor_calls == ["new"]
    assert env.create_calls == []
    assert out["status"] == "ask_idea"


# --------------------------------------------------------------------------- #
# J - old draft-shaped pointer                                                #
# --------------------------------------------------------------------------- #
def test_j_old_draft_pointer_is_ignored(env):
    env.ready()
    env.idea_message()
    env.created()
    old = {"ideation": {"draft_id": "d-old", "status": "collecting", "missing": ["module"],
                        "updated_at": _now_iso()}}
    out = env.turn(MSG, session_vars_in=old)
    assert out["status"] == "complete"
    assert env.extractor_calls == [MSG]
    assert "ideation" not in out["session_vars"]
    assert "draft_id" not in env.create_calls[0]


# --------------------------------------------------------------------------- #
# K - ss failure                                                              #
# --------------------------------------------------------------------------- #
def test_k_similar_lookup_failure_is_graceful_error(env):
    env.ready()
    env.idea_message()
    env.similar_result = IdeationServiceError("boom")
    sv = {"keep": "me"}
    out = env.turn(MSG, session_vars_in=sv)
    assert out["status"] == "error"
    assert out["reply_text"]
    assert env.create_calls == []
    assert "ideation" not in out["session_vars"]


def test_k_create_failure_keeps_pointer_and_retry_resends_same_intake_ref(env):
    env.ready()
    held = env.idea_message("held original idea text")
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held"}]
    ref = _uid()
    sv = env.pointer(similar, message=held, intake_ref=ref)
    env.create_result = IdeationServiceError("boom")
    out = env.turn("new", session_vars_in=sv)
    assert out["status"] == "error"
    assert out["reply_text"]
    assert out["session_vars"]["ideation"] == sv["ideation"]
    assert out["session_vars"]["ideation"]["intake_ref"] == ref

    # the second NEW (pointer as returned) resends the same intake_ref and succeeds
    env.created()
    out2 = env.turn("NEW", session_vars_in=out["session_vars"])
    assert out2["status"] == "complete"
    assert [c["intake_ref"] for c in env.create_calls] == [ref, ref]


def test_k_create_failure_on_fresh_path_is_error(env):
    env.ready()
    env.idea_message()
    env.create_result = IdeationServiceError("boom")
    out = env.turn(MSG)
    assert out["status"] == "error"
    assert "link" not in out


# --------------------------------------------------------------------------- #
# L - link safety                                                             #
# --------------------------------------------------------------------------- #
def test_l_non_uuid_created_id_builds_no_crm_link(env):
    env.ready()
    env.idea_message()
    env.created(idea_id="not-a-uuid")
    out = env.turn(MSG)
    assert f"{CRM}/ideas/not-a-uuid" not in out["reply_text"]
    assert not out.get("link")


def test_l_non_uuid_similar_ideas_are_dropped(env):
    env.ready()
    env.idea_message()
    good = {"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Good similar idea"}
    bad = {"idea_id": "../../evil", "idea_number": "IDEA-0152", "title": "Bad similar idea"}
    env.similar_result = {"matches": [bad, good]}
    out = env.turn(MSG)
    assert "evil" not in out["reply_text"]
    assert "Bad similar idea" not in out["reply_text"]
    assert f"{CRM}/ideas/{good['idea_id']}" in out["reply_text"]
    assert [s["idea_id"] for s in out["session_vars"]["ideation"]["similar"]] == [good["idea_id"]]


# --------------------------------------------------------------------------- #
# M - endpoint                                                                #
# --------------------------------------------------------------------------- #
def test_m_endpoint_routes_to_capture_service(monkeypatch):
    from fastapi.testclient import TestClient

    from app.dependencies import get_db, get_external_api_user
    from tests._external_auth import external_permissions_granted

    seen: dict = {}

    def _fake(db, **kw):  # noqa: ANN001
        seen.update(kw)
        return {"status": "ask_idea", "reply_text": "Sure", "session_vars": {}, "offered_media": []}

    monkeypatch.setattr("app.api.v1.external.ideation.handle_capture_turn", _fake)
    monkeypatch.setattr(
        "app.api.v1.external.ideation.IntegrationLogService.create_integration_log",
        lambda self, log_data, request_payload_dict=None: None,
    )
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_external_api_user] = lambda: {"id": "system"}
    try:
        with external_permissions_granted():
            resp = TestClient(app).post(
                "/api/v1/external/ideation/turn",
                json={
                    "respond_io_id": "rio-1",
                    "message_text": "an idea",
                    "media_selection": "1",
                    "is_new_idea": True,
                },
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ask_idea"
    assert seen["respond_io_id"] == "rio-1"
    assert seen["message_text"] == "an idea"


# --------------------------------------------------------------------------- #
# N - every reply goes through render_reply(kind, facts, *, user_message)     #
# --------------------------------------------------------------------------- #
def test_n_no_access_renders_once(env):
    env.seed_workspace()
    env.seed_contact()
    env.idea_message()
    env.turn(MSG)
    _only_render(env, "no_access")
    assert env.renders[0][2] == MSG


def test_n_unconfigured_renders_once(env):
    env.seed_workspace(product_id=None)
    env.seed_contact()
    env.seed_user()
    env.idea_message()
    env.turn(MSG)
    _only_render(env, "unconfigured")
    assert env.renders[0][2] == MSG


def test_n_ask_idea_renders_once(env):
    env.ready()
    env.extraction("want to submit idea", fields={})
    env.turn("want to submit idea")
    _only_render(env, "ask_idea")
    assert env.renders[0][2] == "want to submit idea"


def test_n_complete_facts_exact(env):
    env.ready()
    env.idea_message(extra_fields={"impact": "Fewer repeat questions"})
    idea_id = env.created()
    out = env.turn(MSG)
    facts = _only_render(env, "complete")
    assert env.renders[0][2] == MSG
    assert facts["idea_number"] == "IDEA-0184"
    assert facts["title"] == TITLE
    assert facts["link"] == f"{CRM}/ideas/{idea_id}"
    assert facts["missing"] == ["Proposed solution", "Department", "Photos or files"]
    assert out["link"] == facts["link"]


def test_n_complete_missing_order_when_nothing_captured(env):
    env.ready()
    env.idea_message()
    env.created()
    env.turn(MSG)
    facts = _only_render(env, "complete")
    assert facts["missing"] == ["Proposed solution", "Impact", "Department", "Photos or files"]


def test_n_similar_offered_facts_exact(env):
    env.ready()
    env.idea_message()
    ideas = env.sim(3)
    env.turn(MSG)
    facts = _only_render(env, "similar_offered")
    assert env.renders[0][2] == MSG
    assert [(i["title"], i["link"]) for i in facts["similar"]] == [
        (i["title"], f"{CRM}/ideas/{i['idea_id']}") for i in ideas
    ]
    assert facts["see_all"] == f"{CRM}/ideas?view=mine"


def test_n_similar_offered_see_all_is_none_for_two_matches(env):
    env.ready()
    env.idea_message()
    env.sim(2)
    env.turn(MSG)
    facts = _only_render(env, "similar_offered")
    assert facts["see_all"] is None


def test_n_similar_picked_facts(env):
    env.ready()
    similar = [
        {"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "First held idea"},
        {"idea_id": _uid(), "idea_number": "IDEA-0097", "title": "Second held idea"},
    ]
    env.turn("2", session_vars_in=env.pointer(similar))
    facts = _only_render(env, "similar_picked")
    assert env.renders[0][2] == "2"
    assert facts["link"] == f"{CRM}/ideas/{similar[1]['idea_id']}"


def test_n_new_user_message_is_current_not_held(env):
    env.ready()
    held = env.idea_message("chatbot should remember what the dealer asked before")
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held similar"}]
    env.created()
    env.turn("NEW", session_vars_in=env.pointer(similar, message=held))
    facts = _only_render(env, "complete")
    assert env.renders[0][2] == "NEW"
    assert facts["title"] == TITLE


def test_n_error_renders_once(env):
    env.ready()
    env.idea_message()
    env.similar_result = IdeationServiceError("boom")
    env.turn(MSG)
    _only_render(env, "error")
    assert env.renders[0][2] == MSG


# --------------------------------------------------------------------------- #
# O - language follows the user's message                                     #
# --------------------------------------------------------------------------- #
def test_o_extractor_language_is_passed_to_render(env):
    env.ready()
    env.idea_message(language="ms")
    env.created()
    env.turn(MSG)
    _only_render(env, "complete")
    assert env.languages == ["ms"]


@pytest.mark.parametrize("lang", [None, "fr"])
def test_o_none_or_unknown_language_falls_back_to_en(env, lang):
    env.ready()
    env.idea_message(language=lang)
    env.created()
    env.turn(MSG)
    assert env.languages == ["en"]


def test_o_ask_idea_follows_language(env):
    env.ready()
    env.extraction("nak hantar idea", fields={}, language="ms")
    env.turn("nak hantar idea")
    _only_render(env, "ask_idea")
    assert env.languages == ["ms"]


def test_o_no_access_follows_language_and_still_no_ss_call(env):
    env.seed_workspace()
    env.seed_contact()
    env.idea_message(language="zh")
    env.turn(MSG)
    _only_render(env, "no_access")
    assert env.languages == ["zh"]
    _no_ss(env)


def test_o_unconfigured_follows_language(env):
    env.seed_workspace(product_id=None)
    env.seed_contact()
    env.seed_user()
    env.idea_message(language="ms")
    env.turn(MSG)
    _only_render(env, "unconfigured")
    assert env.languages == ["ms"]
    _no_ss(env)


def test_o_similar_offered_stores_language_on_pointer(env):
    env.ready()
    env.idea_message(language="zh")
    env.sim(2)
    out = env.turn(MSG)
    assert env.languages == ["zh"]
    assert out["session_vars"]["ideation"]["language"] == "zh"


def test_o_new_reply_renders_in_held_language(env):
    env.ready()
    held = env.idea_message("held original idea text", language="zh")
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held similar"}]
    env.created()
    env.turn("NEW", session_vars_in=env.pointer(similar, message=held, language="zh"))
    _only_render(env, "complete")
    assert env.languages == ["zh"]


def test_o_number_pick_renders_in_held_language(env):
    env.ready()
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held similar"}]
    env.turn("1", session_vars_in=env.pointer(similar, language="ms"))
    _only_render(env, "similar_picked")
    assert env.languages == ["ms"]


def test_o_held_pointer_without_language_renders_en(env):
    env.ready()
    held = env.idea_message("held original idea text")
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held similar"}]
    env.created()
    env.turn("NEW", session_vars_in=env.pointer(similar, message=held))
    assert env.languages == ["en"]


def test_o_real_ms_complete_reply_keeps_exact_facts(env):
    env.ready()
    env.idea_message(language="ms")
    idea_id = env.created()
    out = env.turn(MSG)
    reply = out["reply_text"]
    assert "IDEA-0184" in reply
    assert TITLE in reply
    assert f"{CRM}/ideas/{idea_id}" in reply
    for name in ("Proposed solution", "Impact", "Department", "Photos or files"):
        assert name in reply


# --------------------------------------------------------------------------- #
# P - the required-field decision goes through _missing_required              #
# --------------------------------------------------------------------------- #
def test_p_missing_required_empty_lets_a_problemless_message_proceed(env):
    env.ready()
    env.extraction("want to submit idea", fields={})
    env.created()
    env.mp.setattr(env.svc, "_missing_required", lambda fields: [])
    out = env.turn("want to submit idea")
    assert out["status"] == "complete"
    assert len(env.create_calls) == 1


def test_p_missing_required_problem_forces_ask_back_even_with_a_problem(env):
    env.ready()
    env.idea_message()
    env.mp.setattr(env.svc, "_missing_required", lambda fields: ["problem"])
    out = env.turn(MSG)
    assert out["status"] == "ask_idea"
    _no_ss(env)
    assert "ideation" not in out["session_vars"]


# --------------------------------------------------------------------------- #
# Q - phase 3 fix round                                                       #
# --------------------------------------------------------------------------- #
def test_q_other_session_vars_keys_survive_a_similar_offered_turn(env):
    env.ready(session_vars={"focus": {"x": 1}})
    env.idea_message()
    env.sim(2)
    out = env.turn(MSG)
    assert out["status"] == "similar_offered"
    assert out["session_vars"]["focus"] == {"x": 1}
    persisted = env.persisted()
    assert persisted["focus"] == {"x": 1}
    assert persisted["ideation"]["status"] == "similar_offered"


def test_q_other_session_vars_keys_survive_a_complete_turn(env):
    env.ready(session_vars={"focus": {"x": 1}})
    env.idea_message()
    env.created()
    out = env.turn(MSG)
    assert out["status"] == "complete"
    assert out["session_vars"]["focus"] == {"x": 1}
    assert env.persisted()["focus"] == {"x": 1}


@pytest.mark.parametrize("outcome", ["similar_offered", "complete"])
def test_q_a_key_written_by_another_writer_during_the_turn_survives(env, outcome):
    """Review SF7: the turn reads session_vars, then runs the LLM extractor and the ss calls,
    then writes. A key another writer (the chatbot tail, a parallel turn) lands in between
    must survive; the write re-reads the row and changes only `ideation`."""
    from app.services.conversation_variables_service import get_for_contact, overwrite_for_contact

    env.ready(session_vars={"focus": {"x": 1}})
    env.idea_message()
    if outcome == "similar_offered":
        env.sim(2)
    else:
        env.created()
    real_similar = env.svc.call_similar_own

    def _similar_and_concurrent_write(base_url, api_key, payload):  # noqa: ANN001
        current = get_for_contact(env.db, respond_io_id=env.rio)
        overwrite_for_contact(env.db, respond_io_id=env.rio, state={**current, "written_meanwhile": 7})
        return real_similar(base_url, api_key, payload)

    env.svc.call_similar_own = _similar_and_concurrent_write
    try:
        out = env.turn(MSG)
    finally:
        env.svc.call_similar_own = real_similar
    assert out["status"] == outcome
    persisted = env.persisted()
    assert persisted["written_meanwhile"] == 7
    assert persisted["focus"] == {"x": 1}
    assert out["session_vars"]["written_meanwhile"] == 7


@pytest.mark.parametrize("base", [None, ""])
def test_q_similar_offered_reply_has_no_none_or_dangling_link_without_a_base_url(env, base):
    env.mp.setattr(env.svc.settings, "frontend_base_url", base)
    env.ready()
    env.idea_message()
    ideas = env.sim(2)
    reply = env.turn(MSG)["reply_text"]
    assert "None" not in reply
    numbered = [ln for ln in reply.splitlines() if ln.lstrip()[:2] in ("1.", "2.")]
    assert len(numbered) == 2
    for ln, idea in zip(numbered, ideas):
        assert idea["title"] in ln
        assert " - " not in ln
        assert not ln.rstrip().endswith("-")


@pytest.mark.parametrize("base", [None, ""])
def test_q_similar_picked_reply_has_no_none_or_dangling_colon_without_a_base_url(env, base):
    env.mp.setattr(env.svc.settings, "frontend_base_url", base)
    env.ready()
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "First held idea"}]
    reply = env.turn("1", session_vars_in=env.pointer(similar))["reply_text"]
    assert "None" not in reply
    assert not reply.endswith(": ")
    assert not reply.rstrip().endswith(":")


def test_q_live_turn_ignores_a_pointer_that_only_the_caller_carries(env):
    env.ready()
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held"}]
    env.extraction("1", fields={})
    out = env.raw_turn("1", session_vars_in=env.pointer(similar), is_test=False)
    assert env.extractor_calls == ["1"]
    assert out["status"] != "similar_picked"


def test_q_live_turn_reads_the_pointer_from_the_db_row(env):
    env.ready()
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held"}]
    env.contact.session_vars = env.pointer(similar)
    env.db.flush()
    out = env.raw_turn("1", session_vars_in=None, is_test=False)
    assert out["status"] == "similar_picked"
    assert env.extractor_calls == []


def test_q_test_turn_still_honours_the_callers_pointer(env):
    env.ready()
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held"}]
    out = env.raw_turn("1", session_vars_in=env.pointer(similar, is_test=True), is_test=True)
    assert out["status"] == "similar_picked"
    assert env.extractor_calls == []


@pytest.mark.parametrize("bad", ["x", None, 7, ["a"]])
def test_q_malformed_held_item_is_a_fresh_message_not_an_error(env, bad):
    env.ready()
    sv = env.pointer([])
    sv["ideation"]["similar"] = [bad]
    env.extraction("1", fields={})
    out = env.turn("1", session_vars_in=sv)
    assert env.extractor_calls == ["1"]
    assert out["status"] == "ask_idea"


# --------------------------------------------------------------------------- #
# R - a vague reply to the ask-back never loops silently                      #
# --------------------------------------------------------------------------- #
def test_r_ask_reply_without_a_problem_gives_up(env):
    env.ready()
    env.extraction("hmm not sure", fields={}, language="ms")
    out = env.turn_ask("hmm not sure")
    assert out["status"] == "ask_idea_gave_up"
    assert out["reply_text"]
    facts = _only_render(env, "ask_idea_gave_up")
    assert isinstance(facts, dict)
    assert env.languages == ["ms"]
    _no_ss(env)
    assert "ideation" not in out["session_vars"]
    assert "ideation" not in env.persisted()


def test_r_ask_reply_with_a_problem_runs_the_normal_flow_to_create(env):
    env.ready()
    env.idea_message()
    env.created()
    out = env.turn_ask(MSG)
    assert out["status"] == "complete"
    assert len(env.similar_calls) == 1
    assert len(env.create_calls) == 1


def test_r_ask_reply_with_a_problem_runs_the_normal_flow_to_a_list(env):
    env.ready()
    env.idea_message()
    env.sim(2)
    out = env.turn_ask(MSG)
    assert out["status"] == "similar_offered"
    assert env.create_calls == []


def test_r_without_ask_reply_no_problem_is_still_ask_idea(env):
    env.ready()
    env.extraction("hmm not sure", fields={})
    out = env.turn("hmm not sure")
    assert out["status"] == "ask_idea"
    _only_render(env, "ask_idea")
    _no_ss(env)


def test_r_ask_reply_false_explicit_is_ask_idea(env):
    env.ready()
    env.extraction("hmm not sure", fields={})
    out = env.raw_turn("hmm not sure", ask_reply=False)
    assert out["status"] == "ask_idea"


@pytest.mark.parametrize("lang", ["en", "ms", "zh"])
def test_r_response_carries_the_language_for_ask_idea(env, lang):
    env.ready()
    env.extraction("want to submit idea", fields={}, language=lang)
    out = env.turn("want to submit idea")
    assert out["status"] == "ask_idea"
    assert out["language"] == lang


def test_r_response_carries_the_language_for_ask_idea_gave_up(env):
    env.ready()
    env.extraction("hmm", fields={}, language="zh")
    assert env.turn_ask("hmm")["language"] == "zh"


def test_r_response_carries_the_language_for_complete(env):
    env.ready()
    env.idea_message(language="ms")
    env.created()
    out = env.turn(MSG)
    assert out["status"] == "complete"
    assert out["language"] == "ms"


def test_r_response_carries_the_language_for_no_access(env):
    env.seed_workspace()
    env.seed_contact()
    env.idea_message(language="zh")
    out = env.turn(MSG)
    assert out["status"] == "no_access"
    assert out["language"] == "zh"


def test_r_language_falls_back_to_en_in_the_response(env):
    env.ready()
    env.idea_message(language="fr")
    env.created()
    assert env.turn(MSG)["language"] == "en"


@pytest.mark.parametrize("lang", ["en", "ms", "zh"])
def test_r_real_give_up_copy_is_a_statement_that_differs_from_the_ask(lang):
    from app.services.ideation_capture_replies import render_reply

    give_up = render_reply("ask_idea_gave_up", {}, user_message="hmm", language=lang)
    ask = render_reply("ask_idea", {}, user_message="hmm", language=lang)
    assert give_up.strip()
    assert "?" not in give_up and "？" not in give_up
    assert give_up != ask


def test_r_give_up_copy_key_is_registered():
    from app.services.chatbot_reply_copy import CHATBOT_REPLY_COPY

    for key in ("ideation_capture_give_up", "ideation_capture_give_up.ms", "ideation_capture_give_up.zh"):
        assert key in CHATBOT_REPLY_COPY, key


# ---- endpoint: ask_reply in, language out ----------------------------------------------------
@pytest.fixture
def turn_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from app.dependencies import get_db, get_external_api_user
    from tests._external_auth import external_permissions_granted

    seen: dict = {}

    def _fake(db, **kw):  # noqa: ANN001
        seen.clear()
        seen.update(kw)
        return {"status": "ask_idea", "reply_text": "Sure", "session_vars": {},
                "offered_media": [], "language": "ms"}

    monkeypatch.setattr("app.api.v1.external.ideation.handle_capture_turn", _fake)
    monkeypatch.setattr(
        "app.api.v1.external.ideation.IntegrationLogService.create_integration_log",
        lambda self, log_data, request_payload_dict=None: None,
    )
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_external_api_user] = lambda: {"id": "system"}
    try:
        with external_permissions_granted():
            yield TestClient(app), seen
    finally:
        app.dependency_overrides.clear()


_TURN = "/api/v1/external/ideation/turn"


def test_r_endpoint_forwards_ask_reply_true(turn_endpoint):
    client, seen = turn_endpoint
    resp = client.post(_TURN, json={"respond_io_id": "rio-1", "message_text": "hmm", "ask_reply": True})
    assert resp.status_code == 200, resp.text
    assert seen["ask_reply"] is True


def test_r_endpoint_ask_reply_absent_means_false(turn_endpoint):
    client, seen = turn_endpoint
    resp = client.post(_TURN, json={"respond_io_id": "rio-1", "message_text": "hmm"})
    assert resp.status_code == 200, resp.text
    assert seen["ask_reply"] is False


def test_r_endpoint_response_carries_language(turn_endpoint):
    client, _ = turn_endpoint
    resp = client.post(_TURN, json={"respond_io_id": "rio-1", "message_text": "hmm"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["language"] == "ms"


# --------------------------------------------------------------------------- #
# S - has_idea: an intent-only message never reaches ss, even with a problem   #
# --------------------------------------------------------------------------- #
def _with_has_idea(env, has_idea):
    env.idea_message()
    # set after construction so a missing dataclass field fails in the service, not here
    env.extractions[MSG].has_idea = has_idea


def test_s_has_idea_false_with_a_problem_asks_back_and_calls_no_ss(env):
    env.ready()
    _with_has_idea(env, False)
    out = env.turn(MSG)
    assert out["status"] == "ask_idea"
    assert out["reply_text"]
    _no_ss(env)
    assert "ideation" not in out["session_vars"]
    assert "ideation" not in env.persisted()


def test_s_has_idea_true_with_a_problem_runs_the_normal_flow(env):
    env.ready()
    _with_has_idea(env, True)
    env.created()
    out = env.turn(MSG)
    assert out["status"] == "complete"
    assert len(env.similar_calls) == 1
    assert len(env.create_calls) == 1


def test_s_has_idea_none_with_a_problem_runs_the_normal_flow(env):
    env.ready()
    _with_has_idea(env, None)
    env.created()
    out = env.turn(MSG)
    assert out["status"] == "complete"
    assert len(env.similar_calls) == 1
    assert len(env.create_calls) == 1


def test_s_has_idea_false_on_an_ask_reply_gives_up_and_calls_no_ss(env):
    env.ready()
    _with_has_idea(env, False)
    out = env.turn_ask(MSG)
    assert out["status"] == "ask_idea_gave_up"
    _no_ss(env)
    assert "ideation" not in out["session_vars"]


# --------------------------------------------------------------------------- #
# T - permanent ss failures answer config_error, transient ones stay error     #
# --------------------------------------------------------------------------- #
_PERMANENT = [401, 403, 404, 409, 422]
_TRANSIENT = [500, 502, 503, 408, 429, None]
_CONFIG_ERROR_EN = (
    "Idea capture isn't set up correctly here, so I couldn't save that. "
    "Please let your Sorento contact know."
)


def _ss_error(status_code):
    return IdeationServiceError("boom", status_code=status_code)


def _create_scenario(env, where, status_code):
    """Run a turn whose CREATE call fails. Returns (out, pointer_in or None)."""
    env.ready()
    env.create_result = _ss_error(status_code)
    if where == "fresh":
        env.idea_message()
        return env.turn(MSG), None
    held = env.idea_message("held original idea text")
    similar = [{"idea_id": _uid(), "idea_number": "IDEA-0151", "title": "Held"}]
    sv = env.pointer(similar, message=held)
    return env.turn("NEW", session_vars_in=sv), sv


@pytest.mark.parametrize("status_code", _PERMANENT)
def test_t_similar_own_permanent_failure_is_config_error(env, status_code):
    env.ready()
    env.idea_message()
    env.similar_result = _ss_error(status_code)
    out = env.turn(MSG)
    assert out["status"] == "config_error"
    assert env.renders[-1][0] == "config_error"
    assert env.create_calls == []
    assert "ideation" not in out["session_vars"]


@pytest.mark.parametrize("status_code", _TRANSIENT)
def test_t_similar_own_transient_failure_stays_error(env, status_code):
    env.ready()
    env.idea_message()
    env.similar_result = _ss_error(status_code)
    out = env.turn(MSG)
    assert out["status"] == "error"
    assert env.renders[-1][0] == "error"
    assert env.create_calls == []
    assert "ideation" not in out["session_vars"]


@pytest.mark.parametrize("where", ["fresh", "held_new"])
@pytest.mark.parametrize("status_code", _PERMANENT)
def test_t_create_permanent_failure_is_config_error(env, where, status_code):
    out, sv = _create_scenario(env, where, status_code)
    assert out["status"] == "config_error"
    assert env.renders[-1][0] == "config_error"
    assert "link" not in out
    if sv is not None:
        # pointer untouched, as for "error"
        assert out["session_vars"]["ideation"] == sv["ideation"]
    else:
        assert "ideation" not in out["session_vars"]


@pytest.mark.parametrize("where", ["fresh", "held_new"])
@pytest.mark.parametrize("status_code", _TRANSIENT)
def test_t_create_transient_failure_stays_error(env, where, status_code):
    out, sv = _create_scenario(env, where, status_code)
    assert out["status"] == "error"
    assert env.renders[-1][0] == "error"
    if sv is not None:
        assert out["session_vars"]["ideation"] == sv["ideation"]


@pytest.mark.parametrize("step", ["similar_own", "create"])
def test_t_config_error_is_logged_at_error_level_with_status(env, caplog, step):
    import logging

    env.ready()
    env.idea_message()
    if step == "similar_own":
        env.similar_result = _ss_error(403)
    else:
        env.create_result = _ss_error(403)
    with caplog.at_level(logging.DEBUG):
        out = env.turn(MSG)
    assert out["status"] == "config_error"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "config_error must log at ERROR"
    assert any("403" in r.getMessage() for r in errors)


def test_t_config_error_logs_the_ss_code(env, caplog):
    import logging

    env.ready()
    env.idea_message()
    env.similar_result = IdeationServiceError(
        "boom", status_code=403, response_detail={"error": {"code": "embed_scope_required"}}
    )
    with caplog.at_level(logging.DEBUG):
        env.turn(MSG)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("embed_scope_required" in m for m in errors), errors


def test_t_config_error_copy_registered_and_clean():
    from app.services.chatbot_reply_copy import CHATBOT_REPLY_COPY
    from app.services.ideation_capture_replies import render_reply

    for key in (
        "ideation_capture_config_error",
        "ideation_capture_config_error.ms",
        "ideation_capture_config_error.zh",
    ):
        assert key in CHATBOT_REPLY_COPY, key

    assert render_reply("config_error", {}, user_message="x", language="en") == _CONFIG_ERROR_EN
    for lang in ("en", "ms", "zh"):
        text = render_reply("config_error", {}, user_message="x", language=lang)
        assert text.strip()
        assert "?" not in text and "？" not in text
        assert text != render_reply("error", {}, user_message="x", language=lang)
    assert "try again" not in render_reply("config_error", {}, user_message="x", language="en").lower()


def test_t_endpoint_carries_config_error_status(monkeypatch):
    from fastapi.testclient import TestClient

    from app.dependencies import get_db, get_external_api_user
    from tests._external_auth import external_permissions_granted

    def _fake(db, **kw):  # noqa: ANN001
        return {"status": "config_error", "reply_text": "x", "session_vars": {},
                "offered_media": [], "language": "en"}

    monkeypatch.setattr("app.api.v1.external.ideation.handle_capture_turn", _fake)
    monkeypatch.setattr(
        "app.api.v1.external.ideation.IntegrationLogService.create_integration_log",
        lambda self, log_data, request_payload_dict=None: None,
    )
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_external_api_user] = lambda: {"id": "system"}
    try:
        with external_permissions_granted():
            resp = TestClient(app).post(_TURN, json={"respond_io_id": "rio-1", "message_text": "hmm"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "config_error"
