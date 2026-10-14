"""Shell tests for scripts/ops/backup-db.sh and scripts/ops/restore-test.sh.

No network, no Docker, no Postgres: fake `docker`, `pg_dump`, `rclone`, `curl` and `age`
executables sit first on PATH and log their argv to calls.log. The fake Drive is a local
directory; any rclone argument containing ':' maps into it.

Run: python3 -m pytest scripts/ops/tests -q
"""

from __future__ import annotations

import fcntl
import gzip
import os
import shlex
import subprocess
import textwrap
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
BACKUP = OPS / "backup-db.sh"
RESTORE = OPS / "restore-test.sh"

DUMP_SQL = textwrap.dedent(
    """\
    --
    -- PostgreSQL database dump
    --

    -- Dumped from database version 17.2 (Debian 17.2-1.pgdg120+1)
    -- Dumped by pg_dump version 17.2 (Debian 17.2-1.pgdg120+1)

    CREATE TABLE public.users (id integer);
    """
)

FAKES = {
    # docker: `exec <c> pg_dump ...` prints a dump, `exec -i <c> psql` drains stdin,
    # `exec <c> psql -c <sql>` answers counts / max(), `run` prints an id.
    "docker": r"""#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  run) [ -n "${FAKE_DOCKER_RUN_FAIL:-}" ] && exit 1; echo fakecontainerid; exit 0 ;;
  rm) exit 0 ;;
  ps) [ -n "${FAKE_DOCKER_PS:-}" ] && echo "$FAKE_DOCKER_PS"; exit 0 ;;
  exec)
    shift
    [ "$1" = "-i" ] && { shift; stdin=1; }
    shift  # container
    case "$1" in
      pg_dump) [ -n "${FAKE_PG_DUMP_FAIL:-}" ] && { echo partial; exit 1; }; cat "$FAKE_DUMP_SQL"; exit 0 ;;
      pg_isready) exit 0 ;;
      psql)
        if [ -n "${stdin:-}" ]; then cat > /dev/null; [ -n "${FAKE_PSQL_RESTORE_FAIL:-}" ] && exit 3; exit 0; fi
        sql="${@: -1}"
        case "$sql" in
          *"count(*)"*)
            for t in ${FAKE_EMPTY_TABLES:-}; do case "$sql" in *" $t"*) echo 0; exit 0 ;; esac; done
            echo 42 ;;
          *"max("*) echo "2026-10-14 02:59:00" ;;
          *) echo ok ;;
        esac
        exit 0 ;;
    esac ;;
esac
exit 0
""",
    "pg_dump": r"""#!/usr/bin/env bash
echo "pg_dump $*" >> "$FAKE_LOG"
[ -n "${FAKE_PG_DUMP_FAIL:-}" ] && exit 1
cat "$FAKE_DUMP_SQL"
""",
    "rclone": r"""#!/usr/bin/env bash
echo "rclone $*" >> "$FAKE_LOG"
map() { case "$1" in *:*) echo "$FAKE_REMOTE/${1#*:}" ;; *) echo "$1" ;; esac; }
cmd="$1"; shift
args=(); inc=()
while [ $# -gt 0 ]; do
  case "$1" in
    --include) inc+=("$2"); shift 2 ;;
    --max-depth|--min-age) shift 2 ;;
    -*) shift ;;
    *) args+=("$1"); shift ;;
  esac
done
case "$cmd" in
  copy)
    [ -n "${FAKE_RCLONE_COPY_FAIL:-}" ] && exit 1
    dst="$(map "${args[1]}")"; mkdir -p "$dst"; cp "$(map "${args[0]}")" "$dst/" ;;
  copyto) cp "$(map "${args[0]}")" "$(map "${args[1]}")" ;;
  lsf)
    d="$(map "${args[0]}")"; [ -d "$d" ] || exit 0
    for f in "$d"/*; do
      [ -f "$f" ] || continue; b="${f##*/}"
      if [ ${#inc[@]} -eq 0 ]; then echo "$b"; continue; fi
      for g in "${inc[@]}"; do case "$b" in $g) echo "$b"; break ;; esac; done
    done ;;
  delete) [ -n "${FAKE_RCLONE_DELETE_FAIL:-}" ] && exit 1; exit 0 ;;
esac
""",
    "curl": r"""#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
[ -n "${FAKE_CURL_FAIL:-}" ] && exit 7
exit 0
""",
    "age": r"""#!/usr/bin/env bash
echo "age $*" >> "$FAKE_LOG"
out=""; in=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out="$2"; shift 2 ;; -r|-i|-R) shift 2 ;; -d) shift ;; *) in="$1"; shift ;; esac
done
if [ -n "$in" ]; then cat "$in" > "$out"; else cat > "$out"; fi
""",
}


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in FAKES.items():
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)
    remote = tmp_path / "remote"
    remote.mkdir()
    dump_sql = tmp_path / "dump.sql"
    dump_sql.write_text(DUMP_SQL)
    e = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "FAKE_LOG": str(tmp_path / "calls.log"),
        "FAKE_REMOTE": str(remote),
        "FAKE_DUMP_SQL": str(dump_sql),
        "BACKUP_ENV_FILE": str(tmp_path / "no-such.env"),
        "BACKUP_NAME": "sorento_crm",
        "DB_CONTAINER": "proddb",
        "DB_USER": "crm",
        "DB_NAME": "crmdb",
        "RCLONE_DEST": "gdrive:db-backups",
        "RETENTION_DAYS": "30",
        "BACKUP_DIR": str(tmp_path / "work"),
        "LOCK_FILE": str(tmp_path / "backup.lock"),
        "RESTORE_WORK_DIR": str(tmp_path / "restore"),
        "RESTORE_LOCK_FILE": str(tmp_path / "restore.lock"),
        "RESTORE_READY_TIMEOUT": "5",
        "SANITY_TABLES": "users customers orders products audit_logs",
        "SANITY_LATEST": "audit_logs.changed_at",
    }
    return e, tmp_path


def run(script, e, **extra):
    full = {**e, **{k: str(v) for k, v in extra.items()}}
    return subprocess.run(["bash", str(script)], env=full, capture_output=True, text=True, timeout=60)


def calls(tmp_path):
    p = tmp_path / "calls.log"
    return p.read_text().splitlines() if p.exists() else []


def curl_urls(tmp_path):
    return [shlex.split(c)[-1] for c in calls(tmp_path) if c.startswith("curl ")]


def remote_files(tmp_path, sub="db-backups"):
    d = tmp_path / "remote" / sub
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


HC = "https://hc-ping.example/uuid-b"
HCR = "https://hc-ping.example/uuid-r"


# ---------------------------------------------------------------- backup-db.sh


def test_backup_success_pings_uploads_prunes(env):
    e, tmp = env
    r = run(BACKUP, e, HC_BACKUP_URL=HC)
    assert r.returncode == 0, r.stderr
    assert curl_urls(tmp) == [f"{HC}/start", HC]
    files = remote_files(tmp)
    assert len(files) == 1 and files[0].startswith("sorento_crm_") and files[0].endswith(".sql.gz")
    with gzip.open(tmp / "remote" / "db-backups" / files[0], "rt") as fh:
        assert "Dumped from database version 17.2" in fh.read()
    log = calls(tmp)
    assert any(c.startswith("docker exec proddb pg_dump") and "-U crm" in c and "crmdb" in c for c in log)
    deletes = [c for c in log if c.startswith("rclone delete")]
    assert len(deletes) == 1
    assert "--min-age 30d" in deletes[0] and "gdrive:db-backups" in deletes[0]
    assert "sorento_crm_[0-9]*Z.sql.gz*" in deletes[0]
    # local copy removed after upload
    assert list((tmp / "work").glob("*.sql.gz*")) == []


def test_backup_host_pg_dump_when_no_container(env):
    e, tmp = env
    e = {k: v for k, v in e.items() if k != "DB_CONTAINER"}
    r = run(BACKUP, e)
    assert r.returncode == 0, r.stderr
    assert any(c.startswith("pg_dump ") for c in calls(tmp))
    assert not any(c.startswith("docker ") for c in calls(tmp))


@pytest.mark.parametrize(
    "fault,code",
    [("FAKE_PG_DUMP_FAIL", 3), ("FAKE_RCLONE_COPY_FAIL", 6)],
)
def test_backup_failure_pings_fail_and_skips_prune(env, fault, code):
    e, tmp = env
    r = run(BACKUP, e, HC_BACKUP_URL=HC, **{fault: "1"})
    assert r.returncode == code, r.stderr
    assert curl_urls(tmp) == [f"{HC}/start", f"{HC}/fail"]
    assert not any(c.startswith("rclone delete") for c in calls(tmp))
    assert list((tmp / "work").glob("*.sql.gz*")) == []


def test_backup_corrupt_gzip_fails_before_upload(env, tmp_path):
    e, tmp = env
    # a gzip on PATH whose output is not gzip: `gzip -t` must catch it
    fake_gzip = tmp / "bin" / "gzip"
    real = subprocess.run(["bash", "-c", "command -v gzip"], capture_output=True, text=True).stdout.strip()
    fake_gzip.write_text(f'#!/usr/bin/env bash\nif [ "$1" = "-t" ]; then exec {real} "$@"; fi\ncat >/dev/null; echo notgzip\n')
    fake_gzip.chmod(0o755)
    r = run(BACKUP, e, HC_BACKUP_URL=HC)
    assert r.returncode == 4, r.stderr
    assert curl_urls(tmp) == [f"{HC}/start", f"{HC}/fail"]
    assert not any(c.startswith("rclone copy") for c in calls(tmp))


def test_backup_prune_failure_alerts_but_keeps_upload(env):
    e, tmp = env
    r = run(BACKUP, e, HC_BACKUP_URL=HC, FAKE_RCLONE_DELETE_FAIL="1")
    assert r.returncode == 7
    assert len(remote_files(tmp)) == 1
    assert curl_urls(tmp)[-1] == f"{HC}/fail"


def test_backup_lock_held_exits_75_without_dumping(env):
    e, tmp = env
    with open(e["LOCK_FILE"], "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = run(BACKUP, e, HC_BACKUP_URL=HC)
    assert r.returncode == 75
    assert not any("pg_dump" in c for c in calls(tmp))
    assert curl_urls(tmp) == []


def test_backup_without_hc_url_runs_without_pings(env):
    e, tmp = env
    r = run(BACKUP, e)
    assert r.returncode == 0, r.stderr
    assert curl_urls(tmp) == []
    assert len(remote_files(tmp)) == 1


def test_backup_ping_failure_does_not_fail_backup(env):
    e, tmp = env
    r = run(BACKUP, e, HC_BACKUP_URL=HC, FAKE_CURL_FAIL="1")
    assert r.returncode == 0, r.stderr
    assert len(remote_files(tmp)) == 1


def test_backup_age_encryption(env):
    e, tmp = env
    r = run(BACKUP, e, BACKUP_ENCRYPT="age", AGE_RECIPIENT="age1examplerecipient")
    assert r.returncode == 0, r.stderr
    files = remote_files(tmp)
    assert len(files) == 1 and files[0].endswith(".sql.gz.age")
    assert any(c.startswith("age ") and "-r age1examplerecipient" in c for c in calls(tmp))
    assert list((tmp / "work").iterdir()) == []


def test_backup_age_without_recipient_fails_before_dump(env):
    e, tmp = env
    r = run(BACKUP, e, HC_BACKUP_URL=HC, BACKUP_ENCRYPT="age")
    assert r.returncode == 2
    assert not any("pg_dump" in c for c in calls(tmp))
    assert curl_urls(tmp)[-1] == f"{HC}/fail"


def test_backup_unknown_encrypt_mode_rejected(env):
    e, tmp = env
    r = run(BACKUP, e, BACKUP_ENCRYPT="rot13")
    assert r.returncode == 2
    assert remote_files(tmp) == []


def test_backup_rejects_bad_retention(env):
    e, tmp = env
    r = run(BACKUP, e, RETENTION_DAYS="0")
    assert r.returncode == 2
    assert remote_files(tmp) == []


def test_backup_env_file_is_read(env):
    e, tmp = env
    envf = tmp / "backup.env"
    envf.write_text("BACKUP_NAME=n8n\nRCLONE_DEST=gdrive:db-backups/n8n\n")
    e = {k: v for k, v in e.items() if k not in ("BACKUP_NAME", "RCLONE_DEST")}
    r = run(BACKUP, e, BACKUP_ENV_FILE=envf)
    assert r.returncode == 0, r.stderr
    files = remote_files(tmp, "db-backups/n8n")
    assert len(files) == 1 and files[0].startswith("n8n_")


# ------------------------------------------------------------- restore-test.sh


def seed_remote(tmp, names, sub="db-backups"):
    d = tmp / "remote" / sub
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        with gzip.open(d / n, "wt") as fh:
            fh.write(DUMP_SQL)


def docker_calls(tmp):
    return [c for c in calls(tmp) if c.startswith("docker ")]


def test_restore_newest_dump_into_throwaway_container(env):
    e, tmp = env
    seed_remote(tmp, ["sorento_crm_20261012T030000Z.sql.gz", "sorento_crm_20261014T030000Z.sql.gz",
                      "sorento_crm_20261013T030000Z.sql.gz", "zz_other_20261015T030000Z.sql.gz"])
    r = run(RESTORE, e, HC_RESTORE_URL=HCR)
    assert r.returncode == 0, r.stderr + r.stdout
    assert curl_urls(tmp) == [f"{HCR}/start", HCR]
    log = calls(tmp)
    assert any(c.startswith("rclone copyto") and "sorento_crm_20261014T030000Z.sql.gz" in c for c in log)
    runs = [c for c in log if c.startswith("docker run")]
    assert len(runs) == 1
    assert "pgvector/pgvector:pg17" in runs[0]
    assert "--network none" in runs[0]
    assert " -p " not in runs[0] and "--publish" not in runs[0]
    name = shlex.split(runs[0])[shlex.split(runs[0]).index("--name") + 1]
    assert name.startswith("restore-test-")
    # every docker call targets only the throwaway container, never the live DB
    for c in docker_calls(tmp):
        assert "proddb" not in c
    assert any(c.startswith("docker rm") and name in c for c in log)
    for t in ["users", "customers", "orders", "products", "audit_logs"]:
        assert t in r.stdout
    assert "2026-10-14 02:59:00" in r.stdout
    assert not (tmp / "restore").exists() or list((tmp / "restore").iterdir()) == []


def test_restore_pg_major_override(env):
    e, tmp = env
    seed_remote(tmp, ["sorento_crm_20261014T030000Z.sql.gz"])
    r = run(RESTORE, e, PG_MAJOR="16")
    assert r.returncode == 0, r.stderr
    assert any("pgvector/pgvector:pg16" in c for c in calls(tmp) if c.startswith("docker run"))


@pytest.mark.parametrize("fault", ["FAKE_PSQL_RESTORE_FAIL", "FAKE_DOCKER_RUN_FAIL"])
def test_restore_failure_pings_fail_and_removes_container(env, fault):
    e, tmp = env
    seed_remote(tmp, ["sorento_crm_20261014T030000Z.sql.gz"])
    r = run(RESTORE, e, HC_RESTORE_URL=HCR, **{fault: "1"})
    assert r.returncode != 0
    assert curl_urls(tmp) == [f"{HCR}/start", f"{HCR}/fail"]
    assert any(c.startswith("docker rm") and "restore-test-" in c for c in calls(tmp))


def test_restore_empty_table_fails(env):
    e, tmp = env
    seed_remote(tmp, ["sorento_crm_20261014T030000Z.sql.gz"])
    r = run(RESTORE, e, HC_RESTORE_URL=HCR, FAKE_EMPTY_TABLES="orders")
    assert r.returncode != 0
    assert curl_urls(tmp)[-1] == f"{HCR}/fail"
    assert any(c.startswith("docker rm") for c in calls(tmp))


def test_restore_no_dump_on_drive_fails(env):
    e, tmp = env
    r = run(RESTORE, e, HC_RESTORE_URL=HCR)
    assert r.returncode != 0
    assert curl_urls(tmp)[-1] == f"{HCR}/fail"
    assert not any(c.startswith("docker run") for c in calls(tmp))


def test_restore_rejects_unsafe_table_name(env):
    e, tmp = env
    seed_remote(tmp, ["sorento_crm_20261014T030000Z.sql.gz"])
    r = run(RESTORE, e, SANITY_TABLES="users; drop table users")
    assert r.returncode == 2
    assert not any(c.startswith("docker run") for c in calls(tmp))


def test_restore_encrypted_dump_needs_identity(env):
    e, tmp = env
    seed_remote(tmp, ["sorento_crm_20261014T030000Z.sql.gz"])
    d = tmp / "remote" / "db-backups"
    (d / "sorento_crm_20261014T030000Z.sql.gz").rename(d / "sorento_crm_20261014T030000Z.sql.gz.age")
    r = run(RESTORE, e)
    assert r.returncode == 2
    assert not any(c.startswith("docker run") for c in calls(tmp))
    ident = tmp / "id.txt"
    ident.write_text("AGE-SECRET-KEY-FAKE\n")
    r = run(RESTORE, e, AGE_IDENTITY=ident)
    assert r.returncode == 0, r.stderr
    assert any(c.startswith("age -d") for c in calls(tmp))


def test_restore_precreates_roles(env):
    e, tmp = env
    seed_remote(tmp, ["sorento_crm_20261014T030000Z.sql.gz"])
    r = run(RESTORE, e, RESTORE_ROLES="crm readonly")
    assert r.returncode == 0, r.stderr
    log = calls(tmp)
    assert any('CREATE ROLE "crm"' in c for c in log)
    assert any('CREATE ROLE "readonly"' in c for c in log)


# ------------------------------------------------------------- review round 1

RUNBOOK = OPS.parents[1] / "documentation" / "runbooks" / "backups.md"


def runbook_env_file(tmp, **overrides):
    """The backup.env heredoc from the runbook, placeholders filled, as the owner would write it."""
    text = RUNBOOK.read_text()
    start = text.index("sudo tee /etc/sorento-backup/backup.env >/dev/null <<'EOF'\n") + len(
        "sudo tee /etc/sorento-backup/backup.env >/dev/null <<'EOF'\n"
    )
    body = text[start : text.index("\nEOF\n", start)]
    fills = {"<DB_CONTAINER>": "proddb", "<DB_USER>": "crm", "<DB_NAME>": "crmdb",
             "<same as the old script>": "30", "<HC_BACKUP_URL>": HC, "<HC_RESTORE_URL>": HCR}
    for k, v in fills.items():
        body = body.replace(k, v)
    for k, v in overrides.items():
        body += f"\n{k}={v}"
    p = tmp / "runbook.env"
    p.write_text(body + "\n")
    p.chmod(0o600)
    return p


def bare(e):
    keep = ("PATH", "HOME", "FAKE_LOG", "FAKE_REMOTE", "FAKE_DUMP_SQL", "BACKUP_DIR", "LOCK_FILE",
            "RESTORE_WORK_DIR", "RESTORE_LOCK_FILE", "RESTORE_READY_TIMEOUT")
    return {k: v for k, v in e.items() if k in keep}


def test_runbook_env_file_works_for_both_scripts(env):
    e, tmp = env
    envf = runbook_env_file(tmp)
    r = run(BACKUP, bare(e), BACKUP_ENV_FILE=envf)
    assert r.returncode == 0, r.stderr + r.stdout
    assert curl_urls(tmp) == [f"{HC}/start", HC]
    r = run(RESTORE, bare(e), BACKUP_ENV_FILE=envf)
    assert r.returncode == 0, r.stderr + r.stdout
    for t in ["users", "customers", "orders", "products", "audit_logs"]:
        assert f"rows {t}=" in r.stdout


def test_env_file_writable_by_others_is_refused(env):
    e, tmp = env
    envf = runbook_env_file(tmp)
    envf.chmod(0o666)
    r = run(BACKUP, bare(e), BACKUP_ENV_FILE=envf)
    assert r.returncode == 2
    assert not any("pg_dump" in c for c in calls(tmp))


def test_restore_empty_sanity_latest_skips_max(env):
    e, tmp = env
    seed_remote(tmp, ["sorento_crm_20261014T030000Z.sql.gz"])
    r = run(RESTORE, e, SANITY_LATEST="")
    assert r.returncode == 0, r.stderr
    assert not any("max(" in c for c in calls(tmp))


def test_restore_waits_on_tcp_not_socket(env):
    e, tmp = env
    seed_remote(tmp, ["sorento_crm_20261014T030000Z.sql.gz"])
    r = run(RESTORE, e)
    assert r.returncode == 0, r.stderr
    ready = [c for c in calls(tmp) if "pg_isready" in c]
    assert ready and all("-h 127.0.0.1" in c for c in ready)


def test_restore_ignores_longer_prefix_job(env):
    e, tmp = env
    seed_remote(tmp, ["sorento_crm_20261014T030000Z.sql.gz", "sorento_crm_v2_20261020T030000Z.sql.gz"])
    r = run(RESTORE, e)
    assert r.returncode == 0, r.stderr
    assert any(c.startswith("rclone copyto") and "sorento_crm_20261014T030000Z" in c for c in calls(tmp))


def test_backup_prune_pattern_is_anchored_to_stamp(env):
    e, tmp = env
    r = run(BACKUP, e)
    assert r.returncode == 0, r.stderr
    d = [c for c in calls(tmp) if c.startswith("rclone delete")][0]
    assert "sorento_crm_[0-9]" in d


def test_restore_refuses_symlinked_work_dir(env):
    e, tmp = env
    seed_remote(tmp, ["sorento_crm_20261014T030000Z.sql.gz"])
    target = tmp / "elsewhere"
    target.mkdir()
    (tmp / "restore").symlink_to(target)
    r = run(RESTORE, e)
    assert r.returncode == 2
    assert not any(c.startswith("rclone copyto") for c in calls(tmp))


def test_restore_removes_leftover_containers_first(env):
    e, tmp = env
    seed_remote(tmp, ["sorento_crm_20261014T030000Z.sql.gz"])
    r = run(RESTORE, e, FAKE_DOCKER_PS="restore-test-sorento_crm-20261007T043000Z")
    assert r.returncode == 0, r.stderr
    log = calls(tmp)
    assert any(c.startswith("docker ps") and "name=restore-test-sorento_crm-" in c for c in log)
    first_run = next(i for i, c in enumerate(log) if c.startswith("docker run"))
    assert any(c.startswith("docker rm") and "restore-test-sorento_crm-20261007T043000Z" in c
               for c in log[:first_run])
