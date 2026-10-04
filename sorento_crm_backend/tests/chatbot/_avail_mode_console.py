"""A dealer (availability-only) console for the AVAIL-MODE-REPLIES scenario suite.

Not itself a test file (no `test_` prefix). Built on `_r9_engine_console.EngineConsole`:
the engine, the resolver over seeded codes, the MCP presenter and `fetch.output_structurer`
are REAL; the parser verdict and the two tool reads are stubbed. Unlike the round 9
console, the stock tool answers per code from `outcomes` through the REAL decision
(`app.services.stock_ask_branch.branch` / `short_of`), so a scenario states the stock
facts ("SRT5674 has 30, cap 100") and reads the reply the dealer would get.

Plan: documentation/plans/chatbot/PLAN-avail-mode-replies-02oct.md. Catalogue of every
scenario: tests/chatbot/AVAIL-MODE-SCENARIOS.md.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.eta_policy import dealer_view
from app.services.stock_ask_branch import branch, short_of

from tests.chatbot._r9_engine_console import EngineConsole, _present_response
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO

#: The codes every scenario can name, beside the round 9 console's own (the SRTWC286
#: family, SRTWC287-S-150, ELP3754). Real Sorento codes from the hand tests and plan
#: samples; SRTW2000 has a shipment, MWT5727SS-CR has none.
CODES = [
    "SRT5674",
    "CWCX604",
    "SRTW2000",
    "MWT5727SS-CR",
    # A second family ("check stock srtwc6022" placed both, answer_bridge hand pass 11).
    "SRTWC6022-SH-UF",
    "SRTWC6022-SH-UF-NEW",
    # Owner hand test, 3 Oct 2026: the did-you-mean for "srt5764", the SRTW2000 family
    # the bare "eta" follow-up expanded to, and catalogue rows a stray "2" matches.
    "SRT57-CR",
    "SRT5713",
    "SRT5732",
    "SRTW2000-SS-CR",
    "SRTW2000-A",
    "SRTW2000-NL",
    "2001",
    "2002",
    "2120H",
    "1/2 ULTRA CIRCULAR",
    "32MM TAIL PIECE COUPLING",
]


@dataclass
class Stock:
    on_hand: int = 0
    x: int = 100
    eta: date | None = None


def _seed(session_factory, codes: list[str]) -> dict[str, str]:
    from app.models.product import Product, ProductCategory, UnitOfMeasure

    db = session_factory()
    db.info["company_scope"] = frozenset({SORENTO})
    tag = uuid.uuid4().hex[:6]
    cat = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=f"ZZTC-am-{tag}",
        category_name="ZZT AM",
        class_label="am",
        search_synonyms=[],
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=f"ZZTU-am-{tag}", uom_name="ZZT uom")
    db.add_all([cat, uom])
    db.flush()
    out: dict[str, str] = {}
    for code in codes:
        pid = str(uuid.uuid4())
        out[pid] = code
        db.add(
            Product(
                id=pid,
                product_code=code,
                product_name=f"ZZT {code}",
                category_id=cat.id,
                base_uom_id=uom.id,
                list_price=1,
            )
        )
    db.commit()
    return out


class AvailConsole(EngineConsole):
    def __init__(self, session_factory, monkeypatch, stub_access, *, phone: str, **stock: Stock) -> None:
        super().__init__(session_factory, monkeypatch, stub_access, phone=phone)
        self.codes.update(_seed(session_factory, CODES))
        self.uuid_of = {code: pid for pid, code in self.codes.items()}
        #: Code -> its stock facts. A code not named here has nothing on hand, X 100.
        self.outcomes: dict[str, Stock] = {k.replace("_", "-"): v for k, v in stock.items()}
        present = _present_response()

        def fake_call_tool(client, name: str, args: dict[str, Any]) -> str:
            self.tool_calls.append((name, args))
            ids = [pid for pid in args.get("product_ids") or [] if pid in self.codes]
            if name in ("crm_incoming_stock_by_product", "crm_incoming_stock_list"):
                # The route's real shape (tester-local pass on 7fa5d654, step 15): only a
                # product WITH a shipment has a row, its date ISO, and the REAL
                # `eta_policy.dealer_view` builds what the dealer is told from it, with
                # the codes asked.
                rows = []
                for pid in ids:
                    eta = self._facts(self.codes[pid]).eta
                    if eta:
                        rows.append(
                            {"product_code": self.codes[pid], "estimated_arrival_date": eta.isoformat()}
                        )
                told = dealer_view({"data": rows}, asked=[self.codes[pid] for pid in ids])
                return present(name, json.dumps(told))
            if name != "crm_inventory_stock_balance_list":
                return json.dumps({"answers": []})
            wanted = args.get("requested_quantities") or {}
            if isinstance(wanted, str):
                wanted = json.loads(wanted)
            entries = []
            for pid in ids:
                code = self.codes[pid]
                qty = wanted.get(pid)
                facts = self._facts(code)
                entry = {
                    "product_id": pid,
                    "product_code": code,
                    "product_name": code,
                    "needs_quantity": qty is None,
                    "requested_qty": qty,
                    "branch": None,
                    "cap_unset": False,
                    "category_name": "ZZT AM",
                    "eta": None,
                    "packing_list": None,
                    "available_qty": None,
                }
                if qty is not None:
                    entry["branch"] = branch(qty, facts.x, facts.on_hand, facts.eta)
                    entry["available_qty"] = short_of(qty, facts.x, facts.on_hand)
                    if entry["branch"] == "incoming":
                        entry["eta"] = facts.eta.strftime("%d/%m/%Y")
                entries.append(entry)
            payload = {
                "data": [],
                "pagination": {"total": 0, "page": 1, "limit": 50},
                "empty": True,
                "stock_visibility": {"mode": "availability", "warehouse_codes": None, "source": "contact"},
                "stock_availability": entries,
                "last_updated_at": "2026-10-02T09:00:00",
            }
            return present(name, json.dumps(payload))

        monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)

    def _facts(self, code: str) -> Stock:
        return self.outcomes.get(code, Stock())

    @property
    def stock_calls(self) -> list[dict[str, Any]]:
        return [args for name, args in self.tool_calls if name == "crm_inventory_stock_balance_list"]
