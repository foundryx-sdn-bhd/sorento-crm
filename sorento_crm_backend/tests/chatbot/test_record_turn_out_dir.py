"""REPORTS-HELP: the recorder can write OUT of git (`--out-dir`), where gate case data has to live
(`CONTRACT-chatbot-eval-runner.md`: case data stays outside the repo), for `test_reports_replay.py`'s
`$REPORTS_REPLAY_DIR`."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("chatbot_record_turn_outdir", _BACKEND / "scripts" / "chatbot_record_turn.py")
assert _spec is not None and _spec.loader is not None
recorder = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("chatbot_record_turn_outdir", recorder)
_spec.loader.exec_module(recorder)


def test_write_with_an_out_dir_lands_there_and_not_in_the_repo(tmp_path) -> None:
    path = recorder._write("reports", "rpt-020 run 7", [{"verdict": {}}], out_root=tmp_path)
    assert path == tmp_path / "rpt-020-run-7.json"
    assert json.loads(path.read_text()) == {"turns": [{"verdict": {}}]}
    assert not (recorder.REPLAY_ROOT / "reports").exists()


def test_write_without_an_out_dir_keeps_the_group_folder() -> None:
    import inspect

    assert inspect.signature(recorder._write).parameters["out_root"].default is None


def test_the_cli_takes_out_dir_and_the_reports_group() -> None:
    import argparse

    seen: dict = {}

    def _stop(self, args=None, namespace=None):
        ns = real_parse(self, args, namespace)
        seen.update(vars(ns))
        raise SystemExit(0)

    real_parse = argparse.ArgumentParser.parse_args
    argparse.ArgumentParser.parse_args = _stop
    try:
        try:
            recorder.main(["--db-url", "postgresql://x/x", "--group", "reports", "--contact", "1", "--out-dir", "/tmp/x"])
        except SystemExit:
            pass
    finally:
        argparse.ArgumentParser.parse_args = real_parse
    assert seen.get("out_dir") == "/tmp/x" and seen.get("group") == "reports", seen


def _main_exit(argv: list[str]) -> int:
    try:
        recorder.main(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_s3_the_reports_group_without_an_out_dir_is_refused() -> None:
    assert _main_exit(["--db-url", "postgresql://x/x", "--group", "reports", "--contact", "1"]) == 2


def test_s4_an_out_dir_inside_the_repo_is_refused() -> None:
    inside = str(_BACKEND / "tests" / "chatbot" / "zzt-not-here")
    assert _main_exit(["--db-url", "postgresql://x/x", "--group", "reports", "--contact", "1", "--out-dir", inside]) == 2
