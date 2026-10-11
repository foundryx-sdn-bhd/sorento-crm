#!/usr/bin/env python3
"""Append the Chinese class words to `product_categories.search_synonyms`.

WHY THIS EXISTS
---------------
The parser prompt used to carry a Chinese -> English class map (v50, "马桶 -> water
closet"). Owner ruling 11 Oct 2026 (PROMPT-ABLATION #1574): vocabulary lives in DATA, not in
the prompt. No category, brand or product carried a Chinese word, so with the map cut a
Chinese class word reached the resolver raw and named nothing.

`search_synonyms` is the column every class reader already takes: `ClassVocabulary`
(`app/services/product_class_signal.py`), `resolve_category_class`
(`app/services/chatbot/lanes/business/services.py`, shared by top selling, sales ranking and
the low stock ask) and the head's grounding vocabulary. Adding the words there is the whole
change; no resolver code moves.

WHAT IT DOES
------------
For every searchable category whose `class_label` is a key of `ZH_CLASS_SYNONYMS`, appends
the words that category does not carry yet, after its existing ones. Each word sits under
exactly one class; a word that could name two classes is left out on purpose.

SAFETY / IDEMPOTENCY
--------------------
Dry-run is the DEFAULT; nothing is written without `--apply`. A word already present is
skipped, so a second `--apply` reports nothing to add. Append only: no word is ever removed.

Run from sorento_crm_backend/:
    venv/bin/python scripts/add_zh_category_synonyms.py
    venv/bin/python scripts/add_zh_category_synonyms.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session

from app.models.product import ProductCategory

# class_label -> Chinese words. The v50 parser prompt's map, one class per word.
ZH_CLASS_SYNONYMS: dict[str, list[str]] = {
    "Water Closet": ["马桶", "坐厕"],
    "Wash Basin": ["面盆", "洗手盆"],
    "Tap": ["水龙头"],
    "Bathroom Furniture": ["浴室柜"],
    # "sink" already answers for Kitchen Sink (product_class_signal.CLASS_SYNONYMS), and a
    # basin has its own words above.
    "Kitchen Sink": ["厨房水槽", "水槽"],
    "Shower": ["淋浴", "花洒"],
    "Bathtub and Jacuzzi": ["浴缸"],
    "Urinal": ["小便斗"],
}


def plan(db: Session) -> List[Dict[str, Any]]:
    """One entry per category missing at least one of its class's words."""
    rows = (
        db.query(ProductCategory)
        .filter(
            ProductCategory.is_searchable.is_(True),
            ProductCategory.class_label.in_(sorted(ZH_CLASS_SYNONYMS)),
        )
        .order_by(ProductCategory.category_code)
        .all()
    )
    entries: List[Dict[str, Any]] = []
    for row in rows:
        have = {str(w).strip().lower() for w in (row.search_synonyms or [])}
        add = [w for w in ZH_CLASS_SYNONYMS[row.class_label] if w.lower() not in have]
        if add:
            entries.append(
                {"id": str(row.id), "category_code": row.category_code, "class_label": row.class_label, "add": add}
            )
    return entries


def run(db: Session, *, apply: bool = False) -> Dict[str, Any]:
    """Report (and with `apply`, write) the words each category is missing. The caller commits."""
    entries = plan(db)
    added = 0
    for entry in entries:
        print(f"  {entry['category_code']:<12} {entry['class_label']:<22} + {', '.join(entry['add'])}")
        if apply:
            row = db.get(ProductCategory, entry["id"])
            row.search_synonyms = [*(row.search_synonyms or []), *entry["add"]]
            added += len(entry["add"])
    if apply:
        db.flush()
    return {"plan": entries, "added": added}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the words. Without it the script only reports.")
    args = parser.parse_args()

    from app.database import SessionLocal
    from app.models.base import set_company_scope

    db = SessionLocal()
    # A script has no principal; `None` is the sanctioned all-companies scope (see
    # scripts/backfill_manual_alias_links.py), so every company's categories are read.
    set_company_scope(db, None)
    try:
        result = run(db, apply=args.apply)
        if args.apply:
            db.commit()
        mode = "applied" if args.apply else "dry-run"
        print(f"{mode}: {len(result['plan'])} categories, {result['added']} words written")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
