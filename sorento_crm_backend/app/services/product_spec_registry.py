"""Seed and maintain the Spec Registry.

Ownership is split, and the split is the whole point of this module:

  * the SEED owns vocabulary - label, type, unit, allowed_values, synonyms, gates.
    These must match what the parser extracts against, so a drifted value is repaired
    on every re-seed rather than preserved.
  * a HUMAN owns calibration - rank_weight, is_active, the match window, and
    excluded_values. Weights are tuned against the eval baseline and that tuning is the
    only calibration the ranker has, so the seed must never overwrite it.

Scope is the T0 tracer's pilot keys (jayson-odoo/sorento-crm#73). The remaining keys
measured present in the catalog (trap_type, wc_form, rimless, seat_material, ...) land
in T1, and `bowl_count` ships inactive there because nothing in the catalog carries it.

Coverage figures below are measurements taken against the live catalog copy over
11,584 distinct active codes. They are recorded on the row so a later reviewer can see
why a key is weighted the way it is without redoing the work.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.product_spec import ProductSpecRegistry, ProductSpecSearchPolicy
from app.services.product_spec_rules import builder_identity, builder_of, clean_builder
from app.services.product_spec_rules import compile_builder as _compile_builder
from app.services.product_spec_rules import fold_rules

# Fields the seed owns. Anything not listed here is left alone once a row exists.
_SEED_OWNED = (
    "label",
    "data_type",
    "unit",
    "allowed_values",
    "synonyms",
    "measured_coverage",
)
# `applies_when` used to be seed-owned. It is CALIBRATION, not vocabulary: which
# classes may carry a key is a merchandising judgement ("bowl count is not only for
# kitchen sinks"), and the n8n parser is not held to it the way it is held to the value
# list. Seed-owned, an edit here was reverted on the next deploy without saying so.
#
# `applies_to_classes` is gone entirely. It was never seeded, never read by anything,
# and never held a value in any row - a column that existed only to be rendered in a
# table header. The scope people were reading in that column was always `applies_when`.

# How close a numeric value must be to count, defaulted from the key's UNIT.
# `(tolerance, decay)`; decay 0 means exact-or-nothing.
#
# The bug this replaces: one module-level `+/- 5` was a millimetre intuition applied to
# every numeric key, so `bowl_count` 1 vs 2 (distance 1) scored a PERFECT match and a
# single-bowl sink outranked real double-bowl sinks for "double bowl kitchen sink".
#
# A count is exact-or-nothing. Millimetres keep the old constants, so dimensions behave
# exactly as before. Add a row here rather than special-casing a key.
_MATCH_DEFAULTS_BY_UNIT: dict[str | None, tuple[float, float]] = {
    "mm": (5.0, 150.0),
    None: (0.0, 0.0),
}


# The ranker's scoring knobs, seeded with the constants they replaced so behaviour on
# day one is unchanged. `discontinued_penalty` is the one genuinely new number: 4,969
# active-but-discontinued products used to rank as if they were live. It is set below
# CLASS_BOOST on purpose - a discontinued product that answers the question still beats
# a live one that does not, it just answers second.
SEARCH_POLICY_SEED: list[dict] = [
    {
        "policy_key": "class_boost",
        "label": "A matching product class is worth",
        "value": 5.0,
        "help_text": "The strongest signal there is - every product has a class.",
    },
    {
        "policy_key": "free_term_boost",
        "label": "Each matching word is worth",
        "value": 1.2,
        "help_text": "Words matched against the product's spec sentence, not its raw description.",
    },
    {
        "policy_key": "numeric_boost",
        "label": "A matching measurement is worth",
        "value": 2.0,
        "help_text": "Scaled by how close it is - see each key's own tolerance.",
    },
    {
        "policy_key": "mismatch_penalty",
        "label": "Contradicting what was asked costs",
        "value": 2.5,
        "help_text": (
            "A floor-standing WC when the customer said wall hung. Smaller than the match "
            "it opposes: this demotes the product, it never removes it."
        ),
    },
    {
        "policy_key": "discontinued_penalty",
        "label": "Being discontinued costs",
        "value": 2.0,
        "help_text": (
            "4,969 discontinued products are still active and still sellable, so this "
            "ranks them below a live equivalent rather than hiding them."
        ),
    },
    {
        "policy_key": "flyer_source_boost",
        "label": "A spec read from the flyer counts extra",
        "value": 1.5,
        "help_text": (
            "A multiplier on the match, not a score of its own. The flyer is the better "
            "source and often the only one: 14 cards say FRAMELESS where 2 descriptions "
            "do, and 9 say MASSAGE JET where none do. Above 1, a product whose flyer "
            "card states the spec beats one whose description merely mentions the word. "
            "Set it to 1 to treat both sources alike."
        ),
    },
    {
        "policy_key": "human_source_boost",
        "label": "A spec a person set counts extra",
        "value": 1.5,
        "help_text": (
            "A multiplier on the match, like the flyer boost and deliberately a separate "
            "knob so the two can be tuned apart. A value somebody set by hand, or a "
            "supplier confirmed, is the best evidence there is: nobody types a spec that "
            "is already right. Set it to 1 to treat a hand-set spec like a parsed one."
        ),
    },
    {
        "policy_key": "relevance_floor",
        "label": "Show nothing below a score of",
        "value": 1.5,
        "help_text": (
            "Below this the best match is one weak word hit. The chatbot asks for a code "
            "or a photo instead of offering a guess."
        ),
    },
    {
        "policy_key": "max_candidates",
        "label": "Offer at most this many products",
        "value": 5.0,
        "help_text": "Variants of one model collapse onto the model, so five means five models.",
    },
]


SEED_RULE_MARKER = "_seed"

# No sanitaryware product is five metres in any direction, and the catalogue carries
# separator typos ("540X440180MM" parses as 540 x 440180). Seeded onto every millimetre
# key as `max_value`, where it is editable and can be looked at - it was a module
# constant in the derivation engine, which said the same thing about a 6 mm thickness
# and a 1.8 m bath and could be changed by nobody (AC-A.5).
DEFAULT_MM_MAX_VALUE = 5000


def _rule_identity(rule: dict) -> str:
    """A rule's meaning, for telling a shipped rule from a person's: its builder."""
    return builder_identity(builder_of(rule))


# The compiler lives in `product_spec_rules`; re-exported here because this module is
# where callers have always found it.
compile_builder = _compile_builder


# The tub classes, as the class choices store them, the category's broader "Bathtub and
# Jacuzzi" included.
_TUB_CLASSES = ("Bathtub", "Jacuzzi", "Bathtub and Jacuzzi")
# Classes a hose length is never read on (#1286): their "1.75M" is the product itself.
_NO_HOSE_CLASSES = _TUB_CLASSES


def _rules_from_shipped_tables() -> dict[str, list[dict]]:
    """The shipped rules, as builders (#1286, D5).

    Built FROM the token tables in `product_spec_derivation` rather than retyped, so the
    seeded rules cannot drift from the vocabulary the tables hold - a transcription
    error here would be a silent catalogue-wide derivation change. The readers that
    were regular expressions (a number before a word, a size, a flag word) are written
    as the builder the appendix `rule-engine-built-in-rules.md` gives them; nobody reads
    or edits a pattern any more.

    Neighbouring Words rules with the same answer are folded into one rule with several
    words, which is how the rules grid shows them: 268 rows become 202 rules over 49
    specifications. Every rule carries `_seed`, which the seed repair reads.

    Imported inside the function: `product_spec_derivation` imports this module.
    """
    from app.services import product_spec_derivation as d

    def words(table, look_in: str = "any") -> list[dict]:
        return [
            {"kind": "words", "look_in": look_in, "words": [token], "value": value}
            for token, value in table
        ]

    def flag(*phrases: str, **extra) -> list[dict]:
        return [{"kind": "words", "look_in": "any", "words": list(phrases), "value": True, **extra}]

    def number(**parts) -> list[dict]:
        return [{"kind": "number", "look_in": "any", **parts}]

    # A round or square product's stored columns are MIS-KEYED, not merely different:
    # `CONCRETE ROUND BASIN (407X120X10MM)` has 407 in `length` and it is a diameter. So
    # the rules that read a length say "except when Shape is Round or Square" and the
    # diameter rule says "only when" it is (AC-A.1).
    def only_when_round(is_round: bool) -> dict:
        return {"spec": "shape", "is": is_round, "values": ["round", "square"]}

    def column(fact: str) -> dict:
        return {"kind": "product", "fact": fact, "only_when": only_when_round(False)}

    def size(pick, *, look_in: str = "description", when_round: bool | None = None) -> dict:
        # The product master's own text: the size block never reads a pasted flyer
        # card, which states its size in its own labelled rows (the `flyer` rules).
        rule: dict = {"kind": "size", "look_in": look_in, "pick": pick}
        if when_round is not None:
            rule["only_when"] = only_when_round(when_round)
        return rule

    rules: dict[str, list[dict]] = {
        "material": words(d.MATERIAL_TOKENS),
        "mounting": words(d.MOUNTING_TOKENS),
        "control_type": words(d.CONTROL_TOKENS),
        "product_type": words(d.PRODUCT_TYPE_TOKENS),
        "water_supply": words(d.WATER_SUPPLY_TOKENS),
        "steel_grade": words(d.STEEL_GRADE_TOKENS),
        "furniture_type": words(d.FURNITURE_TOKENS),
        # A count must stand on its own: "2-WAYS", "3 WAYS", and now "12 WAY" too.
        "way_count": number(before=["WAY", "WAYS"]),
        "piece_count": number(before=["IN 1"]),
        "capacity_oz": number(before=["OZ"]),
        # The bare "12L" form is where the real data is (6L cisterns, 12L and 20L bins),
        # and the same letters end a product code (SRTKS1008L): a number touching a
        # letter or digit in front is never read.
        "capacity_litre": number(before=["L", "LTR", "LITRE", "LITRES", "LITER", "LITERS"]),
        "power_hp": number(before=["HP"]),
        "is_thermostatic": flag("THERMOSTATIC"),
        # "Sliding" in this catalogue always means a height-adjustable shower rail.
        "has_sliding_rail": flag("SLIDING"),
        "is_high_basin": flag("HIGH BASIN"),
        "has_filter": flag("FILTER TAP"),
        "has_pull_out_shower": flag("PULL OUT SHOWER"),
        "has_chopping_board": flag("CHOPPING BOARD"),
        "has_dish_rack": flag("DISH RACK"),
        "no_overflow": flag("W/O OVERFLOW", "WO OVERFLOW", "WITHOUT OVERFLOW"),
        "has_shower_union": flag("SHOWER UNION"),
        "spray_functions": number(before=["FUNCTION", "FUNCTIONS"]),
        # Printed in metres off the card ("c/w 1.2m"), stored in millimetres like every
        # other length, because that is the unit the ranker compares in.
        # Not on a bathtub or jacuzzi: "BRAVAT 1.75M ... BATHTUB" is the tub's own length,
        # and reading it as a 1750 mm hose was the parity run's finding (#1286). The
        # values are the class labels as the class choices store them, the category's
        # broader "Bathtub and Jacuzzi" included.
        "hose_length": number(
            before=["M"],
            written_in="metres",
            only_when={"spec": "class", "is": False, "values": list(_NO_HOSE_CLASSES)},
        ),
        "is_frameless": flag("FRAMELESS"),
        # Chat fix PR-A (AC-A5): a tub whose catalogue text names its jets. Only on a tub
        # class, read off this derivation's class (the category's included): "WHIRLPOOL
        # FLUSHING WC" is a flush, and two such WCs are classed by their category alone,
        # which the key's `applies_when` gate never overrules. The bare word JACUZZI counts
        # only on a product whose own class is Jacuzzi (the class means "bathtub with
        # massage jets"): "JACUZZI PILLOW" and "JACUZZI PARTS" are filed with the tubs and
        # are no jet (fix round 1, F2).
        "has_massage": flag(
            "MASSAGE",
            "WHIRLPOOL",
            "AIR BUBBLE",
            "JET",
            "JETS",
            "JETTED",
            only_when={"spec": "class", "is": True, "values": list(_TUB_CLASSES)},
        )
        + flag("JACUZZI", only_when={"spec": "class", "is": True, "values": ["Jacuzzi"]}),
        "has_led": flag("LED"),
        "is_honeycomb": flag("HONEYCOMB"),
        "is_soft_close": flag("SOFT CLOSE", "SOFT CLOSING"),
        "has_diverter": flag("DIVERTER"),
        "is_rimless": flag("RIMLESS"),
        "bar_count": words(d.BAR_COUNT_TOKENS),
        "spout_type": words(d.SPOUT_TOKENS),
        "trap_type": words(d.TRAP_TOKENS),
        "flush_type": words(d.FLUSH_TOKENS),
        "shape": words(d.SHAPE_TOKENS),
        # The nouns at the END of the product name, then the two readers that used to
        # run underneath them and appear on no screen. UNDER, not over: 20,697 of
        # 23,063 live products sit in a category that carries a class, so a category
        # rule on top would re-class the catalogue on a filing code (#425, AC-A.1).
        "class": [
            {"kind": "words", "look_in": "name", "words": [token], "at_end": True, "value": value}
            for token, value in d.CLASS_TAIL_TOKENS
        ]
        + [{"kind": "product", "fact": "name"}, {"kind": "product", "fact": "class"}],
        # Words first, then the code suffix as the fallback. Longest first: "MATT
        # BLACK" and "FULL ROSE GOLD" must beat "BLACK" and "GOLD".
        "finish": words(d.FINISH_WORDS)
        + [
            {"kind": "code", "code_match": "ends_with", "texts": [f"-{suffix}"], "value": value}
            for suffix, value in d.FINISH_SUFFIXES.items()
        ],
        # "S-TRAP 300MM" / "S-TRAP:250MM" / "( S- TRAP 250MM )".
        "trap_length": number(after=["S TRAP", "P TRAP"], before=["MM"]),
        # Word forms first, then the digit form: the order the old reader used.
        "bowl_count": [
            {
                "kind": "words",
                "look_in": "any",
                "words": [f"{word} BOWL", f"{word} BOWLS"],
                "value": count,
            }
            for word, count in d.BOWL_WORDS.items()
        ]
        + number(before=["BOWL", "BOWLS"]),
        "is_smart": flag("INTELLIGENT", "AUTO INDUCTION", "SMART TOILET", "SMART WC"),
        # The order the engine has always run: the product master's own column, then the
        # size in its description, then a lone stated size, then the flyer's labelled
        # size ("D: L680xW375xH770mm" - 177 of 756 cards state one).
        "dim_length": [
            column("length"),
            size(1, when_round=False),
            # The one size a row states when it does not state three: "MARBLE TOP BASIN
            # (800MM)". Not below 10, so `CABANA GLASS SHELF 8MM` is not an 8 mm long
            # shelf, and never right after a trap: "(P-TRAP 180MM)" is where the waste
            # leaves, and reading it as the length put a wrong Length on 889 WCs.
            {
                "kind": "number",
                "look_in": "description",
                "before": ["MM"],
                "ignore_below": 10,
                "skip_after": ["S TRAP", "P TRAP"],
                "only_when": only_when_round(False),
            },
            # "BRAVAT SHOWER ARM (LENGTH-200MM)": a length stated in words. The lone size
            # above never reads it, because a number touched by a letter across a hyphen
            # is part of a code (#1286 parity run).
            {
                "kind": "number",
                "look_in": "description",
                "after": ["LENGTH"],
                "before": ["MM"],
                "only_when": only_when_round(False),
            },
            size("L", look_in="flyer"),
        ],
        "dim_width": [column("width"), size(2, when_round=False), size("W", look_in="flyer")],
        "dim_height": [column("height"), size(3, when_round=False), size("H", look_in="flyer")],
        # Round and square products only, where the same three numbers mean something
        # else entirely: 407 across, 120 deep, 10 thick.
        "diameter": [size(1, when_round=True)],
        "depth": [size(2, when_round=True)],
        "thickness": [size(4, when_round=False), size(3, when_round=True)],
        # What the seat cover is made of. Only the flyer says it in words ("*PP Seat
        # Cover"); the code rule sits LAST, which is where it runs: a word beats a code
        # convention, and a code convention beats nothing (#447).
        "seat_material": [
            {"kind": "words", "look_in": "flyer", "words": ["PP ... SEAT"], "value": "pp"},
            {"kind": "words", "look_in": "flyer", "words": ["UF ... SEAT"], "value": "uf"},
            {"kind": "words", "look_in": "any", "words": ["UREA ... SEAT"], "value": "uf"},
            {"kind": "words", "look_in": "any", "words": ["DUROPLAST"], "value": "duroplast"},
            {"kind": "code", "code_match": "contains", "texts": ["-UF"], "value": "uf"},
        ],
        "has_drainer": flag("DRAINER"),
        # 137 write it joined, 5 split ("OVER FLOW"): the split form matches both.
        "has_overflow": flag("OVER FLOW"),
        # "C/W BASIN SCREW" has one; "**W/O SCREW" says the opposite.
        "has_fixing_screw": flag("SCREW", skip_after=["W/O", "WITHOUT"]),
    }
    return {
        key: [
            {"builder": builder_of(rule), SEED_RULE_MARKER: True}
            for rule in fold_rules([{"builder": clean_builder(b)} for b in builders])
        ]
        for key, builders in rules.items()
    }


def seed_derivation_rules(db: Session, *, commit: bool = False) -> dict:
    """Give each key its shipped rules, ONCE. An edited row is never overwritten.

    Same ownership split as everything else calibratable here: the seed puts the
    starting point in, and from then on it belongs to whoever tunes it. A re-seed that
    reinstated the shipped list would delete a rule someone added, on deploy, silently.
    """
    shipped = _rules_from_shipped_tables()
    written = 0
    for row in db.query(ProductSpecRegistry).all():
        if row.derivation_rules is not None:
            continue
        rules = shipped.get(row.spec_key)
        if not rules:
            continue
        row.derivation_rules = rules
        written += 1
    db.flush()
    if commit:
        db.commit()
    return {"keys_seeded": written}


def seed_search_policy(db: Session, *, commit: bool = False) -> dict:
    """Create missing policy rows. An existing row is NEVER overwritten - it is tuning."""
    existing = {row.policy_key for row in db.query(ProductSpecSearchPolicy).all()}
    created = 0
    for entry in SEARCH_POLICY_SEED:
        if entry["policy_key"] in existing:
            continue
        db.add(ProductSpecSearchPolicy(**entry))
        created += 1
    db.flush()
    if commit:
        db.commit()
    return {"created": created}


def search_policy(db: Session) -> dict[str, float]:
    """Every scoring knob as a plain dict. Missing rows fall back to the seed."""
    stored = {row.policy_key: float(row.value) for row in db.query(ProductSpecSearchPolicy).all()}
    return {entry["policy_key"]: stored.get(entry["policy_key"], float(entry["value"]))
            for entry in SEARCH_POLICY_SEED}


def default_match_window(unit: str | None) -> tuple[float, float]:
    """(tolerance, decay) for a unit. Unknown units are exact-or-nothing, never guessed."""
    return _MATCH_DEFAULTS_BY_UNIT.get(unit, (0.0, 0.0))

#: Words a spec value spells in capitals when it is read out ("pp" -> "PP").
SPEC_ACRONYMS = frozenset({"pvc", "abs", "pp", "led", "uv", "ss", "sus"})

#: A stored slug: lowercase words joined by "_" ("cold_only", "s_trap", "pp").
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def display_spec_value(raw: Any, value_labels: dict | None = None, *, title_case: bool = False) -> str:
    """One stored spec value in the words a customer reads (round 4 R7 on PR #833, owner
    console test 27 Sep 2026: "why all the values are snake case? too technical").

    The registry key's own `value_labels` (staff-edited on Product Specifications) wins
    over everything. Otherwise a slug reads in sentence case with its words apart
    ("cold_only" -> "Cold only", "s_trap" -> "S trap", "free_standing" -> "Free
    standing"), an acronym in capitals ("pp" -> "PP"). Text that is not a slug is left as
    stored ("White"), save that no "_" ever survives. `True` / `False` read Yes / No, a
    whole number has no ".0". Every enum value of the seed reads plain through here
    (`test_attribute_asks_round4`), so nothing needs a label typed in to stop a
    snake_case value reaching a reply.

    `title_case=True` is the dealer kit tag's reading ("Stainless Steel"), the one other
    reader: round 5 S2 (reviewer pass at d6fa2b31) folded its copy of this function in
    here, so the two share one acronym list and one set of rules."""
    if isinstance(raw, bool):
        return "Yes" if raw else "No"
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    text = str(raw)
    labels = value_labels or {}
    if text in labels and str(labels[text]).strip():
        return str(labels[text])
    if _SLUG_RE.match(text):
        words = text.split("_")
        shown = [
            w.upper() if w in SPEC_ACRONYMS else (w.capitalize() if n == 0 or title_case else w)
            for n, w in enumerate(words)
        ]
        return " ".join(shown)
    return " ".join(text.replace("_", " ").split())


SPEC_REGISTRY_SEED: list[dict] = [
    {
        "spec_key": "class",
        "label": "Product class",
        "data_type": "enum",
        # Open vocabulary: sourced from product_categories.class_label, which grows.
        "allowed_values": [],
        # What each class means in customer language, matched case-insensitively alongside
        # the class label itself (`product_class_signal.ClassVocabulary`). Chat fix PR-A
        # (AC-A3) moved these words here from the code list `CLASS_SYNONYMS`, word for
        # word, so the parser's `{{specs}}` block and staff on Product Specifications see
        # them; migration `cfs_0001_spec_registry_seed` writes them onto an installed database.
        # Malay terms included because the corpus is bilingual.
        "synonyms": {
            "Kitchen Sink": ["kitchen sink", "sink", "sinki", "dapur"],
            # "water tap" / "water basin": the owner's own words for the two (26 Sep 2026,
            # "which water tap got stock").
            "Tap": ["tap", "faucet", "mixer", "paip", "kepala paip", "water tap"],
            "Wash Basin": ["wash basin", "basin", "washbasin", "sink basin", "besen", "water basin"],
            "Water Closet": ["water closet", "wc", "toilet", "toilet bowl", "closet", "tandas"],
            "Shower": ["shower", "shower head", "rain shower", "hand shower", "pancuran"],
            "Bathroom Furniture": ["cabinet", "mirror", "vanity", "bathroom cabinet", "kabinet"],
            # The two the catalog used to file together. Each answers only to its own
            # words, so "bathtub" stops returning jacuzzis (owner ruling 16 Oct 2026:
            # "Jacuzzi is like bathtub with massage jet").
            "Bathtub": ["bathtub", "bath tub", "tub"],
            "Jacuzzi": ["jacuzzi", "bathtub with massage jets", "whirlpool", "spa bath", "按摩浴缸"],
            # Kept: 182 products are classed from their CATEGORY alone, which names both.
            # They genuinely could be either, and answering to both words is the truthful
            # reading.
            "Bathtub and Jacuzzi": ["bathtub", "bath tub", "jacuzzi", "tub", "shower screen"],
            "Bathroom Accessory": [
                "bathroom accessory",
                "towel rail",
                "towel bar",
                "soap holder",
                "paper holder",
                "robe hook",
            ],
            "Cloth Hanger": ["cloth hanger", "clothes hanger", "hanger", "penyidai"],
            "Urinal": ["urinal", "urinary"],
            "Flexible Hose": ["flexible hose", "hose", "shower tube", "hos"],
        },
        # Total coverage (every product has a category) and the highest-precision
        # signal available, so it carries the largest weight in the ranker.
        "measured_coverage": 11584,
        "rank_weight": 5.0,
    },
    {
        "spec_key": "shape",
        "label": "Shape",
        "data_type": "enum",
        "allowed_values": ["round", "square", "rectangular", "oval"],
        "synonyms": {
            "round": ["round", "circular"],
            "square": ["square"],
            "rectangular": ["rectangular", "rectangle"],
            "oval": ["oval", "ellipse"],
        },
        "measured_coverage": 365,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "diameter",
        "label": "Diameter",
        "data_type": "numeric",
        "synonyms": {"_self": ["diameter", "dia", "across"]},
        "unit": "mm",
        # A rectangular product has no diameter. Ungated this would be proposed for
        # every sink, and the ranker would compare a width against it.
        "applies_when": {"shape": ["round", "square"]},
        "measured_coverage": 365,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "dim_length",
        "label": "Length",
        "data_type": "numeric",
        "synonyms": {"_self": ["length", "long"]},
        "unit": "mm",
        # Deliberately NOT gated on shape: shape is unknown for most rows (no ROUND
        # or SQUARE token in the description), and gating would drop every unlabelled
        # rectangular product.
        "measured_coverage": 3390,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "dim_width",
        "label": "Width",
        "data_type": "numeric",
        "synonyms": {"_self": ["width", "wide"]},
        "unit": "mm",
        "measured_coverage": 3390,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "dim_height",
        "label": "Height",
        "data_type": "numeric",
        "synonyms": {"_self": ["height", "tall", "high"]},
        "unit": "mm",
        "measured_coverage": 3390,
        "rank_weight": 1.5,
    },
    {
        "spec_key": "depth",
        "label": "Depth",
        "data_type": "numeric",
        "synonyms": {"_self": ["depth", "deep"]},
        "unit": "mm",
        "measured_coverage": 365,
        "rank_weight": 1.0,
    },
    {
        "spec_key": "thickness",
        "label": "Thickness",
        "data_type": "numeric",
        "synonyms": {"_self": ["thickness", "thick", "gauge"]},
        "unit": "mm",
        # Only 136 codes carry the 4th dimension, so it is weighted low: a key that is
        # NULL for 99% of rows must not dominate a score.
        "measured_coverage": 136,
        "rank_weight": 1.0,
    },
    {
        "spec_key": "material",
        "label": "Material",
        "data_type": "enum",
        "allowed_values": ["stainless_steel", "ceramic", "glass", "pvc", "brass", "acrylic", "abs", "nanograin", "granite", "marble"],
        "synonyms": {
            "stainless_steel": ["stainless", "stainless steel", "s/steel", "steel", "inox"],
            "ceramic": ["ceramic", "porcelain"],
            "glass": ["glass", "tempered glass"],
            "pvc": ["pvc", "plastic"],
            "abs": ["abs", "abs plastic"],
            "nanograin": ["nanograin", "nano grain", "nano"],
            "granite": ["granite", "granite stone", "quartz"],
            "marble": ["marble", "marble top", "marmar"],
            "brass": ["brass"],
            "acrylic": ["acrylic"],
        },
        "measured_coverage": 2316,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "mounting",
        "label": "Mounting",
        "data_type": "enum",
        "allowed_values": [
            "wall_hung",
            "floor_standing",
            "pedestal",
            "concealed",
            "counter_top",
            "under_counter",
            "pillar_mounted",
            "concealed",
            "counter_top",
            "semi_recessed",
            "exposed",
        ],
        "synonyms": {
            "wall_hung": ["wall hung", "wall mounted", "wall mount", "hang on wall"],
            "floor_standing": ["floor standing", "floor mounted", "free standing"],
            "pedestal": ["pedestal", "with pedestal"],
            "concealed": ["concealed", "hidden", "in wall"],
            "counter_top": ["counter top", "countertop", "above counter", "on counter", "top mount", "table top", "tabletop"],
            "under_counter": ["under counter", "undermount", "under mount", "below counter"],
            # 383 taps say PILLAR MOUNTED and 220 more just say PILLAR. In this catalog
            # the word always describes where the tap is fixed, which is why it moved
            # out of control_type.
            "semi_recessed": ["semi recessed", "semi-recessed", "half recessed"],
            "exposed": ["exposed", "exposed shower", "surface mounted"],
            "pillar_mounted": ["pillar mounted", "pillar mount", "pillar tap", "pillar"],
        },
        "measured_coverage": 3289,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "finish",
        "label": "Finish or colour",
        "data_type": "enum",
        "allowed_values": [
            "black",
            "gunmetal",
            "nickel",
            "grey",
            "rose_gold",
            "chrome",
            "french_gold",
            "white",
            "satin_chrome",
            # 73 flyer cards. Distinct from french_gold in Sorento's own range, and
            # nothing else mapped to it, so a customer asking for it got nothing.
            "golden_yellow",
        ],
        "synonyms": {
            "black": ["black", "matt black", "matte black"],
            "gunmetal": ["gunmetal", "gun metal"],
            "nickel": ["nickel", "brushed nickel"],
            "grey": ["grey", "gray"],
            "rose_gold": ["rose gold", "rosegold"],
            "chrome": ["chrome", "polished chrome"],
            "french_gold": ["french gold", "gold"],
            "white": ["white"],
            "golden_yellow": ["golden yellow", "gold yellow", "yellow gold"],
            "satin_chrome": ["satin chrome", "satin"],
        },
        "measured_coverage": 1600,
        "rank_weight": 1.5,
    },
    {
        "spec_key": "control_type",
        "label": "Control type",
        "data_type": "enum",
        # `mixer`, `pillar` and `bib` moved to product_type / mounting: they name the
        # product or where it is fixed, not how the water is controlled, and holding
        # them here let one product score the same fact twice.
        "allowed_values": ["single_lever", "two_way", "self_closing", "sensor"],
        "synonyms": {
            "single_lever": ["single lever", "single handle", "one lever"],
            "two_way": ["two way", "2 way", "dual"],
            "self_closing": ["self closing", "self-closing", "push", "delay action"],
            "sensor": ["sensor", "automatic", "auto", "touchless"],
        },
        "measured_coverage": 201,
        "rank_weight": 1.5,
    },
    {
        "spec_key": "product_type",
        "label": "Type",
        "data_type": "enum",
        # The noun a customer says inside a class. Sits below `class` in weight because
        # it is narrower and less complete, but above the free-text leg: someone asking
        # for a "bib tap" means something specific that a generic tap does not satisfy.
        "allowed_values": [
            "angle_valve",
            "bib_tap",
            "basin_tap",
            "kitchen_tap",
            "shower_tap",
            "mixer_tap",
            "hand_shower",
            "rain_shower",
            "shower_set",
            "shower_head",
            "close_coupled",
            "one_piece",
            "art_basin",
            "mirror_cabinet",
            # The flyer's own aisles. Each of these was already in the catalog under a
            # class so broad it could not answer for them: "double towel bar" and
            # "paper holder" were both simply Bathroom Accessory.
            "pop_up_waste",
            "towel_bar",
            "towel_shelf",
            "hook_bar",
            "robe_hook",
            "corner_basket",
            "paper_holder",
            "grab_bar",
            "soap_dispenser",
            "flexible_hose",
            "flush_valve",
            "floor_trap",
            "floor_grating",
            "cistern",
            "tumbler",
            "mirror",
            "bidet",
            # Nouns the flyer sells by name that derived to nothing at all.
            "toilet_seat",
            "urinal",
            "toilet_brush",
            "squatting_pan",
            "towel_ring",
            "dustbin",
            "bottle_trap",
            "water_pump",
            "hinge",
            "shower_seat",
            "handrail",
            "drain_pipe",
            "stop_cock",
        ],
        "synonyms": {
            "angle_valve": ["angle valve", "stop valve", "corner valve"],
            "bib_tap": ["bib tap", "hose bib", "bib", "garden tap"],
            "basin_tap": ["basin tap", "basin mixer", "wash basin tap"],
            "kitchen_tap": ["kitchen tap", "sink tap", "kitchen sink tap", "paip dapur"],
            "shower_tap": ["shower tap", "shower mixer"],
            "mixer_tap": ["mixer", "mixer tap", "hot and cold tap"],
            "hand_shower": ["hand shower", "handheld shower", "hand held shower"],
            "rain_shower": ["rain shower", "rainfall shower", "overhead shower"],
            "shower_set": ["shower set", "shower kit", "complete shower"],
            "shower_head": ["shower head", "showerhead"],
            # "close couple" is how a customer actually wrote it (live turn 7c39e638,
            # "close couple wc available stock in p trap"): with only the inflected
            # spellings here, the phrase bound no `product_type` at all, so the answer
            # was every water closet with stock, wall-hung ones included. Migration
            # `spec_vocab_close_couple` re-seeds this row onto an installed DB.
            "close_coupled": [
                "close coupled",
                "close-coupled",
                "close couple",
                "two piece",
                "coupled",
            ],
            "one_piece": ["one piece", "one-piece", "single piece"],
            "art_basin": ["art basin", "vessel basin", "designer basin"],
            "mirror_cabinet": ["mirror cabinet", "cabinet mirror"],
            "pop_up_waste": ["pop up waste", "popup waste", "pop-up waste", "waste cover"],
            "towel_bar": ["towel bar", "towel rack", "towel rail", "penyangkut tuala"],
            "towel_shelf": ["towel shelf", "shelf"],
            "hook_bar": ["hook bar", "hanger bar"],
            "robe_hook": ["robe hook", "hook", "coat hook"],
            "corner_basket": ["corner basket", "corner rack", "corner shelf"],
            "paper_holder": ["paper holder", "toilet roll holder", "tissue holder"],
            "grab_bar": ["grab bar", "handrail", "safety bar"],
            "soap_dispenser": ["soap dispenser", "soap holder", "soap dish"],
            "flexible_hose": ["flexible hose", "flexi hose", "connector hose", "hose"],
            "flush_valve": ["flush valve", "flushing valve"],
            "floor_trap": ["floor trap", "floor waste"],
            "floor_grating": ["floor grating", "grating", "floor drain"],
            "cistern": ["cistern", "flush tank", "water tank"],
            "tumbler": ["tumbler", "toothbrush holder"],
            "mirror": ["mirror", "led mirror", "bathroom mirror"],
            "bidet": ["bidet", "hand bidet", "bidet spray", "shattaf", "jet spray"],
            "toilet_seat": ["toilet seat", "seat cover", "wc seat", "seat and cover"],
            "urinal": ["urinal", "urinal bowl"],
            "toilet_brush": ["toilet brush", "brush holder", "wc brush"],
            "squatting_pan": ["squatting pan", "squat pan", "jamban cangkung"],
            "towel_ring": ["towel ring", "ring towel holder"],
            "dustbin": ["dustbin", "waste bin", "rubbish bin", "tong sampah"],
            "bottle_trap": ["bottle trap", "basin trap"],
            "water_pump": ["water pump", "pressure pump", "booster pump", "pam air"],
            "hinge": ["hinge", "hinges", "cabinet hinge", "door hinge"],
            "shower_seat": ["shower seat", "foldable seat", "bath seat"],
            "handrail": ["handrail", "hand rail", "safety rail", "toilet safety rail"],
            "drain_pipe": ["drain pipe", "waste pipe", "extension pipe"],
            "stop_cock": ["stop cock", "stopcock", "isolating valve"],
        },
        "measured_coverage": 5075,
        "rank_weight": 3.0,
    },
    {
        "spec_key": "water_supply",
        "label": "Water supply",
        "data_type": "enum",
        # Cold-only or hot-and-cold. The first question a salesperson asks about a tap
        # and the commonest phrase on the flyer (878 descriptions say COLD TAP), yet
        # nothing in the registry could express it: a cold tap and a mixer both derived
        # to product_type=basin_tap and scored identically for "basin mixer tap".
        "allowed_values": ["cold_only", "mixer"],
        "synonyms": {
            "cold_only": ["cold tap", "cold only", "single cold", "cold water tap"],
            "mixer": ["mixer", "mixer tap", "hot and cold", "hot cold", "mixer type"],
        },
        "measured_coverage": 1226,
        "rank_weight": 2.5,
    },
    {
        "spec_key": "is_rimless",
        "label": "Rimless",
        "data_type": "boolean",
        # Printed on the flyer as its own selling point and asked for by name. Scoped to
        # the pan classes: "rimless" said of a tap is not a fact about the tap.
        "applies_when": {"class": ["Water Closet", "Urinal", "Squatting Pan"]},
        "synonyms": {"true": ["rimless", "rim less", "no rim"]},
        "measured_coverage": 337,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "bar_count",
        "label": "Number of bars",
        "data_type": "numeric",
        # Single or double towel bar. The same shape as bowl_count, and for the same
        # reason: the flyer prints them as separate products and the customer says the
        # number, so without it "double towel bar" scored a single bar just as highly.
        "synonyms": {
            "_self": ["bar", "bars"],
            "1": ["single towel bar", "single bar", "one bar"],
            "2": ["double towel bar", "double bar", "two bar", "twin bar"],
        },
        "measured_coverage": 264,
        "rank_weight": 2.5,
    },
    {
        "spec_key": "furniture_type",
        "label": "Furniture type",
        "data_type": "enum",
        # 990 cabinets all derived to class "Bathroom Furniture" and nothing else, so a
        # basin cabinet and a mirror cabinet were the same product to the ranker. The
        # flyer names them apart on every card.
        # No mirror_cabinet here: product_type already answers for it, and holding one
        # fact in two keys lets a mirror cabinet score the same thing twice.
        "allowed_values": ["basin_cabinet", "side_cabinet", "tall_cabinet"],
        "synonyms": {
            "basin_cabinet": ["basin cabinet", "vanity", "vanity cabinet", "under basin cabinet"],
            "side_cabinet": ["side cabinet", "storage cabinet"],
            "tall_cabinet": ["tall cabinet", "tall unit", "column cabinet"],
        },
        "measured_coverage": 595,
        "rank_weight": 3.0,
    },
    {
        "spec_key": "is_thermostatic",
        "label": "Thermostatic",
        "data_type": "boolean",
        "synonyms": {"true": ["thermostatic", "temperature control", "constant temperature"]},
        "measured_coverage": 44,
        "rank_weight": 2.5,
    },
    {
        "spec_key": "has_sliding_rail",
        "label": "Sliding rail",
        "data_type": "boolean",
        # "Sliding" in this catalogue always means a height-adjustable shower rail.
        "synonyms": {"true": ["sliding", "sliding bar", "sliding rail", "adjustable rail"]},
        "measured_coverage": 148,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "is_high_basin",
        "label": "High basin",
        "data_type": "boolean",
        "synonyms": {"true": ["high basin", "tall basin", "vessel height", "high rise"]},
        "measured_coverage": 100,
        "rank_weight": 2.5,
    },
    {
        "spec_key": "has_filter",
        "label": "Water filter",
        "data_type": "boolean",
        "synonyms": {"true": ["filter tap", "filter", "with filter", "water filter"]},
        "measured_coverage": 64,
        "rank_weight": 2.5,
    },
    {
        "spec_key": "has_pull_out_shower",
        "label": "Pull-out shower",
        "data_type": "boolean",
        "synonyms": {"true": ["pull out shower", "pull-out shower", "pull out spray"]},
        "measured_coverage": 40,
        "rank_weight": 2.5,
    },
    {
        "spec_key": "has_chopping_board",
        "label": "Chopping board",
        "data_type": "boolean",
        # What the flyer sells the multifunction sinks on.
        "synonyms": {"true": ["chopping board", "cutting board", "with board"]},
        "measured_coverage": 36,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "has_dish_rack",
        "label": "Dish rack",
        "data_type": "boolean",
        "synonyms": {"true": ["dish rack", "drying rack", "with rack"]},
        "measured_coverage": 38,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "no_overflow",
        "label": "Without overflow",
        "data_type": "boolean",
        # The opposite of has_overflow, and stated on its own: a basin sold WITHOUT one
        # is a different product, not a product missing a fact.
        "synonyms": {"true": ["without overflow", "no overflow", "w/o overflow"]},
        "measured_coverage": 40,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "has_shower_union",
        "label": "Shower union",
        "data_type": "boolean",
        "synonyms": {"true": ["shower union", "union", "wall union"]},
        "measured_coverage": 54,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "steel_grade",
        "label": "Steel grade",
        "data_type": "enum",
        # 304 vs 201 is the first thing a kitchen-sink buyer asks and the biggest price
        # difference on the page. 705 descriptions say 304, 137 say 201, and the flyer
        # prints "S/Steel 304" 218 times - it was simply not askable before.
        "allowed_values": ["304", "201"],
        "synonyms": {
            "304": ["304", "sus304", "grade 304", "s/steel 304", "stainless steel 304"],
            "201": ["201", "sus201", "grade 201", "s/steel 201"],
        },
        "measured_coverage": 842,
        "rank_weight": 2.5,
    },
    {
        "spec_key": "spray_functions",
        "label": "Spray functions",
        "data_type": "numeric",
        # "3 function shower" is how the flyer sells it (36 cards) and how a customer
        # asks. A count, so exact-or-nothing - a 2-function head does not nearly satisfy
        # someone who asked for 3.
        "synonyms": {
            "_self": ["function", "functions", "spray", "mode", "modes", "setting"],
            "1": ["single function", "1 function", "one function"],
            "2": ["2 function", "two function", "double function"],
            "3": ["3 function", "three function", "triple function"],
            "5": ["5 function", "five function"],
        },
        "measured_coverage": 349,
        "rank_weight": 2.5,
    },
    {
        "spec_key": "hose_length",
        "label": "Hose length",
        "data_type": "numeric",
        "unit": "mm",
        # The hand-bidet hose the flyer prints as "c/w 1.2m" on 41 cards. Customers ask
        # because it decides whether the spray reaches, and 189 descriptions carry it.
        "synonyms": {"_self": ["hose", "hose length", "pipe", "tube"]},
        "measured_coverage": 237,
        "rank_weight": 2.0,
        # Millimetres, but a hose is not a cabinet: 100mm either side is what "about
        # 1.2m" means to a customer, and nothing beyond half a metre is the same hose.
        "match_tolerance": 100.0,
        "match_decay": 500.0,
    },
    {
        "spec_key": "way_count",
        "label": "Ways (diverter outlets)",
        "data_type": "numeric",
        # How many outlets the diverter feeds. Deliberately separate from
        # spray_functions: a 2-way set can carry a 3-function hand shower, and the
        # flyer prints both on the same card.
        "synonyms": {"_self": ["way", "ways", "outlet", "outlets", "diverter way"]},
        "measured_coverage": 222,
        "rank_weight": 3.0,
        "match_tolerance": 0.0,
        "match_decay": 1.0,
    },
    {
        "spec_key": "piece_count",
        "label": "Pieces in the set",
        "data_type": "numeric",
        # A 4-in-1 and a 3-in-1 furniture set can quote the same 580x460x400; this is
        # the only thing that tells them apart.
        "synonyms": {"_self": ["in 1", "piece set", "pieces", "set of"]},
        "measured_coverage": 300,
        "rank_weight": 3.0,
        "match_tolerance": 0.0,
        "match_decay": 1.0,
    },
    {
        "spec_key": "capacity_oz",
        "label": "Capacity (oz)",
        "data_type": "numeric",
        "unit": "oz",
        "synonyms": {"_self": ["oz", "ounce", "ounces"]},
        "measured_coverage": 3,
        "rank_weight": 2.0,
        "match_tolerance": 1.0,
        "match_decay": 10.0,
    },
    {
        "spec_key": "capacity_litre",
        "label": "Capacity",
        "data_type": "numeric",
        "unit": "L",
        # The flyer's page 16 sells dustbins as 8 litre and 12 litre and nothing read it.
        "synonyms": {"_self": ["litre", "liter", "capacity", "volume", "size in litre"]},
        "measured_coverage": 74,
        "rank_weight": 2.5,
        # A litre either side is the same bin to a customer; five litres is not.
        "match_tolerance": 1.0,
        "match_decay": 5.0,
    },
    {
        "spec_key": "power_hp",
        "label": "Power",
        "data_type": "numeric",
        "unit": "hp",
        "synonyms": {"_self": ["horsepower", "hp", "power", "motor"]},
        "measured_coverage": 48,
        "rank_weight": 2.5,
        "match_tolerance": 0.1,
        "match_decay": 0.5,
    },
    {
        "spec_key": "is_frameless",
        "label": "Frameless",
        "data_type": "boolean",
        # The clearest case for reading the flyer over the description: 14 flyer cards
        # say FRAMELESS and only 2 descriptions do.
        "synonyms": {"true": ["frameless", "frame less", "no frame", "borderless"]},
        "measured_coverage": 16,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "has_massage",
        "label": "Massage jets",
        "data_type": "boolean",
        # Chat fix PR-A (owner ruling 16 Oct 2026: "Jacuzzi is like bathtub with massage
        # jet"): "any bathtub with massage jet" finds every jetted tub, a Jacuzzi-class one
        # or a bathtub whose text says MASSAGE / AIR BUBBLE, and a plain bathtub ask stays
        # plain bathtubs. 191 tub codes on the dev copy (16 Oct 2026) name the jets.
        "applies_when": {"class": list(_TUB_CLASSES)},
        "synonyms": {"true": ["massage", "massage jet", "massage jets", "with massage", "jetted", "air bubble"]},
        "measured_coverage": 191,
        "rank_weight": 2.5,
    },
    {
        "spec_key": "has_led",
        "label": "LED light",
        "data_type": "boolean",
        "synonyms": {"true": ["led", "led light", "lighted", "illuminated", "with light"]},
        # 84, NOT the 1,148 a `LIKE '%LED%'` first reported: that substring also matches
        # CONCEALED and CLOSE-COUPLED. Every count in this file is a word-boundary match.
        "measured_coverage": 84,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "is_honeycomb",
        "label": "Honeycomb structure",
        "data_type": "boolean",
        # The flyer gives page 7's bathroom furniture its own name and sells on it; 154
        # descriptions carry it.
        "synonyms": {"true": ["honeycomb", "honey comb", "honeycomb structure"]},
        "measured_coverage": 154,
        "rank_weight": 2.5,
    },
    {
        "spec_key": "is_soft_close",
        "label": "Soft close",
        "data_type": "boolean",
        "synonyms": {"true": ["soft close", "soft closing", "slow close", "damper"]},
        "measured_coverage": 8,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "has_diverter",
        "label": "Diverter",
        "data_type": "boolean",
        "synonyms": {"true": ["diverter", "with diverter", "divertor"]},
        "measured_coverage": 37,
        "rank_weight": 1.5,
    },
    {
        "spec_key": "spout_type",
        "label": "Spout",
        "data_type": "enum",
        "allowed_values": ["flexible", "double_flexible", "pull_out", "swivel", "gooseneck", "waterfall"],
        "synonyms": {
            "flexible": ["flexible", "flexible head", "flexi", "bendable", "hose spout"],
            "double_flexible": ["double flexible", "double spout"],
            "pull_out": ["pull out", "pull-out", "extendable", "pull down"],
            "swivel": ["swivel", "rotating", "turnable"],
            "gooseneck": ["gooseneck", "goose neck", "high arc"],
            "waterfall": ["waterfall", "water fall", "cascade"],
        },
        "measured_coverage": 641,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "trap_type",
        "label": "Trap",
        "data_type": "enum",
        # The one spec where being wrong means a toilet that cannot be installed: the
        # outlet is either in the floor (S) or the wall (P). Read ONLY where the
        # description says so; the `-P` code-suffix rule needs class gating and is not
        # in this release.
        "allowed_values": ["s_trap", "p_trap"],
        "synonyms": {
            "s_trap": ["s trap", "s-trap", "floor outlet", "floor waste"],
            "p_trap": ["p trap", "p-trap", "wall outlet", "wall waste"],
        },
        "measured_coverage": 1027,
        "rank_weight": 3.0,
    },
    {
        "spec_key": "trap_length",
        "label": "Trap outlet length",
        "data_type": "numeric",
        "synonyms": {"_self": ["trap"]},
        "unit": "mm",
        # Independent of trap_type: "150mm S-trap" and "300mm S-trap" are different
        # products a customer must not be shown interchangeably. The catalog spells the
        # separator three ways ("S-TRAP 300MM", "S-TRAP:250MM", "S- TRAP 250MM").
        "measured_coverage": 738,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "flush_type",
        "label": "Flush type",
        "data_type": "enum",
        "allowed_values": ["washdown", "siphonic", "twister"],
        "synonyms": {
            "washdown": ["washdown", "wash down", "wash-down"],
            "siphonic": ["siphonic", "syphonic"],
            # TWISTER is Sorento's own branded flush name, not generic language, but a
            # customer repeating a salesperson's or a poster's term still deserves it.
            "twister": ["twister flush", "twister flushing", "twister"],
        },
        "applies_when": {"class": ["Water Closet"]},
        "measured_coverage": 622,
        "rank_weight": 2.0,
    },
    {
        "spec_key": "bowl_count",
        "label": "Number of bowls",
        "data_type": "numeric",
        "applies_when": {"class": ["Kitchen Sink"]},
        # A numeric key with synonyms, because customers say the number in words.
        # Without these "double bowl kitchen sink" only earned a substring hit on the
        # rendered sentence, so a SINGLE bowl sink scored the same and was never
        # demoted - the one distinction the customer actually cared about.
        "synonyms": {
            "_self": ["bowl", "bowls"],
            "1": ["single bowl", "one bowl", "1 bowl", "single sink"],
            "2": ["double bowl", "two bowl", "2 bowl", "twin bowl", "double sink"],
            "3": ["triple bowl", "three bowl", "3 bowl"],
        },
        # 106 of 1,148 kitchen sinks state this. Low coverage is not low value: someone
        # asking for a double bowl will reject every single bowl, so where the catalog
        # says it, it decides the answer. Weighted accordingly, and NULL elsewhere.
        "measured_coverage": 106,
        "rank_weight": 3.0,
        # A count, so any number above this is a model number, not bowls: "MOCHA GLASS
        # BASIN (650x420MM) 6086 BOWL ONLY" read 6086 bowls (#1286 parity run). Flagged
        # and dropped like an implausible size, and editable like every cap.
        "max_value": 9,
    },
    {
        "spec_key": "seat_material",
        "label": "Seat cover material",
        "data_type": "enum",
        "allowed_values": ["pp", "uf", "duroplast"],
        # Only the flyer states it, on 25 cards (PP 13, UF 12). Low coverage, high
        # decisiveness: someone asking for a UF seat is refusing a PP one, so where the
        # catalog says it, it settles the answer.
        "synonyms": {
            "pp": ["pp", "pp seat", "pp seat cover", "polypropylene", "plastic seat"],
            "uf": ["uf", "uf seat", "uf seat cover", "urea", "urea formaldehyde"],
            "duroplast": ["duroplast", "duroplast seat", "soft close seat"],
        },
        "applies_when": {"class": ["Water Closet"]},
        "measured_coverage": 25,
        "rank_weight": 2.5,
    },
    {
        "spec_key": "has_drainer",
        "label": "Has a drainer board",
        "data_type": "boolean",
        "applies_when": {"class": ["Kitchen Sink"]},
        "synonyms": {"true": ["drainer", "with drainer", "drainer board", "drain board"]},
        "measured_coverage": 18,
        "rank_weight": 1.5,
    },
    {
        "spec_key": "has_overflow",
        "label": "Has an overflow",
        "data_type": "boolean",
        "synonyms": {"true": ["overflow", "with overflow", "c/w overflow", "overflow hole"]},
        # 137 write it joined, 5 split ("OVER FLOW") - both read by the same rule.
        "measured_coverage": 142,
        "rank_weight": 1.5,
    },
    {
        "spec_key": "has_fixing_screw",
        "label": "Comes with a fixing screw",
        "data_type": "boolean",
        "synonyms": {"true": ["basin screw", "fixing screw", "c/w screw", "with screw"]},
        "measured_coverage": 29,
        "rank_weight": 1.0,
    },
    {
        "spec_key": "is_smart",
        "label": "Intelligent / smart toilet",
        "data_type": "boolean",
        "applies_when": {"class": ["Water Closet"]},
        "synonyms": {
            "true": ["intelligent toilet", "intelligent", "smart toilet", "smart wc", "auto induction"]
        },
        # Rare (76 of 1,655 WCs) but decisive: a customer asking for one rejects every
        # ordinary WC, so where the catalog states it, it should dominate the result.
        "measured_coverage": 76,
        "rank_weight": 3.0,
    },
]

PILOT_SPEC_KEYS: tuple[str, ...] = tuple(entry["spec_key"] for entry in SPEC_REGISTRY_SEED)


def _seed_values(entry: dict) -> dict:
    """Normalise a seed entry into the seed-owned column values."""
    return {
        "label": entry["label"],
        "data_type": entry["data_type"],
        "unit": entry.get("unit"),
        "allowed_values": entry.get("allowed_values", []),
        "synonyms": entry.get("synonyms", {}),
        "applies_when": entry.get("applies_when", {}),
        "measured_coverage": entry.get("measured_coverage"),
    }


def seed_spec_registry(db: Session, *, commit: bool = False) -> dict:
    """Create missing registry rows and repair drifted vocabulary on existing ones.

    Idempotent: a re-seed with nothing to fix writes nothing. Runs on every deploy as
    the key set grows, so it must never clobber a hand-tuned `rank_weight` or a
    hand-flipped `is_active` - those are the reviewer's, not the seed's.
    """
    created = 0
    updated = 0

    existing = {row.spec_key: row for row in db.query(ProductSpecRegistry).all()}

    for entry in SPEC_REGISTRY_SEED:
        key = entry["spec_key"]
        values = _seed_values(entry)
        row = existing.get(key)

        if row is None:
            # Calibration is set ONCE, at creation, then belongs to whoever tunes it.
            # An explicit value in the seed entry wins over the unit default, so a key
            # that needs an unusual window can say so without inventing a new unit.
            tolerance, decay = default_match_window(entry.get("unit"))
            db.add(
                ProductSpecRegistry(
                    spec_key=key,
                    rank_weight=entry.get("rank_weight", 1.0),
                    excluded_values=entry.get("excluded_values", []),
                    is_active=entry.get("is_active", True),
                    # Same bargain as the match window: set from the unit at creation,
                    # then owned by whoever tunes it. A millimetre key gets the cap that
                    # used to be a constant in the engine; everything else gets none,
                    # because a bowl count and a horsepower have no such number.
                    max_value=entry.get(
                        "max_value",
                        DEFAULT_MM_MAX_VALUE if entry.get("unit") == "mm" else None,
                    ),
                    match_tolerance=entry.get("match_tolerance", tolerance),
                    match_decay=entry.get("match_decay", decay),
                    **values,
                )
            )
            created += 1
            continue

        # A row a human took ownership of is never repaired. The anti-drift guarantee
        # only applies to vocabulary this file shipped; overwriting someone's edit on
        # every deploy would make the UI a lie.
        if (row.source or "seed") != "seed":
            continue

        changed = False
        for field, value in values.items():
            # A label the owner edited stays theirs; every other seed column is repaired.
            if field == "label" and row.label_owned:
                continue
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True

        # Shipped rules reach an install that already has rules, without undoing a
        # person's order.
        #
        # Without this, shipped vocabulary could never reach an install again: the
        # column is preferred over the shipped table (`configured_rules`), so adding 17
        # product types to the table moved the value list and changed nothing about what
        # gets derived - the screen listed `bidet` and not one of 715 bidets carried it.
        #
        # A rule is the seed's when its builder is one the seed ships (its identity),
        # marker or not: a person's save drops `_seed`, and a shipped rule saved
        # unchanged is still the shipped rule. When the stored list already holds every
        # shipped rule and no rule the seed placed that it no longer ships, nothing
        # shipped changed and the list is left EXACTLY as stored - order included,
        # because order is priority and a person may have moved a row. Otherwise the
        # person's own rules go first and the shipped list follows in shipped order, so
        # a corrected shipped rule replaces the broken one instead of landing beside it.
        shipped_for_key = _rules_from_shipped_tables().get(key) or []
        stored_rules = list(row.derivation_rules or [])
        if shipped_for_key and stored_rules:
            wanted = {_rule_identity(r) for r in shipped_for_key}
            stored_ids = {_rule_identity(r) for r in stored_rules}
            retired = [
                r
                for r in stored_rules
                if r.get(SEED_RULE_MARKER) and _rule_identity(r) not in wanted
            ]
            if not (wanted <= stored_ids and not retired):
                human = [
                    r
                    for r in stored_rules
                    if not r.get(SEED_RULE_MARKER) and _rule_identity(r) not in wanted
                ]
                merged = human + list(shipped_for_key)
                if merged != stored_rules:
                    row.derivation_rules = merged
                    changed = True

        if changed:
            updated += 1

    db.flush()
    if commit:
        db.commit()

    return {"created": created, "updated": updated}


# --------------------------------------------------------------------------- #
# removing one rule, one choice or one word (#1286, D7 / D13: deferred removes)
#
# Each is a record action (`record_actions`: `spec_rule.remove`, `spec_value.remove`,
# `spec_word.remove`) that commits when its 5 s window lapses. They make exactly the
# change the PATCH fields make, one item at a time, so a removal parked while somebody
# else edits the same key cannot write back a list it read before their edit.
# --------------------------------------------------------------------------- #
def _registry_row_for_update(db: Session, spec_key: str) -> ProductSpecRegistry:
    from app.services.error_handler import handle_not_found

    row = (
        db.query(ProductSpecRegistry)
        .filter(ProductSpecRegistry.spec_key == spec_key)
        .with_for_update()
        .first()
    )
    if row is None:
        raise handle_not_found("Spec key", spec_key)
    return row


def _gone(message: str):
    from app.services.error_handler import AppException

    return AppException(status_code=404, message=message, code="spec_registry_item_gone")


# How long a deferred remove waits for another catalogue read to finish before it gives
# up (review S-8). Its window has lapsed and the tab may be closed, so refusing at once
# would void it with nobody told; a save or a preview takes a few seconds.
REMOVE_WAIT_SECONDS = 30


def _wait_for_catalogue_read(db: Session) -> str:
    """The catalogue-read slot for a remove, waited for rather than refused."""
    import time

    from app.services import product_spec_preview
    from app.services.error_handler import AppException

    deadline = time.monotonic() + REMOVE_WAIT_SECONDS
    while True:
        try:
            return product_spec_preview.begin_catalogue_read(db)
        except AppException as exc:
            if exc.status_code != 409 or time.monotonic() >= deadline:
                raise
        time.sleep(0.25)


def remove_rule(db: Session, spec_key: str, builder: dict) -> dict:
    """Remove the first rule that reads what `builder` says, then re-read what changed.

    Compared after the stored form's normalisation (`clean_builder`), so the rule the
    screen shows and the rule stored are the same rule however the screen spelled its
    words. A key still reading the shipped rules has them written to its column first:
    removing one is the moment the list becomes the business's own.
    """
    from app.services import product_spec_preview

    # The one catalogue-read slot a save and a preview share (security review S1): a
    # remove never piles a second catalogue read onto a running one. It waits its turn
    # (review S-8), and fails only when the other read outlasts REMOVE_WAIT_SECONDS.
    # Taken before anything changes.
    token = _wait_for_catalogue_read(db)
    try:
        return _remove_rule(db, spec_key, builder)
    finally:
        product_spec_preview.end_catalogue_read(token)


def _remove_rule(db: Session, spec_key: str, builder: dict) -> dict:
    from app.services import product_spec_rederive
    from app.services.product_spec_derivation import stored_or_shipped_rules
    from app.services.product_spec_rules import builder_identity

    row = _registry_row_for_update(db, spec_key)
    wanted = builder_identity(clean_builder(builder if isinstance(builder, dict) else {}))
    rules = [
        {"builder": builder_of(rule)}
        for rule in stored_or_shipped_rules(row)
        if builder_of(rule)
    ]
    index = next(
        (i for i, rule in enumerate(rules) if builder_identity(clean_builder(rule["builder"])) == wanted),
        None,
    )
    if index is None:
        raise _gone("That rule is no longer on this specification.")

    fingerprint_before = product_spec_rederive.rules_fingerprint(db)
    # Stored even when it empties the list: [] is "no rules", never "use the shipped
    # rules" (review B-1).
    row.derivation_rules = rules[:index] + rules[index + 1 :]
    db.commit()
    updated = product_spec_rederive.reread_after_save_logged(
        db, spec_key, fingerprint_before=fingerprint_before
    )
    return {"spec_key": spec_key, "products_updated": updated}


def remove_value(db: Session, spec_key: str, value: str) -> dict:
    """Take one choice off a key: a staff-added one is dropped with its words and its
    label; a shipped one is suppressed (the same effect as the PATCH's `user_values` and
    `suppressed_values`), because the shipped list is the parser's contract.

    A suppressed choice stops being produced (`configured_rules`), so the products that
    hold it are read again straight away, the same as a rule save (review S-10).
    """
    from app.services import product_spec_preview

    token = _wait_for_catalogue_read(db)
    try:
        return _remove_value(db, spec_key, value)
    finally:
        product_spec_preview.end_catalogue_read(token)


def _remove_value(db: Session, spec_key: str, value: str) -> dict:
    from app.services import product_spec_rederive

    row = _registry_row_for_update(db, spec_key)
    value = str(value or "").strip()
    added = [str(v) for v in (row.user_values or [])]
    shipped = [str(v) for v in (row.allowed_values or [])]

    if value in added:
        row.user_values = [v for v in added if v != value]
        row.user_synonyms = {k: w for k, w in (row.user_synonyms or {}).items() if k != value}
        row.value_labels = {k: w for k, w in (row.value_labels or {}).items() if k != value}
        # A rule setting a choice that is gone would keep writing it, and the next
        # rules save would refuse it (review N-R4). Only a stored list can hold one:
        # the shipped rules set shipped choices only.
        rules = list(row.derivation_rules or [])
        kept = [r for r in rules if str(builder_of(r).get("value", "")).strip() != value]
        if len(kept) != len(rules):
            fingerprint_before = product_spec_rederive.rules_fingerprint(db)
            row.derivation_rules = kept
            db.commit()
            updated = product_spec_rederive.reread_after_save_logged(
                db, spec_key, fingerprint_before=fingerprint_before
            )
            return {"spec_key": spec_key, "value": value, "products_updated": updated}
    elif value in shipped:
        suppressed = [str(v) for v in (row.suppressed_values or [])]
        if value not in suppressed:
            fingerprint_before = product_spec_rederive.rules_fingerprint(db)
            row.suppressed_values = [*suppressed, value]
            db.commit()
            updated = product_spec_rederive.reread_after_save_logged(
                db, spec_key, fingerprint_before=fingerprint_before
            )
            return {"spec_key": spec_key, "value": value, "products_updated": updated}
    else:
        raise _gone("That choice is no longer on this specification.")

    db.commit()
    return {"spec_key": spec_key, "value": value, "products_updated": 0}


def remove_word(db: Session, spec_key: str, value: str, word: str) -> dict:
    """Take one word off a choice (or off the key itself, `_self`): a staff-added word is
    dropped, a shipped one is suppressed (the PATCH's `user_synonyms` and
    `suppressed_synonyms`). Compared the way the words match: case and spacing folded."""
    row = _registry_row_for_update(db, spec_key)
    value = str(value or "").strip()
    folded = normalise_vocabulary(word)
    added = list((row.user_synonyms or {}).get(value) or [])
    shipped = list((row.synonyms or {}).get(value) or [])

    if any(normalise_vocabulary(w) == folded for w in added):
        kept = [w for w in added if normalise_vocabulary(w) != folded]
        words = {k: list(w) for k, w in (row.user_synonyms or {}).items() if k != value}
        if kept:
            words[value] = kept
        row.user_synonyms = words
    elif any(normalise_vocabulary(w) == folded for w in shipped):
        suppressed = {k: list(w) for k, w in (row.suppressed_synonyms or {}).items()}
        taken = suppressed.get(value) or []
        if not any(normalise_vocabulary(w) == folded for w in taken):
            suppressed[value] = [*taken, next(w for w in shipped if normalise_vocabulary(w) == folded)]
        row.suppressed_synonyms = suppressed
    else:
        raise _gone("That word is no longer on this choice.")

    db.commit()
    return {"spec_key": spec_key, "value": value, "word": word}


def delete_registry_key(db: Session, spec_key: str) -> None:
    """Delete a user-created key. Seeded keys are deactivated, never deleted.

    A seeded key would simply reappear on the next deploy, so offering "delete" for one
    would be a button that silently does nothing. Shared by `DELETE /spec-registry/
    {spec_key}` and the `spec_key.delete` record action (D.6, PLAN-spec-workbench-
    redesign.md) - one refusal, not two copies of it drifting apart.
    """
    from app.services.error_handler import AppException, handle_not_found

    row = db.query(ProductSpecRegistry).filter_by(spec_key=spec_key).first()
    if row is None:
        raise handle_not_found("Spec key", spec_key)
    if (row.source or "seed") == "seed":
        raise AppException(
            status_code=400,
            message=(
                "This key ships with the product and would come back on the next "
                "deploy. Switch it off instead."
            ),
            code="spec_registry_seed_undeletable",
        )
    db.delete(row)
    db.commit()


def merged_allowed_values(row: ProductSpecRegistry) -> list:
    """The shipped values plus the ones staff added, minus the ones they took away.

    Suppression is applied LAST and matches on the exact stored value, so taking a
    shipped value away and adding one back under the same name both work, in either
    order - the same bargain `merged_synonyms` strikes for words.
    """
    merged = list(row.allowed_values or [])
    for value in row.user_values or []:
        if value not in merged:
            merged.append(value)
    dropped = {str(v).strip() for v in (row.suppressed_values or [])}
    return [v for v in merged if str(v).strip() not in dropped]


def value_for_registry(row: ProductSpecRegistry, raw: Any, reject) -> Any:
    """One value, forced into the shape the registry describes, or refused.

    Every write path calls this and none keeps its own copy: a value a reviewer accepts
    off a pasted flyer card (the batch route), the same value typed into the same field
    (the PUT), and the same value ticked out of a whole flyer's proposals (the bulk
    apply) are one claim about the product. Two copies of the coercion would mean one
    surface could store a word another refuses - an out-of-vocabulary enum, a
    "measurement" that is not a number - and the vocabulary is the whole reason the
    registry is shared with the parser and the ranker.

    It lives HERE rather than in a route module because a service (the flyer ingest)
    is now one of the callers, and a service reaching into `app/api` for a helper is
    the wrong direction. `app/api/v1/master_data/product_specifications.py` keeps a
    one-line alias, so its two existing callers do not change.

    `reject` builds the refusal, because the callers are refusing different things: a
    value typed into one field is a bad business state (400), and a body naming a value
    the registry cannot accept is the wrong shape for the call (422). The blank case
    answers 400 either way - it is the write choke point's own refusal
    (`product_spec_write._prepare`), reproduced here only so the message can name the
    key's label instead of its slug.
    """
    from app.services.error_handler import AppException

    def _blank():
        # An empty value is not a value, it is a removal wearing one. Stored, it
        # canonicalises to nothing while derivation keeps producing something, so the
        # merge would raise the same conflict on every run forever - in a table whose
        # contract is exceptions only.
        return AppException(
            status_code=400,
            message=(
                f"{row.label} cannot be blank. To take the value away, remove the "
                f"specification instead."
            ),
            code="product_spec_bad_value",
        )

    if isinstance(raw, (list, tuple)):
        # A product can genuinely carry two of these at once: SRTWT9605-RG is "Rose
        # Gold + Matt Black", and derivation stores both. So the list is coerced
        # element-wise and KEPT as a list, or accepting a proposal would write a
        # different shape from the one a re-derivation of the same words produces.
        from app.services.product_spec_derivation import MULTI_VALUE_KEYS

        if row.spec_key not in MULTI_VALUE_KEYS:
            raise reject(f"{row.label} holds one value, not several.")
        items = [value_for_registry(row, item, reject) for item in raw]
        if not items:
            raise _blank()
        # One tone is stored as the tone, exactly as `apply_rules` does it: a
        # one-element list and the value itself must not be two different answers.
        return items[0] if len(items) == 1 else items

    data_type = (row.data_type or "").lower()
    if data_type == "boolean":
        return str(raw).strip().lower() in {"true", "yes", "1"}

    if data_type == "numeric":
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise reject(f"{row.label} is a measurement, so it needs a number.")
        return int(number) if number.is_integer() else number

    value = str(raw).strip()
    if not value:
        raise _blank()

    allowed = merged_allowed_values(row)
    if allowed and value not in allowed:
        raise reject(
            f"{row.label} does not have a value called \"{value}\". "
            f"Add it to the specification first, or pick one of: {', '.join(allowed)}."
        )
    return value


def merged_synonyms(row: ProductSpecRegistry) -> dict:
    """Seed synonyms with the staff-added ones folded in, per value.

    Additions are additive: a word added here can never remove one the n8n parser is
    relying on. Removals are explicit and separate - `suppressed_synonyms` says "this
    business does not use that word for that value", which is what "matte black" needed:
    it ships as a word for `black`, and while it is bound there it cannot mean a colour
    of its own however many values you add.

    Suppression is applied LAST, so suppressing a word the seed ships and adding it back
    under another value both work, in either order.

    A SUPPRESSED VALUE publishes no words. Its spellings stay STORED - a suppressed value
    is still in `allowed_values`, and keeping them is what makes putting it back one click
    rather than a retyping exercise - but nothing reads them while it is withdrawn.

    Unpublished matters because every reader takes this map: `find_similar_value` would
    refuse a proposal by naming the suppressed value, the product dropdown would say
    "pick it above" when it is not above, and `GET /spec-registry` would advertise words
    for a value the same response reports as not allowed - in the one vocabulary the
    ranker and the n8n parser share.
    """
    merged = {value: list(words) for value, words in (row.synonyms or {}).items()}
    for value, words in (row.user_synonyms or {}).items():
        existing = merged.setdefault(value, [])
        for word in words:
            if word not in existing:
                existing.append(word)
    for value, words in (row.suppressed_synonyms or {}).items():
        if value not in merged:
            continue
        dropped = {str(w).strip().lower() for w in words}
        merged[value] = [w for w in merged[value] if str(w).strip().lower() not in dropped]
    silenced = {str(v).strip() for v in (row.suppressed_values or [])}
    return {value: words for value, words in merged.items() if str(value).strip() not in silenced}


CLASS_KEY = "class"


def shipped_class_synonyms() -> dict[str, list[str]]:
    """The `class` key's words per class as the seed ships them, for a caller with no
    registry row (`class_synonyms`)."""
    entry = next(e for e in SPEC_REGISTRY_SEED if e["spec_key"] == CLASS_KEY)
    return {label: list(words) for label, words in entry["synonyms"].items()}


def class_synonyms(db: Session) -> dict[str, list[str]]:
    """The words each class answers to, as configured: the registry's `class` row (seed
    words, staff's added, suppressed ones taken away). A database with no `class` row yet
    takes the shipped seed, the same bargain `configured_scopes` strikes."""
    row = db.query(ProductSpecRegistry).filter_by(spec_key=CLASS_KEY).first()
    if row is None:
        return shipped_class_synonyms()
    return merged_synonyms(row)


def shipped_scopes() -> dict[str, dict]:
    """`applies_when` as the seed declares it, for a caller with no database."""
    return {
        entry["spec_key"]: entry["applies_when"]
        for entry in SPEC_REGISTRY_SEED
        if entry.get("applies_when")
    }


def shipped_max_values() -> dict[str, float]:
    """The plausibility cap each key ships with, for a caller with no database.

    Every millimetre key, plus a key whose seed entry names its own `max_value` (the
    bowl count). A capacity in litres and a horsepower have no such number, and
    inventing one for them would drop real values.
    """
    caps: dict[str, float] = {}
    for entry in SPEC_REGISTRY_SEED:
        if entry.get("max_value") is not None:
            caps[entry["spec_key"]] = float(entry["max_value"])
        elif entry.get("unit") == "mm":
            caps[entry["spec_key"]] = float(DEFAULT_MM_MAX_VALUE)
    return caps


def numeric_product_columns() -> set[str]:
    """The `Product` columns a `from_field column:<name>` rule may legally name.

    Computed from the model rather than hand-listed (B3): `from_field
    column:currency` crashed derivation for the whole catalogue because nothing
    checked the column existed, let alone that it held a number - `_number(str(raw))`
    is an unguarded `float()`. A column added to `Product` later is reachable the
    moment it exists, with no second list to remember to update.
    """
    from sqlalchemy import Float, Integer, Numeric

    from app.models.product import Product

    return {
        column.name
        for column in Product.__table__.columns
        if isinstance(column.type, (Numeric, Integer, Float))
    }


def configured_max_values(db: Session) -> dict[str, float]:
    """Each key's cap as configured. Blank on a row means NO cap, and says so.

    A row that exists answers for itself: someone who cleared the field meant "stop
    dropping numbers on this key", and falling through to the shipped 5000 would ignore
    them. Only a key with no row at all takes the shipped value.
    """
    caps = shipped_max_values()
    for row in db.query(ProductSpecRegistry).all():
        if row.max_value is None:
            caps.pop(row.spec_key, None)
        else:
            caps[row.spec_key] = float(row.max_value)
    return caps


def configured_scopes(db: Session) -> dict[str, dict]:
    """Which products each key applies to, as configured.

    Reads EVERY row for the same reason `configured_rules` does: `is_active` says
    whether the key is searched, not which products may legitimately carry it.
    """
    scopes = shipped_scopes()
    for row in db.query(ProductSpecRegistry).all():
        # An explicit empty gate is a real answer - "this applies to everything" - so it
        # overwrites the seed's rather than falling through to it.
        scopes[row.spec_key] = row.applies_when or {}
    return {key: gate for key, gate in scopes.items() if gate}


def active_registry(db: Session) -> list[ProductSpecRegistry]:
    """Active keys only, ordered stably so a cached render is byte-comparable.

    Returns live ORM rows, deliberately unmodified: callers that need the customer
    vocabulary must go through `merged_synonyms(row)`. Folding the merge in here would
    mean mutating a tracked object on a read path, where a single stray autoflush would
    write the merged result back over the seed-owned column and quietly destroy the
    distinction this table just gained.
    """
    return (
        db.query(ProductSpecRegistry)
        .filter(ProductSpecRegistry.is_active.is_(True))
        .order_by(ProductSpecRegistry.spec_key)
        .all()
    )


# --------------------------------------------------------------------------- #
# Which keys a product MAY carry, and whether a word is already in the vocabulary
#
# Both of these exist because the frontend must not be the one deciding. The
# applicability rules live in derivation and the vocabulary is the contract the
# ranker and the n8n parser share; a second copy of either on the client drifts the
# first time somebody edits `applies_when` or adds a synonym, and milestone 2's
# supplier portal calls the same logic from a different principal entirely.
# --------------------------------------------------------------------------- #
def applicable_keys_for_code(db: Session, product_code: str) -> list[dict]:
    """Every active key, whether this product may carry it, and whether it already does.

    **Not `keys-for-product`.** That endpoint builds its answer from `spec.values`, so
    it returns the keys the product already HOLDS - the numerator, where the picker
    needs the denominator. It is also one query per code, and it was sitting behind a
    permission granted to no role at all.

    Applicability is `applies_when` and nothing else. `applies_to_classes` was never
    seeded, never read and never held a value in any row, so a picker keyed on it would
    have offered every product every key while looking deliberate about it.

    The gate is evaluated exactly as `product_spec_derivation._apply_scope` evaluates
    it, and the three rules it encodes are all load-bearing:

      * a key is excluded only when the gate's own key holds a value that CONTRADICTS
        it - an absent gate value never excludes, because absence of a word is never
        evidence of absence;
      * the comparison is case-insensitive;
      * a class INHERITED FROM THE CATEGORY never gates, because it is a decode of a
        filing code and evidence read from the product cannot be overruled by a guess
        about the product.

    Keeping this in one place rather than two is the point: change the rule in
    derivation and the picker changes with it.
    """
    from sqlalchemy import func

    from app.models.base import company_scope
    from app.models.product import Product
    from app.models.product_spec import ProductSpecifications
    from app.services.error_handler import AppException

    code = (product_code or "").strip()

    # All-companies, like the authored write: a spec value is true of the model rather
    # than of one company's copy of it, so which copy the caller can see must not change
    # which keys the picker offers.
    with company_scope(db, None):
        product = (
            db.query(Product).filter(func.upper(Product.product_code) == code.upper()).first()
        )
        if product is None:
            # Not an empty list. An empty list reads as "this product may carry
            # nothing", which is a sentence about the product rather than about the
            # code being wrong, and the picker would render it as a finished answer.
            raise AppException(
                status_code=404,
                message=f"No product carries the code {code}.",
                code="product_not_found",
            )

        spec = (
            db.query(ProductSpecifications)
            .join(Product, Product.id == ProductSpecifications.product_id)
            .filter(Product.product_code == product.product_code)
            .first()
        )

    values = (spec.values if spec else None) or {}
    provenance = (spec.provenance if spec else None) or {}

    # Held is "has a value" and nothing else. A removed key keeps its tombstone in
    # `provenance` so re-derivation will not refill it, but it is off the table, and
    # the picker is the one way back on: setting a value replaces the stamp wholesale.
    held = set(values)

    out: list[dict] = []
    for row in active_registry(db):
        gate = row.applies_when or {}
        applicable = True
        for gate_key, permitted in gate.items():
            gate_value = (values.get(gate_key) or {}).get("value")
            allowed = {str(v).strip().lower() for v in (permitted or [])}
            if not allowed or gate_value is None:
                continue
            if (provenance.get(gate_key) or {}).get("source") == "category":
                continue
            if str(gate_value).strip().lower() not in allowed:
                applicable = False
                break

        out.append(
            {
                "spec_key": row.spec_key,
                "label": row.label,
                "data_type": row.data_type,
                "unit": row.unit,
                "allowed_values": merged_allowed_values(row),
                "synonyms": merged_synonyms(row),
                "value_labels": dict(row.value_labels or {}),
                "applicable": applicable,
                "held": row.spec_key in held,
            }
        )
    return out


def normalise_vocabulary(text) -> str:
    """Case, spacing, punctuation and separators folded away, for comparison only.

    Never stored. The point is that "Brushed Brass", "brushed_brass" and
    "  BRUSHED-BRASS  " are one word, because storing them as three does not add a
    word - it splits one, and half the products then answer a customer's question
    while the other half do not, with nothing on any screen saying why.
    """
    import re

    folded = str(text or "").strip().lower()
    folded = re.sub(r"[\s_\-/]+", " ", folded)
    folded = re.sub(r"[^a-z0-9 ]", "", folded)
    return folded.strip()


def find_similar_key(db: Session, label: str) -> dict | None:
    """The existing key a proposed label already means, or None when it is new.

    Checked against the key, the label AND every merged synonym, because that is
    where the near-duplicates actually are: somebody proposing "Surface colour" has
    not looked for a key called `finish`, and nothing about the two strings says they
    are the same thing except the synonym that binds them.

    Enforced on the server as well as offered in the dialog (D7, D11): the dialog is a
    courtesy to the person typing, and any other client would otherwise walk straight
    past it.
    """
    needle = normalise_vocabulary(label)
    if not needle:
        return None

    rows = db.query(ProductSpecRegistry).order_by(ProductSpecRegistry.spec_key).all()

    for row in rows:
        if normalise_vocabulary(row.spec_key) == needle:
            return {
                "spec_key": row.spec_key,
                "label": row.label,
                "matched_on": "spec_key",
                "matched_text": row.spec_key,
            }
    for row in rows:
        if normalise_vocabulary(row.label) == needle:
            return {
                "spec_key": row.spec_key,
                "label": row.label,
                "matched_on": "label",
                "matched_text": row.label,
            }
    for row in rows:
        for words in merged_synonyms(row).values():
            for word in words or []:
                if normalise_vocabulary(word) == needle:
                    return {
                        "spec_key": row.spec_key,
                        "label": row.label,
                        "matched_on": "synonym",
                        "matched_text": word,
                    }
    return None


def find_similar_value(row: ProductSpecRegistry, value: str) -> dict | None:
    """The value a proposed word already means on THIS key, or None when it is new.

    Synonyms are checked as well as values for the reason `matte black` exists: it
    ships as a WORD for `black`, so adding it as a value of its own creates a value
    nothing can ever match while looking like it worked.
    """
    needle = normalise_vocabulary(value)
    if not needle:
        return None

    for existing in merged_allowed_values(row):
        if normalise_vocabulary(existing) == needle:
            return {"value": existing, "matched_on": "value", "matched_text": str(existing)}
    for existing, words in merged_synonyms(row).items():
        for word in words or []:
            if normalise_vocabulary(word) == needle:
                return {"value": existing, "matched_on": "synonym", "matched_text": word}
    return None


def vocabulary_owners(db: Session) -> dict[str, list[ProductSpecRegistry]]:
    """Every registry row (inactive ones too), indexed by each normalised word it owns.

    A row owns its key, its label and every merged word (all value keys, `_self`
    included; a suppressed word is already gone from `merged_synonyms`). Built once per
    request so a save checking several words reads the 50-odd rows once.
    """
    owners: dict[str, list[ProductSpecRegistry]] = {}
    for other in db.query(ProductSpecRegistry).order_by(ProductSpecRegistry.spec_key).all():
        seen: set[str] = set()
        texts = [other.spec_key, other.label]
        for words in merged_synonyms(other).values():
            texts.extend(words or [])
        for text in texts:
            needle = normalise_vocabulary(text)
            if needle and needle not in seen:
                seen.add(needle)
                owners.setdefault(needle, []).append(other)
    return owners


def find_word_on_other_key(
    db: Session,
    row: ProductSpecRegistry,
    word: str,
    owners: dict[str, list[ProductSpecRegistry]] | None = None,
) -> ProductSpecRegistry | None:
    """The OTHER registry row a word already belongs to, or None.

    One word, one field: the same phrase on two fields makes the parser's pick a coin
    toss. Pass `owners` (from `vocabulary_owners`) when judging several words.
    """
    needle = normalise_vocabulary(word)
    if not needle:
        return None
    if owners is None:
        owners = vocabulary_owners(db)
    return next((o for o in owners.get(needle, []) if o.id != row.id), None)
