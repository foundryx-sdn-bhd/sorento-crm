#!/usr/bin/env python3
"""Encrypt AI assistant provider keys still stored in plain text (AI-KEY-ENCRYPT).

`ai_assistant_configs.{api_key,anthropic_api_key,gemini_api_key}_ciphertext` held
the key in plain text until the columns became `EncryptedText`. The app reads both
forms, so this can run any time after the code is deployed. It encrypts with the
same key the app uses (derived from JWT_SECRET), so run it with the deployment's env.

Dry run by default: prints per-column counts of plaintext / encrypted / empty values,
never a value. `--apply` encrypts the plaintext ones in one transaction. Re-running
is a no-op.

Run from sorento_crm_backend/:
    python scripts/encrypt_ai_assistant_keys.py           # dry run
    python scripts/encrypt_ai_assistant_keys.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.utils.field_encryption import encrypt_secret, is_fernet_token  # noqa: E402

COLUMNS = ("api_key_ciphertext", "anthropic_api_key_ciphertext", "gemini_api_key_ciphertext")


def backfill(db: Session, *, apply: bool) -> dict[str, dict[str, int]]:
    """Counts per column as found; with ``apply``, plaintext values are encrypted."""
    counts = {c: {"plaintext": 0, "encrypted": 0, "empty": 0} for c in COLUMNS}
    rows = db.execute(text(f"SELECT id, {', '.join(COLUMNS)} FROM ai_assistant_configs")).all()
    for row in rows:
        for column in COLUMNS:
            value = getattr(row, column)
            if not value:
                counts[column]["empty"] += 1
            elif is_fernet_token(value):
                counts[column]["encrypted"] += 1
            else:
                counts[column]["plaintext"] += 1
                if apply:
                    db.execute(
                        text(f"UPDATE ai_assistant_configs SET {column} = :v WHERE id = :i"),
                        {"v": encrypt_secret(value), "i": row.id},
                    )
    if apply:
        db.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="Encrypt plaintext keys (default: dry run).")
    args = parser.parse_args()

    from app.database import SessionLocal

    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    with SessionLocal() as db:
        counts = backfill(db, apply=args.apply)
    for column, c in counts.items():
        print(f"{column}: plaintext={c['plaintext']} encrypted={c['encrypted']} empty={c['empty']}")
    if not args.apply and any(c["plaintext"] for c in counts.values()):
        print("Re-run with --apply to encrypt the plaintext values.")


if __name__ == "__main__":
    main()
