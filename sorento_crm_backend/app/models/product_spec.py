"""Spec search storage: the vocabulary, and later the values derived against it.

Kept out of `product.py` because this is a separate concern with its own lifecycle:
`product.py` models the catalog as the business maintains it, this models what spec
search derives from it.

See documentation/plans/products/PLAN-spec-search.md section 6.
"""
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.database import Base
from app.models.base import CompanyScopedMixin


class ProductSpecRegistry(Base):
    """The spec vocabulary, read by BOTH the CRM ranker and the n8n parser.

    One source of truth on purpose. If the parser held its own copy, the two would
    drift the first time a value was renamed, and the drift is silent: the parser
    emits a value the ranker never matches, every query scores worse, and nothing
    logs an error.
    """

    __tablename__ = "product_spec_registry"

    # Surrogate uuid PK, per ADR-PRODUCT-STANDARDS: the polymorphic key columns can
    # only be typed uuid if every id is one. `spec_key` stays the key people use - it is
    # unique, it just is not the primary key.
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    spec_key = Column(String(64), nullable=False, unique=True)
    label = Column(String(150), nullable=False)
    # enum | numeric | boolean. Drives extraction (an enum is matched against
    # allowed_values, a numeric is parsed and compared with tolerance).
    data_type = Column(String(16), nullable=False)
    unit = Column(String(16), nullable=True)
    # Closed vocabulary for enum keys. Empty for `class` and `brand`, which are open
    # and sourced from product_categories: a frozen list there would go stale the
    # moment a category is added.
    allowed_values = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # Values that EXIST in the catalog but are not things a customer searches for.
    # `brand` holds OTHERS and NO LOGO, which record the absence of a brand; offered to
    # a model as options they read as "none of the above", so any word it could not
    # place got filed under one - "interlignet wc" came back branded OTHERS. Excluded
    # here they are simply not offered, and the unplaceable word stays a free term.
    # A tuning knob, not vocabulary: seeded once, then owned by whoever tunes it.
    excluded_values = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # value -> [customer phrasings]. Customer language is not catalog language: nobody
    # types "stainless_steel", they type "s/steel" or "stainless".
    synonyms = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # Empty list means "every class". Otherwise the key is only proposed for the
    # classes listed, so wc_form is never offered for a kitchen sink.
    applies_to_classes = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # Conditional gate on ANOTHER spec's value, e.g. diameter only applies when
    # shape is round or square. Ungated, diameter would be proposed for a rectangular
    # product, where it is meaningless.
    applies_when = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # Multiplier on this key's contribution to the match score. Hand-tuned against the
    # eval baseline, so the seed must never overwrite it.
    rank_weight = Column(Numeric(6, 3), nullable=False, server_default=text("1.0"))
    # How close a NUMERIC value must be to count as a match. These are properties of
    # the quantity, not of the ranker, which is why they live here: one module-level
    # "+/- 5" was a millimetre intuition applied to every numeric key, and it made a
    # one-bowl sink an EXACT match for "double bowl" (1 and 2 are within 5).
    #
    #   match_tolerance : distance still scored as a perfect match
    #   match_decay     : distance at which the score reaches zero. 0 means
    #                     exact-or-nothing, which is what a COUNT needs.
    #
    # Defaulted from `unit` at seed time, then owned by whoever tunes them - the same
    # split as rank_weight.
    match_tolerance = Column(Numeric(10, 3), nullable=False, server_default=text("0"))
    match_decay = Column(Numeric(10, 3), nullable=False, server_default=text("0"))
    # The number above which a reading is a typo rather than a measurement. Seeded 5000
    # on every millimetre key, blank everywhere else, and blank means no cap.
    #
    # It was `MAX_PLAUSIBLE_MM`, a constant in the derivation engine, which said the
    # same thing about a 6 mm glass thickness and a 1.8 m bath and could be changed by
    # nobody. The catalogue really does carry "540X440180MM" - a separator typo that
    # parses as a 440-metre sink - so the cap has to exist; it just belongs to the key.
    max_value = Column(Numeric(12, 3), nullable=True)
    # How many catalog codes carried this key when it was seeded. Recorded so a later
    # reviewer can see why a key is weighted low without redoing the measurement.
    measured_coverage = Column(Integer, nullable=True)
    # Hand-flippable. A key with no source yet (bowl_count) ships inactive so the
    # parser never extracts it and the ranker never weights it.
    is_active = Column(Boolean, nullable=False, server_default=text("true"), default=True)
    # `seed` | `user`. The seed REPAIRS drift on every deploy so the CRM ranker and the
    # n8n parser can never disagree about what a value is called - that guarantee is
    # why this table exists. A user-created key has no seed to drift from, so it is
    # never touched. Without the flag, "editable from the UI" and "repaired on deploy"
    # are the same row fighting each other, and the deploy always wins.
    source = Column(String(16), nullable=False, server_default=text("'seed'"))
    # Extra customer phrasings added by staff, merged with (never replacing) the seed's
    # synonyms at read time. Adding a word for a shipped key is the common case and
    # should not require taking ownership of the whole row.
    user_synonyms = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # Values staff added to a SHIPPED key. Additive and never seed-repaired, exactly as
    # user_synonyms is: `allowed_values` is the parser's contract and an edit there would
    # be reverted on deploy, which left "add the value first" as an instruction nobody
    # could follow. Read merged with allowed_values; see merged_allowed_values().
    user_values = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # {value: [words this business does NOT use for it]}. The mirror of user_synonyms:
    # staff-owned, never seed-repaired, subtracted at read time. Nothing is deleted, so
    # un-suppressing a word puts it straight back.
    suppressed_synonyms = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # Shipped VALUES this business has taken away, on the same bargain as the words
    # above: staff-owned, never seed-repaired, subtracted at read time. `user_values`
    # could only ever ADD, so a shipped value was permanent - a business that does not
    # sell french gold had no way to stop the ranker offering it.
    suppressed_values = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # A standing preference for particular VALUES of this key: {"SORENTO": 1.5}. Applied
    # to any product carrying the value, whether or not the customer asked for it, so
    # "our own brand first" is a setting rather than a deploy. NOT applied when the
    # customer named this key themselves - someone who asks for Bravat is asking for
    # Bravat, and a house preference that overrode that would be a bug, not a boost.
    # Calibration, like rank_weight: seeded once, then owned by whoever tunes it.
    value_weights = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # HOW this key is read out of a product's text, as an ordered list of rules. First
    # match wins, so order is priority ("STAINLESS STEEL" must sit above "STEEL").
    #
    #   {"match": "contains",    "pattern": "S/STEEL 304", "value": "stainless_steel"}
    #   {"match": "ends_with",   "pattern": "SQUATTING PAN", "value": "Squatting Pan"}
    #   {"match": "present",     "pattern": "OVER\\s*FLOW", "value": true}
    #   {"match": "regex",       "pattern": "(\\d+)MM S-TRAP", "capture": 1, "unit": "mm"}
    #   {"match": "code_suffix", "pattern": "BL", "value": "black"}
    #
    # Optional "source": "any" (default) | "description" | "flyer". These were Python
    # lists, which meant a key created in the UI could never be populated - the form
    # made a promise the engine could not keep.
    # NULL: this key reads with the shipped rules. A list, even an empty one, is this
    # business's own: [] means the key has no rules (review B-1, #1286). Read it through
    # `product_spec_derivation.stored_or_shipped_rules`, never `or`.
    derivation_rules = Column(JSONB, nullable=True)
    # value -> display label ("pp" -> "PP"), purely cosmetic (#423). Editable on seed
    # AND user rows, like user_synonyms: staff-owned, never seed-repaired (nothing in
    # `_seed_values` names this column, so the repair loop cannot touch it).
    value_labels = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict)
    # What this field means to the chatbot: one line (max 200) printed on its line in the
    # parser's `{{specs}}`. Staff-owned and never seed-repaired (`_seed_values` does not
    # name it).
    parser_hint = Column(Text, nullable=True)
    # True once staff edited `label`: the seed repair then leaves the name alone.
    label_owned = Column(Boolean, nullable=False, server_default=text("false"), default=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True, onupdate=func.now())


class ProductFlyerText(Base):
    """What the printed flyer says about a product code.

    RETIRED AS AN INPUT (PR 4, AC-B.18): derivation no longer reads this table, and a
    flyer reaches specs only as reviewed proposals from pasted text. The table stays
    until the later deploy that drops it (RUNBOOK-flyer-promote.md, step 6), so a
    rollback still has the text. It was a second text source for derivation, keyed on
    the CODE for the same reason derivation is - one card describes the model, and the
    model exists once per company.
    """

    __tablename__ = "product_flyer_text"

    # Surrogate uuid PK, per ADR-PRODUCT-STANDARDS: the polymorphic key columns can
    # only be typed uuid if every id is one. `product_code` stays the key people use - it is
    # unique, it just is not the primary key.
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_code = Column(String(100), nullable=False, unique=True)
    # Which flyer, in words a person recognises ("SORENTO A3 FLYER 2025-2026").
    source_label = Column(String(200), nullable=False)
    source_id = Column(String(64), nullable=True)
    lines = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    text = Column(Text, nullable=False, server_default="")
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True, onupdate=func.now())


class ProductSpecSearchPolicy(Base):
    """The ranker's scoring knobs, as data.

    These were module constants, which meant "discontinued products should rank lower"
    needed an engineer. Each row is one number the ranker reads at search time, seeded
    with the constant it replaced so the behaviour on day one is identical.
    """

    __tablename__ = "product_spec_search_policy"

    # Surrogate uuid PK, per ADR-PRODUCT-STANDARDS: the polymorphic key columns can
    # only be typed uuid if every id is one. `policy_key` stays the key people use - it is
    # unique, it just is not the primary key.
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    policy_key = Column(String(64), nullable=False, unique=True)
    label = Column(String(150), nullable=False)
    value = Column(Numeric(10, 3), nullable=False)
    # Shown next to the field. A number with no explanation gets tuned by guesswork.
    help_text = Column(Text, nullable=False, server_default="")
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True, onupdate=func.now())


class ProductSpecifications(Base):
    """Derived spec values for one product row.

    Keyed on `product_id`, but derived and reviewed per `product_code`: the same model
    exists once per company (11,414 codes across 22,805 rows), and deriving per row
    would let the two copies disagree with nothing detecting it. One derivation fans
    out to every row sharing the code.
    """

    __tablename__ = "product_specifications"
    __audit_skip__ = "derived from products by derive_product_specs, 513 to 5,220 rows a day (measured 27 Sep 2026, review B3)"

    # Surrogate uuid PK, per ADR-PRODUCT-STANDARDS: the polymorphic key columns can
    # only be typed uuid if every id is one. `product_id` stays the key people use - it is
    # unique, it just is not the primary key.
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id = Column(
        UUID(as_uuid=False),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # {"diameter": {"value": 407, "unit": "mm"}, "material": {"value": "ceramic"}}
    values = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # Same keys as `values`: {"source", "confidence", "evidence"}. A source in
    # `product_spec_write.AUTHORED_SOURCES` marks a value a person set, which
    # re-derivation must never overwrite - test membership in that set, never `==
    # 'human'`. An authored entry may also carry `absent: true`, a tombstone saying this
    # product does not have this spec, in which case the key is deliberately NOT in
    # `values`. All three columns are written in `product_spec_write` and nowhere else.
    provenance = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # The code-free spec sentence that gets embedded. Rendered inside
    # `product_spec_write.write_spec_row` so it can never drift from `values`.
    rendered_text = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, server_default="derived")
    # Hash of the derivation inputs. Equal hash means nothing to do, so a re-run over
    # the whole catalog costs one read per code instead of a rewrite.
    derived_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True, onupdate=func.now())

    __table_args__ = (
        Index("ix_product_specifications_values", "values", postgresql_using="gin"),
        Index("ix_product_specifications_status", "status"),
    )


class ProductSpecException(Base):
    """Something a human needs to look at. Exceptions only, never routine successes.

    If this table fills with successes the filter is wrong, and the queue becomes the
    data-entry programme the design exists to avoid.
    """

    __tablename__ = "product_spec_exceptions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_code = Column(String(100), nullable=False)
    spec_key = Column(String(64), nullable=False)
    # shape_mismatch - stored L/W/H describe a round or square product
    # column_conflict - the description disagrees with a stored column
    # low_confidence - derived below the review threshold
    reason = Column(String(48), nullable=False)
    proposed = Column(JSONB, nullable=True)
    stored = Column(JSONB, nullable=True)
    resolved_at = Column(DateTime(timezone=False), nullable=True)
    resolved_by = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index(
            "ix_product_spec_exceptions_open",
            "product_code",
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )


class ProductFindabilityRun(Base):
    """One sweep of a flyer, asking every card for its own product.

    Persisted rather than printed because the point is comparison: the number after a
    vocabulary change only means something next to the number before it. Keyed on the
    flyer's `source_id`, so the Cabana and Mocha flyers are new rows, not new code.
    """

    __tablename__ = "product_findability_runs"
    __audit_skip__ = "findability test run, recomputed"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id = Column(String(64), nullable=True)
    source_label = Column(String(200), nullable=True)
    # A full flyer takes about half an hour, so the sweep runs in the background and
    # this is how the screen knows whether it is watching a total or a work in progress.
    status = Column(String(16), nullable=False, server_default="running")
    error = Column(Text, nullable=True)
    # How deep a result still counts as found.
    window = Column(Integer, nullable=False, server_default=text("25"))
    cards = Column(Integer, nullable=False, server_default=text("0"))
    # Found from the card's own printed words - the angle that catches a missing spec.
    found_by_card = Column(Integer, nullable=False, server_default=text("0"))
    # Found from every spec the flyer states, together. The best case.
    found_by_specs = Column(Integer, nullable=False, server_default=text("0"))
    not_found = Column(Integer, nullable=False, server_default=text("0"))
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)


class ProductFindabilityResult(Base):
    """One card, and every way of asking for it."""

    __tablename__ = "product_findability_results"
    __audit_skip__ = "findability test result, recomputed"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(
        UUID(as_uuid=False),
        ForeignKey("product_findability_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_code = Column(String(100), nullable=False)
    # A discontinued code losing to the live one that replaced it is the ranker working.
    is_discontinued = Column(Boolean, nullable=False, server_default=text("false"))
    phrase = Column(Text, nullable=False, server_default="")
    # The easiest question that finds it: "one:product_type", "card", "all", or "none".
    boundary = Column(String(64), nullable=False, server_default="none")
    # {"all": 1, "one:finish": null, ...} - every angle and where it landed, so the
    # screen can show WHICH way of asking failed without re-running the sweep.
    ranks = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_findability_results_run", "run_id"),
        Index("ix_findability_results_boundary", "boundary"),
    )


class ProductSpecFlyerBatch(Base, CompanyScopedMixin):
    """One proposal pass over one flyer reading.

    The dealer kit reads a flyer; this is master data's answer to "what does that
    flyer SAY about the products it names". It is a separate table from
    `dealer_kit.flyer_reading` because it has its own lifecycle - proposing,
    proposed or failed, its own counts, and its own applied stamp - and folding
    that onto another module's row would put master-data state on a table the
    dealer kit owns and may drop.

    UNIQUE on `flyer_reading_id`: one batch per reading (AC-A.5). Re-proposing
    deletes the batch's proposals and recomputes them against the master as it is
    NOW, rather than accumulating a second set nobody asked for. The history of
    what was ever applied lives in the spec provenance, which survives the delete.
    """

    __tablename__ = "product_spec_flyer_batches"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    flyer_reading_id = Column(
        UUID(as_uuid=False),
        ForeignKey("dealer_kit.flyer_reading.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # proposing | proposed | failed. The database CHECK holds the same three.
    status = Column(String(16), nullable=False, server_default=text("'proposing'"))
    # Why the pass failed, in the words the job recorded. NULL on a row that has
    # not failed.
    error_message = Column(Text, nullable=True)
    # The RQ job, for an operator asking where a pass went.
    job_id = Column(String(64), nullable=True)

    product_count = Column(Integer, nullable=False, server_default=text("0"))
    proposal_count = Column(Integer, nullable=False, server_default=text("0"))
    new_count = Column(Integer, nullable=False, server_default=text("0"))
    change_count = Column(Integer, nullable=False, server_default=text("0"))
    conflict_count = Column(Integer, nullable=False, server_default=text("0"))
    unchanged_count = Column(Integer, nullable=False, server_default=text("0"))
    suppressed_count = Column(Integer, nullable=False, server_default=text("0"))
    # How many rows of this batch have ever been written, over every apply.
    applied_count = Column(Integer, nullable=False, server_default=text("0"))
    # How many of `product_count` are a sibling picked up via another product's
    # card (PLAN-flyer-family-proposals.md R1) rather than their own printed code.
    # Zero on a batch with no families, and on every batch proposed before this
    # feature existed.
    via_count = Column(Integer, nullable=False, server_default=text("0"))

    created_by = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=False), nullable=True)
    # The LATEST apply, not the first: a reviewer works through a batch over
    # several sittings, and "when was this last written from" is the question the
    # list screen asks.
    applied_at = Column(DateTime(timezone=False), nullable=True)
    applied_by = Column(UUID(as_uuid=False), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('proposing', 'proposed', 'failed')",
            name="ck_product_spec_flyer_batches_status",
        ),
        Index("ix_product_spec_flyer_batches_created", "created_at"),
    )


class ProductSpecFlyerProposal(Base):
    """One key a flyer states about one product, and how it stands against the master.

    Stored rather than recomputed on read, because the apply payload names these
    ids (never values - the values come off the reading, which is the whole
    security model of the route) and because a row remembers its own `outcome`
    after somebody has decided about it.

    `kind` is a SNAPSHOT taken when the pass ran. Apply re-classifies against the
    live spec row before writing anything, so a batch proposed yesterday cannot
    overwrite what somebody set this morning.

    Not `CompanyScopedMixin`: a proposal is reachable only through its batch,
    which is scoped, and the cascade below is what removes it.
    """

    __tablename__ = "product_spec_flyer_proposals"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id = Column(
        UUID(as_uuid=False),
        ForeignKey("product_spec_flyer_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The match as it stood when the pass ran. Kept as BOTH id and code: the id
    # links to the product record, the code is what the person reads and what the
    # write choke point takes.
    product_id = Column(
        UUID(as_uuid=False), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    product_code = Column(String(100), nullable=False)
    # Which flyer pages the code was printed on, for ordering and for a reviewer
    # holding the paper.
    pages = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # The printed code whose card filled THIS row's gaps (PLAN-flyer-family-
    # proposals.md R1), when `product_code` was never itself printed on the
    # flyer - e.g. `SRTWC8152-SH` on a row for `SRTWC8152-SH-UF-300`. NULL on a
    # row proposed from the product's own card.
    via_product_code = Column(String(100), nullable=True)

    spec_key = Column(String(100), nullable=False)
    # Scalar or list, exactly as `propose_from_text` returned it.
    value = Column(JSONB, nullable=False)
    # The registry's unit, never the flyer's: the unit belongs to the key.
    unit = Column(String(32), nullable=True)
    # The printed words the value was read from. What makes the badge honest a
    # year later.
    evidence = Column(Text, nullable=False, server_default="")
    # new | change | conflict | unchanged | suppressed, at propose time.
    kind = Column(String(16), nullable=False)
    # What the product held when the pass ran, so the review screen can show the
    # two side by side without a second query per row.
    stored_value = Column(JSONB, nullable=True)
    stored_unit = Column(String(32), nullable=True)
    stored_source = Column(String(32), nullable=True)

    # flyer | manual. Who put this row here: the pass that read the paper, or a
    # person who added the key the flyer missed while reviewing it. It decides the
    # SOURCE the value is written under (AC-G.2) - a machine read stays `flyer`,
    # a person's typing is `human` - so it is a column rather than a guess made
    # from whether `edited_at` is set.
    origin = Column(String(16), nullable=False, server_default=text("'flyer'"))
    # When a person last changed this row's value on the review screen, and who.
    # `edited` on the wire is derived from `edited_at`, so there is one fact here
    # rather than a flag that can disagree with its own timestamp.
    edited_at = Column(DateTime(timezone=False), nullable=True)
    edited_by = Column(UUID(as_uuid=False), nullable=True)

    # applied | already_matches | conflict_not_confirmed | product_spec_bad_value |
    # product_not_found. NULL until somebody has decided about this row.
    outcome = Column(String(32), nullable=True)
    applied_at = Column(DateTime(timezone=False), nullable=True)
    applied_by = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        # One row per key per product per batch. A flyer printing a code twice is
        # one product, so the second card must not produce a second row nobody can
        # tell from the first.
        UniqueConstraint(
            "batch_id", "product_id", "spec_key", name="uq_product_spec_flyer_proposal"
        ),
        CheckConstraint(
            "kind IN ('new', 'change', 'conflict', 'unchanged', 'suppressed')",
            name="ck_product_spec_flyer_proposals_kind",
        ),
        CheckConstraint(
            "origin IN ('flyer', 'manual')",
            name="ck_product_spec_flyer_proposals_origin",
        ),
        Index("ix_product_spec_flyer_proposals_batch", "batch_id"),
    )


class ProductSpecVerification(Base):
    """Who vouched for a code's specs, and who or what took that back.

    Keyed on `product_code` for the same reason derivation is: the model exists once
    per company and a person confirming its specs is confirming the model, not one
    company's copy of it. Deliberately NOT company-scoped, matching every other spec
    table (AC-D.1).

    Append-only in the sense that matters: a verification is one row, stamped once,
    and it is never deleted or re-pointed. Withdrawing it fills the `invalidated_*`
    fields on that same row, so the row still answers both halves of the question -
    who vouched for this, and who (or what) took it back. Re-verifying afterwards
    inserts a NEW row and leaves the withdrawn one exactly as it was.

    `verified_by_user_id` is text with no FK on purpose: a deleted user must not take
    the history of what they verified with them.

    The state a screen shows is DERIVED from these rows (AC-D.2) and never stored: no
    rows reads unverified, an active row reads verified, a latest row invalidated by
    hand reads unverified, and any other invalidation reads needs-re-verify with the
    diff that caused it. Nothing re-hashes values to decide a pill.
    """

    __tablename__ = "product_spec_verifications"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_code = Column(String(100), nullable=False)
    # `internal` today. `supplier` arrives with the supplier portal in milestone 2, and
    # the partial unique index below is per party so the two stamps coexist on one code
    # without either being able to overwrite the other.
    party = Column(String(16), nullable=False, server_default="internal", default="internal")
    supplier_id = Column(UUID(as_uuid=False), nullable=True)
    verified_by_user_id = Column(Text, nullable=True)
    # Stamped rather than joined: a no-FK text id cannot be joined for a display name,
    # and the repo forbids UUIDs in the UI.
    verified_by_name = Column(Text, nullable=True)
    verified_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    values_hash = Column(String(64), nullable=False)
    invalidated_at = Column(DateTime(timezone=False), nullable=True)
    # values_changed - a write moved the values out from under the stamp.
    # manual_unverify - a person withdrew it.
    invalidated_reason = Column(String(32), nullable=True)
    # {"changed": [{"spec_key", "was", "now"}]} for values_changed, null for a manual
    # withdrawal: a withdrawal has no diff, and rendering one with an empty diff would
    # misrepresent it as a re-check.
    invalidated_diff = Column(JSONB, nullable=True)
    # Both null means the system did it.
    invalidated_by_user_id = Column(Text, nullable=True)
    invalidated_by_name = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        # One live stamp per code per party. The partial index is what makes a
        # concurrent double-verify land as one row rather than two.
        Index(
            "uq_product_spec_verifications_active",
            "product_code",
            "party",
            unique=True,
            postgresql_where=text("invalidated_at IS NULL"),
        ),
        Index("ix_product_spec_verifications_code", "product_code"),
    )
