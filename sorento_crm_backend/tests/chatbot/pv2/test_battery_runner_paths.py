"""PV2-EVAL: the in-process runner must import THIS worktree's sorento_crm_mcp, not the venv's
editable install pointing at the primary checkout."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from scripts.pv2_battery import runner


@pytest.fixture(autouse=True)
def _restore_import_state():
    saved_path = list(sys.path)
    saved_mods = {k: v for k, v in sys.modules.items() if k == "sorento_crm_mcp" or k.startswith("sorento_crm_mcp.")}
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for k in [k for k in sys.modules if k == "sorento_crm_mcp" or k.startswith("sorento_crm_mcp.")]:
            del sys.modules[k]
        sys.modules.update(saved_mods)


def _worktree_mcp() -> Path:
    return Path(runner.__file__).resolve().parents[3] / "sorento_crm_mcp"


def test_presenters_resolve_inside_worktree():
    runner.ensure_worktree_mcp()
    mod = importlib.import_module("sorento_crm_mcp.presenters")
    assert _worktree_mcp() in Path(mod.__file__).resolve().parents
    assert Path(sys.path[0]).resolve() == _worktree_mcp()


def test_returns_presenters_path():
    got = runner.ensure_worktree_mcp()
    mod = importlib.import_module("sorento_crm_mcp.presenters")
    assert Path(got).resolve() == Path(mod.__file__).resolve()


def test_drops_modules_imported_from_elsewhere(tmp_path):
    import types

    fake = types.ModuleType("sorento_crm_mcp")
    fake.__file__ = str(tmp_path / "sorento_crm_mcp" / "__init__.py")
    sys.modules["sorento_crm_mcp"] = fake
    runner.ensure_worktree_mcp()
    mod = importlib.import_module("sorento_crm_mcp.presenters")
    assert _worktree_mcp() in Path(mod.__file__).resolve().parents


def test_idempotent():
    first = runner.ensure_worktree_mcp()
    second = runner.ensure_worktree_mcp()
    assert Path(first).resolve() == Path(second).resolve()
    assert sys.path.count(str(_worktree_mcp())) == 1 or Path(sys.path[0]).resolve() == _worktree_mcp()
