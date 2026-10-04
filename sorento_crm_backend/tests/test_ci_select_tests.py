"""scripts/ci_select_tests.py: which backend test files a PR's CI runs (CI-SPEED).

Before this, a PR ran only the test files it changed. #1411 changed
app/services/shipping_order_ingest_service.py and rules/shipping_order_rules.py;
tests/test_ingest_parity_security_fixes.py imports the first (line 42) and broke
in the release, its first execution. The script adds every test file that
imports a changed backend module, directly or through one importing module.

Pure filesystem work, no database: the cases build a scratch backend tree, and
one case runs against this checkout with #1411's real file list.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

import ci_select_tests as sel  # noqa: E402

P = "sorento_crm_backend/"


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "sorento_crm_backend"
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return root


BASE = {
    "app/__init__.py": "",
    "app/services/__init__.py": "",
    "app/services/foo.py": "def f():\n    return 1\n",
    "app/services/bar.py": "from app.services.foo import f\n",
    "app/services/baz.py": "from . import bar\n",
    "app/services/qux.py": "X = 1\n",
    "tests/__init__.py": "",
    "tests/conftest.py": "",
    "tests/test_direct.py": "from app.services.foo import f\n",
    "tests/test_module_import.py": "import app.services.foo as foo\n",
    "tests/test_from_package.py": "from app.services import foo\n",
    "tests/test_patch_string.py": "def test_x(monkeypatch):\n    monkeypatch.setattr('app.services.foo.f', lambda: 2)\n",
    "tests/test_via_bar.py": "from app.services.bar import f\n",
    "tests/test_via_baz.py": "from app.services import baz\n",
    "tests/test_unrelated.py": "from app.services.qux import X\n",
    "tests/test_prefix_lookalike.py": "import app.services.foobar\n",
    "tests/_helpers.py": "from app.services.foo import f\n",
    "tests/test_via_helper.py": "from tests._helpers import f\n",
    "tests/scm/__init__.py": "",
    "tests/scm/test_scm_direct.py": "from app.services.foo import f\n",
}


def test_direct_and_one_level_importers_are_selected(tmp_path):
    root = _tree(tmp_path, BASE)
    result = sel.select([P + "app/services/foo.py"], root, scm_runs=False)
    assert result.full is False
    assert result.tests == sorted(
        P + "tests/" + name
        for name in (
            "scm/test_scm_direct.py",
            "test_direct.py",
            "test_from_package.py",
            "test_module_import.py",
            "test_patch_string.py",
            "test_via_bar.py",
            "test_via_helper.py",
        )
    )


def test_two_levels_away_is_not_selected(tmp_path):
    """baz imports bar imports foo: transitive one level only."""
    root = _tree(tmp_path, BASE)
    result = sel.select([P + "app/services/foo.py"], root, scm_runs=False)
    assert P + "tests/test_via_baz.py" not in result.tests
    assert P + "tests/test_unrelated.py" not in result.tests
    assert P + "tests/test_prefix_lookalike.py" not in result.tests


def test_relative_import_counts_as_an_importer(tmp_path):
    root = _tree(tmp_path, BASE)
    result = sel.select([P + "app/services/bar.py"], root, scm_runs=False)
    assert P + "tests/test_via_baz.py" in result.tests
    assert P + "tests/test_via_bar.py" in result.tests


def test_scm_tests_are_left_to_the_scm_shards_when_they_run(tmp_path):
    root = _tree(tmp_path, BASE)
    result = sel.select([P + "app/services/foo.py"], root, scm_runs=True)
    assert P + "tests/scm/test_scm_direct.py" not in result.tests
    assert P + "tests/test_direct.py" in result.tests


def test_changed_test_files_are_always_selected(tmp_path):
    root = _tree(tmp_path, BASE)
    result = sel.select(
        [P + "tests/test_unrelated.py", P + "tests/scm/test_scm_direct.py", P + "tests/_helpers.py"],
        root,
        scm_runs=True,
    )
    # A changed helper module selects its importers, a changed scm test is the
    # SCM shards' job, and a non-test file is never a pytest argument.
    assert result.tests == [P + "tests/test_unrelated.py", P + "tests/test_via_helper.py"]


def test_a_changed_sub_conftest_selects_its_directory(tmp_path):
    files = dict(BASE)
    files["tests/scm/conftest.py"] = ""
    files["tests/scm/test_scm_other.py"] = "X = 1\n"
    root = _tree(tmp_path, files)
    result = sel.select([P + "tests/scm/conftest.py"], root, scm_runs=False)
    assert result.tests == [P + "tests/scm/test_scm_direct.py", P + "tests/scm/test_scm_other.py"]
    assert result.full is False


def test_a_changed_root_conftest_runs_the_full_shards(tmp_path):
    root = _tree(tmp_path, BASE)
    result = sel.select([P + "tests/conftest.py"], root, scm_runs=False, durations={})
    assert result.full is True


def test_a_script_loaded_by_path_or_bare_name_is_selected(tmp_path):
    files = dict(BASE)
    files["scripts/tool_x.py"] = "def run():\n    pass\n"
    files["tests/test_by_path.py"] = "spec = spec_from_file_location('t', ROOT / 'scripts' / 'tool_x.py')\n"
    files["tests/test_by_name.py"] = "sys.path.insert(0, 'scripts')\nimport tool_x\n"
    files["tests/test_not_it.py"] = "import tool_xy\n"
    root = _tree(tmp_path, files)
    result = sel.select([P + "scripts/tool_x.py"], root, scm_runs=False)
    assert result.tests == [P + "tests/test_by_name.py", P + "tests/test_by_path.py"]


def test_this_script_selects_its_own_test(tmp_path):
    result = sel.select([P + "scripts/ci_select_tests.py"], BACKEND, scm_runs=False)
    assert P + "tests/test_ci_select_tests.py" in result.tests


def test_non_python_and_non_backend_paths_select_nothing(tmp_path):
    root = _tree(tmp_path, BASE)
    result = sel.select(
        ["sorento_crm_frontend/app/page.tsx", P + "app/templates/x.html", "documentation/x.md"],
        root,
        scm_runs=False,
    )
    assert result.tests == [] and result.full is False


def test_deleted_module_still_selects_what_imports_it(tmp_path):
    files = dict(BASE)
    del files["app/services/foo.py"]
    root = _tree(tmp_path, files)
    result = sel.select([P + "app/services/foo.py"], root, scm_runs=False)
    assert P + "tests/test_direct.py" in result.tests


def _many(tmp_path: Path, n: int) -> Path:
    files = dict(BASE)
    for i in range(n):
        files[f"tests/test_many_{i:02d}.py"] = "from app.services.qux import X\n"
    return _tree(tmp_path, files)


def test_budget_is_estimated_cpu_seconds_and_full_only_above_it(tmp_path):
    """test_unrelated + 3 test_many files import qux, at 10 s each = 40 CPU-s."""
    root = _many(tmp_path, 3)
    durations = {"tests/test_unrelated.py": 10.0, **{f"tests/test_many_{i:02d}.py": 10.0 for i in range(3)}}
    at = sel.select([P + "app/services/qux.py"], root, scm_runs=False, budget=40.0, durations=durations)
    assert at.count == 4 and at.seconds == pytest.approx(40.0)
    assert at.full is False, "at the budget is still under it"
    over = sel.select([P + "app/services/qux.py"], root, scm_runs=False, budget=39.9, durations=durations)
    assert over.full is True and over.count == 4
    assert over.tests == at.tests, "the selection is still reported above the budget"


def test_file_count_alone_never_trips_the_budget(tmp_path):
    root = _many(tmp_path, 40)
    durations = {f"tests/test_many_{i:02d}.py": 0.5 for i in range(40)}
    result = sel.select([P + "app/services/qux.py"], root, scm_runs=False, budget=30.0, durations=durations)
    assert result.count == 41 and result.full is False


def test_a_file_without_history_counts_at_the_median_file(tmp_path):
    root = _many(tmp_path, 2)
    # Known: 1, 5, 100 -> median 5. test_many_01 has no history.
    durations = {"tests/test_unrelated.py": 1.0, "tests/test_many_00.py": 100.0, "tests/test_other.py": 5.0}
    result = sel.select([P + "app/services/qux.py"], root, scm_runs=False, durations=durations)
    assert result.seconds == pytest.approx(1.0 + 100.0 + 5.0)
    assert sel.estimate_seconds([P + "tests/x.py", P + "tests/y.py"], {"a": 1.0, "b": 2.0, "c": 4.0, "d": 9.0}) == pytest.approx(6.0)


def test_durations_file_is_summed_per_file(tmp_path):
    root = _tree(tmp_path, {".test_durations": '{"tests/test_a.py::test_1": 1.5, "tests/test_a.py::C::test_2": 2.0, "tests/test_b.py::t": 4}'})
    assert sel.file_durations(root) == {"tests/test_a.py": 3.5, "tests/test_b.py": 4.0}
    assert sel.file_durations(tmp_path / "nowhere") == {}


def test_a_path_a_shell_would_parse_is_never_selected(tmp_path):
    files = dict(BASE)
    files["tests/test_bad name.py"] = "from app.services.foo import f\n"
    root = _tree(tmp_path, files)
    result = sel.select([P + "app/services/foo.py"], root, scm_runs=False)
    assert all(" " not in t for t in result.tests)


def test_cli_above_the_budget_writes_no_list_and_full_true(tmp_path):
    files = dict(BASE)
    files[".test_durations"] = '{"tests/test_direct.py::t": 3.0}'
    root = _tree(tmp_path, files)
    out = tmp_path / "gh_output"
    out.write_text("")
    proc = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "ci_select_tests.py"), "--root", str(root), "--budget", "2"],
        input=f"{P}app/services/foo.py\n",
        env={"GITHUB_OUTPUT": str(out), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert dict(line.split("=", 1) for line in out.read_text().splitlines()) == {"tests": "", "full": "true"}
    # 7 files: test_direct at 3 s, six unknown at the median (3 s) = 21 CPU-s.
    assert "7 test files, estimated 21 CPU-s (budget 2): above the budget" in proc.stdout
    assert f"{P}tests/test_direct.py" in proc.stdout


def test_cli_writes_the_outputs_and_logs_the_list(tmp_path):
    root = _tree(tmp_path, BASE)
    out = tmp_path / "gh_output"
    out.write_text("")
    proc = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "ci_select_tests.py"), "--root", str(root), "--scm", "false"],
        input=f"{P}app/services/qux.py\n{P}tests/test_direct.py\n",
        env={"GITHUB_OUTPUT": str(out), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert outputs == {
        "tests": f"{P}tests/test_direct.py {P}tests/test_unrelated.py",
        "full": "false",
    }
    assert f"{P}tests/test_unrelated.py" in proc.stdout
    assert "2 test files, estimated 0 CPU-s (budget 3600)" in proc.stdout


# #1411 (123d4282c) as GitHub listed it. The test that broke the 1 Oct release
# imports the ingest service at module level; the PR ran only the three test
# files it changed.
PR_1411 = [
    "documentation/plans/autocount/PLAN-spo-xlsx-product-fallback.md",
    "documentation/plans/autocount/spo-xlsx-product-fallback-acceptance-criteria.md",
    P + "app/services/grn_spo_matching.py",
    P + "app/services/incoming_stock_service.py",
    P + "app/services/procurement_service.py",
    P + "app/services/rules/shipping_order_rules.py",
    P + "app/services/shipping_order_ingest_service.py",
    P + "scripts/dedupe_spo_xlsx_superseded.py",
    P + "scripts/oneoff/dedupe_spo_standalone.py",
    P + "tests/test_dedupe_spo_standalone.py",
    P + "tests/test_spo_xlsx_product_fallback.py",
    P + "tests/test_spo_xlsx_supersede.py",
]


@pytest.mark.skipif(not (BACKEND / "app").is_dir(), reason="needs the backend tree")
def test_pr_1411_selects_the_test_that_broke_the_release():
    """#1411 touched procurement_service, which ~60 test files import directly:
    ~216 files, ~2668 CPU-s by the committed durations, under the 3600 budget,
    so the selected list itself runs on the PR, and it holds the file."""
    result = sel.select(PR_1411, BACKEND, scm_runs=False)
    assert P + "tests/test_ingest_parity_security_fixes.py" in result.tests
    for changed in PR_1411[-3:]:
        assert changed in result.tests
    # 216 files, ~2668 CPU-s when this was written: under the budget, so the
    # list itself runs. Not asserted, the tree and the durations move.
    print(f"#1411: {result.count} files, {result.seconds:.0f} CPU-s, full={result.full}")
