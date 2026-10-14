"""AC-1/AC-7: the MCP presenter hides a product name that equals the code (case, spaces)."""
import sys
from pathlib import Path

# The backend venv's sorento_crm_mcp is an editable install from the primary checkout;
# put THIS checkout first so the import under test is this worktree's code.
_ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, _ROOT)
for _m in [m for m in sys.modules if m == "sorento_crm_mcp" or m.startswith("sorento_crm_mcp.")]:
    del sys.modules[_m]

import sorento_crm_mcp.presenters as presenters  # noqa: E402
from sorento_crm_mcp.presenters import _distinct_name  # noqa: E402


def test_import_resolves_to_this_checkout():
    assert str(Path(presenters.__file__).resolve()).startswith(_ROOT)


def test_case_difference_is_hidden():
    assert _distinct_name("11X11", "11x11") is None


def test_surrounding_spaces_are_hidden():
    assert _distinct_name(" 1861 ", "1861") is None


def test_differing_name_is_kept():
    assert _distinct_name("SRT6641", "FLOOR TRAP") == "FLOOR TRAP"
