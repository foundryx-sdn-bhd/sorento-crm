"""CHAT-FIND MCP surface (M1, M2): the `crm_find` catalog entry and its presenter.

Plan: documentation/plans/chatbot/PLAN-chat-generic-find-12oct.md sections 2.3 and 4.

* M1 the ToolSpec, and `filters` (a list of dicts) crossing FastMCP as compact JSON.
* M2 `present_response("crm_find", <route body>)`: head line, `Code:` / `Status:` / `Stock:`
  lines, the availability verdict wording, the none line, `Showing 20 of N.`, never " · ".
"""
from __future__ import annotations

import contextlib
import json

import pytest
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext

from sorento_crm_mcp import presenters
from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.server import _normalize_query_value, create_mcp_app
from sorento_crm_mcp.settings import Settings

TOOL = "crm_find"
WC = "SRTWC8383-RL-MBL"
COVER = "SRTSCBD823-MBL"

FILTERS = [{"key": "fits", "product_ids": ["00000000-0000-0000-0000-0000000000a1"]}]
COMPACT_FILTERS = json.dumps(FILTERS, separators=(",", ":"), sort_keys=True)


# --------------------------------------------------------------------------- M1


def _spec():
    matches = [s for s in CATALOG if s.name == TOOL]
    assert len(matches) == 1, f"expected exactly one {TOOL} entry in CATALOG"
    return matches[0]


def test_m1_catalog_entry() -> None:
    spec = _spec()
    assert spec.method == "GET"
    assert spec.path == "/api/v1/master-data/products/find"
    assert tuple(spec.query_params) == ("filters", "contact_id", "space_id")
    assert spec.body_params == ()
    assert spec.domain == "products"
    assert spec.escalation_team == "sales"
    assert TOOL in presenters.PRESENTER_TOOLS


def test_m1_a_list_of_dicts_is_compact_sorted_json_on_the_wire() -> None:
    assert _normalize_query_value(FILTERS) == COMPACT_FILTERS
    # Key order in the caller's objects must not change the string the backend receives.
    reordered = [{"product_ids": FILTERS[0]["product_ids"], "key": "fits"}]
    assert _normalize_query_value(reordered) == COMPACT_FILTERS


def test_m1_a_list_of_strings_still_passes_through_as_a_list() -> None:
    assert _normalize_query_value(["a", "b"]) == ["a", "b"]


class _FakeSettings:
    crm_base_url = "http://crm.local"
    external_api_key = "test-key"


class _CapturingClient:
    def __init__(self):
        self.query = None

    async def get(self, path, path_params=None, query=None, tool_name=None):
        return await self.request("GET", path, path_params, query, None, tool_name)

    async def request(self, method, path, path_params=None, query=None, body=None, tool_name=None):
        self.query = dict(query or {})
        return json.dumps({"groups": [], "stock_visibility": None})


@contextlib.contextmanager
def _request_context(client):
    token = request_ctx.set(
        RequestContext(
            request_id=1,
            meta=None,
            session=None,  # type: ignore[arg-type]
            lifespan_context={"client": client, "settings": _FakeSettings()},
        )
    )
    try:
        yield
    finally:
        request_ctx.reset(token)


async def _call(filters):
    app = create_mcp_app(Settings(CRM_BASE_URL="http://crm.local", EXTERNAL_API_KEY="k"))
    client = _CapturingClient()
    with _request_context(client):
        await app.call_tool(TOOL, {"filters": filters, "contact_id": "1", "space_id": "s"})
    assert client.query is not None
    return client.query


@pytest.mark.asyncio
async def test_m1_a_native_list_reaches_the_backend_as_compact_json() -> None:
    query = await _call(FILTERS)
    assert query["filters"] == COMPACT_FILTERS


@pytest.mark.asyncio
async def test_m1_a_json_string_survives_fastmcp_preparse_and_arrives_compact() -> None:
    query = await _call(json.dumps(FILTERS, indent=2))
    assert query["filters"] == COMPACT_FILTERS


# --------------------------------------------------------------------------- M2


def _row(code, *, discontinued=False, stock=None, name=None):
    return {
        "product_id": f"id-{code}",
        "product_code": code,
        "product_name": name or code,
        "is_discontinued": discontinued,
        "stock": stock or {"mode": "detailed", "on_hand": 12, "verdict": None},
    }


def _group(head, rows, *, anchor=WC, relation="covers", more=0):
    return {
        "filter": "fits",
        "anchor": {"product_id": f"id-{anchor}", "product_code": anchor},
        "relation": relation,
        "head": head,
        "rows": rows,
        "more": more,
    }


def _body(*groups, visibility=None):
    return {"groups": list(groups), "stock_visibility": visibility}


def _present(body) -> dict:
    return json.loads(presenters.present_response(TOOL, json.dumps(body)))


def test_m2_a_row_prints_code_and_stock_one_fact_per_line() -> None:
    env = _present(_body(_group(f"Seat cover for {WC}:", [_row(COVER)])))
    assert env["response"].split("\n") == [f"Seat cover for {WC}:", f"Code: {COVER}", "Stock: 12"]
    assert env["has_result"] is True
    assert env["result_type"] == "find"


def test_m2_a_discontinued_row_marks_status_before_stock() -> None:
    env = _present(_body(_group(f"Seat cover for {WC}:", [_row(COVER, discontinued=True)])))
    lines = env["response"].split("\n")
    assert lines[1:] == [f"Code: {COVER}", "Status: Discontinued", "Stock: 12"]


def test_m2_rows_and_groups_are_separated_by_a_blank_line() -> None:
    second = "SRTSCBD333A-UF"
    env = _present(
        _body(
            _group(f"Seat cover for {WC}:", [_row(COVER), _row(second, stock={"mode": "detailed", "on_hand": 4, "verdict": None})]),
            _group(f"{COVER} fits these WCs:", [_row(WC)], anchor=COVER, relation="wcs"),
        )
    )
    assert env["response"] == (
        f"Seat cover for {WC}:\n"
        f"Code: {COVER}\nStock: 12\n"
        "\n"
        f"Code: {second}\nStock: 4\n"
        "\n"
        f"{COVER} fits these WCs:\n"
        f"Code: {WC}\nStock: 12"
    )


def test_m2_compact_and_none_modes_print_the_number() -> None:
    env = _present(
        _body(
            _group(
                f"Seat cover for {WC}:",
                [
                    _row(COVER, stock={"mode": "compact", "on_hand": 34, "verdict": None}),
                    _row("SRTSCBD333-UF", stock={"mode": "none", "on_hand": 0, "verdict": None}),
                ],
            )
        )
    )
    assert "Stock: 34" in env["response"].split("\n")
    assert "Stock: 0" in env["response"].split("\n")


def test_m2_availability_prints_the_verdict_wording_and_no_number() -> None:
    verdict = {"branch": "in_stock", "eta": None, "available_qty": None}
    env = _present(
        _body(
            _group(
                f"Seat cover for {WC}:",
                [_row(COVER, stock={"mode": "availability", "on_hand": None, "verdict": verdict})],
            ),
            visibility={"mode": "availability"},
        )
    )
    assert "Stock: ✅ Got stock." in env["response"].split("\n")
    assert "None" not in env["response"]


def test_m2_availability_uses_the_stock_tools_own_verdict_tail() -> None:
    for verdict in (
        {"branch": "incoming", "eta": "20 Oct 2026", "available_qty": None},
        {"branch": "no_incoming", "eta": None, "available_qty": None},
        {"branch": "in_stock", "eta": None, "available_qty": 3},
    ):
        env = _present(
            _body(
                _group(
                    f"Seat cover for {WC}:",
                    [_row(COVER, stock={"mode": "availability", "on_hand": None, "verdict": verdict})],
                )
            )
        )
        assert f"Stock: {presenters._verdict_tail(verdict)}" in env["response"].split("\n"), verdict


def test_m2_a_none_group_is_just_its_head_line() -> None:
    none_line = f"No seat cover or WC is on record for {WC}."
    env = _present(_body(_group(none_line, [], relation=None)))
    assert env["response"] == none_line
    assert env["has_result"] is False


def test_m2_more_prints_showing_twenty_of_n() -> None:
    rows = [_row(f"SRTSC{i:02d}") for i in range(20)]
    env = _present(_body(_group(f"Seat cover for {WC}:", rows, more=3)))
    assert env["response"].split("\n")[-1] == "Showing 20 of 23."


def test_m2_has_result_is_false_only_when_no_group_has_rows() -> None:
    empty = _group(f"No seat cover or WC is on record for {WC}.", [], relation=None)
    assert _present(_body(empty))["has_result"] is False
    assert _present(_body())["has_result"] is False
    full = _group(f"Seat cover for {COVER}:", [_row(WC)], anchor=COVER)
    assert _present(_body(empty, full))["has_result"] is True


def test_m2_never_a_middle_dot() -> None:
    verdict = {"branch": "incoming", "eta": "20 Oct 2026", "available_qty": None}
    env = _present(
        _body(
            _group(
                f"Seat cover for {WC}:",
                [
                    _row(COVER, discontinued=True),
                    _row("SRTSCBD333-UF", stock={"mode": "availability", "on_hand": None, "verdict": verdict}),
                ],
                more=2,
            )
        )
    )
    assert " · " not in env["response"]
    assert "·" not in env["response"]
