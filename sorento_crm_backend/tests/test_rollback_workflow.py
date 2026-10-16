"""HA-MEM-DEPLOY-GUARD: `.github/workflows/rollback.yml` redeploys an earlier main commit.

It reuses the release job's pieces (Docker Hub login, release-tag promotion, the scp of
`blue_green_deploy.sh`, the SSH run, the public endpoint check) behind the same
`production` environment approval and the same `deploy-production` queue, with no secret the
release job does not already use. The SHA input is validated (40 hex chars, an ancestor of
main) and only the validated value reaches a shell or the SSH script.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
ROLLBACK = REPO / ".github" / "workflows" / "rollback.yml"
DEPLOY = REPO / ".github" / "workflows" / "deploy.yml"


def _load(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    # PyYAML reads the bare key `on` as boolean True.
    if True in doc:
        doc["on"] = doc.pop(True)
    return doc


@pytest.fixture(scope="module")
def rollback():
    return _load(ROLLBACK)


@pytest.fixture(scope="module")
def job(rollback):
    jobs = rollback["jobs"]
    assert len(jobs) == 1, list(jobs)
    return next(iter(jobs.values()))


def _steps(job_def) -> dict:
    return {s.get("name", ""): s for s in job_def["steps"]}


def test_dispatch_only_with_a_required_sha_input(rollback):
    on = rollback["on"]
    assert set(on) == {"workflow_dispatch"}, on
    sha = on["workflow_dispatch"]["inputs"]["sha"]
    assert sha["required"] is True


def test_behind_the_production_approval_and_the_deploy_queue(job):
    assert job["environment"]["name"] == "production"
    assert job["concurrency"] == {"group": "deploy-production", "cancel-in-progress": False}
    assert "refs/heads/main" in job["if"]


def test_uses_no_secret_the_release_job_does_not(rollback):
    used = set(re.findall(r"secrets\.([A-Z0-9_]+)", ROLLBACK.read_text()))
    known = set(re.findall(r"secrets\.([A-Z0-9_]+)", DEPLOY.read_text()))
    assert used, "the rollback needs the SSH and registry secrets"
    assert used <= known, f"new secrets: {used - known}"


def test_raw_input_never_reaches_a_shell(job):
    for step in job["steps"]:
        for field in ("run",):
            body = step.get(field) or ""
            assert "inputs.sha" not in body and "github.event.inputs" not in body, step.get("name")
        script = (step.get("with") or {}).get("script") or ""
        assert "inputs.sha" not in script and "github.event.inputs" not in script, step.get("name")


def test_the_sha_is_validated_as_a_main_commit(job):
    steps = _steps(job)
    validate = next(s for n, s in steps.items() if "alidate" in n)
    assert "inputs.sha" in str(validate.get("env")), "the input arrives via env, not interpolation"
    assert '[[ "$SHA" =~ ^[0-9a-f]{40}$ ]]' in validate["run"], "whole-value 40-hex check"
    assert "merge-base --is-ancestor" in validate["run"]
    assert "deploy.yml/runs?head_sha=$SHA&branch=main&event=workflow_dispatch&status=success" in validate["run"], (
        "only a commit whose release succeeded qualifies"
    )
    checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0, "ancestry needs main's history"


def test_checks_every_image_exists_before_touching_the_server(job):
    names = [s.get("name", "") for s in job["steps"]]
    check = next(i for i, n in enumerate(names) if "images exist" in n)
    scp = next(i for i, n in enumerate(names) if "Sync deploy script" in n)
    assert check < scp
    assert "imagetools inspect" in job["steps"][check]["run"]


def test_runs_the_same_script_with_the_old_sha_and_skips_migrations(job):
    steps = _steps(job)
    scp = steps["Sync deploy script to server"]
    assert scp["with"]["source"] == "scripts/blue_green_deploy.sh"
    assert scp["with"]["target"] == "/opt/sorento-crm2"
    script = steps["Roll back on the server (blue/green)"]["with"]["script"]
    assert "./scripts/blue_green_deploy.sh" in script
    assert 'IMAGE_TAG="${{ steps.target.outputs.sha }}"' in script
    assert "SKIP_MIGRATIONS=1" in script


def test_endpoint_check_is_the_release_jobs_own(job):
    deploy_job = _load(DEPLOY)["jobs"]["build-and-deploy"]
    want = _steps(deploy_job)["Verify public endpoints"]["with"]["script"]
    got = _steps(job)["Verify public endpoints"]["with"]["script"]
    assert got == want, "keep the two checks identical; edit both together"


def test_the_ssh_budget_matches_the_release_step(job):
    deploy_job = _load(DEPLOY)["jobs"]["build-and-deploy"]
    want = _steps(deploy_job)["Deploy to Server (blue/green)"]["with"]["command_timeout"]
    got = _steps(job)["Roll back on the server (blue/green)"]["with"]["command_timeout"]
    assert got == want == "100m", "worker grace 60m + lock wait 10m + dump and the rest"


def test_the_release_tag_moves_only_after_the_server_step_succeeded(job):
    """A failed rollback must leave the moving `<svc>-<RELEASE_TAG>` tag on what is still
    running, or a manual `docker compose up` would pull the images that failed."""
    names = [s.get("name", "") for s in job["steps"]]
    server = names.index("Roll back on the server (blue/green)")
    promote = next(i for i, n in enumerate(names) if n.startswith("Promote"))
    assert promote > server, names
    cond = str(job["steps"][promote].get("if", "success()"))
    assert "always()" not in cond and "failure()" not in cond, cond
