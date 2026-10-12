"""CA-2 round 2: `ReportDefinition.pivot_rows_by` and its validation.

Contract: `pivot_rows_by: Optional[Dict[str, str]] = None`, keyed by the values of the
`datasets_by` param, value = the pivot rows column key for that value's dataset. `validate()`
raises ValueError when it is set without `datasets_by`, names a value the param does not offer,
or names a key that is not a dimension of that value's dataset. Built on `chat_overview`
(count_by: asks | products; the products dataset adds the `product` dimension).
"""
from __future__ import annotations

import dataclasses

import pytest


def _overview():
    import app.services.reports  # noqa: F401
    from app.services.reports import registry as reg

    definition = reg.get("chat_overview")
    assert definition is not None
    return reg, definition


def _with(**changes):
    reg, definition = _overview()
    return reg, dataclasses.replace(definition, **changes)


def test_the_field_defaults_to_none():
    _reg, definition = _overview()
    assert definition.pivot_rows_by is None


def test_a_valid_mapping_passes():
    reg, definition = _with(pivot_rows_by={"asks": "outcome", "products": "product"})
    reg.validate(definition)


def test_a_mapping_without_datasets_by_is_refused():
    reg, definition = _with(datasets_by=None, pivot_rows_by={"asks": "outcome"})
    with pytest.raises(ValueError):
        reg.validate(definition)


def test_a_value_the_param_does_not_offer_is_refused():
    reg, definition = _with(pivot_rows_by={"asks": "outcome", "zzt_unknown": "outcome"})
    with pytest.raises(ValueError):
        reg.validate(definition)


def test_a_key_that_is_not_a_dimension_of_that_values_dataset_is_refused():
    # `product` exists only on the products dataset, not on the asks one.
    reg, definition = _with(pivot_rows_by={"asks": "product"})
    with pytest.raises(ValueError):
        reg.validate(definition)


def test_a_measure_is_not_a_dimension():
    reg, definition = _with(pivot_rows_by={"products": "named"})
    with pytest.raises(ValueError):
        reg.validate(definition)


def test_a_key_in_no_dataset_is_refused():
    reg, definition = _with(pivot_rows_by={"products": "zzt_nothing"})
    with pytest.raises(ValueError):
        reg.validate(definition)
