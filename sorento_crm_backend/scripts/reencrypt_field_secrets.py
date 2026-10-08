#!/usr/bin/env python3
"""Move every stored secret onto FIELD_ENCRYPTION_KEY (AI-KEY-ENCRYPT).

Covers every column `app/utils/field_encryption.py` encrypts:
- respond_workspaces: api_key_ciphertext, ideation_intake_api_key_ciphertext,
  ideation_embed_signing_secret_ciphertext, chatbot_retry_ingress_key_ciphertext
  (app/services/respond_workspace_service.py)
- integrations.credentials_json (app/services/integration_admin_service.py)
- ai_assistant_configs: api_key_ciphertext, anthropic_api_key_ciphertext,
  gemini_api_key_ciphertext (EncryptedText, app/models/ai_assistant.py)

Per value:
- token the current key opens       -> left alone ("current")
- token an older key opens          -> re-encrypted under the current key ("old_key"),
                                       which includes every JWT_SECRET-derived token
- token no configured key opens     -> left alone, reported ("unreadable")
- not a token                       -> encrypted for the ai_assistant_configs columns,
                                       which held plain text before this lane; reported
                                       and left alone anywhere else ("plaintext")

Dry run by default: counts only, never a value. `--apply` writes in one transaction
and refuses unless FIELD_ENCRYPTION_KEY is set (re-encrypting onto the JWT-derived key
would defeat the point). Each UPDATE is a compare-and-set on the value read, so a secret
saved meanwhile is never overwritten (counted as "skipped"). Re-running is a no-op.

Run from sorento_crm_backend/ with the deployment's env (FIELD_ENCRYPTION_KEY, and the
JWT_SECRET the old tokens were written under):
    python scripts/reencrypt_field_secrets.py           # dry run
    python scripts/reencrypt_field_secrets.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.utils.field_encryption import (  # noqa: E402
    encrypt_secret,
    field_encryption_key_configured,
    is_fernet_token,
    opens_with_current_key,
    reencrypt_secret,
)

# (table, column, plaintext is a legacy value to encrypt)
COLUMNS = (
    ("respond_workspaces", "api_key_ciphertext", False),
    ("respond_workspaces", "ideation_intake_api_key_ciphertext", False),
    ("respond_workspaces", "ideation_embed_signing_secret_ciphertext", False),
    ("respond_workspaces", "chatbot_retry_ingress_key_ciphertext", False),
    ("integrations", "credentials_json", False),
    ("ai_assistant_configs", "api_key_ciphertext", True),
    ("ai_assistant_configs", "anthropic_api_key_ciphertext", True),
    ("ai_assistant_configs", "gemini_api_key_ciphertext", True),
)
STATES = ("current", "old_key", "plaintext", "unreadable", "empty")


def _classify(value: str | None) -> str:
    if not value:
        return "empty"
    if not is_fernet_token(value):
        return "plaintext"
    if opens_with_current_key(value):
        return "current"
    try:
        reencrypt_secret(value)
    except ValueError:
        return "unreadable"
    return "old_key"


def backfill(db: Session, *, apply: bool) -> dict[str, dict[str, int]]:
    """Counts per ``table.column`` as found; with ``apply``, old-key and legacy plaintext
    values are written under the current key."""
    if apply and not field_encryption_key_configured():
        raise RuntimeError("FIELD_ENCRYPTION_KEY is not set; refusing to re-encrypt")
    counts: dict[str, dict[str, int]] = {}
    for table, column, encrypt_plaintext in COLUMNS:
        tally = counts[f"{table}.{column}"] = dict.fromkeys((*STATES, "skipped"), 0)
        rows = db.execute(text(f"SELECT id, {column} AS v FROM {table}")).all()
        for row in rows:
            state = _classify(row.v)
            tally[state] += 1
            if not apply:
                continue
            if state == "old_key":
                new = reencrypt_secret(row.v)
            elif state == "plaintext" and encrypt_plaintext:
                new = encrypt_secret(row.v)
            else:
                continue
            result = db.execute(
                text(f"UPDATE {table} SET {column} = :new WHERE id = :i AND {column} = :old"),
                {"new": new, "i": row.id, "old": row.v},
            )
            if result.rowcount == 0:
                tally["skipped"] += 1  # changed since it was read; the newer value stands
    if apply:
        db.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="Re-encrypt (default: dry run).")
    args = parser.parse_args()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.config import settings

    # Its own engine: never echo, and keep bound values (a legacy plaintext AI key is the
    # `:old` of its UPDATE) out of any DBAPIError message.
    engine = create_engine(settings.database_url, echo=False, hide_parameters=True)
    SessionLocal = sessionmaker(bind=engine)

    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    try:
        print(f"FIELD_ENCRYPTION_KEY set: {field_encryption_key_configured()}")
        with SessionLocal() as db:
            counts = backfill(db, apply=args.apply)
    except (RuntimeError, ValueError) as e:  # ValueError: a malformed FIELD_ENCRYPTION_KEY
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    for name, tally in counts.items():
        print(f"{name}: " + " ".join(f"{s}={tally[s]}" for s in (*STATES, "skipped")))
    if not args.apply and any(t["old_key"] or t["plaintext"] for t in counts.values()):
        print("Re-run with --apply to move old_key (and AI-key plaintext) values onto the current key.")
    if any(t["unreadable"] for t in counts.values()):
        print("unreadable > 0: those values were written under a key no longer configured; re-enter them.")


if __name__ == "__main__":
    main()
