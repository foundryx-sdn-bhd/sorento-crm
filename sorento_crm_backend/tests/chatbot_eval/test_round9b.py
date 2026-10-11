import pytest

from scripts.chatbot_eval.run import main
from tests.chatbot_eval import fakes
from tests.chatbot_eval.fakes import case_dict, write_case

NEVER = "tests.chatbot_eval.fakes:NeverEngine"


def _data(tmp_path):
    fakes.JUDGE_CALLS.clear()
    d = tmp_path / "data"
    c = case_dict("k0")
    c["expect"]["judge_note"] = "n"
    write_case(d, c)
    return d


@pytest.mark.parametrize("judge,env_model", [
    ("anthropic", "claude-unknown-9"),
    ("openai", "gpt-unknown-9"),
    ("tests.chatbot_eval.fakes:UnknownModelJudge", None),
])
def test_unknown_judge_model_refused(tmp_path, monkeypatch, capsys, judge, env_model):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    if env_model:
        monkeypatch.setenv("EVAL_JUDGE_MODEL", env_model)
    else:
        monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
    rc = main(["--data-dir", str(_data(tmp_path)), "--engine", NEVER, "--judge", judge])
    assert rc == 2
    assert (env_model or "claude-unknown-9") in capsys.readouterr().err
    assert fakes.JUDGE_CALLS == []
