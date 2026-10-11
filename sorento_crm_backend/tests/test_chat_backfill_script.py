"""CA-4 backfill script runs as documented: `python scripts/chat_backfill.py --help` from the
backend root, with no PYTHONPATH, and refuses an unknown era."""
import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _script(*args):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "scripts/chat_backfill.py", *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_backfill_script_help_lists_era_estimate_limit_and_relabel():
    result = _script("--help")
    assert result.returncode == 0, result.stderr
    for flag in ("--era", "--estimate", "--limit", "--relabel"):
        assert flag in result.stdout


def test_an_unknown_era_is_refused_before_anything_runs():
    result = _script("--era", "Z", "--estimate")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
