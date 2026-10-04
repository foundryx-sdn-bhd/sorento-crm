"""Phase 2 RED tests - AC-15: escalation product suggestions and focus product, scoped turn.

`escalation_services.product_suggestions` (did-you-mean for a code the resolver cannot place)
and `focus_product_origin` (brand/company of the focus products, for routing) run on the
turn's session; with a MOCHA brand scope stamped they must name nothing SORENTO or unbranded.
"""
from __future__ import annotations

from app.models.base import set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._brand_scope_seed import db, world  # noqa: F401  (fixtures by name)


def _session(db, world, *, scoped: bool):
    from app.models.base import set_brand_scope

    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    set_brand_scope(db, frozenset({world.mocha.id}) if scoped else None)
    return db


def _typo(code: str) -> str:
    return code[:-1] + ("a" if code[-1] != "a" else "b")


def _suggest(db, code):
    from app.services.chatbot.lanes.escalation_services import product_suggestions

    return product_suggestions(db, code)


def test_suggestions_unscoped_control_offers_the_other_brand_and_unbranded(db, world) -> None:
    assert world.codes["sorento"] in _suggest(_session(db, world, scoped=False), _typo(world.codes["sorento"]))
    assert world.codes["null"] in _suggest(db, _typo(world.codes["null"]))


def test_suggestions_scoped_offer_no_sorento_or_unbranded_code(db, world) -> None:
    scoped = _session(db, world, scoped=True)
    assert world.codes["sorento"] not in _suggest(scoped, _typo(world.codes["sorento"]))
    assert world.codes["null"] not in _suggest(scoped, _typo(world.codes["null"]))


def test_suggestions_scoped_still_offer_the_in_scope_neighbour(db, world) -> None:
    assert world.codes["mocha"] in _suggest(_session(db, world, scoped=True), _typo(world.codes["mocha"]))


def _origin(db, code):
    from app.services.chatbot.lanes.escalation_services import focus_product_origin

    return focus_product_origin(db, [{"hint": "product", "raw": code, "canonical_code": code}])


def test_focus_product_unscoped_control_finds_the_sorento_brand(db, world) -> None:
    out = _origin(_session(db, world, scoped=False), world.codes["sorento"])
    assert out["brand"] == world.sorento.brand_code.lower(), out


def test_focus_product_scoped_treats_an_out_of_scope_code_as_not_found(db, world) -> None:
    scoped = _session(db, world, scoped=True)
    for which in ("sorento", "null"):
        out = _origin(scoped, world.codes[which])
        assert out["brand"] is None and out["company"] is None, out
        assert world.codes[which].upper() in out["not_found"], out


def test_focus_product_scoped_still_resolves_the_in_scope_code(db, world) -> None:
    out = _origin(_session(db, world, scoped=True), world.codes["mocha"])
    assert out["brand"] == world.mocha.brand_code.lower(), out
