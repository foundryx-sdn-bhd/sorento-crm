"""Node heap for the frontend type-check and image build.

Private-repo GitHub runners have less RAM than public ones, and `tsc` /
`next build` died with "JavaScript heap out of memory" (exit 134) on the
default heap once the repo went private (10 Oct 2026). Pure file checks.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
FE_DOCKERFILE = ROOT / "sorento_crm_frontend" / "Dockerfile"
MIN_HEAP_MB = 6144
pytestmark = pytest.mark.skipif(
    not WORKFLOW.is_file(), reason="repo-root files not present (backend-only checkout)"
)


def _heap_mb(node_options: str) -> int:
    m = re.search(r"--max-old-space-size=(\d+)", node_options)
    return int(m.group(1)) if m else 0


def _typecheck_step() -> dict:
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["typecheck-frontend"]
    assert job["name"] == "Type-check frontend"
    steps = [s for s in job["steps"] if "npm run typecheck" in str(s.get("run", ""))]
    assert len(steps) == 1, "no single `npm run typecheck` step"
    return steps[0]


def test_typecheck_step_raises_node_heap():
    opts = str((_typecheck_step().get("env") or {}).get("NODE_OPTIONS", ""))
    assert _heap_mb(opts) >= MIN_HEAP_MB, f"typecheck NODE_OPTIONS={opts!r}"


def test_frontend_image_build_raises_node_heap_and_keeps_other_flags():
    envs = re.findall(r'^ENV NODE_OPTIONS="([^"]*)"', FE_DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert len(envs) == 1, f"expected one NODE_OPTIONS ENV, got {envs}"
    assert _heap_mb(envs[0]) >= MIN_HEAP_MB, f"Dockerfile NODE_OPTIONS={envs[0]!r}"
    assert "--max-semi-space-size=128" in envs[0]
