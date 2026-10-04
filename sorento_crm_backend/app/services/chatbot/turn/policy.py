# Policy: the frozen view over chatbot_domains + chatbot_entity_kinds a turn is handed
# (PLAN-chatbot-turn-rearch.md "The policy"). Built from ROWS, never from a constant -
# tests seed rows via `Policy.from_rows`, matching the real loader's own shape.
from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from app.services.chatbot.turn import policy_rows

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class DomainPolicy:
    name: str
    label: str
    intents: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    primary_tool: str | None = None
    escalation_team_code: str | None = None
    switch_words: tuple[str, ...] = ()
    takes_date_filter: bool = False
    reveal_key: str | None = None
    supported: bool = True
    narrowing: dict[str, str] = field(default_factory=dict)
    ladder: tuple[str, ...] = ()
    sort_order: int = 0


@dataclass(frozen=True)
class EntityKindPolicy:
    kind: str
    resolver_source: str = ""
    did_you_mean: bool = True
    default_narrowing: str = "optional_filter"
    family_grouping: str | None = None
    base_property_words: dict[str, str] = field(default_factory=dict)
    # Owner ruling 20 Sep 2026 (PLAN-chatbot-answer-half-reattach.md "Roster cap"):
    # the ceiling on any roster this kind is asked in. `engine.py` reads this off
    # every kind row and hands the mapping down to `resolve_gate.run` / `gate.run_gate`.
    # PICKER-NO-CAP (owner, 2 Oct 2026): 50, the column's default and S3 ceiling.
    roster_cap: int = 50


@dataclass(frozen=True)
class Policy:
    domains: tuple[DomainPolicy, ...]
    kinds: tuple[EntityKindPolicy, ...]
    tier_order: tuple[str, ...] = ()

    def domain(self, name: str) -> DomainPolicy | None:
        for row in self.domains:
            if row.name == name:
                return row
        return None

    def kind(self, kind: str) -> EntityKindPolicy | None:
        for row in self.kinds:
            if row.kind == kind:
                return row
        return None

    @classmethod
    def from_rows(
        cls,
        *,
        domains: list[dict[str, Any]],
        kinds: list[dict[str, Any]],
        tier_order: list[str] | None = None,
    ) -> "Policy":
        domain_rows = tuple(
            DomainPolicy(
                name=row["name"],
                label=row.get("label", row["name"]),
                intents=tuple(row.get("intents") or ()),
                tools=tuple(row.get("tools") or ()),
                primary_tool=row.get("primary_tool"),
                escalation_team_code=row.get("escalation_team_code"),
                switch_words=tuple(row.get("switch_words") or ()),
                takes_date_filter=bool(row.get("takes_date_filter", False)),
                reveal_key=row.get("reveal_key"),
                supported=bool(row.get("supported", True)),
                narrowing=dict(row.get("narrowing") or {}),
                ladder=tuple(row.get("ladder") or ()),
                sort_order=int(row.get("sort_order", 0)),
            )
            for row in domains
        )
        kind_rows = tuple(
            EntityKindPolicy(
                kind=row["kind"],
                resolver_source=row.get("resolver_source", ""),
                did_you_mean=bool(row.get("did_you_mean", True)),
                default_narrowing=row.get("default_narrowing", "optional_filter"),
                family_grouping=row.get("family_grouping"),
                base_property_words=dict(row.get("base_property_words") or {}),
                roster_cap=int(row.get("roster_cap") or 50),
            )
            for row in kinds
        )
        return cls(domains=domain_rows, kinds=kind_rows, tier_order=tuple(tier_order or ()))


def load_policy(db: "Session") -> Policy:
    """The real loader: `chatbot_domains` + `chatbot_entity_kinds` + `system_settings.
    chatbot_tier_order` (AC-1501/AC-1502/AC-1535) - loaded once per turn, frozen.

    Raw SQL, not an ORM read: `app/models/chatbot_policy.py` declares both tables only so
    `create_all` builds them for a blank-schema fixture; the columns are read positionally
    here because the loader wants whatever the row carries, not a mapped subset.

    A table that exists but holds no rows falls back to `policy_rows.py`'s seed - the
    blank-schema case, where `create_all` made the table and no migration ever seeded it.
    The fallback is the SAME data the migration writes (one copy, imported by both), so a
    test and a migrated database see one policy.
    """
    from sqlalchemy import text

    domain_rows = _rows(db, "SELECT * FROM chatbot_domains ORDER BY sort_order")
    kind_rows = _rows(db, "SELECT * FROM chatbot_entity_kinds ORDER BY sort_order")

    tier_order: list[str] = []
    try:
        tier_row = db.execute(
            text("SELECT chatbot_tier_order FROM system_settings LIMIT 1")
        ).first()
        tier_order = list(tier_row[0]) if tier_row and tier_row[0] else []
    except Exception:  # noqa: BLE001 - no settings row yet is the seed default, not a failure
        tier_order = []

    from app.modules.chatbot.lane_vocabulary import default_tier_order

    return Policy.from_rows(
        domains=domain_rows or _default_domain_rows(),
        kinds=kind_rows or [dict(row) for row in policy_rows.DEFAULT_KIND_ROWS],
        tier_order=tier_order or default_tier_order(),
    )


def _default_domain_rows() -> list[dict[str, Any]]:
    """`policy_rows.DEFAULT_DOMAIN_ROWS`, plus `takes_date_filter` (AC-1501) - the SAME
    derivation the S0 migration applies at insert time (`any(tool in DATE_PARAM_TOOLS for
    tool in row["tools"])`), so a blank-schema fallback answers this question the same way
    a migrated database's own column does, rather than defaulting every domain to False
    (`DomainPolicy.takes_date_filter`'s own dataclass default)."""
    return [
        {
            **row,
            "takes_date_filter": any(t in policy_rows.DATE_PARAM_TOOLS for t in row["tools"]),
        }
        for row in policy_rows.DEFAULT_DOMAIN_ROWS
    ]


@functools.lru_cache(maxsize=1)
def default_policy() -> "Policy":
    """The frozen-seed `Policy` every pure reader without a live database session falls
    back to (AC-1594): the SAME `policy_rows.py` data `load_policy`'s own blank-schema
    fallback uses, so the two can never disagree. Several old `lanes/business/` ported
    modules read a domain fact (a switch word, a tool, a date-filter flag) from deep
    inside a pure helper with no `db` session and no `ctx` reaching it - this is what they
    read instead of the retired `contracts.DOMAIN_SPEC`. Cached: the seed never changes
    within a process, and every caller wants the same frozen object.
    """
    from app.modules.chatbot.lane_vocabulary import default_tier_order

    return Policy.from_rows(
        domains=_default_domain_rows(),
        kinds=[dict(row) for row in policy_rows.DEFAULT_KIND_ROWS],
        tier_order=default_tier_order(),
    )


def domain_switch_words(policy: "Policy") -> dict[str, str]:
    """word -> domain, inverted from each row's `switch_words` (was `contracts.
    DOMAIN_SWITCH_WORDS`). A word claimed by two domains keeps the LAST domain in
    `policy.domains` order - same behaviour the old dict comprehension had."""
    return {word: row.name for row in policy.domains for word in row.switch_words}


def _rows(db: "Session", sql: str) -> list[dict[str, Any]]:
    """Read a policy table, or return an empty list when it is not there at all.

    A missing table aborts the enclosing Postgres transaction, and every engine test
    shares one - so the existence check runs first (`to_regclass`) rather than being
    caught after the fact.
    """
    from sqlalchemy import text

    table = sql.split(" FROM ")[1].split(" ")[0]
    exists = db.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar()
    if not exists:
        return []
    return [dict(row) for row in db.execute(text(sql)).mappings().all()]
