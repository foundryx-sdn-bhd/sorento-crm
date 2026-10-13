#!/usr/bin/env python3
"""Count, then delete, embedding documents whose source row no longer exists
(SCOPE-FRAMES, finding 2; documentation/plans/security/PLAN-embedding-scope-frames-12oct.md).

Until this lane the embedding worker dead-lettered every `<type>.deleted` event, so a
hard-deleted source kept its embedding, and one with a NULL company stayed visible under
every company scope. The worker now removes the embedding on the delete event; this sweeps
what was left behind before that, plus deletes that never raise an event (bulk
`query.delete()`, frame trims, FK cascades).

    python scripts/purge_orphan_embeddings.py           # dry run: counts per source_type
    python scripts/purge_orphan_embeddings.py --apply   # delete them (chunks first), one commit

Logic: `embedding_worker.purge_orphan_embeddings`. `debtor:<company>:<code>` seeds (and legacy
`debtor:<code>` ones) have no row by design and are never touched; types with no source table
(mcp_tool, schema_doc, ...) are not scanned. Idempotent: a second `--apply` finds nothing.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.models.base import set_company_scope  # noqa: E402
from app.services.embedding_worker import purge_orphan_embeddings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="delete the orphans (default: dry run)")
    args = parser.parse_args()

    db = SessionLocal()
    set_company_scope(db, None)
    try:
        result = purge_orphan_embeddings(db, apply=args.apply)
        if args.apply:
            db.commit()
        else:
            db.rollback()
        total = sum(result["counts"].values())
        for source_type, count in sorted(result["counts"].items()):
            print(f"{source_type}: {count}")
        print(f"{'deleted' if args.apply else 'would delete'}: {total} document(s)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
