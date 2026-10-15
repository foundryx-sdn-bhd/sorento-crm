"""AC-7: the promotion 409 conflict label prints the code alone when the name equals it."""
from types import SimpleNamespace

from app.services.marketing_service import _product_display_label


class _Db:
    def __init__(self, product):
        self._p = product

    def query(self, *_a):
        return self

    def filter(self, *_a):
        return self

    def first(self):
        return self._p


def _label(code, name):
    return _product_display_label(_Db(SimpleNamespace(product_code=code, product_name=name)), "pid")


def test_label_code_alone_when_name_equals_code():
    assert _label("1861", "1861") == "1861"
    assert _label("11X11", "11x11") == "11X11"


def test_label_keeps_differing_name():
    assert _label("SRT6641", "FLOOR TRAP") == "SRT6641 - FLOOR TRAP"


def test_label_falls_back_to_id_without_product():
    assert _product_display_label(_Db(None), "pid") == "pid"
