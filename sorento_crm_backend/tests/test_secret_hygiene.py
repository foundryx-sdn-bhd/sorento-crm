"""Secret hygiene for compose files, .gitignore and gitleaks (UAC AC-16..AC-20).

Pure file checks: stdlib + PyYAML, no DB. Values are never printed, only names.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FULL_COMPOSE = ROOT / "sorento_crm" / "docker-compose.yml"
BACKEND_COMPOSE = ROOT / "sorento_crm_backend" / "docker-compose.yml"
SECRET_NAME = re.compile(r"PASSWORD|SECRET|TOKEN|ACCESS_KEY", re.I)
pytestmark = pytest.mark.skipif(
    not (ROOT / "sorento_crm" / "docker-compose.yml").is_file(),
    reason="repo-root files not present (backend-only checkout)",
)
REF = r"\$\{%s(?P<op>:?[-?+][^}]*)?\}"


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _tracked_compose_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [
        ROOT / f
        for f in out
        if re.search(r"(^|/)(docker-compose[^/]*|compose[^/]*)\.ya?ml$", f)
    ]


def _assert_required_everywhere(path: Path, var: str) -> None:
    text = _text(path)
    uses = [
        m for m in re.finditer(REF % re.escape(var), text)
        if not text[: m.start()].rsplit("\n", 1)[-1].lstrip().startswith("#")
    ]
    assert uses, f"{path.name}: {var} is not referenced at all"
    weak = [m.group(0).split("}")[0] for m in uses if not (m.group("op") or "").startswith(":?")]
    assert not weak, f"{path.name}: {var} used without ':?' {len(weak)} time(s)"
    bare = re.findall(r"(?<!\{)\$%s\b" % re.escape(var), text)
    assert not bare, f"{path.name}: {var} used as bare $VAR"


def _required_names(path: Path) -> set[str]:
    return set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?", _text(path)))


# AC-16 ---------------------------------------------------------------------

@pytest.mark.parametrize("var", ["POSTGRES_USER", "POSTGRES_PASSWORD", "JWT_SECRET"])
def test_full_stack_compose_requires_var_everywhere(var):
    _assert_required_everywhere(FULL_COMPOSE, var)


def test_full_stack_compose_sample_env_block_has_no_secret_values():
    bad = []
    for line in _text(FULL_COMPOSE).splitlines():
        m = re.match(r"^#\s+([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m and SECRET_NAME.search(m.group(1)):
            val = m.group(2).strip()
            if val and not re.fullmatch(r"<[^>]*>", val):
                bad.append(m.group(1))
    assert not bad, f"sample .env block gives values for: {bad}"


# AC-17 ---------------------------------------------------------------------

@pytest.mark.parametrize("var", ["POSTGRES_PASSWORD", "JWT_SECRET"])
def test_backend_compose_requires_secret_var_everywhere(var):
    _assert_required_everywhere(BACKEND_COMPOSE, var)


@pytest.mark.parametrize("name", ["DATABASE_URL", "DIRECT_URL"])
def test_backend_compose_db_urls_built_from_user_and_password_refs(name):
    text = _text(BACKEND_COMPOSE)
    lines = [
        l for l in text.splitlines()
        if re.match(rf"^\s*-?\s*{name}\s*[:=]", l) and not l.lstrip().startswith("#")
    ]
    assert lines, f"{name} not set in backend compose"
    for l in lines:
        assert "${POSTGRES_USER" in l, f"{name} does not use POSTGRES_USER ref"
        assert "${POSTGRES_PASSWORD" in l, f"{name} does not use POSTGRES_PASSWORD ref"
        m = re.search(r"://([^@\s]*)@", l)
        assert m, f"{name} has no userinfo"
        residue = re.sub(r"\$\{[^}]*\}", "", m.group(1))
        assert residue == ":", f"{name} has inline literal credentials"


def test_no_tracked_compose_has_nonempty_default_for_secret_names():
    bad = []
    for p in _tracked_compose_files():
        for m in re.finditer(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]+)\}", _text(p)):
            if SECRET_NAME.search(m.group(1)):
                bad.append(f"{p.name}:{m.group(1)}")
    assert not bad, f"non-empty default for secret-named vars: {bad}"


def test_no_tracked_compose_has_literal_secret_assignment():
    bad = []
    for p in _tracked_compose_files():
        for line in _text(p).splitlines():
            s = line.strip()
            if s.startswith("#"):
                continue
            m = re.match(r"^-?\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(\S.*)$", s)
            if not m or not SECRET_NAME.search(m.group(1)):
                continue
            val = m.group(2).strip().strip("'\"")
            if val and not val.startswith("${"):
                bad.append(f"{p.name}:{m.group(1)}")
    assert not bad, f"literal secret-named assignments: {bad}"


# AC-18 ---------------------------------------------------------------------

@pytest.mark.parametrize("compose", [FULL_COMPOSE, BACKEND_COMPOSE], ids=["full", "backend"])
def test_env_example_beside_compose_lists_every_required_var(compose):
    example = compose.parent / ".env.example"
    assert example.is_file(), f"missing {example.relative_to(ROOT)}"
    listed = set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)=", _text(example), re.M))
    missing = _required_names(compose) - listed
    assert not missing, f".env.example lacks: {sorted(missing)}"
    assert _required_names(compose), "compose declares no required vars"


@pytest.mark.parametrize("compose", [FULL_COMPOSE, BACKEND_COMPOSE], ids=["full", "backend"])
def test_compose_header_mentions_env_example(compose):
    comments = "\n".join(l for l in _text(compose).splitlines() if l.lstrip().startswith("#"))
    assert ".env.example" in comments


# AC-19 ---------------------------------------------------------------------

def _ignored(path: str) -> bool:
    return subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", path], cwd=ROOT
    ).returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "docker-compose.override.yml",
        "compose.override.yaml",
        "server.key",
        ".env.local",
        ".env.production",
        "sorento_crm_backend/.env.ci-tests",
    ],
)
def test_gitignore_ignores_local_secret_files(path):
    assert _ignored(path), f"{path} is not ignored"


@pytest.mark.parametrize(
    "path", [".env.example", "sorento_crm/.env.example", "sorento_crm_backend/.env.example"]
)
def test_gitignore_keeps_env_example_tracked(path):
    assert not _ignored(path), f"{path} is ignored"


# AC-20 ---------------------------------------------------------------------

def _workflow() -> dict:
    return yaml.safe_load(_text(ROOT / ".github" / "workflows" / "deploy.yml"))


def _gitleaks_job():
    jobs = _workflow()["jobs"]
    for name, job in jobs.items():
        for step in job.get("steps", []) or []:
            if "gitleaks" in str(step.get("uses", "")).lower() or "gitleaks" in str(step.get("run", "")).lower():
                return name, job, step
    return None, None, None


def test_deploy_workflow_has_gitleaks_step():
    name, _, _ = _gitleaks_job()
    assert name, "no gitleaks job or step in deploy.yml"


def test_gitleaks_action_is_pinned_to_full_version_or_sha():
    _, _, step = _gitleaks_job()
    assert step, "no gitleaks step"
    uses = str(step.get("uses", ""))
    if uses:
        ref = uses.split("@", 1)[1] if "@" in uses else ""
        assert re.fullmatch(r"v\d+\.\d+\.\d+|[0-9a-f]{40}", ref), f"unpinned ref {ref!r}"


def test_gitleaks_skips_merge_group_and_dispatch_but_runs_on_labelled_pr_and_push():
    # AC-21 supersedes the old "same `if` as pii-guard" test: gitleaks must NOT
    # run on merge_group or workflow_dispatch.
    name, job, step = _gitleaks_job()
    assert name, "no gitleaks job or step"
    effective = str(step.get("if") or job.get("if") or "")
    assert effective, "gitleaks has no if"

    def run(event: str, label: str = "") -> bool:
        expr = effective.replace("${{", "").replace("}}", "").strip()
        expr = re.sub(r"github\.event\.label\.name", repr(label), expr)
        expr = re.sub(r"github\.event_name", repr(event), expr)
        expr = expr.replace("&&", " and ").replace("||", " or ").replace("!=", " != ")
        expr = re.sub(r"(?<![=!<>])!(?!=)", " not ", expr)
        return bool(eval(expr, {"__builtins__": {}}, {}))

    assert not run("merge_group")
    assert not run("workflow_dispatch")
    assert run("pull_request", "ci")
    assert not run("pull_request", "other")
    assert run("push")


def test_gitleaks_action_is_pinned_to_a_commit_sha():
    # AC-21 supersedes the older "full version or sha" pin test.
    _, _, step = _gitleaks_job()
    uses = str(step.get("uses", ""))
    assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", uses), f"not a 40-hex SHA pin: {uses!r}"


def test_gitleaks_version_env_is_exact():
    _, _, step = _gitleaks_job()
    ver = str((step.get("env") or {}).get("GITLEAKS_VERSION", ""))
    assert re.fullmatch(r"v?\d+\.\d+\.\d+", ver), f"GITLEAKS_VERSION={ver!r}"


def test_gitleaks_allowlist_regexes_are_anchored():
    text = _text(ROOT / ".gitleaks.toml")
    block = re.search(r"^\[allowlist\].*?^regexes\s*=\s*\[(.*?)^\]", text, re.M | re.S)
    assert block, "no [allowlist] regexes"
    regexes = re.findall(r"'''(.*?)'''", block.group(1))
    assert regexes
    bad = [r for r in regexes if not (r.startswith("^") and r.endswith("$"))]
    assert not bad, f"unanchored allowlist regexes: {bad}"


def test_gitleaks_toml_extends_default_rules():
    p = ROOT / ".gitleaks.toml"
    assert p.is_file(), ".gitleaks.toml missing"
    cfg = _text(p)
    assert re.search(r"^\[extend\]", cfg, re.M)
    assert re.search(r"^\s*useDefault\s*=\s*true", cfg, re.M)


@pytest.mark.parametrize("placeholder", ["ci-dummy-secret", "cloud-lane-test-key"])
def test_gitleaks_toml_allowlists_fake_placeholder(placeholder):
    p = ROOT / ".gitleaks.toml"
    assert p.is_file(), ".gitleaks.toml missing"
    assert "allowlist" in _text(p).lower()
    assert placeholder in _text(p)


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks binary absent")
def test_gitleaks_config_parses_with_binary(tmp_path):
    cfg = ROOT / ".gitleaks.toml"
    assert cfg.is_file(), ".gitleaks.toml missing"
    r = subprocess.run(
        ["gitleaks", "dir", str(tmp_path), "-c", str(cfg), "--no-banner"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
