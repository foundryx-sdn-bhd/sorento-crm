"""The salesperson master over HTTP: list it, and annotate the two columns S1 added.

    GET   /api/v1/master-data/sales-agents
    PATCH /api/v1/master-data/sales-agents/{id}/annotation

Three things this file exists to pin, each of which is a way the screen can look
finished while holding nothing:

1. **The field list.** ``response_model`` silently drops any field the schema does not
   declare, so an agent whose ``demand_class`` the captain just set would come back
   without it and the row would read as unclassified. Every declared field is asserted
   on the wire, including on a ``source='import'`` row - the rows S1 actually creates.
2. **``extra="forbid"`` on the PATCH body.** The annotation body is the only write this
   page performs; a typo'd field name must be refused rather than silently ignored,
   because "saved" with nothing written is the failure that looks like success.
3. **The vocabulary guard is the SERVICE's**, not the route's. A word outside
   ``DEMAND_CLASSES`` is refused by ``sales_agent_service.assert_demand_class`` with the
   message that names the allowed words, and nothing else in the same save is written.
4. **The class lands on the row the user clicked.** The code is unique per COMPANY, so
   two rows may spell it the same; a class written by re-resolving the code could land on
   the namesake.

Postgres only, on a blank scratch schema (``blank_session``), and every row this file
creates carries the ``ZZT`` marker so nothing here can be confused with master data.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.models.access import RespondContact
from app.models.sales_agent import SalesAgent
from app.services.scm.demand_class import DEFAULT_DEMAND_CLASS, PROJECT
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/master-data/sales-agents"
_USER = {"id": str(uuid.uuid4()), "email": "captain@example.test", "role": "admin"}

#: The incumbent company every test schema is seeded with (tests/conftest.py). Used to
#: build a company-OWNED agent beside a shared one under the same code.
SORENTO_COMPANY_ID = "00000000-0000-0000-0000-000000000001"

#: Every key `SalesAgentResponse` declares. Asserted as an exact set: a missing key is
#: the response_model drop this file exists to catch, and an unexpected one means the
#: mirror schema grew a field the frontend types do not know about.
RESPONSE_KEYS = {
    "id",
    "sales_agent",
    "description",
    "is_active",
    "internal_note",
    "follow_up",
    "person_label",
    "aliases",
    "demand_class",
    "location_group",
    "contact_id",
    "contact_name",
    "source",
    "created_at",
    "updated_at",
}


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


VIEW = "master_data.sales_agents.view"
EDIT = "master_data.sales_agents.edit"


def _client(db, monkeypatch, *, granted: set[str]):
    """A client holding exactly `granted`.

    Slug-aware on purpose: a stub that answers True (or False) to everything cannot tell
    `.view` from `.edit`, so a read-only role reaching the annotation would pass such a
    test. The blank schema holds no role grants, which is why the answer is stubbed at
    all rather than seeded as a whole RBAC chain.
    """
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.services.user_service import UserPermissionService

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: _USER
    app.dependency_overrides[get_current_user_or_api_key] = lambda: _USER
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in granted,
    )
    return TestClient(app)


@pytest.fixture
def client(db, monkeypatch):
    from app.main import app

    c = _client(db, monkeypatch, granted={VIEW, EDIT})
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def denied_client(db, monkeypatch):
    """A caller holding no permissions, to prove the routes are actually gated."""
    from app.main import app

    c = _client(db, monkeypatch, granted=set())
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def read_only_client(db, monkeypatch):
    """A caller holding the view but not the edit, which is a real role shape."""
    from app.main import app

    c = _client(db, monkeypatch, granted={VIEW})
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


def _seed(db, **kwargs) -> SalesAgent:
    row = SalesAgent(
        id=str(uuid.uuid4()),
        sales_agent=kwargs.pop("sales_agent", unique_code("AGENT")),
        source=kwargs.pop("source", "manual"),
        is_active=kwargs.pop("is_active", True),
        **kwargs,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------------------- #
# List
# --------------------------------------------------------------------------- #
def test_list_returns_the_seeded_agents(client, db):
    a = _seed(db, sales_agent="ZZT SEAN I", person_label="ZZT Sean", demand_class=PROJECT)
    b = _seed(db, sales_agent="ZZT LCL", source="import")

    res = client.get(BASE)
    assert res.status_code == 200, res.text

    body = res.json()
    by_code = {r["sales_agent"]: r for r in body["data"]}
    assert {a.sales_agent, b.sales_agent} <= set(by_code)
    assert body["pagination"]["total"] >= 2
    assert by_code["ZZT SEAN I"]["person_label"] == "ZZT Sean"
    assert by_code["ZZT SEAN I"]["demand_class"] == PROJECT


def test_list_row_declares_every_field(client, db):
    _seed(db, sales_agent="ZZT SOLO", person_label="ZZT Person", demand_class=PROJECT)

    row = client.get(BASE).json()["data"][0]
    assert set(row) == RESPONSE_KEYS


def test_an_import_created_row_serialises(client, db):
    """The rows S1 actually creates carry ``source='import'``.

    The mirror schema's ``source`` started as ``Literal["autocount", "manual"]``, which
    an import-created row fails response validation on - a 500 on a list the operator
    has done nothing wrong to reach.
    """
    _seed(db, sales_agent="ZZT IMPORTED", source="import")

    res = client.get(BASE, params={"query": "ZZT IMPORTED"})
    assert res.status_code == 200, res.text

    row = res.json()["data"][0]
    assert row["source"] == "import"
    assert set(row) == RESPONSE_KEYS


def test_list_searches_on_the_code(client, db):
    _seed(db, sales_agent="ZZT FINDME")
    _seed(db, sales_agent="ZZT OTHER")

    codes = [r["sales_agent"] for r in client.get(BASE, params={"query": "FINDME"}).json()["data"]]
    assert codes == ["ZZT FINDME"]


def test_list_is_gated(denied_client):
    assert denied_client.get(BASE).status_code == 403


# --------------------------------------------------------------------------- #
# Annotation
# --------------------------------------------------------------------------- #
def test_annotation_writes_label_and_class(client, db):
    agent = _seed(db, sales_agent="ZZT SEAN III")

    res = client.patch(
        f"{BASE}/{agent.id}/annotation",
        json={"person_label": "ZZT Sean", "demand_class": PROJECT},
    )
    assert res.status_code == 200, res.text

    body = res.json()
    assert body["person_label"] == "ZZT Sean"
    assert body["demand_class"] == PROJECT
    assert set(body) == RESPONSE_KEYS

    db.expire_all()
    stored = db.query(SalesAgent).filter(SalesAgent.id == agent.id).one()
    assert stored.person_label == "ZZT Sean"
    assert stored.demand_class == PROJECT


def test_annotation_clears_the_class(client, db):
    """Unset is a value the captain can choose - a class set by mistake must come off."""
    agent = _seed(db, sales_agent="ZZT CLEARME", demand_class=DEFAULT_DEMAND_CLASS)

    res = client.patch(f"{BASE}/{agent.id}/annotation", json={"demand_class": None})
    assert res.status_code == 200, res.text
    assert res.json()["demand_class"] is None

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == agent.id).one().demand_class is None


def test_annotation_leaves_omitted_fields_alone(client, db):
    agent = _seed(db, sales_agent="ZZT KEEPME", person_label="ZZT Keep", demand_class=PROJECT)

    res = client.patch(f"{BASE}/{agent.id}/annotation", json={"person_label": "ZZT Renamed"})
    assert res.status_code == 200, res.text
    assert res.json()["demand_class"] == PROJECT


def test_a_word_outside_the_vocabulary_is_refused(client, db):
    agent = _seed(db, sales_agent="ZZT BADCLASS", demand_class=PROJECT)

    res = client.patch(f"{BASE}/{agent.id}/annotation", json={"demand_class": "dealer"})
    assert res.status_code == 400, res.text
    assert "dealer" in res.text

    db.expire_all()
    stored = db.query(SalesAgent).filter(SalesAgent.id == agent.id).one()
    assert stored.demand_class == PROJECT


def test_an_unknown_field_is_refused(client, db):
    """``extra="forbid"``: a typo'd key must fail, not save nothing and answer 200."""
    agent = _seed(db, sales_agent="ZZT EXTRA")

    res = client.patch(
        f"{BASE}/{agent.id}/annotation",
        json={"person_label": "ZZT P", "demandclass": PROJECT},
    )
    assert res.status_code == 422, res.text


def test_annotation_on_a_missing_agent_is_404(client):
    res = client.patch(f"{BASE}/{uuid.uuid4()}/annotation", json={"person_label": "ZZT P"})
    assert res.status_code == 404, res.text


def test_annotation_is_gated(denied_client, db):
    agent = _seed(db, sales_agent="ZZT GATED")

    res = denied_client.patch(f"{BASE}/{agent.id}/annotation", json={"person_label": "ZZT P"})
    assert res.status_code == 403, res.text


def test_a_read_only_role_may_list_but_not_annotate(read_only_client, db):
    """`.view` and `.edit` are different answers, and the routes must ask different ones.

    A stub that grants everything cannot see the difference, so a read-only role reaching
    the annotation would look gated when it is not.
    """
    agent = _seed(db, sales_agent="ZZT READONLY")

    assert read_only_client.get(BASE).status_code == 200
    res = read_only_client.patch(
        f"{BASE}/{agent.id}/annotation", json={"person_label": "ZZT P"}
    )
    assert res.status_code == 403, res.text


def test_a_refused_class_leaves_the_label_unwritten(client, db):
    """One save, one transaction: a rejected class must not commit the label beside it.

    Otherwise the row half-saves, the toast says it failed, and the name the user did not
    mean to keep is already stored.
    """
    agent = _seed(db, sales_agent="ZZT HALFSAVE", person_label="ZZT Old")

    res = client.patch(
        f"{BASE}/{agent.id}/annotation",
        json={"person_label": "ZZT New", "demand_class": "dealer"},
    )
    assert res.status_code == 400, res.text

    db.expire_all()
    stored = db.query(SalesAgent).filter(SalesAgent.id == agent.id).one()
    assert stored.person_label == "ZZT Old"
    assert stored.demand_class is None


def test_an_over_long_person_label_is_refused(client, db):
    """`person_label` is varchar(100).

    Without a bound on the body the write dies in the flush and the raw
    `StringDataRightTruncation` reaches the user's toast.
    """
    agent = _seed(db, sales_agent="ZZT LONGLABEL")

    res = client.patch(
        f"{BASE}/{agent.id}/annotation", json={"person_label": "Z" * 101}
    )
    assert res.status_code == 422, res.text

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == agent.id).one().person_label is None


# --------------------------------------------------------------------------- #
# Retiring a code
# --------------------------------------------------------------------------- #
# `is_active` decides whether a code is offered by the Agent pickers
# (`sales_order_service.list_agents` filters on it). Until the record page carried a switch
# for it there was no way to retire a code at all, so a person who had left was still
# offered on every order. It is sales-agent-only: the shared `MirrorAnnotationUpdate` the
# other mirror entities use does not accept it, because for them the column is synced and a
# UI write would be clobbered by the next re-sync.
def test_the_patch_can_retire_an_agent(client, db):
    agent = _seed(db, sales_agent="ZZT RETIRE", is_active=True)

    res = client.patch(f"{BASE}/{agent.id}/annotation", json={"is_active": False})
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is False

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == agent.id).one().is_active is False


def test_the_patch_can_bring_a_retired_agent_back(client, db):
    agent = _seed(db, sales_agent="ZZT REHIRE", is_active=False)

    res = client.patch(f"{BASE}/{agent.id}/annotation", json={"is_active": True})
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is True

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == agent.id).one().is_active is True


def test_omitting_is_active_leaves_it_alone(client, db):
    """The same `model_fields_set` rule as every other field: a save that only renames the
    person must not quietly reactivate a code somebody retired."""
    agent = _seed(db, sales_agent="ZZT KEEPRETIRED", is_active=False)

    res = client.patch(f"{BASE}/{agent.id}/annotation", json={"person_label": "ZZT P"})
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is False

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == agent.id).one().is_active is False


def test_a_refused_class_leaves_the_activation_unwritten(client, db):
    """One save, one transaction - the same rule the label already follows."""
    agent = _seed(db, sales_agent="ZZT HALFRETIRE", is_active=True)

    res = client.patch(
        f"{BASE}/{agent.id}/annotation",
        json={"is_active": False, "demand_class": "dealer"},
    )
    assert res.status_code == 400, res.text

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == agent.id).one().is_active is True


def test_retiring_is_gated_on_the_edit_permission(read_only_client, db):
    agent = _seed(db, sales_agent="ZZT NOEDIT ACTIVE")

    res = read_only_client.patch(f"{BASE}/{agent.id}/annotation", json={"is_active": False})
    assert res.status_code == 403, res.text

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == agent.id).one().is_active is True


# --------------------------------------------------------------------------- #
# Bulk annotation
# --------------------------------------------------------------------------- #
# 38 codes arrived unclassified from the client's first upload and every one of them had
# to be opened, edited and saved on its own. The bulk route sets ONE field across a
# selection, with the same normalisation, the same vocabulary guard and the same
# permission as the per-row PATCH - it is that PATCH applied N times, never a second
# opinion about what a demand class is.
def test_bulk_sets_the_demand_class_on_every_selected_agent(client, db):
    a = _seed(db, sales_agent="ZZT BULK A")
    b = _seed(db, sales_agent="ZZT BULK B")
    untouched = _seed(db, sales_agent="ZZT BULK C")

    res = client.post(
        f"{BASE}/bulk-annotate",
        json={"sales_agent_ids": [a.id, b.id], "demand_class": PROJECT},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"updated": 2}

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == a.id).one().demand_class == PROJECT
    assert db.query(SalesAgent).filter(SalesAgent.id == b.id).one().demand_class == PROJECT
    assert db.query(SalesAgent).filter(SalesAgent.id == untouched.id).one().demand_class is None


def test_bulk_normalises_the_location_group(client, db):
    """Same `upper(btrim())` the single-row annotation applies - a group typed in lower
    case has to compare equal to the warehouse-code suffix it names."""
    a = _seed(db, sales_agent="ZZT BULK GROUP")

    res = client.post(
        f"{BASE}/bulk-annotate",
        json={"sales_agent_ids": [a.id], "location_group": " bb "},
    )
    assert res.status_code == 200, res.text

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == a.id).one().location_group == "BB"


def test_bulk_leaves_an_omitted_field_alone(client, db):
    """`model_fields_set`: setting the group must not wipe a class the captain set."""
    a = _seed(db, sales_agent="ZZT BULK KEEP", demand_class=PROJECT)

    res = client.post(
        f"{BASE}/bulk-annotate",
        json={"sales_agent_ids": [a.id], "location_group": "HP"},
    )
    assert res.status_code == 200, res.text

    db.expire_all()
    stored = db.query(SalesAgent).filter(SalesAgent.id == a.id).one()
    assert stored.demand_class == PROJECT
    assert stored.location_group == "HP"


def test_bulk_clears_a_field_sent_as_null(client, db):
    """An explicit null is a decision - the same one the single-row PATCH already honours."""
    a = _seed(db, sales_agent="ZZT BULK CLEAR", demand_class=DEFAULT_DEMAND_CLASS,
              location_group="BB")

    res = client.post(
        f"{BASE}/bulk-annotate",
        json={"sales_agent_ids": [a.id], "demand_class": None},
    )
    assert res.status_code == 200, res.text

    db.expire_all()
    stored = db.query(SalesAgent).filter(SalesAgent.id == a.id).one()
    assert stored.demand_class is None
    assert stored.location_group == "BB"


def test_bulk_refuses_a_word_outside_the_vocabulary(client, db):
    """And writes nothing: a rejected class must not classify half the selection."""
    a = _seed(db, sales_agent="ZZT BULK BAD A")
    b = _seed(db, sales_agent="ZZT BULK BAD B")

    res = client.post(
        f"{BASE}/bulk-annotate",
        json={"sales_agent_ids": [a.id, b.id], "demand_class": "dealer"},
    )
    assert res.status_code == 400, res.text
    assert "dealer" in res.text

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == a.id).one().demand_class is None
    assert db.query(SalesAgent).filter(SalesAgent.id == b.id).one().demand_class is None


def test_bulk_refuses_an_id_it_does_not_hold(client, db):
    """Named rather than silently skipped: "updated: 1 of 2" with no reason is how a
    selection quietly loses a row."""
    a = _seed(db, sales_agent="ZZT BULK MISSING")

    res = client.post(
        f"{BASE}/bulk-annotate",
        json={"sales_agent_ids": [a.id, str(uuid.uuid4())], "demand_class": PROJECT},
    )
    assert res.status_code == 404, res.text

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == a.id).one().demand_class is None


def test_bulk_requires_at_least_one_agent(client):
    res = client.post(f"{BASE}/bulk-annotate", json={"sales_agent_ids": []})
    assert res.status_code == 422, res.text


def test_bulk_is_gated_on_the_same_permission_as_the_patch(read_only_client, db):
    """A role that may read the master must not be able to reclassify 38 rows at once."""
    a = _seed(db, sales_agent="ZZT BULK GATED")

    res = read_only_client.post(
        f"{BASE}/bulk-annotate",
        json={"sales_agent_ids": [a.id], "demand_class": PROJECT},
    )
    assert res.status_code == 403, res.text


def test_the_class_lands_on_the_clicked_row_not_a_namesake(client, db):
    """The code is unique PER COMPANY, so two rows may legitimately spell it the same.

    A shared master row (`company_id IS NULL`) and a company-owned row can both be
    `ZZT NAMESAKE`. Classifying the company-owned one by re-resolving the CODE is a
    `.first()` with no ORDER BY, so it can flush the class onto the shared row instead:
    the wrong salesperson's entire book changes class and nothing on screen says so.
    """
    shared = _seed(db, sales_agent="ZZT NAMESAKE", company_id=None)
    owned = _seed(db, sales_agent="ZZT NAMESAKE", company_id=SORENTO_COMPANY_ID)

    res = client.patch(f"{BASE}/{owned.id}/annotation", json={"demand_class": PROJECT})
    assert res.status_code == 200, res.text
    assert res.json()["id"] == owned.id
    assert res.json()["demand_class"] == PROJECT

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == owned.id).one().demand_class == PROJECT
    assert db.query(SalesAgent).filter(SalesAgent.id == shared.id).one().demand_class is None


# --------------------------------------------------------------------------- #
# Linked portal contact (D46b)
#
# `sales_agents.contact_id` decides which debtors a portal salesperson may pick
# from. The column shipped with the price tag feature and was never writable, so
# every salesperson's debtor dropdown was empty with nothing said about why.
# These pin the write, its clear, and - the failure that looks like success -
# that the link AND the person's name both survive the response schema.
# --------------------------------------------------------------------------- #
def _seed_contact(db, *, name: str) -> RespondContact:
    c = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+60{uuid.uuid4().hex[:9]}",
        name=name,
    )
    db.add(c)
    db.commit()
    return c


def test_annotation_links_a_portal_contact_and_answers_with_the_name(client, db):
    agent = _seed(db, sales_agent="ZZT LINKME")
    contact = _seed_contact(db, name="ZZT CONTACT AA")

    res = client.patch(f"{BASE}/{agent.id}/annotation", json={"contact_id": contact.id})
    assert res.status_code == 200, res.text

    body = res.json()
    assert body["contact_id"] == contact.id
    # Never an id on the screen: the modal re-opens on the person, not a uuid.
    assert body["contact_name"] == "ZZT CONTACT AA"
    assert set(body) == RESPONSE_KEYS

    db.expire_all()
    stored = db.query(SalesAgent).filter(SalesAgent.id == agent.id).one()
    assert stored.contact_id == contact.id


def test_annotation_clears_the_linked_contact(client, db):
    contact = _seed_contact(db, name="ZZT Unlink Me")
    agent = _seed(db, sales_agent="ZZT UNLINKME", contact_id=contact.id)

    res = client.patch(f"{BASE}/{agent.id}/annotation", json={"contact_id": None})
    assert res.status_code == 200, res.text
    assert res.json()["contact_id"] is None
    assert res.json()["contact_name"] is None

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == agent.id).one().contact_id is None


def test_a_save_that_omits_the_contact_leaves_the_link_alone(client, db):
    """The modal sends only what it edited, so a save of the class alone must not
    unlink the person. That is the shape the `write_*` flags exist for."""
    contact = _seed_contact(db, name="ZZT Keep Linked")
    agent = _seed(db, sales_agent="ZZT KEEPLINK", contact_id=contact.id)

    res = client.patch(f"{BASE}/{agent.id}/annotation", json={"demand_class": PROJECT})
    assert res.status_code == 200, res.text
    assert res.json()["contact_id"] == contact.id

    db.expire_all()
    assert db.query(SalesAgent).filter(SalesAgent.id == agent.id).one().contact_id == contact.id


def test_an_unlinked_agent_answers_a_null_name(client, db):
    _seed(db, sales_agent="ZZT NOLINK")

    row = client.get(BASE, params={"query": "ZZT NOLINK"}).json()["data"][0]
    assert row["contact_id"] is None
    assert row["contact_name"] is None
    assert set(row) == RESPONSE_KEYS
