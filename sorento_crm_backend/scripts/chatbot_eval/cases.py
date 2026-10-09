"""Case model and loader. Case data lives outside the repo; the loader refuses a data dir inside it."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

OUTCOMES = ("answer", "clarify", "not_supported", "handover")
SPLITS = ("dev", "hidden")


class DataDirInRepoError(Exception):
    """The data dir sits inside the git work tree that holds this package."""


@dataclass
class Expect:
    outcome: str
    acceptable_alternates: list[str] = field(default_factory=list)
    must: list[dict] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    judge_note: str | None = None


@dataclass
class Case:
    id: str
    split: str
    topic_code: str | None
    language: str | None
    contact: dict
    history: list[dict]
    message: dict
    variants: list[dict]
    expect: Expect


@dataclass
class LoadResult:
    cases: list[Case]
    invalid: list[tuple[str, str]]


def _repo_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def _parse(raw: object) -> Case:
    if not isinstance(raw, dict):
        raise ValueError("case is not a JSON object")
    for key in ("id", "split", "message", "expect"):
        if not raw.get(key):
            raise ValueError(f"missing {key}")
    if raw["split"] not in SPLITS:
        raise ValueError(f"unknown split {raw['split']!r}")
    message, exp = raw["message"], raw["expect"]
    if not isinstance(message, dict) or not message.get("text"):
        raise ValueError("missing message.text")
    if not isinstance(exp, dict) or not exp.get("outcome"):
        raise ValueError("missing expect.outcome")
    if exp["outcome"] not in OUTCOMES:
        raise ValueError(f"unknown outcome {exp['outcome']!r}")
    alternates = list(exp.get("acceptable_alternates") or [])
    bad = [a for a in alternates if a not in OUTCOMES]
    if bad:
        raise ValueError(f"unknown alternate {bad[0]!r}")
    return Case(
        id=str(raw["id"]),
        split=raw["split"],
        topic_code=raw.get("topic_code"),
        language=raw.get("language"),
        contact=dict(raw.get("contact") or {}),
        history=list(raw.get("history") or []),
        message=dict(message),
        variants=list(raw.get("variants") or []),
        expect=Expect(
            outcome=exp["outcome"],
            acceptable_alternates=alternates,
            must=list(exp.get("must") or []),
            options=list(exp.get("options") or []),
            must_not=list(exp.get("must_not") or []),
            judge_note=exp.get("judge_note"),
        ),
    )


def load_cases(data_dir: str | Path, split: str | None = None) -> LoadResult:
    root = Path(data_dir).resolve()
    repo = _repo_root()
    if repo is not None and (root == repo or repo in root.parents):
        raise DataDirInRepoError(f"{root} is inside the repo; keep case data outside git")
    cases: list[Case] = []
    invalid: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            case = _parse(json.loads(path.read_text()))
        except (ValueError, OSError) as exc:  # JSONDecodeError is a ValueError
            invalid.append((str(path), str(exc)))
            continue
        if split is None or case.split == split:
            cases.append(case)
    return LoadResult(cases=cases, invalid=invalid)
