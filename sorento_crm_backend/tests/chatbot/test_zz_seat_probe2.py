import json, os
from collections import Counter
import pytest
from tests.chatbot.test_find_live_parser import _system, _keys, _product_codes, db, KNOWN_BRANDS  # noqa: F401

pytestmark = pytest.mark.live_external
N = int(os.environ.get("PROBE_N", "3"))
STOCK_REPLY = "Stock for SRTSCBD333-UF:\n✅ Got stock\nStock for SRTSCBD333A-UF:\n✅ Got stock"
FILE_REPLY = "Here are the technical drawings for SRTWC8383-RL-MBL:\n1. SRTWC8383-RL-MBL technical drawing.pdf"
OTHER_STOCK = "Stock for SRTWT5608: 12 units (KL: 8, JB: 4)"
CASES = [
    ("P1 cold", "seat cover SRTWC8122", OTHER_STOCK.replace("SRTWT5608", "x") if False else "(none)", None),
    ("P2 after stock", "seat cover for SRTWC8383-RL-MBL", OTHER_STOCK, [("stock SRTWT5608", OTHER_STOCK)]),
    ("P3 after file", "SRTWC8122 seat cover", FILE_REPLY, [("SRTWC8383-RL-MBL technical drawing", FILE_REPLY)]),
    ("P4 carry", "SRTWC8383-RL-MBL", STOCK_REPLY, [("SRTWC8122 seat cover stock", STOCK_REPLY)]),
    ("P4b carry nostock", "SRTWC8383-RL-MBL", "Here is SRTSCBD333-UF and SRTSCBD333A-UF.", [("SRTWC8122 seat cover", "Here is SRTSCBD333-UF and SRTSCBD333A-UF.")]),
    ("P5 leak", "SRTWC8383-RL-MBL technical drawing", STOCK_REPLY, [("seat cover for SRTWC8122 stock", STOCK_REPLY)]),
    ("P7 cold for", "seat cover for SRTWC8383-RL-MBL", "(none)", None),
    ("P8 stock", "check stock seat cover for SRTWC8122", "(none)", None),
    ("P6 narrow", "only the 333A one", STOCK_REPLY, [("stock seat cover for SRTWC8122", STOCK_REPLY)]),
]

def _parse(system, message, prev, recent):
    from app.services.chatbot.head import parser
    config = parser.ParserConfig(system_prompt=system, prompt_version=0, provider="openai",
        model=os.environ.get("CHAT_FIND_LIVE_MODEL", "gpt-5.4-mini"), api_key=os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected")
    block = parser.build_user_block(previous_response=prev, latest_user_message=message, pending_kind=None,
        focus=None, recent_exchanges=recent, brands=KNOWN_BRANDS)
    return dict(parser.parse(config, block))

def test_probe(db):
    system = os.environ.get("PROBE_SYSTEM_FILE")
    system = open(system).read() if system else _system(db)
    for name, message, prev, recent in CASES:
        seen = Counter()
        for _ in range(N):
            out = _parse(system, message, prev, recent)
            att = [e.get("canonical_code") for e in out.get("entities") or [] if e.get("hint") == "attachment_type"]
            find = out.get("find") if isinstance(out.get("find"), dict) else {}
            vals = [f.get("value") for f in find.get("filters") or [] if isinstance(f, dict)]
            seen[json.dumps([_keys(out), vals, out.get("domain_hint"), out.get("domain_in_message"), sorted(_product_codes(out)), att], ensure_ascii=False)] += 1
        print("PROBE", name, dict(seen))
