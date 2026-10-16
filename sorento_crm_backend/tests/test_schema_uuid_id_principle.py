"""Design principle (documentation/reference/ADR-PRODUCT-STANDARDS.md §Data Model):

    Every domain table has a uuid `id` primary key.

Why it's a hard constraint, not a guideline: the polymorphic key columns
(`audit_logs.entity_id`, `conversation_sla_tracking.source_entity_id`,
`notifications.source_entity_id`, …) store a stringified id and can only be
typed uuid - with the type-safety that buys - if every id they might hold is
genuinely a uuid. One natural-key table (a `code`-keyed lookup) is enough to
force those columns back to text, and text silently accepts a `uuid = text`
mismatch that Postgres would otherwise reject at write time.

So this test walks the SQLAlchemy models and fails if any table lacks a uuid
`id`, UNLESS the table is in EXEMPTIONS below with a reason. A new table
therefore MUST either comply or be consciously added to the allowlist in a
reviewed diff - the default is compliance.

The allowlist is the honest record of what does not comply yet and why. It
should only ever shrink. Bringing a table into compliance = delete its line
(see 298_market_segments_uuid_id for the worked example).

Run: pytest tests/test_schema_uuid_id_principle.py -v
"""
from __future__ import annotations

import pytest
from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID

import app.models  # noqa: F401 - registers every model on Base.metadata
from app.database import Base

# Every entry is a table that does NOT have a uuid `id`, mapped to why it is
# tolerated. Categories:
#   JUNCTION - pure M2M association; a composite natural PK is correct, a
#                surrogate uuid would add nothing.
#   EXTERNAL - the schema is owned by another system (NextAuth on the frontend,
#                n8n, the Respond ingest); we do not get to redesign its PK.
#   LEGACY   - a uuid-shaped value stored in a text column, or a natural-key
#                PK, on an existing table. Converting is a real migration
#                (usually FK-heavy auth/RBAC) tracked separately, not a licence
#                to add more.
EXEMPTIONS: dict[str, str] = {
    # --- JUNCTION (composite PK, pure association) ---
    "respond_contact_access_types": "JUNCTION (contact_id + access_type_code)",
    "respond_contact_market_segments": "JUNCTION (contact_id + segment_code)",
    "team_member_market_segments": "JUNCTION (team_member_id + segment_code)",
    "team_member_brands": "JUNCTION (team_member_id + brand_code), mirrors team_member_market_segments",
    # Schema-qualified (ADR-0011): `projects.brands` is a junction, CORE `brands` is
    # not, and a bare "brands" key here would exempt the wrong table.
    "projects.brands": "JUNCTION (project_id + brand_id)",
    "projects.collaborators": "JUNCTION (project_id + user_id)",
    "projects.series_brands": "JUNCTION (series_id + brand_id), mirrors series_categories",
    "projects.series_categories": "JUNCTION (series_id + category_id)",
    "projects.series_products": "JUNCTION (series_id + product_id)",
    # One row per project, keyed BY the project. A surrogate uuid would invite a
    # second row per project, which is exactly what the PK is there to prevent.
    "projects.sales_profile": "JUNCTION - 1:1 extension, PK is project_id",
    # One snapshot per closed episode, keyed BY its frame (a uuid, no FK: retention trims
    # the frame and the fact stays). The writer's ON CONFLICT (frame_id) DO NOTHING is the
    # idempotency; a surrogate id would allow a second row per episode.
    "chatbot.chat_episode_facts": "JUNCTION - 1:1 extension, PK is frame_id (uuid)",
    # --- EXTERNAL (schema owned elsewhere) ---
    "verification_tokens": "EXTERNAL - NextAuth composite PK (identifier + token)",
    "chat_histories": "EXTERNAL - Respond/n8n ingest, BigInteger id by their design",
    "entity_conversation_messages": "EXTERNAL - mirrors an upstream message id",
    # --- LEGACY: text id holding a uuid-shaped value (auth / RBAC / session) ---
    "users": "LEGACY - text id shared with NextAuth; conversion is FK-heavy",
    "user_roles": "LEGACY - text id, auth core",
    "user_permissions": "LEGACY - text id, auth core",
    "user_role_assignments": "LEGACY - text id, auth core",
    "user_role_permissions": "LEGACY - text id, auth core",
    "user_quick_access": "LEGACY - text id, keyed off users",
    "user_list_column_configs": "LEGACY - text id, keyed off users",
    "respond_contacts": "LEGACY - text id (gen_random_uuid()::text), FK target of many",
    "impersonation_sessions": "LEGACY - text id, auth-adjacent",
    "contact_impersonation_sessions": "LEGACY - text id, auth-adjacent",
    # --- LEGACY: text id, system / config tables ---
    "system_settings": "LEGACY - text id singleton",
    "system_logs": "LEGACY - text id",
    "health_alert_state": "LEGACY - text id keyed by alert kind",
    "document_numbering_rules": "LEGACY - text id",
    "respond_contact_cs_routing": "LEGACY - text id",
    # --- LEGACY: natural-key config lookups (candidates for the market_segments treatment) ---
    "contact_access_types": "LEGACY - code PK, small controlled vocabulary",
    "email_event_configs": "LEGACY - event_key PK, small controlled vocabulary",
    "scm.currency_rate": "LEGACY - currency code PK, singleton row per currency (scm.CurrencyRate)",
}


def _has_uuid_id(table) -> bool:
    idc = table.columns.get("id")
    return idc is not None and isinstance(idc.type, PGUUID)


def _test_owned_table_names() -> set[str]:
    """Tables mapped by throwaway models defined INSIDE the test suite
    (e.g. ``demo_widgets``, ``fake_lookup_target``). They share the global
    ``Base.metadata``, so once another test module is imported in a full-suite
    run they show up here - but they are not application tables and the uuid-id
    principle does not apply to them. Attribute each mapped table to its defining
    module and drop the ones that live under ``tests``.

    Keyed the same way as ``_non_compliant_tables`` (``Table.key``), so the two are
    comparable: on the bare name a test model called `brands` would suppress
    `projects.brands` as well."""
    names: set[str] = set()
    for mapper in Base.registry.mappers:
        module = (getattr(mapper.class_, "__module__", "") or "")
        if module.startswith("tests.") or module == "tests" or module.startswith("test_"):
            local = getattr(mapper, "local_table", None)
            if local is not None:
                names.add(local.key)
    return names


def _non_compliant_tables() -> list[str]:
    """Reported by SCHEMA-QUALIFIED name (`t.key`), so the allowlist can name one table.

    `projects.brands` is a junction with a composite PK and CORE `brands` is a compliant
    uuid-id table; keyed on the bare name, exempting one would exempt the other, and the
    stale-entry test could never tell them apart (ADR-0011). `t.key` is the bare name for
    a default-schema table and `schema.name` for the rest, which is exactly the
    distinction wanted.
    """
    ignore = _test_owned_table_names()
    return [
        t.key
        for t in Base.metadata.tables.values()
        if t.key not in ignore and not _has_uuid_id(t)
    ]


def test_every_table_has_a_uuid_id_or_a_documented_exemption():
    """The core guard. A new table with no uuid id fails here until it is either
    fixed or added to EXEMPTIONS with a reason - the default is compliance."""
    offenders = [
        name for name in _non_compliant_tables() if name not in EXEMPTIONS
    ]
    assert not offenders, (
        "These tables have no uuid `id` and are not in the EXEMPTIONS allowlist. "
        "Give the table a `id = Column(UUID(as_uuid=False), primary_key=True, ...)` "
        "(preferred), or, only if it is a junction/external/legacy table, add it to "
        "EXEMPTIONS in this file with a reason:\n  " + "\n  ".join(sorted(offenders))
    )


def test_market_segments_is_compliant_and_not_exempt():
    """Regression pin for migration 298 + the model change: market_segments must
    carry a uuid `id` and must NOT be in the allowlist. If someone reverts the
    model, this fails loudly instead of the table silently rejoining EXEMPTIONS."""
    assert "market_segments" not in EXEMPTIONS
    ms = Base.metadata.tables["market_segments"]
    assert _has_uuid_id(ms), "market_segments.id must be a uuid primary key"
    assert ms.columns["id"].primary_key, "market_segments uuid id must be the PK"
    code = ms.columns["code"]
    assert code.unique, "market_segments.code must stay a unique business key (FK target)"


def test_allowlist_has_no_stale_entries():
    """The allowlist may only shrink. If a table was brought into compliance but
    its EXEMPTIONS line was left behind, flag it so the list stays honest."""
    non_compliant = set(_non_compliant_tables())
    stale = [name for name in EXEMPTIONS if name not in non_compliant]
    assert not stale, (
        "These tables now have a uuid id but are still listed in EXEMPTIONS - "
        "delete their lines:\n  " + "\n  ".join(sorted(stale))
    )


def test_exemptions_all_carry_a_reason():
    blank = [name for name, why in EXEMPTIONS.items() if not (why and why.strip())]
    assert not blank, f"EXEMPTIONS entries need a reason: {blank}"
