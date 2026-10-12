"""SPEC-PROMO-PARAMS slice 2, D: the MCP presenters state a spec miss from the response only.

Card section 9, slice 4. Contract assumed (coordinator's brief):
  * `spec_alternatives`: `[{"key", "label", "unit", "values"}]`
  * `spec_unrecorded`: `{key: count of products with no value for that key}`
  * `spec_ambiguous`: `[{"key": "size", "value": 250, "candidates": [...], "labels": [...]}]`
and the sentence text is read from the whole envelope (intro, items, any extra field), because the
brief does not say which envelope field carries it. Today the presenters ignore all three keys:
an empty `data` renders only "No matching results found.".
"""
import json

import pytest

from sorento_crm_mcp.presenters import present_response

TOOLS = ["crm_master_products_list", "crm_inventory_stock_balance_list"]
PAGING = {"pagination": {"total": 0, "page": 1, "limit": 50}, "empty": True}


def text(tool, body) -> str:
    """Everything the envelope says, lower-cased."""
    return json.dumps(json.loads(present_response(tool, json.dumps({"data": [], **PAGING, **body})))).lower()


@pytest.mark.parametrize("tool", TOOLS)
def test_d1_miss_names_the_existing_values_and_the_unrecorded_count(tool):
    """RED: the presenter ignores both keys, so none of the words appear."""
    out = text(
        tool,
        {
            "spec_alternatives": [{"key": "dim_length", "label": "Length", "unit": "mm", "values": [100, 150]}],
            "spec_unrecorded": {"dim_length": 3},
        },
    )
    assert "length" in out
    assert "100 mm" in out and "150 mm" in out
    assert "3" in out and "recorded" in out


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("unrecorded", [None, {}, {"dim_length": 0}])
def test_d2_no_unrecorded_sentence_when_nothing_is_unrecorded(tool, unrecorded):
    """The alternatives still print (RED on that assertion today); no 'recorded' sentence at all."""
    body = {"spec_alternatives": [{"key": "dim_length", "label": "Length", "unit": "mm", "values": [100]}]}
    if unrecorded is not None:
        body["spec_unrecorded"] = unrecorded
    out = text(tool, body)
    assert "100 mm" in out
    assert "recorded" not in out


@pytest.mark.parametrize("tool", TOOLS)
def test_d3_ambiguous_size_asks_which_axis(tool):
    """RED: the presenter ignores `spec_ambiguous`, so it prints the generic no-results line."""
    out = text(
        tool,
        {
            "spec_ambiguous": [
                {"key": "size", "value": 250, "candidates": ["dim_length", "dim_width"], "labels": ["Length", "Width"]}
            ]
        },
    )
    assert "250 mm" in out
    assert "length" in out and "width" in out
    assert " or " in out
    assert "no matching results" not in out
