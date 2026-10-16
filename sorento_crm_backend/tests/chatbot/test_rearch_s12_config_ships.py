"""Migration test for `chatbot_rearch_s12` (does not exist yet - the coder writes it).

Owner ruling: deploying this lane must need NO hand setup on prod. Three narrowing
values the owner set by hand on the clone's Chatbot Domains page must ship as seed +
migration, AND the parser prompt must be republished AND the `production` label moved
by the migration itself - this REVERSES `alembic/versions/chatbot_rearch_s4.py`'s own
"the production label is NOT moved... the owner promotes from the Prompts page" rule.

Measured on the clone (`chatbot_domains.narrowing`, audit log = the owner's own UI
edits), and confirmed against `app/services/chatbot/turn/policy_rows.py`'s own CURRENT
(pre-s12) values, byte for byte:

* `order.product`: `optional_filter` -> `list_all` (`policy_rows.py:181`)
* `promotion.product`: `optional_filter` -> `narrow_by_tier` (`policy_rows.py:102`)
* `product_attachment.product`: `must_narrow_one` -> `narrow_by_tier` (`policy_rows.py:86`)

Scratch schema only (`tests/_pg_fixture.py::blank_session`), never the shared DB.
`_seed_pre_s12` replays the real alembic chain up to (not including) s12 by loading
each migration module by file path and calling its own exposed function directly, in
the SAME order and via the SAME convention `scripts.bootstrap_env.seed_chatbot_policy`
already uses for exactly this reason (that function's own docstring: "create_all gives
a bootstrapped database the TABLES and COLUMNS ... but none of the rows these
migrations INSERT/UPDATE"). `chatbot_rearch_s6f` (an id-column schema change) and
`chatbot_rearch_s10` (a `roster_cap` column with a server default) are deliberately
NOT replayed, matching `seed_chatbot_policy`'s own list - both are schema-only changes
`Base.metadata.create_all` already reproduces, neither touches `chatbot_domains.
narrowing` or `ai_prompt_versions` at all.

One deliberate substitution from the brief's own wording, flagged rather than forced:
"seed_chatbot_policy on a fresh scratch schema ends with the production label on the
rendered version too" is tested here by replaying `_seed_pre_s12` plus `s12.upgrade()`
directly inside `blank_session()` (`TestS12Upgrade::test_seed_replay_ends_with_
production_on_rendered_version`), NOT by calling `scripts.bootstrap_env.
seed_chatbot_policy()` itself. That function hard-codes `from app.database import
engine` and commits through its own top-level `engine.begin()` blocks with no
surrounding rollback; the shared blank schema `blank_session()` reads (`tests.
_pg_fixture._BLANK`) is memoized ONCE per test-session and reused by every other
scratch-schema test in the suite, so calling the real function against it would
permanently leak `chatbot_domains`/`ai_prompt_versions` rows into every test that
runs after this one in the same session - not an isolated defect, a suite-wide one.
Once `chatbot_rearch_s12` exists, `scripts.bootstrap_env.seed_chatbot_policy` needs a
new `s12 = _load(...)` line and an `s12.apply_narrowing(conn)` / `s12.upgrade`-shaped
call added to its own body (mirrored, not exercised, here) for the two paths to stay
in lockstep - a fact worth a quick manual check when that slice lands, not a fact this
file can safely assert without the same leak.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.models.chatbot_policy import ChatbotDomain
from app.services.chatbot.turn import policy_rows
from app.services.chatbot_parser_prompt import (
    BLOCKS_BEGIN,
    BLOCKS_END,
    SEMANTIC_PARSER_PROMPT,
    render_prompt_blocks,
)
from tests._pg_fixture import blank_session

_ALEMBIC_DIR = Path(__file__).resolve().parent.parent.parent / "alembic"
_VERSIONS_DIR = _ALEMBIC_DIR / "versions"
_S12_PATH = _VERSIONS_DIR / "chatbot_rearch_s12.py"

PROMPT_NAME = "chatbot_semantic_parser"

_NARROWING_DOMAINS = ("order", "promotion", "product_attachment")


def _load(filename: str):
    """`chatbot_rearch_s0.py` does `from _chatbot_policy_seed import ...` by bare
    module name (`_chatbot_policy_seed.py` lives in `alembic/`, not `alembic/
    versions/`) - a real `alembic upgrade` resolves it because `ScriptDirectory`
    puts `alembic/` on `sys.path`; loading a migration module directly does not,
    so it is added here too, the SAME fix `scripts.bootstrap_env.seed_chatbot_
    policy` already applies for the identical reason."""
    if str(_ALEMBIC_DIR) not in sys.path:
        sys.path.insert(0, str(_ALEMBIC_DIR))
    spec = importlib.util.spec_from_file_location(f"_zzt_test_{filename}", _VERSIONS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_pre_s12(bind) -> None:
    """Replays the real chain up to (not including) s12 - the SAME 8-step sequence
    `scripts.bootstrap_env.seed_chatbot_policy` already uses (its own docstring order:
    s0 -> s4 -> s6d -> s6e -> s7 -> s8 -> s9 -> s11), so this test's "before" state is
    exactly what a real `alembic upgrade` produces the instant before s12 runs."""
    _load("chatbot_rearch_s0.py").seed_domains_and_kinds(bind)
    _load("chatbot_rearch_s4.py").publish_policy_blocks(bind)
    _load("chatbot_rearch_s6d.py").apply_narrowing(bind)
    _load("chatbot_rearch_s6e.py").apply_narrowing(bind)
    _load("chatbot_rearch_s7.py").apply_narrowing(bind)
    _load("chatbot_rearch_s8.py").apply_narrowing(bind)
    _load("chatbot_rearch_s9.py").apply_tools(bind)
    _load("chatbot_rearch_s11.py").apply_narrowing(bind)


def _run_migration(db: Session, module, direction: str) -> None:
    """Drives a migration's real `upgrade()`/`downgrade()` entrypoint through a live
    `Operations` context, exactly `test_migration_454_tag_template_versions.py::_run`
    does - needed because both call `op.get_bind()` internally rather than take a bind
    argument, unlike each slice's own shared `apply_narrowing(bind)` helper."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    context = MigrationContext.configure(connection=db.connection())
    with Operations.context(context):
        getattr(module, direction)()


def _narrowing(db: Session, name: str) -> dict[str, Any]:
    row = db.query(ChatbotDomain).filter(ChatbotDomain.name == name).one()
    return dict(row.narrowing or {})


def _all_narrowing(db: Session) -> dict[str, dict[str, Any]]:
    return {name: _narrowing(db, name) for name in _NARROWING_DOMAINS}


def _production_version(db: Session) -> AIPromptVersion | None:
    label = (
        db.query(AIPromptLabel)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .first()
    )
    if label is None:
        return None
    return db.query(AIPromptVersion).filter(AIPromptVersion.id == label.version_id).first()


def _version_count(db: Session) -> int:
    return db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME).count()


def _expected_template(db: Session) -> str:
    """`chatbot_rearch_s4.py::_body`'s own formula, imported directly (the constants
    and the renderer, never a copy of the string maths) so this test can never drift
    from what s4 - and s12, once it exists - actually publish."""
    blocks = render_prompt_blocks(db)
    return f"{SEMANTIC_PARSER_PROMPT.rstrip()}\n\n{BLOCKS_BEGIN}\n{blocks}{BLOCKS_END}\n"


def test_s12_migration_file_exists() -> None:
    assert _S12_PATH.exists(), (
        "chatbot_rearch_s12 does not exist yet - the coder writes the migration this "
        "whole file pins (owner ruling: deploying this lane needs no hand setup on "
        "prod)."
    )


class TestS12SeedParity:
    """`turn/policy_rows.py` is the in-memory fallback `policy.load_policy` reads when
    the table is absent or empty (its own docstring) - every prior narrowing migration
    keeps it in sync by hand, and s12 is no exception."""

    def test_policy_rows_carry_the_new_values(self) -> None:
        order = next(row for row in policy_rows.DEFAULT_DOMAIN_ROWS if row["name"] == "order")
        promotion = next(row for row in policy_rows.DEFAULT_DOMAIN_ROWS if row["name"] == "promotion")
        product_attachment = next(
            row for row in policy_rows.DEFAULT_DOMAIN_ROWS if row["name"] == "product_attachment"
        )

        assert order["narrowing"]["product"] == "list_all", order["narrowing"]
        assert order["narrowing"]["order"] == "narrow_to_code", order["narrowing"]
        assert order["narrowing"]["customer"] == "must_narrow_one", order["narrowing"]

        assert promotion["narrowing"]["product"] == "narrow_by_tier", promotion["narrowing"]
        assert promotion["narrowing"]["tier"] == "narrow_by_tier", promotion["narrowing"]

        assert product_attachment["narrowing"]["product"] == "narrow_by_tier", (
            product_attachment["narrowing"]
        )
        assert (
            product_attachment["narrowing"]["attachment_type"] == "narrow_by_type"
        ), product_attachment["narrowing"]


class TestS12Upgrade:
    def test_narrowing_changes_other_keys_untouched(self) -> None:
        assert _S12_PATH.exists(), "no chatbot_rearch_s12 to run yet"
        s12 = _load("chatbot_rearch_s12.py")
        with blank_session() as db:
            _seed_pre_s12(db.get_bind())
            db.commit()

            before = _all_narrowing(db)
            assert before["order"]["product"] == "optional_filter", before["order"]
            assert before["promotion"]["product"] == "optional_filter", before["promotion"]
            assert before["product_attachment"]["product"] == "must_narrow_one", (
                before["product_attachment"]
            )

            _run_migration(db, s12, "upgrade")
            db.commit()
            after = _all_narrowing(db)

        assert after["order"]["product"] == "list_all", after["order"]
        assert after["order"]["order"] == before["order"]["order"] == "narrow_to_code", after["order"]
        assert (
            after["order"]["customer"] == before["order"]["customer"] == "must_narrow_one"
        ), after["order"]

        assert after["promotion"]["product"] == "narrow_by_tier", after["promotion"]
        assert (
            after["promotion"]["tier"] == before["promotion"]["tier"] == "narrow_by_tier"
        ), after["promotion"]

        assert after["product_attachment"]["product"] == "narrow_by_tier", (
            after["product_attachment"]
        )
        assert (
            after["product_attachment"]["attachment_type"]
            == before["product_attachment"]["attachment_type"]
            == "narrow_by_type"
        ), after["product_attachment"]

    def test_prompt_republished_and_production_label_moved(self) -> None:
        assert _S12_PATH.exists(), "no chatbot_rearch_s12 to run yet"
        s12 = _load("chatbot_rearch_s12.py")
        with blank_session() as db:
            _seed_pre_s12(db.get_bind())
            db.commit()

            before_production = _production_version(db)
            assert before_production is not None, "s4's own seed always labels one version"
            before_production_id = before_production.id

            _run_migration(db, s12, "upgrade")
            db.commit()

            expected_template = _expected_template(db)
            production = _production_version(db)

        assert production is not None, "production must still resolve to a version"
        assert production.id != before_production_id, (
            "the label must move to a NEW version - s4's own rule (label stays put) "
            "is what s12 reverses"
        )
        assert production.name == PROMPT_NAME
        assert production.template == expected_template, (
            "the republished version's template must be s4's own formula "
            "(SEMANTIC_PARSER_PROMPT + the rendered policy blocks), computed AFTER "
            "s12's own narrowing changes land"
        )

    def test_idempotent_second_upgrade_adds_no_second_version(self) -> None:
        assert _S12_PATH.exists(), "no chatbot_rearch_s12 to run yet"
        s12 = _load("chatbot_rearch_s12.py")
        with blank_session() as db:
            _seed_pre_s12(db.get_bind())
            db.commit()

            _run_migration(db, s12, "upgrade")
            db.commit()
            once_narrowing = _all_narrowing(db)
            once_count = _version_count(db)
            once_production_id = _production_version(db).id

            _run_migration(db, s12, "upgrade")
            db.commit()
            twice_narrowing = _all_narrowing(db)
            twice_count = _version_count(db)
            twice_production_id = _production_version(db).id

        assert once_narrowing == twice_narrowing
        assert twice_count == once_count, (
            f"a second upgrade must publish no second version: {once_count} -> "
            f"{twice_count}"
        )
        assert twice_production_id == once_production_id

    def test_downgrade_restores_narrowing_and_the_prior_production_label(self) -> None:
        assert _S12_PATH.exists(), "no chatbot_rearch_s12 to run yet"
        s12 = _load("chatbot_rearch_s12.py")
        with blank_session() as db:
            _seed_pre_s12(db.get_bind())
            db.commit()

            before_narrowing = _all_narrowing(db)
            before_production_id = _production_version(db).id

            _run_migration(db, s12, "upgrade")
            db.commit()
            _run_migration(db, s12, "downgrade")
            db.commit()

            after_narrowing = _all_narrowing(db)
            after_production_id = _production_version(db).id

        assert after_narrowing == before_narrowing, (
            f"downgrade must restore the pre-s12 narrowing values: {after_narrowing!r} "
            f"!= {before_narrowing!r}"
        )
        assert after_production_id == before_production_id, (
            "downgrade must point production back at the version it pointed to "
            "before s12 ran, not merely at SOME older version"
        )

    def test_seed_replay_ends_with_production_on_rendered_version(self) -> None:
        """The seed-parity half of the owner ruling: a FRESH scratch-schema install,
        replayed through the real chain including s12, ends with `production`
        pointing at a version whose template matches what the domains hold at THAT
        moment - the same fact `scripts.bootstrap_env.seed_chatbot_policy` must hold
        once it gains its own `s12` step (see this file's own module docstring for
        why that function itself is not called directly here)."""
        assert _S12_PATH.exists(), "no chatbot_rearch_s12 to run yet"
        s12 = _load("chatbot_rearch_s12.py")
        with blank_session() as db:
            _seed_pre_s12(db.get_bind())
            db.commit()
            _run_migration(db, s12, "upgrade")
            db.commit()

            expected_template = _expected_template(db)
            production = _production_version(db)

        assert production is not None
        assert production.template == expected_template

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
import pytest  # noqa: E402

pytestmark = pytest.mark.parser_prompt_rows
