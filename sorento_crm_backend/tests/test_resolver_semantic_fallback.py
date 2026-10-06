"""RESOLVE-SEMANTIC-FALLBACK, the resolver half (owner report, 6 Oct 2026 20:02 MYT):
"TAP SRTWT5608 x 5" reached the resolver as the folded token "TAPSRTWT5608", matched no
code tier (exact / prefix / substring on product_code) and came back "I could not find
TAP SRTWT5608" while SRTWT5608 exists.

The shared resolver (`entity_resolver.resolve_references`, behind `POST /api/v1/system/
references/resolve` for the chatbot, n8n and every API-key caller) now asks a code reader
ONE narrow question about a product-typed token that missed every code tier, and acts on
a structured answer:

* `code`    - the exact tier is re-run with the value; a hit resolves the token;
* `family`  - the prefix-then-substring product probe runs with the value; one row is
              that code, several are the ambiguous picker, none is still a miss;
* `none`    - the token stays unresolved, the existing not-found wording is untouched.

Rules pinned here: never asked on a clean message; only product-typed tokens; the value
must sit inside what the dealer typed (the catalogue decides existence, the model may not
invent or correct); a reader failure is a miss, never a failed call; byte identity for a
call without a reader (no `fallback` key anywhere); the route asks ONCE per request even
when `fallback_to_all_types` re-runs the unresolved tokens; `semantic_fallback: false`
opts a caller out.

The reader itself (the provider call, its budget and its guard) is `tests/
test_resolver_code_reader.py`. Fake ZZT fixture codes only (public repo).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from ._pg_fixture import blank_session, unique_code

TAP = "ZZTWT5608"
FAMILY = ["ZZTWC8066-RL", "ZZTWC8066-SH", "ZZTWC8066-SH-150"]
CODES = [TAP, *FAMILY, "ZZTWC286-SH"]


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


@pytest.fixture
def db():
    with blank_session() as session:
        current = session.execute(text("SHOW search_path")).scalar()
        session.execute(text(f"SET LOCAL search_path TO {current}, public"))
        set_company_scope(session, None)
        cat, uom = str(uuid.uuid4()), str(uuid.uuid4())
        session.add(ProductCategory(id=cat, category_code=unique_code("C")[:50], category_name="C"))
        session.add(UnitOfMeasure(id=uom, uom_code=unique_code("U")[:20], uom_name="Each"))
        session.flush()
        for code in CODES:
            session.add(
                Product(
                    id=str(uuid.uuid4()), product_code=code, product_name=code, category_id=cat,
                    base_uom_id=uom, list_price=10, is_active=True, company_id=DEFAULT_COMPANY_ID,
                )
            )
        session.commit()
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


class FakeReader:
    """A `CodeReader`: answers from `answers` ({token: (kind, value)}), counts its calls and
    remembers what it was asked (the `(token, typed text)` pairs)."""

    def __init__(self, answers: dict[str, tuple[str, str | None]] | None = None, *, raises: bool = False):
        from app.services.resolver_code_reader import CodeReading

        self._reading = CodeReading
        self.answers = answers or {}
        self.raises = raises
        self.calls: list[list[tuple[str, str | None]]] = []

    def __call__(self, missed):
        self.calls.append(list(missed))
        if self.raises:
            raise RuntimeError("provider down")
        out = {}
        for token, _typed in missed:
            kind, value = self.answers.get(token, ("none", None))
            out[token] = self._reading(kind=kind, value=value)
        return out


def _resolve(db, tokens, raws=None, *, reader=None, types=None):
    from app.services.entity_resolver import resolve_references

    return resolve_references(
        db,
        tokens,
        allowed_entity_types=types if types is not None else ["product"] * len(tokens),
        raw_tokens=raws,
        code_reader=reader,
    )


def _by_token(result) -> dict[str, Any]:
    return {r["token"]: r for r in result.as_dict()["resolutions"]}


def _product(db, code: str) -> None:
    """One more searchable product in the fixture's category and unit."""
    template = db.query(Product).filter(Product.product_code == TAP).one()
    db.add(
        Product(
            id=str(uuid.uuid4()), product_code=code, product_name=code, category_id=template.category_id,
            base_uom_id=template.base_uom_id, list_price=10, is_active=True, company_id=DEFAULT_COMPANY_ID,
        )
    )


def _codes(resolution: dict[str, Any]) -> list[str]:
    return [m["canonical_code"] for m in resolution["matches"]]


# --------------------------------------------------------------------------- #
# Never on a clean message, never for a non-product token
# --------------------------------------------------------------------------- #


def test_a_clean_message_never_asks_the_reader(db):
    reader = FakeReader()
    result = _resolve(db, [TAP, "ZZTWC286SH"], [TAP, "ZZTWC286-SH"], reader=reader)

    assert reader.calls == []
    assert result.unresolved_tokens == []
    assert all("fallback" not in r for r in result.as_dict()["resolutions"])


def test_a_family_the_substring_tier_already_placed_is_not_a_miss(db):
    """"8066" sits inside the ZZTWC8066 codes: the existing substring probe places the
    family and flags it ambiguous (the picker); the reader is never asked."""
    reader = FakeReader()
    result = _resolve(db, ["8066"], reader=reader)

    assert reader.calls == []
    assert result.ambiguous_tokens == ["8066"]
    assert sorted(_codes(_by_token(result)["8066"])) == FAMILY


def test_a_non_product_token_is_never_asked(db):
    reader = FakeReader({"TAPZZTWT5608": ("code", TAP)})
    result = _resolve(db, ["TAPZZTWT5608"], ["TAP ZZTWT5608"], reader=reader, types=["customer"])

    assert reader.calls == []
    assert result.unresolved_tokens == ["TAPZZTWT5608"]


def test_a_lone_brand_hinted_miss_is_not_asked_even_as_the_only_token(db):
    """One token, one hint: positional and set readings are the same thing, so a lone
    brand- or category-hinted miss under a product domain is still not a product
    question (reviewer, third pass, finding 2)."""
    reader = FakeReader({"ZZQBRANDX": ("code", "ZZQBRANDX")})
    from app.services.entity_resolver import resolve_references

    result = resolve_references(db, ["ZZQBRANDX"], allowed_entity_types=["brand"], domain_hint="product", raw_tokens=["ZZQBRANDX"], code_reader=reader)
    assert reader.calls == []
    assert "fallback" not in _by_token(result)["ZZQBRANDX"]


def test_a_customer_hinted_miss_beside_a_product_is_not_asked(db):
    """One hint per token (the chatbot's body): the request-wide type union includes
    product, but the customer token's OWN hint is customer, so only the product token is a
    question (reviewer finding 2)."""
    reader = FakeReader({"TAPZZTNOPE1": ("none", None), "KEDAI88": ("family", "88")})
    result = _resolve(
        db, ["KEDAI88", "TAPZZTNOPE1"], ["Kedai 88", "TAP ZZTNOPE1"], reader=reader,
        types=["customer", "product"],
    )

    assert reader.calls == [[("TAPZZTNOPE1", "TAP ZZTNOPE1")]], reader.calls
    rows = _by_token(result)
    assert "fallback" not in rows["KEDAI88"]
    assert rows["TAPZZTNOPE1"]["fallback"]["kind"] == "none"


def test_an_ambiguous_token_is_not_a_miss_and_is_not_asked(db):
    reader = FakeReader()
    _resolve(db, ["ZZTWC8066"], reader=reader)
    assert reader.calls == []


# --------------------------------------------------------------------------- #
# The owner's turn: a type word beside the code
# --------------------------------------------------------------------------- #


def test_a_code_reading_resolves_the_token_on_the_exact_tier(db):
    reader = FakeReader({"TAPZZTWT5608": ("code", TAP)})
    result = _resolve(db, ["TAPZZTWT5608"], ["TAP ZZTWT5608"], reader=reader)

    assert reader.calls == [[("TAPZZTWT5608", "TAP ZZTWT5608")]]
    row = _by_token(result)["TAPZZTWT5608"]
    assert row["resolved"] is True
    assert _codes(row) == [TAP]
    assert row["matches"][0]["match_tier"] == "exact", "the gate reads the typed code as exact"
    assert row["fallback"] == {"asked": True, "kind": "code", "value": TAP, "matched": 1}
    assert result.unresolved_tokens == []


def test_a_token_with_no_typed_text_is_not_asked(db):
    """No `raw_tokens` (a caller that sends only folded tokens): the guard could accept
    nothing but the whole token, which the code tiers already missed on, so the call is
    not made; the trace says why (reviewer, third pass, finding 4)."""
    reader = FakeReader({"TAPZZTWT5608": ("code", TAP)})
    result = _resolve(db, ["TAPZZTWT5608"], None, reader=reader)
    assert reader.calls == []
    assert _by_token(result)["TAPZZTWT5608"]["fallback"] == {"asked": False, "kind": "not_asked", "reason": "no_typed_text"}
    assert result.unresolved_tokens == ["TAPZZTWT5608"]


def test_a_code_reading_the_catalogue_does_not_hold_stays_a_miss(db):
    reader = FakeReader({"TAPZZTNOPE1": ("code", "ZZTNOPE1")})
    result = _resolve(db, ["TAPZZTNOPE1"], ["TAP ZZTNOPE1"], reader=reader)

    row = _by_token(result)["TAPZZTNOPE1"]
    assert row["matches"] == []
    assert result.unresolved_tokens == ["TAPZZTNOPE1"]
    assert row["fallback"] == {"asked": True, "kind": "code", "value": "ZZTNOPE1", "matched": 0}


def test_a_value_outside_the_typed_text_is_rejected_never_a_typo_fix(db):
    """The model may only READ a code out of what the dealer typed. "zztw5608" typed,
    "ZZTWT5608" answered: the catalogue has it, and it is still refused."""
    reader = FakeReader({"zztw5608": ("code", TAP)})
    result = _resolve(db, ["zztw5608"], ["zztw5608"], reader=reader)

    row = _by_token(result)["zztw5608"]
    assert row["matches"] == []
    assert result.unresolved_tokens == ["zztw5608"]
    assert row["fallback"]["kind"] == "none"
    assert row["fallback"]["asked"] is True
    assert row["fallback"]["reason"] == "not_in_typed_text"


def test_the_guard_reads_the_raw_text_not_only_the_folded_token(db):
    """Folded token "TAPZZTWT5608", raw "TAP ZZTWT5608": the value "ZZTWT5608" is inside
    both; a value "ZZT WT5608" (a space the dealer never typed) is inside neither once
    both sides are folded to letters and digits, so it is accepted too - the fold is
    what the exact tier matches on as well."""
    reader = FakeReader({"TAPZZTWT5608": ("code", "ZZT WT5608")})
    result = _resolve(db, ["TAPZZTWT5608"], ["TAP ZZTWT5608"], reader=reader)
    assert _codes(_by_token(result)["TAPZZTWT5608"]) == [TAP]


# --------------------------------------------------------------------------- #
# A family answer is the existing picker
# --------------------------------------------------------------------------- #


def test_a_family_reading_with_several_codes_is_the_ambiguous_picker(db):
    reader = FakeReader({"TOILET8066": ("family", "8066")})
    result = _resolve(db, ["TOILET8066"], ["TOILET 8066"], reader=reader)

    row = _by_token(result)["TOILET8066"]
    assert row["ambiguous"] is True
    assert row["resolved"] is False
    assert sorted(_codes(row)) == FAMILY
    assert {m["match_tier"] for m in row["matches"]} == {"substring"}
    assert row["fallback"] == {"asked": True, "kind": "family", "value": "8066", "matched": 3}
    assert result.unresolved_tokens == []


def test_a_family_reading_with_one_code_is_that_code(db):
    reader = FakeReader({"TAP5608": ("family", "5608")})
    result = _resolve(db, ["TAP5608"], ["TAP 5608"], reader=reader)

    row = _by_token(result)["TAP5608"]
    assert row["resolved"] is True
    assert _codes(row) == [TAP]
    assert row["fallback"]["matched"] == 1


def test_a_family_reading_inside_no_code_is_a_miss(db):
    reader = FakeReader({"TAP9999": ("family", "9999")})
    result = _resolve(db, ["TAP9999"], ["TAP 9999"], reader=reader)

    assert result.unresolved_tokens == ["TAP9999"]
    assert _by_token(result)["TAP9999"]["fallback"]["matched"] == 0


# --------------------------------------------------------------------------- #
# None, errors, and the mix
# --------------------------------------------------------------------------- #


def test_a_none_reading_keeps_the_miss_and_says_it_was_asked(db):
    reader = FakeReader({"BIDET": ("none", None)})
    result = _resolve(db, ["BIDET"], ["bidet"], reader=reader)

    row = _by_token(result)["BIDET"]
    assert row["matches"] == []
    assert result.unresolved_tokens == ["BIDET"]
    assert row["fallback"] == {"asked": True, "kind": "none", "value": None}


def test_a_reader_that_raises_is_a_miss_never_a_failed_call(db):
    reader = FakeReader(raises=True)
    result = _resolve(db, ["TAPZZTWT5608"], ["TAP ZZTWT5608"], reader=reader)

    assert result.unresolved_tokens == ["TAPZZTWT5608"]
    row = _by_token(result)["TAPZZTWT5608"]
    assert row["fallback"]["asked"] is True
    assert row["fallback"]["kind"] == "error"


def test_a_token_the_reader_did_not_answer_is_reported_not_asked(db):
    class Silent(FakeReader):
        def __call__(self, missed):
            self.calls.append(list(missed))
            return {}

    result = _resolve(db, ["TAPZZTWT5608"], ["TAP ZZTWT5608"], reader=Silent())
    assert _by_token(result)["TAPZZTWT5608"]["fallback"] == {"asked": False, "kind": "not_asked"}


def test_only_the_missed_tokens_are_asked_and_each_is_answered_alone(db):
    reader = FakeReader({"TAPZZTWT5608": ("code", TAP), "WCZZTWC8066SS": ("code", "ZZTWC8066-SS")})
    result = _resolve(
        db,
        ["ZZTWC286SH", "TAPZZTWT5608", "WCZZTWC8066SS", "ZZTNOPE2"],
        ["ZZTWC286-SH", "TAP ZZTWT5608", "WC ZZTWC8066-SS", "ZZTNOPE2"],
        reader=reader,
    )

    assert reader.calls == [[("TAPZZTWT5608", "TAP ZZTWT5608"), ("WCZZTWC8066SS", "WC ZZTWC8066-SS"), ("ZZTNOPE2", "ZZTNOPE2")]]
    rows = _by_token(result)
    assert _codes(rows["ZZTWC286SH"]) == ["ZZTWC286-SH"] and "fallback" not in rows["ZZTWC286SH"]
    assert _codes(rows["TAPZZTWT5608"]) == [TAP]
    # ZZTWC8066-SS is not in the catalogue: the model's answer does not make it exist.
    assert rows["WCZZTWC8066SS"]["matches"] == []
    assert result.unresolved_tokens == ["WCZZTWC8066SS", "ZZTNOPE2"]


# --------------------------------------------------------------------------- #
# Byte identity without a reader
# --------------------------------------------------------------------------- #


def test_no_reader_means_no_fallback_key_anywhere(db):
    result = _resolve(db, ["TAPZZTWT5608", TAP], ["TAP ZZTWT5608", TAP]).as_dict()
    assert all("fallback" not in r for r in result["resolutions"])
    assert result["unresolved_tokens"] == ["TAPZZTWT5608"]


# --------------------------------------------------------------------------- #
# The route: asked once per request, opt-out
# --------------------------------------------------------------------------- #


def _route(db, tokens, raws, *, code_reader, fallback_to_all_types=True):
    from app.api.v1.system.references import _resolve_input

    return _resolve_input(
        db,
        " ".join(raws),
        tokens,
        match_mode="or",
        allowed_entity_types=["product"] * len(tokens),
        fallback_to_all_types=fallback_to_all_types,
        limit=50,
        raw_tokens=raws,
        code_reader=code_reader,
    )


def test_a_stock_ask_on_a_read_out_code_is_a_forward_ask_never_the_counted_set(db, monkeypatch):
    """The chatbot's stock ask (AND mode, `require: {stock: true}`) over the prod shape
    "TAP ZZTWT5608 x 5": the reader reads the code, the catalogue places it, and the route
    must treat the turn as it treats a typed "ZZTWT5608" (code first: no `predicate`, no
    HAS / counted-set branch). Measured on a1eae3bb through the real engine: the route's
    `_code_matched` compared the folded token with the code, found no containment and ran
    the counted set, so the dealer read "Couldn't find a TAP ZZTWT5608 with stock (checked
    ZZTWT5608)" for a code that exists."""
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
    from app.services import resolver_code_reader
    from app.services.resolver_code_reader import CodeReading

    monkeypatch.setattr(
        resolver_code_reader, "read_codes",
        lambda db_, missed, **kw: {tok: CodeReading("code", TAP) for tok, _ in missed},
    )

    def ask(query, tokens, raws):
        payload = ResolveReferenceRequest(
            query=query, tokens=tokens, raw_tokens=raws, allowed_entity_types=["product"],
            match_mode="and", fallback_to_all_types=True, limit=15, dry_run=True,
            domain="inventory", require={"stock": True},
        )
        return resolve_reference_post(payload, current_user={"id": None}, db=db)

    typed = ask("ZZTWT5608 x 5", ["ZZTWT5608"], ["ZZTWT5608"])
    read_out = ask("TAP ZZTWT5608 x 5", ["TAPZZTWT5608"], ["TAP ZZTWT5608"])

    assert "predicate" not in typed
    assert "predicate" not in read_out, read_out.get("predicate")
    # The typed code answers in the AND shape (`intersection`); the read-out one degraded
    # to the OR shape (`resolutions`) when the AND probe missed the folded token.
    assert [m["canonical_code"] for m in typed["intersection"]] == [TAP]
    codes = lambda r: [m["canonical_code"] for m in r["resolutions"][0]["matches"]]  # noqa: E731
    assert codes(read_out) == [TAP]
    assert read_out["resolutions"][0]["fallback"] == {"asked": True, "kind": "code", "value": TAP, "matched": 1}
    assert read_out["unresolved_tokens"] == []


def test_the_route_asks_once_even_when_it_reruns_the_unresolved_tokens(db):
    """`fallback_to_all_types` re-runs the tokens still unresolved against every type
    (`references._resolve_input`'s cross-type pass, which calls `resolve_references`
    directly with no reader): the reader is asked in the primary pass only, so the
    whole request spends one call and the primary pass's verdict is what the merged
    resolution carries."""
    reader = FakeReader({"TAPZZTNOPE1": ("none", None)})
    result = _route(db, ["TAPZZTNOPE1"], ["TAP ZZTNOPE1"], code_reader=reader)

    assert len(reader.calls) == 1, reader.calls
    assert result["unresolved_tokens"] == ["TAPZZTNOPE1"]
    assert result["resolutions"][0]["fallback"]["kind"] == "none"


def test_the_route_wraps_a_bare_reader_in_the_per_request_cap_and_memo(db):
    """`_resolve_input` is the one place every caller's reader passes through, so it
    owns the per-request bounds: a reader handed in bare (a test double, a future
    caller) still sees at most 5 tokens, and the two past the cap are `not_asked`."""
    tokens = [f"TAPZZTNOPE{i}" for i in range(1, 8)]
    raws = [f"TAP ZZTNOPE{i}" for i in range(1, 8)]
    reader = FakeReader({t: ("none", None) for t in tokens})

    result = _route(db, tokens, raws, code_reader=reader, fallback_to_all_types=False)

    assert len(reader.calls) == 1 and len(reader.calls[0]) == 5, reader.calls
    kinds = {r["token"]: r["fallback"]["kind"] for r in result["resolutions"]}
    assert [kinds[t] for t in tokens] == ["none"] * 5 + ["not_asked"] * 2, kinds
    assert result["unresolved_tokens"] == tokens


def test_the_route_resolves_the_owners_line(db):
    reader = FakeReader({"TAPZZTWT5608": ("code", TAP)})
    result = _route(db, ["TAPZZTWT5608"], ["TAP ZZTWT5608"], code_reader=reader)

    assert result["unresolved_tokens"] == []
    assert result["resolutions"][0]["matches"][0]["canonical_code"] == TAP


def test_the_post_route_builds_the_reader_unless_the_caller_opts_out(db, monkeypatch):
    from app.api.v1.system import references
    from app.services import resolver_code_reader

    built: list[dict[str, Any]] = []

    def fake_reader_for(session, *, message_text, user_id, dry_run):
        built.append({"message_text": message_text, "user_id": user_id, "dry_run": dry_run})
        return FakeReader({"TAPZZTWT5608": ("code", TAP)})

    monkeypatch.setattr(resolver_code_reader, "reader_for", fake_reader_for)
    principal = {"id": None}

    payload = references.ResolveReferenceRequest(
        query="TAP ZZTWT5608 x 5", tokens=["TAPZZTWT5608"], raw_tokens=["TAP ZZTWT5608"],
        allowed_entity_types=["product"], dry_run=True,
    )
    result = references.resolve_reference_post(payload, current_user=principal, db=db)
    assert built == [{"message_text": "TAP ZZTWT5608 x 5", "user_id": None, "dry_run": True}]
    assert result["unresolved_tokens"] == []

    built.clear()
    off = payload.model_copy(update={"semantic_fallback": False})
    result = references.resolve_reference_post(off, current_user=principal, db=db)
    assert built == [], "opted out: no reader is built, no model is asked"
    assert result["unresolved_tokens"] == ["TAPZZTWT5608"]
    assert all("fallback" not in r for r in result["resolutions"])


def test_words_the_route_split_out_of_a_sentence_are_never_asked(db):
    """`_synthesize_alpha_tokens`: a free-text query sent for an alpha-only type is split
    into words by the route itself; nobody decided those words were codes, so the reader
    is not consulted even when `product` is among the allowed types (security review,
    final head item 5)."""
    from app.api.v1.system.references import _resolve_input

    reader = FakeReader({"basin": ("code", TAP)})
    _resolve_input(
        db, "photo of a basin ZZTWT5608 tap", None,
        allowed_entity_types=["attachment_type", "product"], code_reader=reader,
    )
    assert reader.calls == [], reader.calls


def test_a_hint_spelled_by_an_alias_is_still_a_product_question(db):
    """The per-token hint is folded through the resolver's own aliases ("products",
    "master_product" are `product`), so a caller using one is asked like any other
    (reviewer, fourth pass, should-fix 2)."""
    reader = FakeReader({"TAPZZTWT5608": ("code", TAP)})
    result = _resolve(db, ["TAPZZTWT5608"], ["TAP ZZTWT5608"], reader=reader, types=["products"])

    assert reader.calls == [[("TAPZZTWT5608", "TAP ZZTWT5608")]], reader.calls
    assert [m.canonical_code for m in result.resolutions[0].matches] == [TAP]


def test_a_code_reading_answers_the_exact_row_alone_never_its_variant_siblings(db):
    """A typed exact code comes back with its variant siblings beside it (Tier 1's picker
    aid), which the gate's AND-mode exactness test and `turn/narrow.typed_exact_settles`
    then strip by comparing the TYPED token with the code. A code READ OUT of
    "TAP ZZTKS6145" is not the typed token, so siblings here would turn a found code into a
    "which one?" question (measured on the gate, reviewer fourth pass, should-fix 4): the
    re-probe is the exact row alone, and the answer is the one the typed code settles to."""
    _product(db, "ZZTKS6145")
    _product(db, "ZZTKS6145-NEW")
    db.flush()
    reader = FakeReader({"TAPZZTKS6145": ("code", "ZZTKS6145")})
    typed_alone = _resolve(db, ["ZZTKS6145"], ["ZZTKS6145"], reader=None)
    read_out = _resolve(db, ["TAPZZTKS6145"], ["TAP ZZTKS6145"], reader=reader)

    assert [m.canonical_code for m in typed_alone.resolutions[0].matches] == ["ZZTKS6145", "ZZTKS6145-NEW"]
    assert [m.canonical_code for m in read_out.resolutions[0].matches] == ["ZZTKS6145"]
    assert read_out.resolutions[0].matches[0].match_tier == "exact"
    assert read_out.resolutions[0].ambiguous is False
    assert read_out.resolutions[0].fallback == {"asked": True, "kind": "code", "value": "ZZTKS6145", "matched": 1}


def test_a_code_reading_reaches_a_product_set_when_the_callers_types_do(db):
    """A flyer / set code read out of "TAP ZZTWC8608-RL" is a product-domain hit for a caller
    that asked for `product` (`allowed` widens to `product_set`, D10b), exactly as the exact
    tier answers the typed code."""
    from app.models.product_set import ProductSet

    db.add(ProductSet(id=str(uuid.uuid4()), set_code="ZZTWC8608-RL", name="ZZT set", company_id=DEFAULT_COMPANY_ID))
    db.flush()
    reader = FakeReader({"TAPZZTWC8608RL": ("code", "ZZTWC8608-RL")})
    typed_alone = _resolve(db, ["ZZTWC8608-RL"], ["ZZTWC8608-RL"], reader=None)
    read_out = _resolve(db, ["TAPZZTWC8608RL"], ["TAP ZZTWC8608-RL"], reader=reader)

    assert [(m.entity_type, m.canonical_code) for m in typed_alone.resolutions[0].matches] == [("product_set", "ZZTWC8608-RL")]
    assert [(m.entity_type, m.canonical_code) for m in read_out.resolutions[0].matches] == [("product_set", "ZZTWC8608-RL")]
    assert read_out.resolutions[0].fallback["matched"] == 1


def test_a_token_typed_twice_takes_its_last_lines_hint_like_its_raw_text(db):
    """One rule for the raw text and the hint of a duplicated token: the LAST line's, as the
    head-code retry has always read `raw_by_token` (reviewer, fifth pass, nit 2)."""
    reader = FakeReader({"TAPZZTNOPE1": ("none", None)})
    last_is_product = _resolve(
        db, ["TAPZZTNOPE1", "TAPZZTNOPE1"], ["TAP ZZTNOPE1", "TAP ZZTNOPE1"],
        reader=reader, types=["customer", "product"],
    )
    assert reader.calls == [[("TAPZZTNOPE1", "TAP ZZTNOPE1")]], reader.calls
    assert last_is_product.resolutions[0].fallback["asked"] is True

    reader = FakeReader({"TAPZZTNOPE1": ("none", None)})
    last_is_customer = _resolve(
        db, ["TAPZZTNOPE1", "TAPZZTNOPE1"], ["TAP ZZTNOPE1", "TAP ZZTNOPE1"],
        reader=reader, types=["product", "customer"],
    )
    assert reader.calls == [], reader.calls
    assert last_is_customer.resolutions[0].fallback is None


def test_a_token_typed_twice_is_asked_once_and_uses_one_slot(db):
    """The owner's 15-line message carried one line twice; one question, one of the 5
    slots, one re-probe (reviewer nit 4)."""
    reader = FakeReader({"TAPZZTNOPE1": ("none", None)})
    result = _resolve(db, ["TAPZZTNOPE1", "TAPZZTNOPE1"], ["TAP ZZTNOPE1", "TAP ZZTNOPE1"], reader=reader)

    assert reader.calls == [[("TAPZZTNOPE1", "TAP ZZTNOPE1")]], reader.calls
    assert all(r["fallback"]["kind"] == "none" for r in result.as_dict()["resolutions"])


def test_the_cross_type_rerun_keeps_the_primary_passes_verdict_on_the_trace(db):
    """`fallback_to_all_types` replaces an unresolved resolution with the cross-type
    entry when that pass finds something; the reader's verdict rides along (reviewer
    nit 6). ZZTNOPE1 is a customer code here, so the cross-type pass finds it as a
    customer while the product-typed primary pass asked and got `none`."""
    from app.models.order import Customer

    db.add(Customer(id=str(uuid.uuid4()), customer_code="ZZTNOPE1", customer_name="ZZT Nope", company_id=DEFAULT_COMPANY_ID))
    db.flush()
    reader = FakeReader({"ZZTNOPE1": ("none", None)})
    result = _route(db, ["ZZTNOPE1"], ["ZZTNOPE1"], code_reader=reader)

    row = result["resolutions"][0]
    assert any(m["entity_type"] == "customer" for m in row["matches"]), row
    assert row["fallback"] == {"asked": True, "kind": "none", "value": None}


def test_the_get_route_builds_the_reader_unless_the_caller_opts_out(db, monkeypatch):
    from app.api.v1.system import references
    from app.services import resolver_code_reader

    built: list[dict[str, Any]] = []

    def fake_reader_for(session, *, message_text, user_id, dry_run):
        built.append({"message_text": message_text, "user_id": user_id, "dry_run": dry_run})
        return FakeReader({"TAPZZTWT5608": ("code", TAP)})

    monkeypatch.setattr(resolver_code_reader, "reader_for", fake_reader_for)
    kwargs = dict(
        query="TAP ZZTWT5608", tokens=["TAPZZTWT5608"], match_mode="or", allowed_entity_types=["product"],
        access_levels=None, fallback_to_all_types=False, domain_hint=None, domain=None, limit=None,
        current_user={"id": "u1"}, db=db,
    )
    result = references.resolve_reference(semantic_fallback=True, **kwargs)
    assert built == [{"message_text": "TAP ZZTWT5608", "user_id": "u1", "dry_run": False}]
    # GET sends no `raw_tokens`, so the reader has nothing it could read a code out of
    # beyond the folded token the tiers already missed on: built, never called.
    assert result["resolutions"][0]["fallback"] == {"asked": False, "kind": "not_asked", "reason": "no_typed_text"}
    assert result["unresolved_tokens"] == ["TAPZZTWT5608"]

    built.clear()
    result = references.resolve_reference(semantic_fallback=False, **kwargs)
    assert built == []
    assert result["unresolved_tokens"] == ["TAPZZTWT5608"]


def test_semantic_fallback_defaults_on(db):
    from app.api.v1.system.references import ResolveReferenceRequest

    assert ResolveReferenceRequest(query="x").semantic_fallback is True
