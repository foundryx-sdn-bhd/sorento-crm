"""C3: code builds up to 3 next-best options from structured tool results (no model involved).

Products come from the v1 turn's own resolved `product_ids` (the args of the tool v1 called for the moment) and,
beyond those, from the parser's product mentions resolved by the same resolver the advisor tools use. Tools are called through mcp_local WITHOUT `view`, which returns the raw JSON
before presenting (stock_availability / data rows, incoming data, promotion products, crm_find groups). Only the
`crm_find` fits line is taken from the tool's own `head` text, which is already a filled sentence.
Limits (dev data): stored alternatives are only structured in availability-mode (dealer) stock results; for
detailed-mode contacts there is no alternative option. Promo end date comes from crm_marketing_promotions_list.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_OPTIONS = 3
KIND_ORDER = ["alternative", "incoming", "promo", "accessory"]
#: which option the scenario's own kind cares most about (kept first when more than 3 exist)
SCENARIO_PRIORITY = {"alt_out_of_stock": "alternative", "eta_counter": "incoming", "qty_counter": "incoming",
                     "promo": "promo", "accessory_fit": "accessory"}


@dataclass
class Option:
    kind: str
    sentence: str
    evidence: list[str] = field(default_factory=list)


def _j(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def product_ids_from_v1(v1_turn: dict[str, Any], cap: int = 3) -> list[str]:
    ids: list[str] = []
    for t in v1_turn.get("tools") or []:
        for pid in (t.get("args") or {}).get("product_ids") or []:
            if isinstance(pid, str) and pid not in ids:
                ids.append(pid)
    return ids[:cap]


def product_ids_from_parse(v1_turn: dict[str, Any], scope: dict[str, Any], cap: int = 3) -> list[str]:
    """The parser's product mentions resolved with the same resolver the advisor tools use (mcp_local._resolve)."""
    from . import mcp_local

    texts: list[str] = []
    for e in (v1_turn.get("parser") or {}).get("entities") or []:
        if isinstance(e, dict) and str(e.get("hint") or "").lower() == "product":
            t = str(e.get("canonical_code") or e.get("raw") or "").strip()
            if t and t not in texts:
                texts.append(t)
    ids: list[str] = []
    for t in texts[:cap]:
        got, _msgs = mcp_local._resolve([t], "product", scope)
        ids += [i for i in got if i not in ids]
    return ids


def alternative_options(stock_raw: str) -> list[Option]:
    d = _j(stock_raw)
    out: list[Option] = []
    for entry in (d or {}).get("stock_availability") or []:
        for alt in entry.get("alternatives") or []:
            if alt.get("branch") == "in_stock" and alt.get("product_code"):
                out.append(Option("alternative", f"{alt['product_code']} is available.", [stock_raw]))
    return out[:1]


def incoming_options(incoming_raw: str, multi: bool) -> list[Option]:
    d = _j(incoming_raw)
    for row in (d or {}).get("data") or []:
        date = row.get("nearest_estimated_arrival_date") or (row.get("etas") or [None])[0]
        if date:
            prefix = f"Next arrival for {row.get('product_code')}" if multi and row.get("product_code") else "Next arrival"
            return [Option("incoming", f"{prefix}: {date}.", [incoming_raw])]
    return []


def _promo_name(desc: str) -> str:
    name = desc.strip()
    for ext in (".pdf", ".PDF"):
        if name.endswith(ext):
            name = name[: -len(ext)]
    return name


def promo_options(promo_raw: str, promotions_raw: str) -> list[Option]:
    d = _j(promo_raw)
    ends = {p.get("description"): p.get("end_date") for p in ((_j(promotions_raw) or {}).get("data") or [])}
    for row in (d or {}).get("data") or []:
        promo = row.get("promotion") or {}
        if row.get("is_expired") or not promo.get("is_active") or not promo.get("description"):
            continue
        name = _promo_name(promo["description"])
        end = ends.get(promo["description"])
        sentence = f"{name} runs until {end}." if end else f"{name} is a live promotion."
        return [Option("promo", sentence, [promo_raw, promotions_raw])]
    return []


def accessory_options(find_raw: str) -> list[Option]:
    d = _j(find_raw)
    for g in (d or {}).get("groups") or []:
        if g.get("rows") and g.get("head"):
            return [Option("accessory", str(g["head"]).strip(), [find_raw])]
    return []


def build_options(
    scenario: dict[str, Any],
    v1_turn: dict[str, Any],
    scope: dict[str, Any],
    *,
    call_tool: Callable[[str, dict[str, Any], dict[str, Any]], str] | None = None,
    calls_log: list[dict[str, Any]] | None = None,
) -> list[Option]:
    from . import mcp_local

    call_tool = call_tool or mcp_local.call_tool_raw
    pids = product_ids_from_v1(v1_turn)
    if len(pids) < 3:
        try:
            pids += [i for i in product_ids_from_parse(v1_turn, scope) if i not in pids]
        except Exception:  # noqa: BLE001 - resolver trouble must not break the arm
            pass
    pids = pids[:3]
    if not pids:
        return []

    def call(name: str, args: dict[str, Any]) -> str:
        t0 = time.monotonic()
        raw = call_tool(name, args, scope)
        if calls_log is not None:
            calls_log.append({"name": name, "args": args, "result_chars": len(raw), "result": raw[:20000],
                              "latency_ms": int((time.monotonic() - t0) * 1000)})
        return raw

    found: dict[str, list[Option]] = {}
    found["alternative"] = alternative_options(call("crm_inventory_stock_balance_list", {"product_ids": pids, "requested_qty": 1}))
    found["incoming"] = incoming_options(call("crm_incoming_stock_by_product", {"product_ids": pids}), len(pids) > 1)
    promo_raw = call("crm_marketing_promotion_products_list", {"product_ids": pids, "active": True})
    promotions_raw = call("crm_marketing_promotions_list", {"active": True, "limit": 100}) if _j(promo_raw) and (_j(promo_raw) or {}).get("data") else "{}"
    found["promo"] = promo_options(promo_raw, promotions_raw)
    found["accessory"] = accessory_options(call("crm_find", {"filters": [{"key": "fits", "product_ids": pids[:1]}]}))
    order = list(KIND_ORDER)
    first = SCENARIO_PRIORITY.get(scenario.get("scenario_kind") or "")
    if first in order:
        order.remove(first)
        order.insert(0, first)
    opts: list[Option] = []
    for k in order:
        opts += found.get(k, [])
    return opts[:MAX_OPTIONS]
