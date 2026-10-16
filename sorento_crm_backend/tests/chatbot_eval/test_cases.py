import json
from pathlib import Path

import pytest

from scripts.chatbot_eval.cases import DataDirInRepoError, load_cases
from tests.chatbot_eval.fakes import case_dict, write_case


def test_valid_case_loads_with_fields(tmp_path):
    write_case(tmp_path, case_dict("a1"))
    res = load_cases(tmp_path)
    assert [c.id for c in res.cases] == ["a1"]
    assert res.invalid == []
    c = res.cases[0]
    assert c.split == "dev" and c.message["text"] == "how many TESTCODE-1?"
    assert c.expect.outcome == "answer"
    assert c.expect.must == [{"code": "TESTCODE-1"}]
    assert c.expect.acceptable_alternates == [] and c.expect.must_not == []


def test_recursive_discovery(tmp_path):
    write_case(tmp_path / "x" / "y", case_dict("deep"))
    assert [c.id for c in load_cases(tmp_path).cases] == ["deep"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("id"),
        lambda d: d.pop("split"),
        lambda d: d.pop("message"),
        lambda d: d["message"].pop("text"),
        lambda d: d.pop("expect"),
        lambda d: d["expect"].pop("outcome"),
        lambda d: d["expect"].update(outcome="bogus"),
        lambda d: d.update(split="train"),
    ],
)
def test_invalid_cases_listed_with_reason(tmp_path, mutate):
    d = case_dict("bad")
    mutate(d)
    p = write_case(tmp_path, d, name="bad.json")
    write_case(tmp_path, case_dict("good"))
    res = load_cases(tmp_path)
    assert [c.id for c in res.cases] == ["good"]
    assert len(res.invalid) == 1
    path, reason = res.invalid[0]
    assert Path(path) == p
    assert isinstance(reason, str) and reason


def test_unparseable_json_is_invalid(tmp_path):
    (tmp_path / "broken.json").write_text("{not json")
    res = load_cases(tmp_path)
    assert res.cases == [] and len(res.invalid) == 1


def test_split_filter(tmp_path):
    write_case(tmp_path, case_dict("d1", split="dev"))
    write_case(tmp_path, case_dict("h1", split="hidden"))
    assert [c.id for c in load_cases(tmp_path, split="hidden").cases] == ["h1"]
    assert [c.id for c in load_cases(tmp_path, split="dev").cases] == ["d1"]
    assert sorted(c.id for c in load_cases(tmp_path).cases) == ["d1", "h1"]


def test_data_dir_inside_repo_refused():
    tests_dir = Path(__file__).resolve().parent
    with pytest.raises(DataDirInRepoError):
        load_cases(tests_dir)


def test_data_dir_outside_repo_ok(tmp_path):
    assert load_cases(tmp_path).cases == []
