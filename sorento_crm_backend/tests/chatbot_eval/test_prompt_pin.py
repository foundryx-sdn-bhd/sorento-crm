import json

from scripts.chatbot_eval.engine import HttpTurnEngine
from scripts.chatbot_eval.run import main
from tests.chatbot_eval.fakes import KwEngine, case_dict, write_case
from tests.chatbot_eval.test_runner import mk_case

KW = "tests.chatbot_eval.fakes:KwEngine"


def test_envelope_pins_prompt_version():
    eng = HttpTurnEngine("http://fake.invalid", "k", 5, prompt_version=52)
    env = eng.build_envelope(mk_case(), "final q", None, "r")
    assert env["prompt_overrides"] == {"chatbot_semantic_parser": "52"}


def test_envelope_without_prompt_version_has_no_key():
    env = HttpTurnEngine("http://fake.invalid", "k", 5).build_envelope(mk_case(), "final q", None, "r")
    assert "prompt_overrides" not in env


def _run(tmp_path, *extra):
    d, out = tmp_path / "data", tmp_path / "out"
    write_case(d, case_dict("k0"))
    write_case(d, case_dict("k1"))
    KwEngine.SEEN.clear()
    rc = main(["--data-dir", str(d), "--engine", KW, "--out", str(out), *extra])
    assert rc == 0
    return json.loads(next(out.rglob("*.json")).read_text())


def test_cli_prompt_version_passed_to_engine_and_report(tmp_path, monkeypatch):
    # round 11: the pin is resolved to the ai_prompt_versions uuid before the engine is built
    from scripts.chatbot_eval import prompts

    monkeypatch.setenv("EVAL_TRACE_DB_URL", "postgresql://u:p@127.0.0.1:1/sorento_eval_x")
    monkeypatch.setattr(prompts, "query_prompt", lambda dsn, value: {"id": "uuid-52", "version": 52, "template_md5": "m"})
    rep = _run(tmp_path, "--prompt-version", "52")
    assert any(kw.get("prompt_version") == "uuid-52" for kw in KwEngine.SEEN)
    assert rep["prompt"]["version"] == 52


def test_report_prompt_version_defaults_to_label(tmp_path):
    assert _run(tmp_path)["prompt_version"] == "label"


def test_report_records_run_metadata(tmp_path):
    rep = _run(tmp_path, "--split", "dev", "--variants", "none")
    assert rep["engine"] == KW
    assert rep["run_id"]
    assert rep["split"] == "dev"
    assert rep["variants"] == "none"
    assert rep["cases"] == 2
    assert rep["started_at"]
