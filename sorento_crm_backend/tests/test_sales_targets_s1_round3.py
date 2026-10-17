"""S1 fix lane round 3: the owner's retest of 27 Sep 14:25 MYT (PR #1297).

- F1: figures are edited in Edit mode and saved in ONE request. `PATCH /sales/targets/{id}`
  takes `figures: [{period_id, target_value}]` (the target's own periods, or on a team target
  its agents' periods) and, on a team target, `new_agents: [{sales_agent_id, figures:
  [{period_start, target_value}]}]` for a member with no figure yet. Different periods carry
  different figures; a team period stays the sum of its agents' (T3).
- F2: commission tiers (plan 3.3, UAC S4-1): `commission_method` and `tiers` on create and
  PATCH, returned on the detail, copied by Duplicate; per period `commission_earned` and
  `bonus_earned` (null when the method is `none`).
- F3: an agent target of a team target edits its own figures on its own record, and they write
  through to the team target's sum.

Seeds and fixtures are the S1 file's (`test_sales_targets_s1.py`).
"""
from __future__ import annotations

from decimal import Decimal

from .test_sales_targets_s1 import (  # noqa: F401  (fixtures)
    BASE,
    TEAMS_BASE,
    _agent,
    api,
    world,
)


def _agent_target(client, agent_id, **extra):
    body = {
        "subject_kind": "agent", "sales_agent_id": agent_id, "name": "ZZT Q4", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01",
        "end_date": "2026-12-31", "split_every": 1, "split_unit": "month", "target_value": 100,
        **extra,
    }
    res = client.post(BASE, json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _team_target(client, db, figures=(("A", 600), ("B", 400)), members=("A", "B", "C")):
    agents = {code: _agent(db, code) for code in members}
    team = client.post(
        TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id for a in agents.values()]}
    ).json()
    res = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team Q4",
        "metric": "amount", "basis": "ordered", "product_scope": "all",
        "start_date": "2026-10-01", "end_date": "2026-12-31", "split_every": 1, "split_unit": "month",
        "agent_figures": [
            {"sales_agent_id": agents[code].id, "target_value": value} for code, value in figures
        ],
    })
    assert res.status_code == 201, res.text
    return res.json(), agents


def _core(tiers):
    # The one-engine fields (threshold, is_additional, period_start, account_rates; lane
    # SALES-TARGET-DEALER) are additive; these tests pin the original three.
    return [{k: t[k] for k in ("from_pct", "rate", "bonus_amount")} for t in tiers]


def _values(body):
    return [p["target_value"] for p in body["periods"]]


# --------------------------------------------------------------------------------------- #
# F1: one request for every changed figure
# --------------------------------------------------------------------------------------- #


def test_batched_figures_give_each_period_its_own_figure(api):
    client, db, _ = api
    a = _agent(db, "A")
    created = _agent_target(client, a.id)
    oct_, nov, dec = created["periods"]
    res = client.patch(f"{BASE}/{created['id']}", json={
        "figures": [
            {"period_id": oct_["id"], "target_value": 1000},
            {"period_id": dec["id"], "target_value": 3000.5},
        ],
    })
    assert res.status_code == 200, res.text
    assert _values(res.json()) == [1000, 100, 3000.5]
    assert _values(client.get(f"{BASE}/{created['id']}").json()) == [1000, 100, 3000.5]


def test_batched_figures_ride_with_a_rename_in_one_request(api):
    client, db, _ = api
    a = _agent(db, "A")
    created = _agent_target(client, a.id)
    res = client.patch(f"{BASE}/{created['id']}", json={
        "name": "ZZT Q4 renamed",
        "figures": [{"period_id": created["periods"][1]["id"], "target_value": 250}],
    })
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "ZZT Q4 renamed"
    assert _values(res.json()) == [100, 250, 100]


def test_team_batch_writes_agent_figures_per_period_and_resums(api):
    client, db, _ = api
    team_target, agents = _team_target(client, db)
    assert _values(team_target) == [1000, 1000, 1000]
    children = {c["sales_agent_id"]: c for c in team_target["children"]}
    a_periods = children[agents["A"].id]["periods"]
    b_periods = children[agents["B"].id]["periods"]
    res = client.patch(f"{BASE}/{team_target['id']}", json={
        "figures": [
            {"period_id": a_periods[0]["id"], "target_value": 700},
            {"period_id": a_periods[2]["id"], "target_value": 900},
            {"period_id": b_periods[1]["id"], "target_value": 0},
        ],
    })
    assert res.status_code == 200, res.text
    body = res.json()
    # Oct 700 + 400, Nov 600 + 0, Dec 900 + 400.
    assert _values(body) == [1100, 600, 1300]
    after = {c["sales_agent_id"]: c for c in body["children"]}
    assert [p["target_value"] for p in after[agents["A"].id]["periods"]] == [700, 600, 900]
    assert [p["target_value"] for p in after[agents["B"].id]["periods"]] == [400, 0, 400]


def test_team_batch_adds_a_member_with_no_figure_yet(api):
    client, db, _ = api
    team_target, agents = _team_target(client, db)
    c = agents["C"]
    assert [m["sales_agent_id"] for m in team_target["members_without_figure"]] == [c.id]
    res = client.patch(f"{BASE}/{team_target['id']}", json={
        "new_agents": [{
            "sales_agent_id": c.id,
            "figures": [
                {"period_start": "2026-10-01", "target_value": 50},
                {"period_start": "2026-12-01", "target_value": 70},
            ],
        }],
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["child_count"] == 3
    assert body["members_without_figure"] == []
    child = next(ch for ch in body["children"] if ch["sales_agent_id"] == c.id)
    # A period left out starts at 0.
    assert [p["target_value"] for p in child["periods"]] == [50, 0, 70]
    assert _values(body) == [1050, 1000, 1070]


def test_team_batch_refuses_the_team_s_own_period(api):
    client, db, _ = api
    team_target, _ = _team_target(client, db)
    res = client.patch(f"{BASE}/{team_target['id']}", json={
        "figures": [{"period_id": team_target["periods"][0]["id"], "target_value": 5}],
    })
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "TEAM_TARGET_IS_SUM"


def test_batch_refuses_another_target_s_period_and_writes_nothing(api):
    client, db, _ = api
    a = _agent(db, "A")
    b = _agent(db, "B")
    mine = _agent_target(client, a.id)
    theirs = _agent_target(client, b.id)
    res = client.patch(f"{BASE}/{mine['id']}", json={
        "name": "ZZT should not stick",
        "figures": [
            {"period_id": mine["periods"][0]["id"], "target_value": 999},
            {"period_id": theirs["periods"][0]["id"], "target_value": 5},
        ],
    })
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "UNKNOWN_PERIOD"
    after = client.get(f"{BASE}/{mine['id']}").json()
    assert after["name"] == "ZZT Q4"
    assert _values(after) == [100, 100, 100]
    assert _values(client.get(f"{BASE}/{theirs['id']}").json()) == [100, 100, 100]


def test_batch_refuses_a_negative_figure_and_a_non_uuid_period(api):
    client, db, _ = api
    a = _agent(db, "A")
    created = _agent_target(client, a.id)
    negative = client.patch(f"{BASE}/{created['id']}", json={
        "figures": [{"period_id": created["periods"][0]["id"], "target_value": -1}],
    })
    assert negative.status_code == 422, negative.text
    bad_id = client.patch(f"{BASE}/{created['id']}", json={
        "figures": [{"period_id": "not-a-uuid", "target_value": 1}],
    })
    assert bad_id.status_code == 422, bad_id.text


def test_new_agents_is_team_only_and_members_only(api):
    client, db, _ = api
    a = _agent(db, "A")
    outsider = _agent(db, "Z")
    solo = _agent_target(client, a.id)
    res = client.patch(f"{BASE}/{solo['id']}", json={
        "new_agents": [{"sales_agent_id": outsider.id, "figures": []}],
    })
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "NOT_A_TEAM_TARGET"

    team_target, _ = _team_target(client, db)
    res = client.patch(f"{BASE}/{team_target['id']}", json={
        "new_agents": [{"sales_agent_id": outsider.id, "figures": []}],
    })
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "AGENT_NOT_IN_TEAM"


# --------------------------------------------------------------------------------------- #
# F3: the agent target writes through to the team target
# --------------------------------------------------------------------------------------- #


def test_agent_record_figures_write_through_to_the_team_target(api):
    client, db, _ = api
    team_target, agents = _team_target(client, db)
    child_id = next(
        c["target_id"] for c in team_target["children"] if c["sales_agent_id"] == agents["A"].id
    )
    child = client.get(f"{BASE}/{child_id}").json()
    assert child["parent"]["id"] == team_target["id"]
    res = client.patch(f"{BASE}/{child_id}", json={
        "figures": [
            {"period_id": child["periods"][1]["id"], "target_value": 800},
            {"period_id": child["periods"][2]["id"], "target_value": 650},
        ],
    })
    assert res.status_code == 200, res.text
    assert _values(res.json()) == [600, 800, 650]
    parent = client.get(f"{BASE}/{team_target['id']}").json()
    # 400 from B each month: 1000, 1200, 1050.
    assert _values(parent) == [1000, 1200, 1050]


def test_agent_record_still_follows_the_parent_for_what_counts(api):
    client, db, _ = api
    team_target, agents = _team_target(client, db)
    child_id = team_target["children"][0]["target_id"]
    child = client.get(f"{BASE}/{child_id}").json()
    res = client.patch(f"{BASE}/{child_id}", json={
        "end_date": "2026-11-30",
        "figures": [{"period_id": child["periods"][0]["id"], "target_value": 1}],
    })
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "CHILD_FOLLOWS_PARENT"
    assert _values(client.get(f"{BASE}/{child_id}").json())[0] != 1


def test_agent_record_cannot_take_new_agents(api):
    client, db, _ = api
    team_target, agents = _team_target(client, db)
    child_id = team_target["children"][0]["target_id"]
    res = client.patch(f"{BASE}/{child_id}", json={
        "new_agents": [{"sales_agent_id": agents["C"].id, "figures": []}],
    })
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "NOT_A_TEAM_TARGET"


# --------------------------------------------------------------------------------------- #
# F2: commission tiers (plan 3.3, UAC S4-1)
# --------------------------------------------------------------------------------------- #


def test_new_target_has_no_commission(api):
    client, db, _ = api
    a = _agent(db, "A")
    created = _agent_target(client, a.id)
    assert created["commission_method"] == "none"
    assert created["tiers"] == []
    assert created["periods"][0]["commission_earned"] is None
    assert created["periods"][0]["bonus_earned"] is None


def test_tiers_are_added_edited_and_removed_through_patch(api):
    client, db, _ = api
    a = _agent(db, "A")
    created = _agent_target(client, a.id)
    added = client.patch(f"{BASE}/{created['id']}", json={
        "commission_method": "marginal",
        "tiers": [
            {"from_pct": 100, "rate": 4, "bonus_amount": 500},
            {"from_pct": 0, "rate": 2},
        ],
    })
    assert added.status_code == 200, added.text
    body = added.json()
    assert body["commission_method"] == "marginal"
    # Ordered by from %.
    assert _core(body["tiers"]) == [
        {"from_pct": 0, "rate": 2, "bonus_amount": None},
        {"from_pct": 100, "rate": 4, "bonus_amount": 500},
    ]
    assert body["periods"][0]["commission_earned"] == 0
    assert body["periods"][0]["bonus_earned"] == 0

    edited = client.patch(f"{BASE}/{created['id']}", json={
        "commission_method": "retroactive", "tiers": [{"from_pct": 0, "rate": 2.5}],
    })
    assert edited.status_code == 200, edited.text
    assert edited.json()["commission_method"] == "retroactive"
    assert _core(edited.json()["tiers"]) == [{"from_pct": 0, "rate": 2.5, "bonus_amount": None}]

    removed = client.patch(f"{BASE}/{created['id']}", json={"commission_method": "none", "tiers": []})
    assert removed.status_code == 200, removed.text
    assert removed.json()["tiers"] == []
    assert removed.json()["periods"][0]["commission_earned"] is None


def test_tier_rules_s4_1(api):
    client, db, _ = api
    a = _agent(db, "A")
    created = _agent_target(client, a.id)
    url = f"{BASE}/{created['id']}"
    none_with_tiers = client.patch(url, json={"commission_method": "none", "tiers": [{"from_pct": 0, "rate": 1}]})
    assert none_with_tiers.status_code == 422, none_with_tiers.text
    assert none_with_tiers.json().get("code") == "INVALID_TIERS"
    method_without_tiers = client.patch(url, json={"commission_method": "marginal", "tiers": []})
    assert method_without_tiers.status_code == 422, method_without_tiers.text
    assert method_without_tiers.json().get("code") == "INVALID_TIERS"
    repeated = client.patch(url, json={
        "commission_method": "marginal",
        "tiers": [{"from_pct": 50, "rate": 1}, {"from_pct": 50, "rate": 2}],
    })
    assert repeated.status_code == 422, repeated.text
    assert repeated.json().get("code") == "INVALID_TIERS"
    negative = client.patch(url, json={"commission_method": "marginal", "tiers": [{"from_pct": -1, "rate": 1}]})
    assert negative.status_code == 422, negative.text
    # Only the method, with tiers already saved, is fine; so is only the tiers.
    ok = client.patch(url, json={"commission_method": "marginal", "tiers": [{"from_pct": 0, "rate": 1}]})
    assert ok.status_code == 200, ok.text
    only_method = client.patch(url, json={"commission_method": "retroactive"})
    assert only_method.status_code == 200, only_method.text
    assert _core(only_method.json()["tiers"]) == [{"from_pct": 0, "rate": 1, "bonus_amount": None}]


def test_create_takes_tiers_and_duplicate_copies_them(api):
    client, db, _ = api
    a = _agent(db, "A")
    created = _agent_target(
        client, a.id, commission_method="marginal",
        tiers=[{"from_pct": 0, "rate": 2}, {"from_pct": 100, "rate": 4, "bonus_amount": 500}],
    )
    assert created["commission_method"] == "marginal"
    assert len(created["tiers"]) == 2
    dup = client.post(f"{BASE}/{created['id']}/duplicate")
    assert dup.status_code == 201, dup.text
    assert dup.json()["commission_method"] == "marginal"
    assert dup.json()["tiers"] == created["tiers"]


def test_an_agent_target_of_a_team_holds_its_own_tiers(api):
    """T4: each child holds its own tiers, changed on its own page."""
    client, db, _ = api
    team_target, _ = _team_target(client, db)
    child_id = team_target["children"][0]["target_id"]
    res = client.patch(f"{BASE}/{child_id}", json={
        "commission_method": "marginal", "tiers": [{"from_pct": 0, "rate": 3}],
    })
    assert res.status_code == 200, res.text
    assert _core(res.json()["tiers"]) == [{"from_pct": 0, "rate": 3, "bonus_amount": None}]
    assert client.get(f"{BASE}/{team_target['id']}").json()["tiers"] == []


# --------------------------------------------------------------------------------------- #
# commission_service golden numbers (UAC S4-2 to S4-6)
# --------------------------------------------------------------------------------------- #


def _earned(method, tiers, target, achieved):
    from app.services.sales.commission_service import Tier, commission_for

    return commission_for(
        method,
        [Tier(Decimal(str(f)), Decimal(str(r)), None if b is None else Decimal(str(b))) for f, r, b in tiers],
        Decimal(str(target)),
        Decimal(str(achieved)),
    )


def test_flat_s4_2():
    commission, bonus = _earned("marginal", [(0, 2.5, None)], 100000, "104250.50")
    assert commission == Decimal("2606.26")
    assert bonus == Decimal("0")


def test_marginal_s4_3():
    commission, _ = _earned("marginal", [(0, 2, None), (100, 4, None)], 100000, 120000)
    assert commission == Decimal("2800.00")


def test_retroactive_s4_4():
    assert _earned("retroactive", [(0, 2, None), (100, 4, None)], 100000, 120000)[0] == Decimal("4800.00")
    assert _earned("retroactive", [(0, 2, None), (100, 4, None)], 100000, "99999.99")[0] == Decimal("2000.00")


def test_bonus_s4_5():
    assert _earned("marginal", [(100, 2, 500)], 100000, 100000)[1] == Decimal("500.00")
    assert _earned("marginal", [(100, 2, 500)], 100000, "99990")[1] == Decimal("0")


def test_quantity_rm_per_unit_s4_6():
    from app.services.sales.commission_service import Tier, commission_for

    commission, _ = commission_for(
        "marginal", [Tier(Decimal("0"), Decimal("1.50"), None)], Decimal("1000"), Decimal("1200"),
        metric="quantity",
    )
    assert commission == Decimal("1800.00")


def test_none_is_null():
    assert _earned("none", [], 100, 100) == (None, None)

