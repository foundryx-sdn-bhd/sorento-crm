"""Phase 2 RED tests - the report-ask catalogue (lane REPORT-ENGINE, slice 1a).

`documentation/plans/chatbot/PLAN-report-engine.md` section 10 "Catalogue / datasets".

`app.services.reports.ask.CATALOGUE` maps the spec's words to each definition's columns and
param keys. These tests pin that every word resolves to something that really exists in the
definition it names, so a renamed column cannot leave a dead word behind.

Owner direction 4 Oct 2026: every basis (delivered, ordered, outstanding) reads SALES ORDER lines,
so the catalogue holds three bases and none of them points at the delivery order dataset.

Ambiguity flagged to the captain: PLAN section 10 does not fix the shape of a CATALOGUE entry.
Pinned here (tolerantly): an entry exposes `dimensions` and `filters`, each either a mapping
`spec word -> column / param key` or a plain collection of words that ARE the keys. Entries may
be dicts or objects.
"""
from __future__ import annotations

import pytest


def _field(entry, name):
    return entry[name] if isinstance(entry, dict) else getattr(entry, name)


def _keys(collection):
    """The definition-side keys a `spec word -> key` mapping (or a bare word list) points at."""
    return list(collection.values()) if isinstance(collection, dict) else list(collection)


BASES = ["delivered", "ordered", "outstanding"]


def _definition(basis):
    from app.services.reports.ask import CATALOGUE as _DATASETS

    CATALOGUE = _DATASETS["sales"]

    return _field(CATALOGUE[basis], "definition")


def _definitions():
    """Every basis the catalogue HAS (the missing ones are asserted by the three-bases test)."""
    from app.services.reports.ask import CATALOGUE as _DATASETS

    CATALOGUE = _DATASETS["sales"]

    return {basis: _field(CATALOGUE[basis], "definition") for basis in BASES if basis in CATALOGUE}


def test_catalogue_has_the_three_bases():
    from app.services.reports.ask import CATALOGUE as _DATASETS

    CATALOGUE = _DATASETS["sales"]

    assert set(CATALOGUE) == set(BASES), set(CATALOGUE)


def test_the_basis_labels():
    from app.services.reports.ask import CATALOGUE as _DATASETS

    CATALOGUE = _DATASETS["sales"]

    assert {b: _field(CATALOGUE[b], "label") for b in BASES} == {
        "delivered": "delivered sales", "ordered": "ordered sales", "outstanding": "outstanding orders"}


def test_no_basis_reads_the_delivery_order_dataset():
    from app.services.reports.datasets.delivery_order_lines import DEFINITION as delivery_orders

    for basis, definition in _definitions().items():
        assert definition is not delivery_orders, basis
        assert definition.dataset.key != delivery_orders.dataset.key, basis


@pytest.mark.parametrize("basis", BASES)
def test_every_dimension_word_maps_to_a_dimension_column(basis):
    from app.services.reports.ask import CATALOGUE as _DATASETS

    CATALOGUE = _DATASETS["sales"]

    definition = _definition(basis)
    dimensions = _keys(_field(CATALOGUE[basis], "dimensions"))
    assert dimensions, basis
    tagged = {c.key for c in definition.dataset.columns if c.tag == "dimension"}
    for key in dimensions:
        assert key in tagged, (basis, key, sorted(tagged))


@pytest.mark.parametrize("basis", BASES)
def test_every_filter_word_maps_to_a_select_param(basis):
    from app.services.reports import registry as reg
    from app.services.reports.ask import CATALOGUE as _DATASETS

    CATALOGUE = _DATASETS["sales"]

    definition = _definition(basis)
    filters = _keys(_field(CATALOGUE[basis], "filters"))
    assert filters, basis
    select_keys = {p.key for p in definition.params if isinstance(p, reg.SelectParam)}
    for key in filters:
        assert key in select_keys, (basis, key, sorted(select_keys))


@pytest.mark.parametrize("basis", BASES)
def test_both_bases_speak_the_same_words(basis):
    """One spec, two bases: the words a caller may use are the same for both."""
    from app.services.reports.ask import CATALOGUE as _DATASETS

    CATALOGUE = _DATASETS["sales"]

    words = {"customer", "product", "brand", "category", "sales_agent", "location", "channel", "month"}
    dims = _field(CATALOGUE[basis], "dimensions")
    assert words <= set(dims), (basis, sorted(set(dims)))


def test_dealer_keys():
    from app.services.reports.ask import DEALER_KEYS

    assert set(DEALER_KEYS) == {"product", "brand", "category", "month"}, DEALER_KEYS


def test_no_ask_definition_is_on_the_reports_screen():
    """The Reports screen catalogue is unchanged: no ask basis registers anywhere."""
    from app.services.reports.registry import all_definitions

    registered = {d.key for d in all_definitions()}
    for basis, definition in _definitions().items():
        assert definition.key not in registered, (basis, registered)
        assert all(d is not definition for d in all_definitions()), basis
