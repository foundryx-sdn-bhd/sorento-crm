"""EVAL-BENCH: the in-process replay installs the company scope listeners, and refuses without them.

`replay.py run|live` drives `engine.run_turn` in this process. Only app/main.py and worker.py
register the SELECT filter, so a bench run read the candidate / resolver / breakdown tables with
no company or brand scope while the MCP fetch (HTTP, scoped) was scoped. Synthetic data only.
"""
from __future__ import annotations

import pytest

from app.models.base import set_brand_scope, set_company_scope
from app.models.product import Brand, Product
from app.services import company_scope as cs
from app.services.company_scope import DEFAULT_COMPANY_ID
from scripts.chatbot_eval import replay as replay_mod
from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import seeded, stub_access  # noqa: F401
from tests.chatbot_eval.test_replay import recorded  # noqa: F401
from tests.chatbot_eval.test_replay_failfast import _cli, _preflight
from tests.chatbot_eval.test_replay_live import live_env  # noqa: F401


@pytest.fixture
def fresh_flag(monkeypatch):
    """A process where nothing has registered yet; a spy stands in for the real registration."""
    calls: list[int] = []

    def spy() -> None:
        calls.append(1)
        monkeypatch.setattr(cs, "_INSTALLED", True)

    monkeypatch.setattr(cs, "_INSTALLED", False)
    monkeypatch.setattr(cs, "register_company_scope_listeners", spy)
    return calls


@pytest.mark.parametrize("cmd", ["run", "live"])
def test_red_the_command_registers_the_scope_listeners(cmd, fresh_flag, tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):  # noqa: F811
    _preflight(monkeypatch, {"a1"}, {"a1"})
    _cli(cmd, tmp_path, session_factory, recorded, monkeypatch)
    assert fresh_flag, "register_company_scope_listeners was never called"
    assert cs._INSTALLED is True


@pytest.mark.parametrize("cmd", ["run", "live"])
def test_red_the_preflight_refuses_when_the_listeners_are_not_installed(cmd, tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):  # noqa: F811
    _preflight(monkeypatch, {"a1"}, {"a1"})
    monkeypatch.setattr(cs, "_INSTALLED", False)
    monkeypatch.setattr(cs, "register_company_scope_listeners", lambda: None)
    rc, out = _cli(cmd, tmp_path, session_factory, recorded, monkeypatch)
    text = capsys.readouterr()
    shown = text.out + text.err
    assert rc == 2, shown
    assert "scope" in shown, shown
    assert (not out.exists()) or out.read_text() == ""


def test_red_a_replay_session_does_not_read_another_companys_or_brand_s_product(session_factory, monkeypatch):  # noqa: F811
    """After the runner's registration, a plain query on a session scoped the way the engine's
    scoped factory scopes it hides the other company's product and the out-of-scope brand."""
    replay_mod._register_scope()   # idempotent; makes the test independent of which test ran first
    seed = session_factory()
    seed_mocha(seed)
    own = product(seed, company_id=DEFAULT_COMPANY_ID, code="ZZSCOPEOWN")
    other = product(seed, company_id=MOCHA_ID, code="ZZSCOPEOTHER")
    wrong_brand = product(seed, company_id=DEFAULT_COMPANY_ID, code="ZZSCOPEBRAND")
    keep = Brand(id="00000000-0000-0000-0000-0000000000b1", brand_code="ZZSB1", brand_name="ZZ keep",
                 company_id=DEFAULT_COMPANY_ID)
    drop = Brand(id="00000000-0000-0000-0000-0000000000b2", brand_code="ZZSB2", brand_name="ZZ drop",
                 company_id=DEFAULT_COMPANY_ID)
    seed.add_all([keep, drop])
    seed.flush()
    own.brand_id, wrong_brand.brand_id = keep.id, drop.id
    seed.commit()

    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    set_brand_scope(db, frozenset({keep.id}))
    codes = {p.product_code for p in db.query(Product).filter(Product.product_code.like("ZZSCOPE%"))}
    assert codes == {"ZZSCOPEOWN"}, codes
