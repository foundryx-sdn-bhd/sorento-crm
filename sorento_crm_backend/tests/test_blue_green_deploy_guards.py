"""HA-MEM-DEPLOY-GUARD: the blue/green script's memory guard, snapshot and Postgres check.

Runs the real `scripts/blue_green_deploy.sh` against stub `docker`, `sudo` and `sleep`
binaries, so no container, nginx or database is touched. The stub records every call and
can change the db container's state when the new colour starts (`up -d --no-deps`) or when
the worker is recreated (after the nginx swap), which is how the tests simulate Postgres
going down at each point of a deploy.

Incidents: 10 Sep (overlap OOM, Postgres killed, pipeline green), 26 Sep (new code on the old
schema after a failed migration). Runbook: documentation/runbooks/memory-and-deploy-guards.md.
"""
from __future__ import annotations

import fcntl
import os
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "blue_green_deploy.sh"

DOCKER_STUB = r'''#!/usr/bin/env python3
import os, subprocess, sys
d = os.environ["STUB_DIR"]
args = sys.argv[1:]
def rd(name, default=""):
    p = os.path.join(d, name)
    return open(p).read() if os.path.exists(p) else default
def fire(event):
    # Apply a scripted Postgres incident once, when `event` happens.
    if rd("incident_on").strip() != event or os.path.exists(os.path.join(d, "incident_done")):
        return
    open(os.path.join(d, "incident_done"), "w").close()
    if os.path.exists(os.path.join(d, "incident_state")):
        open(os.path.join(d, "db_state"), "w").write(rd("incident_state"))
    if os.path.exists(os.path.join(d, "incident_log")):
        open(os.path.join(d, "db_logs"), "a").write(rd("incident_log"))
with open(os.path.join(d, "calls.log"), "a") as f:
    f.write(" ".join(args) + "\n")
if args[0] == "compose":
    rest = args[1:]
    while rest and rest[0] == "--profile":
        rest = rest[2:]
    cmd = rest[0]
    if cmd == "pull" and os.environ.get("DEPLOY_LOCK_FILE"):
        # Is the deploy lock held while the deploy runs? (flock -n fails when it is.)
        held = subprocess.run(["flock", "-n", os.environ["DEPLOY_LOCK_FILE"], "true"]).returncode != 0
        open(os.path.join(d, "lock_held_during_deploy"), "a").write("yes\n" if held else "no\n")
    if cmd == "up" and os.path.exists(os.path.join(d, "spawn_child")):
        # A child that outlives the script and inherits its fds (as a stray background
        # process would): the lock must still be released when the script exits.
        p = subprocess.Popen(["/bin/sleep", "30"], close_fds=False, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        open(os.path.join(d, "child_pid"), "w").write(str(p.pid))
    if cmd == "ps":
        svc = rest[-1]
        if svc == "db":
            print(rd("db_cid", "cid-db").strip())
        else:
            print("cid-" + svc)
    elif cmd == "up":
        if "--no-deps" in rest and "--force-recreate" not in rest:
            fire("up")
        if "--force-recreate" in rest:
            fire("worker")
    elif cmd == "exec":
        joined = " ".join(rest)
        if "pg_dump" in joined:
            if os.path.exists(os.path.join(d, "snapshot_fail")):
                sys.stderr.write("pg_dump: error: connection failed\n")
                sys.exit(1)
            sys.stdout.write("PGDMP fake custom-format dump\n")
        elif "pg_restore" in joined:
            data = sys.stdin.read()
            sys.exit(0 if data.startswith("PGDMP") else 1)
    sys.exit(0)
if args[0] == "inspect":
    fmt = args[1]
    if "Health.Status" in fmt:
        print("healthy")
    elif "StartedAt" in fmt:
        print(rd("db_state", "0 2026-10-01T00:00:00Z").strip())
    elif "RestartCount" in fmt:
        print("0")
    elif "State.Status" in fmt:
        print("running")
    sys.exit(0)
if args[0] == "logs":
    cid = args[-1]
    if cid == "cid-db":
        sys.stdout.write(rd("db_logs"))
    elif cid == "cid-worker":
        print("APScheduler started in worker process")
    elif cid == "cid-worker_fast":
        print("Starting RQ worker role=fast")
    sys.exit(0)
sys.exit(0)
'''


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def box(tmp_path):
    """A fake /opt/sorento-crm2: the script under scripts/, stubs on PATH."""
    root = tmp_path / "box"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "blue_green_deploy.sh")
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "docker", DOCKER_STUB)
    _exe(bin_dir / "sudo", f'#!/bin/sh\necho "sudo $*" >> "{stub_dir}/calls.log"\nexit 0\n')
    _exe(bin_dir / "sleep", "#!/bin/sh\nexit 0\n")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16000000 kB\nMemAvailable:    8000000 kB\n")
    box = {"root": root, "stub": stub_dir, "bin": bin_dir, "meminfo": meminfo, "lock": root / ".deploy.lock"}
    yield box
    pid_file = stub_dir / "child_pid"
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass


def _run(box, **env_extra):
    env = {
        "PATH": f"{box['bin']}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "STUB_DIR": str(box["stub"]),
        "IMAGE_TAG": "abc123",
        "MEMINFO_FILE": str(box["meminfo"]),
        "DEPLOY_LOCK_FILE": str(box["lock"]),
        "HOME": str(box["root"]),
    }
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(box["root"] / "scripts" / "blue_green_deploy.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _calls(box) -> list[str]:
    log = box["stub"] / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def _index(calls, needle) -> int:
    for i, line in enumerate(calls):
        if needle in line:
            return i
    return -1


def _started_new_colour(calls) -> bool:
    return any(l.startswith("compose") and " up -d --no-deps backend_" in l for l in calls)


def _swapped(calls) -> bool:
    return any("sudo install" in l for l in calls)


def _set_mem_mb(box, mb: int) -> None:
    box["meminfo"].write_text(f"MemTotal:       16000000 kB\nMemAvailable:   {mb * 1024} kB\n")


def _incident(box, on: str, state: str | None = None, log: str | None = None) -> None:
    (box["stub"] / "incident_on").write_text(on)
    if state is not None:
        (box["stub"] / "incident_state").write_text(state)
    if log is not None:
        (box["stub"] / "incident_log").write_text(log)


# --- happy path ----------------------------------------------------------------


def test_healthy_deploy_snapshots_before_the_new_colour_and_succeeds(box):
    r = _run(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Deploy complete" in r.stdout
    calls = _calls(box)
    dump = _index(calls, "pg_dump")
    up = _index(calls, "up -d --no-deps backend_")
    assert dump != -1 and up != -1 and dump < up, calls
    assert _index(calls, "pg_restore --list") != -1, "the dump must be checked before migrating"
    dumps = list((box["root"] / "backups" / "pre-migrate").glob("pre-migrate-*-abc123.dump"))
    assert len(dumps) == 1, dumps
    assert stat.S_IMODE(dumps[0].stat().st_mode) == 0o600
    assert not list((box["root"] / "backups" / "pre-migrate").glob("*.partial"))


# --- memory guard --------------------------------------------------------------


def test_memory_guard_refuses_the_new_colour_below_the_default_threshold(box):
    _set_mem_mb(box, 2000)
    r = _run(box)
    assert r.returncode != 0
    assert "memory guard" in r.stdout and "2000 MB available" in r.stdout, r.stdout
    calls = _calls(box)
    assert not _started_new_colour(calls), calls
    assert not _swapped(calls)


def test_memory_threshold_is_env_tunable(box):
    _set_mem_mb(box, 2000)
    r = _run(box, DEPLOY_MIN_AVAILABLE_MB="1500")
    assert r.returncode == 0, r.stdout + r.stderr


def test_memory_threshold_can_be_set_on_the_box_in_deploy_env(box):
    (box["root"] / ".deploy.env").write_text("DEPLOY_MIN_AVAILABLE_MB=9000\n")
    r = _run(box)  # 8000 MB available
    assert r.returncode != 0
    assert "DEPLOY_MIN_AVAILABLE_MB=9000" in r.stdout, r.stdout
    assert not _started_new_colour(_calls(box))


def test_unreadable_meminfo_fails_closed(box):
    box["meminfo"].write_text("nothing useful\n")
    r = _run(box)
    assert r.returncode != 0
    assert not _started_new_colour(_calls(box))


# --- snapshot ------------------------------------------------------------------


def test_failed_snapshot_stops_the_deploy_before_any_migration(box):
    (box["stub"] / "snapshot_fail").write_text("1")
    r = _run(box)
    assert r.returncode != 0
    assert "snapshot failed" in r.stdout, r.stdout
    assert not _started_new_colour(_calls(box))
    snap_dir = box["root"] / "backups" / "pre-migrate"
    assert not list(snap_dir.glob("*")) if snap_dir.exists() else True


def test_snapshots_older_than_48h_are_pruned_and_newer_kept(box):
    snap_dir = box["root"] / "backups" / "pre-migrate"
    snap_dir.mkdir(parents=True)
    old = snap_dir / "pre-migrate-20261001T000000Z-old.dump"
    recent = snap_dir / "pre-migrate-20261013T000000Z-recent.dump"
    unrelated = snap_dir / "keep-me.sql"
    for f in (old, recent, unrelated):
        f.write_text("x")
    three_days = time.time() - 3 * 24 * 3600
    os.utime(old, (three_days, three_days))
    os.utime(unrelated, (three_days, three_days))
    r = _run(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not old.exists()
    assert recent.exists()
    assert unrelated.exists(), "only the script's own snapshots are pruned"


# --- Postgres restart / crash detection ----------------------------------------


def test_missing_db_container_fails_before_anything_changes(box):
    (box["stub"] / "db_cid").write_text("")
    r = _run(box)
    assert r.returncode != 0
    assert "Postgres container" in r.stdout, r.stdout
    calls = _calls(box)
    assert not any(" pull " in l for l in calls), calls
    assert not _started_new_colour(calls)


def test_db_container_restart_while_the_new_colour_starts_aborts_before_the_swap(box):
    _incident(box, "up", state="1 2026-10-14T09:16:09Z")
    r = _run(box)
    assert r.returncode != 0
    assert "restarted during the deploy" in r.stdout, r.stdout
    calls = _calls(box)
    assert not _swapped(calls), "traffic must stay on the old colour"
    assert _index(calls, "stop backend_") != -1, "the new colour is stopped"


def test_postgres_crash_recovery_without_a_container_restart_is_caught(box):
    """The 10 Sep shape: the OOM killer took one backend, the postmaster reinitialized in
    place, the container never restarted and pg_postmaster_start_time() did not move."""
    _incident(
        box,
        "up",
        log=(
            "LOG:  server process (PID 4242) was terminated by signal 9: Killed\n"
            "LOG:  all server processes terminated; reinitializing\n"
        ),
    )
    r = _run(box)
    assert r.returncode != 0
    assert "crash-recovered" in r.stdout, r.stdout
    assert not _swapped(_calls(box))


def test_postgres_down_after_the_swap_still_fails_the_pipeline(box):
    _incident(box, "worker", state="1 2026-10-14T09:16:09Z")
    r = _run(box)
    assert r.returncode != 0, "a deploy during which Postgres restarted is never green"
    assert "Deploy complete" not in r.stdout
    assert "deploy FAILED" in r.stdout, r.stdout
    assert _swapped(_calls(box))


def test_db_logs_are_read_from_the_deploy_start_only(box):
    r = _run(box)
    assert r.returncode == 0, r.stdout + r.stderr
    db_log_calls = [l for l in _calls(box) if l.startswith("logs") and l.endswith("cid-db")]
    assert db_log_calls and all("--since" in l for l in db_log_calls), db_log_calls


# --- deploy lock (shared with the weekly restore test, HA-BACKUP-CHECK) -----------------


def _lock_is_free(path: Path) -> bool:
    return subprocess.run(["flock", "-n", str(path), "true"]).returncode == 0


def test_the_lock_path_on_the_box_is_fixed():
    """The restore test waits on this exact file; a different path means no mutual exclusion."""
    assert 'DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/opt/sorento-crm2/.deploy.lock}"' in SCRIPT.read_text()


def test_the_lock_is_held_for_the_deploy_and_released_after_success(box):
    r = _run(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert set((box["stub"] / "lock_held_during_deploy").read_text().split()) == {"yes"}
    assert _lock_is_free(box["lock"])


def test_the_lock_is_released_after_a_failed_deploy(box):
    _set_mem_mb(box, 2000)
    r = _run(box)
    assert r.returncode != 0
    assert set((box["stub"] / "lock_held_during_deploy").read_text().split()) == {"yes"}
    assert _lock_is_free(box["lock"])


def test_the_lock_is_released_even_when_a_child_keeps_the_fd_open(box):
    (box["stub"] / "spawn_child").write_text("1")
    _incident(box, "up", state="1 2026-10-14T09:16:09Z")  # fail after the child started
    r = _run(box)
    assert r.returncode != 0
    pid = int((box["stub"] / "child_pid").read_text())
    os.kill(pid, 0)  # the child is still alive and still holds the inherited fd
    assert _lock_is_free(box["lock"])


def test_a_held_lock_makes_the_deploy_wait_then_give_up_without_touching_anything(box):
    box["lock"].touch()
    with open(box["lock"]) as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        r = _run(box, DEPLOY_LOCK_WAIT_SECONDS="1")
    assert r.returncode != 0
    assert "/.deploy.lock" in r.stdout and "held" in r.stdout, r.stdout
    assert _calls(box) == [], "nothing may run while a restore test or another deploy holds the box"
