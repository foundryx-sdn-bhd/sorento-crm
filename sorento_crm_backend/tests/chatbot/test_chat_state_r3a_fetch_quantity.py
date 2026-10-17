"""CHAT-STATE round 3, R3-A (red): the quantity the State holds reaches the stock FETCH.

A contact asked "SRT1 x 5 stock?" and was answered; the next message refines, keeps or
re-asks that product without a quantity. This turn's `crm_inventory_stock_balance_list`
call must carry `requested_quantities` for the held product's own code, so the bot does
not ask "How many units?". Round 2 stopped at `apply_changes` and went green while the
carry never reached the fetch: every assertion here reads the arguments of the MCP call.

Real members: cp0-b5eb30e1b9, cp0-f14dc9c818, cp0-253d58ba8b, cp0-665ba3e897,
es-sdac84c3f5, es-s5fbda6317 (codes and quantities taken from their recorded prior turn).
The parser output is stubbed; the code executes. Owner ruling 18 Oct ("don't re-ask what
the user gave"): a refine of the SAME held subject that lands on several family codes
carries the one held quantity onto every code it lands on; never onto a new subject or
another product the message names.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.state import Focus, focus_to_wire
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot._turn_helpers import entity
from tests.chatbot.test_chat_state_seam import Seam, _emit, _op, _product, seam  # noqa: F401
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access  # noqa: F401
from tests.chatbot._r9_engine_console import SORENTO

STOCK = "crm_inventory_stock_balance_list"


def _wanted(calls: list[tuple[str, dict[str, Any]]]) -> dict[str, int]:
    """Every `requested_quantities` the turn's stock fetches carried, merged."""
    merged: dict[str, int] = {}
    for name, args in calls:
        if name != STOCK:
            continue
        wanted = args.get("requested_quantities") or {}
        if isinstance(wanted, str):
            wanted = json.loads(wanted)
        merged.update({k: int(v) for k, v in wanted.items()})
    return merged


def _fetched(calls) -> set[str]:
    return {pid for name, args in calls if name == STOCK for pid in args.get("product_ids") or []}


def _reply(out) -> str:
    return ((out.reply or {}).get("text") or "").lower()


# --------------------------------------------------------------------------- #
# Group A: the same held product(s), refined / kept / re-asked, no quantity stated
# --------------------------------------------------------------------------- #


def _seed_more(seam: Seam, codes: list[str]) -> dict[str, str]:
    """Extra catalogue rows beside the harness's own (own category, own uom)."""
    db = seam.session_factory()
    db.info["company_scope"] = frozenset({SORENTO})
    cat = ProductCategory(id=str(uuid.uuid4()), category_code=unique_code("R3A")[:50], category_name="ZZT R3A",
                          class_label="r3a", search_synonyms=[])
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("U")[:50], uom_name="ZZT uom")
    db.add_all([cat, uom])
    db.flush()
    ids: dict[str, str] = {}
    for code in codes:
        pid = str(uuid.uuid4())
        ids[code] = pid
        db.add(Product(id=pid, product_code=code, product_name=f"ZZT {code}", category_id=cat.id,
                       base_uom_id=uom.id, list_price=1))
    db.commit()
    seam.uuid_of.update(ids)
    return ids


def _answered(seam: Seam, held: list[tuple[str, int]], *, in_task: bool = True, in_row: bool = False) -> dict[str, Any]:
    """The State after an answered stock check of `held` ([(code, qty)])."""
    rows, slots = [], []
    for code, qty in held:
        key = seam.uuid_of[code]
        rows.append({"raw": code, "canonical_code": code, "hint": "product", "uuid": key, "current_message": False,
                     **({"quantity": qty} if in_row else {})})
        slots.append(task_mod.Slot(key, code, qty))
    tasks = (task_mod.Task(kind="stock_qty", domain="inventory", status=task_mod.ANSWERED, slots=tuple(slots)),) if in_task else ()
    return {"focus": focus_to_wire(Focus(products=rows, domains=["inventory"], tasks=tasks)), "open_question": None}


def _spec(raw: str, key: str, value: str) -> dict[str, Any]:
    return entity(raw, "specification", canonical_code=value, spec_key=key, spec_value=value)


def _ask(*entities, changes=None, **extra) -> dict[str, Any]:
    return _emit(domain_hint="inventory", intent_hint="check_stock", entities=list(entities),
                 state_changes=changes if changes is not None else [_op("keep")], **extra)


# (id, message, held [(code, qty)], verdict entities, state_changes, extra)
KEEP_CASES = [
    ("cp0_b5eb30e1b9_black_also", "Black also?", [("SRTSA840-BL", 1)], [_spec("black", "colour", "black")], None, {}),
    ("cp0_f14dc9c818_hv_stock", "Ok, hv stock?", [("CSH2072", 72)], [], None, {}),
    ("es_sdac84c3f5_stainless", "stainless steel", [("SRTSC04-BL", 4), ("SRTSC05-GM", 1), ("SRTSS8770-BL", 5)],
     [_spec("stainless steel", "material", "stainless_steel")], None, {}),
    ("es_s5fbda6317_want_grey", "want grey",
     [("SRT36525-GY", 1), ("SRTWB1409", 1), ("SRT93-BL", 1), ("SRTWT7449-BL", 1)],
     [_spec("grey", "colour", "grey")], None, {}),
    ("en_check_again", "check again", [("SRT1", 5)], [], None, {}),
    ("bm_ada_putih", "ada yang putih?", [("SRT391-GM", 12)], [_spec("putih", "colour", "white")], None, {}),
    ("zh_brand", "有没有Mocha的?", [("SRTWC286-SH", 3)], [entity("Mocha", "brand", canonical_code="MOCHA")], None, {}),
    ("mixed_size", "yg 465mm boleh?", [("SRTKT1631SS", 8)], [_spec("465mm", "size", "465")], None, {}),
    ("bare_code_retyped", "SRT1", [("SRT1", 5)], [_product("SRT1")], [], {}),
    ("bare_code_retyped_keep_bm", "srt391-gm lagi", [("SRT391-GM", 20)], [_product("SRT391-GM")], [_op("keep")], {}),
]
ALL_KEEP_CODES = sorted({c for case in KEEP_CASES for c, _ in case[2]})


@pytest.mark.parametrize("message,held,ents,changes,extra", [c[1:] for c in KEEP_CASES], ids=[c[0] for c in KEEP_CASES])
def test_r3a_a_refine_keep_or_reask_of_the_held_product_fetches_with_its_held_quantity(seam, message, held, ents, changes, extra):
    _seed_more(seam, [c for c, _ in held if c not in seam.uuid_of])
    prev = _answered(seam, held)
    out = seam.say(message, _ask(*ents, changes=changes, **extra), previous=prev)
    calls = seam.turn_calls[-1]
    want = {seam.uuid_of[c]: q for c, q in held}
    assert _wanted(calls) == want, (calls, _reply(out))
    assert "how many" not in _reply(out)


def test_r3a_the_quantity_held_only_on_the_product_row_also_reaches_the_fetch(seam):
    prev = _answered(seam, [("SRT1", 5)], in_task=False, in_row=True)
    out = seam.say("check again", _ask(), previous=prev)
    assert _wanted(seam.turn_calls[-1]) == {seam.uuid_of["SRT1"]: 5}, seam.turn_calls[-1]
    assert "how many" not in _reply(out)


def test_r3a_an_explicit_clear_of_the_quantity_carries_nothing_to_the_fetch(seam):
    prev = _answered(seam, [("SRT1", 5)], in_row=True)
    seam.say("just check stock, any quantity", _ask(changes=[_op("keep"), _op("clear", "quantity")]), previous=prev)
    assert _wanted(seam.turn_calls[-1]) == {}, seam.turn_calls[-1]


def test_r3a_a_quantity_fills_only_its_own_code_not_another_product_the_message_names(seam):
    prev = _answered(seam, [("SRT1", 5)])
    seam.say("and SRT391-GM?", _ask(_product("SRT391-GM"), changes=[_op("add", "product")]), previous=prev)
    wanted = _wanted(seam.turn_calls[-1])
    assert seam.uuid_of["SRT391-GM"] not in wanted, wanted


def test_r3a_a_new_subject_never_gets_the_old_quantity_in_the_fetch(seam):
    prev = _answered(seam, [("SRT1", 5)])
    seam.say("SRT391-GM stock", _ask(_product("SRT391-GM"), changes=[_op("new_subject"), _op("set", "product")]), previous=prev)
    assert _wanted(seam.turn_calls[-1]) == {}, seam.turn_calls[-1]


def test_r3a_a_quantity_stated_in_this_message_wins_in_the_fetch(seam):
    prev = _answered(seam, [("SRT1", 5)])
    seam.say("SRT1 x 9 in black", _ask(_product("SRT1") | {"quantity": 9}, _spec("black", "colour", "black")), previous=prev)
    assert _wanted(seam.turn_calls[-1]) == {seam.uuid_of["SRT1"]: 9}, seam.turn_calls[-1]


# --------------------------------------------------------------------------- #
# Group B (owner ruling 18 Oct): a refine of the same held subject landing on several
# family codes carries the one held quantity onto every code it lands on
# --------------------------------------------------------------------------- #

FAMILIES = [
    # (id, message, parent, qty, matching children (nested ok), non-matching child)
    ("cp0_253d58ba8b_if_p_trap", "If p trap?", "MWC6603-S8", 5,
     ["MWC7601-RL-P", "MWC7609-RL-P", "MWC7612-P", "MWC7614-RL-P"], "MWC7610-RL"),
    ("cp0_665ba3e897_how_about_p_trap", "How about p trap", "SRTWC286-SH", 10,
     ["SRTWC286-SH-P", "SRTWC286-SH-P-150", "SRTWC286-SH-P-200"], "SRTWC286-SH-NEW"),
    ("bm_nak_p_trap", "nak p trap", "MWC7617-SH-UF", 3, ["MWC7617-SH-UF-P", "MWC7632-SH-UF-P"], "MWC7617-SH-UF-S"),
    ("zh_p_trap", "要P trap的", "IBWC7601-RL", 8, ["IBWC7601-RL-P", "IBWC7601-RL-P-X", "IBWC7601-RL-P-Y"], "IBWC7601-RL-S"),
    ("mixed_ada_p_trap", "ada p trap tak?", "MWCX7604-RL", 2, ["MWCX7604-RL-P", "MWCX7605-RL-P"], "MWCX7606-RL"),
]


def _seed_family(session_factory, parent: str, children: list[str], other: str) -> dict[str, str]:
    db = session_factory()
    db.add(ProductSpecRegistry(
        id=str(uuid.uuid4()), spec_key="trap_type", label="Trap", data_type="enum",
        allowed_values=["s_trap", "p_trap"], synonyms={"p_trap": ["p trap"], "s_trap": ["s trap"]},
    ))
    cat = ProductCategory(id=str(uuid.uuid4()), category_code=unique_code("WC")[:50], category_name="ZZT WC",
                          class_label="Water Closet", company_id=DEFAULT_COMPANY_ID)
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom])
    db.flush()
    ids: dict[str, str] = {}
    rows = [(parent, None, "s_trap"), (other, parent, "s_trap")]
    rows += [(c, parent, "p_trap") for c in children]
    for code, up, trap in rows:
        p = Product(id=str(uuid.uuid4()), product_code=code, product_name=f"Name {code}", category_id=cat.id,
                    base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID,
                    variant_of_id=ids.get(up) if up else None)
        db.add(p)
        db.flush()
        ids[code] = p.id
        db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=p.id,
                                     values={"class": {"value": "Water Closet"}, "trap_type": {"value": trap}}))
    db.commit()
    return ids


def _family_turn(session_factory, stub_access, monkeypatch, message, ids, parent, qty, verdict):
    key = ids[parent]
    row = {"raw": parent, "canonical_code": parent, "hint": "product", "uuid": key, "current_message": False}
    task = task_mod.Task(kind="stock_qty", domain="inventory", status=task_mod.ANSWERED,
                         slots=(task_mod.Slot(key, parent, qty),))
    prev = {"focus": focus_to_wire(Focus(products=[row], domains=["inventory"], tasks=(task,))), "open_question": None}

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(system_prompt="stub", prompt_version=1, provider="openai", model="m",
                                       api_key="k", closed_lists=True)

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: verdict)
    for k in parser_mod.DECLARED_KEYS:
        verdict.setdefault(k, None)
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda f, c: frozenset({DEFAULT_COMPANY_ID}))
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(MCPRuntimeClient, "call_tool",
                        lambda self, name, args: calls.append((name, args)) or json.dumps({"ok": True, "items": []}))
    stub_access()
    msg = {"event_type": "message.received", "contact": {"id": CONTACT_ID},
           "message": {"messageId": f"ZZT-r3a-{uuid.uuid4().hex[:8]}", "contactId": CONTACT_ID, "channelId": "whatsapp",
                       "traffic": "incoming", "message": {"type": "text", "text": message}}}
    out = engine_mod.run_turn(_envelope(is_test=True, mock_reformulator_output=verdict, message=msg, previous_conversation_state=prev), session_factory=session_factory)
    assert out.error is None, out.error
    return out, calls


P_TRAP = {"raw": "p trap", "hint": "specification", "canonical_code": None, "spec_key": "trap_type",
          "spec_value": "p_trap", "current_message": True, "confident": True}


@pytest.mark.parametrize("message,parent,qty,children,other", [c[1:] for c in FAMILIES], ids=[c[0] for c in FAMILIES])
def test_r3a_a_refine_landing_on_several_family_codes_carries_the_one_held_quantity_to_each(
    session_factory, seeded, stub_access, monkeypatch, message, parent, qty, children, other
):
    ids = _seed_family(session_factory, parent, children, other)
    verdict = _parser_output(intent_hint="check_stock", domain_hint="inventory", user_goal="stock", entities=[dict(P_TRAP)],
                             state_changes=[_op("keep")])
    out, calls = _family_turn(session_factory, stub_access, monkeypatch, message, ids, parent, qty, verdict)
    landed = {ids[c] for c in children}
    assert _fetched(calls) == landed, (calls, _reply(out))
    assert _wanted(calls) == {pid: qty for pid in landed}, (calls, _reply(out))
    assert "how many" not in _reply(out)


def test_r3a_a_family_refine_that_names_another_product_gives_that_product_no_quantity(
    session_factory, seeded, stub_access, monkeypatch
):
    ids = _seed_family(session_factory, "MWC6603-S8", ["MWC7601-RL-P", "MWC7609-RL-P"], "MWC7610-RL")
    other = _seed_more_plain(session_factory, "MWC9602-P")
    named = {"raw": "MWC9602-P", "hint": "product", "canonical_code": "MWC9602-P", "current_message": True, "confident": True}
    verdict = _parser_output(intent_hint="check_stock", domain_hint="inventory", user_goal="stock",
                             entities=[named, dict(P_TRAP)], state_changes=[_op("keep")])
    _out, calls = _family_turn(session_factory, stub_access, monkeypatch, "MWC9602-P p trap?", ids, "MWC6603-S8", 5, verdict)
    assert other not in _wanted(calls), calls


def test_r3a_a_family_refine_after_an_explicit_clear_carries_nothing(session_factory, seeded, stub_access, monkeypatch):
    ids = _seed_family(session_factory, "MWC6603-S8", ["MWC7601-RL-P", "MWC7609-RL-P"], "MWC7610-RL")
    verdict = _parser_output(intent_hint="check_stock", domain_hint="inventory", user_goal="stock",
                             entities=[dict(P_TRAP)],
                             state_changes=[_op("clear", "quantity")])
    _out, calls = _family_turn(session_factory, stub_access, monkeypatch, "p trap, any quantity", ids, "MWC6603-S8", 5, verdict)
    assert _wanted(calls) == {}, calls


def _seed_more_plain(session_factory, code: str) -> str:
    db = session_factory()
    cat = ProductCategory(id=str(uuid.uuid4()), category_code=unique_code("WC2")[:50], category_name="ZZT WC2",
                          class_label="Water Closet", company_id=DEFAULT_COMPANY_ID)
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM2")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom])
    db.flush()
    pid = str(uuid.uuid4())
    db.add(Product(id=pid, product_code=code, product_name=f"Name {code}", category_id=cat.id, base_uom_id=uom.id,
                   list_price=1, company_id=DEFAULT_COMPANY_ID))
    db.commit()
    return pid
