"""WA-CONCISE round 15 (red-first): the engine hands the turn localizer to the cross-domain
fold. `apply_crossdomain_hit` read `getattr(ctx, "localizer")` but the engine's ctx is a dict,
so a staff-edited label ("Kod Barang") was never known to the fold on the live path. The fix
is a `localizer=` keyword on `apply_crossdomain_hit` (and `answer_for`) that the engine passes.
Placeholder data only."""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

from app.services.chatbot import answer_bridge, label_catalog
from app.services.chatbot.label_catalog import Localizer
from app.services.chatbot.lanes.business import fetch
from app.services.chatbot.turn.compose import Answer
from tests.chatbot._wa_concise_helpers import (
    STOCK_TOOL,
    detailed_payload,
    detailed_row,
    envelope_json,
)

_XD = (
    "*Product Code:* SRTWT185-GM\n*Stock:* 0\n*Container:* IAAU1907074\n*ETA:* 2026-09-09\n"
    "*Incoming Quantity:* 200\n\U0001f6a9  *(PENDING ALLOCATION)*"
)
_LADDER_RESULT = {
    "zeroset": {},
    "render": {"_xdBlock": {"any": True, "team": "warehouse", "block": _XD}},
}


def _loc() -> Localizer:
    return Localizer("ms", {**label_catalog.defaults("ms"), "Product Code": "Kod Barang"})


def test_apply_crossdomain_hit_folds_with_the_localizer_the_engine_hands_it(monkeypatch):
    loc = _loc()
    env = json.loads(
        envelope_json(STOCK_TOOL, detailed_payload([detailed_row("Sorento", "SRTWT185-GM", "BRW", 0, 56)]))
    )
    ctx = {"semantic_input": {}, "access": {"attributes": ["inventory.sellable"]}, "tool": STOCK_TOOL, "localizer": loc}
    primary = fetch.output_structurer(env, ctx)["response"]
    monkeypatch.setattr(answer_bridge, "_run_crossdomain_ladder", lambda **_: _LADDER_RESULT)

    out = answer_bridge.apply_crossdomain_hit(
        Answer(text=primary),
        envelope={"figures": [{"fields": [{"label": "Product Code", "value": "SRTWT185-GM"}]}]},
        parser={},
        resolved={},
        entities_names=[],
        crossdomain_ladder={},
        ctx=ctx,
        services=None,
        contact_id="c1",
        space_id="s1",
        localizer=loc,
    )

    text = loc.reply(out.text)
    assert text.count("*Kod Barang:* SRTWT185-GM") == 1, text
    assert "*Stok:*" not in text, text
    assert (
        "*Kod Barang:* SRTWT185-GM\n*BRW:* 0 (O/S: 56)\n*Kontena:* IAAU1907074\n*ETA:* 2026-09-09\n"
        "*Kuantiti Masuk:* 200\n\U0001f6a9  *(MENUNGGU PERUNTUKAN)*"
    ) in text, text


def test_answer_for_miss_path_accepts_the_turn_localizer():
    assert "localizer" in inspect.signature(answer_bridge.answer_for).parameters


def test_engine_passes_its_localizer_at_the_cross_domain_hit_call_site():
    src = Path(inspect.getsourcefile(answer_bridge)).with_name("engine.py").read_text()
    start = src.index("answer_bridge.apply_crossdomain_hit(")
    end = src.index("profile=state_out.profile", start)

    assert re.search(r"localizer\s*=\s*localizer", src[start : end + 200]), src[start : end + 200]
