"""RED tests for S6b - the business lane's fetch step (AC-604 to AC-606).

Written BEFORE `app/services/chatbot/lanes/business/fetch.py` exists (Phase 2, test-first).
Every test below is expected to fail today with an `ImportError` / `AttributeError` naming
that module or one of its functions - that is the correct red reason. A failure for any
other reason (a typo in a fixture path, a wrong assertion) is a defect in THIS file, not
evidence the port is done.

Scope: `sub-fetch-results` (tool pick -> tool-filter -> tier probe -> entity-ids-transformer
-> MCPRuntimeClient -> output-structurer -> fetch-result). S6a's resolve+gate (already merged
into this branch) is the caller; S6c's answer/miss lane is NOT built yet, so the "result" arm
of `fetch_result` only hands the fetch payload back to n8n via `delegate_payload` - it does not
render a customer-facing answer.

Hazards: H11 (zero tools must not silently produce an empty turn), H43 (moot - the in-process
call binds `domain` directly, there is no missing `$4`), H46 (`_isTimeline` is a CONTAINS
check, not an equality check - `contracts.is_timeline` is the one place this is decided and
S6b must consume it, not re-derive it), H49 (verify the live tool-selection distribution before porting
any per-tool branch - `crm_order_management_orders_by_product_list` was never selected in the
captures graded so far), H52 (the MCP call goes through the CONFIGURED url,
`settings.ai_assistant_mcp_url`, never a literal IP or scheme), H53 (there is no tool
search at all since 8 Sep 2026 - the tool is read off `contracts.DOMAIN_SPEC` - so this
package still issues no SQL and names no table).

**Contract this file assumes and asserts** (S6a set the precedent - see
`lanes/business/services.py`'s `ResolveGateServices` and its own `_probe()` docstring, which
names this exact seam as "S6b's fetch, over MCPRuntimeClient (D10)"):

    app.services.chatbot.lanes.business.fetch
        select_tool(domain: str | None) -> list[{"name": str, "similarity": float}]
            One candidate, `DOMAIN_SPEC[domain].tools[0]`, or none. `select_tool`'s own
            per-domain grading lives in `test_tool_pick_from_domain_spec.py`.
        tool_filter(candidates: list[dict], *, has_product: bool | None) -> ToolPick
            ToolPick.items  : the n8n item list, [] or [{"json": {...best, "_tool_pick": {...}}}]
                              - BYTE-EQUAL to today (D8 parity)
            ToolPick.outcome: "picked" | "not_found" - H11's fix, ALWAYS distinguishable
        tier_probe_plan(tier_gate: dict) -> list[dict]           (n8n item-list shape)
        tier_probe_collect(tier_gate: dict, *, plan_items, probe_results) -> dict
        entity_ids_transformer(trigger: dict) -> dict[str, Any]  (tool call args)
        call_tool(name: str, args: dict, *, mcp) -> Any           (thin pass-through, H52)
        output_structurer(result: dict, ctx: dict) -> dict        (deterministic, H7)
        fetch_result(item: dict, *, tool=None, tier_probe=None) -> dict  (adds `_fetch_arm`)

    app.services.chatbot.lanes.business.services
        FetchServices(mcp_call)  - dataclass, same shape as `ResolveGateServices`. It
        carried `embed` / `tool_search` until the tool RAG was dropped.

    app.services.chatbot.lanes.business (package init, alongside `run_until_exit`)
        run_fetch(payload, *, services: FetchServices, dry_run: bool) -> dict
            The next call site after `run_until_exit`'s "continue" exit: dispatches on
            `_fetch_arm` (see `TestEngineDispatch` below).

Where the plan/UAC under-specifies an exact function name, this docstring is the tester's own
choice, made explicit so the coder can push back on it rather than silently drifting from it.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

from tests.chatbot import _corpus

FETCH_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "services"
    / "chatbot"
    / "lanes"
    / "business"
    / "fetch.py"
)


def _fetch_source() -> str:
    """The fetch module's own source text, for the static hazard checks (H52, H53).

    Read as TEXT rather than imported, so the static checks below can run and report their
    real reason even while the module does not exist yet or fails to import.
    """
    if not FETCH_MODULE_PATH.exists():
        pytest.fail(
            f"{FETCH_MODULE_PATH} does not exist yet - S6b's fetch.py has not been written. "
            "This is the expected RED reason for every test in this file."
        )
    return FETCH_MODULE_PATH.read_text(encoding="utf-8")


def _import_fetch():
    from app.services.chatbot.lanes.business import fetch as fetch_mod

    return fetch_mod


def _import_fetch_services():
    from app.services.chatbot.lanes.business.services import FetchServices

    return FetchServices


def _import_migration_312():
    """`312_container_status_checkpoints` starts with a digit, so it cannot be a
    normal dotted import - load it straight off its path, same pattern as
    `tests/test_container_status_checkpoints.py`."""
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "312_container_status_checkpoints.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_312", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# --------------------------------------------------------------------------- #
# AC-604 - tool selection: the domain's own first tool, H11 on an empty pick
# --------------------------------------------------------------------------- #


class TestToolSearch:
    def test_the_pick_is_a_table_read_with_no_seam_and_no_sql(self):
        """`select_tool` reads `contracts.DOMAIN_SPEC` and touches nothing else.

        H53: `sub-get-rag`'s pgvector query stayed retired and the service call that
        replaced it is now gone too - measured over the 740 business turns in the 7 Sep
        2026 prod copy, the similarity pick was the domain's FIRST-LISTED tool on every
        turn, so the search was deciding a question with one answer (and could not even
        run in the deployed image, whose backend has no MCP catalogue to seed from).
        Per-domain grading lives in `test_tool_pick_from_domain_spec.py`; what this file
        keeps is the static hazard check.

        H43: the n8n query's `$4` is `domain`, LIKE-matched against `source_id`, and is
        genuinely missing on some call sites live (the hazard). It cannot exist here:
        `domain` is the only parameter, so `domain=None` means "no tool" by construction,
        never "the caller forgot to wire a parameter".
        """
        fetch = _import_fetch()

        assert fetch.select_tool("master_products") == [
            {"name": "crm_master_products_list", "similarity": 1.0}
        ]
        assert fetch.select_tool(None) == []

        source = _fetch_source()
        assert "embedding_chunks" not in source, "fetch.py must not name the raw table (H53)"
        assert not re.search(r"\bSELECT\b", source, re.IGNORECASE), (
            "fetch.py must not issue SQL directly - the tool is read off DOMAIN_SPEC (H53)"
        )

    # ----------------------------------------------------------------------- #
    # F4 (review, 7 Sep 2026) - the incoming-shipments-to-list collapse. It was a
    # policy seam in `services.py` that renamed `crm_incoming_stock_shipments` when it
    # won the similarity pick; there is no similarity pick left, so the rule it
    # enforced is now a property of the domain table itself.
    # ----------------------------------------------------------------------- #

    def test_an_incoming_turn_always_reads_the_list_tool(self):
        """Evidence turn 147d6888-d313-4612-a32f-364cec119ec4: "incoming TIIU6323920"
        picked `crm_incoming_stock_shipments` (0.4675) over `crm_incoming_stock_list`
        (0.4537). The shipments tool's header carries no clearance checkpoints and no
        `field_access` block, so it can never render the container timeline - only the
        list tool can, and only the list tool is `tools[0]`."""
        # Re-pinned 17 Sep 2026 (tester): `DOMAIN_SPEC` moved to the DB-backed
        # `turn.policy.Policy` at S0/S6 (AC-1594) - `contracts.py`'s own comment names
        # `turn.policy.default_policy()` as the frozen-seed reader that replaces it,
        # same data (`turn.policy_rows.DEFAULT_DOMAIN_ROWS`), one copy. `DomainPolicy.
        # tools` is the same tuple `DOMAIN_SPEC[name].tools` used to be.
        from app.services.chatbot.turn.policy import default_policy

        fetch = _import_fetch()

        assert fetch.select_tool("incoming") == [
            {"name": "crm_incoming_stock_list", "similarity": 1.0}
        ]
        # Both other incoming tools stay in the tuple as allow-list members (a probe may
        # name `crm_incoming_stock_by_product`, which renders batch numbers on purpose)
        # and neither can be selected.
        assert default_policy().domain("incoming").tools[1:] == (
            "crm_incoming_stock_by_product",
            "crm_incoming_stock_shipments",
        )

    def test_tool_filter_picks_max_similarity_tiebreak_name(self):
        """AC-604: max `similarity` wins; an exact tie breaks on `name` ASC (deterministic)."""
        fetch = _import_fetch()

        candidates = [
            {"name": "crm_marketing_promotions_list", "similarity": 0.40},
            {"name": "crm_master_products_list", "similarity": 0.91},
            {"name": "crm_incoming_stock_list", "similarity": 0.91},
        ]
        result = fetch.tool_filter(candidates, has_product=True)

        assert result.outcome == "picked"
        assert len(result.items) == 1
        picked = result.items[0]["json"]
        # "crm_incoming_stock_list" < "crm_master_products_list" lexically - the tie goes to
        # the alphabetically-first name, matching `tool-filter.js`'s own `cmp(label(a), label(b))`.
        assert picked["name"] == "crm_incoming_stock_list"
        assert picked["_tool_pick"]["chosen"] == "crm_incoming_stock_list"
        assert picked["_tool_pick"]["count"] == 3
        assert picked["_tool_pick"]["has_product"] is True
        rejected_names = {r["name"] for r in picked["_tool_pick"]["rejected"]}
        assert rejected_names == {"crm_marketing_promotions_list", "crm_master_products_list"}

    def test_zero_tools_is_not_found_outcome(self):
        """H11: zero candidates is a DISTINGUISHABLE outcome, never a silent empty turn.

        `tool-filter.js` returns `[]` on zero tools (parity, preserved on `.items`), but the
        Python port must ALSO say so through a channel a caller can act on - the JS's own
        empty array is indistinguishable from "ran and found nothing to say", which is
        exactly the hazard (a turn that goes quiet is not the same as a turn that answers
        "I don't have a tool for that").
        """
        fetch = _import_fetch()

        result = fetch.tool_filter([], has_product=None)

        assert result.items == [], "parity: zero tools in, zero items out (D8)"
        assert result.outcome == "not_found", (
            "H11: the caller must be able to tell 'no tool matched' apart from 'nothing ran'"
        )


# --------------------------------------------------------------------------- #
# AC-605 / AC-606 - node replay against the captured n8n executions
# --------------------------------------------------------------------------- #


def _has_product_from_gate(fixture: _corpus.Fixture) -> bool | None:
    """Tolerant read of `build-ctx-resolved`'s `ctx.gate.compatible_entities` (tool-filter.js)."""
    items = fixture.upstream("build-ctx-resolved")
    if not items:
        return None
    bcr = items[0].get("json") or {}
    try:
        entities = ((bcr.get("ctx") or {}).get("gate") or {}).get("compatible_entities")
    except AttributeError:
        return None
    if not isinstance(entities, list):
        return None
    return any(isinstance(e, dict) and e.get("entity_type") == "product" for e in entities)


def _run_tool_filter(fixture: _corpus.Fixture) -> list:
    fetch = _import_fetch()
    rag_output = fixture.first("Execute 'sub-get-rag'")
    candidates = rag_output.get("tools") or []
    result = fetch.tool_filter(candidates, has_product=_has_product_from_gate(fixture))
    return result.items


def _run_tier_probe_plan(fixture: _corpus.Fixture) -> list:
    fetch = _import_fetch()
    tier_gate = fixture.first("tier-gate")
    return fetch.tier_probe_plan(tier_gate)


def _run_tier_probe_collect(fixture: _corpus.Fixture) -> list:
    fetch = _import_fetch()
    tier_gate = fixture.first("tier-gate")
    plan_items = [i.get("json") for i in fixture.upstream("tier-probe-plan")]
    probe_results = [i.get("json") for i in fixture.upstream("tier-probe")]
    return [{"json": fetch.tier_probe_collect(tier_gate, plan_items=plan_items, probe_results=probe_results)}]


def _run_fetch_result(fixture: _corpus.Fixture) -> list:
    fetch = _import_fetch()
    item = (fixture.input[0] or {}).get("json") or {}
    tool = None
    if fixture.upstream("tool-filter"):
        tool = fixture.first("tool-filter")
    tier_probe = None
    if fixture.upstream("tier-probe-collect"):
        tier_probe = fixture.first("tier-probe-collect")
    return [{"json": fetch.fetch_result(item, tool=tool, tier_probe=tier_probe)}]


_S6B_RUNNERS = {
    "tool-filter": _run_tool_filter,
    "tier-probe-plan": _run_tier_probe_plan,
    "tier-probe-collect": _run_tier_probe_collect,
    "fetch-result": _run_fetch_result,
}
_S6B_NODES = sorted(_S6B_RUNNERS)


def _replay(fixture: _corpus.Fixture) -> None:
    actual = _corpus.json_round_trip(_S6B_RUNNERS[fixture.node](fixture))
    expected = _corpus.json_round_trip(fixture.expected)
    from tests.chatbot import divergences

    registered = divergences.find(fixture.node, fixture.name.split("/")[-1])
    if actual == expected:
        if registered is not None:
            pytest.fail(
                f"{fixture.node}/{fixture.name}: registered divergence {registered.hazard} "
                "no longer diverges - retire the entry"
            )
        return
    if registered is not None:
        return
    assert actual == expected, (
        f"{fixture.node}/{fixture.name} diverges from the captured n8n output and is not "
        "registered in tests/chatbot/divergences.py"
    )


@pytest.mark.parametrize(
    "fixture",
    _corpus.graded([f for node in _S6B_NODES for f in _corpus.full_corpus(node)]) or [None],
    ids=lambda f: f"{f.node}/{f.name}" if f is not None else "corpus-absent",
)
def test_entity_ids_transformer_replay_placeholder_tool_filter_replay(fixture) -> None:
    """AC-606: `tool-filter`, `tier-probe-plan`, `tier-probe-collect` and `fetch-result`
    against every captured `sub-fetch-results-rs` execution (16 captures; a vendored subset
    of these now exists too, see `test_gate_0_vendored_subset_is_present` below - AC-008's
    "5 per branch" floor is a separate, per-branch count that test does not verify).

    `entity-ids-transformer` and `output-structurer` have NO captured `runData` fixtures
    anywhere in the corpus today (checked: `tests/fixtures/nodes/{entity-ids-transformer,
    output-structurer}/` does not exist under either slug). They are covered by hand-built
    UNIT tests below instead (`TestEntityIdsTransformer`, `TestOutputStructurer`) - clearly
    NOT corpus replay, and NOT a substitute for the real captures AC-008 requires before this
    slice's PR opens.
    """
    if fixture is None:
        pytest.skip(_corpus.corpus_skip_reason())
    _replay(fixture)


@pytest.mark.parametrize("node", _S6B_NODES)
def test_gate_0_vendored_subset_is_present(node: str) -> None:
    """AC-008 requires a vendored subset under `tests/fixtures/chatbot/nodes/<node>/` before
    this slice's PR opens (gate 0). This test used to assert the OPPOSITE - it was written as
    a tripwire before the port existed, deliberately failing the moment a vendored subset
    appeared, so the gap could not silently stay unfixed. The coder has since vendored one
    fixture per node for all four S6b replay nodes and the tripwire fired as designed; it is
    inverted here to the steady-state assertion this suite should carry from now on.

    This only checks PRESENCE, not the AC-008 floor - "at least 5 real captures per branch of
    every branch the slice ports" is a per-branch count this test does not attempt to verify
    (some nodes here have only one or two vendored fixtures today; see the corpus's own
    `scripts/chatbot_fixture_coverage.py` / `COVERAGE.md` for the branch-level count that
    actually gates a merge).
    """
    assert _corpus.vendored(node), (
        f"no vendored fixtures for {node} under tests/fixtures/chatbot/nodes/{node}/ - the "
        "always-on replay gate would pass by having nothing to check"
    )


# --------------------------------------------------------------------------- #
# AC-605, H52 - the MCP call: configured URL, never a raw IP; entity-ids-transformer
# --------------------------------------------------------------------------- #


class TestCallTool:
    def test_mcp_call_uses_configured_url_never_raw_ip(self):
        """`call_tool` is a thin pass-through onto whatever `MCPRuntimeClient`-shaped object
        it is handed, and the PRODUCTION construction of that object (in `services.py`,
        mirroring `_probe()`'s own D10 docstring) is what binds it to
        `settings.ai_assistant_mcp_url` - never a literal host.

        H52's own catalogued hazard is a raw IP (`72.62.195.20`) baked into an n8n HTTP
        node. The static check below is the mechanical guarantee that this file never grows
        the same thing back in.
        """
        fetch = _import_fetch()

        received: list[tuple[str, dict[str, Any]]] = []

        class FakeMcp:
            def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
                received.append((name, arguments))
                return '{"answers": []}'

        args = {"product_ids": ["6136ea6b-1699-46ec-8e8e-f60c8bb64310"], "space_id": "364817"}
        result = fetch.call_tool("crm_master_products_list", args, mcp=FakeMcp())

        assert received == [("crm_master_products_list", args)], (
            "the seam must receive {name, arguments} exactly as entity-ids-transformer "
            "built them, with no re-shaping (D10)"
        )
        assert result == '{"answers": []}'

        source = _fetch_source()
        assert "72.62.195.20" not in source, "H52: no literal raw IP in fetch.py"
        assert "http://" not in source, "H52: no hard-coded scheme+host in fetch.py"


class TestEntityIdsTransformer:
    """Hand-built unit tests over `entity-ids-transformer.js`'s own literal behaviour.

    No `runData` fixture exists for this node in the corpus (checked directly), so these are
    NOT graded against a real capture - they assert the structural rules the JS states in its
    own comments, so the port has something to satisfy before the corpus catches up (AC-008
    tracks the capture gap separately, in `test_gate_0_vendored_subset_is_present` above -
    which only covers the four replay nodes; `entity-ids-transformer` and `output-structurer`
    have no vendored fixtures at all yet).
    """

    def test_type_to_param_maps_product_and_dedupes_uuids(self):
        fetch = _import_fetch()
        trigger = {
            "entities": [
                {"uuid": "6136ea6b-1699-46ec-8e8e-f60c8bb64310", "entity_type": "product", "code": "A"},
                {"uuid": "6136ea6b-1699-46ec-8e8e-f60c8bb64310", "entity_type": "product", "code": "A"},
                {"uuid": "7136ea6b-1699-46ec-8e8e-f60c8bb64311", "entity_type": "customer", "code": "B"},
            ],
            "tool": "crm_master_products_list",
            "semantic_input": {"contact_id": 900000039, "space_id": "ignored-by-the-hardcode"},
        }
        out = fetch.entity_ids_transformer(trigger)

        assert out["product_ids"] == ["6136ea6b-1699-46ec-8e8e-f60c8bb64310"]
        assert out["customer_ids"] == ["7136ea6b-1699-46ec-8e8e-f60c8bb64311"]

    def test_bad_uuid_is_skipped_not_thrown(self):
        fetch = _import_fetch()
        trigger = {
            "entities": [{"uuid": "not-a-uuid", "entity_type": "product", "code": "A"}],
            "tool": "crm_master_products_list",
            "semantic_input": {"contact_id": "1", "space_id": "x"},
        }
        out = fetch.entity_ids_transformer(trigger)
        assert out.get("product_ids") in (None, [])

    def test_contact_id_coerces_int_and_padded_string(self):
        """`String(x ?? '').trim()` - int and a space-padded string both fold to the trimmed
        string form, per the JS's own measured-over-24-executions comment."""
        fetch = _import_fetch()
        for raw in (900000039, "900000039 ", " 900000039"):
            trigger = {"entities": [], "tool": "x", "semantic_input": {"contact_id": raw, "space_id": "s"}}
            out = fetch.entity_ids_transformer(trigger)
            assert out["contact_id"] == "900000039"

    def test_space_id_is_hard_coded_364817(self):
        """The ONE deliberate hard-code the JS keeps (a single-tenant confirmed decision) -
        NOT a D5 divergence, because `entity-ids-transformer` is not `resolve-entity` /
        `get-access-types` / the probes; D5 only reassigns those four call sites."""
        fetch = _import_fetch()
        trigger = {"entities": [], "tool": "x", "semantic_input": {"contact_id": "1", "space_id": "some-other-value"}}
        out = fetch.entity_ids_transformer(trigger)
        assert out["space_id"] == "364817"


# --------------------------------------------------------------------------- #
# AC-605, H7, H46 - output-structurer: deterministic, single-source timeline sentinel
# --------------------------------------------------------------------------- #


class TestOutputStructurer:
    def test_output_structurer_is_deterministic(self, monkeypatch):
        """H7: `output-structurer` is the "orphaned answer LLM" hazard's own resolution -
        there is NO answer LLM in this port (D10). Same input in, same output out, twice,
        and no LLM provider seam is ever touched.
        """
        fetch = _import_fetch()

        def _forbidden(*_a, **_k):
            pytest.fail("output_structurer must not call an LLM provider (H7, D10)")

        # Patch every provider-facing entry point this package could plausibly reach, so a
        # future accidental import still trips this test rather than silently calling out.
        import app.services.llm_provider as llm_provider_mod

        monkeypatch.setattr(llm_provider_mod, "LLMProvider", _forbidden, raising=False)

        result = {
            "response": "Here are the matching products.",
            "response_intro": "Here are the matching products.",
            "answers": [{"title": "SRTWB7096", "fields": [{"key": "product_code", "label": "Product Code", "value": "SRTWB7096"}]}],
            "attachments": [],
            "has_result": True,
            "field_access": None,
        }
        ctx = {"semantic_input": {"requested_attributes": []}}

        first = fetch.output_structurer(result, ctx)
        second = fetch.output_structurer(result, ctx)
        assert _corpus.json_round_trip(first) == _corpus.json_round_trip(second)

    def test_timeline_sentinel_denies_mixed_array(self):
        """H46: `output-structurer`'s per-contact denial note is gated on `!is_timeline(...)`,
        and `is_timeline` is a CONTAINS check (`contracts.is_timeline`, already declared in
        `app/services/chatbot/contracts.py` for exactly this reason - the module's own
        docstring: "Declared here, with `is_timeline` beside it, so S6b's port consumes the
        one reading instead of re-deriving it").

        `['__all__', 'eta_delay_date']` still counts as a timeline request (the sentinel is
        PRESENT), so the denial note for `eta_delay_date` is SUPPRESSED - a customer who asked
        for the whole container timeline is not told, field by field, which parts of "the
        whole timeline" they may not see. `['eta_delay_date']` alone carries no sentinel, so
        the note is emitted normally.
        """
        from app.services.chatbot.contracts import is_timeline

        assert is_timeline(["__all__", "eta_delay_date"]) is True
        assert is_timeline(["eta_delay_date"]) is False

        fetch = _import_fetch()
        source = _fetch_source()
        assert "contracts import is_timeline" in source or "contracts.is_timeline" in source, (
            "output_structurer must import contracts.is_timeline rather than re-deriving its "
            "own copy of the sentinel check (H46)"
        )

        # `output_structurer`'s `result` argument is the MCP tool's RENDER ENVELOPE (what
        # `_find_payload` accepts: a top-level `items` list, `portal_url`, or `token` - or
        # those same three nested under `content[].text`, since all 20 real captures arrive
        # wrapped as `{"content": [...]}`). It is NOT shaped like this function's own return
        # value - the earlier version of this fixture passed `{"response", "answers", ...}`,
        # which `_find_payload` does not recognise, so `_extract_envelope` fell through to
        # its empty envelope and no denial note could ever fire either way. Reshaped to the
        # real input: a top-level `items` list plus `result_type: "incoming_stock"` (so the
        # clearance-envelope gate that guards field-level denial actually opens).
        denied_result = {
            "result_type": "incoming_stock",
            "intro": "Here is what I found.",
            "items": [
                {
                    "title": "row",
                    "fields": [{"key": "product_code", "label": "Product Code", "value": "SRTWB7096"}],
                }
            ],
            "has_result": True,
            "field_access": {"denied": [{"field": "eta_delay_date", "label": "ETA Delay"}]},
        }

        timeline_ctx = {"semantic_input": {"requested_attributes": ["__all__", "eta_delay_date"]}}
        plain_ctx = {"semantic_input": {"requested_attributes": ["eta_delay_date"]}}

        timeline_out = fetch.output_structurer(denied_result, timeline_ctx)
        plain_out = fetch.output_structurer(denied_result, plain_ctx)

        timeline_text = timeline_out.get("response") or ""
        plain_text = plain_out.get("response") or ""
        assert "eta delay" not in timeline_text.lower(), (
            "a mixed requested_attributes array containing '__all__' must still suppress the "
            "per-field denial note (H46 contains-semantics)"
        )
        assert "eta delay" in plain_text.lower() or "can't share" in plain_text.lower(), (
            "requested_attributes without the sentinel must still emit the denial note"
        )

    # ----------------------------------------------------------------------- #
    # AC5 - a checkpoint ask expands backwards through the container's journey
    # ----------------------------------------------------------------------- #

    _ALL_CHECKPOINTS: dict[str, str] = {
        "loading_date": "2026-01-01",
        "etc_date": "2026-01-02",
        "etd_date": "2026-01-03",
        "estimated_arrival_date": "2026-01-04",
        "eta_delay_date": "2026-01-05",
        "inspection_date": "2026-01-06",
        "approval_date": "2026-01-07",
        "gatepass_date": "2026-01-08",
        "warehouse_arrival_date": "2026-01-09",
        "informed_collection_date": "2026-01-10",
        "collection_date": "2026-01-11",
    }

    def _checkpoint_envelope(self, extra_fields: dict[str, str] | None = None) -> dict:
        fields = [{"key": "product_code", "label": "Product Code", "value": "SRTWB7096"}]
        for k, v in self._ALL_CHECKPOINTS.items():
            fields.append({"key": k, "label": k, "value": v})
        for k, v in (extra_fields or {}).items():
            fields.append({"key": k, "label": k, "value": v})
        return {
            "result_type": "incoming_stock",
            "intro": "Here is what I found.",
            "items": [{"title": "row", "fields": fields}],
            "has_result": True,
            "field_access": None,
        }

    def test_checkpoint_ask_keeps_every_earlier_checkpoint(self):
        """Asking for gatepass implies the whole journey UP TO gatepass - a customer who
        asks "when is gatepass" wants the story so far, not one isolated date."""
        fetch = _import_fetch()
        envelope = self._checkpoint_envelope({"liner_code": "CMA"})
        ctx = {"semantic_input": {"requested_attributes": ["gatepass_date"]}}

        out = fetch.output_structurer(envelope, ctx)

        kept = {f["key"] for f in out["answers"][0]["fields"]}
        for k in (
            "loading_date", "etc_date", "etd_date", "estimated_arrival_date",
            "eta_delay_date", "inspection_date", "approval_date", "gatepass_date",
        ):
            assert k in kept, f"{k} should be kept (earlier than or equal to gatepass)"
        for k in ("warehouse_arrival_date", "informed_collection_date", "collection_date", "liner_code"):
            assert k not in kept, f"{k} should be dropped (later than gatepass, or non-checkpoint)"

    def test_checkpoint_ask_warehouse_arrival(self):
        """Asking for warehouse arrival keeps everything through it, drops what comes after."""
        fetch = _import_fetch()
        envelope = self._checkpoint_envelope()
        ctx = {"semantic_input": {"requested_attributes": ["warehouse_arrival_date"]}}

        out = fetch.output_structurer(envelope, ctx)

        kept = {f["key"] for f in out["answers"][0]["fields"]}
        for k in (
            "loading_date", "etc_date", "etd_date", "estimated_arrival_date",
            "eta_delay_date", "inspection_date", "approval_date", "gatepass_date",
            "warehouse_arrival_date",
        ):
            assert k in kept, f"{k} should be kept (earlier than or equal to warehouse arrival)"
        for k in ("informed_collection_date", "collection_date"):
            assert k not in kept, f"{k} should be dropped (later than warehouse arrival)"

    def test_non_checkpoint_ask_does_not_expand(self):
        """`liner_code` is not a sequence key - asking for it must not pull in any
        checkpoint beyond the ALWAYS-kept ETA. `eta_delay_date` rides along with the ETA
        as its own always-kept key (the delay is IMPLIED by the ETA, never named), so it
        is kept here too, same as `estimated_arrival_date`."""
        fetch = _import_fetch()
        envelope = self._checkpoint_envelope({"liner_code": "CMA"})
        ctx = {"semantic_input": {"requested_attributes": ["liner_code"]}}

        out = fetch.output_structurer(envelope, ctx)

        kept = {f["key"] for f in out["answers"][0]["fields"]}
        assert "liner_code" in kept
        assert "product_code" in kept  # identity
        assert "estimated_arrival_date" in kept  # ALWAYS_KEPT_KEYS, ships regardless
        assert "eta_delay_date" in kept  # ALWAYS_KEPT_KEYS, rides with the ETA
        for k in (
            "loading_date", "etc_date", "etd_date", "inspection_date",
            "approval_date", "gatepass_date", "warehouse_arrival_date",
            "informed_collection_date", "collection_date",
        ):
            assert k not in kept, f"{k} must not be pulled in by a non-checkpoint ask"

    def test_expansion_does_not_rewrite_requested_attributes(self):
        """`requested_attributes` echoed in the output is still the ORIGINAL, single-key
        list - only `keep_keys` (internal, not echoed) grows. The "not recorded yet" note
        fires only for the key actually asked (gatepass), never for an absent inspection_date
        that merely got pulled in by the expansion."""
        fetch = _import_fetch()
        fields = [
            {"key": "product_code", "label": "Product Code", "value": "SRTWB7096"},
            {"key": "loading_date", "label": "loading_date", "value": "2026-01-01"},
            # inspection_date is ABSENT from this row entirely (not recorded on the CRM
            # side) - it must not get a synthetic "not recorded yet" note.
            # gatepass_date is also absent - it SHOULD get the note, since it was asked.
        ]
        envelope = {
            "result_type": "incoming_stock",
            "intro": "Here is what I found.",
            "items": [{"title": "row", "fields": fields}],
            "has_result": True,
            "field_access": None,
        }
        ctx = {"semantic_input": {"requested_attributes": ["gatepass_date"]}}

        out = fetch.output_structurer(envelope, ctx)

        assert out["requested_attributes"] == ["gatepass_date"]
        notes = {f["key"]: f["value"] for f in out["answers"][0]["fields"] if f.get("key")}
        assert notes.get("gatepass_date") == "not recorded yet"
        assert "inspection_date" not in notes

    def test_checkpoint_expansion_untouched_when_timeline(self):
        """`['__all__']` already keeps everything (AC5.3) - the checkpoint expansion is
        gated on `not timeline` and must not run (or matter) in that arm."""
        fetch = _import_fetch()
        envelope = self._checkpoint_envelope({"liner_code": "CMA"})
        ctx = {"semantic_input": {"requested_attributes": ["__all__"]}}

        out = fetch.output_structurer(envelope, ctx)

        kept = {f["key"] for f in out["answers"][0]["fields"]}
        assert set(self._ALL_CHECKPOINTS) <= kept
        assert "liner_code" in kept

    def test_expanded_checkpoint_dates_read_chronologically(self):
        """A checkpoint ask expands `keep_keys` backwards (AC5) without setting the
        `timeline` sentinel - it is a PARTIAL timeline and must read the same way: the
        dates chronological sort must fire on `expanded` too, not just on `timeline`, or
        the kept dates render in the CRM's narrative order (ETA, inspection, approval,
        gatepass, ... loading, ETC, ETD trailing) instead of by when they happened."""
        fetch = _import_fetch()
        fields = [
            {"key": "product_code", "label": "Product Code", "value": "SRTWB7096"},
            {"key": "estimated_arrival_date", "label": "estimated_arrival_date", "value": "2026-05-01"},
            {"key": "inspection_date", "label": "inspection_date", "value": "2026-05-22"},
            {"key": "approval_date", "label": "approval_date", "value": "2026-05-26"},
            {"key": "gatepass_date", "label": "gatepass_date", "value": "2026-06-03"},
            {"key": "warehouse_arrival_date", "label": "warehouse_arrival_date", "value": "2026-06-04"},
            {"key": "loading_date", "label": "loading_date", "value": "2026-04-15"},
            {"key": "etd_date", "label": "etd_date", "value": "2026-04-18"},
        ]
        envelope = {
            "result_type": "incoming_stock",
            "intro": "Here is what I found.",
            "items": [{"title": "row", "fields": fields}],
            "has_result": True,
            "field_access": None,
        }
        ctx = {"semantic_input": {"requested_attributes": ["gatepass_date"]}}

        out = fetch.output_structurer(envelope, ctx)

        out_fields = out["answers"][0]["fields"]
        kept_dates = [f["key"] for f in out_fields if f["key"].endswith("_date")]
        assert kept_dates == [
            "loading_date", "etd_date", "estimated_arrival_date",
            "inspection_date", "approval_date", "gatepass_date",
        ]
        assert "warehouse_arrival_date" not in kept_dates

    def test_non_expanding_ask_leaves_single_date_in_place(self):
        """A plain (non-checkpoint) ask never sets `expanded`, and `timeline` is False too
        - the chronological sort must not run, so a lone date field stays exactly where the
        CRM put it."""
        fetch = _import_fetch()
        fields = [
            {"key": "product_code", "label": "Product Code", "value": "SRTWB7096"},
            {"key": "estimated_arrival_date", "label": "estimated_arrival_date", "value": "2026-05-01"},
            {"key": "liner_code", "label": "liner_code", "value": "CMA"},
        ]
        envelope = {
            "result_type": "incoming_stock",
            "intro": "Here is what I found.",
            "items": [{"title": "row", "fields": fields}],
            "has_result": True,
            "field_access": None,
        }
        ctx = {"semantic_input": {"requested_attributes": ["liner_code"]}}

        out = fetch.output_structurer(envelope, ctx)

        out_keys = [f["key"] for f in out["answers"][0]["fields"]]
        assert out_keys.index("estimated_arrival_date") == 1

    # ----------------------------------------------------------------------- #
    # A bare container ask ("incoming TIIU6323920") is a timeline ask
    # (live turn f07632b6-d56d-4036-944c-8200462caac3)
    # ----------------------------------------------------------------------- #

    def test_bare_container_ask_is_a_timeline(self):
        """No `requested_attributes` at all, but the resolved entity is an
        `inbound_shipment` - every recorded checkpoint comes out, chronologically, the
        same as the `__all__` sentinel does."""
        fetch = _import_fetch()
        fields = [
            {"key": "product_code", "label": "Product Code", "value": "SRTWB7096"},
            {"key": "estimated_arrival_date", "label": "estimated_arrival_date", "value": "2026-01-04"},
            {"key": "loading_date", "label": "loading_date", "value": "2026-01-01"},
            {"key": "gatepass_date", "label": "gatepass_date", "value": "2026-01-08"},
            {"key": "etc_date", "label": "etc_date", "value": "2026-01-02"},
            {"key": "collection_date", "label": "collection_date", "value": "2026-01-11"},
            {"key": "etd_date", "label": "etd_date", "value": "2026-01-03"},
            {"key": "warehouse_arrival_date", "label": "warehouse_arrival_date", "value": "2026-01-09"},
            {"key": "eta_delay_date", "label": "eta_delay_date", "value": "2026-01-05"},
            {"key": "informed_collection_date", "label": "informed_collection_date", "value": "2026-01-10"},
            {"key": "inspection_date", "label": "inspection_date", "value": "2026-01-06"},
            {"key": "approval_date", "label": "approval_date", "value": "2026-01-07"},
        ]
        envelope = {
            "result_type": "incoming_stock",
            "intro": "Here is what I found.",
            "items": [{"title": "row", "fields": fields}],
            "has_result": True,
            "field_access": None,
        }
        ctx = {
            "semantic_input": {"requested_attributes": []},
            "entities": [
                {
                    "uuid": "6136ea6b-1699-46ec-8e8e-f60c8bb64310",
                    "entity_type": "inbound_shipment",
                    "code": "TIIU6323920",
                }
            ],
        }

        out = fetch.output_structurer(envelope, ctx)

        out_fields = out["answers"][0]["fields"]
        kept = [f["key"] for f in out_fields if f["key"] != "product_code"]
        assert kept == list(fetch.CLEARANCE_CHECKPOINT_ORDER), (
            "every checkpoint must be kept, in chronological order, exactly as the "
            "'__all__' sentinel behaves"
        )
        assert out["requested_attributes"] == [], "the echoed ask itself is untouched"

    def test_bare_product_ask_stays_eta_only(self):
        """No `requested_attributes`, and the resolved entity is a `product` - a bare
        product ask must NOT be widened into a timeline; only identity + the always-kept
        ETA (and its delay, which rides along with it) survive."""
        fetch = _import_fetch()
        envelope = self._checkpoint_envelope({"liner_code": "CMA"})
        ctx = {
            "semantic_input": {"requested_attributes": []},
            "entities": [
                {
                    "uuid": "7136ea6b-1699-46ec-8e8e-f60c8bb64311",
                    "entity_type": "product",
                    "code": "SRTWB7096",
                }
            ],
        }

        out = fetch.output_structurer(envelope, ctx)

        kept = {f["key"] for f in out["answers"][0]["fields"]}
        assert kept == {"product_code", "estimated_arrival_date", "eta_delay_date"}, (
            "a bare product ask keeps only identity + the always-kept ETA (and its "
            "always-kept delay), never widens into a full checkpoint timeline"
        )

    def test_container_ask_with_an_attribute_is_not_widened(self):
        """An EXPLICIT attribute ask (`gatepass_date`) alongside an `inbound_shipment`
        entity must still take the backward-expansion path, not the full timeline - the
        new bare-container rule only fires when `requested_attributes` is empty."""
        fetch = _import_fetch()
        envelope = self._checkpoint_envelope({"liner_code": "CMA"})
        ctx = {
            "semantic_input": {"requested_attributes": ["gatepass_date"]},
            "entities": [
                {
                    "uuid": "6136ea6b-1699-46ec-8e8e-f60c8bb64310",
                    "entity_type": "inbound_shipment",
                    "code": "TIIU6323920",
                }
            ],
        }

        out = fetch.output_structurer(envelope, ctx)

        kept = {f["key"] for f in out["answers"][0]["fields"]}
        for k in (
            "loading_date", "etc_date", "etd_date", "estimated_arrival_date",
            "eta_delay_date", "inspection_date", "approval_date", "gatepass_date",
        ):
            assert k in kept, f"{k} should be kept (earlier than or equal to gatepass)"
        assert "warehouse_arrival_date" not in kept, (
            "an explicit attribute ask must not be overridden into the full timeline "
            "just because the entity is an inbound_shipment"
        )
        assert "informed_collection_date" not in kept
        assert "collection_date" not in kept

    def test_bare_container_ask_falls_back_to_parser_hint(self):
        """`_names_a_shipment` checks the RESOLVED entity list first and only falls back to
        the parser's own raw `hint` when the resolved list carries no type at all - this
        pins that fallback branch directly rather than only through a resolved entity."""
        fetch = _import_fetch()

        timeline_envelope = self._checkpoint_envelope()
        timeline_ctx = {
            "semantic_input": {
                "requested_attributes": [],
                "entities": [{"raw": "TIIU6323920", "hint": "inbound_shipment"}],
            },
            "entities": [],
        }
        timeline_out = fetch.output_structurer(timeline_envelope, timeline_ctx)
        timeline_kept = [f["key"] for f in timeline_out["answers"][0]["fields"] if f["key"] != "product_code"]
        assert timeline_kept == list(fetch.CLEARANCE_CHECKPOINT_ORDER), (
            "the raw parser hint 'inbound_shipment' must widen to the full timeline, "
            "exactly as a resolved entity_type does"
        )

        eta_envelope = self._checkpoint_envelope()
        eta_ctx = {
            "semantic_input": {
                "requested_attributes": [],
                "entities": [{"raw": "SRTWB7096", "hint": "product"}],
            },
            "entities": [],
        }
        eta_out = fetch.output_structurer(eta_envelope, eta_ctx)
        eta_kept = {f["key"] for f in eta_out["answers"][0]["fields"]}
        assert eta_kept == {"product_code", "estimated_arrival_date", "eta_delay_date"}, (
            "a raw parser hint of 'product' must NOT widen into a timeline - only "
            "identity + the always-kept ETA (and its always-kept delay) survive"
        )


def test_clearance_checkpoint_order_has_no_duplicates_and_matches_parser_vocabulary():
    """The tuple is hardcoded (output_structurer is a pure function with no session) and
    must mirror the same vocabulary the semantic parser already hardcodes - a checkpoint
    the parser cannot name can never appear in `requested_attributes` in the first place,
    and a duplicate would double-count in the expansion index lookup.

    Also pinned against the migration's own `CHECKPOINTS` list (the seed for the real
    `statuses` rows this timeline reads): a later migration that adds a checkpoint must
    extend `CLEARANCE_CHECKPOINT_ORDER` in the same change, or the new checkpoint is seeded
    but the fetch lane can never expand it."""
    fetch = _import_fetch()
    order = fetch.CLEARANCE_CHECKPOINT_ORDER
    assert len(order) == len(set(order)), "CLEARANCE_CHECKPOINT_ORDER has a duplicate"

    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    for key in order:
        assert f'"{key}"' in SEMANTIC_PARSER_PROMPT or f"'{key}'" in SEMANTIC_PARSER_PROMPT, (
            f"{key} is in CLEARANCE_CHECKPOINT_ORDER but not quoted in the parser prompt"
        )

    migration = _import_migration_312()
    assert order == tuple(key for key, *_ in migration.CHECKPOINTS), (
        "CLEARANCE_CHECKPOINT_ORDER has drifted from the seeded checkpoints in "
        "312_container_status_checkpoints.py - a migration adding a checkpoint must "
        "extend this tuple too"
    )


# --------------------------------------------------------------------------- #
# PLAN-chatbot-eta-delay-always-kept-24sep: the ETA delay rides along with the ETA on
# every incoming answer, asked for or not - `eta_delay_date` joins `ALWAYS_KEPT_KEYS`.
# `req_attrs` (the "the customer NAMED this field" list) is untouched, so a denied or
# blank delay the customer never named stays silent: no refusal line, no "not recorded
# yet" line. Only an EXPLICIT `eta_delay_date` ask still turns those notes on (AC-4,
# covered by `test_timeline_sentinel_denies_mixed_array`'s `plain_ctx`/`plain_out` pair
# above - left unchanged).
# --------------------------------------------------------------------------- #


class TestEtaDelayAlwaysKept:
    def test_bare_eta_ask_keeps_delay(self):
        """AC-1: no `requested_attributes` at all, no shipment named - the delay still
        rides along with the ETA."""
        fetch = _import_fetch()
        envelope = {
            "result_type": "incoming_stock",
            "intro": "Here is what I found.",
            "items": [
                {
                    "title": "row",
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": "SRTWB7096"},
                        {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-28"},
                        {"key": "eta_delay_date", "label": "ETA Delay", "value": "2026-10-02"},
                    ],
                }
            ],
            "has_result": True,
            "field_access": None,
        }
        ctx = {"semantic_input": {"requested_attributes": []}}

        out = fetch.output_structurer(envelope, ctx)

        kept = {f["key"] for f in out["answers"][0]["fields"]}
        assert "estimated_arrival_date" in kept
        assert "eta_delay_date" in kept
        response = (out.get("response") or "").lower()
        assert "not recorded yet" not in response
        assert "can't share" not in response

    def test_explicit_eta_ask_keeps_delay(self):
        """AC-2: an explicit `estimated_arrival_date` ask still keeps the delay riding
        along, with no note text either way."""
        fetch = _import_fetch()
        envelope = {
            "result_type": "incoming_stock",
            "intro": "Here is what I found.",
            "items": [
                {
                    "title": "row",
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": "SRTWB7096"},
                        {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-28"},
                        {"key": "eta_delay_date", "label": "ETA Delay", "value": "2026-10-02"},
                    ],
                }
            ],
            "has_result": True,
            "field_access": None,
        }
        ctx = {"semantic_input": {"requested_attributes": ["estimated_arrival_date"]}}

        out = fetch.output_structurer(envelope, ctx)

        kept = {f["key"] for f in out["answers"][0]["fields"]}
        assert "estimated_arrival_date" in kept
        assert "eta_delay_date" in kept
        response = (out.get("response") or "").lower()
        assert "not recorded yet" not in response
        assert "can't share" not in response

    def test_denied_delay_silent_when_not_named(self):
        """AC-3: the contact may not see `eta_delay_date` (already stripped by
        `field_access.py` before the row reaches this function) and never named it in
        `requested_attributes` - no refusal line, no "eta delay" mention at all. The ETA
        itself is unaffected."""
        fetch = _import_fetch()
        envelope = {
            "result_type": "incoming_stock",
            "intro": "Here is what I found.",
            "items": [
                {
                    "title": "row",
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": "SRTWB7096"},
                        {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-28"},
                        # eta_delay_date is ABSENT - field_access.py already stripped it.
                    ],
                }
            ],
            "has_result": True,
            "field_access": {"denied": [{"field": "eta_delay_date", "label": "ETA Delay"}]},
        }
        ctx = {"semantic_input": {"requested_attributes": ["estimated_arrival_date"]}}

        out = fetch.output_structurer(envelope, ctx)

        kept = {f["key"] for f in out["answers"][0]["fields"]}
        assert "estimated_arrival_date" in kept
        response = (out.get("response") or "").lower()
        assert "can't share" not in response
        assert "eta delay" not in response
        assert "not recorded yet" not in response

    def test_blank_delay_silent_when_not_named(self):
        """AC-5: the row has no `eta_delay_date` at all (not denied, just not recorded)
        and it was never named in `requested_attributes` - no synthetic "not recorded
        yet" entry for it."""
        fetch = _import_fetch()
        envelope = {
            "result_type": "incoming_stock",
            "intro": "Here is what I found.",
            "items": [
                {
                    "title": "row",
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": "SRTWB7096"},
                        {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-28"},
                    ],
                }
            ],
            "has_result": True,
            "field_access": None,
        }
        ctx = {"semantic_input": {"requested_attributes": ["estimated_arrival_date"]}}

        out = fetch.output_structurer(envelope, ctx)

        notes = {f["key"]: f["value"] for f in out["answers"][0]["fields"] if f.get("key")}
        assert "eta_delay_date" not in notes


# --------------------------------------------------------------------------- #
# Owner console defect I (owner ruling: "label it"). `output_structurer`'s
# multi-company "which company came back empty" block (fetch.py:1043-1087) builds its
# "no <noun> records for ..." sentence by flattening EVERY `ctx.entities[].code` into one
# comma list - including a customer's internal debtor code (e.g. "300-H070") and every
# alias-row name variant for the SAME customer, un-labelled and un-deduped. The owner
# wants an axis-labelled sentence ("customer X, product Y" - the `_AXES` vocabulary
# `answer.py:1586-1592` already uses elsewhere) with the internal debtor code never
# printed and the alias rows collapsed to one customer name.
# --------------------------------------------------------------------------- #


class TestLabelledNotFoundLineNeverLeaksInternalDebtorCode:
    def test_customer_alias_rows_collapse_and_the_debtor_code_never_prints(self) -> None:
        fetch = _import_fetch()

        result = {
            "result_type": "order",
            "intro": "Here are the results.",
            "items": [],
            "has_result": False,
            "lookup_companies": [{"name": "Sorento"}, {"name": "Mocha"}],
        }
        ctx = {
            "semantic_input": {"requested_attributes": []},
            "entities": [
                # The SAME customer, three rows: the internal debtor code plus two
                # alias/name variants - exactly the shape a multi-alias customer
                # match carries.
                {"code": "300-H070", "uuid": "cust-1", "entity_type": "customer"},
                {
                    "code": "HANLIM TRADING SDN BHD [A/C I]",
                    "uuid": "cust-1",
                    "entity_type": "customer",
                },
                {
                    "code": "HANLIM TRADING SDN BHD",
                    "uuid": "cust-2",
                    "entity_type": "customer",
                },
                {"code": "RPACC", "uuid": "prod-1", "entity_type": "product"},
            ],
        }

        out = fetch.output_structurer(result, ctx)
        response = out.get("response") or ""

        assert "300-H070" not in response, (
            f"the internal debtor code must never reach the customer: {response!r}"
        )
        assert "[A/C I]" not in response, (
            f"the alias-row name variant must not appear alongside the plain name: {response!r}"
        )
        assert response.count("HANLIM TRADING SDN BHD") == 1, (
            "the alias rows for the SAME customer must collapse to ONE printed name, "
            f"not one bullet per alias: {response!r}"
        )
        assert "no order records found for customer HANLIM TRADING SDN BHD, product RPACC" in response, (
            f"the sentence must be axis-labelled (customer / product), not a bare comma "
            f"list of raw entity codes: {response!r}"
        )


# --------------------------------------------------------------------------- #
# H49 - verify the live tool-selection distribution before porting a per-tool branch
# --------------------------------------------------------------------------- #


class TestToolDistribution:
    def test_tool_distribution_note(self):
        """H49: the plan says "verify the live tool-selection distribution before porting
        any per-tool branch" - `crm_order_management_orders_by_product_list` has never been
        selected in the fixtures graded so far. This test does NOT claim to have re-measured
        production; it asserts the port has made the SAFE choice given that measurement is
        still open: either the module records the measured distribution in its own docstring
        (so the claim is auditable, same pattern as `COVERAGE.md`'s scanned-count rows), or it
        contains no `if tool_name == 'crm_order_management_orders_by_product_list'` branch.
        """
        source = _fetch_source()
        has_note = "orders_by_product_list" in source and (
            "never selected" in source.lower() or "distribution" in source.lower()
        )
        has_dedicated_branch = bool(
            re.search(r"crm_order_management_orders_by_product_list", source)
            and re.search(r"if\b.*crm_order_management_orders_by_product_list", source)
        )
        assert has_note or not has_dedicated_branch, (
            "H49: fetch.py branches on a tool the live distribution has never selected, "
            "with no docstring recording the measurement that justifies it"
        )


# --------------------------------------------------------------------------- #
# Capacity - no DB session held across the MCP call (plan "Capacity and safety")
# --------------------------------------------------------------------------- #


class TestCapacity:
    def test_no_session_across_mcp_call(self, counting_session_factory):
        """The plan's capacity rule, restated for S6b: 'never hold a DB session across LLM
        or MCP I/O'. `call_tool` (the MCP round trip) must run with zero sessions open on
        the factory the caller handed in.

        `select_tool` used to be the other half of this test, because it embedded the
        customer's message through a provider. It reads `DOMAIN_SPEC` now: there is no I/O
        to hold a session across, which is a stronger guarantee than the one this asserted.
        """
        fetch = _import_fetch()
        FetchServices = _import_fetch_services()

        observed_during_mcp: list[int] = []

        def fake_mcp_call(name, args):
            observed_during_mcp.append(counting_session_factory.state["open"])
            return "{}"

        services = FetchServices(mcp_call=fake_mcp_call)
        assert services.mcp_call is fake_mcp_call

        # The session is opened, used to build `services` (a real production binding takes
        # `db`), and MUST be closed before the MCP call below runs.
        db = counting_session_factory()
        db.close()

        assert fetch.select_tool("master_products") == [
            {"name": "crm_master_products_list", "similarity": 1.0}
        ]
        fetch.call_tool("crm_master_products_list", {}, mcp=type("M", (), {"call_tool": staticmethod(fake_mcp_call)})())

        assert observed_during_mcp == [0], (
            "a DB session was open during the MCP call - fetch.py must not hold one across "
            "MCP I/O"
        )


# --------------------------------------------------------------------------- #
# D14 - dry run still reads, writes nothing
# --------------------------------------------------------------------------- #


class TestDryRun:
    def test_dry_run_still_reads_but_writes_nothing(self, session_factory):
        """D14: a dry-run turn performs the SAME MCP read as a live turn (parity - the
        customer-facing behaviour of a test turn must match production, or console/clone
        testing proves nothing) but commits no row anywhere outside `chatbot.turns` (which
        this package does not even write to - that is the engine's job).
        """
        from app.services.chatbot.lanes.business import fetch

        FetchServices = _import_fetch_services()

        commits: list[None] = []
        db = session_factory()
        original_commit = db.commit

        def counting_commit():
            commits.append(None)
            return original_commit()

        db.commit = counting_commit  # type: ignore[method-assign]

        mcp_reads: list[str] = []

        def fake_mcp_call(name, args):
            mcp_reads.append(name)
            return '{"answers": []}'

        services = FetchServices(mcp_call=fake_mcp_call)

        continue_payload = {
            "_exit_kind": "continue",
            "gate": {"compatible_entities": [{"uuid": "6136ea6b-1699-46ec-8e8e-f60c8bb64310", "entity_type": "product", "code": "SRTWB7096"}]},
            # The domain is what names the tool now, so a payload that reaches the read
            # has to carry one - this is the parser emission a "spec for SRTWB7096" turn
            # arrives with.
            "ctx": {"parse": {"output": {"domain_hint": "master_products"}}},
        }

        from app.services.chatbot.lanes import business

        business.run_fetch(continue_payload, services=services, dry_run=True)

        assert mcp_reads, "a dry-run turn must still make the MCP read (D14 parity)"
        assert commits == [], "a dry-run turn must commit nothing (D14)"


# --------------------------------------------------------------------------- #
# Engine dispatch - `_fetch_arm` decides what happens next
# --------------------------------------------------------------------------- #


class TestEngineDispatch:
    """`lanes/business/__init__.py::run_fetch` - the next call site after `run_until_exit`'s
    "continue" exit (AC-605). It is NOT `engine.py` itself: S6a's own precedent is that the
    engine calls exactly ONE function per lane stage (`run_until_exit`), so `run_fetch` is
    that same seam for the fetch stage. `engine.py` wiring `run_fetch` into `run_turn` is a
    separate, later step this file does not test.
    """

    @staticmethod
    def _services(mcp_result: str = '{"answers": []}'):
        FetchServices = _import_fetch_services()
        return FetchServices(mcp_call=lambda name, args: mcp_result)

    @staticmethod
    def _master_products_ctx() -> dict:
        """The parser emission whose domain names `crm_master_products_list`.

        Every payload below that reaches the read carries it: `select_tool` picks off
        `domain_hint`, so a payload without one ends `not_found` before any tool is called.
        """
        return {"parse": {"output": {"domain_hint": "master_products"}}}

    def test_fetch_arm_tier_ask_does_not_delegate_business_query(self):
        """`_fetch_arm == 'tier-ask'`: the turn needs the customer to pick an access tier
        before anything is fetched. This is `access-level-choice-message`'s own path (S6c
        renders the actual copy); S6b's job is only to make sure the turn does NOT fall
        through to a normal 'result' delegate while a tier is still unresolved.
        """
        from app.services.chatbot.lanes import business

        payload = {
            "_exit_kind": "continue",
            "gate": {"compatible_entities": []},
            "tier_ask": True,
            "tier_any_available": True,
        }
        fragment = business.run_fetch(payload, services=self._services(), dry_run=False)

        assert fragment.get("delegate") != "business_query", (
            "a tier-ask arm must not be handed to n8n as an ordinary business_query result"
        )
        assert fragment.get("kind") == "tier_ask" or fragment.get("_fetch_arm") == "tier-ask"

    def test_fetch_arm_error_is_a_failed_looked_up_stage(self):
        """`_fetch_arm == 'error'`: the fetch itself failed (the MCP tool returned an error
        item). The fragment must carry enough for the engine to record a `failed` turn at
        `stage = looked_up` with an error reply, matching every other lane's failure shape
        (AC-105, AC-107, AC-403's pattern).
        """
        from app.services.chatbot.lanes import business

        def erroring_mcp_call(name, args):
            return '{"error": "MCP tool crm_master_products_list timed out"}'

        payload = {
            "_exit_kind": "continue",
            "gate": {"compatible_entities": [{"uuid": "6136ea6b-1699-46ec-8e8e-f60c8bb64310", "entity_type": "product", "code": "SRTWB7096"}]},
            "ctx": self._master_products_ctx(),
        }
        services = type(self._services())(mcp_call=erroring_mcp_call)

        fragment = business.run_fetch(payload, services=services, dry_run=False)

        assert fragment.get("kind") == "error" or fragment.get("_fetch_arm") == "error"
        assert fragment.get("error"), "an error fragment must carry the reason text"

    def test_fetch_arm_result_delegates_business_query_with_fetch_result_attached(self):
        """`_fetch_arm == 'result'`: S6c (answer + miss) is not built yet, so the turn still
        delegates to n8n's `business_query` lane - but the fetch's own result rides along on
        `delegate_payload` so the NEXT slice (S6c) has something to build the answer from
        without re-fetching.
        """
        from app.services.chatbot.lanes import business

        payload = {
            "_exit_kind": "continue",
            "gate": {"compatible_entities": [{"uuid": "6136ea6b-1699-46ec-8e8e-f60c8bb64310", "entity_type": "product", "code": "SRTWB7096"}]},
            "ctx": self._master_products_ctx(),
        }
        fragment = business.run_fetch(
            payload,
            services=self._services('{"answers": [{"title": "SRTWB7096"}], "has_result": true}'),
            dry_run=False,
        )

        assert fragment.get("delegate") == "business_query"
        delegate_payload = fragment.get("delegate_payload") or fragment.get("payload")
        assert delegate_payload is not None, (
            "the 'result' arm must attach the fetch's own output to delegate_payload for S6c"
        )

    def test_a_resource_attachment_fetch_with_no_resolved_entity_never_ships_unfiltered(
        self,
    ) -> None:
        """Owner console defect item 4b: `TYPE_TO_PARAM` (fetch.py:292-310) and
        `entity_ids_transformer` (fetch.py:365-482) are uuid-only - an entity with no
        resolvable uuid is recorded in `_diagnostics.skipped` and contributes NO
        `*_ids` param at all. When `gate.compatible_entities` is empty (nothing
        resolved), the built args carry only `view` / `contact_id` / `space_id`, and
        `run_fetch` calls `crm_resource_attachments_list` with THAT - an unscoped
        listing of every attachment.

        `mcp_call` must never be reached with `crm_resource_attachments_list` and no
        entity-id filter key present in the args.
        """
        from app.services.chatbot.lanes import business

        FetchServices = _import_fetch_services()
        calls: list[tuple[str, dict]] = []

        def recording_mcp_call(name: str, args: dict) -> str:
            calls.append((name, dict(args)))
            return '{"answers": [{"title": "unrelated file"}], "has_result": true}'

        services = FetchServices(mcp_call=recording_mcp_call)

        payload = {
            "_exit_kind": "continue",
            # Nothing resolved - the exact shape a "attachment for <unknown thing>"
            # miss carries into the fetch step.
            "gate": {"compatible_entities": []},
            "ctx": {"parse": {"output": {"domain_hint": "resource_attachment"}}},
        }

        business.run_fetch(payload, services=services, dry_run=False)

        entity_id_keys = {
            "product_ids",
            "promotion_ids",
            "order_ids",
            "customer_ids",
            "transporter_ids",
            "form_ids",
            "shipment_ids",
            "attachment_type_ids",
            "attachment_ids",
            "certificate_ids",
        }
        unfiltered = [
            (name, args)
            for name, args in calls
            if name == "crm_resource_attachments_list" and not (entity_id_keys & set(args))
        ]
        assert not unfiltered, (
            "crm_resource_attachments_list must never be called with no entity-id filter "
            f"key present - an unresolved entity fetch must refuse instead: {unfiltered!r}"
        )
