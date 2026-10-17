"""Live golden check for the one-message ideate extractor (IDEA-CAPTURE).

Runs each fake-but-realistic golden message through the REAL `extract_ideate_turn` (the
shipped schema + a prompt text) against the live LLM and prints what was captured, so a human
can check: no invented field, use cases only where named, the right language.

    OPENAI_API_KEY=... venv/bin/python scripts/ideation_capture_golden.py
    venv/bin/python scripts/ideation_capture_golden.py --prompt-file prod_prompt.txt --model gpt-4o-mini
    venv/bin/python scripts/ideation_capture_golden.py --messages my_messages.txt   # one per line
    venv/bin/python scripts/ideation_capture_golden.py --from-db   # crew: the DB's production line
    venv/bin/python scripts/ideation_capture_golden.py --turns --model gpt-5.4-mini   # SEQUENCES

Migration `idea_capture_fields_0001` publishes the new `ideate_extractor` fallback to the
registry (a stock production text moves to the next version; an owner-edited one is left). Crew
applies it (`alembic upgrade head`) before `--from-db`, otherwise `--from-db` runs the OLD text.

`--turns` runs the multi-turn SEQUENCES (a narrowing follow-up, a short reply to every question
the bot asks, a topic switch and back) through the REAL `handle_capture_turn` with `is_test=True`,
carrying `session_vars` from each result into the next call. Only the contact, the access gate,
the config and the two shared-service calls are stubbed; the extractor and the reply copy are real.
It reports, it does not assert. Combines with `--prompt-file` and `--from-db`.

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
import uuid

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
    ("zh problem + workaround + want",
     "客户每次问货到了没有，我们都要打电话问仓库，很慢。现在只能一个个打电话。"
     "希望系统能直接显示到货状态。"),
    ("ms solution only",
     "kita patut buat app untuk scan resit pelanggan"),
    ("en impact + department (dropped fields)",
     "Accounts department keeps chasing overdue invoices on paper. It costs us about RM20k a "
     "month in late payments. Maybe auto reminders on WhatsApp."),
]


_ID_A = "00000000-0000-4000-8000-0000000000a1"
_ID_B = "00000000-0000-4000-8000-0000000000b2"
_TWO_SIMILAR = [
    {"idea_id": _ID_A, "idea_number": "IDEA-0101", "title": "Quote approval by WhatsApp"},
    {"idea_id": _ID_B, "idea_number": "IDEA-0102", "title": "Faster quote sign off"},
]
_IDEA = "Quote approvals take days because managers must sign a paper form."
_IDEA_REPLY = "the quote approval takes days because managers sign on paper, want it approved on the phone"

# Multi-turn sequences (fake text). Turn keys: text, ask_reply (bool), similar (the fake ss
# similar-own matches for that turn), lane ("ideate" default; "other" = a turn the router would
# send elsewhere: skipped, the session pointer carries over untouched).
SEQUENCES: list[tuple[str, list[dict]]] = [
    ("ask-back short reply sorento", [{"text": "I have an idea"}, {"text": "sorento", "ask_reply": True}]),
    ("ask-back short reply yes", [{"text": "I have an idea"}, {"text": "yes", "ask_reply": True}]),
    ("ask-back short reply 2026", [{"text": "I have an idea"}, {"text": "2026", "ask_reply": True}]),
    ("ask-back reply carries the idea", [{"text": "I have an idea"}, {"text": _IDEA_REPLY, "ask_reply": True}]),
    ("similar list then pick 2", [{"text": _IDEA, "similar": _TWO_SIMILAR}, {"text": "2"}]),
    ("similar list then NEW", [{"text": _IDEA, "similar": _TWO_SIMILAR}, {"text": "NEW"}]),
    ("similar list then yes", [{"text": _IDEA, "similar": _TWO_SIMILAR}, {"text": "yes"}]),
    ("similar list then 2026", [{"text": _IDEA, "similar": _TWO_SIMILAR}, {"text": "2026"}]),
    ("narrow to a branch", [{"text": _IDEA}, {"text": "only for the Penang branch"}]),
    ("narrow to a quarter", [{"text": _IDEA}, {"text": "mainly in Q1 2026"}]),
    ("switch topic and back to NEW", [
        {"text": _IDEA, "similar": _TWO_SIMILAR},
        {"text": "what's the stock of SRTWC8517?", "lane": "other"},
        {"text": "NEW"},
    ]),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-file", help="production ideate_extractor text; default: fallback")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--messages", help="file with one message per line instead of the golden set")
    ap.add_argument("--from-db", action="store_true", help="production prompt + config from the DB")
    ap.add_argument("--turns", action="store_true", help="run SEQUENCES through handle_capture_turn")
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
            _run_turns(db) if args.turns else _run(db, cases)
        finally:
            db.close()
        return 0

    with mock.patch.object(ideation_extractor, "AIAssistantConfigService") as cfg, mock.patch.object(
        ideation_extractor.ai_prompt_registry, "render", return_value=(prompt, None)
    ):
        cfg.return_value.get.return_value = config
        _run_turns(None) if args.turns else _run(None, cases)
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


def _run_turns(db) -> None:
    """Each sequence through the real `handle_capture_turn` (is_test), session carried over."""
    from app.services import ideation_capture_service as svc

    created: list[dict] = []

    def fake_create(_base, _key, payload):
        created.append(payload)
        n = len(created)
        return {"status": "captured", "idea_id": str(uuid.uuid4()), "idea_number": f"IDEA-90{n:02d}",
                "title": payload.get("title") or ""}

    current = {"similar": []}
    contact = SimpleNamespace(phone_number="+60100000000", session_vars={}, display_name="Test Staff",
                              submitter_tier=None)
    ready = SimpleNamespace(base_url="https://ss.example", api_key="k", product_id="prod-1", is_ready=True)
    with mock.patch.object(svc, "_get_contact_row", return_value=contact), mock.patch.object(
        svc, "_access_user", return_value=SimpleNamespace(id=1)
    ), mock.patch.object(svc, "_resolve_ideation_config", return_value=ready), mock.patch.object(
        svc, "call_similar_own", side_effect=lambda *_a, **_k: {"matches": list(current["similar"])}
    ), mock.patch.object(svc, "call_create_idea", side_effect=fake_create), mock.patch.object(
        svc.settings, "frontend_base_url", "https://crm.example"
    ):
        for name, turns in SEQUENCES:
            print(f"\n=== {name}")
            session_vars: dict = {}
            for turn in turns:
                text = turn["text"]
                if turn.get("lane", "ideate") == "other":
                    print(f"> {text}\n  (other lane: skipped, pointer carried over)")
                    continue
                current["similar"] = turn.get("similar") or []
                before = len(created)
                out = svc.handle_capture_turn(
                    db, respond_io_id="golden", message_text=text, session_vars_in=session_vars,
                    is_test=True, ask_reply=bool(turn.get("ask_reply")),
                )
                session_vars = out["session_vars"]
                print(f"> {text}{'  [ask_reply]' if turn.get('ask_reply') else ''}")
                print(f"  status={out['status']}")
                print("  reply: " + out["reply_text"].replace("\n", " / "))
                if len(created) > before:
                    p = created[-1]
                    print("  created: " + json.dumps(
                        {k: p.get(k) for k in ("problem", "desired_outcome", "use_cases", "current_workaround")
                         if p.get(k)}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
