"""The `sales` module owns its tables and can be uninstalled with purge (plan 3.7, S6).

Same two promises `tests/test_projects_module_purge_invariants.py` checks for `projects`,
read off the models: every table declared in `app/models/sales.py` is in `PURGE_ORDER`
(nothing survives an uninstall-with-purge by being forgotten), the frontend's copy of the list
matches it in order, and a purge deletes the module's rows and never a core sales agent.
"""
from __future__ import annotations

import inspect
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.database import Base
from app.models import sales as sales_models
from app.models.base import company_scope
from app.models.sales_agent import SalesAgent
from tests._pg_fixture import blank_session

FE_PURGE_MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "sorento_crm_frontend" / "modules" / "sales" / "purge_tables.json"
)


def _declared_models() -> list[type]:
    found = []
    for obj in vars(sales_models).values():
        if (
            inspect.isclass(obj)
            and issubclass(obj, Base)
            and obj is not Base
            and obj.__module__ == sales_models.__name__
            and hasattr(obj, "__tablename__")
        ):
            found.append(obj)
    return found


def test_every_module_table_lives_in_the_sales_schema():
    """V3: the schema is the module key, so `\\dn` shows what the module owns."""
    models = _declared_models()
    assert models, "app/models/sales.py declares no tables"
    assert {m.__table__.schema for m in models} == {"sales"}


def test_purge_order_is_exactly_the_declared_models():
    from app.modules.sales.purge import PURGE_ORDER

    assert sorted(m.__name__ for m in PURGE_ORDER) == sorted(
        m.__name__ for m in _declared_models()
    )
    assert len(PURGE_ORDER) == len(set(PURGE_ORDER))


def test_children_are_purged_before_their_parents():
    from app.modules.sales.purge import PURGE_ORDER

    position = {m.__table__.fullname: i for i, m in enumerate(PURGE_ORDER)}
    for index, model in enumerate(PURGE_ORDER):
        for fk in model.__table__.foreign_key_constraints:
            target = list(fk.elements)[0].column.table.fullname
            if target in position and target != model.__table__.fullname:
                assert index < position[target], f"{model.__table__.fullname} after {target}"


def test_the_frontend_purge_manifest_matches_purge_order_exactly():
    from app.modules.sales.purge import PURGE_ORDER

    assert FE_PURGE_MANIFEST.exists(), f"missing {FE_PURGE_MANIFEST}"
    manifest = json.loads(FE_PURGE_MANIFEST.read_text())
    assert manifest["moduleKey"] == "sales"
    assert manifest["tables"] == [m.__table__.fullname for m in PURGE_ORDER]


def test_the_module_is_in_the_manifest_and_permission_map():
    from app.modules.runtime.module_manifest import MODULE_MANIFEST
    from app.modules.runtime.permission_module_map import module_for_permission
    from app.modules.sales.bootstrap import MODULE_KEY
    from app.rbac.permission_registry import PERMISSION_REGISTRY

    assert MODULE_KEY == "sales"
    assert MODULE_MANIFEST["sales"].dependencies == frozenset({"base", "product", "order"})
    assert module_for_permission("sales.teams.view") == "sales"
    slugs = {p["slug"] for p in PERMISSION_REGISTRY}
    assert {f"sales.teams.{a}" for a in ("view", "add", "edit", "delete")} <= slugs


def test_purge_empties_the_module_and_leaves_sales_agents():
    from app.models.sales import SalesTeam, SalesTeamMember
    from app.modules.sales.purge import PURGE_ORDER, purge

    with blank_session() as db:
        company_id = db.execute(text("select id from companies where code = 'SRT'")).scalar()
        with company_scope(db, frozenset({company_id})):
            agent = SalesAgent(id=str(uuid.uuid4()), sales_agent=f"ZZTP-{uuid.uuid4().hex[:6]}")
            team = SalesTeam(id=str(uuid.uuid4()), company_id=company_id, name="ZZT Purge")
            db.add_all([agent, team])
            db.flush()
            db.add(
                SalesTeamMember(
                    id=str(uuid.uuid4()),
                    company_id=company_id,
                    sales_team_id=team.id,
                    sales_agent_id=agent.id,
                )
            )
            db.flush()

            counts = purge(db)
            assert counts == {
                "sales.opportunity_lines": 0,
                "sales.opportunities": 0,
                "sales.team_members": 1,
                "sales.teams": 1,
                "sales.target_periods": 0,
                "sales.target_commission_tiers": 0,
                "sales.target_scope": 0,
                "sales.targets": 0,
                "sales.target_tier_rates": 0,
                "sales.target_accounts": 0,
                "sales.point_ratios": 0,
            }
            assert set(counts) == {m.__table__.fullname for m in PURGE_ORDER}
            assert db.query(SalesAgent).filter(SalesAgent.id == agent.id).count() == 1


@pytest.mark.parametrize("_", [None])
def test_purge_on_an_empty_module_reports_zero(_):
    from app.modules.sales.purge import PURGE_ORDER, purge

    with blank_session() as db:
        with company_scope(db, None):
            assert purge(db) == {m.__table__.fullname: 0 for m in PURGE_ORDER}
