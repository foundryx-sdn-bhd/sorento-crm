"""SPEC-PROMO-PARAMS slice 2, D: the MCP presenters state a spec miss from the response only.

Card section 9, slice 4. Contract assumed (coordinator's brief):
  * `spec_alternatives`: `[{"key", "label", "unit", "values"}]`
  * `spec_unrecorded`: `{key: count of products with no value for that key}`
and the sentence text is read from the whole envelope (intro, items, any extra field), because the
brief does not say which envelope field carries it. Today the presenters ignore both keys:
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
def test_d4_a_size_miss_echoes_the_size_and_merges_every_axis(tool):
    """Owner ruling 13 Oct (option b): "No 250 mm. Sizes on file: 130 mm, 150 mm." One sorted list
    over every axis, the asked size echoed; the unrecorded count still follows."""
    out = text(
        tool,
        {
            "spec_size_asked": {"value": 250, "unit": "mm"},
            "spec_alternatives": [
                {"key": "dim_length", "label": "Length", "unit": "mm", "values": [150, 130]},
                {"key": "dim_width", "label": "Width", "unit": "mm", "values": [130]},
            ],
            "spec_unrecorded": {"size": 28},
        },
    )
    assert "no 250 mm. sizes on file: 130 mm, 150 mm." in out, out  # `text()` lowercases
    assert "values on file" not in out and "no exact match" not in out, out
    assert "28 have no size recorded." in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_d5_the_sizes_on_file_sort_as_numbers(tool):
    """Reviewer B2 (b6da364a): the list sorted as text ("1000 mm, 130 mm, 130.5 mm, 90 mm")."""
    out = text(tool, {
        "spec_size_asked": {"value": 250, "unit": "mm"},
        "spec_alternatives": [{"key": "dim_length", "label": "Length", "unit": "mm", "values": [1000, 130.5, 90, 130]}],
    })
    assert "sizes on file: 90 mm, 130 mm, 130.5 mm, 1000 mm." in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_d6_a_size_miss_over_a_family_names_it(tool):
    """Owner hand test round 2 (13 Oct): "250mm srtwc286 got stock?" reads "No 250 mm for
    SRTWC286. Sizes on file: 150 mm, 200 mm." when the route names the family (`subject`)."""
    out = text(tool, {
        "spec_size_asked": {"value": 250, "unit": "mm", "subject": "ZZTWC286"},
        "spec_alternatives": [{"key": "trap_length", "label": "Trap outlet length", "unit": "mm", "values": [200, 150]}],
    })
    assert "no 250 mm for zztwc286. sizes on file: 150 mm, 200 mm." in out, out
