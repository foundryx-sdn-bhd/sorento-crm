"""CS-ROUTING-TARGETS: a routing rule can pin any eligible PIC, and saving the
rules page never loses rules.

Owner, 14 Oct 2026: a CS user can leave the tier-1 Project Customer Service team
and still be the target of a salesman's routing rule. "Eligible PIC" is one rule,
``app.services.cs_routing_service.is_eligible_pic``:

* ``users.status`` ACTIVE, not trashed, not an integration user;
* holds ``procurement.purchase_requests.process`` through a role OTHER than
  admin/superadmin (those roles pass every permission check, so they say nothing
  about who processes purchase requests - crew answer Q1);
* holds a ``user_companies`` grant for the company in play: Sorento for the
  dropdown and the save check (as the tier-1 team lookup was), the pinned
  contact's own company at assignment time.

The same rule backs the dropdown (``list_candidates``), the save check and the
form SLA resolver, and the pin is consulted only on the purchase_request agent's
customer_service stage (crew answer Q3): the submit and approval stages never read
it, so a pinned CS user can never take a stage that is not theirs.

Saving is one ``PUT /contacts/{id}/cs-routing`` carrying every rule. Every rule is
validated before anything is deleted, so one bad rule returns an error naming it by
the "Priority N" label the page shows (``priority + 1``, reviewer round 1) and the
stored rules stay exactly as they were. The old per-use_case PUT/DELETE and
the new bulk PUT all require ``user_management.contacts.edit`` (crew answer Q4).

All tests run on the blank Postgres schema (tests/_pg_fixture.py), which holds only
what each test seeds.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves the circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402
from app.dependencies import get_current_user, get_db
from app.models.access import (
    AccessAgent,
    AgentTeam,
    RespondContact,
    RespondContactCsRouting,
    Team,
    TeamMember,
)
from app.models.company import Company, UserCompany
from app.models.notification import Notification
from app.models.sla import FormSLAConfig, SLAPolicy, SLAPolicyTier
from app.models.user import (
    User,
    UserPermission,
    UserRole,
    UserRoleAssignment,
    UserRolePermission,
)
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session, unique_code

SORENTO = "00000000-0000-0000-0000-000000000001"
PROCESS = "procurement.purchase_requests.process"
CONTACTS_EDIT = "user_management.contacts.edit"
BASE = "/api/v1/user-management/contacts"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def _get_or_create_permission(db, slug: str) -> UserPermission:
    row = db.query(UserPermission).filter(UserPermission.slug == slug).first()
    if row is None:
        row = UserPermission(id=str(uuid.uuid4()), slug=slug, name=slug)
        db.add(row)
        db.flush()
    return row


def _get_or_create_role(db, slug: str, permission_slugs: list[str]) -> UserRole:
    role = db.query(UserRole).filter(UserRole.slug == slug).first()
    if role is None:
        role = UserRole(id=str(uuid.uuid4()), slug=slug, name=unique_code(slug))
        db.add(role)
        db.flush()
    for p in permission_slugs:
        perm = _get_or_create_permission(db, p)
        exists = (
            db.query(UserRolePermission)
            .filter(
                UserRolePermission.role_id == role.id,
                UserRolePermission.permission_id == perm.id,
            )
            .first()
        )
        if exists is None:
            db.add(UserRolePermission(id=str(uuid.uuid4()), role_id=role.id, permission_id=perm.id))
    db.flush()
    return role


def _user(db, name, *, roles=(), companies=(SORENTO,), status="ACTIVE", trashed=False, integration=False):
    uid = str(uuid.uuid4())
    db.add(
        User(
            id=uid,
            email=f"{uid}@t.zzt",
            name=name,
            status=status,
            is_trashed=trashed,
            is_integration=integration,
            # In-app only: no email/whatsapp delivery is queued from a test.
            notify_email_on_assignment=False,
            notify_email_on_escalation=False,
        )
    )
    db.flush()
    for role in roles:
        db.add(UserRoleAssignment(id=str(uuid.uuid4()), user_id=uid, role_id=role.id))
    for company_id in companies:
        db.add(UserCompany(id=str(uuid.uuid4()), user_id=uid, company_id=company_id))
    db.flush()
    return uid


def _team(db, agent_id, team_set_code, tier, members, *, company_id=SORENTO):
    """A team wired to ``agent_id`` at ``tier`` of ``team_set_code``. ``members`` is a
    list of ``(user_id, include_in_round_robin)``."""
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name=unique_code("Team"), company_id=company_id))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code=team_set_code,
            team_id=team_id,
            tier=tier,
            company_id=company_id,
        )
    )
    db.flush()
    for i, (uid, in_rr) in enumerate(members):
        db.add(
            TeamMember(
                id=str(uuid.uuid4()),
                team_id=team_id,
                user_id=uid,
                sort_order=i,
                include_in_round_robin=in_rr,
            )
        )
    db.flush()
    return team_id


def _policy(db, tiers=(1,)):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code=unique_code("POL"), name="ZZT policy"))
    for t in tiers:
        db.add(
            SLAPolicyTier(
                id=str(uuid.uuid4()),
                policy_id=policy_id,
                tier_level=t,
                tier_name=f"Tier {t}",
                response_hours=4,
                resolution_hours=24,
            )
        )
    db.flush()
    return policy_id


def _config(db, policy_id, team_set_code, start_event, resolve_event):
    cfg = FormSLAConfig(
        id=str(uuid.uuid4()),
        source_entity_type="purchase_request",
        stage_code=unique_code("stage"),
        policy_id=policy_id,
        agent_code="purchase_request",
        team_set_code=team_set_code,
        start_event=start_event,
        resolve_event=resolve_event,
        is_active=True,
        notify_assignee=True,
    )
    db.add(cfg)
    db.flush()
    return cfg


def _seed(db) -> SimpleNamespace:
    """The procurement CS world: the purchase_request access agent, its tier-1
    customer_service team, a second company, and one user per eligibility case.

    Names are chosen so the eligible set sorts differently by name than by team
    order or insert order, and so a case-sensitive sort would put the lowercase
    "zzt bella" last instead of first.
    """
    cs_role = _get_or_create_role(db, unique_code("cs_role"), [PROCESS, CONTACTS_EDIT])
    admin_role = _get_or_create_role(db, "admin", [PROCESS, CONTACTS_EDIT])
    plain_role = _get_or_create_role(db, unique_code("plain_role"), [CONTACTS_EDIT])

    mocha = Company(id=str(uuid.uuid4()), name="ZZT Mocha", code=unique_code("MCH")[:50])
    db.add(mocha)
    db.flush()

    s = SimpleNamespace(cs_role=cs_role, admin_role=admin_role, mocha_id=mocha.id)
    # Eligible.
    s.in_team = _user(db, "ZZT Dina Team", roles=[cs_role])
    s.outside = _user(db, "zzt bella outside", roles=[cs_role])
    s.admin_plus = _user(db, "ZZT Cara AdminPlus", roles=[admin_role, cs_role])
    # Not eligible.
    s.admin_only = _user(db, "ZZT Aaron AdminOnly", roles=[admin_role])
    s.no_perm_member = _user(db, "ZZT Abel NoPerm", roles=[plain_role])
    s.inactive = _user(db, "ZZT Alex Inactive", roles=[cs_role], status="INACTIVE")
    s.trashed = _user(db, "ZZT Amy Trashed", roles=[cs_role], trashed=True)
    s.integration = _user(db, "ZZT Ana Integration", roles=[cs_role], integration=True)
    s.mocha_only = _user(db, "ZZT Andy Mocha", roles=[cs_role], companies=[mocha.id])
    s.no_grant = _user(db, "ZZT Ari NoGrant", roles=[cs_role], companies=())

    s.agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=s.agent_id, code="purchase_request", name="ZZT Purchase Request"))
    db.flush()
    # The no-permission member is in the team but never drawn by round-robin, so a
    # round-robin assignment is always ``in_team``.
    s.cs_team = _team(
        db, s.agent_id, "customer_service", 1, [(s.no_perm_member, False), (s.in_team, True)]
    )

    s.contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=s.contact_id,
            phone_number=f"+60{uuid.uuid4().hex[:9]}",
            name=unique_code("salesman"),
        )
    )
    db.flush()
    return s


def _pin(db, contact_id, user_id, *, use_case="purchase_request", conditions=None, priority=0):
    row = RespondContactCsRouting(
        id=str(uuid.uuid4()),
        respond_contact_id=contact_id,
        use_case=use_case,
        cs_pic_user_id=user_id,
        match_conditions=conditions or [],
        priority=priority,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _client(db, monkeypatch, *, granted=True, requested=None) -> TestClient:
    user = {"id": str(uuid.uuid4())}

    def _db_override():
        yield db

    def _check(self, uid, slug):
        if requested is not None:
            requested.append(slug)
        return granted

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", _check)
    return TestClient(app)


def _stored(db, contact_id):
    rows = (
        db.query(RespondContactCsRouting)
        .filter(RespondContactCsRouting.respond_contact_id == contact_id)
        .order_by(RespondContactCsRouting.use_case, RespondContactCsRouting.priority)
        .all()
    )
    return [(r.id, r.use_case, r.cs_pic_user_id, r.priority) for r in rows]


CASH = [{"field": "sales_type", "operator": "equals", "value": "cash_sales"}]
PROJECT = [{"field": "sales_type", "operator": "equals", "value": "project"}]


# ---------------------------------------------------------------------------
# 1. Save never loses rules
# ---------------------------------------------------------------------------


def test_save_with_one_ineligible_rule_keeps_every_stored_rule(monkeypatch):
    """The reported loss: rule 2 targets a user who cannot be pinned. Today the page
    DELETEs every use_case first and PUTs rule by rule, so rule 2's error leaves the
    contact with rule 1 only. Now the whole save is refused and nothing changes."""
    with blank_session() as db:
        s = _seed(db)
        _pin(db, s.contact_id, s.in_team, conditions=PROJECT, priority=0)
        _pin(db, s.contact_id, s.outside, conditions=CASH, priority=1)
        _pin(db, s.contact_id, s.in_team, use_case="sponsorship_form", priority=2)
        before = _stored(db, s.contact_id)

        # Priority 2 on the page is an incomplete rule (no PIC yet) the page does not
        # send, so the refused rule is the second one sent but the page's Priority 3.
        r = _client(db, monkeypatch).put(
            f"{BASE}/{s.contact_id}/cs-routing",
            json={
                "rules": [
                    {"use_case": "purchase_request", "cs_pic_user_id": s.in_team, "match_conditions": PROJECT, "priority": 0},
                    {"use_case": "purchase_request", "cs_pic_user_id": s.admin_only, "match_conditions": CASH, "priority": 2},
                    {"use_case": "sponsorship_form", "cs_pic_user_id": s.outside, "match_conditions": [], "priority": 3},
                ]
            },
        )

        assert r.status_code == 400, r.text
        assert r.json()["message"].startswith("Priority 3: ZZT Aaron AdminOnly cannot be assigned")
        assert _stored(db, s.contact_id) == before


def test_save_replaces_every_rule_in_one_call(monkeypatch):
    with blank_session() as db:
        s = _seed(db)
        _pin(db, s.contact_id, s.in_team, conditions=PROJECT, priority=0)
        _pin(db, s.contact_id, s.in_team, use_case="sponsorship_form", priority=1)

        r = _client(db, monkeypatch).put(
            f"{BASE}/{s.contact_id}/cs-routing",
            json={
                "rules": [
                    {"use_case": "purchase_request", "cs_pic_user_id": s.outside, "match_conditions": CASH, "priority": 0},
                    {"use_case": "purchase_request", "cs_pic_user_id": s.in_team, "match_conditions": [], "priority": 1},
                ]
            },
        )

        assert r.status_code == 200, r.text
        pins = r.json()["pins"]
        assert [(p["use_case"], p["cs_pic_user_id"], p["priority"], p["match_conditions"]) for p in pins] == [
            ("purchase_request", s.outside, 0, CASH),
            ("purchase_request", s.in_team, 1, []),
        ]
        assert pins[0]["cs_pic_name"] == "zzt bella outside"
        # The sponsorship rule that was not sent is gone: save = replace.
        assert [(uc, uid, prio) for _, uc, uid, prio in _stored(db, s.contact_id)] == [
            ("purchase_request", s.outside, 0),
            ("purchase_request", s.in_team, 1),
        ]


def test_save_with_two_rules_on_the_same_conditions_is_refused(monkeypatch):
    """Two rules for one use_case with the same condition set used to collapse into
    one row silently (the second PUT overwrote the first). Now it is an error that
    names both rules, and nothing changes."""
    with blank_session() as db:
        s = _seed(db)
        _pin(db, s.contact_id, s.in_team, priority=0)
        before = _stored(db, s.contact_id)

        r = _client(db, monkeypatch).put(
            f"{BASE}/{s.contact_id}/cs-routing",
            json={
                "rules": [
                    {"use_case": "purchase_request", "cs_pic_user_id": s.in_team, "match_conditions": CASH, "priority": 1},
                    {"use_case": "purchase_request", "cs_pic_user_id": s.outside, "match_conditions": list(reversed(CASH)), "priority": 2},
                ]
            },
        )

        assert r.status_code == 400, r.text
        assert r.json()["message"].startswith(
            "Priority 3 has the same conditions as Priority 2 for Purchase Request"
        )
        assert _stored(db, s.contact_id) == before


def test_refused_rule_never_shows_an_email(monkeypatch):
    """A user with no name is "The selected user", never their email (security
    review N1: the message must not reveal any user's address)."""
    with blank_session() as db:
        s = _seed(db)
        nameless = _user(db, None, roles=[])
        email = db.query(User).filter(User.id == nameless).one().email

        r = _client(db, monkeypatch).put(
            f"{BASE}/{s.contact_id}/cs-routing",
            json={"rules": [{"use_case": "purchase_request", "cs_pic_user_id": nameless, "priority": 0}]},
        )

        assert r.status_code == 400, r.text
        message = r.json()["message"]
        assert message.startswith("Priority 1: The selected user cannot be assigned")
        assert email not in message


def test_save_caps_the_number_of_rules(monkeypatch):
    """Security review N2: one request cannot carry an unbounded list."""
    with blank_session() as db:
        s = _seed(db)
        rule = {"use_case": "purchase_request", "cs_pic_user_id": s.in_team, "priority": 0}

        r = _client(db, monkeypatch).put(
            f"{BASE}/{s.contact_id}/cs-routing", json={"rules": [rule] * 101}
        )

        assert r.status_code == 422, r.text


def test_save_with_no_rules_clears_the_contact(monkeypatch):
    with blank_session() as db:
        s = _seed(db)
        _pin(db, s.contact_id, s.in_team)

        r = _client(db, monkeypatch).put(f"{BASE}/{s.contact_id}/cs-routing", json={"rules": []})

        assert r.status_code == 200, r.text
        assert r.json()["pins"] == []
        assert _stored(db, s.contact_id) == []


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("put", "{base}/{cid}/cs-routing", {"rules": []}),
        ("put", "{base}/{cid}/cs-routing/purchase_request", {"cs_pic_user_id": "x"}),
        ("delete", "{base}/{cid}/cs-routing/purchase_request", None),
    ],
)
def test_routing_writes_require_contacts_edit(monkeypatch, method, path, body):
    with blank_session() as db:
        s = _seed(db)
        url = path.format(base=BASE, cid=s.contact_id)

        requested: list[str] = []
        client = _client(db, monkeypatch, granted=False, requested=requested)
        kwargs = {"json": body} if body is not None else {}
        r = getattr(client, method)(url, **kwargs)

        assert r.status_code == 403, r.text
        assert CONTACTS_EDIT in requested


# ---------------------------------------------------------------------------
# 2 + 3. Eligibility: one rule for the dropdown, the save check and assignment
# ---------------------------------------------------------------------------


def test_dropdown_lists_active_permission_holders_sorted_by_name(monkeypatch):
    with blank_session() as db:
        s = _seed(db)

        r = _client(db, monkeypatch).get(f"{BASE}/cs-routing/candidates")

        assert r.status_code == 200, r.text
        assert [(c["id"], c["name"]) for c in r.json()["candidates"]] == [
            (s.outside, "zzt bella outside"),
            (s.admin_plus, "ZZT Cara AdminPlus"),
            (s.in_team, "ZZT Dina Team"),
        ]


@pytest.mark.parametrize(
    "attr, eligible",
    [
        ("in_team", True),
        ("outside", True),
        ("admin_plus", True),
        ("admin_only", False),
        ("no_perm_member", False),
        ("inactive", False),
        ("trashed", False),
        ("integration", False),
        ("mocha_only", False),
        ("no_grant", False),
    ],
)
def test_is_eligible_pic(attr, eligible):
    from app.services.cs_routing_service import is_eligible_pic

    with blank_session() as db:
        s = _seed(db)
        assert is_eligible_pic(db, getattr(s, attr)) is eligible


def test_eligibility_follows_the_company_in_play():
    from app.services.cs_routing_service import is_eligible_pic

    with blank_session() as db:
        s = _seed(db)
        assert is_eligible_pic(db, s.mocha_only, company_id=s.mocha_id) is True
        assert is_eligible_pic(db, s.outside, company_id=s.mocha_id) is False
        assert is_eligible_pic(db, "no-such-user") is False


# ---------------------------------------------------------------------------
# 2 + 4. Assignment: a pinned PIC outside the team gets the CS stage, and My
# Pending, notifications and escalation keep working for them
# ---------------------------------------------------------------------------


def _start(db, cfg, contact_id):
    from app.services.form_sla_service import FormSLAOrchestrator

    return FormSLAOrchestrator(db)._start_for_config(
        cfg, str(uuid.uuid4()), contact_id=contact_id
    )


def test_cs_stage_goes_to_a_pinned_pic_outside_the_team():
    from app.services.sla_service import ConversationSLATrackingService

    with blank_session() as db:
        s = _seed(db)
        cfg = _config(db, _policy(db), "customer_service", "approved", "resolved,voided")
        _pin(db, s.contact_id, s.outside, conditions=[])

        tracker = _start(db, cfg, s.contact_id)

        assert tracker.assigned_to_id == s.outside
        mine = ConversationSLATrackingService(db).list_my_pending(s.outside)
        assert str(tracker.id) in {str(row["id"]) for row in mine}
        assert (
            db.query(Notification)
            .filter(
                Notification.user_id == s.outside,
                Notification.source_entity_id == str(tracker.id),
                Notification.event_type == "assigned",
            )
            .count()
            == 1
        )


def test_cs_stage_pin_to_an_ineligible_user_falls_back_to_round_robin():
    """The pinned user is even a member of the tier-1 team - membership no longer
    qualifies anyone, the permission does."""
    with blank_session() as db:
        s = _seed(db)
        cfg = _config(db, _policy(db), "customer_service", "approved", "resolved,voided")
        _pin(db, s.contact_id, s.no_perm_member, conditions=[])

        tracker = _start(db, cfg, s.contact_id)

        assert tracker.assigned_to_id == s.in_team


def test_pin_is_never_read_on_the_submit_stage():
    with blank_session() as db:
        s = _seed(db)
        sales_exec = _user(db, "ZZT Sales Exec", roles=[])
        _team(db, s.agent_id, "project_sales", 1, [(sales_exec, True)])
        cfg = _config(db, _policy(db), "project_sales", "submit", "send_for_approval")
        _pin(db, s.contact_id, s.outside, conditions=[])

        tracker = _start(db, cfg, s.contact_id)

        assert tracker.assigned_to_id == sales_exec


def test_escalation_from_a_pinned_pic_outside_the_team():
    from app.services.form_sla_service import FormSLAOrchestrator

    with blank_session() as db:
        s = _seed(db)
        supervisor = _user(db, "ZZT CS Supervisor", roles=[s.cs_role])
        _team(db, s.agent_id, "customer_service", 2, [(supervisor, True)])
        cfg = _config(db, _policy(db, tiers=(1, 2)), "customer_service", "approved", "resolved,voided")
        _pin(db, s.contact_id, s.outside, conditions=[])
        tracker = _start(db, cfg, s.contact_id)
        assert tracker.assigned_to_id == s.outside

        FormSLAOrchestrator(db)._escalate_tracker(tracker, trigger="manual", reason="ZZT overdue")

        db.refresh(tracker)
        assert tracker.current_tier == 2
        assert tracker.assigned_to_id == supervisor
