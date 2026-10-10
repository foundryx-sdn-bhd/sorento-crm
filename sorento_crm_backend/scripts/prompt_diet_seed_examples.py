"""PROMPT-DIET: turn crew DEV cases into worked examples for `chatbot_examples`.

Reads every `*.json` case under --data-dir (the crew dev directory, never a path inside the
repo, never a hidden set), masks the customer's name and company out of the message and the
context, and writes two files into --out-dir for crew to load:

  examples.jsonl   one example per line
  examples.sql     idempotent INSERTs (WHERE NOT EXISTS), same rows

A case supplies the expected parser emission as `expect`; a case without one is skipped.
Output is deterministic (sorted by case id) so a rerun changes nothing. Embeddings are filled
only with --embed (one embedding provider call per example); without it the rows carry no
vector and retrieval skips them.

  venv/bin/python scripts/prompt_diet_seed_examples.py --data-dir D --out-dir O [--embed]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

CARDS_FILE = BACKEND / "app" / "services" / "chatbot" / "head" / "prompt_diet_data" / "cards.txt"
#: `order_status` values that mean a sales ask (same list as `prompt_diet.SALES_ORDER_STATUSES`;
#: this script may not import the chatbot package, `tests/chatbot/test_import_boundary.py`).
SALES_ORDER_STATUSES = {"sales_report", "sales_analysis", "top_selling", "sales_ranking"}
NAME_MASK = "[customer]"
COMPANY_MASK = "[company]"


def refuse(reason: str) -> int:
    print(f"refused: {reason}", file=sys.stderr)
    return 2


def check_data_dir(data_dir: Path) -> str | None:
    resolved = data_dir.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        return f"{resolved} is inside the repo; case text must stay outside it"
    if any("hidden" in part.casefold() for part in resolved.parts):
        return f"{resolved} looks like a hidden set; hidden cases never feed examples"
    return None


def mask_terms(contact: dict) -> list[tuple[str, str]]:
    """(text, mask) pairs, longest first so a full name goes before its parts."""
    name = (contact.get("name") or "").strip()
    first = (contact.get("first_name") or "").strip()
    company = (contact.get("company") or "").strip()
    terms: list[tuple[str, str]] = [(company, COMPANY_MASK)]
    terms += [(str(label).strip(), COMPANY_MASK) for label in contact.get("labels") or []]
    terms += [(name, NAME_MASK), (first, NAME_MASK)]
    terms += [(token, NAME_MASK) for token in name.split() if len(token) >= 3]
    terms += [(token, COMPANY_MASK) for token in company.split() if len(token) >= 5]
    return sorted({t for t in terms if t[0]}, key=lambda t: (-len(t[0]), t[0]))


def mask(text: str, terms: list[tuple[str, str]]) -> str:
    for term, mask_text in terms:
        text = re.sub(rf"\b{re.escape(term)}\b", mask_text, text, flags=re.IGNORECASE)
    return text


def previous_reply(history: list[dict]) -> str | None:
    for turn in reversed(history or []):
        if turn.get("role") == "assistant" and (turn.get("text") or "").strip():
            return turn["text"].strip()
    return None


def card_names() -> set[str]:
    return {
        line[len("<<card:") : -len(">>")]
        for line in CARDS_FILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("<<card:") and line.endswith(">>")
    }


def area_of(expect: dict, cards: set[str]) -> str | None:
    if expect.get("order_status") in SALES_ORDER_STATUSES:
        return "sales"
    hint = expect.get("domain_hint")
    return hint if hint in cards else None


def build_examples(data_dir: Path) -> list[dict]:
    rows: list[dict] = []
    cards = card_names()
    for path in sorted(data_dir.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        expect = case.get("expect")
        area = area_of(expect, cards) if isinstance(expect, dict) else None
        if area is None:
            print(f"skipped {path.name}: no usable expected emission", file=sys.stderr)
            continue
        terms = mask_terms(case.get("contact") or {})
        context = previous_reply(case.get("history") or [])
        rows.append(
            {
                "area": area,
                "message": mask(str((case.get("message") or {}).get("text") or ""), terms),
                "context": mask(context, terms) if context else None,
                "expected_output": json.loads(mask(json.dumps(expect, ensure_ascii=False), terms)),
                "source": "dev",
            }
        )
    return rows


def config_openai_key(url: str) -> str:
    """The OpenAI key from the assistant configuration, read only. Never printed, never the env."""
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            row = conn.execute(
                text("SELECT provider, api_key_ciphertext FROM ai_assistant_configs ORDER BY created_at LIMIT 1")
            ).first()
    finally:
        engine.dispose()
    if row is None or (row.provider or "").strip().casefold() != "openai" or not row.api_key_ciphertext:
        raise SystemExit("refused: no OpenAI key in ai_assistant_configs at --config-db-url")
    return row.api_key_ciphertext


def sql_text(value: str | None) -> str:
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def sql_insert(row: dict) -> str:
    embedding = row.get("embedding")
    vector = "NULL" if embedding is None else f"CAST('[{','.join(str(float(v)) for v in embedding)}]' AS vector)"
    return (
        "INSERT INTO chatbot_examples (area, message, context, expected_output, embedding, source, enabled)\n"
        f"SELECT {sql_text(row['area'])}, {sql_text(row['message'])}, {sql_text(row['context'])},\n"
        f"       CAST({sql_text(json.dumps(row['expected_output'], sort_keys=True, ensure_ascii=False))} AS jsonb),\n"
        f"       {vector}, 'dev', true\n"
        "WHERE NOT EXISTS (\n"
        f"    SELECT 1 FROM chatbot_examples WHERE source = 'dev' AND area = {sql_text(row['area'])}\n"
        f"      AND message = {sql_text(row['message'])}\n"
        ");\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--embed", action="store_true", help="fill each row's embedding (provider calls)")
    ap.add_argument(
        "--config-db-url",
        help="database whose ai_assistant_configs row holds the OpenAI key (read only); required with --embed",
    )
    args = ap.parse_args()

    reason = check_data_dir(args.data_dir)
    if reason:
        return refuse(reason)
    rows = build_examples(args.data_dir)
    if args.embed:
        if not args.config_db_url:
            return refuse("--embed needs --config-db-url (the key is read from ai_assistant_configs, never the env)")
        from app.config import settings
        from app.services.llm_provider import OpenAIProvider

        provider = OpenAIProvider(config_openai_key(args.config_db_url), settings.embedding_model_name)
        for row in rows:
            row["embedding"] = provider.embed("\n".join(part for part in (row["context"], row["message"]) if part))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "examples.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    (args.out_dir / "examples.sql").write_text(
        "-- PROMPT-DIET dev examples; idempotent, never touches alembic_version\n"
        + "".join(sql_insert(row) for row in rows),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
