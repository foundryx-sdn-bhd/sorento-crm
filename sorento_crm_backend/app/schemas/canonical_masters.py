"""Canonical shapes for master data pushed in by the ESB (Phase C).

**Canonical, not AutoCount.** Sorento never sees `DocKey`, `AccNo` or `ItemCode`
 -  the ESB translates before it gets here. That boundary is what keeps vendor
quirks (`"T"`/`"F"` booleans, `Dtlkey` vs `DtlKey` casing, per-customer UDF
arrays) out of Sorento entirely.

Every model DROPS unknown fields and the ingest route logs their names once per
request (owner decision, 1 Oct 2026, `app.schemas.ingest_extras`). Rejecting them
used to fail every record of an entity the day the shared service mapped a new
AutoCount column, so the two repos had to deploy in a fixed order. The log line is
what keeps a field the ESB believed it sent from being silently lost.

Related records are addressed by **code, never by local id**. The ESB has no
knowledge of Sorento's UUIDs, and a code that does not resolve yet is a
sequencing artefact the ingest reports as retryable rather than invalid.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.ingest_extras import INGEST_MODEL_CONFIG, note_unknown_fields


class _Canonical(BaseModel):
    # Unknown keys are dropped and their names logged, never refused (owner decision,
    # 1 Oct 2026, `app.schemas.ingest_extras`). Declared fields stay strictly typed.
    model_config = INGEST_MODEL_CONFIG

    @model_validator(mode="before")
    @classmethod
    def _note_unknown(cls, data):
        note_unknown_fields(cls, data)
        return data

    @model_validator(mode="before")
    @classmethod
    def _blank_optional_string_is_null(cls, data):
        """D14's third state (ingest parity, 2026-09-06): a blank string on an
        OPTIONAL field is an explicit clear, exactly what a blank cell means to
        the xlsx import. The ESB sends `""` for a mapped AutoCount field that
        is empty (it omits None and never sends null), and storing that `""`
        verbatim would be the one column shape the upload can never produce.
        Required identity fields (`code`, `name`, `source_ref`) are left alone:
        `""` there still fails `min_length=1` - blank is not a way to omit them.
        Runs before `str_strip_whitespace`, so whitespace-only counts as blank.
        """
        if not isinstance(data, dict):
            return data
        for name, field in cls.model_fields.items():
            value = data.get(name)
            if isinstance(value, str) and not value.strip() and not field.is_required():
                data[name] = None
        return data

    # AutoCount's stable DocKey. The idempotency key: without it a re-push
    # cannot be told from a new record and every sync duplicates.
    source_ref: str = Field(..., min_length=1, max_length=255)
    # Human-facing document/account number. Display only -- expected to change.
    source_doc_no: Optional[str] = Field(None, max_length=100)


class CanonicalProductCategory(_Canonical):
    """Products cannot be created without a category (products.category_id is
    NOT NULL), so this must sync before products or every product reports
    retryable."""

    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    # Absent vs null (D14): omitted leaves the stored value untouched (or, on
    # create, the model's own True default); an explicit null clears it.
    is_active: Optional[bool] = None


class CanonicalBrand(_Canonical):
    """Products sync AFTER brands (autocount-brands-ingest D1/D2): a brand
    push gives the ESB's auto-create-on-product-push path (D9, unchanged) a
    proper master row with its own integration reference, instead of only
    the code = name placeholder a product's `brand_code` creates today. Caps
    are the `brands` column widths (`app/models/product.py`), not the 100/255
    the other reference masters use."""

    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = None
    # Absent vs null (D14): omitted leaves the stored value untouched (or, on
    # create, the model's own True default); an explicit null clears it.
    is_active: Optional[bool] = None


class CanonicalUnitOfMeasure(_Canonical):
    """Likewise for products.base_uom_id."""

    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    # Canonical divisibility, 0..4 (front-planning plan 6.4). Absent (D14) means
    # untouched on update / the model's own 0 default on create - never a value
    # this schema invents.
    decimal_places: Optional[int] = Field(None, ge=0, le=4)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class CanonicalWarehouse(_Canonical):
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    location: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None


class CanonicalSupplier(_Canonical):
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    contact_name: Optional[str] = Field(None, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    phone_number: Optional[str] = Field(None, max_length=100)
    address_line1: Optional[str] = Field(None, max_length=255)
    address_line2: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=40)
    country: Optional[str] = Field(None, max_length=100)
    payment_terms_days: Optional[int] = Field(None, ge=0, le=3650)
    # `payment_terms_code` REMOVED (D15 end state, S4's contract 2.1 cutover):
    # accepted-and-warned `deprecated_field` through S0-S3, now dropped and
    # logged like any other unknown key (`app.schemas.ingest_extras`) - see
    # `documentation/plans/autocount/PLAN
    # -autocount-cross-repo-contract.md` section 10.
    is_active: Optional[bool] = None


class CanonicalCustomer(_Canonical):
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    phone_number: Optional[str] = Field(None, max_length=100)
    registration_number: Optional[str] = Field(None, max_length=100)
    tax_id: Optional[str] = Field(None, max_length=100)
    # `credit_limit` / `payment_terms_days` / `payment_terms_code` REMOVED
    # (D15 end state, S4's contract 2.1 cutover) - see the note on
    # `CanonicalSupplier.payment_terms_days` above; `customers` never had
    # matching columns for any of the three.
    country: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None
    # D16 (S2): AutoCount `Debtor.DebtorType` / `Debtor.AreaCode`. `market_segment_code`
    # folds through `customer_rules.fold_market_segment` - an unknown spelling drops
    # with warning `segment_unknown` rather than failing the record, fill-only (a
    # hand-set segment is never overwritten). `region` is free text, written whenever
    # sent (no reference table - AutoCount states none for it).
    market_segment_code: Optional[str] = Field(None, max_length=50)
    region: Optional[str] = Field(None, max_length=80)
    # AutoCount `Debtor.SalesAgent`. Absent or blank = untouched; an unknown code leaves the
    # agent as it was with warning `agent_unresolved`. A known one is set on the linked ledger
    # and every same-code ledger of the company.
    sales_agent_code: Optional[str] = Field(None, max_length=100)


class CanonicalSalesAgent(_Canonical):
    """The salesperson master, and the only shape here whose row is SHARED.

    `code` is the agent code as a document states it (`SEAN I`, `LCL`); it is
    stored upper-cased and trimmed, because that is how `sales_agent_service`
    stores and matches it and a second spelling would become a second agent with
    its own demand class.

    `person_label` is who the codes belong to - reporting metadata, never
    identity: `SEAN I` and `SEAN III` are two agents, not one person with two
    codes.

    Deliberately NOT here: `internal_note`, `follow_up`, `demand_class` and
    `location_group`. Those are the captain's annotations, made on the master
    screen, and AutoCount holds no opinion about any of them - so they are
    unknown fields, and are dropped (never written) rather than letting a
    weekly re-sync restate a classification nobody upstream owns.
    """

    code: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None
    person_label: Optional[str] = Field(None, max_length=100)


class CanonicalProduct(_Canonical):
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    # products.category_id and base_uom_id are NOT NULL, so these are how a
    # product becomes creatable at all. Unresolvable -> retryable, because the
    # category may simply not have been synced yet.
    category_code: Optional[str] = Field(None, max_length=100)
    uom_code: Optional[str] = Field(None, max_length=100)
    brand_code: Optional[str] = Field(None, max_length=100)
    # AutoCount ItemType (ITEM-TYPE-CRM). Back-created like brand_code; absent or
    # blank leaves the stored link untouched.
    item_type_code: Optional[str] = Field(None, max_length=100)
    # CRM-owned (PLAN D14, price-tag-feedback-r2). Overwrites the stored value
    # only when non-empty; empty/absent leaves a manually entered barcode
    # untouched - see `master_ingest_service._product_columns`.
    bar_code: Optional[str] = Field(None, max_length=100)
    list_price: Optional[Decimal] = Field(None, ge=0)
    cost_price: Optional[Decimal] = Field(None, ge=0)
    is_active: Optional[bool] = None
    # D2 (S1): explicit flag wins over the description-derived **** convention.
    is_discontinued: Optional[bool] = None
    # D4 (S1): AutoCount `Item.Desc2`, stored on its own column - never
    # concatenated into `description` the way the xlsx import's Desc 2
    # handling does. See migration 475_products_remark.
    remark: Optional[str] = Field(None, max_length=500)
