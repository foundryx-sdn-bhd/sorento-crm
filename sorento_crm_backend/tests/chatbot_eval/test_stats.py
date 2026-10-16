"""EVAL-BENCH D3 (AC3.1-AC3.5): paired bootstrap comparison of two arms with reps.

Red until `scripts/chatbot_eval/stats.py` exists. Synthetic booleans only.
"""
from __future__ import annotations

import random

from scripts.chatbot_eval.stats import format_compare, paired_compare


def _arm(passes: list[bool], reps: int = 3) -> dict[str, list[bool]]:
    return {f"i{n}": [p] * reps for n, p in enumerate(passes)}


def test_identical_arms_are_within_noise():
    arm = _arm([n % 2 == 0 for n in range(60)])
    r = paired_compare(arm, dict(arm), n_boot=2000, seed=1)
    lo, hi = r["delta_ci"]
    assert lo <= 0 <= hi
    assert r["delta"] == 0
    assert r["verdict"] == "within noise"


def test_head_better_when_items_flip_fail_to_pass_in_every_rep():
    base = _arm([n >= 15 for n in range(60)])
    head = _arm([True] * 60)
    r = paired_compare(base, head, n_boot=2000, seed=1)
    assert r["delta_ci"][0] > 0
    assert r["verdict"] == "better"
    assert abs(r["delta"] - 0.25) < 1e-9
    assert r["n_items"] == 60


def test_head_worse_is_the_mirror():
    base = _arm([True] * 60)
    head = _arm([n >= 15 for n in range(60)])
    r = paired_compare(base, head, n_boot=2000, seed=1)
    assert r["delta_ci"][1] < 0
    assert r["verdict"] == "worse"


def test_two_reps_is_refused_with_reason_and_no_verdict_word():
    base = _arm([n >= 15 for n in range(60)], reps=2)
    head = _arm([True] * 60, reps=3)
    r = paired_compare(base, head, n_boot=500, seed=1)
    assert r["verdict"] == "refused"
    assert r["reason"] == "2 reps < 3"
    out = format_compare(r)
    assert "verdict refused: 2 reps < 3" in out
    for word in ("better", "worse", "within noise"):
        assert word not in out.replace("verdict refused", "")


def test_items_in_one_arm_only_are_excluded_and_listed():
    base = _arm([True] * 10)
    head = _arm([True] * 10)
    base["only_base"] = [True] * 3
    head["only_head"] = [False] * 3
    r = paired_compare(base, head, n_boot=500, seed=1)
    assert r["n_items"] == 10
    assert sorted(r["excluded"]) == ["only_base", "only_head"]
    assert "2" in format_compare(r)


def test_same_seed_gives_identical_cis():
    rng = random.Random(7)
    base = {f"i{n}": [rng.random() < 0.5 for _ in range(3)] for n in range(40)}
    head = {f"i{n}": [rng.random() < 0.6 for _ in range(3)] for n in range(40)}
    a = paired_compare(base, head, n_boot=2000, seed=5)
    b = paired_compare(base, head, n_boot=2000, seed=5)
    assert a["delta_ci"] == b["delta_ci"]
    assert a["base_ci"] == b["base_ci"] and a["head_ci"] == b["head_ci"]
    assert format_compare(a) == format_compare(b)


def test_random_flipping_across_reps_is_within_noise():
    rng = random.Random(11)
    base = {f"i{n}": [rng.random() < 0.5 for _ in range(3)] for n in range(40)}
    head = {f"i{n}": [rng.random() < 0.5 for _ in range(3)] for n in range(40)}
    r = paired_compare(base, head, n_boot=2000, seed=3)
    assert r["verdict"] == "within noise"
    assert r["delta_ci"][0] < 0 < r["delta_ci"][1]


def test_per_arm_means_and_cis_are_reported():
    base = _arm([True, True, False, False])
    head = _arm([True, True, True, False])
    r = paired_compare(base, head, n_boot=500, seed=1, min_items=1)
    assert r["base_mean"] == 0.5 and r["head_mean"] == 0.75
    assert r["base_ci"][0] <= 0.5 <= r["base_ci"][1]
    assert r["reps"] == 3


def test_weights_give_weighted_means():
    # a: weight 3, passes both arms; b: weight 1, fails base and passes head.
    base = {"a": [True] * 3, "b": [False] * 3}
    head = {"a": [True] * 3, "b": [True] * 3}
    r = paired_compare(base, head, n_boot=500, seed=1, weights={"a": 3.0, "b": 1.0}, min_items=1)
    assert abs(r["base_mean"] - 0.75) < 1e-9
    assert abs(r["head_mean"] - 1.0) < 1e-9
    assert abs(r["delta"] - 0.25) < 1e-9


def test_fewer_than_min_items_is_refused_with_the_count():
    base = _arm([True, False, True, False, True])
    head = _arm([True] * 5)
    r = paired_compare(base, head, n_boot=500, seed=1)
    assert r["verdict"] == "refused"
    assert r["reason"] == "5 items < 10"
    assert "verdict refused: 5 items < 10" in format_compare(r)
