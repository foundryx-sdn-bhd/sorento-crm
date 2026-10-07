"""Cloud console replay launcher (DO-SUMMARY-CAP): the REAL backend, the REAL parser.

Unlike the AVAIL-RENDER / AVAIL-ONHAND-BASIS launchers this one stubs nothing but storage:
the 6 Oct 2026 sandbox routes `api.openai.com` through a proxy that carries the credential,
so the semantic parser's own LLM call runs live. The whole point of the lane's A/B/C replay
is what the LIVE parser returns for the quick-reply label "Delivery order list"
(`reference_positions`, `open_question_answer`, `document`), which no stub can measure.

LLM-TEST-COST (`documentation/agents/cloud-lanes.md`): starts in `replay` (cassettes under
`~/.cache/sorento-llm-cassettes`, never in the repo); `--record` calls live once and writes
them; `--live` spends without recording. `LLM_CASSETTE_DATE` pins the parser's "today" so a
recording stays valid across days.

    SORENTO_ENV_FILE=.env.browse venv/bin/python <this file> [--record|--live] [--port 8000]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

parser = argparse.ArgumentParser()
parser.add_argument("--record", action="store_true", help="call the parser live and write cassettes")
parser.add_argument("--live", action="store_true", help="call the parser live, record nothing")
parser.add_argument("--port", type=int, default=8000)
args = parser.parse_args()

os.environ.setdefault("LLM_CASSETTE_MODE", "record" if args.record else "off" if args.live else "replay")
os.environ.setdefault("LLM_CASSETTE_DATE", "Wednesday, 07 October 2026")
os.environ.setdefault("ENABLE_SCHEDULER", "false")

import local_storage  # noqa: F401,E402

from app.main import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    print(f"[run_backend] LLM_CASSETTE_MODE={os.environ['LLM_CASSETTE_MODE']} port={args.port}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=args.port)
