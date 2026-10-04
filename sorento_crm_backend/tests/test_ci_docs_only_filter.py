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
3. Fast PR gate + queue (the jobs' `if` lines and the workflow `concurrency`):
   a `ci`-labelled PR runs the fast gates and the SCM shards only when SCM
   paths changed, the six backend shards run in the merge queue and in a
   release, and only a `ci` label run joins the per-PR concurrency group.
4. Changed tests on a PR (`backend_tests` output of `changes`, and job
   `test-backend-changed` with its "Select the changed test files to run"
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


def run_step(
    tmp_path: Path,
    event: str,
    files: list,
    *,
    gh_fails: bool = False,
    before: str = BEFORE,
    skip_tests: str = "",
) -> dict[str, str]:
    """Run the real step with `files` as the API answer; return $GITHUB_OUTPUT.

    Each entry of `files` is a path, or a (new_path, previous_path) pair for a
    rename, shaped into the PR-files array or the compare object exactly as the
    GitHub API returns them.
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
    }
    env = {
        "EVENT": event, "PR": "1", "PUSH_BEFORE": before, "PUSH_AFTER": SHA,
        "MG_BASE": "c" * 40, "MG_HEAD": "d" * 40, "SKIP_TESTS": skip_tests,
    }
    outputs = _run_shell(tmp_path, _step_script("Detect changed areas"), env, fixtures, gh_fails=gh_fails)
    assert set(outputs) == AREA_KEYS, outputs
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


def test_release_always_tests_the_whole_head(tmp_path):
    """A dispatch has no diff: every area counts, docs_only is never true."""
    assert run_step(tmp_path, "workflow_dispatch", DOCS_ONLY) == FULL_PIPELINE
    assert run_step(tmp_path, "workflow_dispatch", [], skip_tests="false") == FULL_PIPELINE


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
        # the SCM shards' job runs these whenever `scm` is true
        "sorento_crm_backend/tests/scm/test_committed_v.py",
        "sorento_crm_backend/tests/scm/nested/test_x.py",
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


def test_scm_test_change_runs_the_scm_shards_not_the_changed_files_job(tmp_path):
    out = run_step(tmp_path, "pull_request", ["sorento_crm_backend/tests/scm/test_committed_v.py"])
    assert out["scm"] == "true" and out["backend_tests"] == ""
    both = run_step(tmp_path, "pull_request", [
        "sorento_crm_backend/tests/scm/test_committed_v.py", "sorento_crm_backend/tests/test_rbac.py",
    ])
    assert both["scm"] == "true" and both["backend_tests"] == "sorento_crm_backend/tests/test_rbac.py"


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


# The "Select the changed test files to run" step of `test-backend-changed`,
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


def test_changed_tests_job_steps_after_select_gate_on_its_output():
    """Every step after the select step, pytest included, skips on an empty list."""
    text = _workflow_text()
    block = re.search(r"\n  test-backend-changed:\n(.*?)\n  [a-z][\w-]*:\n", text, re.S).group(1)
    steps = re.split(r"\n      - name: ", block)[1:]
    names = [s.splitlines()[0] for s in steps]
    assert names[:2] == ["Checkout", "Select the changed test files to run"], names
    for step in steps[2:]:
        assert "        if: steps.select.outputs.files != ''" in step, step.splitlines()[0]
    assert "--dist loadfile" in steps[-1] and "$FILES" in steps[-1], "runs the selected files via env, under xdist"


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


GATES_ON_PR = {"check-migration-heads", "changes", "release-ci-label", "pii-guard", "gitleaks"}


def test_pull_request_fast_gate_backend_with_scm_paths():
    ran = _ran(_simulate("pull_request", outputs={"backend": "true", "scm": "true", "frontend": "false", "mcp": "false", "docs_only": "false"}))
    assert ran == GATES_ON_PR | {"validate-backend", "test-backend-scm"}
    assert "test-backend" not in ran, "the six backend shards never run on a pull_request"


def test_pull_request_fast_gate_backend_without_scm_paths():
    ran = _ran(_simulate("pull_request", outputs={"backend": "true", "scm": "false", "frontend": "false", "mcp": "false", "docs_only": "false"}))
    assert ran == GATES_ON_PR | {"validate-backend"}


def test_pull_request_fast_gate_frontend_and_mcp():
    ran = _ran(_simulate("pull_request", outputs={"backend": "false", "scm": "false", "frontend": "true", "mcp": "true", "docs_only": "false"}))
    assert ran == GATES_ON_PR | {"validate-frontend", "typecheck-frontend", "validate-mcp"}


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
    assert ran == GATES_ON_PR | {"validate-backend", "test-backend-changed"}
    assert "test-backend" not in ran


def test_pull_request_with_code_and_a_test_file_runs_both_gates():
    outputs = {**TESTS_ONLY_OUTPUTS, "scm": "true", "frontend": "true"}
    ran = _ran(_simulate("pull_request", outputs=outputs))
    assert ran == GATES_ON_PR | {
        "validate-backend", "test-backend-scm", "test-backend-changed", "validate-frontend", "typecheck-frontend",
    }


def test_changed_tests_job_never_runs_off_a_pull_request_or_with_another_label():
    for event in ("merge_group", "workflow_dispatch", "push"):
        results = _simulate(event, outputs={**FULL_PIPELINE, "backend_tests": "sorento_crm_backend/tests/test_x.py", "backend_code": ""})
        assert results["test-backend-changed"] == "skipped", (event, results)
    for label in ("needs-hand-test", "lane-running"):
        results = _simulate("pull_request", label=label, outputs=TESTS_ONLY_OUTPUTS)
        assert results["test-backend-changed"] == "skipped", label


def test_pull_request_with_another_label_runs_nothing():
    for label in ["needs-hand-test", "lane-running", "needs-decision"]:
        assert _ran(_simulate("pull_request", label=label)) == set(), label


def test_merge_group_runs_the_full_suite_and_never_deploys():
    ran = _ran(_simulate("merge_group", outputs=FULL_PIPELINE))
    assert ran == {
        "check-migration-heads", "pii-guard", "changes", "validate-backend", "validate-mcp",
        "test-backend-scm", "test-backend", "validate-frontend", "typecheck-frontend",
    }


RELEASE_ONLY = {"check-migration-heads", "pii-guard", "changes", "build-images", "build-and-deploy", "notify-owner"}
FULL_SUITE = {
    "check-migration-heads", "pii-guard", "changes", "validate-backend", "validate-mcp",
    "test-backend-scm", "test-backend", "validate-frontend", "typecheck-frontend",
}


def test_push_to_main_runs_the_alembic_gate_and_nothing_else():
    """Owner decision 29 Sep 2026: a merge does not test or deploy."""
    for outputs in (FULL_PIPELINE, {**NO_AREA, "docs_only": "true"}):
        results = _simulate("push", outputs=outputs)
        assert _ran(results) == {"check-migration-heads", "pii-guard", "gitleaks"}, results
        assert results["changes"] == "skipped"
        assert results["build-images"] == "skipped"
        assert results["build-and-deploy"] == "skipped"
        assert results["notify-owner"] == "skipped"


def test_release_runs_the_full_suite_then_builds_and_deploys():
    ran = _ran(_simulate("workflow_dispatch", outputs=FULL_PIPELINE))
    assert ran == FULL_SUITE | RELEASE_ONLY


def test_release_with_skip_tests_builds_and_deploys_only():
    ran = _ran(_simulate("workflow_dispatch", outputs={"docs_only": "false", **NO_AREA}))
    assert ran == RELEASE_ONLY


def test_release_dispatched_on_another_ref_tests_but_never_ships():
    results = _simulate("workflow_dispatch", outputs=FULL_PIPELINE, ref="refs/heads/crew/ci-fast-gate")
    assert _ran(results) == FULL_SUITE
    assert results["build-images"] == "skipped"
    assert results["build-and-deploy"] == "skipped"
    assert results["notify-owner"] == "skipped"


@pytest.mark.parametrize("failed", ["test-backend", "test-backend-scm", "validate-frontend", "typecheck-frontend",
                                    "validate-backend", "validate-mcp", "check-migration-heads", "build-images"])
def test_release_with_a_failed_gate_does_not_deploy_but_still_notifies(failed):
    results = _simulate("workflow_dispatch", outputs=FULL_PIPELINE, forced={failed: "failure"})
    assert results["build-and-deploy"] == "skipped", failed
    assert results["notify-owner"] == "success", "the owner is mailed about the failed release"


def test_release_with_skip_tests_and_a_failed_build_does_not_deploy():
    for failed in ("build-images", "check-migration-heads"):
        results = _simulate("workflow_dispatch", outputs={"docs_only": "false", **NO_AREA}, forced={failed: "failure"})
        assert results["build-and-deploy"] == "skipped", failed


def test_deploy_job_carries_the_production_environment():
    """The owner's approval gate: the only job that touches the server."""
    text = _workflow_text()
    block = re.search(r"\n  build-and-deploy:\n(.*?)\n  [a-z][\w-]*:\n", text, re.S).group(1)
    assert re.search(r"^    environment:\n      name: production$", block, re.M), block
    for other in ("build-images", "notify-owner", "test-backend", "changes"):
        other_block = re.search(rf"\n  {other}:\n(.*?)\n  [a-z][\w-]*:\n", text, re.S).group(1)
        assert "environment:" not in other_block, other


def test_merge_group_never_deploys_whatever_the_flags():
    results = _simulate("merge_group", outputs=FULL_PIPELINE)
    assert results["build-images"] == "skipped"
    assert results["build-and-deploy"] == "skipped"
    assert results["notify-owner"] == "skipped"


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


@pytest.mark.parametrize("label", ["needs-hand-test", "lane-running", "needs-decision", "ready-for-agent"])
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
    # Under the budget when this was written (216 files, ~2668 CPU-s); either
    # way the file runs on the PR: in the list, or in the full shards.
    if outputs["full"] == "false":
        selected = outputs["tests"].split()
        assert "sorento_crm_backend/tests/test_ingest_parity_security_fixes.py" in selected
        assert set(filtered["backend_tests"].split()) <= set(selected)
    else:
        assert outputs == {"tests": "", "full": "true"}
    assert "estimated" in result.stdout and "sorento_crm_backend/tests/test_ingest_parity_security_fixes.py" in result.stdout


def test_changes_job_outputs_prefer_the_selection():
    text = _workflow_text()
    block = re.search(r"\n  changes:\n(.*?)\n    steps:", text, re.S).group(1)
    tests_expr = _expression(re.search(r"^      backend_tests: (.*)$", block, re.M).group(1))
    full_expr = _expression(re.search(r"^      backend_full: (.*)$", block, re.M).group(1))
    filt = {"backend_tests": "sorento_crm_backend/tests/test_a.py"}
    ran = {"steps": {"select": {"outputs": {"tests": "sorento_crm_backend/tests/test_a.py sorento_crm_backend/tests/test_b.py", "full": "false"}}, "filter": {"outputs": filt}}}
    skipped = {"steps": {"select": {"outputs": {}}, "filter": {"outputs": filt}}}
    assert _gh_eval(tests_expr, ran) == "sorento_crm_backend/tests/test_a.py sorento_crm_backend/tests/test_b.py"
    assert _gh_eval(tests_expr, skipped) == "sorento_crm_backend/tests/test_a.py"
    assert _gh_eval(full_expr, ran) is False and _gh_eval(full_expr, skipped) is False
    over = {"steps": {"select": {"outputs": {"tests": "", "full": "true"}}, "filter": {"outputs": filt}}}
    assert _gh_eval(full_expr, over) is True


def test_selection_over_the_budget_runs_the_full_shards_on_the_pr():
    outputs = {**TESTS_ONLY_OUTPUTS, "backend_full": "true"}
    ran = _ran(_simulate("pull_request", outputs=outputs))
    assert ran == GATES_ON_PR | {"validate-backend", "test-backend", "test-backend-scm"}
    assert "test-backend-changed" not in ran


def test_selection_under_the_budget_runs_only_the_changed_files_job():
    for full in ("false", "", None):
        outputs = dict(TESTS_ONLY_OUTPUTS)
        if full is not None:
            outputs["backend_full"] = full
        ran = _ran(_simulate("pull_request", outputs=outputs))
        assert ran == GATES_ON_PR | {"validate-backend", "test-backend-changed"}, full


def test_backend_full_never_changes_a_release_or_the_queue():
    for event in ("merge_group", "workflow_dispatch"):
        assert _ran(_simulate(event, outputs={**FULL_PIPELINE, "backend_full": "false"})) == _ran(_simulate(event, outputs=FULL_PIPELINE))


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
    assert re.search(r"^        shard: \[1, 2, 3, 4, 5, 6, serial\]$", block, re.M)
    steps = {s.splitlines()[0]: s for s in re.split(r"\n      - name: ", block)[1:]}
    xdist = steps["Run backend suite under xdist (executing gate)"]
    assert "        if: matrix.shard != 'serial'" in xdist and "--splits 6 --group ${{ matrix.shard }}" in xdist
    assert "matrix.shard != 'serial'" in steps["Upload backend shard durations artifact"]
    for name in ("Run migration tests serially (executing gate)", "Run view/DDL tests serially (executing gate)"):
        assert "        if: matrix.shard == 'serial'" in steps[name], name
    assert "matrix.shard == 1" not in block
