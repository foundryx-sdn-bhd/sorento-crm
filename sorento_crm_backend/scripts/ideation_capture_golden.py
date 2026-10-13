"""Live golden check for the one-message ideate extractor (IDEA-CAPTURE).

Runs each fake-but-realistic golden message through the REAL `extract_ideate_turn` (the
shipped schema + a prompt text) against the live LLM and prints what was captured, so a human
can check: no invented field, use cases only where named, the right language.

    OPENAI_API_KEY=... venv/bin/python scripts/ideation_capture_golden.py
    venv/bin/python scripts/ideation_capture_golden.py --prompt-file prod_prompt.txt --model gpt-4o-mini
    venv/bin/python scripts/ideation_capture_golden.py --messages my_messages.txt   # one per line
    venv/bin/python scripts/ideation_capture_golden.py --from-db   # crew: the DB's production line

`--prompt-file` is the production-label `ideate_extractor` text (an owner-edited install runs
the new schema under the OLD prompt; that combination is the one that must hold). Without it the
shipped fallback is used. No database is touched: the config and the prompt are supplied here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import ideation_extractor  # noqa: E402
from app.services.ai_prompt_registry import PROMPT_KEYS  # noqa: E402

# Fake, realistic. Never paste real customer or staff conversation here (public repo).
GOLDEN: list[tuple[str, str]] = [
    ("en problem + workaround",
     "Sales reps retype WhatsApp orders into the CRM by hand, about an hour a day each. "
     "Right now we copy into Excel first then key in."),
    ("ms problem + outcome",
     "Stok gudang selalu tak sama dengan sistem. Nak sistem update sendiri bila barang keluar "
     "supaya sales nampak stok sebenar."),
    ("en all four",
     "Customers keep asking us where their delivery is. Want them to check it themselves. "
     "E.g. a dealer checking if his PO shipped, a project customer checking the site delivery "
     "date. Now we WhatsApp logistics and wait for them to reply."),
    ("zh problem only", "报价单每次都要手动算折扣，经常算错。"),
    ("intent only", "I have an idea"),
    ("solution only", "we should build an app to scan orders"),
    ("manglish typo",
     "the showroom staff cant see promo price lah, customer ask then they must call HQ. "
     "would be gd if price list show promo in red"),
    ("en use cases no workaround",
     "Service team has no way to see a customer's past warranty claims. Need it for when a "
     "customer calls about a repeat leak, and when a dealer asks if a claim was approved."),
    ("mixed ms en workaround",
     "Bila container sampai, kita check packing list manual dengan pen. Selalu miss item. "
     "Sekarang kita print dan tick satu-satu."),
    ("en impact + department (dropped fields)",
     "Accounts department keeps chasing overdue invoices on paper. It costs us about RM20k a "
     "month in late payments. Maybe auto reminders on WhatsApp."),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-file", help="production ideate_extractor text; default: fallback")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--messages", help="file with one message per line instead of the golden set")
    ap.add_argument("--from-db", action="store_true", help="production prompt + config from the DB")
    args = ap.parse_args()

    prompt = (
        Path(args.prompt_file).read_text(encoding="utf-8")
        if args.prompt_file
        else PROMPT_KEYS["ideate_extractor"].fallback()
    )
    cases = GOLDEN
    if args.messages:
        lines = Path(args.messages).read_text(encoding="utf-8").splitlines()
        cases = [(f"msg {i}", line) for i, line in enumerate(lines, 1) if line.strip()]

    # The proxy injects the real key for api.openai.com; any non-empty value passes the guard.
    config = SimpleNamespace(
        provider="openai",
        model=args.model,
        api_key_ciphertext=os.environ.get("OPENAI_API_KEY") or "proxy-injected",
    )
    if args.from_db:
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            _, version = ideation_extractor.ai_prompt_registry.render(db, "ideate_extractor")
            print(f"# production ideate_extractor version: {version}")
            _run(db, cases)
        finally:
            db.close()
        return 0

    with mock.patch.object(ideation_extractor, "AIAssistantConfigService") as cfg, mock.patch.object(
        ideation_extractor.ai_prompt_registry, "render", return_value=(prompt, None)
    ):
        cfg.return_value.get.return_value = config
        _run(None, cases)
    return 0


def _run(db, cases: list[tuple[str, str]]) -> None:
    for name, text in cases:
        out = ideation_extractor.extract_ideate_turn(db, message_text=text)
        print(json.dumps({
            "case": name,
            "message": text,
            "language": out.language,
            "has_idea": out.has_idea,
            "title": out.title,
            "fields": out.fields,
            "use_cases": getattr(out, "use_cases", None),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
