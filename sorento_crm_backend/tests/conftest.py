"""Global pytest fixtures for the backend test suite.

Every test runs against Postgres -- either the live DB (rolled back) or an empty
scratch schema built from the real DDL, both via tests/_pg_fixture.py. There is
no sqlite anywhere in the suite. This file now holds only real cross-test
hygiene: sweeping/dropping the scratch schema and resetting leak-prone
process globals between tests.
"""
import os

import pytest


# Holds the session-long advisory lock. Kept on a module global because the lock lives on the
# CONNECTION: closing it would release the lock and let a concurrent run start sweeping.
_SWEEP_LOCK: dict = {}

# Arbitrary but fixed. Every pytest session in every worktree against this database competes for
# this one key, which is exactly the point.
_SWEEP_LOCK_KEY = 727327001


def _owner_pid(schema_name: str):
    """The PID embedded in a scratch schema name, if it carries one.

    ``zzs_blank_<pid>_<rand>`` (see tests/_pg_fixture.py). A hand made schema
    under the same prefix has no PID and reads as ownerless.
    """
    parts = schema_name.split("_")
    for part in parts:
        if part.isdigit():
            return int(part)
    return None


def _process_is_alive(pid: int) -> bool:
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Somebody else's process, but a RUNNING one. Not ours to drop.
        return True
    return True


def _sweep_orphan_scratch_schemas():
    """Drop scratch schemas left behind by a run that is no longer alive.

    The end-of-session drop below only fires if the process exits cleanly. A
    killed run -- a timeout, an interrupted agent, a crash -- leaves its ~199
    table schema behind, and those pile up (105 had accumulated across this
    migration's many interrupted runs). Sweeping at session START keeps the
    shared database from filling with them regardless of how the previous run
    died.

    ORPHAN IS THE OPERATIVE WORD, and it did not used to be. This dropped every
    ``zzt_%`` schema it could see, which is correct exactly when no other pytest
    is running and destructive whenever one is: the newcomer deleted the tables
    out from under a live run in another checkout of this repository, and the
    victim reported dozens of "relation ... does not exist" errors at fixture
    setup that belonged to no change anybody had made. Postgres words a dropped
    SCHEMA identically to a missing TABLE, so it does not even read as deletion.
    That has cost hours of bisecting an innocent diff, and it is the worst kind
    of noise: a false failure is indistinguishable from a real one until
    somebody re-runs the suite and happens to see it pass.

    Two guards now, because they cover different attackers:

    * this sweep only ever considers schemas under ``SCRATCH_SCHEMA_PREFIX``,
      which is this checkout's own namespace. A ``zzt_`` schema belongs to a
      checkout still running the older code and is none of our business -- we
      cannot prove it is dead, so we do not touch it;
    * within our own namespace, a schema whose owning PID is still running is
      left alone, which covers two runs of THIS checkout.

    Two pytest runs on one database remain a bad idea -- they share the real
    tables -- but they no longer sabotage each other's scratch schema, and the
    failures a developer sees are their own.

    A third guard sits in front of both: a Postgres advisory lock held for the
    WHOLE session rather than just across the sweep. The first session in takes
    it and cleans up; any session that starts while that one is still running
    fails to take it and touches nothing. Failing to acquire is therefore not an
    error, it is the signal that somebody else is working. It buys what the PID
    check cannot: a schema is not swept between the moment its owner picked the
    name and the moment it created it.
    """
    try:
        from sqlalchemy import text
        from app.database import engine
        from tests._pg_fixture import SCRATCH_SCHEMA_PREFIX

        admin = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        got = admin.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": _SWEEP_LOCK_KEY}
        ).scalar()
        if not got:
            # Another run owns the database. Leave its schemas alone.
            admin.close()
            return

        # Held until _release_sweep_lock, so nobody else sweeps underneath us.
        _SWEEP_LOCK["connection"] = admin

        names = [
            r[0]
            for r in admin.execute(
                text("SELECT nspname FROM pg_namespace WHERE nspname LIKE :pattern"),
                {"pattern": f"{SCRATCH_SCHEMA_PREFIX}\\_%"},
            )
        ]
        for name in names:
            pid = _owner_pid(name)
            # Never our own, whatever the ordering: this runs at session start,
            # but a module-scoped fixture that builds the schema first would
            # otherwise have it swept from under itself.
            if pid is not None and (pid == os.getpid() or _process_is_alive(pid)):
                continue
            admin.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
    except Exception:
        pass


def _release_sweep_lock():
    connection = _SWEEP_LOCK.pop("connection", None)
    if connection is None:
        return
    try:
        from sqlalchemy import text

        connection.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": _SWEEP_LOCK_KEY}
        )
    except Exception:
        pass
    finally:
        try:
            connection.close()
        except Exception:
            pass


@pytest.fixture(scope="session", autouse=True)
def _blank_schema_lifecycle():
    """Sweep stale scratch schemas before the run, drop this run's after it.

    Two halves because the two failure modes differ: the sweep handles schemas
    a *previous* killed run left behind; the drop handles *this* run's schema on
    a clean exit.
    """
    _sweep_orphan_scratch_schemas()
    yield
    try:
        from tests._pg_fixture import drop_blank_schema

        drop_blank_schema()
    except Exception:
        pass
    # Last, so the lock outlives this run's own schema and a queued run finds the DB tidy.
    _release_sweep_lock()


# The per-test metadata-restore backstop that used to live here is gone. It
# re-applied a snapshot of every model column's declared type before each test,
# to undo an in-place rewrite of ``Model.__table__`` by a sqlite shim fixture.
# No fixture mutates model metadata any more, and CLAUDE.md's "Tests run on
# Postgres ONLY, NEVER sqlite" rule forbids the mutation that made it necessary,
# so all it bought was a walk over ~2,500 columns on every single test.


# Module-level function symbols that tests stub. Same family as the column-type
# backstop above: a bare ``svc_module.resolve_references = lambda ...`` in a fixture is
# never undone, so it stayed installed for every test that ran later in the session.
# Four fixtures did exactly that, and the visible damage was the reverse of a stub's
# intent -- a WORKING resolution path failed with "lambda() got an unexpected keyword
# argument", in a file that stubs nothing, only in a full-suite run.
_STUBBABLE_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("app.services.ai_assistant_service", "resolve_references"),
)


@pytest.fixture(autouse=True)
def _restore_stubbed_module_symbols():
    """Restore module-level symbols a test rebound without undoing it.

    Every current site uses ``monkeypatch``, which restores itself; this is the backstop
    for the next one, because the failure it causes lands in somebody else's file and
    reads as a production bug rather than as a leaked stub.

    Reads ``sys.modules`` and never IMPORTS. Importing the module here instead cost 149
    errors across the suite: the assistant service pulls a large chain in with it, and doing
    that from a fixture loaded it far earlier than the suite otherwise would, in tests that
    never touch it. A module that was never imported cannot be holding a leaked stub, so
    there is nothing to restore anyway.
    """
    import sys

    originals = [
        (module, attr, getattr(module, attr))
        for module_path, attr in _STUBBABLE_SYMBOLS
        if (module := sys.modules.get(module_path)) is not None
        and hasattr(module, attr)
    ]
    yield
    for module, attr, original in originals:
        if getattr(module, attr, None) is not original:
            setattr(module, attr, original)

# ---------------------------------------------------------------------------
# Company-scope test default (multi-company isolation).
#
# Production defaults an absent ``db.info['company_scope']`` to UNSET
# (fail-closed -> 0 rows) so a request path that never runs the resolver cannot
# leak. Legacy tests, however, seed and query owned tables WITHOUT setting any
# scope. Under the fail-closed default they return 0 rows; and because
# migration 305 makes owned ``company_id`` NOT NULL on the live Postgres test
# DB, a null-company insert is rejected outright. So for the test process we
# default an absent scope to the **Sorento** company (all backfilled live data
# is Sorento): owned inserts auto-stamp Sorento (satisfying NOT NULL) and reads
# filter to Sorento (where the data is). The ``after_begin`` listener only fills
# the key when unset, so ``company_scope(db, ...)`` / ``set_company_scope`` still
# win - the dedicated ``tests/test_company_scope.py`` overrides per-test to
# assert the real four-state / fail-closed semantics.
# ---------------------------------------------------------------------------
import app.services.company_scope as _company_scope  # noqa: E402  (ensures module loaded)
from sqlalchemy.orm import Session as _SAScopeSession  # noqa: E402
from sqlalchemy import event as _sa_scope_event  # noqa: E402

# Install the enforcement listeners (do_orm_execute filter + before_insert
# auto-stamp) for the whole test process, exactly as production does at app/worker
# import time. Idempotent (``_INSTALLED`` guard). Without this a test that uses a
# bare ``SessionLocal`` and never imports ``app.main`` gets no auto-stamp, so its
# owned inserts leave ``company_id`` NULL and violate the NOT NULL / FK - the
# ``after_begin`` default below only sets the scope, it does not stamp.
_company_scope.register_company_scope_listeners()

_SORENTO_COMPANY_ID = "00000000-0000-0000-0000-000000000001"
_SORENTO_TEST_SCOPE = frozenset({_SORENTO_COMPANY_ID})

# Seed the incumbent Sorento company into EVERY schema the suite builds, the moment
# its ``companies`` table is created. Test schemas come from ``create_all`` (the
# shared blank schema AND per-module scratch schemas), never from migration 302 which
# seeds this row in production. Since the scope layer auto-stamps owned inserts with
# the incumbent company, that row must exist or every ``*_company_id_fkey`` rejects
# the insert. An ``after_create`` DDL hook covers all of them uniformly.
from app.models.company import Company as _ScopeCompany  # noqa: E402


@_sa_scope_event.listens_for(_ScopeCompany.__table__, "after_create")
def _seed_default_company_after_create(target, connection, **kw):  # noqa: ANN001
    connection.execute(
        target.insert().values(
            id=_SORENTO_COMPANY_ID, name="Sorento", code="SRT", is_active=True
        )
    )


# Same gap, same fix, for the packing list's running number. `document_numbering_rules`
# holds ROWS, not schema, so `create_all` produces the empty table and migration 440's seed
# never runs on a test schema. Without the `inbound_shipment_draft` rule every container
# created by a convert - or by `/new` without a number - is refused for having no series to
# draw from, which reads as an unrelated regression in whatever suite hits it first.
import uuid as _uuid_for_seed  # noqa: E402

from app.models.numbering import DocumentNumberingRule as _NumberingRule  # noqa: E402
from app.services import numbering_defaults as _numbering_defaults  # noqa: E402


@_sa_scope_event.listens_for(_NumberingRule.__table__, "after_create")
def _seed_packing_list_numbering_after_create(target, connection, **kw):  # noqa: ANN001
    # The rule's shape comes from the one definition the migration and the service share,
    # so a change to the series cannot leave the test schema numbering the old way.
    connection.execute(
        target.insert().values(
            id=str(_uuid_for_seed.uuid4()),
            company_id=_SORENTO_COMPANY_ID,
            doc_type=_numbering_defaults.INBOUND_SHIPMENT_DRAFT_DOC_TYPE,
            enabled=True,
            prefix_template=_numbering_defaults.INBOUND_SHIPMENT_DRAFT_PREFIX_TEMPLATE,
            number_digits=_numbering_defaults.INBOUND_SHIPMENT_DRAFT_NUMBER_DIGITS,
            next_value=1,
            start_value=1,
            reset_policy=_numbering_defaults.INBOUND_SHIPMENT_DRAFT_RESET_POLICY,
        )
    )


@_sa_scope_event.listens_for(_SAScopeSession, "after_begin")
def _default_company_scope_for_tests(session, transaction, connection):  # noqa: ANN001
    if getattr(_company_scope, "_ENFORCE", True):
        session.info.setdefault("company_scope", _SORENTO_TEST_SCOPE)


_IDEMP_REDIS = []  # process-wide cache: [client] or [None]


def _idempotency_redis():
    if not _IDEMP_REDIS:
        try:
            from app.config import settings as _settings
            import redis as _redis

            _IDEMP_REDIS.append(_redis.from_url(_settings.redis_url, decode_responses=True))
        except Exception:
            _IDEMP_REDIS.append(None)
    return _IDEMP_REDIS[0]


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset leak-prone process-global / contextvar state after every test so
    ordering can't pollute. The dominant offender: an impersonation test sets a
    non-UUID audit actor (`stamp_actor(AuditActor(..., user_id="REAL_ADMIN"))`) that, uncleared,
    leaks into later live-Postgres tests whose audit writes cast it to `uuid`
    and fail. Also clears the lookup-binding TTL cache."""
    yield
    try:
        from app.audit_context import clear_actor

        clear_actor()
    except Exception:
        pass
    try:
        from app.services.lookup_validator import _cache_clear

        _cache_clear()
    except Exception:
        pass
    try:
        # The 24h-window lookup keeps a short per-identifier TTL cache. Tests
        # reuse identifiers ("id:123", "437264483") across files with different
        # mocked message lists, so a surviving entry would answer the next test
        # with the previous one's window.
        from app.services.respond_messaging_service import reset_window_cache

        reset_window_cache()
    except Exception:
        pass
    try:
        # The RBAC permission cache (`_rbac_cache`, 30s TTL) is a process global.
        # Tests reuse user ids (e.g. a superadmin seeded under a fixed id) across
        # files; a stale non-superadmin `role_slugs`/`perm` entry cached by an
        # earlier test makes a later route's superadmin bypass miss and query the
        # `user_permissions` table - which many sqlite fixtures deliberately omit,
        # yielding "no such table: user_permissions". Clearing per test removes
        # the cross-test leak. Prod is unaffected (this only runs under pytest).
        from app.services.user_service import invalidate_rbac_cache

        invalidate_rbac_cache()
    except Exception:
        pass
    try:
        # The idempotency middleware caches 2xx responses in Redis for
        # ``idempotency_result_ttl`` seconds, keyed by a hash of (session, method,
        # path, body). Two route tests that POST the SAME body to the SAME endpoint
        # within that window collide: the second gets the first's cached response
        # (e.g. an escalate test at max-tier receives a prior test's "escalated"
        # payload). Flush the ``idemp:*`` keys after each test so ordering can't leak
        # a cached reply. Best-effort: no Redis in some envs → skip. The client is
        # cached process-wide to avoid a reconnect per test.
        _r = _idempotency_redis()
        if _r is not None:
            _keys = list(_r.scan_iter(match="idemp:*", count=500))
            if _keys:
                _r.delete(*_keys)
    except Exception:
        pass
    try:
        # `app.services.storage_router` memoises signed URLs (including signing
        # FAILURES, cached as None) in a process-global `_signed_cache` keyed by
        # (provider, key, expires_in). Without a reset, an earlier test's cached
        # failure for the same key silently short-circuits a later test's
        # working backend and it gets the stale `None` back instead of a real
        # signature. Clear per test so ordering can't leak a cached result.
        from app.services.storage_router import clear_signed_url_cache

        clear_signed_url_cache()
    except Exception:
        pass


# The sqlite type-affinity shims (`@compiles(JSONB|ARRAY, "sqlite")`) and the
# StaticPool audit-cache pre-warm that used to live here are gone: no test
# builds a sqlite engine any more, so nothing would ever trigger them. Every
# test runs on Postgres via tests/_pg_fixture.py.

# ---------------------------------------------------------------------------
# No live external calls, suite-wide (NO-LIVE-LLM-TESTS). The backend `.env` a
# developer runs pytest against carries real OPENAI / RESPOND keys, and a test
# that forgets its own stub would spend them. Two layers, both autouse:
#
# * `tests/_live_call_guard.py` wraps the httpx / urllib / requests transports
#   so any request to an LLM or messaging provider host raises
#   `LiveExternalCallBlocked`, and a blocked attempt fails the test at teardown
#   even when app code swallowed the exception;
# * the provider keys are blanked in `settings` and `os.environ` per test, so a
#   call that somehow bypasses the transport fails on "no key".
#
# A test that genuinely means to reach a provider is marked
# `@pytest.mark.live_external`; it is skipped unless CHATBOT_LIVE_LLM=1, and
# with it set both layers are lifted for that test only.
#
# The older `no_live_llm` fixture below stays: it is the opt-in, seam-level
# guard on `get_provider()` (lane C modules request it), and it catches a
# missing stub before any transport is built.
# ---------------------------------------------------------------------------

_PROVIDER_KEY_SETTINGS = ("openai_api_key", "anthropic_api_key", "gemini_api_key", "respond_api_key")


def _live_opt_in() -> bool:
    from tests._live_call_guard import LIVE_OPT_IN_ENV

    return os.environ.get(LIVE_OPT_IN_ENV) == "1"


def pytest_collection_modifyitems(config, items):
    if _live_opt_in():
        return
    skip = pytest.mark.skip(reason="live external call test; set CHATBOT_LIVE_LLM=1 to run it")
    for item in items:
        if item.get_closest_marker("live_external"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_live_external_calls(request, monkeypatch):
    from tests import _live_call_guard as guard

    if request.node.get_closest_marker("live_external") and _live_opt_in():
        guard.ENABLED = False
        try:
            yield
        finally:
            guard.ENABLED = True
        return

    from app.config import settings

    for name in _PROVIDER_KEY_SETTINGS:
        monkeypatch.setattr(settings, name, None, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)

    guard.ENABLED = True
    guard.BLOCKED_CALLS.clear()
    yield
    guard.fail_if_blocked()


def pytest_configure(config):
    from tests._live_call_guard import install

    install()
    config.addinivalue_line(
        "markers",
        "live_external: this test means to reach a real LLM / messaging provider; "
        "skipped unless CHATBOT_LIVE_LLM=1, which lifts the live call guard for it.",
    )
    config.addinivalue_line(
        "markers",
        "allow_live_llm: opt this test out of the no_live_llm get_provider() seam guard "
        "only; the suite-wide transport guard still blocks the call (see live_external).",
    )
    config.addinivalue_line(
        "markers",
        "serial_ddl: this test runs DDL (or an unbounded write) against a SHARED "
        "relation of the real schema, not a scratch one, so it must not run inside "
        "the xdist pool. CI deselects it with `-m \"not serial_ddl\"` and runs it "
        "afterwards with `-p no:xdist`. See .github/workflows/deploy.yml.",
    )


@pytest.fixture
def no_live_llm(monkeypatch, request):
    """Raise on any call to `get_provider()` unless the test stubs it back out.

    `from app.services.llm_provider import get_provider` is a SEPARATE name
    binding in the importing module, not the original attribute - patching
    only `app.services.llm_provider.get_provider` misses every module that
    already imported it by name (which is every AI-touching service module in
    this codebase). Patched here instead: every module already in `sys.modules`
    whose OWN `get_provider` attribute is still the real function, at the point
    each test actually runs (by which time every test file's own imports have
    already happened, so this reaches all of them).

    A test that stubs `get_provider` itself (every existing AI-touching test
    already does, e.g. `test_translation_service.py`'s `_stub_provider`) simply
    overwrites this raising stub with its own fake - same `monkeypatch`
    instance, so pytest still restores the true original at teardown either
    way. `@pytest.mark.allow_live_llm` lifts only this seam guard; the
    suite-wide transport guard (`_no_live_external_calls`) still blocks the
    request, so a test that really means to reach a provider is marked
    `@pytest.mark.live_external` instead.
    """
    if request.node.get_closest_marker("allow_live_llm"):
        yield
        return

    import sys

    from app.services import llm_provider as _llm_provider

    original = _llm_provider.get_provider

    def _raise(*_args, **_kwargs):
        raise RuntimeError(
            "get_provider() was called with no test stub in place - this test would "
            "make a real network call to an LLM provider. Stub it "
            "(monkeypatch.setattr(<module>, 'get_provider', ...)) or mark the test "
            "@pytest.mark.allow_live_llm."
        )

    for module in list(sys.modules.values()):
        if module is not None and getattr(module, "get_provider", None) is original:
            monkeypatch.setattr(module, "get_provider", _raise, raising=True)
    yield
