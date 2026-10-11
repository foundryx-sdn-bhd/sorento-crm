"""Node heap for the frontend type-check and image build.

Private-repo GitHub runners have less RAM than public ones, and `tsc` /
`next build` died with "JavaScript heap out of memory" (exit 134) on the
default heap once the repo went private (10 Oct 2026). Pure file checks.

The heap is pinned at 4096 MB, not raised further: the private runners have
~7 GB of RAM in total, and release run 38071049191 (11 Oct 2026) showed how
little headroom those runners have. The frontend image job also lost its
`buildkit-cache-dance` steps there: build and push succeeded, then the
runner died "No space left on device" in the cache-dance post step. The
`type=gha` layer cache stays.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
FE_DOCKERFILE = ROOT / "sorento_crm_frontend" / "Dockerfile"
HEAP_MB = 4096
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


def test_typecheck_step_pins_node_heap():
    opts = str((_typecheck_step().get("env") or {}).get("NODE_OPTIONS", ""))
    assert _heap_mb(opts) == HEAP_MB, f"typecheck NODE_OPTIONS={opts!r}"


def test_frontend_image_build_pins_node_heap_and_keeps_other_flags():
    envs = re.findall(r'^ENV NODE_OPTIONS="([^"]*)"', FE_DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert len(envs) == 1, f"expected one NODE_OPTIONS ENV, got {envs}"
    assert _heap_mb(envs[0]) == HEAP_MB, f"Dockerfile NODE_OPTIONS={envs[0]!r}"
    assert "--max-semi-space-size=128" in envs[0]


def _build_images_steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["build-images"]["steps"]


def test_build_images_has_no_buildkit_cache_dance_or_cache_dir_restore():
    uses = [str(s.get("uses", "")) for s in _build_images_steps()]
    assert not [u for u in uses if "buildkit-cache-dance" in u], uses
    assert not [u for u in uses if u.startswith("actions/cache")], uses


def test_frontend_image_build_keeps_gha_layer_cache():
    steps = [s for s in _build_images_steps() if s.get("name") == "Build and push frontend image"]
    assert len(steps) == 1
    with_ = steps[0]["with"]
    assert with_["cache-from"] == "type=gha,scope=frontend"
    assert with_["cache-to"] == "type=gha,mode=max,scope=frontend"
