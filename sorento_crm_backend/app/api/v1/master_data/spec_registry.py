"""Spec Registry API - the vocabulary both the CRM ranker and the n8n parser read.

One vocabulary, two consumers, so this endpoint is the thing that stops them drifting:
if the parser emits `wall_mounted` while the ranker looks for `wall_hung`, every query
quietly scores worse and nothing logs an error.

Writes exist, and they are shaped by that guarantee rather than around it:

  * a `seed` row is repaired on every deploy, so editing its vocabulary would be undone.
    Staff extend it instead - `user_synonyms` is merged in at read time, additive only,
    so a word added here can never remove one the parser depends on.
  * a `user` row has no seed to drift from and is left alone entirely.
  * calibration (`rank_weight`, `is_active`, the match window) has always been
    human-owned on both.

So: add words and tune weights freely; renaming a shipped VALUE is deliberately not
offered, because that is the one edit that breaks the two consumers apart.
"""
import hashlib
import json
import re
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import (
    require_any_permission_with_api_key,
    require_permission_with_api_key,
)
from app.models.product_spec import ProductSpecRegistry, ProductSpecSearchPolicy
from app.services import product_spec_preview, product_spec_rederive
from app.services.error_handler import AppException, handle_internal_error, handle_not_found
from app.services.product_spec_rules import (
    BRAND_IS_NOT_A_SPEC,
    MAX_RULES_PER_TRY,
    MAX_TRY_TEXT,
)
from app.services.product_spec_registry import (
    SEARCH_POLICY_SEED,
    active_registry,
    default_match_window,
    find_similar_key,
    find_similar_value,
    find_word_on_other_key,
    merged_allowed_values,
    merged_synonyms,
    normalise_vocabulary,
    seed_search_policy,
    vocabulary_owners,
)

router = APIRouter()


def _effective_rules(row) -> list[dict]:
    """The rules this key is actually read with: its own, or the shipped fallback.

    Derivation uses the shipped rules whenever the stored column is empty (see
    `configured_rules`), so reporting the empty column as "no rules" is not a harmless
    simplification - it is the screen contradicting the data next to it.

    Each rule is `{"builder": {...}}` and nothing else (contract section 4): a person
    sees rules, not where they came from (D8), so the seed's own marker stays behind.
    """
    from app.services.product_spec_derivation import stored_or_shipped_rules

    rules = stored_or_shipped_rules(row)
    return [{"builder": rule["builder"]} for rule in rules if isinstance(rule.get("builder"), dict)]


def _also_on(row, owners: dict) -> dict[str, list[str]]:
    """{word: [other field labels]} for every word of this row another row also owns.

    Words with no overlap are absent. Built from the same index the save guard reads, so
    the note and the refusal cannot disagree.
    """
    out: dict[str, list[str]] = {}
    for words in merged_synonyms(row).values():
        for word in words or []:
            labels = [
                o.label
                for o in owners.get(normalise_vocabulary(word), [])
                if o.id != row.id
            ]
            if labels:
                out[word] = list(dict.fromkeys(labels))
    return out


def _serialise(row, owners: Optional[dict] = None) -> dict:
    if owners is None:
        from sqlalchemy.orm import object_session

        owners = vocabulary_owners(object_session(row))
    return {
        # Never shown: the History tab needs it to read this row's audit trail.
        "id": str(row.id),
        "spec_key": row.spec_key,
        "label": row.label,
        "data_type": row.data_type,
        "unit": row.unit,
        # Shipped values + the ones staff added, minus the ones taken away: consumers
        # see one vocabulary.
        "allowed_values": merged_allowed_values(row),
        # The editable half, so the UI knows which chips it may remove.
        "user_values": list(row.user_values or []),
        # Shipped values this business has taken away. Returned so the UI can show them
        # struck through with a way back, rather than as a chip that silently vanished.
        "suppressed_values": list(row.suppressed_values or []),
        # Catalog values that exist but are not searchable (placeholder brands). Shown
        # so the UI can explain why a value in the data never comes back in a result.
        "excluded_values": row.excluded_values or [],
        # Standing preference for particular values of this key ({"SORENTO": 1.5}).
        "value_weights": {k: float(v) for k, v in (row.value_weights or {}).items()},
        # HOW this key is read out of a product's text. Editable, first match wins.
        #
        # `derivation_rules` is what is STORED; `effective_rules` is what actually runs.
        # They differ whenever a key has never been edited: derivation falls back to the
        # rules that ship in code, so an empty column meant the screen said "no rules
        # yet, so nothing will ever fill this in" about a key sitting on 74 products it
        # had filled in itself. Showing the shipped set is the difference between a
        # configuration screen and a decoration.
        "derivation_rules": row.derivation_rules or [],
        "effective_rules": _effective_rules(row),
        # Merged view: consumers see one vocabulary, not the seed/user split.
        "synonyms": merged_synonyms(row),
        "applies_when": row.applies_when or {},
        # Rules, for every key without exception (#425): the dimensions used to be read
        # out of a measurement block outside the rule list, and are rules now.
        "read_from": "rules",
        # The number above which a reading is a typo rather than a measurement. Blank
        # means no cap.
        "max_value": float(row.max_value) if row.max_value is not None else None,
        "rank_weight": float(row.rank_weight) if row.rank_weight is not None else None,
        "measured_coverage": row.measured_coverage,
        # Editing surface: which fields the UI may offer, and what it must not touch.
        "source": row.source or "seed",
        "user_synonyms": row.user_synonyms or {},
        # Shipped words this business has taken away, so the UI can show them struck
        # through rather than silently missing.
        "suppressed_synonyms": row.suppressed_synonyms or {},
        "match_tolerance": float(row.match_tolerance) if row.match_tolerance is not None else 0.0,
        "match_decay": float(row.match_decay) if row.match_decay is not None else 0.0,
        "is_active": bool(row.is_active),
        # value -> display label ("pp" -> "PP"), purely cosmetic (#423, D/E). Editable
        # on seed AND user rows, like user_synonyms.
        "value_labels": row.value_labels or {},
        "parser_hint": row.parser_hint,
        "label_owned": bool(row.label_owned),
        "also_on": _also_on(row, owners),
    }


@router.get("")
@router.get("/")
async def get_spec_registry(
    request: Request,
    response: Response,
    current_user: dict = Depends(
        # Reuses the product-master read permission rather than minting a new one:
        # a new permission needs a grant sweep across provisioned roles, and this is
        # product vocabulary that anyone who may read products may read.
        require_permission_with_api_key("master_data.products.view")
    ),
    db: Session = Depends(get_db),
):
    """The active spec vocabulary, with an ETag so the parser can skip re-reading it."""
    try:
        rows = active_registry(db)
        owners = vocabulary_owners(db)
        keys = [_serialise(row, owners) for row in rows]
        stamps = [r.updated_at or r.created_at for r in rows]
        updated_at = max(stamps).isoformat() if stamps else None

        payload = {"keys": keys, "updated_at": updated_at}

        # Hash the payload itself rather than the max timestamp: an admin edit that
        # lands in the same second still changes the body, and a stale prompt is
        # worse than a re-read.
        etag = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:32]
        etag_header = f'"{etag}"'

        if request.headers.get("if-none-match") == etag_header:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag_header})

        response.headers["ETag"] = etag_header
        response.headers["Cache-Control"] = "private, max-age=60"
        return payload
    except Exception as e:
        raise handle_internal_error(str(e))


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #
_EDITABLE_DATA_TYPES = {"enum", "numeric", "boolean"}


class SpecKeyUpdate(BaseModel):
    """What a human may change on an existing key.

    Deliberately narrow. `spec_key`, `data_type` and `unit` are absent because changing
    them on a key that already has derived values against it would silently invalidate
    every stored spec - that is a migration, not an edit.
    """

    label: Optional[str] = Field(default=None, min_length=1, max_length=150)
    rank_weight: Optional[float] = Field(default=None, ge=0, le=100)
    is_active: Optional[bool] = None
    match_tolerance: Optional[float] = Field(default=None, ge=0)
    match_decay: Optional[float] = Field(default=None, ge=0)
    # value -> [extra customer phrasings]. Merged with the seed's, never replacing it.
    user_synonyms: Optional[dict[str, list[str]]] = None
    # value -> [shipped words to stop using for it]. The only way to take a word away.
    suppressed_synonyms: Optional[dict[str, list[str]]] = None
    # Only honoured on a `user` row; a seed row's closed list is the parser's contract.
    allowed_values: Optional[list[str]] = None
    # Calibration, editable on any row: which catalog values are not things a customer
    # searches for. Excluding one hides it from the understanding model and rejects it
    # if the model produces it anyway.
    excluded_values: Optional[list[str]] = None
    # {value: bonus}. A standing preference applied to any product carrying the value,
    # EXCEPT when the customer named this key themselves.
    value_weights: Optional[dict[str, float]] = None
    # Values added to a shipped key. Additive: the seed's own list is untouchable
    # because the n8n parser is held to it.
    user_values: Optional[list[str]] = None
    # Shipped values to stop using. The only way to take a value away, and the mirror
    # of user_values - nothing is deleted, so putting one back is one click.
    suppressed_values: Optional[list[str]] = None
    # Ordered rules that produce this key's value. Editable on ANY row, seed or user:
    # without it a key created here can never be populated, which made the "add a spec"
    # button a promise the engine could not keep.
    derivation_rules: Optional[list[dict]] = None
    # value -> display label ("pp" -> "PP"), purely cosmetic (#423). Editable on ANY
    # row, seed or user - staff-owned like user_synonyms, never seed-repaired.
    value_labels: Optional[dict[str, str]] = None
    # Which products may carry this key at all, as {other_key: [permitted values]}.
    # Calibration rather than vocabulary - "bowl count is not only for kitchen sinks"
    # is a merchandising call - so it is editable and no longer seed-repaired.
    applies_when: Optional[dict[str, list[str]]] = None
    # Ignore readings above this. Seeded 5000 on millimetre keys, because the catalogue
    # carries "540X440180MM" - a separator typo that parses as a 440-metre sink. Blank
    # is a real answer and means no cap, which is why it is `Optional` on a payload that
    # only applies the fields it was sent.
    max_value: Optional[float] = Field(default=None, ge=0)
    # What this field means to the chatbot: one line, max 200 once cleaned (checked in the
    # route so the refusal carries its own code). Blank stores NULL.
    parser_hint: Optional[str] = Field(default=None, max_length=2000)


class SpecKeyCreate(BaseModel):
    """A brand-new key, owned by whoever created it and never seed-repaired."""

    spec_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=150)
    data_type: str
    unit: Optional[str] = Field(default=None, max_length=16)
    allowed_values: list[str] = Field(default_factory=list)
    user_synonyms: dict[str, list[str]] = Field(default_factory=dict)
    applies_when: dict[str, list[str]] = Field(default_factory=dict)
    rank_weight: float = Field(default=1.0, ge=0, le=100)
    is_active: bool = True


def _reject(message: str, code: str):
    from app.services.error_handler import AppException

    return AppException(status_code=400, message=message, code=code)


def _similar_refusal(message: str, match: dict) -> JSONResponse:
    """A 422 carrying the match itself, as a TOP-LEVEL body.

    Not the `AppException` envelope, because the client has to render the match - "that
    is already Finish or colour, use it instead" - and an envelope carrying only a
    string cannot say WHICH key. Mirrors the prompt-registry save-validation shape,
    which returns its `unknown_tokens` the same way and for the same reason.

    There is no way past it. A near-duplicate is refused, full stop: the collision is
    two names for one thing, and a registry holding both answers half of every customer
    question each, with nothing on any screen saying why.
    """
    return JSONResponse(status_code=422, content={"error": message, "match": match})


def _word_refusal(
    db: Session, row, word: str, owners: dict, value: Optional[str] = None
) -> None:
    """409 when `word` already belongs to another field (one word, one field).

    `details` lets the grid put the message under the word: `value` is the value key the
    word was added under (`_self` for an other-name), None for a new value."""
    other = find_word_on_other_key(db, row, word, owners)
    if other is not None:
        raise AppException(
            status_code=409,
            message=f"\"{word}\" is already a word for {other.label}.",
            code="spec_registry_word_on_other_key",
            details={"word": word, "other_label": other.label, "value": value},
        )


def _held_words(row) -> set[str]:
    """Everything this row already says, normalised: judged only for what is NEW."""
    held = {normalise_vocabulary(v) for v in merged_allowed_values(row)}
    held |= {normalise_vocabulary(v) for v in (row.user_values or [])}
    for words in merged_synonyms(row).values():
        held |= {normalise_vocabulary(w) for w in words or []}
    return held


def _validate_reachable(data_type: str, allowed_values, synonyms) -> None:
    """An allowed value nobody can say is a value that can never be searched for.

    There is already a test asserting this invariant across the seeded registry; the UI
    must not be able to introduce a violation the seed forbids.
    """
    if data_type != "enum":
        return
    words = {v: list(w) for v, w in (synonyms or {}).items()}
    unreachable = [v for v in (allowed_values or []) if not words.get(v)]
    if unreachable:
        raise _reject(
            "These values have no words a customer could say, so nothing would ever "
            f"match them: {', '.join(unreachable)}. Add at least one synonym each.",
            "spec_registry_unreachable_value",
        )


class PolicyUpdate(BaseModel):
    """One scoring knob. Bounded so a typo cannot make the ranker meaningless."""

    value: float = Field(ge=0, le=100)


@router.get("/policy")
async def get_search_policy(
    current_user: dict = Depends(require_permission_with_api_key("master_data.spec_registry.view")),
    db: Session = Depends(get_db),
):
    """Every scoring knob, with the label and explanation shown beside it in the UI."""
    seed_search_policy(db, commit=True)
    rows = db.query(ProductSpecSearchPolicy).all()
    by_key = {row.policy_key: row for row in rows}
    return {
        "policy": [
            {
                "policy_key": entry["policy_key"],
                "label": entry["label"],
                "help_text": entry["help_text"],
                "value": float(by_key[entry["policy_key"]].value)
                if entry["policy_key"] in by_key
                else float(entry["value"]),
                "default_value": float(entry["value"]),
            }
            for entry in SEARCH_POLICY_SEED
        ]
    }


@router.patch("/policy/{policy_key}")
async def update_search_policy(
    policy_key: str,
    payload: PolicyUpdate,
    current_user: dict = Depends(require_permission_with_api_key("master_data.spec_registry.edit")),
    db: Session = Depends(get_db),
):
    try:
        seed_search_policy(db)
        row = db.query(ProductSpecSearchPolicy).filter_by(policy_key=policy_key).first()
        if row is None:
            raise handle_not_found("Search setting", policy_key)
        row.value = payload.value
        db.commit()
        return {"policy_key": row.policy_key, "value": float(row.value)}
    except Exception as e:
        if type(e).__name__ in {"AppException", "HTTPException"}:
            raise
        raise handle_internal_error(str(e))


def _validate_rules(
    rules: list, *, allowed_values: list | None = None, data_type: str = "", spec_key: str = ""
) -> list[dict]:
    """Reject a rule the engine could not run, at the point someone builds it.

    One validator (`product_spec_rules.validate_rules`, contract section 3): every
    refusal names the missing part in plain words, and a rule the screen compiled to a
    different pattern than the server is refused rather than saved. What is stored is
    `{"builder": cleaned}` and nothing else.
    """
    from app.services.product_spec_rules import validate_rules

    return validate_rules(
        rules, spec_key=spec_key, data_type=data_type, allowed_values=allowed_values
    )


@router.get("/coverage")
async def get_spec_coverage(
    current_user: dict = Depends(require_permission_with_api_key("master_data.spec_registry.view")),
    db: Session = Depends(get_db),
):
    """How many products actually carry each key, right now, and when it was last read.

    A separate call rather than a field on the registry payload: that payload is what
    the n8n parser reads to build its extraction prompt, it is ETag-cached, and a
    number that changes whenever anyone re-reads the catalogue would break the cache
    for a consumer that has no use for it.

    The registry's own `measured_coverage` is a figure recorded when the key was
    written, so it is a note about the past. Where the two disagree the screen should
    show this one - `bowl_count` says 106 and the catalogue holds 148.

    `last_read` is the newest read among the products carrying a key: the row's
    `updated_at`, else its `created_at` (the same stamp the product's own
    Specifications tab calls `derived_at`). A key no product carries has neither.
    """
    from sqlalchemy import func, text as sql_text

    from app.models.product import Product
    from app.models.product_spec import ProductSpecifications

    rows = (
        db.query(
            func.jsonb_object_keys(ProductSpecifications.values).label("spec_key"),
            func.count().label("n"),
            func.max(
                func.coalesce(ProductSpecifications.updated_at, ProductSpecifications.created_at)
            ).label("last_read"),
        )
        .join(Product, Product.id == ProductSpecifications.product_id)
        .filter(Product.is_active.is_(True))
        .group_by(sql_text("1"))
        .all()
    )
    return {
        "coverage": {key: n for key, n, _ in rows},
        "last_read": {key: read.isoformat() for key, _, read in rows if read is not None},
    }


@router.get("/applicable-keys")
async def applicable_keys(
    code: str,
    # `products.view`, for the same reason `GET /spec-registry` runs on it 300 lines
    # above: this is asked BY THE PRODUCT PAGE, on behalf of somebody editing a
    # product, and gating it on a registry slug granted to zero roles would ship the
    # add-a-specification picker 403'd to everybody. EITHER grant, so widening the
    # door never moves it away from whoever was already standing in it.
    current_user: dict = Depends(
        require_any_permission_with_api_key(
            ["master_data.products.view", "master_data.spec_registry.view"]
        )
    ),
    db: Session = Depends(get_db),
):
    """Which spec keys this product MAY carry, and which it already holds.

    Deliberately not `keys-for-product`, which answers from `spec.values` and therefore
    returns the keys the product already holds - the numerator where the picker needs
    the denominator (AC-A.7).

    Computed server-side because milestone 2's pre-seeding calls the same logic, and a
    second copy of the gate rules on the frontend would drift the first time somebody
    edited `applies_when`.
    """
    from app.services.product_spec_registry import applicable_keys_for_code

    return {"code": code, "keys": applicable_keys_for_code(db, code)}


@router.get("/similar")
async def similar_spec_key(
    label: str,
    current_user: dict = Depends(
        require_any_permission_with_api_key(
            ["master_data.products.view", "master_data.spec_registry.view"]
        )
    ),
    db: Session = Depends(get_db),
):
    """The existing key a proposed label already means, or nothing when it is new.

    Offered in the create dialog before it will submit (D7). The same comparison runs
    inside `POST /spec-registry`, so this endpoint is the courtesy and that one is the
    guard - a client that skips this is refused there rather than quietly splitting a
    word in two.
    """
    from app.services.product_spec_registry import find_similar_key

    return {"label": label, "match": find_similar_key(db, label)}


@router.get("/keys-for-product")
async def keys_for_product(
    code: str,
    # Relaxed from `master_data.spec_registry.view` alone, which is granted to zero
    # roles (M1), to EITHER grant. Same precedent and reasoning as `GET /spec-registry`
    # and `/applicable-keys` above. Either, not instead: a relaxation that swapped one
    # slug for the other would lock out the registry admin this screen belongs to.
    current_user: dict = Depends(
        require_any_permission_with_api_key(
            ["master_data.products.view", "master_data.spec_registry.view"]
        )
    ),
    db: Session = Depends(get_db),
):
    """Which spec keys a given product code carries, and what each one says.

    So the keys table can be filtered by a product rather than only by a word. The
    question "why does SRTWC7614-RL not come back for rimless" is asked about a code,
    and answering it meant opening the product page in another tab and comparing by eye.
    """
    from sqlalchemy import func

    from app.models.product import Product
    from app.models.product_spec import ProductSpecifications

    code = (code or "").strip()
    if not code:
        return {"code": code, "matched_product": None, "keys": {}}

    row = (
        db.query(ProductSpecifications, Product)
        .join(Product, Product.id == ProductSpecifications.product_id)
        .filter(func.upper(Product.product_code) == code.upper())
        .first()
    )
    if row is None:
        return {"code": code, "matched_product": None, "keys": {}}

    spec, product = row
    values = spec.values or {}
    provenance = spec.provenance or {}
    return {
        "code": code,
        "matched_product": {"id": str(product.id), "product_code": product.product_code},
        "keys": {
            key: {
                "value": (entry or {}).get("value"),
                "source": (provenance.get(key) or {}).get("source"),
            }
            for key, entry in values.items()
        },
    }


@router.get("/catalogue-status")
async def get_catalogue_status(
    current_user: dict = Depends(require_permission_with_api_key("master_data.spec_registry.view")),
    db: Session = Depends(get_db),
):
    """Whether the stored specifications were read with the rules that are live now."""
    return product_spec_rederive.status(db)


@router.post("/reread-catalogue")
async def reread_catalogue(
    current_user: dict = Depends(require_permission_with_api_key("master_data.spec_registry.edit")),
    db: Session = Depends(get_db),
):
    """Re-read every product with the current rules.

    Editing a rule changes how a product WOULD be read; the stored values stay as they
    were until this runs. It takes minutes over 22,805 rows, so it runs in the
    background and `catalogue-status` reports on it.
    """
    return product_spec_rederive.start(db)


@router.get("/{spec_key}/products")
async def products_carrying_spec(
    spec_key: str,
    value: Optional[str] = None,
    q: Optional[str] = None,
    class_label: Optional[str] = Query(
        None, description="Narrows to one product class (`class`), e.g. \"Kitchen Sink\"."
    ),
    source_filter: Optional[str] = Query(
        None,
        alias="source",
        description="Narrows to one provenance source, e.g. \"derived\" or \"human\".",
    ),
    limit: int = 100,
    offset: int = 0,
    # The second product-scoped registry read relaxed the same way (AC-A.13). It
    # answers a question about PRODUCTS, asked by somebody who may already read every
    # one of them.
    current_user: dict = Depends(
        require_any_permission_with_api_key(
            ["master_data.products.view", "master_data.spec_registry.view"]
        )
    ),
    db: Session = Depends(get_db),
):
    """Which products actually carry this specification, and what each one says.

    A count on its own ("seen in 106") is not reviewable: it says something happened
    without saying to what. This is how you check a rule did what you meant before
    trusting it in a search - and how you find the row that proves it did not.

    Grouped counts come back with the list so a whole key can be judged at a glance:
    `has_drainer` reading `true` on 74 bathtubs is obvious in the tally and invisible
    in a page of rows.

    `class_label` and `source` narrow the query itself, not the page that came back -
    a key with 10,000 rows only has one page loaded client-side, so filtering the page
    would silently report on whatever happened to land on it. The tallies are computed
    before those two filters (though after `value`/`q`, which already narrowed the set
    they describe), so the Class and Source dropdown options stay complete once one of
    them is picked, instead of collapsing to the choice just made.
    """
    from sqlalchemy import func, or_

    from app.models.product import Product
    from app.models.product_spec import ProductSpecifications

    row = db.query(ProductSpecRegistry).filter_by(spec_key=spec_key).first()
    if row is None:
        raise handle_not_found("Spec key", spec_key)

    stored_value = ProductSpecifications.values[spec_key]["value"].astext
    stored_class = ProductSpecifications.values["class"]["value"].astext
    stored_source = ProductSpecifications.provenance[spec_key]["source"].astext

    base = (
        db.query(ProductSpecifications, Product)
        .join(Product, Product.id == ProductSpecifications.product_id)
        .filter(ProductSpecifications.values.has_key(spec_key))  # noqa: W601
        .filter(Product.is_active.is_(True))
    )
    if value is not None:
        base = base.filter(stored_value == value)
    if q and q.strip():
        # Searched in the database rather than in the page, because the list is
        # paginated: filtering the hundred rows on screen would report "no matches" for
        # a product sitting on page three.
        needle = f"%{q.strip()}%"
        base = base.filter(
            or_(
                Product.product_code.ilike(needle),
                Product.description.ilike(needle),
                stored_value.ilike(needle),
                ProductSpecifications.provenance[spec_key]["evidence"].astext.ilike(needle),
            )
        )

    # Counted over `base` before `class_label`/`source` narrow it further, so the
    # dropdown options describe everything there is to pick, not just what is already
    # picked - narrowing them to match the current filter would make the other options
    # disappear the moment one is chosen.
    tallies = {
        "by_value": [
            {"value": v, "count": n}
            for v, n in base.with_entities(stored_value, func.count()).group_by(stored_value)
            .order_by(func.count().desc()).limit(30).all()
        ],
        "by_class": [
            {"class": c, "count": n}
            for c, n in base.with_entities(stored_class, func.count()).group_by(stored_class)
            .order_by(func.count().desc()).limit(30).all()
        ],
        "by_source": [
            {"source": s, "count": n}
            for s, n in base.with_entities(stored_source, func.count()).group_by(stored_source)
            .order_by(func.count().desc()).all()
        ],
    }

    if class_label is not None:
        base = base.filter(stored_class == class_label)
    if source_filter is not None:
        base = base.filter(stored_source == source_filter)

    total = base.count()
    rows = (
        base.order_by(Product.product_code)
        .offset(max(0, offset))
        .limit(max(1, min(limit, 500)))
        .all()
    )

    return {
        "spec_key": spec_key,
        "label": row.label,
        "total": total,
        **tallies,
        "products": [
            {
                "id": str(product.id),
                "product_code": product.product_code,
                "description": product.description,
                "class": (spec.values.get("class") or {}).get("value"),
                "value": (spec.values.get(spec_key) or {}).get("value"),
                # WHERE it came from and the exact words it was read from, because
                # "why does this say that" is the only question worth asking here.
                "source": (spec.provenance.get(spec_key) or {}).get("source"),
                "evidence": (spec.provenance.get(spec_key) or {}).get("evidence"),
            }
            for spec, product in rows
        ],
    }


class SpecTryRequest(BaseModel):
    """One rule list, tried against a real product or a paste (AC-B.1).

    Exactly one of `productId`/`text` - a request naming both or neither is refused,
    because there is no way to tell which one the rows should read.
    """

    productId: Optional[str] = None
    # Bounded (review B-3): the rules run on the request, so one paste must not hold it.
    text: Optional[str] = Field(default=None, max_length=MAX_TRY_TEXT)
    rules: list[dict] = Field(default_factory=list, max_length=MAX_RULES_PER_TRY)


class SpecPreviewRequest(BaseModel):
    """The draft list a catalogue-wide preview runs against (AC-B.2)."""

    rules: list[dict] = Field(default_factory=list, max_length=MAX_RULES_PER_TRY)


def _rule_allowed_values(row) -> list[str]:
    """Suppressed values included, same as the PATCH: a rule pointed at a value this
    business took away must not be refused as unknown, or removing the value would
    also silently break the rule that used to read it."""
    return [*merged_allowed_values(row), *[str(v) for v in (row.suppressed_values or [])]]


@router.post("/{spec_key}/try")
def try_spec_key(
    spec_key: str,
    payload: SpecTryRequest = Body(...),
    current_user: dict = Depends(require_permission_with_api_key("master_data.spec_registry.view")),
    db: Session = Depends(get_db),
):
    """What the DRAFT rules would read from one real product, row by row (AC-B.1).

    Unsaved: the rules travel in the request body and are never stored. Every OTHER
    key still reads with its own configured rules, because a row on this key can be
    gated on one of them (`shape`, for the round/square rows) and that gate has to be
    answered for real, draft or not.
    """
    try:
        from app.models.product import Product, ProductCategory
        from app.services.product_spec_derivation import (
            configured_max_values,
            configured_rules,
            configured_scopes,
            try_read,
        )

        row = db.query(ProductSpecRegistry).filter_by(spec_key=spec_key).first()
        if row is None:
            raise handle_not_found("Spec key", spec_key)

        has_product = bool((payload.productId or "").strip())
        has_text = bool((payload.text or "").strip())
        if has_product == has_text:
            raise _reject(
                "Try it on either a product or pasted text, not both and not neither.",
                "spec_registry_try_ambiguous_source",
            )

        cleaned = _validate_rules(
            payload.rules or [],
            allowed_values=_rule_allowed_values(row),
            data_type=row.data_type,
            spec_key=spec_key,
        )

        rules_by_key = configured_rules(db)
        scopes_by_key = configured_scopes(db)
        max_values = configured_max_values(db)

        if has_product:
            import uuid as _uuid

            try:
                _uuid.UUID(str(payload.productId))
            except ValueError:
                # A non-UUID `productId` reached the database driver unparsed and
                # raised `InvalidTextRepresentation` - a 500, for a typo in a URL a
                # human never sees (S5). Same answer an unknown-but-valid id gets:
                # there is no such product either way.
                raise handle_not_found("Product", payload.productId)

            found = (
                db.query(Product, ProductCategory)
                .outerjoin(ProductCategory, ProductCategory.id == Product.category_id)
                .filter(Product.id == payload.productId)
                .first()
            )
            if found is None:
                raise handle_not_found("Product", payload.productId)
            product, category = found
            result = try_read(
                spec_key,
                cleaned,
                product=product,
                category=category,
                rules_by_key=rules_by_key,
                scopes_by_key=scopes_by_key,
                max_values=max_values,
            )
            description = product.description or product.product_name or ""
        else:
            result = try_read(
                spec_key,
                cleaned,
                text=payload.text,
                rules_by_key=rules_by_key,
                scopes_by_key=scopes_by_key,
                max_values=max_values,
            )
            description = payload.text or ""

        return {
            "description": description,
            "reads": result["reads"],
            "winner_index": result["winner_index"],
        }
    except Exception as e:
        if type(e).__name__ in {"AppException", "HTTPException"}:
            raise
        raise handle_internal_error(str(e))


@router.post("/{spec_key}/preview")
async def preview_spec_key(
    spec_key: str,
    payload: SpecPreviewRequest = Body(...),
    current_user: dict = Depends(require_permission_with_api_key("master_data.spec_registry.edit")),
    db: Session = Depends(get_db),
):
    """Enqueue a catalogue-wide comparison of the draft rules against what is stored
    (AC-B.2). Same in-process background-thread mechanism `reread-catalogue` already
    runs on - a module-level job dict polled by `GET .../preview/{job_id}` - because
    that is what it already proved out and a preview needs nothing more from it: no
    new table, no queue, no worker restart.
    """
    try:
        row = db.query(ProductSpecRegistry).filter_by(spec_key=spec_key).first()
        if row is None:
            raise handle_not_found("Spec key", spec_key)

        cleaned = _validate_rules(
            payload.rules or [],
            allowed_values=_rule_allowed_values(row),
            data_type=row.data_type,
            spec_key=spec_key,
        )
        job_id = product_spec_preview.start(spec_key, cleaned, db)
        return {"jobId": job_id}
    except Exception as e:
        if type(e).__name__ in {"AppException", "HTTPException"}:
            raise
        raise handle_internal_error(str(e))


@router.get("/{spec_key}/preview/{job_id}")
async def get_spec_key_preview(
    spec_key: str,
    job_id: str,
    # `edit`, matching the POST that started the job (UAC AC-B.2) - a preview is
    # advice for the person about to save a draft, not a general registry read.
    current_user: dict = Depends(require_permission_with_api_key("master_data.spec_registry.edit")),
):
    """`{"status": "pending"}`, the four counts and a sample once `done`, or `failed`."""
    state = product_spec_preview.get(job_id)
    # A job started against a DIFFERENT key 404s the same as an unknown one: the id
    # is a random 12-char token, and a caller free to poll any job under any key
    # could just as well have not checked which key the run was for.
    if state is None or state.get("spec_key") != spec_key:
        raise handle_not_found("Preview job", job_id)
    return {k: v for k, v in state.items() if k != "spec_key"}


@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_spec_key(
    payload: SpecKeyCreate,
    current_user: dict = Depends(require_permission_with_api_key("master_data.spec_registry.add")),
    db: Session = Depends(get_db),
):
    """Register a new spec key. It is `user`-owned, so no deploy will overwrite it.

    A new key changes what a customer PHRASE can resolve to immediately, but no product
    carries it until a derivation rule exists and the catalog is re-derived. The UI says
    so; this endpoint does not pretend otherwise.
    """
    try:
        if payload.data_type not in _EDITABLE_DATA_TYPES:
            raise _reject(
                f"data_type must be one of {sorted(_EDITABLE_DATA_TYPES)}.",
                "spec_registry_bad_type",
            )
        if payload.spec_key == "brand":
            # The product's brand field is the only brand (#1286, D1).
            raise _reject(BRAND_IS_NOT_A_SPEC, "spec_registry_brand")
        if db.query(ProductSpecRegistry).filter_by(spec_key=payload.spec_key).first():
            raise _reject(
                f"A spec key named '{payload.spec_key}' already exists.",
                "spec_registry_duplicate",
            )

        # The exact-key check above catches `finish` twice. It does not catch "Finish
        # or colour" arriving beside `finish`, which is the collision that actually
        # happens - and a registry holding both answers half of every customer
        # question each, with nothing on any screen saying why. Enforced here rather
        # than only in the dialog, because the dialog is one client (D11).
        match = find_similar_key(db, payload.label)
        if match is None:
            match = find_similar_key(db, payload.spec_key)
        if match:
            return _similar_refusal(
                f"\"{payload.label}\" already exists as {match['label']}.", match
            )

        _validate_reachable(payload.data_type, payload.allowed_values, payload.user_synonyms)

        tolerance, decay = default_match_window(payload.unit)
        row = ProductSpecRegistry(
            spec_key=payload.spec_key,
            label=payload.label,
            data_type=payload.data_type,
            unit=payload.unit,
            allowed_values=payload.allowed_values,
            synonyms={},
            user_synonyms=payload.user_synonyms,
            applies_when=payload.applies_when,
            rank_weight=payload.rank_weight,
            is_active=payload.is_active,
            match_tolerance=tolerance,
            match_decay=decay,
            source="user",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _serialise(row)
    except Exception as e:
        if type(e).__name__ == "AppException":
            raise
        raise handle_internal_error(str(e))


# Adding a word to a key's vocabulary is a merchandiser's job - Journey A step 3, done
# from the product page while correcting a spec. Retuning `rank_weight` or rewriting the
# derivation rules is calibration against an eval baseline, and it is not. One route
# serves both, so the grant has to turn on the FIELDS in the payload.
_VOCABULARY_ONLY_FIELDS = {"user_values"}
# The fields that decide which values a customer can say (`_validate_reachable`).
_VOCABULARY_FIELDS = {
    "allowed_values",
    "user_values",
    "suppressed_values",
    "user_synonyms",
    "suppressed_synonyms",
}


@router.patch("/{spec_key}")
def update_spec_key(
    spec_key: str,
    payload: SpecKeyUpdate = Body(...),
    # Either grant gets THROUGH the door; which fields the caller may actually change
    # is decided below. Without the pair here, a merchandiser adding a word would be
    # refused before the route could tell what they were asking for.
    current_user: dict = Depends(
        require_any_permission_with_api_key(
            ["master_data.spec_registry.edit", "master_data.products.edit"]
        )
    ),
    db: Session = Depends(get_db),
):
    """Edit calibration and extend vocabulary. Seed-owned vocabulary stays seed-owned."""
    read_token = None
    try:
        row = db.query(ProductSpecRegistry).filter_by(spec_key=spec_key).first()
        if row is None:
            raise handle_not_found("Spec key", spec_key)

        fields = payload.model_dump(exclude_unset=True)
        # A save that changes how this key is read re-reads its products before it
        # answers, and only one catalogue read runs at a time (security review S1). The
        # slot is taken here, before anything is changed, so a refused save stores
        # nothing; it is given back in `finally`.
        if _READING_FIELDS & set(fields):
            read_token = product_spec_preview.begin_catalogue_read(db)
        # A save that changes how this key is read re-reads the products it changes,
        # straight away (D10, AC-S1.16). Judged against the row as it was, before any
        # field below is applied (a suppressed value is applied early; review S-10).
        reading_before = _reading(row)
        fingerprint_before = (
            product_spec_rederive.rules_fingerprint(db) if _READING_FIELDS & set(fields) else None
        )

        # The stricter grant is required as soon as ANYTHING outside the vocabulary
        # fields is present - a mixed payload is held to the higher bar, or
        # `user_values` becomes a passenger seat for `rank_weight`.
        if set(fields) - _VOCABULARY_ONLY_FIELDS:
            from fastapi import HTTPException

            from app.services.user_service import UserPermissionService

            if not UserPermissionService(db).check_user_has_permission(
                current_user["id"], "master_data.spec_registry.edit"
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Permission required: master_data.spec_registry.edit",
                )

        # The meaning and the cross-field word guard are judged before anything below is
        # applied, so a refused save stores nothing.
        hint = None
        if "parser_hint" in fields:
            # Control characters (NUL included) go first: Postgres refuses NUL in text,
            # and none of them belongs on a one-line prompt entry.
            hint = re.sub(r"[\x00-\x08\x0e-\x1f\x7f]", "", fields["parser_hint"] or "")
            hint = re.sub(r"\s+", " ", hint).strip()
            if len(hint) > 200:
                raise AppException(
                    status_code=422,
                    message="The chatbot meaning is at most 200 characters.",
                    code="spec_registry_hint_too_long",
                )
        owners = None
        if "user_synonyms" in fields or "user_values" in fields:
            owners = vocabulary_owners(db)
        if "user_synonyms" in fields:
            held = _held_words(row)
            for value_key, words in (fields["user_synonyms"] or {}).items():
                for word in words:
                    if word and word.strip() and normalise_vocabulary(word) not in held:
                        _word_refusal(db, row, word.strip(), owners, value_key)
        if "user_values" in fields:
            # Judged here, before any suppression below touches the row, so a refused
            # save changes nothing. A value the row already holds is not judged.
            present = {str(v) for v in merged_allowed_values(row)} | {
                str(v) for v in (row.user_values or [])
            }
            for proposed in fields["user_values"] or []:
                if proposed and proposed.strip() and proposed not in present:
                    _word_refusal(db, row, proposed.strip(), owners)

        # Suppressions land BEFORE the near-duplicate guard below, so the guard judges the
        # vocabulary as this save leaves it. Renaming a value - take `floor_standing`
        # away, add `free_standing` - is one save, and `free standing` ships as a word
        # for the value being removed; judged against the row as it was, the rename was
        # refused as already meaning the value it replaces. `user_synonyms` stays AFTER
        # the guard: the words for the new value spell the new value, and applied first
        # they would make it collide with itself.
        if "suppressed_synonyms" in fields:
            row.suppressed_synonyms = {
                value: [w.strip() for w in words if w and w.strip()]
                for value, words in (fields["suppressed_synonyms"] or {}).items()
                if [w for w in words if w and w.strip()]
            }

        if "suppressed_values" in fields:
            # Only shipped values can be suppressed. A staff-added value is removed by
            # dropping it from user_values, and recording it here as well would leave a
            # tombstone that silently blocks re-adding it under the same name.
            shipped = {str(v) for v in (row.allowed_values or [])}
            row.suppressed_values = [
                v.strip()
                for v in (fields["suppressed_values"] or [])
                if v and v.strip() and v.strip() in shipped
            ]

        if "user_values" in fields:
            # Server-side, mirroring the key guard (D11). The dialog runs the same
            # comparison against data it already holds so the common case never round
            # trips, but the dialog is a courtesy and this is the guard. PATCH replaces
            # `user_values`, so a client re-sends every word the key already holds
            # alongside the new one - a word must not collide with itself.
            present = {str(v) for v in merged_allowed_values(row)} | {
                str(v) for v in (row.user_values or [])
            }
            accepted: list[str] = []
            for proposed in fields["user_values"] or []:
                if proposed in present or proposed in accepted:
                    continue
                match = find_similar_value(row, proposed)
                if match is None:
                    needle = normalise_vocabulary(proposed)
                    earlier = next(
                        (v for v in accepted if normalise_vocabulary(v) == needle), None
                    )
                    if earlier is not None:
                        match = {
                            "value": earlier,
                            "matched_on": "value",
                            "matched_text": earlier,
                        }
                if match:
                    return _similar_refusal(
                        f"\"{proposed}\" is already {match['value']} on {row.label}.", match
                    )
                accepted.append(proposed)

        if "allowed_values" in fields:
            if (row.source or "seed") == "seed":
                raise _reject(
                    "This key's values ship with the product and are kept in step with "
                    "the chatbot parser, so they cannot be edited here. Add customer "
                    "wording instead, or create your own key.",
                    "spec_registry_seed_values_immutable",
                )
            row.allowed_values = fields["allowed_values"]

        if "user_synonyms" in fields:
            row.user_synonyms = {
                value: [w.strip() for w in words if w and w.strip()]
                for value, words in (fields["user_synonyms"] or {}).items()
            }

        if "user_values" in fields:
            shipped = {str(v) for v in (row.allowed_values or [])}
            row.user_values = [
                v.strip()
                for v in (fields["user_values"] or [])
                if v and v.strip() and v.strip() not in shipped
            ]

        if "derivation_rules" in fields:
            row.derivation_rules = _validate_rules(
                fields["derivation_rules"] or [],
                spec_key=row.spec_key,
                # Merged, so a value staff just added is immediately usable in a rule -
                # and INCLUDING the suppressed ones, so taking a value away does not
                # invalidate the rules that read it. Those rules stay stored and stop
                # firing (see configured_rules), which is what makes suppression
                # reversible instead of a one-way delete of somebody's rule.
                allowed_values=[
                    *merged_allowed_values(row),
                    *[str(v) for v in (row.suppressed_values or [])],
                ],
                data_type=row.data_type,
            )

        if "value_labels" in fields:
            # A closed list (allowed_values, merged with what staff added and minus
            # what they took away) is what a label may name. Without one - `brand`,
            # `class` - the words a synonym is keyed under are the only values the row
            # knows by name.
            valid_keys = {str(v) for v in merged_allowed_values(row)}
            if not valid_keys:
                valid_keys = set(merged_synonyms(row).keys())
            cleaned_labels: dict[str, str] = {}
            for value_key, label in (fields["value_labels"] or {}).items():
                value_key = str(value_key).strip()
                label = str(label or "").strip()
                if not label:
                    # An empty label drops the key rather than storing a blank one.
                    continue
                if value_key not in valid_keys:
                    raise AppException(
                        status_code=422,
                        message=f"\"{value_key}\" is not one of {row.label}'s values.",
                        code="spec_registry_label_unknown_value",
                    )
                if len(label) > 60:
                    raise AppException(
                        status_code=422,
                        message=f"The label for \"{value_key}\" is too long (60 characters max).",
                        code="spec_registry_label_too_long",
                    )
                cleaned_labels[value_key] = label
            # Reassigned, never mutated in place: JSONB in-place mutation is not
            # tracked by SQLAlchemy, so `row.value_labels[key] = ...` would silently
            # not persist.
            row.value_labels = cleaned_labels

        if "value_weights" in fields:
            row.value_weights = {
                str(value).strip(): float(bonus)
                for value, bonus in (fields["value_weights"] or {}).items()
                if str(value).strip() and float(bonus) != 0
            }

        if "excluded_values" in fields:
            row.excluded_values = [
                v.strip() for v in (fields["excluded_values"] or []) if v and v.strip()
            ]

        if "applies_when" in fields:
            if "brand" in {str(k).strip().lower() for k in (fields["applies_when"] or {})}:
                raise _reject(BRAND_IS_NOT_A_SPEC, "spec_registry_brand")
            row.applies_when = {
                str(key).strip(): [str(v).strip() for v in (values or []) if str(v).strip()]
                for key, values in (fields["applies_when"] or {}).items()
                if str(key).strip() and [v for v in (values or []) if str(v).strip()]
            }

        if "label" in fields and fields["label"] is not None and fields["label"] != row.label:
            # The owner's name from here on: the seed repair leaves it alone.
            row.label_owned = True
        for field in ("label", "rank_weight", "is_active", "match_tolerance", "match_decay"):
            if field in fields and fields[field] is not None:
                setattr(row, field, fields[field])

        if "parser_hint" in fields:
            row.parser_hint = hint or None

        # Cleared ON PURPOSE is a real answer here ("stop dropping numbers on this
        # key"), so this one applies a None it was actually sent rather than skipping it
        # with the block above.
        if "max_value" in fields:
            row.max_value = fields["max_value"]

        # A value nobody can say is unsearchable, and a newly added one has no words
        # yet. Rather than refusing the save, give it the obvious word - its own name,
        # readably - which is what the person adding "free_standing" meant anyway. They
        # can add more beside it.
        if row.user_values:
            words = dict(row.user_synonyms or {})
            for value in row.user_values:
                if not words.get(value) and not (row.synonyms or {}).get(value):
                    words[value] = [str(value).replace("_", " ")]
            row.user_synonyms = words

        # Judged only when this save touches the vocabulary. A rule, a scope or a cap
        # does not change which values a customer can say, so a value that was already
        # unreachable must not block saving a rule (that is fixed where it is broken).
        if set(fields) & _VOCABULARY_FIELDS:
            _validate_reachable(row.data_type, merged_allowed_values(row), merged_synonyms(row))

        reading_after = _reading(row)
        db.commit()
        db.refresh(row)

        products_updated = 0
        if fingerprint_before is not None and _reading_changed(reading_before, reading_after):
            products_updated = product_spec_rederive.reread_after_save_logged(
                db, row.spec_key, fingerprint_before=fingerprint_before
            )
            db.refresh(row)
        return {**_serialise(row), "products_updated": products_updated}
    except Exception as e:
        if type(e).__name__ in {"AppException", "HTTPException"}:
            raise
        raise handle_internal_error(str(e))
    finally:
        product_spec_preview.end_catalogue_read(read_token)


# The fields that change what a product reads: a rule, a scope, a cap, or a choice taken
# away (a rule setting a suppressed value stops firing, `configured_rules`; review S-10).
_READING_FIELDS = {"derivation_rules", "applies_when", "max_value", "suppressed_values"}


def _reading(row) -> tuple:
    from app.services.product_spec_derivation import stored_or_shipped_rules

    return (
        stored_or_shipped_rules(row),
        dict(row.applies_when or {}),
        row.max_value,
        sorted(str(v) for v in (row.suppressed_values or [])),
    )


def _reading_changed(before: tuple, after: tuple) -> bool:
    """Whether the rules, the scope or the cap moved, compared as data (a Decimal cap
    and the float it was saved from are the same cap)."""
    def canonical(reading: tuple) -> str:
        rules, scope, cap, dropped = reading
        return json.dumps(
            [rules, scope, None if cap is None else float(cap), dropped],
            sort_keys=True,
            default=str,
        )

    return canonical(before) != canonical(after)


class SpecValueAdd(BaseModel):
    """One word, and nothing else. The list is the server's to hold."""

    value: str = Field(min_length=1, max_length=150)


@router.post("/{spec_key}/values")
def add_spec_value(
    spec_key: str,
    payload: SpecValueAdd = Body(...),
    # The same either-grant the vocabulary-only PATCH field takes: adding a word from
    # the product page is a merchandiser's job (Journey A step 3).
    current_user: dict = Depends(
        require_any_permission_with_api_key(
            ["master_data.spec_registry.edit", "master_data.products.edit"]
        )
    ),
    db: Session = Depends(get_db),
):
    """Append ONE word to a key's vocabulary, without the caller holding the list.

    The PATCH above replaces `user_values`, which is right for the registry editor -
    it shows the whole list and submits the whole list. It is wrong for a merchandiser
    adding one word from a product page: that client rebuilds the list from a response
    it fetched some time ago, so a word another person added in between is absent from
    the payload and is deleted by a request that was only ever meant to add.

    So the word arrives alone and the row is re-read under `FOR UPDATE`: two people
    adding at once serialise instead of overwriting each other, and the near-duplicate
    guard runs against the vocabulary as it is NOW rather than as the caller last saw
    it. There is no acknowledgement bypass - a collision is refused, full stop.
    """
    try:
        row = (
            db.query(ProductSpecRegistry)
            .filter_by(spec_key=spec_key)
            .with_for_update()
            .first()
        )
        if row is None:
            raise handle_not_found("Spec key", spec_key)

        proposed = payload.value.strip()
        if not proposed:
            raise _reject("A value needs a word.", "spec_registry_empty_value")

        # Already there verbatim: the word IS in the vocabulary, which is what the
        # caller asked for. Refusing it as a duplicate of itself would turn a double
        # click into an error.
        if proposed in {str(v) for v in merged_allowed_values(row)}:
            return _serialise(row)

        # A word an administrator TOOK AWAY. It is absent from the merged vocabulary, so
        # neither check above sees it, and adding it back here would hand every holder
        # of `products.edit` a silent override of a decision made on the key itself.
        # Refusing is also the only honest answer: a value that stayed shipped-but-
        # suppressed could not then be saved on the product, and the save would name the
        # very action the user had just been told succeeded.
        suppressed = next(
            (
                str(v)
                for v in (row.suppressed_values or [])
                if normalise_vocabulary(v) == normalise_vocabulary(proposed)
            ),
            None,
        )
        if suppressed is not None:
            return _similar_refusal(
                f"\"{proposed}\" was taken off {row.label}, so it is not one of its "
                "values. An administrator can put it back on the specification's own "
                "screen.",
                {
                    "value": suppressed,
                    "matched_on": "suppressed_value",
                    "matched_text": suppressed,
                },
            )

        match = find_similar_value(row, proposed)
        if match:
            return _similar_refusal(
                f"\"{proposed}\" is already {match['value']} on {row.label}.", match
            )

        _word_refusal(db, row, proposed, vocabulary_owners(db))

        row.user_values = [*(row.user_values or []), proposed]

        # A value nobody can say is unsearchable, and a new one has no words yet. Give
        # it its own name, readably - what the person adding "free_standing" meant.
        if not (row.user_synonyms or {}).get(proposed) and not (row.synonyms or {}).get(proposed):
            row.user_synonyms = {
                **(row.user_synonyms or {}),
                proposed: [proposed.replace("_", " ")],
            }

        _validate_reachable(row.data_type, merged_allowed_values(row), merged_synonyms(row))

        db.commit()
        db.refresh(row)
        return _serialise(row)
    except Exception as e:
        if type(e).__name__ in {"AppException", "HTTPException"}:
            raise
        raise handle_internal_error(str(e))


@router.delete("/{spec_key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_spec_key(
    spec_key: str,
    current_user: dict = Depends(
        require_permission_with_api_key("master_data.spec_registry.delete")
    ),
    db: Session = Depends(get_db),
):
    """Delete a user-created key. Seeded keys are deactivated, never deleted.

    A seeded key would simply reappear on the next deploy, so offering "delete" for one
    would be a button that silently does nothing. The refusal lives in
    `product_spec_registry.delete_registry_key`, shared with the `spec_key.delete`
    record action (D.6) so the gear's Delete and this route can never disagree.
    """
    try:
        from app.services.product_spec_registry import delete_registry_key

        delete_registry_key(db, spec_key)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        if type(e).__name__ in {"AppException", "HTTPException"}:
            raise
        raise handle_internal_error(str(e))
