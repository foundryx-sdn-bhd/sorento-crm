"""AC-21, AC-22: `scripts/prompt_diet_seed_examples.py`.

Pinned CLI (the contract names the flags only in part): `--data-dir DIR` (required),
`--out-dir DIR` (required; the script writes `examples.jsonl` and `examples.sql` there).
Embeddings are filled only with `--embed`; these tests never pass it, so no provider is
reached. A refusal exits non-zero, writes nothing, and its message contains the word "repo"
(data dir inside the repository) or "hidden". Every case below is invented.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tests.chatbot.prompt_diet._common import BACKEND, REPO_ROOT, SEED_SCRIPT

NAME = "Zorblat Marn"
COMPANY = "Quixley Trading Sdn Bhd"


def _case(case_id: str, text: str, *, history: list[dict] | None = None) -> dict:
    return {
        "id": case_id,
        "contact": {"name": NAME, "first_name": "Zorblat", "company": COMPANY, "labels": [COMPANY]},
        "message": {"text": text},
        "history": history or [],
        "topic_code": "price_check",
        "split": "dev",
        "expect": {"domain_hint": "master_products"},
    }


def _write_cases(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    cases = [
        _case("c1", f"hi this is {NAME} from {COMPANY}, price for SRT-TEST-1?"),
        _case(
            "c2",
            "and SRT-TEST-2?",
            history=[{"role": "assistant", "text": f"Hello {NAME}, SRT-TEST-1 is in stock."}],
        ),
    ]
    for case in cases:
        (directory / f"{case['id']}.json").write_text(json.dumps(case), encoding="utf-8")


def _run(data_dir: Path, out_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SEED_SCRIPT), "--data-dir", str(data_dir), "--out-dir", str(out_dir)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )


def _good(tmp_path):
    data = tmp_path / "dev_cases"
    _write_cases(data)
    out = tmp_path / "out"
    proc = _run(data, out)
    assert proc.returncode == 0, f"a clean data dir must be accepted: {proc.stderr}"
    return data, out


def test_ac21_a_data_dir_inside_the_repo_is_refused(tmp_path) -> None:
    _good(tmp_path)
    inside = REPO_ROOT / "documentation" / f"zzt_prompt_diet_{uuid.uuid4().hex[:8]}"
    try:
        _write_cases(inside)
        out = tmp_path / "out_repo"
        proc = _run(inside, out)
        assert proc.returncode != 0
        assert "repo" in (proc.stderr + proc.stdout).lower()
        assert not (out / "examples.jsonl").exists()
    finally:
        shutil.rmtree(inside, ignore_errors=True)


@pytest.mark.parametrize("segment", ["hidden", "hidden_cases", "old_hidden_set"])
def test_ac21_a_path_containing_hidden_is_refused(tmp_path, segment: str) -> None:
    _good(tmp_path)
    data = tmp_path / segment / "cases"
    _write_cases(data)
    out = tmp_path / f"out_{segment}"
    proc = _run(data, out)
    assert proc.returncode != 0
    assert "hidden" in (proc.stderr + proc.stdout).lower()
    assert not (out / "examples.jsonl").exists()


def test_ac21_a_clean_outside_dir_writes_jsonl_and_sql(tmp_path) -> None:
    _, out = _good(tmp_path)
    lines = (out / "examples.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        row = json.loads(line)
        assert row["area"]
        assert row["message"]
    sql = (out / "examples.sql").read_text(encoding="utf-8")
    assert "chatbot_examples" in sql
    assert "on conflict" in sql.lower() or "where not exists" in sql.lower(), "the SQL must be idempotent"


def test_ac21_the_sql_is_deterministic_so_a_rerun_changes_nothing(tmp_path) -> None:
    data, out = _good(tmp_path)
    again = tmp_path / "out_again"
    assert _run(data, again).returncode == 0
    assert (again / "examples.sql").read_text() == (out / "examples.sql").read_text()
    assert (again / "examples.jsonl").read_text() == (out / "examples.jsonl").read_text()


def test_ac22_customer_names_are_masked_in_message_and_context(tmp_path) -> None:
    _, out = _good(tmp_path)
    blob = (out / "examples.jsonl").read_text(encoding="utf-8") + (out / "examples.sql").read_text(encoding="utf-8")
    for secret in (NAME, "Zorblat", "Marn", "Quixley", COMPANY):
        assert secret.lower() not in blob.lower(), f"{secret!r} survived masking"


def test_ac22_the_rest_of_the_text_is_kept(tmp_path) -> None:
    _, out = _good(tmp_path)
    rows = [json.loads(line) for line in (out / "examples.jsonl").read_text(encoding="utf-8").splitlines()]
    joined = " ".join(str(r.get("message")) + " " + str(r.get("context")) for r in rows)
    assert "SRT-TEST-1" in joined and "SRT-TEST-2" in joined
    assert "price for" in joined
