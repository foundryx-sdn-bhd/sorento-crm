"""AC-7: printed tag text and SCM policy label drop a name that equals the code. Pure, no DB."""
from app.services.dealer_kit.tag_data_service import _package_text, _set_member_text
from app.services.scm.policy_service import _label


def test_set_member_text_drops_name_equal_to_code():
    out = _set_member_text(
        [{"code": "CWCX1009-RL", "name": "CWCX1009-RL", "dimensions": "800 x 500 mm"}]
    )
    assert out == "- CWCX1009-RL 800 x 500 mm"
    assert "(" not in out


def test_set_member_text_drops_name_equal_ignoring_case():
    out = _set_member_text([{"code": "11X11", "name": "11x11", "dimensions": None}])
    assert out == "- 11X11"


def test_set_member_text_keeps_differing_name():
    out = _set_member_text(
        [{"code": "SRT6641", "name": "FLOOR TRAP", "dimensions": "150 x 150 mm"}]
    )
    assert out == "- SRT6641 (FLOOR TRAP) 150 x 150 mm"


def test_package_text_drops_name_equal_to_code():
    out = _package_text(
        [{"code": "1861", "name": "1861", "dimensions": "10 x 10 mm"}], []
    )
    assert out == "+ 1861 10 x 10 mm"


def test_package_text_keeps_differing_name():
    out = _package_text(
        [{"code": "SRT6641", "name": "FLOOR TRAP", "dimensions": "10 x 10 mm"}], []
    )
    assert out == "+ SRT6641 FLOOR TRAP 10 x 10 mm"


def _sku_row(code, name):
    return {
        "scope_type": "sku",
        "scope_ref": "ref",
        "product_code": code,
        "product_name": name,
        "category_name": None,
        "category_code": None,
    }


def test_policy_label_sku_code_alone_when_name_equals_code():
    assert _label(_sku_row("1861", "1861")) == "1861"


def test_policy_label_sku_keeps_differing_name():
    assert _label(_sku_row("1861", "X")) == "1861 · X"
