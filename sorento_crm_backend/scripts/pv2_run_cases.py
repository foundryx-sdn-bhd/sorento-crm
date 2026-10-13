#!/usr/bin/env python3
"""Drive multi-turn chat cases through the REAL console turn path on parser v2
(CONTRACT-PV2-13oct.md sections 7-8). Crew tooling for recordings and gate runs.

Each case is a list of user texts; every turn is a dry-run console turn
(`console_service.run_console_turn`, nothing written to the contact), the next turn
carries the previous turn's `session_patch` (v1 five keys + `pv2`), and the console
envelope opts in to v2 with `parser_mode: "v2"`. Set `PV2_RECORD_DIR` to also append
each parser v2 output to `<dir>/pv2-record-<date>.jsonl` (fixture recording).

THIS CALLS THE LIVE MODEL configured on the database (ai_assistant_configs). Never run it
from a sandbox without a budget.

Input: JSONL, one object per case: {"case": "A", "contact": "<respond_io_id>", "turns": ["...", "..."]}
Output: JSONL to stdout, one object per turn: {case, turn, text, reply, branch_kind,
prompt_version, quick_replies}.

Run from sorento_crm_backend/:
    PV2_RECORD_DIR=/tmp/pv2rec venv/bin/python scripts/pv2_run_cases.py cases.jsonl > out.jsonl
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    from app.database import SessionLocal
    from app.services.chatbot import console_service

    real_build = console_service._build_envelope

    def build_v2(*args, **kwargs):
        envelope = real_build(*args, **kwargs)
        envelope["parser_mode"] = "v2"
        return envelope

    console_service._build_envelope = build_v2
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        session_vars: dict | None = {}
        run_id = f"pv2-{case['case']}-{uuid.uuid4().hex[:6]}"
        for n, text in enumerate(case["turns"], 1):
            db = SessionLocal()
            try:
                res = console_service.run_console_turn(
                    db,
                    contact_respond_id=str(case["contact"]),
                    text=text,
                    session_vars=session_vars,
                    run_id=run_id,
                )
            finally:
                db.close()
            session_vars = res.session_vars if isinstance(res.session_vars, dict) else session_vars
            print(json.dumps({
                "case": case["case"], "turn": n, "text": text, "reply": res.reply_text,
                "branch_kind": res.branch_kind, "prompt_version": res.prompt_version,
                "quick_replies": res.quick_replies,
            }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
