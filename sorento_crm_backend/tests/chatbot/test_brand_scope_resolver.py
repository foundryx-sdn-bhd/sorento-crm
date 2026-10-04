"""Phase 2 RED tests - in-process resolver paths for a scoped turn (AC-8, AC-9, AC-10).

`PLAN-contact-brand-scope-4oct.md` test item 6. The turn engine calls the resolver in
process on a session it stamped itself (`engine._scoped_factory`), so the REST dependency
never runs; the session carries `set_company_scope` + `set_brand_scope`, as here.

Every assertion reads the whole response as text: the SORENTO / unbranded product must not
appear anywhere in it (match, alternative, did-you-mean, member, pick option).
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.models.base import set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._brand_scope_seed import db, world  # noqa: F401  (fixtures by name)


def _scoped(db, world, *, scoped: bool = True):
    from app.models.base import set_brand_scope

    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    set_brand_scope(db, frozenset({world.mocha.id}) if scoped else None)
    return db


def _resolve(db, tokens, *, types=("product",)):
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post

    body = ResolveReferenceRequest(query=" ".join(tokens), tokens=list(tokens), allowed_entity_types=list(types))
    return resolve_reference_post(body, current_user={}, db=db)


def _text(payload) -> str:
    import re

    return re.sub(r'"elapsed_ms": [0-9.eE+-]+', '"elapsed_ms": 0', json.dumps(payload, default=str))


def _near_miss(code: str) -> str:
    """One character off the end: an exact miss that only the trigram tier can recover."""
    return code[:-1] + ("a" if code[-1] != "a" else "b")


def test_exact_code_of_an_in_scope_product_resolves(db, world) -> None:
    out = _text(_resolve(_scoped(db, world), [world.codes["mocha"]]))
    assert str(world.p_mocha.id) in out


@pytest.mark.parametrize("which", ["sorento", "null"])
def test_exact_code_of_an_out_of_scope_product_resolves_to_nothing(db, world, which) -> None:
    """AC-8: no match, and the product's id and code are nowhere in the reply."""
    scoped = _scoped(db, world)
    ghost = "ZZT-GHOST-" + uuid.uuid4().hex[:6].upper()
    out = _text(_resolve(scoped, [world.codes[which]]))
    miss = _text(_resolve(scoped, [ghost]))
    product = {"sorento": world.p_sorento, "null": world.p_null}[which]
    assert str(product.id) not in out
    assert out.count(world.codes[which]) == miss.count(ghost), "only the typed token echoes, as for a ghost"


@pytest.mark.parametrize("which", ["sorento", "null"])
def test_out_of_scope_code_replies_exactly_like_a_nonexistent_code(db, world, which) -> None:
    """AC-8 / Q4: identical payload to a ghost code once the typed token is masked."""
    scoped = _scoped(db, world)
    code, ghost = world.codes[which], "ZZT-GHOST-" + uuid.uuid4().hex[:6].upper()
    out = _text(_resolve(scoped, [code])).replace(code, "<X>")
    miss = _text(_resolve(scoped, [ghost])).replace(ghost, "<X>")
    assert out == miss


def test_trigram_did_you_mean_never_offers_an_out_of_scope_product(db, world) -> None:
    """AC-9: a near-miss of the SORENTO code is recoverable unscoped (so this test can fail),
    and offers no SORENTO product to the MOCHA-only contact."""
    typo = _near_miss(world.codes["sorento"])
    assert world.codes["sorento"] in _text(_resolve(_scoped(db, world, scoped=False), [typo])), (
        "fixture: the unscoped trigram tier must suggest the SORENTO code, else this proves nothing"
    )
    scoped_out = _text(_resolve(_scoped(db, world), [typo]))
    assert world.codes["sorento"] not in scoped_out
    assert str(world.p_sorento.id) not in scoped_out


def test_trigram_did_you_mean_never_offers_an_unbranded_product(db, world) -> None:
    typo = _near_miss(world.codes["null"])
    assert world.codes["null"] in _text(_resolve(_scoped(db, world, scoped=False), [typo]))
    out = _text(_resolve(_scoped(db, world), [typo]))
    assert world.codes["null"] not in out and str(world.p_null.id) not in out


def test_trigram_still_offers_the_in_scope_neighbour(db, world) -> None:
    out = _text(_resolve(_scoped(db, world), [_near_miss(world.codes["mocha"])]))
    assert world.codes["mocha"] in out


def test_resolve_product_set_members_are_in_scope_only(db, world) -> None:
    """AC-7 / AC-10: a described set built from product ids drops out-of-scope members."""
    from app.services.product_predicate_service import resolve_product_set

    ids = [str(p.id) for p in world.products]
    full = _text(resolve_product_set(_scoped(db, world, scoped=False), require={}, product_ids=ids))
    assert world.codes["sorento"] in full, "fixture: unscoped set must hold the SORENTO product"
    out = _text(resolve_product_set(_scoped(db, world), require={}, product_ids=ids))
    assert world.codes["mocha"] in out
    assert world.codes["sorento"] not in out and world.codes["null"] not in out


def test_chat_visible_product_ids_drops_out_of_scope_ids(db, world) -> None:
    """PLAN slice 2b: the raw-SQL tiers' post-check (`_chat_visible_product_ids`) is the one
    place a trigram/embedding id list is cleaned; it must drop out-of-scope ids."""
    from app.services.entity_resolver import _chat_visible_product_ids

    ids = [str(p.id) for p in world.products]
    assert _chat_visible_product_ids(_scoped(db, world, scoped=False), ids) == set(ids)
    assert _chat_visible_product_ids(_scoped(db, world), ids) == {str(world.p_mocha.id)}


def test_chat_searchable_products_respects_the_session_scope(db, world) -> None:
    """PLAN slice 2b: `chat_searchable_products()` callers get the brand predicate."""
    from app.models.product import Product, chat_searchable_products

    rows = db.query(Product.product_code).filter(
        Product.product_code.in_(list(world.codes.values())), chat_searchable_products()
    )
    assert {r[0] for r in rows} == set(world.codes.values())
    _scoped(db, world)
    rows = db.query(Product.product_code).filter(
        Product.product_code.in_(list(world.codes.values())), chat_searchable_products()
    )
    assert {r[0] for r in rows} == {world.codes["mocha"]}
