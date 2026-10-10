"""CA-3 owner live run at 58e50c3f4, RED (Q11, Q12): the extractor reads customer and product
filter words itself (`customer_words`, `product_words`: arrays of strings, `[]` when none), so the
lane no longer filters by what the parser tagged; and its prompt teaches that "top discussed /
most asked <kind>" ranks PRODUCTS with the kind as a category filter. No network."""
from __future__ import annotations

import json

import pytest

from app.services.ai_prompt_registry import PROMPT_KEYS
from tests.chatbot.test_chat_report_extractor import (  # noqa: F401  (fixtures and helpers by name)
    _Provider,
    _run,
    configured,
    db,
)

GOOD = {
    "group_by": "product", "measure": "asks", "subject": None, "top_n": 5,
    "date_from": "2026-01-01", "date_to": "2026-12-31",
    "askers": [], "category_words": ["water closet"], "brand_words": ["sorento"],
    "customer_words": ["zzt trading"], "product_words": ["zzt-1001"],
}


def test_a_reading_with_customer_and_product_words_round_trips(configured):
    out = _run(configured, _Provider(json.dumps(GOOD)))
    assert out == GOOD, out


def test_the_schema_requires_and_declares_the_two_new_arrays(configured):
    provider = _Provider(json.dumps(GOOD))
    _run(configured, provider)
    schema = provider.calls[0]["json_schema"]
    schema = schema.get("schema", schema)
    assert {"customer_words", "product_words"} <= set(schema["required"]), schema["required"]
    for key in ("customer_words", "product_words"):
        prop = schema["properties"][key]
        assert prop["type"] == "array" and prop["items"]["type"] == "string", prop


@pytest.mark.parametrize("bad", [{"customer_words": "zzt"}, {"product_words": [1]}, {"customer_words": None}])
def test_a_non_list_word_field_is_invalid(configured, bad):
    assert _run(configured, _Provider(json.dumps({**GOOD, **bad}))) is None


def test_the_prompt_teaches_top_discussed_kind_ranks_products_filtered_by_category():
    """Structural pin, loose wording: 'top discussed / most asked <product kind>' is group_by
    product with that kind as a category filter."""
    text = PROMPT_KEYS["chat_report_extractor"].fallback().lower()
    at = max(text.find("top discussed"), text.find("most discussed"))
    assert at != -1, "no 'top discussed / most discussed' teaching"
    near = text[max(0, at - 300): at + 400]
    assert "product" in near and "category" in near, near
