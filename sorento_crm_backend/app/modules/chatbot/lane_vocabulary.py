"""The chatbot module's lane and domain vocabulary, published for core (AC-809, AC-931).

The Settings > Chatbot screen has to render one checkbox per branch kind the build can
complete, and `PUT /settings/general` has to refuse one it cannot. Both live in
`app/api/v1/user_management/settings.py`, which is CORE, and core never imports
`app/services/chatbot/` (AC-002, and `tests/chatbot/test_import_boundary.py` fails naming
the importer). So the module publishes what core needs HERE, on its own surface, and the
package import stays on the module's side of the line - which is what `app/modules/chatbot/`
is for.

`contracts` is imported inside the functions and read as a module attribute, not bound at
import time, so it stays the single source: a slice that adds a lane changes `contracts`
and this answers differently on the next call, with nothing to keep in step.
"""
from __future__ import annotations


def completed_lane_kinds() -> frozenset[str]:
    """Every `branch_kind` this build can finish inside the CRM."""
    from app.services.chatbot import contracts

    return frozenset(contracts.CRM_COMPLETED_BRANCH_KINDS)


def lane_options(*, business_lane_enabled: bool) -> list[tuple[str, bool]]:
    """`(kind, built)` for every lane, sorted, with the one flag the screen needs.

    `built` is False for the three business arms while `chatbot_business_lane_enabled` is
    off: the arm ships, but nothing runs it, so checking it would do nothing at all. Every
    other kind has no second switch and is built unconditionally.
    """
    from app.services.chatbot.contracts import BUSINESS_BRANCH_KINDS

    return [
        (kind, business_lane_enabled if kind in BUSINESS_BRANCH_KINDS else True)
        for kind in sorted(completed_lane_kinds())
    ]


def default_unsupported_domains() -> list[str]:
    """The domains the bot refuses out of the box (AC-304, AC-931).

    Reached through this doorway for the same AC-002 reason `completed_lane_kinds`
    exists: the two core-side readers - `SystemSetting.chatbot_unsupported_domains`'
    Python default and `api/v1/user_management/settings.py`'s null-reset table - may not
    import `app/services/chatbot/`, and hand-copied literals in those two places are
    exactly what drifted when A6 unblocked `spo_allocation` (the settings copy was found
    only after the other two were fixed). Since the chatbot turn re-architecture (AC-1594,
    S6) the source is `turn.policy.default_policy()`'s frozen seed rather than the retired
    `contracts.DEFAULT_UNSUPPORTED_DOMAINS`, and any per-tenant override lives in
    `chatbot_domains.supported`, not here - this is only the blank-install default.

    A LIST, not a tuple, because both readers hand the value to SQLAlchemy as a JSONB
    column value and a tuple serialises differently. Read at call time, so a slice that
    changes the table needs no edit here.
    """
    from app.services.chatbot.turn.policy import default_policy

    return [row.name for row in default_policy().domains if not row.supported]


def escalation_teams() -> tuple[str, ...]:
    """The team codes the escalation lane knows how to route to (AC-1561, AC-002).

    `app/api/v1/system/chatbot_config.py` validates `escalation_team_code` against this
    list before a domain row can save it - reached through this doorway rather than
    `from app.services.chatbot.lanes.escalation import ESCALATION_TEAMS` directly, for
    the same AC-002 reason as the rest of this module: `tests/chatbot/
    test_import_boundary.py` only allows `app/api/v1/external/chat.py`,
    `app/api/v1/system/chatbot.py` and `app/tasks/chat_turns.py` to reach into the
    package, and a config-CRUD route is not one of those three doorways.
    """
    from app.services.chatbot.lanes.escalation import ESCALATION_TEAMS

    return ESCALATION_TEAMS


def suggested_agents() -> tuple[str, ...]:
    """The agent codes the parser may name (`contracts.SUGGESTED_AGENTS`), for the
    prompt's `{{agents}}` fallback on a database with no `access_agents` rows yet
    (PLAN-prompt-dynamic-30sep D3). Through this doorway for the AC-002 reason above."""
    from app.services.chatbot import contracts

    return contracts.SUGGESTED_AGENTS


def default_tier_order() -> list[str]:
    """The tier order default (chatbot turn re-architecture, AC-1502 captain ruling
    16 Sep 2026, AC-1594 captain ruling 16 Sep 2026).

    The ONE literal every reader without a live `system_settings.chatbot_tier_order` row
    falls back to - `SystemSetting.chatbot_tier_order`'s Python default, the S0
    migration's seed and `turn.policy.load_policy` / `default_policy`'s own fallback all
    read this function rather than keeping their own copy, which is what stops the three
    `TIER_ORDER` literals this replaced (`lanes/business/tier_gate.py`,
    `lanes/business/fetch.py`, `turn/policy_rows.py::DEFAULT_TIER_ORDER`) drifting from
    each other. No import of `app/services/chatbot/` needed here at all - the value is a
    plain default, not derived from anything else in the package.
    """
    return ["dealer", "office", "end_user"]


# The Memory card's defaults (chatbot memory lane A, contract section 2/5). One
# declaration, read by `SystemSetting.chatbot_memory`'s Python default and by the
# settings endpoint's own null-reset table, through this doorway for the same AC-002
# reason as the rest of this module: core may not import `app/services/chatbot/`.
CHATBOT_MEMORY_KEYS: tuple[str, ...] = (
    "enabled",
    "default_level",
)

#: `default_level` may never be "off" (contract section 2: the select has no clear) -
#: shared by the settings PUT's value-range check and anything else that needs the
#: vocabulary without hardcoding it a second time.
CHATBOT_MEMORY_DEFAULT_LEVELS: tuple[str, ...] = ("conversation", "episodes", "full")


def default_chatbot_memory() -> dict:
    """Memory off by default (owner ruling, Q1: "off first i need to test"), and the
    level a contact gets once it is switched on with no level of its own."""
    return {
        "enabled": False,
        "default_level": "full",
    }
