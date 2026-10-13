"""Where the CI jobs run: GitHub-hosted or the self-hosted Mac mini runners.

Owner, 14 Oct 2026: GitHub Actions minutes are nearly spent, so the test jobs
may run on self-hosted Linux runners on the Mac mini (label `sorento-mac`),
and crew juggles between those and GitHub-hosted without a code change:

1. Two repo variables pick the place per job group. `RUNNER_TESTS` (the PR
   gate, the backend and SCM shards, vitest, type-check): `github` (also
   unset), `mac` or `auto`. `RUNNER_FAST` (the root and gate jobs: alembic
   heads, PII guard, Changed areas, ci-full refusal, CI gate): `github` or
   `mac`. Anything unknown lands on GitHub-hosted.
2. A `runner` input on workflow_dispatch overrides both for that run:
   `vars` (follow the variables, the default), `auto`, `mac`, `github`.
3. `auto` is decided inside `Changed areas` (no extra billed job): the Mac
   when a `sorento-mac` runner is online and idle, else GitHub-hosted. It
   reads the runners API with the `RUNNERS_READ_TOKEN` secret (fine-grained
   PAT, this repo, Administration: read); no secret, or a failed call, means
   GitHub-hosted, so `auto` can never strand a run.
4. build-images and build-and-deploy always run on GitHub-hosted: the prod
   images are amd64 and the Mac runners are arm64.

The picker step is extracted from the workflow and executed with a stub `gh`,
as in test_ci_docs_only_filter.py, so nothing here can drift from the file.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
MAC = "sorento-mac"
GH = "ubuntu-latest"
PICK_STEP = "Pick the test runner"

TEST_JOBS = ["lean-gate", "test-backend", "test-backend-scm", "validate-frontend", "typecheck-frontend"]
FAST_JOBS = ["check-migration-heads", "pii-guard", "changes", "refuse-ci-full", "ci-gate"]
GITHUB_ONLY_JOBS = ["build-images", "build-and-deploy", "validate-backend", "validate-mcp"]

pytestmark = pytest.mark.skipif(not WORKFLOW.is_file(), reason="workflow not present (backend-only checkout)")


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _jobs() -> dict:
    return _workflow()["jobs"]


def _pick_script() -> str:
    steps = _jobs()["changes"]["steps"]
    step = next(s for s in steps if s.get("name") == PICK_STEP)
    return textwrap.dedent(step["run"])


GH_STUB = """\
#!/usr/bin/env bash
if [ "${GH_STUB_FAIL:-0}" = "1" ]; then echo "stub gh: failing on purpose" >&2; exit 1; fi
expr=""
while [ $# -gt 0 ]; do
  case "$1" in
    --jq) expr="$2"; shift ;;
  esac
  shift
done
exec jq -r "$expr" "$GH_STUB_FILE"
"""


def _runner(name: str, status: str, busy: bool, labels=("self-hosted", "Linux", "ARM64", MAC)) -> dict:
    return {"name": name, "status": status, "busy": busy, "labels": [{"name": n} for n in labels]}


def pick(tmp_path: Path, *, override: str = "", setting: str = "", token: str = "", runners=None, gh_fails=False):
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed (the CI runner has it)")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "gh").write_text(GH_STUB)
    (bindir / "gh").chmod(0o755)
    payload = tmp_path / "runners.json"
    payload.write_text(json.dumps({"total_count": len(runners or []), "runners": runners or []}))
    output = tmp_path / "output.txt"
    output.write_text("")
    env = {
        "PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "GITHUB_OUTPUT": str(output),
        "REPO": "jayson-odoo/sorento-crm",
        "OVERRIDE": override,
        "SETTING": setting,
        "GH_TOKEN": token,
        "GH_STUB_FILE": str(payload),
        "GH_STUB_FAIL": "1" if gh_fails else "0",
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _pick_script()],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    assert set(out) == {"runner"}, out
    return out["runner"]


# --- the picker ------------------------------------------------------------

@pytest.mark.parametrize(
    "override,setting,expected",
    [
        ("", "", GH),
        ("vars", "", GH),
        ("", "github", GH),
        ("", "mac", MAC),
        ("vars", "mac", MAC),
        ("mac", "github", MAC),
        ("github", "mac", GH),
        ("", "something-else", GH),
        ("", "self-hosted", GH),
    ],
)
def test_override_beats_the_variable_and_unknown_lands_on_github(tmp_path, override, setting, expected):
    assert pick(tmp_path, override=override, setting=setting) == expected


def test_auto_without_the_token_secret_stays_on_github(tmp_path):
    idle = [_runner("mac-1", "online", False)]
    assert pick(tmp_path, setting="auto", token="", runners=idle) == GH
    assert pick(tmp_path, override="auto", token="", runners=idle) == GH


def test_auto_takes_the_mac_when_a_mac_runner_is_online_and_idle(tmp_path):
    runners = [_runner("mac-1", "online", True), _runner("mac-2", "online", False)]
    assert pick(tmp_path, setting="auto", token="t", runners=runners) == MAC


def test_auto_overflows_to_github_when_every_mac_runner_is_busy(tmp_path):
    runners = [_runner("mac-1", "online", True), _runner("mac-2", "online", True)]
    assert pick(tmp_path, setting="auto", token="t", runners=runners) == GH


def test_auto_falls_back_to_github_when_the_mac_is_offline(tmp_path):
    runners = [_runner("mac-1", "offline", False), _runner("mac-2", "offline", False)]
    assert pick(tmp_path, setting="auto", token="t", runners=runners) == GH


def test_auto_ignores_idle_runners_without_the_mac_label(tmp_path):
    runners = [_runner("other", "online", False, labels=("self-hosted", "Linux", "X64"))]
    assert pick(tmp_path, setting="auto", token="t", runners=runners) == GH


def test_auto_falls_back_to_github_when_the_runners_api_fails(tmp_path):
    assert pick(tmp_path, setting="auto", token="t", gh_fails=True) == GH


def test_the_dispatch_override_beats_auto_in_the_variable(tmp_path):
    idle = [_runner("mac-1", "online", False)]
    assert pick(tmp_path, override="github", setting="auto", token="t", runners=idle) == GH


# --- the workflow wiring -----------------------------------------------------

def test_dispatch_has_a_runner_choice_defaulting_to_the_variables():
    inputs = _workflow()[True]["workflow_dispatch"]["inputs"]
    runner = inputs["runner"]
    assert runner["type"] == "choice" and runner["default"] == "vars", runner
    assert runner["options"] == ["vars", "auto", "mac", "github"], runner


def test_the_picker_is_the_first_step_of_changed_areas_and_feeds_its_output():
    changes = _jobs()["changes"]
    assert changes["steps"][0]["name"] == PICK_STEP
    step = changes["steps"][0]
    assert step["id"] == "runner"
    env = step["env"]
    assert env["OVERRIDE"] == "${{ inputs.runner }}"
    assert env["SETTING"] == "${{ vars.RUNNER_TESTS }}"
    # The token is only this step's, never the job's or the workflow's.
    assert env["GH_TOKEN"] == "${{ secrets.RUNNERS_READ_TOKEN }}"
    assert "RUNNERS_READ_TOKEN" not in json.dumps(changes.get("env", {}))
    assert "RUNNERS_READ_TOKEN" not in json.dumps(_workflow().get("env", {}))
    assert changes["outputs"]["tests_runner"] == "${{ steps.runner.outputs.runner }}"


@pytest.mark.parametrize("job", TEST_JOBS)
def test_test_jobs_run_where_the_picker_says(job):
    jobs = _jobs()
    assert "changes" in jobs[job]["needs"], job
    assert jobs[job]["runs-on"] == "${{ needs.changes.outputs.tests_runner || 'ubuntu-latest' }}", job


FAST_EXPR = (
    "${{ (inputs.runner == 'mac' || (inputs.runner != 'github' && vars.RUNNER_FAST == 'mac'))"
    " && 'sorento-mac' || 'ubuntu-latest' }}"
)


@pytest.mark.parametrize("job", FAST_JOBS)
def test_fast_jobs_follow_runner_fast_and_the_dispatch_override(job):
    assert _jobs()[job]["runs-on"] == FAST_EXPR, job


@pytest.mark.parametrize("job", GITHUB_ONLY_JOBS)
def test_image_and_deploy_jobs_stay_on_github_hosted(job):
    assert _jobs()[job]["runs-on"] == GH, job


def test_every_job_is_routed_on_purpose():
    assert set(_jobs()) == set(TEST_JOBS) | set(FAST_JOBS) | set(GITHUB_ONLY_JOBS)


def test_the_gitleaks_cli_matches_the_runner_architecture():
    script = (ROOT / "scripts" / "ci_gitleaks.sh").read_text(encoding="utf-8")
    assert "uname -m" in script
    assert "linux_arm64" in script and "linux_x64" in script
