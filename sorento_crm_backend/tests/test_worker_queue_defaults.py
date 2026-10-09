"""The worker must drain every queue something enqueues to.

`notification_service` enqueues all delivery jobs with `queue_name="notifications"`,
but the worker's default queue list omitted it, so those jobs were never drained:
86 `notification_deliveries` rows sat `pending` in production, 32 of them in a single
month, and it cost email as well as web push. The failure is silent by construction -
the enqueue succeeds, the row stays `pending`, and nothing raises.

These tests pin the default so the next queue added cannot quietly drop it again.

#569 split draining across two compose services by latency class (`worker` for slow
batch jobs, `worker_fast` for the short customer-facing ones) so a 39-minute import
never again sits in front of a chatbot media turn or a WhatsApp send. Compose names
only a ROLE on each service (`WORKER_ROLE: batch` / `WORKER_ROLE: fast`), never a
queue - `worker.QUEUES` is the single ordered registry mapping each queue to its
role, so adding a queue is a one-line edit there and these tests fail until it is
classified. See the PRODUCTION note above worker.QUEUES.
"""
import importlib
import re
from itertools import chain
from pathlib import Path

import pytest

from app.config import settings

worker = importlib.import_module("worker")

# The real compose file (repo root, three levels up from this test) is gitignored
# everywhere - dev machines and the server both hand-edit their own copy, and it
# embeds live production secrets as env-var fallback defaults, so it must never be
# committed to git. That means it does not exist in a CI checkout: the compose test
# below skips there rather than fails, and is only load-bearing on a machine that
# actually has the file - typically whoever last edited it locally, and the server
# after a manual sync.
_COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"

# PyYAML is not a dependency of this backend (checked at #569 time) and the compose
# file's `${VAR:-default}` interpolation and YAML anchors are more machinery than a
# one-line assertion needs, so this reads `WORKER_ROLE:` lines directly.
_WORKER_ROLE_LINE = re.compile(r'^\s*WORKER_ROLE:\s*"?([^"#\n]+?)"?\s*(?:#.*)?$', re.MULTILINE)
_WORKER_QUEUES_LINE = re.compile(r'^\s*WORKER_QUEUES:\s*"?([^"#\n]*)"?\s*(?:#.*)?$', re.MULTILINE)

# --- Source scan: every RQ queue name the codebase enqueues to -------------
#
# The list above (test_every_known_queue_is_drained_by_default) is hand-typed, and a
# hand-typed list is exactly what let `notifications` go missing from DEFAULT_QUEUES
# in the first place (see the module docstring). This scans app/ for every shape an
# enqueue call takes here and asserts each resulting queue name is either drained by
# DEFAULT_QUEUES or explicitly accounted for elsewhere, so a NEW `queue_name="foo"`
# fails this test instead of silently enqueuing into a queue nothing drains.
_APP_DIR = Path(__file__).resolve().parents[1] / "app"

# The four shapes an enqueue call takes in this codebase: a bare string literal
# (`queue_name="imports"` / `Queue('imports', ...)`), a `settings.<attr>` /
# `get_queue(settings.<attr>)` reference (resolved via getattr on the real settings
# object), or a module-level `SOMETHING_QUEUE = "..."` constant referenced by name
# (resolved by finding its one definition under app/). Regex over source text, not
# AST - these four shapes are stable and a line-based scan is legible where an AST
# visitor would not be simpler.
_QUEUE_NAME_LITERAL = re.compile(r'queue_name\s*=\s*["\']([a-z_]+)["\']')
_QUEUE_CTOR_LITERAL = re.compile(r'\bQueue\(\s*["\']([a-z_]+)["\']')
_QUEUE_NAME_SETTINGS_ATTR = re.compile(r'queue_name\s*=\s*[\w.]*settings\.(\w+)')
_GET_QUEUE_SETTINGS_ATTR = re.compile(r'get_queue\(\s*[\w.]*settings\.(\w+)\)')
_QUEUE_NAME_CONSTANT_REF = re.compile(r'queue_name\s*=\s*(?:\w+\.)?([A-Z_]+_QUEUE)\b')

# Queues that are real and enqueued to, but drained by something other than
# worker.py's Worker.work() loop over DEFAULT_QUEUES, so they belong off that list
# on purpose rather than by omission.
_DRAINED_ELSEWHERE = {
    # embedding_service.py / product_service.py enqueue onto settings.embedding_queue_name
    # ("embeddings" by default). Manually: app/scripts/drain_embedding_queue.py (see its
    # module docstring). Automatically, when ENABLE_SCHEDULER=true (the "worker" batch
    # container only): app/scheduler/task_scheduler.py's embedding_job_processor handler
    # (_handler_embedding_job_processor, registered at task_scheduler.py:500) ticks
    # _run_queue_jobs_impl(settings.embedding_queue_name, ...) on a schedule - a manual
    # pull, not the RQ Worker's queue list. Either way, never worker.QUEUES.
    "embeddings": (
        "drained by app/scripts/drain_embedding_queue.py and by the scheduler's "
        "embedding_job_processor tick (app/scheduler/task_scheduler.py), not by "
        "worker.py's Worker.work() loop"
    ),
}


def _iter_app_py_files():
    for path in _APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _resolve_queue_constant(name, all_texts):
    """Find the one `NAME = "value"` definition under app/ and return its value.

    Fails (does not skip) on zero or more than one definition - a `queue_name=NAME`
    reference that cannot be resolved to exactly one string literal is exactly the
    drift this scan exists to catch.
    """
    pattern = re.compile(rf'^\s*{re.escape(name)}\s*=\s*["\']([a-z_]+)["\']', re.MULTILINE)
    matches = set()
    for text in all_texts:
        matches.update(pattern.findall(text))
    assert len(matches) == 1, (
        f"expected exactly one string-literal definition of {name} under app/, "
        f"found {sorted(matches)!r}"
    )
    return next(iter(matches))


def _enqueued_queue_names():
    """Every RQ queue name the codebase enqueues to, scanned from app/ source text."""
    texts = [path.read_text() for path in _iter_app_py_files()]
    found = set()

    for text in texts:
        found.update(_QUEUE_NAME_LITERAL.findall(text))
        found.update(_QUEUE_CTOR_LITERAL.findall(text))

        for attr in _QUEUE_NAME_SETTINGS_ATTR.findall(text):
            assert hasattr(settings, attr), (
                f"queue_name references settings.{attr}, which does not exist on "
                "app.config.Settings"
            )
            found.add(getattr(settings, attr))
        for attr in _GET_QUEUE_SETTINGS_ATTR.findall(text):
            assert hasattr(settings, attr), (
                f"get_queue references settings.{attr}, which does not exist on "
                "app.config.Settings"
            )
            found.add(getattr(settings, attr))

        for const_name in _QUEUE_NAME_CONSTANT_REF.findall(text):
            found.add(_resolve_queue_constant(const_name, texts))

    return found


def test_every_queue_the_code_enqueues_to_is_drained_by_the_worker():
    """A NEW `queue_name="foo"` must drain via DEFAULT_QUEUES or be on the allowlist."""
    found = _enqueued_queue_names()
    # Guards the regexes themselves: if they silently stopped matching anything,
    # `undrained` below would be vacuously empty and this test would be dead weight.
    assert found, "the source scan found no queue names at all - the regexes broke"
    assert "imports" in found and "notifications" in found, (
        f"expected the scan to find at least 'imports' and 'notifications', got {found!r}"
    )

    undrained = found - set(worker.DEFAULT_QUEUES) - set(_DRAINED_ELSEWHERE)
    assert not undrained, (
        f"queue(s) {sorted(undrained)!r} are enqueued to but not in worker.DEFAULT_QUEUES - "
        "add them to worker.QUEUES with a role, or to _DRAINED_ELSEWHERE in this file "
        "naming the script/handler that drains them"
    )


def test_every_default_queue_has_an_enqueue_site():
    """A queue the worker drains but nothing enqueues to is dead config."""
    found = _enqueued_queue_names()
    dead = set(worker.DEFAULT_QUEUES) - found
    assert not dead, (
        f"queue(s) {sorted(dead)!r} are in worker.DEFAULT_QUEUES but the scan found no "
        "enqueue site for them under app/ - dead config, or the scan needs a new pattern"
    )


def test_notifications_is_drained_by_default():
    """The queue every notification delivery is enqueued to must be in the default list."""
    assert "notifications" in worker.resolve_queue_names()


@pytest.mark.parametrize(
    "queue",
    ["imports", "respond_io", "catalogue_render", "media", "project_docs", "flyer_read",
     "notifications", "chat"],
)
def test_every_known_queue_is_drained_by_default(queue):
    """Each queue the codebase enqueues to, named individually so a drop is legible.

    `chat` (S7, AC-703): the chatbot turn engine's optional worker offload
    (`CHATBOT_TURN_ON_WORKER`) enqueues `run_turn_job` on `chat` - a user is looking at
    "typing..." the same way `respond_io` and `media` are request-latency-bound, so a
    worker that does not drain it silently strands every offloaded turn exactly the way
    `notifications` was silently stranded before this file existed (see the module
    docstring). RED today: nothing enqueues to `chat` yet and it is not in
    `worker.QUEUES`.
    """
    assert queue in worker.resolve_queue_names()


def test_chat_queue_is_classified_fast_not_batch():
    """S7 / AC-703: `chat` is request-latency-bound, so it belongs with `respond_io` and
    `media` under the `fast` role, never `batch` - a batch-classified `chat` queue would
    sit behind a 39-minute import the same way #569's split exists to prevent for the
    others. Membership only, not exact list equality, so this does not also pin the
    relative order of the other fast queues."""
    assert "chat" in worker.queues_for_role("fast")
    assert "chat" not in worker.queues_for_role("batch")


def test_worker_queues_env_still_overrides(monkeypatch):
    """A checkout may run a worker for only the queues it cares about."""
    monkeypatch.setenv("WORKER_QUEUES", "imports, notifications ,")
    assert worker.resolve_queue_names() == ["imports", "notifications"]


def test_blank_override_falls_back_to_the_default(monkeypatch):
    """An empty WORKER_QUEUES must not silently produce a worker draining nothing."""
    monkeypatch.setenv("WORKER_QUEUES", "   ")
    monkeypatch.delenv("WORKER_ROLE", raising=False)
    assert "notifications" in worker.resolve_queue_names()


def test_roles_are_pairwise_disjoint():
    """No queue is classified under two roles - QUEUES pairs each name with exactly one."""
    names = [name for name, _role in worker.QUEUES]
    assert len(names) == len(set(names)), (
        f"a queue name appears more than once in worker.QUEUES: {names!r}"
    )


def test_role_union_is_default_queues():
    """Every queue in DEFAULT_QUEUES is covered by the known roles, with no double-count."""
    by_role = {role: worker.queues_for_role(role) for role in worker.ROLES}
    union = set(chain(*by_role.values()))
    assert union == set(worker.DEFAULT_QUEUES)
    assert sum(len(v) for v in by_role.values()) == len(worker.DEFAULT_QUEUES)


def test_default_queues_starts_with_imports_and_ends_with_notifications():
    assert worker.DEFAULT_QUEUES[0] == "imports"
    assert worker.DEFAULT_QUEUES[-1] == "notifications"


def test_default_queues_keeps_the_pre_split_order():
    """The #569 split must not REORDER the no-env fallback drain list.

    A queue may be added (S7 put `chat` next to `media`, its latency twin); what this
    guards is that the seven that were here before stay in the order they were in, since
    that order is drain priority and #569 is the change that could have scrambled it.
    """
    pre_split = (
        "imports",
        "respond_io",
        "catalogue_render",
        "media",
        "project_docs",
        "flyer_read",
        "notifications",
    )
    assert [q for q in worker.DEFAULT_QUEUES if q in pre_split] == list(pre_split)


def test_queues_for_role_fast(monkeypatch):
    # `chat` joined at S7 (AC-703); the others are #569's.
    assert worker.queues_for_role("fast") == ["respond_io", "media", "chat", "notifications"]


def test_queues_for_role_batch(monkeypatch):
    assert worker.queues_for_role("batch") == [
        "imports",
        "catalogue_render",
        "project_docs",
        "flyer_read",
        "analytics",
    ]


def test_worker_role_fast_resolves_to_exactly_the_fast_queues(monkeypatch):
    monkeypatch.delenv("WORKER_QUEUES", raising=False)
    monkeypatch.setenv("WORKER_ROLE", "fast")
    assert worker.resolve_queue_names() == worker.queues_for_role("fast")


def test_worker_role_batch_resolves_to_exactly_the_batch_queues(monkeypatch):
    monkeypatch.delenv("WORKER_QUEUES", raising=False)
    monkeypatch.setenv("WORKER_ROLE", "batch")
    assert worker.resolve_queue_names() == worker.queues_for_role("batch")


def test_unknown_worker_role_exits(monkeypatch):
    monkeypatch.delenv("WORKER_QUEUES", raising=False)
    monkeypatch.setenv("WORKER_ROLE", "bogus")
    with pytest.raises(SystemExit):
        worker.resolve_queue_names()


def test_worker_queues_beats_worker_role(monkeypatch):
    """The explicit override wins even when a role is also set."""
    monkeypatch.setenv("WORKER_QUEUES", "media")
    monkeypatch.setenv("WORKER_ROLE", "batch")
    assert worker.resolve_queue_names() == ["media"]


def test_compose_names_only_known_roles_and_no_queue_lists():
    """Compose (where present) names a ROLE per service, never a queue list.

    Skips (not fails) when the compose file is absent - see _COMPOSE_PATH docstring
    above. Fails loudly if the file exists but carries either an unrecognized role
    value or a `WORKER_QUEUES:` line at all - a compose-level queue list is exactly
    the hard-coding the role design replaced.
    """
    if not _COMPOSE_PATH.exists():
        pytest.skip(
            f"{_COMPOSE_PATH} is gitignored and not present in this checkout - "
            "this test only runs where the real compose file does (a dev machine "
            "or the server), never in CI"
        )
    text = _COMPOSE_PATH.read_text()

    role_values = [m.strip() for m in _WORKER_ROLE_LINE.findall(text)]
    assert role_values, f"expected at least one 'WORKER_ROLE:' line in {_COMPOSE_PATH.name}"
    unknown = [v for v in role_values if v not in worker.ROLES]
    assert not unknown, (
        f"{_COMPOSE_PATH.name} sets WORKER_ROLE to unrecognized value(s) {unknown!r}; "
        f"known roles: {worker.ROLES}"
    )

    queue_lines = _WORKER_QUEUES_LINE.findall(text)
    assert not queue_lines, (
        f"{_COMPOSE_PATH.name} still sets 'WORKER_QUEUES:' on a service ({queue_lines!r}) - "
        "compose should name only a WORKER_ROLE; worker.QUEUES in worker.py owns the list"
    )
