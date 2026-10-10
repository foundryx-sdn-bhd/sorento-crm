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


# --------------------------------------------------------------------------- brand names in the prompt (final live Q11)


def _system(provider) -> str:
    return next(m for m in provider.calls[0]["messages"] if m["role"] == "system")["content"].lower()


def test_the_system_prompt_lists_the_brand_names_the_company_holds(configured):
    """Pinned design: the extractor reads the brand names ITSELF from the `db` it is given (the
    way it reads `chatbot_domains`); the signature does not change. A brand row's name is in the
    system prompt it sends."""
    import uuid

    from app.models.product import Brand

    tag = uuid.uuid4().hex[:6].upper()
    for name in (f"ZZT Sorento {tag}", f"ZZT Cabana {tag}"):
        configured.add(Brand(id=str(uuid.uuid4()), brand_code=f"ZB{uuid.uuid4().hex[:6]}", brand_name=name))
    configured.commit()
    provider = _Provider(json.dumps(GOOD))
    _run(configured, provider)
    system = _system(provider)
    assert f"zzt sorento {tag}".lower() in system and f"zzt cabana {tag}".lower() in system, "brand names missing"


def test_the_prompt_teaches_a_brand_name_goes_to_brand_words_never_product_words():
    """Structural pin, loose wording."""
    text = PROMPT_KEYS["chat_report_extractor"].fallback().lower()
    at = text.find("brand name")
    assert at != -1, "no teaching about brand names"
    near = text[max(0, at - 300): at + 400]
    assert "brand_words" in near and "product_words" in near and ("never" in near or "not" in near), near


def test_the_prompt_teaches_customers_and_dealers_mean_the_dealer_asker_only():
    """Owner ruling 11 Oct: a "customer" is a contact with a linked customer (the dealer class);
    unclassified askers are not customers. Structural pin, loose wording."""
    text = PROMPT_KEYS["chat_report_extractor"].fallback().lower()
    for word in ("pelanggan", "客户", "经销商", "peniaga"):
        assert word in text, word
    at = text.find("pelanggan")
    near = text[max(0, at - 500): at + 500]
    assert '["dealer"]' in near or "askers dealer" in near or "dealer only" in near or "only dealer" in near, near
    assert "unclassified" in near and ("never" in near or "not" in near), near


# --------------------------------------------------------------------------- product kinds in the prompt (live Q12)


def test_the_system_prompt_lists_the_category_kinds_the_company_holds(configured):
    """Pinned design: like the brand names, the extractor reads the DISTINCT class labels of the
    product categories from the `db` it is given. Two rows sharing a label list it once."""
    import uuid

    from app.models.product import ProductCategory

    tag = uuid.uuid4().hex[:6].upper()
    wc, wb = f"ZZT Water Closet {tag}", f"ZZT Wash Basin {tag}"
    for label in (wc, wc, wb):
        code = f"ZZ{uuid.uuid4().hex[:8].upper()}"
        configured.add(ProductCategory(id=str(uuid.uuid4()), category_code=code, category_name=code, class_label=label))
    configured.commit()
    provider = _Provider(json.dumps(GOOD))
    _run(configured, provider)
    system = _system(provider)
    assert wc.lower() in system and wb.lower() in system, "category class labels missing"
    assert system.count(wc.lower()) == 1, system.count(wc.lower())


def test_the_prompt_teaches_a_product_kind_goes_to_category_words_never_product_words():
    """Structural pin, loose wording."""
    text = PROMPT_KEYS["chat_report_extractor"].fallback().lower()
    hits = [i for i in range(len(text)) if text.startswith("category label", i) or text.startswith("kind of product", i)]
    assert hits, "no teaching about product kinds / category labels"
    assert any(
        "category_words" in (near := text[max(0, at - 300): at + 400])
        and "product_words" in near and ("never" in near or "not" in near)
        for at in hits
    ), text


# --------------------------------------------------------------------------- "kind" belongs to the asker rule (live regression, prompt v7)


def test_the_prompt_teaches_asked_by_a_role_sets_askers_whatever_the_axis():
    """'top categories asked by salesperson' ranks categories AND limits who asked: askers is set
    whatever the group_by. Structural pin on the askers bullet, loose wording."""
    text = PROMPT_KEYS["chat_report_extractor"].fallback().lower()
    at = text.find("- askers:")
    assert at != -1, "no askers bullet"
    end = text.find("\n- ", at + 1)
    bullet = text[at: end if end != -1 else at + 600]
    assert any(w in bullet for w in ("whatever", "any group_by", "also when", "even when")) and "categor" in bullet, bullet


def test_the_product_type_teaching_does_not_reuse_the_asker_word_kind():
    """The asker rule owns the word 'kind'; the category-label / plain-product-word sentence must
    not reuse it (v7 did, and 'asked by salesperson' stopped reading as askers)."""
    text = PROMPT_KEYS["chat_report_extractor"].fallback().lower()
    hits = [i for i in range(len(text)) if text.startswith("category label", i)]
    assert hits, "no teaching about category labels"
    for at in hits:
        near = text[max(0, at - 120): at + 120]
        assert "kind" not in near, near
