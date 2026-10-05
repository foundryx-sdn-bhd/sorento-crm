"""Owner decision of 3 Oct 2026: Q-A switches to (b). The rebuild script applies the owner's
3 approved text edits (PR #1405 crew-ask of 2 Oct, comment 5944690012) to his current
version and saves the result as a NEW unlabelled plain-text version, with ONLY these changes:

- line 82: `domain_hint` gains `purchase_order` before `purchase_cost`, and `sales` last;
- line 401: the entity `hint` list gains `specification` last;
- the policy block gains the `Domain sales ...` line, after `Domain purchase_cost ...`.

Every edit is a literal old -> new pair that must occur exactly once; anything else refuses
and writes nothing (never a guess). The line diff against the source is proven to be exactly
those three lines before anything is saved.
"""
from __future__ import annotations

import difflib
import importlib.util
import pathlib
import uuid

import pytest
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip
from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services import ai_prompt_registry, chatbot_prompt_vars as pv
from tests._pg_fixture import pg_session

KEY = "chatbot_semantic_parser"
BACKEND = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.prod-20261001.txt"

LINE_82 = (
    "domain_hint = ONE of: master_products | product_attachment | promotion | forms | inventory | order | "
    "incoming | portal_link | resource_attachment | goods_receive | spo_allocation | ideate | purchase_order | "
    "purchase_cost | sales | null"
)
HINT_401 = (
    '"hint": "product|promotion|customer|transporter|inbound_shipment|warehouse|attachment|form|order|category|'
    'brand|attachment_type|specification"'
)
SALES_LINE = (
    'Domain sales ("sales"): intents check_sales. Switch words: sales, sales report, top selling, sales analysis, '
    "best selling. customer narrows must_narrow_one; order narrows narrow_to_code; product narrows list_all. "
    "Takes a date window. Escalates to customer_service."
)


def _script():
    path = BACKEND / "scripts" / "prompt_dynamic_identical_version.py"
    spec = importlib.util.spec_from_file_location("_oe_identical_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _fresh_cache():
    pv.clear_cache()
    ai_prompt_registry.bust_cache()
    yield
    pv.clear_cache()
    ai_prompt_registry.bust_cache()


def _source() -> str:
    return SNAPSHOT.read_text(encoding="utf-8")


def _put_version(db, template: str) -> AIPromptVersion:
    top = db.execute(text("SELECT COALESCE(max(version), 0) FROM ai_prompt_versions WHERE name = :n"), {"n": KEY}).scalar()
    row = AIPromptVersion(id=str(uuid.uuid4()), name=KEY, version=int(top) + 1, template=template,
                          commit_message="t", config_json={})
    db.add(row)
    db.flush()
    return row


def test_the_three_edits_and_nothing_else():
    src = _source()
    out, changes = _script().apply_owner_edits(src)
    a, b = src.split("\n"), out.split("\n")
    ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
    assert [(t, a[i1:i2], b[j1:j2]) for t, i1, i2, j1, j2 in ops] == [
        ("replace", [a[81]], [LINE_82]),
        ("replace", [a[400]], [a[400].replace(HINT_401.removesuffix('|specification"') + '"', HINT_401)]),
        ("insert", [], [SALES_LINE]),
    ]
    assert b[1675] == SALES_LINE and b[1674].startswith("Domain purchase_cost ")
    assert [c["edit"] for c in changes] == ["line 82 domain_hint", "line 401 entity hint", "policy block sales line"]
    assert [c["where"] for c in changes] == ["line 82", "line 401", "after line 1675"]


def _source_with_sales_line_only() -> str:
    """The owner file with ONLY the third edit already applied, built from the script's own pair."""
    _label, old, new = _script().OWNER_EDITS[2]
    src = _source()
    assert src.count(old) == 1 and SALES_LINE not in src
    return src.replace(old, new, 1)


def test_a_source_already_carrying_the_sales_line_refuses_instead_of_doubling_it():
    with pytest.raises(ValueError, match="already"):
        _script().apply_owner_edits(_source_with_sales_line_only())


def test_owner_edits_on_a_version_with_the_sales_line_raises_and_saves_nothing():
    script = _script()
    with pg_session() as db:
        owner = _put_version(db, _source_with_sales_line_only())
        count = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count()
        with pytest.raises(ValueError, match="already"):
            script.owner_edits(db, from_version=owner.version, save=True)
        assert db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count() == count


def test_the_rebuild_commit_message_names_the_account_ledger_block():
    script = _script()
    with pg_session() as db:
        owner = _put_version(db, _source())
        built = script.build(db, from_version=owner.version, save=True)
        row = db.query(AIPromptVersion).filter(
            AIPromptVersion.name == KEY, AIPromptVersion.version == built["saved_version"]
        ).one()
        assert "ACCOUNT-LEDGER block" in row.commit_message


def test_the_pdyn_0003_commit_message_names_the_account_ledger_block():
    path = BACKEND / "alembic" / "versions" / "pdyn_0003_prod_identical.py"
    spec = importlib.util.spec_from_file_location("_oe_pdyn_0003", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "ACCOUNT-LEDGER block" in module.MESSAGE


@pytest.mark.parametrize("damage", ["purchase_cost | null", '|attachment_type"', "Domain purchase_cost "])
def test_an_anchor_that_is_not_there_exactly_once_refuses(damage):
    src = _source().replace(damage, damage.upper(), 1)
    with pytest.raises(ValueError, match="not found exactly once"):
        _script().apply_owner_edits(src)


def test_text_already_carrying_the_edits_refuses():
    once, _ = _script().apply_owner_edits(_source())
    with pytest.raises(ValueError):
        _script().apply_owner_edits(once)


def _with_edits(monkeypatch, edits):
    """The script module with OWNER_EDITS swapped; every anchor must hit the real file once."""
    module = _script()
    src = _source()
    for _label, old, _new in edits:
        assert src.count(old) == 1, old
    monkeypatch.setattr(module, "OWNER_EDITS", tuple(edits))
    return module


def _approved(module):
    return list(module.OWNER_EDITS)


def test_an_edit_that_inserts_an_extra_line_refuses(monkeypatch):
    first, second, third = _approved(_script())
    widened = (first[0], first[1], first[2] + "an unapproved extra line\n")
    module = _with_edits(monkeypatch, [widened, second, third])
    with pytest.raises(ValueError, match="more than the 3 approved lines"):
        module.apply_owner_edits(_source())


def test_an_edit_that_also_changes_a_neighbouring_line_refuses(monkeypatch):
    first, second, third = _approved(_script())
    # Line 82 is followed by a blank line; rewriting that blank line too touches a 4th line.
    spread = (first[0], first[1] + "\n", first[2] + "an unapproved neighbour line\n")
    module = _with_edits(monkeypatch, [spread, second, third])
    with pytest.raises(ValueError, match="more than the 3 approved lines"):
        module.apply_owner_edits(_source())


def test_only_two_of_the_three_edits_refuses(monkeypatch):
    first, second, _third = _approved(_script())
    module = _with_edits(monkeypatch, [first, second])
    with pytest.raises(ValueError, match="more than the 3 approved lines"):
        module.apply_owner_edits(_source())


def test_save_writes_one_unlabelled_plain_text_version_and_is_idempotent():
    script = _script()
    with pg_session() as db:
        owner = _put_version(db, _source())
        labels = {(l.label, l.version_id) for l in db.query(AIPromptLabel).filter(AIPromptLabel.name == KEY)}
        first = script.owner_edits(db, from_version=owner.version, save=True)
        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == first["saved_version"]).one()
        assert row.template == script.apply_owner_edits(_source())[0]
        assert not pv.is_wording_layer(row.template)  # plain text: no registry variable
        assert first["diff_lines"] == 3
        assert db.query(AIPromptLabel).filter(AIPromptLabel.version_id == row.id).count() == 0
        assert {(l.label, l.version_id) for l in db.query(AIPromptLabel).filter(AIPromptLabel.name == KEY)} == labels
        count = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count()
        again = script.owner_edits(db, from_version=owner.version, save=True)
        assert again["saved_version"] == first["saved_version"]
        assert db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count() == count


def test_a_dry_run_writes_nothing():
    script = _script()
    with pg_session() as db:
        owner = _put_version(db, _source())
        count = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count()
        result = script.owner_edits(db, from_version=owner.version, save=False)
        assert result["saved_version"] is None and result["diff_lines"] == 3
        assert db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count() == count


def test_rebuilt_from_the_edited_version_it_verifies_against_it():
    script = _script()
    with pg_session() as db:
        owner = _put_version(db, _source())
        edited = script.owner_edits(db, from_version=owner.version, save=True)["saved_version"]
        built = script.build(db, from_version=edited, save=True)
        assert built["identical"] is True
        assert script.verify(db, built["saved_version"], against_version=edited)["equal"] is True


def test_the_cli_takes_owner_edits_from():
    args = _script().parse_args(["--owner-edits-from", "54", "--save"])
    assert (args.owner_edits_from, args.save) == (54, True)
