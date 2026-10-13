#!/usr/bin/env python3
"""Count, then delete, embedding documents whose source row no longer exists
(SCOPE-FRAMES, finding 2; documentation/plans/security/PLAN-embedding-scope-frames-12oct.md).

Until this lane the embedding worker dead-lettered every `<type>.deleted` event, so a
hard-deleted source kept its embedding, and one with a NULL company stayed visible under
every company scope. The worker now removes the embedding on the delete event; this sweeps
what was left behind before that, plus deletes that never raise an event (bulk
`query.delete()`, frame trims, FK cascades, order and order-line rows replaced by imports).

    python scripts/purge_orphan_embeddings.py                      # dry run: counts per source_type
    python scripts/purge_orphan_embeddings.py --apply --expect N   # N = the dry run's total

`--apply` refuses (exit 2, nothing deleted) unless `--expect` equals the total a fresh dry
run finds, so an apply always follows a dry run that was read. It deletes in batches of
`--batch-size` documents (chunks first), committing each batch, so a large backlog (102,633
documents on the dev copy, 13 Oct) never holds one long transaction. Owner step on prod.

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
from app.models.embeddings import EmbeddingDocument  # noqa: E402
from app.services.embedding_worker import _delete_documents, purge_orphan_embeddings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="delete the orphans (default: dry run)")
    parser.add_argument("--expect", type=int, help="with --apply: the total the dry run reported")
    parser.add_argument("--batch-size", type=int, default=1000, help="documents per committed batch")
    args = parser.parse_args(argv)

    db = SessionLocal()
    set_company_scope(db, None)
    try:
        result = purge_orphan_embeddings(db, apply=False)
        db.rollback()
        total = sum(result["counts"].values())
        for source_type, count in sorted(result["counts"].items()):
            print(f"{source_type}: {count}")
        if not args.apply:
            print(f"would delete: {total} document(s)")
            return 0
        if args.expect != total:
            print(
                f"refused: --expect {args.expect} does not match the {total} orphan document(s) "
                "found now; run the dry run again and pass its total",
                file=sys.stderr,
            )
            return 2
        doc_ids = result["doc_ids"]
        deleted = 0
        for start in range(0, len(doc_ids), max(1, args.batch_size)):
            batch = doc_ids[start : start + max(1, args.batch_size)]
            deleted += _delete_documents(
                db, db.query(EmbeddingDocument.id).filter(EmbeddingDocument.id.in_(batch))
            )
            db.commit()
        print(f"deleted: {deleted} document(s)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
