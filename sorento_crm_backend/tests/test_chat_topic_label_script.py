"""CA-2 labeller script runs as documented: `python scripts/chat_topic_label.py --help` from the
backend root, with no PYTHONPATH."""
import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_label_script_help_runs_without_pythonpath_and_lists_limit():
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "scripts/chat_topic_label.py", "--help"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "--limit" in result.stdout
