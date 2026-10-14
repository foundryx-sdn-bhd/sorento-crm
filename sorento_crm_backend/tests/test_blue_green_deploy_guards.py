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
    if cmd == "pull" and os.environ.get("DEPLOY_LOCK_PATH"):
        # Is the deploy lock held while the deploy runs? (flock -n fails when it is.)
        held = subprocess.run(["flock", "-n", os.environ["DEPLOY_LOCK_PATH"], "true"]).returncode != 0
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
            if os.path.exists(os.path.join(d, "snapshot_garbage")):
                sys.stdout.write("pg_dump: warning: something odd\n")
                sys.exit(0)
            # A fake archive: the magic, then the TOC lines the fake pg_restore -l prints.
            sys.stdout.write("PGDMP fake custom-format dump\n")
            sys.stdout.write("610; 1259 25725 TABLE public alembic_version sorento\n")
            if not os.path.exists(os.path.join(d, "snapshot_no_alembic")):
                sys.stdout.write("9531; 0 25725 TABLE DATA public alembic_version sorento\n")
            sys.stdout.write("9532; 0 25800 TABLE DATA public users sorento\n")
        elif "pg_restore" in joined:
            data = sys.stdin.read()
            if not data.startswith("PGDMP"):
                sys.exit(1)
            sys.stdout.write("".join(data.splitlines(keepends=True)[1:]))
        elif "psql" in joined:
            if os.path.exists(os.path.join(d, "psql_fail")):
                sys.stderr.write('psql: error: database "x" does not exist\n')
                sys.exit(2)
            print(rd("alembic_rows", "1").strip())
    sys.exit(0)
if args[0] == "inspect":
    fmt = args[1]
    if ".Config.Env" in fmt:
        sys.stdout.write(rd("backend_env", "PATH=/usr/bin\nDATABASE_URL=postgresql://app:s3cret@pgbouncer:6432/sorento_app?sslmode=disable\nOTHER=1\n"))
    elif "Health.Status" in fmt:
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
        # Lines may carry an "@<RFC3339>|" prefix; honour --since on those like docker does.
        since = args[args.index("--since") + 1] if "--since" in args else ""
        for line in rd("db_logs").splitlines(keepends=True):
            if line.startswith("@"):
                ts, _, line = line[1:].partition("|")
                if since and ts < since:
                    continue
            sys.stdout.write(line)
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
    # df -Pm <dir>: free MB from the stub dir (default plenty).
    _exe(
        bin_dir / "df",
        f'#!/bin/sh\nfree=$(cat "{stub_dir}/disk_free_mb" 2>/dev/null || echo 500000)\n'
        'printf "Filesystem 1048576-blocks Used Available Capacity Mounted on\\n/dev/x 900000 1 %s 1%% /\\n" "$free"\n',
    )
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


def _run(box, timeout=120, args=(), **env_extra):
    env = {
        "PATH": f"{box['bin']}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "STUB_DIR": str(box["stub"]),
        "IMAGE_TAG": "abc123",
        "MEMINFO_FILE": str(box["meminfo"]),
        "DEPLOY_LOCK_PATH": str(box["lock"]),
        "HOME": str(box["root"]),
    }
    env.update(env_extra)
    env = {k: v for k, v in env.items() if v != ""}
    return subprocess.run(
        ["bash", str(box["root"] / "scripts" / "blue_green_deploy.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
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


def test_the_lock_path_defaults_to_the_one_the_restore_test_uses():
    """HA-BACKUP-CHECK's restore test reads the same variable with the same default; a
    different path means no mutual exclusion."""
    assert 'DEPLOY_LOCK_PATH="${DEPLOY_LOCK_PATH:-/opt/sorento-crm2/.deploy.lock}"' in SCRIPT.read_text()


@pytest.mark.parametrize("via", ["env", "deploy_env"])
def test_the_lock_path_can_be_overridden(box, tmp_path, via):
    """The /var/lib/sorento-deploy fallback for a box where /opt/sorento-crm2 is not root-owned."""
    other = tmp_path / "var" / "lib" / "sorento-deploy" / "deploy.lock"
    other.parent.mkdir(parents=True)
    env = {}
    if via == "env":
        env["DEPLOY_LOCK_PATH"] = str(other)
    else:
        (box["root"] / ".deploy.env").write_text(f"DEPLOY_LOCK_PATH={other}\n")
        env["DEPLOY_LOCK_PATH"] = ""  # unset by the caller: the on-box file decides
    with open(other, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        r = _run(box, timeout=30, DEPLOY_LOCK_WAIT_SECONDS="1", **env)
    assert r.returncode != 0
    assert str(other) in r.stdout, r.stdout
    assert _calls(box) == []
    r = _run(box, **env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _lock_is_free(other)


def test_the_lock_wait_can_be_set_in_deploy_env(box):
    (box["root"] / ".deploy.env").write_text("DEPLOY_LOCK_WAIT_SECONDS=1\n")
    box["lock"].touch()
    with open(box["lock"]) as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        r = _run(box, timeout=30)
    assert r.returncode != 0
    assert "still held" in r.stdout, r.stdout


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


# --- review round 1 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "knob", ["DEPLOY_MIN_AVAILABLE_MB=4500MB", "SNAPSHOT_KEEP_MINUTES=two-days", "SNAPSHOT_MIN_FREE_MB=10G", "DEPLOY_LOCK_WAIT_SECONDS=forever"]
)
def test_a_non_numeric_knob_fails_closed(box, knob):
    """`[ 8000 -lt 4500MB ]` is an error, which an `if` reads as false: the guard would pass."""
    (box["root"] / ".deploy.env").write_text(knob + "\n")
    r = _run(box, timeout=30)
    assert r.returncode != 0
    assert knob.split("=")[0] in r.stdout, r.stdout
    assert not _started_new_colour(_calls(box))


def test_memory_is_checked_before_the_snapshot(box):
    _set_mem_mb(box, 2000)
    r = _run(box)
    assert r.returncode != 0
    assert _index(_calls(box), "pg_dump") == -1, "no dump on a box that cannot take the deploy"


def test_low_disk_stops_the_deploy_before_the_dump(box):
    (box["stub"] / "disk_free_mb").write_text("5000")  # default floor is 10240 MB
    r = _run(box)
    assert r.returncode != 0
    assert "disk" in r.stdout, r.stdout
    calls = _calls(box)
    assert _index(calls, "pg_dump") == -1
    assert not _started_new_colour(calls)


@pytest.mark.parametrize("free_mb,ok", [(5, False), (7, True)])
def test_disk_must_hold_twice_the_newest_dump(box, free_mb, ok):
    snap_dir = box["root"] / "backups" / "pre-migrate"
    snap_dir.mkdir(parents=True)
    (snap_dir / "pre-migrate-20261013T000000Z-prev.dump").write_bytes(b"x" * (3 * 1024 * 1024))
    (box["stub"] / "disk_free_mb").write_text(str(free_mb))
    r = _run(box, SNAPSHOT_MIN_FREE_MB="1")
    assert (r.returncode == 0) is ok, r.stdout + r.stderr


def test_output_that_is_not_a_custom_format_dump_is_refused(box):
    (box["stub"] / "snapshot_garbage").write_text("1")
    r = _run(box)
    assert r.returncode != 0
    assert "snapshot failed" in r.stdout, r.stdout
    assert not _started_new_colour(_calls(box))
    assert not list((box["root"] / "backups" / "pre-migrate").glob("*"))


def test_stale_partial_dumps_are_pruned_too(box):
    snap_dir = box["root"] / "backups" / "pre-migrate"
    snap_dir.mkdir(parents=True)
    partial = snap_dir / "pre-migrate-20261001T000000Z-old.dump.partial"
    partial.write_text("x")
    three_days = time.time() - 3 * 24 * 3600
    os.utime(partial, (three_days, three_days))
    r = _run(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not partial.exists()


def test_ordinary_postgres_log_lines_do_not_fail_the_deploy(box):
    _incident(
        box,
        "up",
        log=(
            "LOG:  checkpoint starting: time\n"
            "LOG:  checkpoint complete: wrote 42 buffers (0.3%)\n"
            "FATAL:  terminating connection due to administrator command\n"
            "LOG:  connection received: host=172.18.0.5 port=40000\n"
            "ERROR:  canceling statement due to statement timeout\n"
        ),
    )
    r = _run(box)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_crash_before_the_deploy_started_is_not_this_deploys(box):
    (box["stub"] / "db_logs").write_text(
        "@2020-01-01T00:00:00Z|LOG:  all server processes terminated; reinitializing\n"
    )
    r = _run(box)
    assert r.returncode == 0, r.stdout + r.stderr


# --- crew audit round (a)-(e) -----------------------------------------------------------


def _dump_call(box) -> str:
    return next(l for l in _calls(box) if "pg_dump" in l)


# (b) the database the APP uses


def test_the_dump_targets_the_apps_database_from_database_url(box):
    r = _run(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DUMP_DB=sorento_app" in _dump_call(box), "name parsed from the backend's DATABASE_URL"
    assert '"$POSTGRES_DB"' not in _dump_call(box), "never the db container's own POSTGRES_DB"
    assert "s3cret" not in r.stdout + r.stderr, "the URL (with its password) is never printed"


def test_deploy_db_name_in_deploy_env_wins(box):
    (box["root"] / ".deploy.env").write_text("DEPLOY_DB_NAME=sorento_crm\n")
    r = _run(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DUMP_DB=sorento_crm" in _dump_call(box)


@pytest.mark.parametrize(
    "backend_env",
    ["PATH=/usr/bin\n", "DATABASE_URL=postgresql://u:p@db:5432\n", "DATABASE_URL=postgresql://u:p@db/x;rm -rf /\n"],
    ids=["no-url", "no-db-in-url", "unsafe-name"],
)
def test_an_unresolvable_db_name_fails_loudly_before_anything_changes(box, backend_env):
    (box["stub"] / "backend_env").write_text(backend_env)
    r = _run(box)
    assert r.returncode != 0
    assert "DEPLOY_DB_NAME" in r.stdout, r.stdout
    calls = _calls(box)
    assert _index(calls, "pg_dump") == -1
    assert not any(" pull " in l for l in calls)
    assert not _started_new_colour(calls)


# (a) the dump must hold alembic_version's data


def test_a_dump_without_alembic_version_data_stops_the_deploy(box):
    (box["stub"] / "snapshot_no_alembic").write_text("1")
    r = _run(box)
    assert r.returncode != 0
    assert "alembic_version" in r.stdout, r.stdout
    assert not _started_new_colour(_calls(box))
    assert not list((box["root"] / "backups" / "pre-migrate").glob("*")), "no half-checked dump is kept"


def test_the_toc_is_read_inside_the_db_container_with_stdin_drained(box):
    r = _run(box)
    assert r.returncode == 0, r.stdout + r.stderr
    toc = next(l for l in _calls(box) if "pg_restore -l" in l)
    assert "cat > /dev/null" in toc, "drain stdin so docker never writes into a closed pipe"


# (d) lock wait default


def test_the_lock_wait_defaults_to_600s(box):
    r = _run(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Waiting up to 600s" in r.stdout, r.stdout


# (c) --preflight


def _snapshot_files(box):
    snap = box["root"] / "backups"
    return sorted(p.relative_to(box["root"]).as_posix() for p in snap.rglob("*")) if snap.exists() else []


def _mutating(calls):
    words = (" pull ", " up ", " stop ", " rm ", "pg_dump", "prune", "sudo", "--force-recreate")
    return [l for l in calls if any(w in f" {l} " for w in words)]


def test_preflight_passes_on_a_healthy_box_and_changes_nothing(box):
    r = _run(box, args=("--preflight",), IMAGE_TAG="")
    assert r.returncode == 0, r.stdout + r.stderr
    for item in ("lock", "db service", "db name", "alembic_version", "memory", "disk", "snapshot dir"):
        assert item in r.stdout, (item, r.stdout)
    assert "FAIL" not in r.stdout
    assert _mutating(_calls(box)) == [], _calls(box)
    assert not box["lock"].exists(), "preflight creates no lock file"
    assert _snapshot_files(box) == [], "preflight creates no snapshot dir"
    assert not (box["root"] / ".active_color").exists()


@pytest.mark.parametrize(
    "setup,item",
    [
        (lambda b: _set_mem_mb(b, 2000), "memory"),
        (lambda b: (b["stub"] / "disk_free_mb").write_text("5000"), "disk"),
        (lambda b: (b["stub"] / "db_cid").write_text(""), "db service"),
        (lambda b: (b["stub"] / "backend_env").write_text("PATH=/x\n"), "db name"),
        (lambda b: (b["stub"] / "psql_fail").write_text("1"), "alembic_version"),
        (lambda b: (b["root"] / ".deploy.env").write_text("SNAPSHOT_MIN_FREE_MB=10G\n"), "SNAPSHOT_MIN_FREE_MB"),
    ],
    ids=["memory", "disk", "db-service", "db-name", "alembic", "knob"],
)
def test_preflight_fails_and_names_the_failing_item(box, setup, item):
    setup(box)
    r = _run(box, args=("--preflight",), IMAGE_TAG="")
    assert r.returncode == 1, r.stdout + r.stderr
    fail_lines = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
    assert any(item in l for l in fail_lines), r.stdout
    assert _mutating(_calls(box)) == []


def test_preflight_flags_a_snapshot_dir_others_can_read(box):
    snap = box["root"] / "backups" / "pre-migrate"
    snap.mkdir(parents=True)
    snap.chmod(0o755)
    r = _run(box, args=("--preflight",), IMAGE_TAG="")
    assert r.returncode == 1
    assert any("snapshot dir" in l for l in r.stdout.splitlines() if l.startswith("FAIL")), r.stdout
    assert stat.S_IMODE(snap.stat().st_mode) == 0o755, "preflight reports, it does not chmod"


def test_preflight_reports_a_held_lock_without_taking_it(box):
    box["lock"].touch()
    with open(box["lock"]) as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        r = _run(box, args=("--preflight",), IMAGE_TAG="", timeout=30)
    assert "lock" in r.stdout and "held" in r.stdout, r.stdout


def test_an_unknown_argument_is_refused(box):
    r = _run(box, args=("--prefligth",))
    assert r.returncode == 2
    assert _calls(box) == []
