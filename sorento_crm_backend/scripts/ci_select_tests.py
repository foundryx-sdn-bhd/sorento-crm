"""Pick the backend test files a pull request's CI runs (CI-SPEED, 1 Oct 2026).

Run by the `changes` job of .github/workflows/deploy.yml. A PR used to run only
the test files it changed; #1411 changed app/services/shipping_order_ingest_service.py
and tests/test_ingest_parity_security_fixes.py, which imports it, first ran in the
release and failed it. So the selection is now:

- every changed `tests/**/test_*.py` file;
- every test file that references a changed backend module (`app/`, `scripts/`,
  or a non-test helper under `tests/`), or references a module that itself
  imports one (one level of transitivity, no more).

"References" is any of: `import app.x`, `from app.x import y`, `from app import x`,
a relative import resolving to one of those, or the dotted name in the source text
(a `monkeypatch.setattr("app.x.y", ...)` or `mock.patch` target). A changed
`scripts/` file also matches a test that loads it by path or by bare name
(`sys.path.insert` + `import name`, `spec_from_file_location(".../name.py")`).

A conftest.py is never imported, so it is handled by place: a changed
`tests/<dir>/conftest.py` selects every test file under `tests/<dir>/`, and a
changed `tests/conftest.py`, the harness of every test, reports `full=true`.

The cap is time, not file count (crew ruling, 1 Oct 2026): the selection's
estimated CPU seconds, summed per file from the committed `.test_durations` (the
file the shards balance on; a file it does not hold counts at the median file),
above BUDGET_SECONDS reports `full=true` and no list: the six main shards and the
SCM shards then run on the PR instead. #1411 selects 216 files at ~2668 CPU-s,
under the budget; one changed-files runner under xdist -n 4 takes it in ~12 min. Files under tests/scm/ are left out
when the SCM shards run anyway (`--scm true`).

Stdlib only: the `changes` job runs it with the runner's own python3, before any
dependency install.

CLI: changed paths (repo-relative, one per line) on stdin; writes `tests=` (space
separated, repo-relative) and `full=` to $GITHUB_OUTPUT and prints the list.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

BUDGET_SECONDS = 3600.0
PREFIX = "sorento_crm_backend/"
SOURCE_DIRS = ("app", "scripts", "tests")
# Only paths a shell cannot misparse ever reach the pytest argument line, the
# same character class as BACKEND_TESTS_RE in the workflow.
SAFE_TEST_RE = re.compile(r"^tests/([A-Za-z0-9_.-]+/)*test_[A-Za-z0-9_.-]*\.py$")
DOTTED_RE = re.compile(r"(?<![\w.])((?:app|scripts|tests)(?:\.[A-Za-z_]\w*)+)")


@dataclass
class Selection:
    tests: list[str]  # the whole selection, also when `full` (printed, never run)
    full: bool  # above the budget: the full shards run instead of `tests`
    count: int
    seconds: float  # estimated CPU seconds of `tests`


def file_durations(root: Path) -> dict[str, float]:
    """`tests/x.py` -> summed seconds of its tests in `<root>/.test_durations`."""
    try:
        raw = json.loads((root / ".test_durations").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    per_file: dict[str, float] = {}
    for test_id, seconds in raw.items():
        name = test_id.split("::", 1)[0]
        per_file[name] = per_file.get(name, 0.0) + float(seconds)
    return per_file


def estimate_seconds(tests: list[str], per_file: dict[str, float]) -> float:
    """Sum of the files' durations; a file with no history counts at the median file."""
    known = sorted(per_file.values())
    median = 0.0
    if known:
        mid = len(known) // 2
        median = known[mid] if len(known) % 2 else (known[mid - 1] + known[mid]) / 2
    return sum(per_file.get(t[len(PREFIX):] if t.startswith(PREFIX) else t, median) for t in tests)


def _module_name(rel: str) -> str | None:
    """`app/services/foo.py` -> `app.services.foo`; a package's __init__ -> the package."""
    if not rel.endswith(".py"):
        return None
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


def _is_test_file(rel: str) -> bool:
    return rel.startswith("tests/") and Path(rel).name.startswith("test_") and rel.endswith(".py")


def _references(rel: str, text: str) -> set[str]:
    """Every dotted name `rel` may load, with all of its prefixes."""
    names: set[str] = set(DOTTED_RE.findall(text))
    package = (_module_name(rel) or "").split(".")
    if not rel.endswith("__init__.py"):
        package = package[:-1]
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    for node in ast.walk(tree) if tree else ():
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                base = ".".join(base_parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if not base:
                continue
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    expanded: set[str] = set()
    for name in names:
        parts = name.split(".")
        expanded.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
    return expanded


def _sources(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for top in SOURCE_DIRS:
        base = root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            try:
                out[rel] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return out


def select(
    changed: list[str],
    root: Path,
    *,
    scm_runs: bool,
    budget: float = BUDGET_SECONDS,
    durations: dict[str, float] | None = None,
) -> Selection:
    rel_changed = [p[len(PREFIX):] for p in changed if p.startswith(PREFIX)]
    picked = {r for r in rel_changed if _is_test_file(r)}
    changed_modules = {
        m for r in rel_changed
        if r.split("/", 1)[0] in SOURCE_DIRS and not _is_test_file(r)
        for m in [_module_name(r)] if m
    }
    conftest_dirs = {r.rsplit("/", 1)[0] for r in rel_changed if r.startswith("tests/") and r.endswith("/conftest.py")}
    scripts_by_path = {
        Path(r).stem: re.compile(rf"\b{re.escape(Path(r).name)}\b|\bimport {re.escape(Path(r).stem)}\b|\bfrom {re.escape(Path(r).stem)} import\b")
        for r in rel_changed if r.startswith("scripts/") and r.endswith(".py")
    }
    if changed_modules or conftest_dirs:
        sources = _sources(root)
        refs = {rel: _references(rel, text) for rel, text in sources.items()}
        targets = set(changed_modules)
        for rel, names in refs.items():
            if not _is_test_file(rel) and names & changed_modules:
                mod = _module_name(rel)
                if mod:
                    targets.add(mod)
        picked |= {rel for rel, names in refs.items() if _is_test_file(rel) and names & targets}
        picked |= {
            rel for rel, text in sources.items()
            if _is_test_file(rel) and any(rx.search(text) for rx in scripts_by_path.values())
        }
        picked |= {
            rel for rel in sources
            if _is_test_file(rel) and any(rel.startswith(d + "/") for d in conftest_dirs)
        }
    if scm_runs:
        picked = {r for r in picked if not r.startswith("tests/scm/")}
    picked = {r for r in picked if SAFE_TEST_RE.match(r)}
    tests = sorted(PREFIX + r for r in picked)
    seconds = estimate_seconds(tests, file_durations(root) if durations is None else durations)
    harness = "tests" in conftest_dirs
    return Selection(tests=tests, full=harness or seconds > budget, count=len(tests), seconds=seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default="sorento_crm_backend", help="the backend directory")
    parser.add_argument("--scm", default="false", choices=("true", "false"), help="the SCM shards run anyway")
    parser.add_argument("--budget", type=float, default=BUDGET_SECONDS, help="estimated CPU seconds")
    args = parser.parse_args()
    changed = [line.strip() for line in sys.stdin if line.strip()]
    result = select(changed, Path(args.root), scm_runs=args.scm == "true", budget=args.budget)
    summary = f"{result.count} test files, estimated {result.seconds:.0f} CPU-s (budget {args.budget:.0f})"
    if result.full and f"{PREFIX}tests/conftest.py" in changed:
        print("tests/conftest.py changed, the harness of every test: the full backend shards run on this PR")
    elif result.full:
        print(f"{summary}: above the budget, the full backend shards run on this PR instead. The selection was:")
        for test in result.tests:
            print(f"  {test}")
    elif result.tests:
        print(f"backend test files selected, changed or importing a changed module: {summary}")
        for test in result.tests:
            print(f"  {test}")
    else:
        print("no backend test file selected")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            # Above the budget the shards run every file, so no list goes out.
            fh.write(f"tests={'' if result.full else ' '.join(result.tests)}\n")
            fh.write(f"full={'true' if result.full else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
