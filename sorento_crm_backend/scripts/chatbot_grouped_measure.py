"""CHAT-SPARSE: decode tokens and latency of the parser call, strict vs grouped, on the
same made-up turns (`documentation/plans/chatbot/PLAN-chat-sparse-emission-12oct.md`).

Live provider calls, no database: the prompt comes from a text file, the user block from
`turn/context.assemble`, the call from `head/parser.parse` (the production path, with its
hydration and wire check). Every turn is synthetic.

    venv/bin/python scripts/chatbot_grouped_measure.py \
        --prompt alembic/data/chatbot_semantic_parser.candidate-20261010.txt \
        --arm strict --arm grouped --out /tmp/grouped.json

Each arm costs one call per turn. Spend cap for the lane is about 60 calls.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.chatbot.head import parser  # noqa: E402
from app.services.chatbot.turn import context as context_mod  # noqa: E402
from app.services.chatbot_parser_prompt import grouped_prompt  # noqa: E402

CURRENT_DATE = "Monday, 12 October 2026"

BRANDS = [{"brand_name": "Sorento", "brand_code": "SRT"}, {"brand_name": "Mocha", "brand_code": "MCH"}]

#: (message, previous response, open question line). Made up; no real customer or record.
TURNS: list[tuple[str, str | None, str | None]] = [
    ("stock SRTWC286", None, None),
    ("how many SRTWT7408 in BRW", None, None),
    ("thanks", "SRTWC286: 12 units in BRW.", None),
    ("outstanding DO for hanlim", None, None),
    ("price of SRTKT39SS", None, None),
    ("yes", "Would you like me to pass this to the purchasing team?", None),
    ("no thanks", "Would you like me to pass this to the purchasing team?", None),
    ("top 10 selling items by quantity this year", None, None),
    ("sales report for KIM HUAT March", None, None),
    ("low stock report by supplier", None, None),
    ("M210-GM x5 and SRTBF 11502 x3, available?", None, None),
    ("2", "1. SRTWC286-W\n2. SRTWC286-B\nWhich one?", None),
    ("any promotion for bathtubs now", None, None),
    ("I want to talk to someone from marketing", None, None),
    ("status of PO 4500123", None, None),
    ("when is the next container coming", None, None),
    ("just SRTWC286", "Sales report, dealer channel, January to September 2026.", None),
    ("10 for all", None,
     'Open question: {"kind": "quantity", "items": [{"position": 1, "code": "SRTWC286", "qty": null}, '
     '{"position": 2, "code": "SRTWT7408", "qty": null}]}'),
    ("I'm the purchaser at our Penang branch, reply in Malay please", None, None),
    ("never mind, show me promotions instead", "SRTWC286: 12 units in BRW.", None),
]


def user_block(message: str, previous: str | None, open_question: str | None) -> str:
    layers = context_mod.ContextLayers(
        level="recent",
        profile_facts=None,
        summaries=None,
        earlier_messages=None,
        previous_response=previous,
        current_subject=None,
        pending_kind=None,
        pending_options=None,
        settings_profile_line=None,
        current_message=message,
        reply_to=None,
        media_line=None,
        open_question_line=open_question,
        brands=BRANDS,
    )
    block, _ = context_mod.assemble(layers)
    return block


def run(prompt: str, arm: str, model: str, limit: int) -> list[dict]:
    text = (grouped_prompt(prompt) if arm == "grouped" else prompt).replace("{{current_date}}", CURRENT_DATE)
    config = parser.ParserConfig(
        system_prompt=text,
        prompt_version=None,
        provider="openai",
        model=model,
        api_key="injected-by-proxy",
        grouped=arm == "grouped",
    )
    from app.services.chatbot import llm_call

    seen: list[str] = []
    real_chat = llm_call.chat

    def capture(*a, **k):
        result = real_chat(*a, **k)
        seen.append(result.content or "")
        return result

    llm_call.chat = capture
    rows = []
    for message, previous, open_question in TURNS[:limit]:
        started = time.perf_counter()
        row: dict = {"arm": arm, "message": message}
        try:
            out = parser.parse(config, user_block(message, previous, open_question))
            row["emission"] = dict(out)
            row["usage"] = out.usage
        except parser.ParserError as exc:
            row["error"] = str(exc)
            row["usage"] = exc.usage
        row["seconds"] = round(time.perf_counter() - started, 3)
        row["wire"] = seen[-1] if seen else None
        seen.clear()
        print(arm, row["seconds"], row["usage"].get("completion_tokens"), message, row.get("error", ""), flush=True)
        rows.append(row)
    llm_call.chat = real_chat
    return rows


def summary(rows: list[dict]) -> dict:
    secs = sorted(r["seconds"] for r in rows)
    toks = [r["usage"].get("completion_tokens", 0) for r in rows]
    p95 = secs[min(len(secs) - 1, round(0.95 * (len(secs) - 1)))]
    return {
        "n": len(rows),
        "errors": sum(1 for r in rows if "error" in r),
        "p50_s": statistics.median(secs),
        "p95_s": p95,
        "max_s": secs[-1],
        "mean_completion_tokens": round(statistics.mean(toks), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--arm", action="append", choices=["strict", "grouped"], required=True)
    ap.add_argument("--model", default="gpt-5.4-mini")
    ap.add_argument("--limit", type=int, default=len(TURNS))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    prompt = Path(args.prompt).read_text(encoding="utf-8")
    result = {}
    for arm in args.arm:
        rows = run(prompt, arm, args.model, args.limit)
        result[arm] = {"summary": summary(rows), "rows": rows}
        print(arm, json.dumps(result[arm]["summary"]), flush=True)
    Path(args.out).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
