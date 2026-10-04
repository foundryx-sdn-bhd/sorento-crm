"""Permission for the Goods Receive Notes pull from AutoCount (PLAN-autocount-grn-pull-crm-02oct.md).

`procurement.grn.autocount_pull` is swept onto every role that already holds the sibling
`procurement.grn.import` permission (P13, the owner ruling `522_autocount_pull_perms` and
`do_pull_0001_perm` applied: the pull is another way to bring in the same book, so whoever
could already import GRNs may pull them instead), integration roles excluded. `admin` is
granted explicitly too.

Revision ID: grn_pull_0001_perm
Revises: acct_ledger_0002_vocab
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

revision = "grn_pull_0001_perm"
down_revision = "acct_ledger_0002_vocab"
branch_labels = None
depends_on = None

_PERMISSIONS = [
    (
        "procurement.grn.autocount_pull",
        "Pull Goods Receive Notes from AutoCount",
        "Permission to pull a goods receive notes snapshot from AutoCount and review/confirm it.",
    ),
]
#: (new slug, sibling slug already granted to the roles that should get it too).
_SWEEP = [
    ("procurement.grn.autocount_pull", "procurement.grn.import"),
]
_GRANT_ROLE_SLUGS = ("admin",)
_EXCLUDED_ROLE_PREFIX = "integration\\_%"


def _insert_permission(bind, slug: str, name: str, description: str) -> None:
    bind.execute(
        sa.text(
            """
            INSERT INTO user_permissions (id, slug, name, description, created_at)
            SELECT gen_random_uuid()::text, :slug, :name, :descr, now()
            WHERE NOT EXISTS (SELECT 1 FROM user_permissions WHERE slug = :slug)
            """
        ),
        {"slug": slug, "name": name, "descr": description},
    )


def _sweep(bind, target: str, source: str) -> None:
    bind.execute(
        sa.text(
            """
            INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at)
            SELECT gen_random_uuid()::text, rp.role_id, tgt.id, now()
            FROM user_role_permissions rp
            JOIN user_permissions src ON src.id = rp.permission_id AND src.slug = :source
            JOIN user_roles r ON r.id = rp.role_id
            CROSS JOIN user_permissions tgt
            WHERE tgt.slug = :target
              AND r.slug NOT LIKE :excluded
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        ),
        {"source": source, "target": target, "excluded": _EXCLUDED_ROLE_PREFIX},
    )


def _grant_to_roles(bind, slug: str, role_slugs: tuple[str, ...]) -> None:
    bind.execute(
        sa.text(
            """
            INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at)
            SELECT gen_random_uuid()::text, r.id, p.id, now()
            FROM user_roles r
            CROSS JOIN user_permissions p
            WHERE p.slug = :slug AND r.slug = ANY(:roles)
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        ),
        {"slug": slug, "roles": list(role_slugs)},
    )


def upgrade() -> None:
    bind = op.get_bind()
    for slug, name, description in _PERMISSIONS:
        _insert_permission(bind, slug, name, description)
    for target, source in _SWEEP:
        _sweep(bind, target, source)
    for slug, _name, _descr in _PERMISSIONS:
        _grant_to_roles(bind, slug, _GRANT_ROLE_SLUGS)


def downgrade() -> None:
    bind = op.get_bind()
    slugs = [slug for slug, _, _ in _PERMISSIONS]
    bind.execute(
        sa.text(
            "DELETE FROM user_role_permissions WHERE permission_id IN "
            "(SELECT id FROM user_permissions WHERE slug = ANY(:slugs))"
        ),
        {"slugs": slugs},
    )
    bind.execute(
        sa.text("DELETE FROM user_permissions WHERE slug = ANY(:slugs)"), {"slugs": slugs}
    )
