"""AC-1 (backend twin): the product code/name display rule, pure functions, no DB."""
import pytest

from app.utils.product_display import code_with_name, distinct_name, same_display_text

TABLE = [
    ("11X11", "11X11", None, "11X11"),
    ("11X11", "11x11", None, "11X11"),
    ("1861", " 1861 ", None, "1861"),
    ("CWC  1009", "cwc 1009", None, "CWC  1009"),
    ("2102AW", "", None, "2102AW"),
    ("2102AW", None, None, "2102AW"),
    ("2102AW", "   ", None, "2102AW"),
    ("SRT6641", "SRT6641 FLOOR TRAP", "SRT6641 FLOOR TRAP", "SRT6641 - SRT6641 FLOOR TRAP"),
]


@pytest.mark.parametrize("code,name,distinct,joined", TABLE)
def test_ac1_table(code, name, distinct, joined):
    assert distinct_name(code, name) == distinct
    assert code_with_name(code, name, " - ") == joined


def test_default_separator_is_dash():
    assert code_with_name("SRT6641", "FLOOR TRAP") == "SRT6641 - FLOOR TRAP"


def test_custom_separator():
    assert code_with_name("SRT6641", "FLOOR TRAP", sep=" · ") == "SRT6641 · FLOOR TRAP"
    assert code_with_name("1861", "1861", sep=" · ") == "1861"


def test_same_display_text():
    assert same_display_text("11X11", "11x11") is True
    assert same_display_text("CWC  1009", "cwc 1009") is True
    assert same_display_text("SRT6641", "SRT6641 FLOOR TRAP") is False
