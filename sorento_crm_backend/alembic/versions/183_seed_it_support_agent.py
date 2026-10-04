"""Seed the IT Support AccessAgent + tier-1 IT Admin team for ticket round-robin.

Creates:
- AccessAgent(code='it_support')
- Team(name='IT Admin Tier 1')
- AgentTeam(agent=it_support, code='it_admin', tier=1, team=<tier-1 team>)
- TeamMember(team=<tier-1>, user=<person38@example.com>) - only when that user
  exists in the target environment (dev/staging convenience). Production
  installs without that seed user simply skip the member; admin can add
  members through the existing User Management → Teams UI.

Idempotent - re-running upgrade is a no-op.

Revision ID: 183_seed_it_support_agent
Revises: 182_ticket_source_channel
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa


revision = "183_seed_it_support_agent"
down_revision = "182_ticket_source_channel"
branch_labels = None
depends_on = None


SEED_DEV_USER_EMAIL = "person38@example.com"
AGENT_CODE = "it_support"
AGENT_NAME = "IT Support"
TEAM_NAME = "IT Admin Tier 1"
TEAM_SET_CODE = "it_admin"


def upgrade() -> None:
    bind = op.get_bind()

    agent_id = bind.execute(
        sa.text("SELECT id FROM access_agents WHERE code = :code"),
        {"code": AGENT_CODE},
    ).scalar()
    if not agent_id:
        agent_id = bind.execute(
            sa.text(
                "INSERT INTO access_agents (id, code, name, description, is_active) "
                "VALUES (gen_random_uuid(), :code, :name, :description, TRUE) "
                "RETURNING id"
            ),
            {
                "code": AGENT_CODE,
                "name": AGENT_NAME,
                "description": "IT support tickets routed to tier-1 IT admin via round-robin.",
            },
        ).scalar()

    team_id = bind.execute(
        sa.text(
            "SELECT t.id FROM teams t "
            "JOIN agent_teams at ON at.team_id = t.id "
            "WHERE at.agent_id = :agent_id AND at.code = :code AND at.tier = 1"
        ),
        {"agent_id": agent_id, "code": TEAM_SET_CODE},
    ).scalar()
    if not team_id:
        team_id = bind.execute(
            sa.text(
                "INSERT INTO teams (id, name, description) "
                "VALUES (gen_random_uuid(), :name, :description) "
                "RETURNING id"
            ),
            {
                "name": TEAM_NAME,
                "description": "Tier-1 IT admins receiving round-robin ticket assignments.",
            },
        ).scalar()
        bind.execute(
            sa.text(
                "INSERT INTO agent_teams (id, agent_id, code, team_id, tier) "
                "VALUES (gen_random_uuid(), :agent_id, :code, :team_id, 1)"
            ),
            {"agent_id": agent_id, "code": TEAM_SET_CODE, "team_id": team_id},
        )

    user_id = bind.execute(
        sa.text("SELECT id FROM users WHERE email = :email LIMIT 1"),
        {"email": SEED_DEV_USER_EMAIL},
    ).scalar()
    if user_id:
        already = bind.execute(
            sa.text(
                "SELECT id FROM team_members WHERE team_id = :team_id AND user_id = :user_id"
            ),
            {"team_id": team_id, "user_id": user_id},
        ).scalar()
        if not already:
            bind.execute(
                sa.text(
                    "INSERT INTO team_members (id, team_id, user_id, sort_order) "
                    "VALUES (gen_random_uuid(), :team_id, :user_id, 0)"
                ),
                {"team_id": team_id, "user_id": user_id},
            )


def downgrade() -> None:
    bind = op.get_bind()
    agent_id = bind.execute(
        sa.text("SELECT id FROM access_agents WHERE code = :code"),
        {"code": AGENT_CODE},
    ).scalar()
    if not agent_id:
        return
    team_ids = [
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT team_id FROM agent_teams WHERE agent_id = :agent_id AND code = :code"
            ),
            {"agent_id": agent_id, "code": TEAM_SET_CODE},
        ).fetchall()
    ]
    bind.execute(
        sa.text("DELETE FROM agent_teams WHERE agent_id = :agent_id AND code = :code"),
        {"agent_id": agent_id, "code": TEAM_SET_CODE},
    )
    if team_ids:
        bind.execute(
            sa.text("DELETE FROM teams WHERE id = ANY(:ids)"),
            {"ids": team_ids},
        )
    bind.execute(
        sa.text("DELETE FROM access_agents WHERE id = :id"),
        {"id": agent_id},
    )
