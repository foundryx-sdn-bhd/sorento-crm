"""The gating logic in .github/workflows/deploy.yml, executed as it ships.

Three rules live in that workflow and are pinned here:

1. Docs-only (job `changes`, step "Detect changed areas"): a change whose every
   path is documentation skips image build, every test suite and the deploy
   (owner ruling 29 Sep 2026). `DOCS_RE` names what counts as documentation,
   `NOT_DOCS_RE` names the documentation/ paths that tests READ as live
   inputs, both ends of a rename count, and `SCM_RE` decides whether the SCM
   shards run on a PR.
2. Release by dispatch (the jobs' `if` lines): a push to main runs the alembic
   gate and nothing else; a `workflow_dispatch` on main runs the full suite
   (or, with `skip_tests`, nothing) then build-images and build-and-deploy,
   which carries `environment: production` so the owner approves each deploy.
3. Cheap gate + merge gate (the jobs' `if` lines and the workflow
   `concurrency`): a `ci`-labelled PR runs the fast gates, the changed
   tests and the SCM shards only when SCM paths changed, never the six
   backend shards; a merge-queue entry and a release run the full suite;
   a `ci-full` label on a PR is refused (owner, 9 Oct 2026: no full suite
   per PR) and there is no nightly (owner, 10 Oct 2026, CI-LEAN); only a
   `ci` label run joins the per-PR concurrency group; every test job carries a
   timeout; the `CI gate` job reports the run as one check (CI-MINUTES-CUT).
4. Changed tests on a PR (`backend_tests` output of `changes`, and job
   `lean-gate` (the `PR gate` check) with its "Select the changed test files to run"
   step): the backend test files a `ci`-labelled PR changes run on the PR
   itself, serially, minus tests/scm (the SCM shards' job), minus what the
   checkout no longer holds and minus tests/ci_excluded.txt. #1387 changed
   one test file and its `ci` run executed no test at all (CI-TESTS-ONLY).

The shell steps are extracted from the workflow file and executed under the
same bash flags GitHub uses, with a stub `gh` that answers every API call from
a canned payload through the real `jq` filter the step passes. The `if`
expressions are evaluated as written with a small evaluator for the operators
they use. So nothing here is a copy that can drift from the workflow: the test
scans every backend and MCP test for the documentation/ paths test code reads
and fails when one would classify as docs (extend NOT_DOCS_RE).

Needs `bash` and `jq`, which the CI runner has; skipped when the workflow file
is not present, which is the case inside the backend Docker image
(validate-backend's `pytest --collect-only` and its regression gate run there,
and the image holds only sorento_crm_backend/).
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "deploy.yml"
TEST_TREES = [REPO / "sorento_crm_backend" / "tests", REPO / "sorento_crm_mcp" / "tests"]
ZERO_SHA = "0" * 40
SHA = "b" * 40
BEFORE = "a" * 40

# Stub `gh api`: answers from $GH_STUB_DIR/<key>.json, where <key> is the
# endpoint after `repos/<owner>/<repo>/` with every `/ ? & =` replaced by `_`,
# through the --jq filter the caller passed, exactly as gh would. A missing
# fixture is a failed call (as a 404 would be). --paginate is accepted and
# ignored (each fixture is one page).
GH_STUB = """\
#!/usr/bin/env bash
if [ "${GH_STUB_FAIL:-0}" = "1" ]; then echo "stub gh: failing on purpose" >&2; exit 1; fi
endpoint=""; expr=""
while [ $# -gt 0 ]; do
  case "$1" in
    api) ;;
    --jq) expr="$2"; shift ;;
    --paginate) ;;
    *) [ -z "$endpoint" ] && endpoint="$1" ;;
  esac
  shift
done
[ -n "$expr" ] || { echo "stub gh: no --jq filter given" >&2; exit 2; }
key=$(printf '%s' "$endpoint" | sed -E 's#^repos/[^/]+/[^/]+/##; s#[/?&=]#_#g')
file="$GH_STUB_DIR/$key.json"
[ -f "$file" ] || { echo "stub gh: no fixture for $endpoint ($key)" >&2; exit 1; }
exec jq -r "$expr" "$file"
"""


def _fixture_key(endpoint: str) -> str:
    return re.sub(r"[/?&=]", "_", re.sub(r"^repos/[^/]+/[^/]+/", "", endpoint))


def _workflow_text() -> str:
    if not WORKFLOW.exists():
        pytest.skip(f"{WORKFLOW} not present (in-image run)")
    return WORKFLOW.read_text(encoding="utf-8")


def _step_script(step_name: str) -> str:
    """The `run` block of the named step, dedented."""
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed (the CI runner has it)")
    lines = _workflow_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {step_name}")
    run_at = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run_at]) - len(lines[run_at].lstrip()) + 2
    body: list[str] = []
    for line in lines[run_at + 1:]:
        if line.strip() and (len(line) - len(line.lstrip())) < indent:
            break
        body.append(line)
    return textwrap.dedent("\n".join(body))


def _run_shell(tmp_path: Path, script: str, env: dict, fixtures: dict, *, gh_fails: bool = False) -> dict[str, str]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    (stub_dir / "gh").write_text(GH_STUB)
    (stub_dir / "gh").chmod(0o755)
    fixture_dir = tmp_path / "api"
    fixture_dir.mkdir(exist_ok=True)
    for endpoint, payload in fixtures.items():
        (fixture_dir / f"{_fixture_key(endpoint)}.json").write_text(json.dumps(payload))
    output = tmp_path / "output.txt"
    output.write_text("")
    full_env = {
        "PATH": f"{stub_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_REPOSITORY": "jayson-odoo/sorento-crm",
        "GH_TOKEN": "stub",
        "GH_STUB_DIR": str(fixture_dir),
        "GH_STUB_FAIL": "1" if gh_fails else "0",
        **env,
    }
    # The same invocation GitHub uses for a `run:` step on ubuntu-latest.
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env=full_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"step failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)


# ---------------------------------------------------------------------------
# Step "Detect changed areas"
# ---------------------------------------------------------------------------

AREA_KEYS = {"docs_only", "backend", "frontend", "mcp", "scm", "backend_tests", "backend_code"}
# Written by a lean release only (CI-LEAN): the diff base and the lean flag.
LEAN_KEYS = {"lean", "base_sha"}
# Written on a PR whose merge tree already passed a PR gate (CI-COST round 2).
PR_KEYS = {"validated"}
LAST_RELEASE = "e" * 40
TREE = "f" * 40
RELEASE_RUNS = "repos/jayson-odoo/sorento-crm/actions/workflows/deploy.yml/runs?branch=main&event=workflow_dispatch&status=success&per_page=1"


def run_step(
    tmp_path: Path,
    event: str,
    files: list,
    *,
    gh_fails: bool = False,
    before: str = BEFORE,
    skip_tests: str = "",
    label: str = "ci",
    full_suite: str = "",
    last_release: str | None = None,
    validated: bool = False,
    pr_validated: bool = False,
) -> dict[str, str]:
    """Run the real step with `files` as the API answer; return $GITHUB_OUTPUT.

    Each entry of `files` is a path, or a (new_path, previous_path) pair for a
    rename, shaped into the PR-files array or the compare object exactly as the
    GitHub API returns them. `label` is the label just added on a
    pull_request event. On a dispatch, `last_release` is the head of the
    last successful release (None: the lookup finds none) and `files` is the
    compare from it to the head; `validated` says a validated-tree artifact
    exists for the head's tree.
    """
    entries = []
    for f in files:
        if isinstance(f, tuple):
            entries.append({"filename": f[0], "previous_filename": f[1], "status": "renamed"})
        else:
            entries.append({"filename": f, "status": "modified"})
    fixtures = {
        "repos/jayson-odoo/sorento-crm/pulls/1/files": entries,
        f"repos/jayson-odoo/sorento-crm/compare/{before}...{SHA}": {"files": entries},
        f"repos/jayson-odoo/sorento-crm/compare/{'c' * 40}...{'d' * 40}": {"files": entries},
        RELEASE_RUNS: {"workflow_runs": [{"head_sha": last_release}] if last_release else []},
        f"repos/jayson-odoo/sorento-crm/git/commits/{SHA}": {"sha": SHA, "tree": {"sha": TREE}},
        f"repos/jayson-odoo/sorento-crm/actions/artifacts?name=validated-tree-{TREE}&per_page=5": {
            "artifacts": [{"name": f"validated-tree-{TREE}", "expired": False}] if validated else []
        },
        f"repos/jayson-odoo/sorento-crm/actions/artifacts?name=pr-validated-tree-{TREE}&per_page=5": {
            "artifacts": [{"name": f"pr-validated-tree-{TREE}", "expired": False}] if pr_validated else []
        },
    }
    if last_release:
        fixtures[f"repos/jayson-odoo/sorento-crm/compare/{last_release}...{SHA}"] = {"files": entries}
    env = {
        "EVENT": event, "LABEL": label if event == "pull_request" else "", "PR": "1",
        "PUSH_BEFORE": before, "PUSH_AFTER": SHA,
        "MG_BASE": "c" * 40, "MG_HEAD": "d" * 40, "SKIP_TESTS": skip_tests, "FULL_SUITE": full_suite,
    }
    outputs = _run_shell(tmp_path, _step_script("Detect changed areas"), env, fixtures, gh_fails=gh_fails)
    assert AREA_KEYS <= set(outputs) <= AREA_KEYS | LEAN_KEYS | PR_KEYS, outputs
    return outputs


DOCS_ONLY = [
    "documentation/plans/ci/PLAN-ci-docs-skip-29sep.md",
    "documentation/plans/_archive/scm/evidence/AC-19b-filters-panel.png",
    "documentation/plans/scm/mockups/so-change-management-grill-v4.html",
    "documentation/user-guides/README.md",
    "documentation/adr/0001-example.md",
    "CLAUDE.md",
    "PRINCIPLES.md",
    "LESSONS-LEARNT.md",
    "README.md",
    ".claude/agents/coder.md",
    ".claude/skills/feature/SKILL.md",
    ".claude/evidence/shared-brand-S2/01-folder-context-menu-1280.png",
    ".cursor/rules/frontend.mdc",
    ".github/PULL_REQUEST_TEMPLATE.md",
]

# `backend_tests` is empty in all three: off a PR the full shards run every
# file, and a docs-only or skip_tests run tests nothing.
FULL_PIPELINE = {"docs_only": "false", "backend": "true", "frontend": "true", "mcp": "true", "scm": "true", "backend_tests": "", "backend_code": ""}
DOCS_SKIP = {"docs_only": "true", "backend": "false", "frontend": "false", "mcp": "false", "scm": "false", "backend_tests": "", "backend_code": ""}
NO_AREA = {"backend": "false", "frontend": "false", "mcp": "false", "scm": "false", "backend_tests": "", "backend_code": ""}


@pytest.mark.parametrize("event", ["pull_request", "push", "merge_group"])
def test_docs_only_change_skips_everything(tmp_path, event):
    assert run_step(tmp_path, event, DOCS_ONLY) == DOCS_SKIP


def _triggers() -> str:
    text = _workflow_text()
    return text[text.index("\non:\n"):text.index("\nconcurrency:\n")]


def test_there_is_no_nightly(tmp_path):
    """CI-LEAN, owner 10 Oct 2026: "i don't need the nightly test". No
    schedule trigger, and the `changes` step carries no schedule branch."""
    assert not re.search(r"^  schedule:", _triggers(), re.M)
    assert "event=schedule" not in _step_script("Detect changed areas")
    assert "nightly_done" not in _step_script("Detect changed areas")


def test_ci_full_is_never_a_label_the_area_step_honours(tmp_path):
    """No full suite per PR (owner, 9 Oct 2026): the step has no ci-full branch,
    so a stray ci-full label reads like any other PR (the job never runs then:
    see test_ci_full_label_is_refused_on_a_pull_request)."""
    assert "ci-full" not in _step_script("Detect changed areas")
    out = run_step(tmp_path, "pull_request", ["sorento_crm_frontend/app/page.tsx"], label="ci-full")
    assert out["backend"] == "false" and out["frontend"] == "true"


NON_DOCS = [
    # code in each service tree
    "sorento_crm_backend/app/main.py",
    "sorento_crm_frontend/app/(protected)/page.tsx",
    "sorento_crm_mcp/sorento_crm_mcp/server.py",
    # migration
    "sorento_crm_backend/alembic/versions/900_docs_skip_guard.py",
    # Dockerfiles and compose
    "sorento_crm_backend/Dockerfile",
    "sorento_crm_frontend/Dockerfile",
    "sorento_crm_mcp/Dockerfile",
    "sorento_crm/docker-compose.yml",
    # the workflow itself, and the other workflow
    ".github/workflows/deploy.yml",
    ".github/workflows/outline-user-guides-sync.yml",
    # config, lockfiles, scripts
    "sorento_crm_frontend/package-lock.json",
    "sorento_crm_frontend/next.config.mjs",
    "sorento_crm_backend/requirements.txt",
    "sorento_crm_backend/tests/ci_excluded.txt",
    "sorento_crm_backend/.test_durations",
    "scripts/blue_green_deploy.sh",
    "scripts/git-hooks/pre-push",
    ".gitignore",
    "pyrightconfig.json",
    # markdown INSIDE a service tree is a fixture or an image input, never docs
    "sorento_crm_backend/tests/chatbot/replay_turns/turn_01.md",
    "sorento_crm_frontend/e2e/fixtures/project-cs/notes.md",
    "sorento_crm_mcp/README.md",
    "sorento_crm_backend/README.md",
    "sorento_crm_frontend/docs/README.md",
    "scripts/git-hooks/README.md",
    # a root-level file that is not markdown
    "e2e-stack.sh",
    "brands-grid.yml",
    # nested folders: only the FIRST path segment is docs
    "sorento_crm_frontend/documentation/x.md",
    "sorento_crm_backend/.claude/agents/x.md",
    "sorento_crm_mcp/CLAUDE.md",
    # documentation/ paths that tests read as live inputs (crew review of #1370)
    "documentation/reference/ADR-PRODUCT-STANDARDS.md",
    "documentation/plans/scm/fixtures/FSCU8103365.xlsx",
    "documentation/plans/scm/fixtures/Jinbaichuan_Invoice.xlsx",
    "documentation/plans/chatbot/samples/top-selling-01.json",
    "documentation/plans/dealer-kit/seed-assets/pdf-geometry.json",
    "documentation/plans/_archive/scm/fixtures/moved-with-the-plan.xlsx",
    "documentation/plans/autocount/PLAN-autocount-cross-repo-contract.md",
    "documentation/plans/_archive/scm/PLAN-scm-fulfilment-feedback-p4.md",
]


@pytest.mark.parametrize("path", NON_DOCS)
def test_one_non_docs_path_keeps_the_full_pipeline(tmp_path, path):
    """One non-docs path in an otherwise docs-only push keeps the full pipeline."""
    assert run_step(tmp_path, "push", DOCS_ONLY + [path]) == FULL_PIPELINE


def test_pull_request_area_flags_still_follow_the_service_trees(tmp_path):
    out = run_step(tmp_path, "pull_request", DOCS_ONLY + ["sorento_crm_backend/app/main.py"])
    assert out == {
        "docs_only": "false", "backend": "true", "frontend": "false", "mcp": "false", "scm": "false",
        "backend_tests": "", "backend_code": "sorento_crm_backend/app/main.py",
    }
    out = run_step(tmp_path, "pull_request", ["sorento_crm_frontend/app/page.tsx"])
    assert out == {"docs_only": "false", "backend": "false", "frontend": "true", "mcp": "false", "scm": "false", "backend_tests": "", "backend_code": ""}
    out = run_step(tmp_path, "pull_request", ["sorento_crm_mcp/pyproject.toml"])
    assert out == {"docs_only": "false", "backend": "false", "frontend": "false", "mcp": "true", "scm": "false", "backend_tests": "", "backend_code": ""}


SCM_PATHS = [
    "sorento_crm_backend/app/api/v1/scm/orders.py",
    "sorento_crm_backend/app/services/scm/sales_order_service.py",
    "sorento_crm_backend/app/modules/scm/bootstrap.py",
    "sorento_crm_backend/app/models/scm.py",
    "sorento_crm_backend/app/schemas/scm_orders.py",
    "sorento_crm_backend/tests/scm/test_committed_v.py",
    "sorento_crm_backend/alembic/versions/273_scm_schema.py",
    # the harness every backend test depends on
    "sorento_crm_backend/tests/conftest.py",
    "sorento_crm_backend/tests/_pg_fixture.py",
    "sorento_crm_backend/tests/ci_excluded.txt",
    "sorento_crm_backend/.test_durations",
    "sorento_crm_backend/scripts/bootstrap_env.py",
    "sorento_crm_backend/requirements.txt",
    ".github/workflows/deploy.yml",
]


@pytest.mark.parametrize("path", SCM_PATHS)
def test_scm_path_on_a_pull_request_runs_the_scm_shards(tmp_path, path):
    out = run_step(tmp_path, "pull_request", [path])
    assert out["scm"] == "true", out
    assert out["backend"] == "true", out


@pytest.mark.parametrize(
    "path",
    [
        "sorento_crm_backend/app/services/procurement_service.py",
        "sorento_crm_backend/app/api/v1/master_data/products.py",
        "sorento_crm_backend/tests/test_rbac.py",
        "sorento_crm_backend/app/models/product.py",
        "sorento_crm_backend/alembic/versions/900_products_x.py",
        "sorento_crm_backend/app/schemas/user.py",
        "sorento_crm_frontend/app/page.tsx",
    ],
)
def test_non_scm_backend_path_on_a_pull_request_skips_the_scm_shards(tmp_path, path):
    out = run_step(tmp_path, "pull_request", [path])
    assert out["scm"] == "false", out


@pytest.mark.parametrize("event", ["push", "merge_group"])
def test_scm_follows_backend_off_a_pull_request(tmp_path, event):
    """Off a PR the SCM shards run for any code change (the queue; a push's flags are unused)."""
    out = run_step(tmp_path, event, ["sorento_crm_frontend/app/page.tsx"])
    assert out == FULL_PIPELINE


@pytest.mark.parametrize("event", ["push", "merge_group"])
def test_rename_out_of_a_service_tree_is_not_docs(tmp_path, event):
    """A rename reports the docs path as `filename`; the old path must count too."""
    moved_out = [("documentation/plans/scm/old-notes.md", "sorento_crm_backend/docs/old-notes.md")]
    assert run_step(tmp_path, event, moved_out) == FULL_PIPELINE
    moved_in = [("sorento_crm_backend/tests/fixtures/x.md", "documentation/plans/scm/x.md")]
    assert run_step(tmp_path, event, moved_in) == FULL_PIPELINE
    fixture_moved = [("documentation/plans/scm/notes/x.xlsx", "documentation/plans/scm/fixtures/x.xlsx")]
    assert run_step(tmp_path, event, fixture_moved) == FULL_PIPELINE


def test_rename_on_a_pull_request_flags_the_tree_it_left(tmp_path):
    """On a PR the area flags follow the trees; the old path of a rename counts."""
    moved_out = [("documentation/plans/scm/old-notes.md", "sorento_crm_backend/docs/old-notes.md")]
    assert run_step(tmp_path, "pull_request", moved_out) == {
        "docs_only": "false", "backend": "true", "frontend": "false", "mcp": "false", "scm": "false", "backend_tests": "", "backend_code": ""
    }
    moved_in = [("sorento_crm_mcp/tests/fixtures/x.md", "documentation/plans/scm/x.md")]
    assert run_step(tmp_path, "pull_request", moved_in) == {
        "docs_only": "false", "backend": "false", "frontend": "false", "mcp": "true", "scm": "false", "backend_tests": "", "backend_code": ""
    }
    fixture_moved = [("documentation/plans/scm/notes/x.xlsx", "documentation/plans/scm/fixtures/x.xlsx")]
    assert run_step(tmp_path, "pull_request", fixture_moved)["docs_only"] == "false"


@pytest.mark.parametrize("event", ["pull_request", "push", "merge_group"])
def test_rename_within_documentation_is_docs(tmp_path, event):
    archived = [
        ("documentation/plans/_archive/scm/PLAN-x.md", "documentation/plans/scm/PLAN-x.md"),
        ("documentation/plans/_archive/scm/x-acceptance-criteria.md", "documentation/plans/scm/x-acceptance-criteria.md"),
    ]
    assert run_step(tmp_path, event, archived) == DOCS_SKIP


def test_fail_safes_land_on_the_full_pipeline(tmp_path):
    assert run_step(tmp_path, "push", DOCS_ONLY, before=ZERO_SHA) == FULL_PIPELINE
    assert run_step(tmp_path, "push", DOCS_ONLY, gh_fails=True) == FULL_PIPELINE
    assert run_step(tmp_path, "merge_group", DOCS_ONLY, gh_fails=True) == FULL_PIPELINE
    assert run_step(tmp_path, "pull_request", DOCS_ONLY, gh_fails=True) == FULL_PIPELINE
    assert run_step(tmp_path, "push", []) == FULL_PIPELINE
    assert run_step(tmp_path, "workflow_dispatch", DOCS_ONLY) == FULL_PIPELINE
    many = [f"documentation/plans/x/{i}.md" for i in range(300)]
    assert run_step(tmp_path, "push", many) == FULL_PIPELINE, "300 files is the compare cap"
    assert run_step(tmp_path, "push", many[:299]) == DOCS_SKIP


def test_release_with_no_release_to_diff_against_tests_the_whole_head(tmp_path):
    """No successful release found (or full_suite): every area counts, the full
    sharded suite runs, docs_only is never true."""
    assert run_step(tmp_path, "workflow_dispatch", DOCS_ONLY) == FULL_PIPELINE
    assert run_step(tmp_path, "workflow_dispatch", [], skip_tests="false") == FULL_PIPELINE
    assert run_step(tmp_path, "workflow_dispatch", DOCS_ONLY, last_release=LAST_RELEASE, full_suite="true") == FULL_PIPELINE
    assert run_step(tmp_path, "workflow_dispatch", DOCS_ONLY, last_release=LAST_RELEASE, gh_fails=True) == FULL_PIPELINE


RELEASE_BATCH = [
    "sorento_crm_backend/app/services/inventory_service.py",
    "sorento_crm_backend/tests/test_rbac.py",
    "sorento_crm_backend/tests/scm/test_committed_v.py",
    "documentation/plans/ci/PLAN-ci-lean-10oct.md",
]


def test_a_release_diffs_against_the_last_successful_release(tmp_path):
    """CI-LEAN: the lean release tests what the merged batch reaches. Backend
    always counts (the core tier runs on every release); the frontend and MCP
    flags follow the batch; tests/scm is listed like any other test."""
    out = run_step(tmp_path, "workflow_dispatch", RELEASE_BATCH, last_release=LAST_RELEASE)
    assert out == {
        "docs_only": "false", "backend": "true", "frontend": "false", "mcp": "false", "scm": "true",
        "backend_tests": "sorento_crm_backend/tests/scm/test_committed_v.py sorento_crm_backend/tests/test_rbac.py",
        "backend_code": "sorento_crm_backend/app/services/inventory_service.py",
        "lean": "true", "base_sha": LAST_RELEASE,
    }
    fe = run_step(tmp_path, "workflow_dispatch", ["sorento_crm_frontend/app/page.tsx"], last_release=LAST_RELEASE)
    assert fe["frontend"] == "true" and fe["backend"] == "true" and fe["lean"] == "true"


def test_a_docs_only_release_still_runs_the_core_tier(tmp_path):
    out = run_step(tmp_path, "workflow_dispatch", DOCS_ONLY, last_release=LAST_RELEASE)
    assert out["docs_only"] == "false" and out["backend"] == "true" and out["lean"] == "true"
    assert out["backend_tests"] == "" and out["frontend"] == "false"


def test_a_release_batch_at_the_compare_cap_runs_the_full_suite(tmp_path):
    many = [f"sorento_crm_backend/app/x{i}.py" for i in range(300)]
    assert run_step(tmp_path, "workflow_dispatch", many, last_release=LAST_RELEASE) == FULL_PIPELINE


@pytest.mark.parametrize("harness", ["sorento_crm_backend/tests/conftest.py", "sorento_crm_backend/tests/_pg_fixture.py"])
def test_a_release_batch_touching_the_test_harness_runs_the_full_suite(tmp_path, harness):
    """CI-COST owner ruling (11 Oct 2026, crew decision (b)): a harness change alters how
    every test runs, so no selection can scope it (the 9 Oct release caught 6 repo-wide
    breaks the changed tests missed). Full shards; rare, so the average stays near 60."""
    out = run_step(tmp_path, "workflow_dispatch", RELEASE_BATCH + [harness], last_release=LAST_RELEASE)
    assert out == FULL_PIPELINE
    nested = run_step(tmp_path, "workflow_dispatch",
                      RELEASE_BATCH + ["sorento_crm_backend/tests/chatbot/conftest.py"], last_release=LAST_RELEASE)
    assert nested["lean"] == "true", "a sub-directory conftest is scoped by the selection, stays lean"


def test_a_tree_whose_release_tests_passed_is_not_tested_again(tmp_path):
    """A re-dispatch (a rejected approval, a deploy hiccup) of a tree whose
    release tests already passed builds and deploys only."""
    out = run_step(tmp_path, "workflow_dispatch", RELEASE_BATCH, last_release=LAST_RELEASE, validated=True)
    assert out == {"docs_only": "false", **NO_AREA}
    assert run_step(tmp_path, "workflow_dispatch", RELEASE_BATCH, last_release=LAST_RELEASE,
                    validated=True, full_suite="true") == FULL_PIPELINE, "full_suite wins"


def test_release_with_skip_tests_zeroes_every_area_but_is_not_docs_only(tmp_path):
    """build-images and build-and-deploy gate on the event, so they still run."""
    out = run_step(tmp_path, "workflow_dispatch", [], skip_tests="true")
    assert out == {"docs_only": "false", **NO_AREA}
    # The input is honoured on a dispatch only: it is empty on every other
    # event, and even a stray "true" must not silence a PR or queue run.
    assert run_step(tmp_path, "pull_request", ["sorento_crm_frontend/x.ts"], skip_tests="true")["frontend"] == "true"
    assert run_step(tmp_path, "merge_group", ["sorento_crm_backend/app/main.py"], skip_tests="true") == FULL_PIPELINE


# ---------------------------------------------------------------------------
# Changed tests on a PR: the `backend_tests` output (CI-TESTS-ONLY, #1387).
# ---------------------------------------------------------------------------

TESTS_ONLY_1387 = ["sorento_crm_backend/tests/test_media_job_lifecycle.py"]


def test_tests_only_pull_request_is_not_docs_and_lists_the_changed_test(tmp_path):
    """#1387 as the workflow classified it (run 36665697605), plus the new output.

    The docs filter never treated tests/ as docs: docs_only was false and
    backend true on that run. What was missing is the list, which is what
    lets the changed file execute on the PR.
    """
    assert run_step(tmp_path, "pull_request", TESTS_ONLY_1387) == {
        "docs_only": "false", "backend": "true", "frontend": "false", "mcp": "false", "scm": "false",
        "backend_tests": "sorento_crm_backend/tests/test_media_job_lifecycle.py", "backend_code": ""
    }


def test_changed_test_files_are_listed_beside_code_changes(tmp_path):
    """The list is the changed test files whatever else the PR touches, in sorted order, space-separated."""
    out = run_step(tmp_path, "pull_request", [
        "sorento_crm_backend/tests/test_rbac.py",
        "sorento_crm_backend/app/services/media_service.py",
        "sorento_crm_backend/tests/chatbot/test_attribute_asks_round5.py",
        "sorento_crm_frontend/app/page.tsx",
        "documentation/plans/ci/PLAN-x.md",
    ])
    assert out["backend_tests"] == (
        "sorento_crm_backend/tests/chatbot/test_attribute_asks_round5.py "
        "sorento_crm_backend/tests/test_rbac.py"
    )
    assert out["backend"] == "true" and out["frontend"] == "true" and out["scm"] == "false"


@pytest.mark.parametrize(
    "path",
    [
        # harness and helper modules: pytest collects nothing from them (exit 5)
        "sorento_crm_backend/tests/conftest.py",
        "sorento_crm_backend/tests/_pg_fixture.py",
        "sorento_crm_backend/tests/chatbot/helpers.py",
        "sorento_crm_backend/tests/ci_excluded.txt",
        "sorento_crm_backend/tests/chatbot/replay_turns/turn_01.md",
        "sorento_crm_backend/tests/scm/fixtures/golden_m2.json",
        "sorento_crm_backend/.test_durations",
        # not a backend test
        "sorento_crm_backend/app/tests/test_x.py",
        "sorento_crm_mcp/tests/test_presenters_top_selling.py",
        "sorento_crm_frontend/lib/test_utils.py",
        # a test named to break a shell line is not handed to one
        "sorento_crm_backend/tests/test_a b.py",
        "sorento_crm_backend/tests/test_$(x).py",
        "sorento_crm_backend/tests/sub dir/test_x.py",
    ],
)
def test_paths_that_are_not_a_runnable_backend_test_file_are_not_listed(tmp_path, path):
    out = run_step(tmp_path, "pull_request", [path])
    assert out["backend_tests"] == "", out


def test_scm_test_change_runs_in_the_pr_gate(tmp_path):
    """CI-LEAN: the SCM shards no longer run on a PR, so a changed tests/scm file
    is listed like any other (the run step keeps tests/scm in its own session)."""
    out = run_step(tmp_path, "pull_request", ["sorento_crm_backend/tests/scm/test_committed_v.py"])
    assert out["backend_tests"] == "sorento_crm_backend/tests/scm/test_committed_v.py"
    nested = run_step(tmp_path, "pull_request", ["sorento_crm_backend/tests/scm/nested/test_x.py"])
    assert nested["backend_tests"] == "sorento_crm_backend/tests/scm/nested/test_x.py"
    both = run_step(tmp_path, "pull_request", [
        "sorento_crm_backend/tests/scm/test_committed_v.py", "sorento_crm_backend/tests/test_rbac.py",
    ])
    assert both["backend_tests"] == "sorento_crm_backend/tests/scm/test_committed_v.py sorento_crm_backend/tests/test_rbac.py"


def test_the_import_selection_on_a_pr_keeps_tests_scm():
    """The selector drops tests/scm only when the SCM shards run anyway; on a PR
    they never do now."""
    script = _step_script("Select backend tests by import")
    assert '--scm false' in script and "$SCM" not in script


# ---------------------------------------------------------------------------
# CI-LEAN: the PR gate, one job doing all the PR's work.
# ---------------------------------------------------------------------------


def test_the_pr_gate_reports_as_pr_gate_on_a_pull_request():
    """The one check the ruleset requires (owner Q3, 10 Oct 2026)."""
    name = _expression(re.search(r"^    name: (.*)$", _job_block("lean-gate"), re.M).group(1))
    assert _gh_eval(name, {"github": {"event_name": "pull_request", "event": {"label": {"name": "ci"}}}}) == "PR gate"


def test_the_pr_gate_runs_whatever_the_fast_gates_did_and_fails_with_them(tmp_path):
    """`always()` so a red root job never turns the required check into a
    "skipped" (which a ruleset accepts); its first step fails on such a need.
    CI-COST round 2: the PII guard, gitleaks and the alembic head check run as
    steps of the PR gate on a PR, so `check-migration-heads` skips there."""
    jobs = _job_ifs()
    assert set(jobs["lean-gate"]["needs"]) == {"changes", "check-migration-heads"}
    for forced in ({"changes": "failure"}, {"check-migration-heads": "failure"}):
        results = _simulate("workflow_dispatch", outputs=LEAN_RELEASE, forced=forced)
        assert results["lean-gate"] == "success", "the job runs (its step then fails)"
    script = _step_script("Fail when a fast gate did not pass")
    green = {"changes": "success", "check-migration-heads": "success"}
    def run(needs, event):
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "EVENT": event,
                 "NEEDS": json.dumps({k: {"result": v, "outputs": {}} for k, v in needs.items()})},
            capture_output=True, text=True,
        )
    assert run(green, "workflow_dispatch").returncode == 0
    assert run({**green, "check-migration-heads": "skipped"}, "pull_request").returncode == 0, "folded on a PR"
    skipped = run({**green, "check-migration-heads": "skipped"}, "workflow_dispatch")
    assert skipped.returncode != 0 and "check-migration-heads skipped" in skipped.stdout + skipped.stderr
    for event in ("pull_request", "workflow_dispatch"):
        for job in green:
            bad = run({**green, job: "failure"}, event)
            assert bad.returncode != 0 and f"{job} failure" in bad.stdout + bad.stderr, (event, job)


def test_the_pr_gate_runs_the_fast_gates_as_its_first_steps():
    """CI-COST round 2: four one-job fast gates billed a minute each for 16-54 s of
    work (run 38122622280). On a PR they are steps of the PR gate, before the
    dependency install, so a red gate still fails in about a minute."""
    names = list(_lean_gate_steps())
    steps = _lean_gate_steps()
    for name, needle in (("Single alembic head", "python3 scripts/ci_alembic_heads.py"),
                         ("No personal data in tracked files", "python3 scripts/pii_guard.py"),
                         ("Secret scan (gitleaks)", "bash scripts/ci_gitleaks.sh")):
        assert name in steps, names
        assert needle in steps[name], name
        assert "github.event_name == 'pull_request'" in steps[name], name
        assert names.index(name) < names.index("Install dependencies"), name
    rng = re.search(r"^\s*RANGE: (.*)$", steps["Secret scan (gitleaks)"], re.M).group(1).strip()
    assert rng == "${{ github.event.pull_request.base.sha }}..${{ github.event.pull_request.head.sha }}", rng
    checkout = _lean_gate_steps()["Checkout"]
    assert "github.event_name == 'pull_request'" in checkout and "fetch-depth" in checkout, "gitleaks reads base..head"


def test_the_gitleaks_script_pins_and_verifies_the_cli():
    script = (REPO / "scripts" / "ci_gitleaks.sh").read_text(encoding="utf-8")
    assert re.search(r'GITLEAKS_VERSION="\$\{GITLEAKS_VERSION:-8\.30\.1\}"', script)
    assert "sha256sum -c -" in script and "--redact" in script
    assert "--config .gitleaks.toml" in script and "--no-merges --first-parent" in script
    assert 'git rev-list --count "$range"' in script
    assert "gitleaks/gitleaks-action" not in _workflow_text(), "the action needs a paid org license"


def test_the_alembic_head_script_passes_on_this_checkout_and_fails_on_two_heads(tmp_path):
    script = REPO / "scripts" / "ci_alembic_heads.py"
    ok = subprocess.run([sys.executable, str(script)], cwd=REPO, capture_output=True, text=True)
    assert ok.returncode == 0 and "1 head(s)" in ok.stdout, ok.stdout + ok.stderr
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "a.py").write_text('revision = "a"\ndown_revision = None\n', encoding="utf-8")
    (versions / "b.py").write_text('revision: str = "b"\ndown_revision = "a"\n', encoding="utf-8")
    (versions / "c.py").write_text('revision = "c"\ndown_revision = ("a",)\n', encoding="utf-8")
    bad = subprocess.run([sys.executable, str(script), str(versions)], capture_output=True, text=True)
    assert bad.returncode != 0 and "2 head(s): ['b', 'c']" in bad.stdout, bad.stdout + bad.stderr
    assert "python3 scripts/ci_alembic_heads.py" in _job_block("check-migration-heads")


def test_the_pr_gate_collects_only_what_it_runs():
    """CI-COST round 2: the whole-suite collect-only billed ~1.8 min of every PR gate
    (38074897990). On a PR the selected files' own run collects them; the whole-suite
    collect stays on the release, where it is the import check for every test."""
    step = _lean_gate_steps()["Pytest collect-only (catches import errors in tests)"]
    assert "github.event_name != 'pull_request'" in step


def test_the_pr_gate_carries_the_frontend_and_mcp_checks():
    steps = _lean_gate_steps()
    joined = "\n".join(steps.values())
    # frontend: the type-check and the vitest files related to what changed
    assert "npm run typecheck" in joined and "--max-old-space-size=4096" in joined
    assert "npx vitest run --changed" in joined
    assert "needs.changes.outputs.frontend == 'true'" in joined
    # backend imports for every test file, and the app and worker import chains
    assert "pytest --collect-only" in joined
    assert "import worker; from app.scheduler.task_scheduler import start_scheduler" in joined
    # mcp
    assert "sorento_crm_mcp/tests" in joined and "needs.changes.outputs.mcp == 'true'" in joined


def test_area_flags_do_not_race_grep_q_on_a_long_file_list(tmp_path):
    """CI-SCM-FILTER-RACE: a flag that matches the first changed path stays true.

    bash's printf writes one line per write() into the pipe, and `grep -q`
    exits on its first match. Under `-o pipefail` a printf still writing
    when grep exits dies of SIGPIPE and the pipeline counts as false, so
    `scm` came back false on run 37140058013 with just two paths. A list long
    many times the pipe buffer makes that race certain.
    """
    filler = [f"sorento_crm_frontend/app/page_{i:05d}.tsx" for i in range(20000)]
    out = run_step(tmp_path, "pull_request", ["sorento_crm_backend/tests/scm/test_committed_v.py", *filler])
    assert {k: out[k] for k in ("backend", "frontend", "mcp", "scm")} == {
        "backend": "true", "frontend": "true", "mcp": "false", "scm": "true",
    }, out


def test_rename_of_a_test_file_lists_both_ends(tmp_path):
    """The old path is in the PR's list too; the job drops it when it is not in the checkout."""
    out = run_step(tmp_path, "pull_request", [
        ("sorento_crm_backend/tests/test_media_jobs.py", "sorento_crm_backend/tests/test_media_job_lifecycle.py"),
    ])
    assert out["backend_tests"] == (
        "sorento_crm_backend/tests/test_media_job_lifecycle.py sorento_crm_backend/tests/test_media_jobs.py"
    )


@pytest.mark.parametrize("event", ["push", "merge_group", "workflow_dispatch"])
def test_changed_tests_are_never_listed_off_a_pull_request(tmp_path, event):
    """The full shards run there; the list must not make the changed-files job run twice."""
    out = run_step(tmp_path, event, TESTS_ONLY_1387)
    assert out["backend_tests"] == ""
    assert out["backend"] == "true"


def test_changed_tests_list_is_empty_on_every_early_exit(tmp_path):
    assert run_step(tmp_path, "pull_request", DOCS_ONLY)["backend_tests"] == ""
    assert run_step(tmp_path, "pull_request", TESTS_ONLY_1387, gh_fails=True) == FULL_PIPELINE
    assert run_step(tmp_path, "workflow_dispatch", [], skip_tests="true")["backend_tests"] == ""


# The "Select the changed test files to run" step of `lean-gate`,
# run against a scratch checkout: the workflow's own ci_excluded.txt, and the
# files each case says exist.


def run_select(
    tmp_path: Path, backend_tests: str, present: list[str], *, excluded: str | None = None,
) -> tuple[str, str]:
    """Run the real step in a scratch checkout; return (files output, stdout).

    `excluded` replaces the workflow's own ci_excluded.txt when given.
    """
    checkout = tmp_path / "checkout"
    (checkout / "sorento_crm_backend" / "tests").mkdir(parents=True)
    if excluded is None:
        shutil.copy(REPO / "sorento_crm_backend" / "tests" / "ci_excluded.txt", checkout / "sorento_crm_backend" / "tests")
    else:
        (checkout / "sorento_crm_backend" / "tests" / "ci_excluded.txt").write_text(excluded)
    for path in present:
        (checkout / path).parent.mkdir(parents=True, exist_ok=True)
        (checkout / path).write_text("def test_x():\n    pass\n")
    output = tmp_path / "select-output.txt"
    output.write_text("")
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _step_script("Select the changed test files to run")],
        cwd=checkout,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GITHUB_OUTPUT": str(output), "BACKEND_TESTS": backend_tests},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"step failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    assert set(outputs) == {"files"}, outputs
    return outputs["files"], result.stdout


def test_changed_tests_step_runs_the_files_the_checkout_holds_relative_to_the_backend(tmp_path):
    files, stdout = run_select(
        tmp_path,
        "sorento_crm_backend/tests/test_media_job_lifecycle.py sorento_crm_backend/tests/chatbot/test_attribute_asks_round5.py",
        ["sorento_crm_backend/tests/test_media_job_lifecycle.py", "sorento_crm_backend/tests/chatbot/test_attribute_asks_round5.py"],
    )
    assert files == "tests/test_media_job_lifecycle.py tests/chatbot/test_attribute_asks_round5.py"
    assert "changed test files to run:" in stdout
    assert "not run:" not in stdout


def test_changed_tests_step_drops_a_deleted_or_renamed_away_file(tmp_path):
    files, stdout = run_select(
        tmp_path,
        "sorento_crm_backend/tests/test_media_job_lifecycle.py sorento_crm_backend/tests/test_media_jobs.py",
        ["sorento_crm_backend/tests/test_media_job_lifecycle.py"],
    )
    assert files == "tests/test_media_job_lifecycle.py"
    assert "tests/test_media_jobs.py (not in the checkout" in stdout


def test_changed_tests_step_drops_a_ci_excluded_file(tmp_path):
    """tests/test_rbac.py is in ci_excluded.txt: it asserts against business rows CI does not seed."""
    files, stdout = run_select(
        tmp_path,
        "sorento_crm_backend/tests/test_rbac.py sorento_crm_backend/tests/test_media_job_lifecycle.py",
        ["sorento_crm_backend/tests/test_rbac.py", "sorento_crm_backend/tests/test_media_job_lifecycle.py"],
    )
    assert files == "tests/test_media_job_lifecycle.py"
    assert "tests/test_rbac.py (listed in tests/ci_excluded.txt)" in stdout


def test_changed_tests_step_does_not_race_grep_q_on_a_long_excluded_list(tmp_path):
    """CI-SCM-FILTER-RACE: an excluded file listed first is dropped, not run.

    Same SIGPIPE race as the area flags: `grep -qxF` exits on the first line
    while printf is still writing the rest of the list.
    """
    excluded = "tests/test_rbac.py\n" + "".join(f"tests/test_filler_{i:05d}.py\n" for i in range(20000))
    files, stdout = run_select(
        tmp_path,
        "sorento_crm_backend/tests/test_rbac.py",
        ["sorento_crm_backend/tests/test_rbac.py"],
        excluded=excluded,
    )
    assert files == "", stdout
    assert "tests/test_rbac.py (listed in tests/ci_excluded.txt)" in stdout


def test_changed_tests_step_with_nothing_left_passes_and_runs_nothing(tmp_path):
    files, stdout = run_select(tmp_path, "sorento_crm_backend/tests/test_rbac.py sorento_crm_backend/tests/test_gone.py", ["sorento_crm_backend/tests/test_rbac.py"])
    assert files == ""
    assert "no changed test file left to run" in stdout


def _lean_gate_steps() -> dict[str, str]:
    block = _job_block("lean-gate")
    steps = re.split(r"\n      - name: ", block)[1:]
    return {s.splitlines()[0]: s for s in steps}


def test_changed_tests_job_steps_after_select_gate_on_its_output():
    """The database steps, pytest included, skip on an empty list."""
    steps = _lean_gate_steps()
    for name in ("Relax Postgres durability for the test run", "Bootstrap the database",
                 "Run the changed test files (executing gate)"):
        assert "        if: steps.select.outputs.files != ''" in steps[name], name
    run = steps["Run the changed test files (executing gate)"]
    assert "--dist loadfile" in run and "$FILES" in run, "runs the selected files via env, under xdist"


# ---------------------------------------------------------------------------
# The `if` lines and the workflow-level concurrency, evaluated as written.
# ---------------------------------------------------------------------------
#
# A small evaluator for the subset of GitHub's expression language the
# workflow uses: `&&` and `||` return an operand (like JavaScript), `!`
# negates, a missing context value is the empty string, `contains()` on a
# list is membership, `needs.*.result` is the list of every need's result,
# and the status functions read the run state the scenario provides.

_GH_TOKEN = re.compile(r"'[^']*'|\d+|[A-Za-z_][\w.*-]*|&&|\|\||==|!=|[(),!]")
_GH_FUNCS = {"always", "cancelled", "success", "failure", "contains"}


def _gh_eval(expression: str, context: dict) -> object:
    def lookup(path: str):
        if path == "needs.*.result":
            return [job.get("result", "") for job in context.get("needs", {}).values()]
        node = context
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return ""
            node = node[part]
        return node

    def contains(haystack, needle):
        if isinstance(haystack, list):
            return needle in haystack
        return str(needle).lower() in str(haystack).lower()

    state = context.get("_run", {})
    funcs = {
        "lookup": lookup,
        "contains": contains,
        "always": lambda: True,
        "cancelled": lambda: state.get("cancelled", False),
        "success": lambda: state.get("success", True),
        "failure": lambda: state.get("failure", False),
    }
    python = []
    for token in _GH_TOKEN.findall(expression):
        if token == "&&":
            python.append(" and ")
        elif token == "||":
            python.append(" or ")
        elif token == "!":
            python.append(" not ")
        elif token in ("==", "!=", "(", ")", ","):
            python.append(f" {token} ")
        elif token.startswith("'") or token.isdigit():
            python.append(token)
        elif token in ("true", "false", "null"):
            python.append({"true": "True", "false": "False", "null": "None"}[token])
        elif token in _GH_FUNCS:
            python.append(token)
        else:
            python.append(f"lookup({token!r})")
    return eval("".join(python), {"__builtins__": {}}, funcs)  # noqa: S307


def _gh_render(template: str, context: dict) -> str:
    return re.sub(r"\$\{\{(.*?)\}\}", lambda m: str(_gh_eval(m.group(1).strip(), context)), template, flags=re.S)


def _expression(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("${{") and raw.endswith("}}"):
        raw = raw[3:-2].strip()
    return raw


def _job_ifs() -> dict[str, dict]:
    """job id -> {"if": expression or None, "needs": [...]} from the workflow text."""
    text = _workflow_text()
    jobs_text = text[text.index("\njobs:\n"):]
    jobs: dict[str, dict] = {}
    for block in re.split(r"\n(?=  [a-z][\w-]*:\n)", jobs_text):
        head = re.match(r"  ([a-z][\w-]*):\n", block)
        if not head:
            continue
        lines = block.splitlines()
        cond = None
        for i, line in enumerate(lines):
            m = re.match(r"^    if: (.*)$", line)
            if not m:
                continue
            value = m.group(1).strip()
            if value in (">-", ">", "|", "|-"):
                folded = []
                for cont in lines[i + 1:]:
                    if cont.strip() and (len(cont) - len(cont.lstrip())) <= 4:
                        break
                    folded.append(cont.strip())
                value = " ".join(folded)
            cond = _expression(value)
            break
        needs = re.search(r"^    needs:\s*(\[[^\]]*\]|\n(?:      .*\n?)+)", block, re.M)
        need_ids: list[str] = []
        if needs:
            need_ids = re.findall(r"[a-z][\w-]*", needs.group(1))
        jobs[head.group(1)] = {"if": cond, "needs": need_ids}
    return jobs


def _simulate(event: str, *, label: str | None = None, outputs: dict | None = None,
              forced: dict | None = None, ref: str = "refs/heads/main") -> dict[str, str]:
    """Result of every job (success/skipped/failure) for one run, in file order.

    Mirrors GitHub's rule: a job whose `if` names no status function skips
    when any need did not succeed; `always()` / `!cancelled()` conditions are
    evaluated against the needs' results instead.
    """
    github = {"event_name": event, "event": {}, "run_id": 1, "workflow": "Build and Deploy Sorento", "ref": ref}
    if event == "pull_request":
        github["event"] = {"pull_request": {"number": 1370}, "label": {"name": label or "ci"}}
    results: dict[str, str] = {}
    forced = forced or {}
    for job_id, job in _job_ifs().items():
        needs = {n: {"result": results[n], "outputs": (outputs or {}) if n == "changes" else {}} for n in job["needs"]}
        ctx = {"github": github, "needs": needs, "_run": {"cancelled": False}}
        cond = job["if"]
        uses_status_fn = bool(cond) and bool(re.search(r"\b(always|cancelled|success|failure)\(\)", cond))
        if not uses_status_fn and any(r != "success" for r in (results[n] for n in job["needs"])):
            results[job_id] = "skipped"
            continue
        runs = True if cond is None else bool(_gh_eval(cond, ctx))
        results[job_id] = (forced.get(job_id, "success") if runs else "skipped")
    return results


def _ran(results: dict[str, str]) -> set[str]:
    return {job for job, r in results.items() if r != "skipped"}


# CI-LEAN + CI-COST round 2: a `ci` PR runs `changes` and ONE job that does
# everything else, fast gates first (`lean-gate`, check name `PR gate`): no image builds, no SCM
# or main shards, no vitest shards, no separate label or gate job.
GATES_ON_PR = {"changes", "lean-gate"}


def test_pull_request_fast_gate_backend_with_scm_paths():
    ran = _ran(_simulate("pull_request", outputs={"backend": "true", "scm": "true", "frontend": "false", "mcp": "false", "docs_only": "false"}))
    assert ran == GATES_ON_PR
    assert "test-backend" not in ran, "the six backend shards never run on a pull_request"


def test_ci_label_never_runs_the_six_shards_whatever_changes_reports():
    """CI-MINUTES-CUT: no output of `changes` can put the shards on a `ci` run."""
    for outputs in (FULL_PIPELINE, {**FULL_PIPELINE, "backend_full": "true"}, {**TESTS_ONLY_OUTPUTS, "backend_full": "true"}):
        results = _simulate("pull_request", label="ci", outputs=outputs)
        assert results["test-backend"] == "skipped", outputs


def test_ci_full_label_is_refused_on_a_pull_request():
    """Owner, 9 Oct 2026: no ci-full per PR. The label runs ONE job, which
    removes it and fails the run with the reason; nothing else runs, so no
    suite, no build and no deploy, whatever `changes` would have said."""
    for outputs in (FULL_PIPELINE, {**NO_AREA, "docs_only": "true"}):
        results = _simulate("pull_request", label="ci-full", outputs=outputs)
        assert _ran(results) == {"refuse-ci-full"}, results
    for label in ("ci", "needs-hand-test", "lane-running"):
        assert _simulate("pull_request", label=label)["refuse-ci-full"] == "skipped", label
    for event in ("push", "merge_group", "workflow_dispatch"):
        assert _simulate(event, outputs=FULL_PIPELINE)["refuse-ci-full"] == "skipped", event


def test_refuse_ci_full_job_removes_the_label_and_fails_with_the_reason():
    block = _job_block("refuse-ci-full")
    assert "pull-requests: write" in block
    assert "--remove-label ci-full" in block
    assert "release-only" in block and "exit 1" in block
    assert re.search(r"^    timeout-minutes: \d+$", block, re.M)


def test_pull_request_fast_gate_backend_without_scm_paths():
    ran = _ran(_simulate("pull_request", outputs={"backend": "true", "scm": "false", "frontend": "false", "mcp": "false", "docs_only": "false"}))
    assert ran == GATES_ON_PR


def test_pull_request_fast_gate_frontend_and_mcp():
    ran = _ran(_simulate("pull_request", outputs={"backend": "false", "scm": "false", "frontend": "true", "mcp": "true", "docs_only": "false"}))
    assert ran == GATES_ON_PR


def test_pull_request_docs_only_runs_the_root_jobs_only():
    ran = _ran(_simulate("pull_request", outputs={**NO_AREA, "docs_only": "true"}))
    assert ran == GATES_ON_PR


TESTS_ONLY_OUTPUTS = {
    "backend": "true", "scm": "false", "frontend": "false", "mcp": "false", "docs_only": "false",
    "backend_tests": "sorento_crm_backend/tests/test_media_job_lifecycle.py", "backend_code": ""
}


def test_pull_request_that_changes_a_test_file_runs_it(tmp_path):
    """#1387: the light checks AND the changed test, never the six shards.

    `outputs` here is what the real step produced for that PR's file list,
    so the job set follows from the shell that ships, not from a hand-typed
    flag set.
    """
    outputs = run_step(tmp_path, "pull_request", TESTS_ONLY_1387)
    assert outputs == TESTS_ONLY_OUTPUTS
    ran = _ran(_simulate("pull_request", outputs=outputs))
    assert ran == GATES_ON_PR
    assert "test-backend" not in ran


def test_pull_request_with_code_and_a_test_file_runs_both_gates():
    outputs = {**TESTS_ONLY_OUTPUTS, "scm": "true", "frontend": "true"}
    ran = _ran(_simulate("pull_request", outputs=outputs))
    assert ran == GATES_ON_PR


def test_changed_tests_job_never_runs_off_a_pull_request_or_with_another_label():
    for event in ("merge_group", "push"):
        results = _simulate(event, outputs={**FULL_PIPELINE, "backend_tests": "sorento_crm_backend/tests/test_x.py", "backend_code": ""})
        assert results["lean-gate"] == "skipped", (event, results)
    for label in ("needs-hand-test", "lane-running", "ci-full"):
        results = _simulate("pull_request", label=label, outputs=TESTS_ONLY_OUTPUTS)
        assert results["lean-gate"] == "skipped", label


def test_pull_request_with_another_label_runs_nothing():
    for label in ["needs-hand-test", "lane-running", "needs-decision"]:
        assert _ran(_simulate("pull_request", label=label)) == set(), label


def test_merge_group_runs_the_full_suite_and_never_deploys():
    ran = _ran(_simulate("merge_group", outputs=FULL_PIPELINE))
    assert ran == {
        "check-migration-heads", "pii-guard", "changes", "validate-backend", "validate-mcp",
        "test-backend-scm", "test-backend", "validate-frontend", "typecheck-frontend", "ci-gate",
    }


# CI-LEAN: a release runs neither the PII guard (the push to main already ran
# it on the same tree) nor the CI gate (build-and-deploy judges every need
# itself), and the owner mail is a step of build-and-deploy, not a job.
RELEASE_ONLY = {"check-migration-heads", "changes", "build-images", "build-and-deploy"}
FULL_SUITE = {
    "check-migration-heads", "pii-guard", "changes", "validate-backend", "validate-mcp",
    "test-backend-scm", "test-backend", "validate-frontend", "typecheck-frontend", "ci-gate",
}
RELEASE_TESTS = FULL_SUITE - {"pii-guard", "ci-gate"}


def test_push_to_main_runs_the_alembic_gate_and_nothing_else():
    """Owner decision 29 Sep 2026: a merge does not test or deploy. CI-COST round 2:
    the PII guard and gitleaks ran on the PR's commits in its PR gate, so a push
    to main is the alembic head check alone (3 billed minutes -> 1)."""
    for outputs in (FULL_PIPELINE, {**NO_AREA, "docs_only": "true"}):
        results = _simulate("push", outputs=outputs)
        assert _ran(results) == {"check-migration-heads"}, results
        assert results["changes"] == "skipped"
        assert results["build-images"] == "skipped"
        assert results["build-and-deploy"] == "skipped"


def test_release_runs_the_full_suite_then_builds_and_deploys():
    ran = _ran(_simulate("workflow_dispatch", outputs=FULL_PIPELINE))
    assert ran == RELEASE_TESTS | RELEASE_ONLY


def test_release_with_skip_tests_builds_and_deploys_only():
    ran = _ran(_simulate("workflow_dispatch", outputs={"docs_only": "false", **NO_AREA}))
    assert ran == RELEASE_ONLY


def test_release_dispatched_on_another_ref_tests_but_never_ships():
    results = _simulate("workflow_dispatch", outputs=FULL_PIPELINE, ref="refs/heads/crew/ci-fast-gate")
    assert _ran(results) == RELEASE_TESTS
    assert results["build-images"] == "skipped"
    assert results["build-and-deploy"] == "skipped"


@pytest.mark.parametrize("failed", ["test-backend", "test-backend-scm", "validate-frontend", "typecheck-frontend",
                                    "validate-backend", "validate-mcp", "check-migration-heads", "build-images"])
def test_release_with_a_failed_gate_does_not_deploy(failed):
    """A failed gate fails the run, and GitHub's own failed-workflow mail goes
    to whoever dispatched it; there is no separate notify job any more."""
    results = _simulate("workflow_dispatch", outputs=FULL_PIPELINE, forced={failed: "failure"})
    assert results["build-and-deploy"] == "skipped", failed


def test_owner_mail_is_the_last_step_of_the_deploy_job():
    """CI-LEAN: the notify job billed a minute per release to print that the
    SMTP secrets are unset (run 38017372957). It is a step now: `always()`,
    so a failed deploy still mails once SMTP is configured."""
    assert "notify-owner" not in _job_ifs()
    block = _job_block("build-and-deploy")
    steps = re.findall(r"^      - name: (.*)$", block, re.M)
    assert steps[-1] == "Mail the owner the deploy result", steps
    tail = block[block.index("- name: Mail the owner the deploy result"):]
    assert "if: always()" in tail and "dawidd6/action-send-mail" in tail


def test_release_runs_neither_the_pii_guard_nor_the_ci_gate():
    for outputs in (FULL_PIPELINE, {"docs_only": "false", **NO_AREA}):
        results = _simulate("workflow_dispatch", outputs=outputs)
        assert results["pii-guard"] == "skipped" and results["ci-gate"] == "skipped", results
    assert _simulate("merge_group", outputs=FULL_PIPELINE)["pii-guard"] == "success"
    for event, label in (("push", None), ("pull_request", "ci")):
        assert _simulate(event, label=label, outputs=FULL_PIPELINE)["pii-guard"] == "skipped", event
    assert "gitleaks" not in _job_ifs(), "a step of the PR gate now, no job"


# ---------------------------------------------------------------------------
# CI-LEAN: one images job, reusing an image whose service tree is unchanged.
# ---------------------------------------------------------------------------

DOCKER_STUB = """\
#!/usr/bin/env bash
echo "docker $*" >> "$DOCKER_LOG"
if [ "$1 $2 $3" = "buildx imagetools inspect" ]; then
  grep -qxF "$4" "$DOCKER_EXISTING" && exit 0
  echo "ERROR: $4: not found" >&2; exit 1
fi
exit 0
"""


def _run_decide(tmp_path: Path, *, existing: list[str], rebuild_all: str = "false") -> tuple[dict, list[str], dict]:
    """Run the real "Decide which images to build" step in a scratch git repo
    holding the three service trees, with a stub `docker`; return the step's
    outputs, the docker calls and each service's tree hash."""
    repo = tmp_path / "repo"
    for svc in ("frontend", "backend", "mcp"):
        (repo / f"sorento_crm_{svc}").mkdir(parents=True)
        (repo / f"sorento_crm_{svc}" / "Dockerfile").write_text(f"FROM scratch # {svc}\n")
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("add", ".")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x")
    trees = {svc: git("rev-parse", f"HEAD:sorento_crm_{svc}") for svc in ("frontend", "backend", "mcp")}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(DOCKER_STUB)
    (bin_dir / "docker").chmod(0o755)
    existing_file = tmp_path / "existing.txt"
    existing_file.write_text("".join(
        f"jayson1004/sorento-crm:{svc}-tree-{trees[svc]}\n" for svc in existing
    ))
    log = tmp_path / "docker.log"
    log.write_text("")
    output = tmp_path / "output.txt"
    output.write_text("")
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _step_script("Decide which images to build")],
        cwd=repo,
        env={"PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}", "GITHUB_OUTPUT": str(output),
             "GITHUB_SHA": SHA, "IMAGE_REPO": "jayson1004/sorento-crm", "REBUILD_ALL": rebuild_all,
             "DOCKER_LOG": str(log), "DOCKER_EXISTING": str(existing_file)},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    return outputs, log.read_text().splitlines(), trees


def test_build_images_is_one_job():
    """Three matrix legs billed three roundings plus three setups (run
    38017372957: 2 + 10 + 1); one job builds what changed in sequence."""
    block = _job_block("build-images")
    assert "matrix:" not in block
    assert re.search(r"^    timeout-minutes: \d+$", block, re.M)


def test_an_unchanged_service_tree_is_retagged_not_rebuilt(tmp_path):
    outputs, calls, trees = _run_decide(tmp_path, existing=["backend", "mcp"])
    assert outputs["build_frontend"] == "true"
    assert outputs["build_backend"] == "false" and outputs["build_mcp"] == "false"
    for svc in ("backend", "mcp"):
        assert (f"docker buildx imagetools create -t jayson1004/sorento-crm:{svc}-{SHA} "
                f"jayson1004/sorento-crm:{svc}-tree-{trees[svc]}") in calls, calls
        assert outputs[f"{svc}_tree"] == trees[svc]
    assert not any("create" in c and "frontend" in c for c in calls), "a changed tree is never retagged"


def test_nothing_in_the_registry_builds_everything(tmp_path):
    outputs, calls, _ = _run_decide(tmp_path, existing=[])
    assert {outputs[f"build_{s}"] for s in ("frontend", "backend", "mcp")} == {"true"}
    assert not any("imagetools create" in c for c in calls)


def test_rebuild_all_ignores_the_registry(tmp_path):
    """A rotated build-arg secret (NEXT_PUBLIC_VAPID_PUBLIC_KEY) is not in the
    tree hash: `rebuild_all` is the release input that forces a build."""
    outputs, calls, _ = _run_decide(tmp_path, existing=["frontend", "backend", "mcp"], rebuild_all="true")
    assert {outputs[f"build_{s}"] for s in ("frontend", "backend", "mcp")} == {"true"}
    assert not any("imagetools" in c for c in calls)
    assert re.search(r"^      rebuild_all:$", _triggers(), re.M)


def test_every_build_pushes_the_sha_tag_and_the_tree_tag():
    block = _job_block("build-images")
    for svc in ("frontend", "backend", "mcp"):
        assert f"${{{{ env.IMAGE_REPO }}}}:{svc}-${{{{ github.sha }}}}" in block, svc
        assert f"${{{{ env.IMAGE_REPO }}}}:{svc}-tree-${{{{ steps.decide.outputs.{svc}_tree }}}}" in block, svc
        assert f"if: steps.decide.outputs.build_{svc} == 'true'" in block, svc


def test_release_with_skip_tests_and_a_failed_build_does_not_deploy():
    for failed in ("build-images", "check-migration-heads"):
        results = _simulate("workflow_dispatch", outputs={"docs_only": "false", **NO_AREA}, forced={failed: "failure"})
        assert results["build-and-deploy"] == "skipped", failed


def test_deploy_job_carries_the_production_environment():
    """The owner's approval gate: the only job that touches the server."""
    text = _workflow_text()
    block = re.search(r"\n  build-and-deploy:\n(.*?)\n  [a-z][\w-]*:\n", text, re.S).group(1)
    assert re.search(r"^    environment:\n      name: production$", block, re.M), block
    for other in ("build-images", "test-backend", "changes"):
        other_block = re.search(rf"\n  {other}:\n(.*?)\n  [a-z][\w-]*:\n", text, re.S).group(1)
        assert "environment:" not in other_block, other


def test_merge_group_never_deploys_whatever_the_flags():
    results = _simulate("merge_group", outputs=FULL_PIPELINE)
    assert results["build-images"] == "skipped"
    assert results["build-and-deploy"] == "skipped"


# ---------------------------------------------------------------------------
# CI-MINUTES-CUT: the one-shot label release, the timeouts and the CI gate.
# ---------------------------------------------------------------------------


def _job_block(job_id: str) -> str:
    text = _workflow_text()
    return re.search(rf"\n  {job_id}:\n(.*?)(?=\n  [a-z][\w-]*:\n|\Z)", text, re.S).group(1)


def test_the_pr_gate_removes_the_ci_label_that_fired_the_run():
    """`ci` is one-shot: the PR gate's first steps remove it (it was its own job,
    a billed minute per PR; `ci-full` has its own refusing job)."""
    assert "release-ci-label" not in _job_ifs()
    step = _lean_gate_steps()["Remove the ci label so a future add re-triggers CI"]
    assert "--remove-label ci" in step and "github.event_name == 'pull_request'" in step
    assert "pull-requests: write" in _job_block("lean-gate")


DEPLOY_JOBS = {"build-images", "build-and-deploy"}
LABEL_JOBS = {"refuse-ci-full"}


def test_every_test_and_gate_job_carries_a_timeout():
    """Backend test jobs 90 min, everything else 20; the deploy jobs are out of this lane's scope.

    90, not 60 (11 Oct 2026): on the private-repo runners the main shards ran 29-59 min of
    pytest and three hit the 60-minute cap (release 38071049191 shards 2 and 6, release
    38091974895 shard 1, nightly 38091972809 shards 1 and 2).

    The caps bound run time only, at about twice the worst observed run:
    on run 37369427779 (5 Oct 2026) a 35-minute cap cut a healthy shard at
    94% (33:55 of pytest, 22 min two days earlier). The same run's eleven
    queued jobs were cancelled 15 min after queueing whatever their cap;
    that was "not acquired by Runner of type hosted even after multiple
    attempts", GitHub's give-up under a saturated account, not the caps
    (lesson 118).
    """
    text = _workflow_text()
    jobs = _job_ifs()
    timeouts = {}
    for job_id in jobs:
        m = re.search(r"^    timeout-minutes: (\d+)$", _job_block(job_id), re.M)
        timeouts[job_id] = int(m.group(1)) if m else None
    shards = {"test-backend", "test-backend-scm", "lean-gate"}
    for job_id, minutes in timeouts.items():
        if job_id in DEPLOY_JOBS:
            continue
        assert minutes == (90 if job_id in shards else 20), (job_id, minutes)
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request'" in text


def test_ci_gate_needs_every_test_and_validate_job():
    jobs = _job_ifs()
    expected = set(jobs) - DEPLOY_JOBS - LABEL_JOBS - {"ci-gate"}
    assert set(jobs["ci-gate"]["needs"]) == expected, jobs["ci-gate"]["needs"]


def test_ci_gate_reports_a_different_name_for_the_full_suite():
    """A ruleset reads the latest check of a name on the head: a `ci` run must
    not stand in for a full-suite run, so the full run reports as `CI gate (full)`."""
    name = _expression(re.search(r"^    name: (.*)$", _job_block("ci-gate"), re.M).group(1))
    github = lambda event, label=None: {"github": {"event_name": event, "event": {"label": {"name": label}} if label else {}}}
    assert _gh_eval(name, github("pull_request", "ci")) == "CI gate"
    for event in ("merge_group", "workflow_dispatch"):
        assert _gh_eval(name, github(event)) == "CI gate (full)", event


def test_changes_job_token_can_read_the_pr_files_and_the_actions_api():
    """Private repo: a restricted default token would fail both `gh api` calls
    and the fail-safe would turn every `ci` run into every area changed."""
    block = _job_block("changes")
    perms = re.search(r"^    permissions:\n((?:      .*\n)+)", block, re.M).group(1)
    assert {"contents: read", "pull-requests: read", "actions: read"} <= {line.strip() for line in perms.splitlines()}


def test_ci_gate_runs_on_every_gated_event_and_on_nothing_else():
    """On a PR the `PR gate` job is the one check (CI-LEAN, Q3); `CI gate (full)`
    stays for the merge queue."""
    assert _simulate("pull_request", label="ci", outputs=TESTS_ONLY_OUTPUTS)["ci-gate"] == "skipped"
    assert _simulate("pull_request", label="ci-full", outputs=FULL_PIPELINE)["ci-gate"] == "skipped"
    assert _simulate("merge_group", outputs=FULL_PIPELINE)["ci-gate"] == "success"
    assert _simulate("workflow_dispatch", outputs=FULL_PIPELINE)["ci-gate"] == "skipped", "build-and-deploy judges the needs"
    assert _simulate("push", outputs=FULL_PIPELINE)["ci-gate"] == "skipped", "a push to main is the alembic gate alone"
    for label in ("needs-hand-test", "lane-running", "needs-decision"):
        assert _simulate("pull_request", label=label)["ci-gate"] == "skipped", label


def _run_gate(tmp_path: Path, needs: dict[str, str]) -> tuple[int, str]:
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _step_script("Fail when a needed job failed or was cancelled")],
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "NEEDS": json.dumps({k: {"result": v, "outputs": {}} for k, v in needs.items()})},
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout + result.stderr


GATE_NEEDS_GREEN = {
    "check-migration-heads": "success", "pii-guard": "success", "changes": "success",
    "validate-backend": "success", "validate-mcp": "skipped", "test-backend-scm": "skipped",
    "test-backend": "skipped", "lean-gate": "skipped", "validate-frontend": "skipped",
    "typecheck-frontend": "skipped",
}


def test_ci_gate_step_passes_when_every_need_succeeded_or_was_path_filtered(tmp_path):
    rc, out = _run_gate(tmp_path, GATE_NEEDS_GREEN)
    assert rc == 0, out
    assert "CI gate passed" in out


@pytest.mark.parametrize("job,result", [
    ("test-backend", "failure"), ("test-backend-scm", "cancelled"), ("validate-frontend", "failure"),
    ("lean-gate", "failure"), ("pii-guard", "failure"),
    # Run 37369427779 (5 Oct 2026): GitHub reports `abandoned` for a job it
    # never found a runner for and for a matrix whose shard hit its cap; a
    # deny-list of failure/cancelled passed that run green.
    ("test-backend", "abandoned"), ("pii-guard", "abandoned"), ("validate-frontend", "abandoned"),
    ("typecheck-frontend", "timed_out"), ("validate-mcp", "something-new"), ("test-backend", ""),
])
def test_ci_gate_step_fails_on_anything_but_success_or_skipped(tmp_path, job, result):
    rc, out = _run_gate(tmp_path, {**GATE_NEEDS_GREEN, job: result})
    assert rc != 0
    assert f"{job} {result or 'missing'}" in out


@pytest.mark.parametrize("job", ["check-migration-heads", "changes"])
def test_ci_gate_step_fails_when_a_root_job_did_not_succeed(tmp_path, job):
    """A skipped root job means nothing downstream was classified or gated."""
    rc, out = _run_gate(tmp_path, {**GATE_NEEDS_GREEN, job: "skipped"})
    assert rc != 0 and "a root job must succeed" in out


def _concurrency() -> tuple[str, str]:
    """The workflow-level `group` and `cancel-in-progress` lines, verbatim."""
    text = _workflow_text()
    block = re.search(r"^concurrency:\n((?:  .*\n)+)", text, re.M)
    assert block, "workflow-level concurrency block not found"
    group = re.search(r"^  group: (.*)$", block.group(1), re.M)
    cancel = re.search(r"^  cancel-in-progress: (.*)$", block.group(1), re.M)
    assert group and cancel, block.group(1)
    return group.group(1).strip(), cancel.group(1).strip()


def _event(event: str, *, label: str | None = None, pr: int | None = None, run_id: int) -> dict:
    ctx = {"workflow": "Build and Deploy Sorento", "event_name": event, "run_id": run_id, "event": {}}
    if pr is not None:
        ctx["event"]["pull_request"] = {"number": pr}
    if label is not None:
        ctx["event"]["label"] = {"name": label}
    return {"github": ctx}


def _resolve(ctx: dict) -> tuple[str, bool]:
    group, cancel = _concurrency()
    return _gh_render(group, ctx), bool(_gh_eval(_expression(cancel), ctx))


def test_concurrency_ci_label_runs_share_the_pr_group_and_cancel():
    first = _resolve(_event("pull_request", label="ci", pr=1370, run_id=1))
    second = _resolve(_event("pull_request", label="ci", pr=1370, run_id=2))
    assert first == ("ci-Build and Deploy Sorento-1370", True)
    assert second[0] == first[0], "a re-added ci label must supersede the older ci run"
    other_pr = _resolve(_event("pull_request", label="ci", pr=1371, run_id=3))
    assert other_pr[0] != first[0], "one PR's ci run must not cancel another PR's"


@pytest.mark.parametrize("label", ["ci-full", "needs-hand-test", "lane-running", "needs-decision", "ready-for-agent"])
def test_concurrency_other_label_events_join_no_group_and_cancel_nothing(label):
    ci_group, _ = _resolve(_event("pull_request", label="ci", pr=1370, run_id=1))
    group, cancel = _resolve(_event("pull_request", label=label, pr=1370, run_id=2))
    assert group != ci_group, f"a {label} event must not join the PR's ci group"
    assert group.endswith("-2"), "falls through to the run id, a group nothing else can join"
    assert cancel is False


@pytest.mark.parametrize("event", ["push", "merge_group", "workflow_dispatch"])
def test_concurrency_non_pr_events_are_unique_and_never_cancel(event):
    group, cancel = _resolve(_event(event, run_id=77))
    assert group == "ci-Build and Deploy Sorento-77"
    assert cancel is False


def test_gh_eval_matches_github_semantics():
    """Guard on the evaluator itself for the operators the workflow relies on."""
    ctx = {"github": {"event": {"pull_request": {"number": 5}}},
           "needs": {"a": {"result": "success"}, "b": {"result": "skipped"}}}
    assert _gh_eval("github.event.pull_request.number || github.run_id", ctx) == 5
    assert _gh_eval("github.event.label.name || 'none'", ctx) == "none"  # missing -> ''
    assert _gh_eval("(true && false && 5) || 9", ctx) == 9
    assert _gh_eval("('a' == 'a' && 'b' == 'b' && 5) || 9", ctx) == 5
    assert _gh_eval("github.event_name == 'pull_request'", ctx) is False
    assert _gh_eval("!contains(needs.*.result, 'failure')", ctx) is True
    assert _gh_eval("contains(needs.*.result, 'skipped')", ctx) is True
    assert _gh_eval("!cancelled() && needs.a.result == 'success'", ctx) is True
    assert _gh_eval("always() && needs.b.result == 'success'", ctx) is False


# ---------------------------------------------------------------------------
# Self-check: every documentation/ path a test reads must NOT be docs.
# ---------------------------------------------------------------------------

_PATH_CALLS = {"Path", "PurePath", "open", "read_text", "read_bytes", "glob", "rglob", "exists", "joinpath"}


def _div_chain(node: ast.AST) -> list[str]:
    """`base / "documentation" / "plans" / name` -> its string parts, in order."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _div_chain(node.left) + _div_chain(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return []


def _documentation_inputs() -> dict[str, str]:
    """documentation/ paths that test CODE names as files or folders.

    Two shapes count: a pathlib `/` chain holding a "documentation" segment
    (the outermost chain only, so a prefix is not reported beside its full
    path), and a string literal starting with `documentation/` passed straight
    to Path()/open()/read_text()/glob() and friends. Docstrings, comments and
    strings that only cite a plan in an assertion message do not count.
    Returns path -> "file:line" of the first mention.
    """
    found: dict[str, str] = {}
    self_file = Path(__file__).resolve()
    for tree_root in TEST_TREES:
        for py in sorted(tree_root.rglob("*.py")):
            if py.resolve() == self_file:
                continue
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            inner: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                    for child in (node.left, node.right):
                        if isinstance(child, ast.BinOp) and isinstance(child.op, ast.Div):
                            inner.add(id(child))
            where = f"{py.relative_to(REPO)}"
            for node in ast.walk(tree):
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and id(node) not in inner:
                    parts = _div_chain(node)
                    if "documentation" in parts:
                        path = "/".join(parts[parts.index("documentation"):])
                        found.setdefault(path, f"{where}:{node.lineno}")
                elif isinstance(node, ast.Call):
                    func = node.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                    if name not in _PATH_CALLS:
                        continue
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                                and arg.value.startswith("documentation/"):
                            found.setdefault(arg.value, f"{where}:{arg.lineno}")
    return found


def _probe(path: str) -> str:
    """A concrete file path inside what a test names (a folder, a glob or a file)."""
    parts = [p for p in path.split("/") if p and "*" not in p]
    if "." not in parts[-1]:
        parts.append("probe.txt")
    return "/".join(parts)


def test_documentation_inputs_scan_finds_the_known_readers():
    """Guard on the scanner itself: the readers the crew review cited are found."""
    inputs = _documentation_inputs()
    for expected in [
        "documentation/plans/scm/fixtures",
        "documentation/reference",
        "documentation/plans/autocount/PLAN-autocount-cross-repo-contract.md",
        "documentation/plans/_archive/scm/PLAN-scm-fulfilment-feedback-p4.md",
        "documentation/plans/dealer-kit/seed-assets/pdf-geometry.json",
        "documentation/plans/chatbot/samples",
    ]:
        assert expected in inputs, f"{expected} not found by the scan; found: {sorted(inputs)}"


def test_every_documentation_path_a_test_reads_keeps_the_full_pipeline(tmp_path):
    """A test that reads documentation/ must not be skipped by a change to it.

    Fails when a test reads a documentation/ path that the workflow would
    classify as docs. The fix is to extend NOT_DOCS_RE in deploy.yml (job
    `changes`) to cover the new path, never to loosen this test.
    """
    inputs = _documentation_inputs()
    assert len(inputs) >= 5, f"scan found too few documentation readers: {inputs}"
    misclassified = {}
    for path, where in sorted(inputs.items()):
        probe = _probe(path)
        out = run_step(tmp_path, "push", [probe])
        if out["docs_only"] != "false":
            misclassified[probe] = where
    assert not misclassified, (
        "these documentation/ paths are read by tests but the workflow treats a "
        "change to them as docs-only; extend NOT_DOCS_RE in deploy.yml: "
        f"{misclassified}"
    )


# ---------------------------------------------------------------------------
# Import-based selection on a PR (CI-SPEED, point 5 of the `changes` comment).
# ---------------------------------------------------------------------------

PR_1411_FILES = [
    "documentation/plans/autocount/PLAN-spo-xlsx-product-fallback.md",
    "sorento_crm_backend/app/services/grn_spo_matching.py",
    "sorento_crm_backend/app/services/incoming_stock_service.py",
    "sorento_crm_backend/app/services/procurement_service.py",
    "sorento_crm_backend/app/services/rules/shipping_order_rules.py",
    "sorento_crm_backend/app/services/shipping_order_ingest_service.py",
    "sorento_crm_backend/scripts/dedupe_spo_xlsx_superseded.py",
    "sorento_crm_backend/scripts/oneoff/dedupe_spo_standalone.py",
    "sorento_crm_backend/tests/test_dedupe_spo_standalone.py",
    "sorento_crm_backend/tests/test_spo_xlsx_product_fallback.py",
    "sorento_crm_backend/tests/test_spo_xlsx_supersede.py",
]


def test_backend_code_lists_changed_modules_not_test_files(tmp_path):
    out = run_step(tmp_path, "pull_request", PR_1411_FILES + [
        "sorento_crm_backend/tests/_void_harness.py",
        "sorento_crm_backend/tests/scm/test_x.py",
        "sorento_crm_backend/app/templates/mail.html",
        "sorento_crm_backend/app/bad name.py",
    ])
    assert out["backend_code"].split() == [
        "sorento_crm_backend/app/services/grn_spo_matching.py",
        "sorento_crm_backend/app/services/incoming_stock_service.py",
        "sorento_crm_backend/app/services/procurement_service.py",
        "sorento_crm_backend/app/services/rules/shipping_order_rules.py",
        "sorento_crm_backend/app/services/shipping_order_ingest_service.py",
        "sorento_crm_backend/scripts/dedupe_spo_xlsx_superseded.py",
        "sorento_crm_backend/scripts/oneoff/dedupe_spo_standalone.py",
        "sorento_crm_backend/tests/_void_harness.py",
    ]
    for event in ("merge_group", "workflow_dispatch", "push"):
        assert run_step(tmp_path, event, PR_1411_FILES)["backend_code"] == ""


def test_select_step_on_pr_1411_runs_the_test_that_broke_the_release(tmp_path):
    """The step's shell as it ships, over this checkout, with #1411's outputs."""
    filtered = run_step(tmp_path, "pull_request", PR_1411_FILES)
    output = tmp_path / "select.txt"
    output.write_text("")
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _step_script("Select backend tests by import")],
        cwd=REPO,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GITHUB_OUTPUT": str(output),
            "BACKEND_CODE": filtered["backend_code"], "BACKEND_TESTS": filtered["backend_tests"], "SCM": filtered["scm"],
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    # Under the budget when this was written (216 files, ~2668 CPU-s); above
    # it (the tree has grown) only the PR's own changed test files go out.
    if outputs["full"] == "false":
        selected = outputs["tests"].split()
        assert "sorento_crm_backend/tests/test_ingest_parity_security_fixes.py" in selected
        assert set(filtered["backend_tests"].split()) <= set(selected)
    else:
        assert outputs == {"tests": filtered["backend_tests"], "full": "true"}
    assert "estimated" in result.stdout and "sorento_crm_backend/tests/test_ingest_parity_security_fixes.py" in result.stdout


def test_changes_job_outputs_prefer_the_selection_and_fall_back_above_the_budget():
    """CI-MINUTES-CUT: no `backend_full` output any more; above the budget the
    script writes no list and the PR's own changed test files run instead."""
    text = _workflow_text()
    block = re.search(r"\n  changes:\n(.*?)\n    steps:", text, re.S).group(1)
    assert "backend_full" not in text, "the over-budget escape to the shards is retired"
    tests_expr = _expression(re.search(r"^      backend_tests: (.*)$", block, re.M).group(1))
    filt = {"backend_tests": "sorento_crm_backend/tests/test_a.py"}
    ran = {"steps": {"select": {"outputs": {"tests": "sorento_crm_backend/tests/test_a.py sorento_crm_backend/tests/test_b.py", "full": "false"}}, "filter": {"outputs": filt}}}
    skipped = {"steps": {"select": {"outputs": {}}, "filter": {"outputs": filt}}}
    over = {"steps": {"select": {"outputs": {"tests": "", "full": "true"}}, "filter": {"outputs": filt}}}
    assert _gh_eval(tests_expr, ran) == "sorento_crm_backend/tests/test_a.py sorento_crm_backend/tests/test_b.py"
    assert _gh_eval(tests_expr, skipped) == "sorento_crm_backend/tests/test_a.py"
    assert _gh_eval(tests_expr, over) == "sorento_crm_backend/tests/test_a.py"


def test_selection_runs_only_the_changed_files_job_on_a_ci_pr():
    """Under or above the budget alike: the changed-files job, never the shards."""
    ran = _ran(_simulate("pull_request", outputs=TESTS_ONLY_OUTPUTS))
    assert ran == GATES_ON_PR


STUB_PYTHON = """#!/usr/bin/env bash
echo "$*" >> "$PY_LOG"
case "$*" in *"$PY_FAIL_ON"*) [ -n "$PY_FAIL_ON" ] && exit 1 ;; esac
case "$*" in *serial_ddl*) [ "${PY_NO_SERIAL:-1}" = "1" ] && [[ "$*" != *"not serial_ddl"* ]] && exit 5 ;; esac
exit 0
"""


def _run_changed_step(tmp_path: Path, files: str, **env) -> tuple[int, list[str]]:
    stub = tmp_path / "pybin"
    stub.mkdir(exist_ok=True)
    (stub / "python").write_text(STUB_PYTHON)
    (stub / "python").chmod(0o755)
    log = tmp_path / "py.log"
    log.write_text("")
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _step_script("Run the changed test files (executing gate)")],
        env={"PATH": f"{stub}:{os.environ.get('PATH', '/usr/bin:/bin')}", "FILES": files, "PY_LOG": str(log), **env},
        capture_output=True,
        text=True,
    )
    return result.returncode, log.read_text().splitlines()


def test_changed_files_run_in_the_shards_two_pass_split(tmp_path):
    rc, calls = _run_changed_step(tmp_path, "tests/test_a.py tests/test_migration_400_x.py tests/chatbot/test_b.py")
    assert rc == 0, calls
    assert len(calls) == 3, calls
    assert "-n auto --dist loadfile" in calls[0] and "not serial_ddl" in calls[0]
    assert calls[0].endswith("tests/test_a.py tests/chatbot/test_b.py")
    assert "-p no:xdist" in calls[1] and "-m serial_ddl" in calls[1] and "test_migration" not in calls[1]
    assert "-p no:xdist" in calls[2] and calls[2].endswith("tests/test_migration_400_x.py")


def test_scm_files_never_share_a_session_with_the_main_tree(tmp_path):
    """#1411's selection: tests/test_product_discontinued_at_list.py imports
    tests.scm.conftest by name, and in one session with tests/scm files pytest
    then never registers it as tests/scm's conftest (`fixture 'scm_app' not
    found`). The shards never mix the trees; neither may this job."""
    rc, calls = _run_changed_step(tmp_path, "tests/test_a.py tests/scm/test_s.py tests/test_b.py")
    assert rc == 0 and len(calls) == 4, calls
    assert calls[0].endswith("tests/test_a.py tests/test_b.py") and "tests/scm" not in calls[0]
    assert calls[1].endswith("tests/test_a.py tests/test_b.py") and "-m serial_ddl" in calls[1]
    assert calls[2].endswith("tests/scm/test_s.py") and "--dist loadfile" in calls[2]
    assert calls[3].endswith("tests/scm/test_s.py") and "-p no:xdist" in calls[3]


def test_changed_files_step_fails_on_any_pass_but_runs_them_all(tmp_path):
    rc, calls = _run_changed_step(tmp_path, "tests/test_a.py tests/test_migration_400_x.py", PY_FAIL_ON="loadfile")
    assert rc == 1 and len(calls) == 3
    rc, calls = _run_changed_step(tmp_path, "tests/test_migration_400_x.py", PY_FAIL_ON="test_migration")
    assert rc == 1 and len(calls) == 1


def test_changed_files_step_with_only_migrations_skips_the_pool(tmp_path):
    rc, calls = _run_changed_step(tmp_path, "tests/test_migration_400_x.py")
    assert rc == 0 and len(calls) == 1 and "test_migration_400_x.py" in calls[0]


def test_serial_ddl_steps_have_their_own_matrix_entry_off_the_balanced_shards():
    """CI-SPEED: the serial steps no longer ride on shard 1, which run 36875899060
    measured as the release's critical path (43.8 min vs 14-22 for shards 2-6)."""
    text = _workflow_text()
    block = re.search(r"\n  test-backend:\n(.*?)\n  [a-z][\w-]*:\n", text, re.S).group(1)
    # Ten balanced shards since 11 Oct 2026: six ran 29-59 min (about 290 runner-minutes in
    # all) on the private-repo runners, so ten land near 30 each.
    assert re.search(r"^        shard: \[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, serial\]$", block, re.M)
    steps = {s.splitlines()[0]: s for s in re.split(r"\n      - name: ", block)[1:]}
    xdist = steps["Run backend suite under xdist (executing gate)"]
    assert "        if: matrix.shard != 'serial'" in xdist and "--splits 10 --group ${{ matrix.shard }}" in xdist
    assert "matrix.shard != 'serial'" in steps["Upload backend shard durations artifact"]
    for name in ("Run migration tests serially (executing gate)", "Run view/DDL tests serially (executing gate)"):
        assert "        if: matrix.shard == 'serial'" in steps[name], name
    assert "matrix.shard == 1" not in block


def test_no_test_workflow_runs_on_a_schedule():
    """CI-LEAN (owner, 10 Oct 2026): no nightly test of any kind. The never-stuck smoke
    (about 30 billed minutes a night) is manual-only, like the deploy.yml full suite."""
    smoke = WORKFLOW.parent / "never-stuck-nightly.yml"
    if not smoke.exists():
        pytest.skip(f"{smoke} not present (in-image run)")
    text = smoke.read_text(encoding="utf-8")
    triggers = text[text.index("\non:\n"):text.index("\nconcurrency:\n")]
    assert not re.search(r"^  schedule:", triggers, re.M)
    assert re.search(r"^  workflow_dispatch:", triggers, re.M)
    assert not re.search(r"^  schedule:", _triggers(), re.M)


# ---------------------------------------------------------------------------
# CI-LEAN: the lean release (core tier + what the batch reaches, one job).
# ---------------------------------------------------------------------------

LEAN_RELEASE = {
    "docs_only": "false", "backend": "true", "frontend": "false", "mcp": "false", "scm": "true",
    "backend_tests": "sorento_crm_backend/tests/test_rbac.py", "backend_code": "", "lean": "true",
}


def test_a_lean_release_runs_the_release_tests_job_never_the_shards():
    ran = _ran(_simulate("workflow_dispatch", outputs=LEAN_RELEASE))
    assert ran == {"check-migration-heads", "changes", "lean-gate", "build-images", "build-and-deploy"}
    over = _ran(_simulate("workflow_dispatch", outputs={**LEAN_RELEASE, "lean": "false"}))
    assert "lean-gate" not in over and {"test-backend", "test-backend-scm"} <= over, "over the cap: the full shards"


def test_a_red_release_tests_job_blocks_the_deploy():
    assert "lean-gate" in _job_ifs()["build-and-deploy"]["needs"]
    results = _simulate("workflow_dispatch", outputs=LEAN_RELEASE, forced={"lean-gate": "failure"})
    assert results["build-and-deploy"] == "skipped"


def test_the_release_tests_job_reports_as_release_tests():
    name = _expression(re.search(r"^    name: (.*)$", _job_block("lean-gate"), re.M).group(1))
    assert _gh_eval(name, {"github": {"event_name": "workflow_dispatch", "event": {}}}) == "Release tests"


def _changes_outputs() -> dict[str, str]:
    block = re.search(r"\n  changes:\n(.*?)\n    steps:", _workflow_text(), re.S).group(1)
    outputs = block[block.index("\n    outputs:\n"):]
    return {k: _expression(v) for k, v in re.findall(r"^      ([a-z_]+): (.*)$", outputs, re.M)}


def test_the_lean_flag_survives_a_selection_over_the_budget():
    """CI-COST: release run 38099410256 (main f7e48144b) printed lean=true, but the
    import selection reached 2076 files (32731 CPU-s, over the 3600 budget), the
    `lean` job output read `select.full`, turned false, and the 10 main shards,
    3 SCM shards, 4 vitest shards and the serial shard ran: ~468 billed minutes.
    The full shards now run only when the filter step itself says not lean
    (full_suite, or a release whose diff cannot be established)."""
    lean = _changes_outputs()["lean"]
    ctx = lambda flt, full: {"steps": {"filter": {"outputs": {"lean": flt}}, "select": {"outputs": {"full": full}}}}
    assert str(_gh_eval(lean, ctx("true", "false"))).lower() == "true"
    assert str(_gh_eval(lean, ctx("true", "true"))).lower() == "true", "over the budget is still a lean release"
    assert str(_gh_eval(lean, ctx("", ""))).lower() == "false"
    assert str(_gh_eval(lean, ctx("", "true"))).lower() == "false"


def test_a_lean_release_over_the_budget_runs_core_tier_and_changed_tests_in_one_job(tmp_path):
    """The batch of release 38099410256 reaches every test through app/models/__init__.py.
    Over the budget, the release-tests job gets the core tier and the batch's changed test
    files (each PR already ran its own import selection in its PR gate), and no shard runs."""
    output = tmp_path / "select.txt"
    output.write_text("")
    changed_test = "sorento_crm_backend/tests/chatbot/test_chat_backfill.py"
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _step_script("Select backend tests by import")],
        cwd=REPO,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GITHUB_OUTPUT": str(output),
             "BACKEND_CODE": "sorento_crm_backend/app/models/__init__.py", "BACKEND_TESTS": changed_test,
             "LEAN": "true"},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    step = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert step["full"] == "true", "app/models/__init__.py reaches the whole suite"
    tests = step["tests"].split()
    assert changed_test in tests
    assert "sorento_crm_backend/tests/test_rbac.py" in tests, "the core tier runs"
    assert len(tests) < 100, f"core tier + changed tests only, got {len(tests)} files"
    ctx = {"steps": {"filter": {"outputs": {"lean": "true", "backend_tests": changed_test}},
                     "select": {"outputs": step}}}
    rendered = {k: str(_gh_eval(v, ctx)).lower() if k == "lean" else str(_gh_eval(v, ctx))
                for k, v in _changes_outputs().items() if k in ("lean", "backend_tests")}
    assert rendered["lean"] == "true"
    assert rendered["backend_tests"] == step["tests"]
    ran = _ran(_simulate("workflow_dispatch", outputs={**LEAN_RELEASE, **rendered}))
    assert ran == {"check-migration-heads", "changes", "lean-gate", "build-images", "build-and-deploy"}


def test_full_suite_input_states_the_measured_cost():
    """The last full run (38099410256) billed ~468 minutes, not the ~250 the input claimed."""
    desc = re.search(r"^      full_suite:\n        description: \"(.*)\"$", _triggers(), re.M).group(1)
    assert "470 billed minutes" in desc


def test_a_passed_release_tests_job_marks_its_tree_validated():
    steps = _lean_gate_steps()
    mark = steps["Mark this tree as validated"]
    assert "github.event_name == 'workflow_dispatch'" in mark and "success()" in mark
    assert "validated-tree-" in "\n".join(steps.values())


def test_the_core_tier_lists_existing_test_files_only():
    core = REPO / "sorento_crm_backend" / "tests" / "release_core.txt"
    lines = [l.strip() for l in core.read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("#")]
    assert len(lines) >= 20
    for rel in lines:
        assert re.match(r"^tests/([A-Za-z0-9_.-]+/)*test_[A-Za-z0-9_.-]*\.py$", rel), rel
        assert (REPO / "sorento_crm_backend" / rel).is_file(), rel


def test_the_select_step_adds_the_core_tier_on_a_lean_release(tmp_path):
    output = tmp_path / "select.txt"
    output.write_text("")
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _step_script("Select backend tests by import")],
        cwd=REPO,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GITHUB_OUTPUT": str(output),
             "BACKEND_CODE": "", "BACKEND_TESTS": "", "LEAN": "true"},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert "sorento_crm_backend/tests/chatbot/test_turn_replay.py" in outputs["tests"].split()
    assert outputs["full"] == "false"
    block = re.search(r"\n  changes:\n(.*?)\n  [a-z][\w-]*:\n", _workflow_text(), re.S).group(1)
    assert "steps.filter.outputs.lean == 'true'" in block, "the checkout and select steps run on a lean release"


def test_full_suite_is_a_release_input():
    assert re.search(r"^      full_suite:$", _triggers(), re.M)


# ---------------------------------------------------------------------------
# CI-COST round 2 (crew decision (c)): a PR gate that passed marks its merge
# tree; a re-label on the same tree (1.15 runs per head, 4-11 Oct) is a no-op.
# ---------------------------------------------------------------------------

def test_a_pr_whose_merge_tree_already_passed_tests_nothing(tmp_path):
    out = run_step(tmp_path, "pull_request", ["sorento_crm_backend/app/services/inventory_service.py",
                                              "sorento_crm_frontend/app/page.tsx"], pr_validated=True)
    assert out == {**NO_AREA, "docs_only": "false", "validated": "true"}


def test_a_pr_without_a_marker_is_tested_as_before(tmp_path):
    out = run_step(tmp_path, "pull_request", ["sorento_crm_backend/app/services/inventory_service.py"])
    assert out["backend"] == "true" and "validated" not in out
    failed = run_step(tmp_path, "pull_request", ["sorento_crm_backend/app/services/inventory_service.py"], gh_fails=True)
    assert failed["backend"] == "true" and "validated" not in failed, "a failed lookup tests"


def test_a_pr_marker_never_skips_a_release(tmp_path):
    """The release keeps its own tests: a PR marker has its own name prefix."""
    out = run_step(tmp_path, "workflow_dispatch", RELEASE_BATCH, last_release=LAST_RELEASE, pr_validated=True)
    assert out["lean"] == "true" and out["backend"] == "true" and "validated" not in out
    upload = _lean_gate_steps()["Upload the validated-tree marker"]
    assert "'pr-validated-tree-'" in upload and "'validated-tree-'" in upload
    detect = _step_script("Detect changed areas")
    assert "name=validated-tree-$tree" in detect and "name=pr-validated-tree-$tree" in detect


def test_only_a_fully_green_pr_gate_marks_its_tree():
    names = list(_lean_gate_steps())
    steps = _lean_gate_steps()
    for marker in ("Mark this tree as validated", "Upload the validated-tree marker"):
        cond = re.search(r"^\s*if: (.*)$", steps[marker], re.M).group(1)
        assert cond.startswith("success() && "), cond
        assert "github.event_name == 'pull_request'" in cond and "needs.changes.outputs.validated != 'true'" in cond
        assert "github.event_name == 'workflow_dispatch'" in cond
    assert names[-2:] == ["Mark this tree as validated", "Upload the validated-tree marker"], "after every check"


def test_a_validated_pr_skips_the_fast_gates_too():
    block = re.search(r"\n  changes:\n(.*?)\n    steps:", _workflow_text(), re.S).group(1)
    assert re.search(r"^      validated: \$\{\{ steps\.filter\.outputs\.validated \}\}$", block, re.M)
    steps = _lean_gate_steps()
    for name in ("Single alembic head", "No personal data in tracked files"):
        assert "needs.changes.outputs.validated != 'true'" in steps[name], name


def test_a_validated_pr_still_scans_its_commits_for_secrets():
    """The marker is keyed on the merge TREE, gitleaks scans commit HISTORY: a commit
    adding a secret and a second reverting it leave a tree that already passed, and a
    merge-commit merge lands both (reviewer, CI-COST round 2). The scan always runs."""
    steps = _lean_gate_steps()
    cond = re.search(r"^\s*if: (.*)$", steps["Secret scan (gitleaks)"], re.M).group(1).strip()
    assert cond == "github.event_name == 'pull_request'", cond
    checkout = steps["Checkout"]
    depth = re.search(r"fetch-depth: (.*)$", checkout, re.M).group(1)
    assert "validated" not in depth, "full history on every PR, the scan needs base..head"


def test_the_pr_marker_name_is_never_the_release_marker():
    upload = _lean_gate_steps()["Upload the validated-tree marker"]
    name = re.search(r"^\s*name: (.*)$", upload, re.M).group(1).strip()
    tree = "${{ steps.tree.outputs.tree }}"
    assert name.endswith(tree), name
    prefix = _expression(name[: -len(tree)])
    assert _gh_eval(prefix, {"github": {"event_name": "pull_request"}}) == "pr-validated-tree-"
    assert _gh_eval(prefix, {"github": {"event_name": "workflow_dispatch"}}) == "validated-tree-"
