"""ZH-SYNONYMS: `scripts/add_zh_category_synonyms.py`.

Owner ruling (11 Oct 2026, PROMPT-ABLATION #1574): vocabulary lives in DATA, not in the
parser prompt. The Chinese class words the v50 prompt carried are appended to
`product_categories.search_synonyms`, which `ClassVocabulary`
(`app/services/product_class_signal.py`) and `resolve_category_class`
(`app/services/chatbot/lanes/business/services.py`) already read, so no resolver changes.

TEST-FIRST: the script does not exist yet, so the import fails with ModuleNotFoundError.

Interface pinned here:

* `ZH_CLASS_SYNONYMS: dict[str, list[str]]`, class_label -> Chinese words, each word under
  exactly one class.
* `plan(db) -> list[dict]`, one entry per category that is missing at least one word, each
  with `category_code`, `class_label`, `add` (the missing words, in map order).
* `run(db, apply=False) -> dict` with `plan` and `added` (words written). Caller commits.

Phrasing below is made up; real chat turns stay out of this public repo.
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid

import pytest

from app.models.base import company_scope
from app.models.product import ProductCategory

from ._pg_fixture import blank_session, unique_code

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "add_zh_category_synonyms.py"

V50_WORDS = {
    "马桶": "Water Closet",
    "坐厕": "Water Closet",
    "面盆": "Wash Basin",
    "洗手盆": "Wash Basin",
    "水龙头": "Tap",
    "浴室柜": "Bathroom Furniture",
    "厨房水槽": "Kitchen Sink",
    "水槽": "Kitchen Sink",
    "淋浴": "Shower",
    "花洒": "Shower",
    "浴缸": "Bathtub and Jacuzzi",
    "小便斗": "Urinal",
}


def _script():
    spec = importlib.util.spec_from_file_location("add_zh_category_synonyms", _SCRIPT)
    assert spec is not None and spec.loader is not None and _SCRIPT.exists(), f"missing {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _category(db, suffix: str, label: str | None, synonyms: list[str], *, searchable: bool = True):
    code = unique_code(f"ZZ-{suffix}")
    row = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=code,
        category_name=code,
        class_label=label,
        search_synonyms=list(synonyms),
        is_searchable=searchable,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def db():
    with blank_session() as session:
        with company_scope(session, None):
            yield session


def test_the_map_carries_every_v50_word_under_one_class() -> None:
    mapping = _script().ZH_CLASS_SYNONYMS
    seen: dict[str, str] = {}
    for label, words in mapping.items():
        for word in words:
            assert word not in seen, f"{word} is under {seen[word]} and {label}"
            seen[word] = label
    assert {w: seen.get(w) for w in V50_WORDS} == V50_WORDS


def test_dry_run_reports_per_category_and_writes_nothing(db) -> None:
    wc = _category(db, "WC", "Water Closet", ["toilet"])
    run = _script().run(db, apply=False)
    entry = next(p for p in run["plan"] if p["category_code"] == wc.category_code)
    assert entry["class_label"] == "Water Closet"
    assert entry["add"] == ["马桶", "坐厕"]
    assert run["added"] == 0
    db.refresh(wc)
    assert wc.search_synonyms == ["toilet"]


def test_apply_appends_keeps_existing_and_resolves_the_chinese_word(db) -> None:
    from app.services.chatbot.lanes.business.services import resolve_category_class

    wc = _category(db, "WC", "Water Closet", ["toilet"])
    tap = _category(db, "FT", "Tap", ["tap", "faucet"])
    assert resolve_category_class(db, "马桶") == ([], [])

    run = _script().run(db, apply=True)
    db.flush()
    db.refresh(wc)
    db.refresh(tap)

    assert wc.search_synonyms == ["toilet", "马桶", "坐厕"]
    assert tap.search_synonyms == ["tap", "faucet", "水龙头"]
    assert run["added"] >= 3
    assert resolve_category_class(db, "马桶") == ([wc.id], ["Water Closet"])
    assert resolve_category_class(db, "水龙头") == ([tap.id], ["Tap"])
    # A mixed phrase: the Chinese word is a whole word of it.
    assert resolve_category_class(db, "sorento 马桶") == ([wc.id], ["Water Closet"])


def test_second_run_plans_nothing(db) -> None:
    wc = _category(db, "WC", "Water Closet", ["toilet", "马桶"])
    script = _script()
    first = script.run(db, apply=True)
    assert next(p for p in first["plan"] if p["category_code"] == wc.category_code)["add"] == ["坐厕"]
    db.flush()
    second = script.run(db, apply=True)
    assert not [p for p in second["plan"] if p["category_code"] == wc.category_code]
    db.refresh(wc)
    assert wc.search_synonyms == ["toilet", "马桶", "坐厕"]


def test_unclassed_and_non_searchable_categories_are_left_alone(db) -> None:
    blank = _category(db, "MISC", None, [])
    hidden = _category(db, "WCX", "Water Closet", [], searchable=False)
    run = _script().run(db, apply=True)
    codes = {p["category_code"] for p in run["plan"]}
    assert blank.category_code not in codes
    assert hidden.category_code not in codes
    db.refresh(hidden)
    assert hidden.search_synonyms == []
