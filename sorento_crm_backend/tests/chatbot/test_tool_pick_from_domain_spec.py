"""AC-1 to AC-6: the business lane picks its tool from `DOMAIN_SPEC`, not from a vector.

Written BEFORE the change (Phase 2, test-first). The red reason every test below should
fail with today is the OLD contract: `select_tool(db, *, query, domain, services)` embeds
the customer's message and asks `EmbeddingReadService.search_tool_chunks` for the nearest
`mcp_tool` chunk. A failure for any other reason is a defect in this file.

Why the pick is a table read now, measured rather than argued (see
`documentation/plans/chatbot/PLAN-chatbot-drop-tool-rag.md`): over every business turn in
the 7 Sep 2026 prod copy (`sorento_ai_automation_0907`, `chatbot.turns.trace`
`_tool_pick.chosen`, 740 turns) the pick was the domain's FIRST-LISTED tool on every
single turn, and not one of the twelve variants was ever chosen. The vector search was
deciding a question with one answer, at the cost of one embedding call per turn and a
runtime dependency on a seeding chain that cannot run in the deployed backend image (the
MCP catalogue is not in it), so production's tool RAG has been frozen since 2 June 2026
and every tool added since was unreachable.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.turn.policy import default_policy

# AC-1594: contracts.DOMAIN_SPEC is deleted; fetch.select_tool itself already reads
# turn.policy.default_policy() (see its own docstring) - this file's remaining direct
# references port to the same real seam, not a hand-built stand-in.
_POLICY_DOMAINS = {row.name: row for row in default_policy().domains}


# The measured historic pick, per domain, pinned by hand (UAC AC-1). This table is
# deliberately NOT derived from `DOMAIN_SPEC` - deriving it would assert that the code
# agrees with itself. It says what production chose, so a reordering of any `tools` tuple
# fails here rather than silently changing which tool answers a customer.
PINNED_PICK: dict[str, str] = {
    "inventory": "crm_inventory_stock_balance_list",
    "incoming": "crm_incoming_stock_list",
    "master_products": "crm_master_products_list",
    "order": "crm_order_management_orders_list",
    "product_attachment": "crm_master_product_attachments_list",
    "promotion": "crm_marketing_promotions_list",
    "resource_attachment": "crm_resource_attachments_list",
    "spo_allocation": "crm_procurement_spo_allocations_last_receipt_list",
    "forms": "crm_forms_management_forms_list",
    "purchase_order": "crm_procurement_po_placed_list",
    "portal_link": "crm_portal_link_get",
    # PLAN-chatbot-last-purchase-cost.md, 12 Sep 2026: a domain this plan invents, so no
    # captured turn can carry it - one tool, no choice to measure.
    "purchase_cost": "crm_procurement_po_last_cost_list",
    # R7 (PLAN-prompt-dynamic-30sep D9): the `sales` domain's first tool. A sales status
    # never reaches it (`run_fetch`'s three overrides pick), so no captured turn does.
    "sales": "crm_sales_report",
}

#: The two domains with an empty `tools` tuple. Nothing can answer them, which is why the
#: bot refuses them - a fact `DOMAIN_SPEC` already carries as `default_supported=False`.
EMPTY_TOOL_DOMAINS = ("goods_receive", "ideate")


def _tool_services(mcp_call: Any):
    """The one-seam fetch bundle. After this change `FetchServices` has ONE field."""
    from app.services.chatbot.lanes.business.services import FetchServices

    return FetchServices(mcp_call=mcp_call)


def _continue_payload(domain: str | None) -> dict[str, Any]:
    """A resolved `continue` exit carrying one product entity and a domain hint."""
    payload: dict[str, Any] = {
        "_exit_kind": "continue",
        "gate": {
            "compatible_entities": [
                {
                    "uuid": "6136ea6b-1699-46ec-8e8e-f60c8bb64310",
                    "entity_type": "product",
                    "code": "SRTWB7096",
                }
            ]
        },
    }
    if domain is not None:
        payload["ctx"] = {"parse": {"output": {"domain_hint": domain}}}
    return payload


# --------------------------------------------------------------------------- #
# AC-1 - deterministic pick
# --------------------------------------------------------------------------- #


class TestDeterministicPick:
    @pytest.mark.parametrize(("domain", "expected"), sorted(PINNED_PICK.items()))
    def test_domain_picks_its_pinned_tool(self, domain: str, expected: str) -> None:
        """One candidate, named `DOMAIN_SPEC[domain].tools[0]`, similarity 1.0."""
        assert fetch_mod.select_tool(domain) == [{"name": expected, "similarity": 1.0}]

    def test_the_pinned_table_covers_every_answerable_domain(self) -> None:
        """A new domain with tools must be pinned here, or its pick is unmeasured."""
        answerable = {d for d, spec in _POLICY_DOMAINS.items() if spec.tools}
        assert answerable == set(PINNED_PICK), (
            "every DOMAIN_SPEC domain with a non-empty `tools` tuple needs a row in "
            "PINNED_PICK naming the tool production actually chose"
        )

    def test_the_pinned_tool_is_the_first_listed_one(self) -> None:
        """The FIRST entry of each `tools` tuple is now a contract, not an ordering."""
        assert {d: spec.tools[0] for d, spec in _POLICY_DOMAINS.items() if spec.tools} == PINNED_PICK

    @pytest.mark.parametrize("domain", EMPTY_TOOL_DOMAINS)
    def test_a_domain_with_no_tools_picks_nothing(self, domain: str) -> None:
        assert _POLICY_DOMAINS[domain].tools == ()
        assert fetch_mod.select_tool(domain) == []

    def test_no_domain_and_an_unknown_domain_pick_nothing(self) -> None:
        assert fetch_mod.select_tool(None) == []
        assert fetch_mod.select_tool("") == []
        # `purchasing` is a TEAM name, not a domain - the exact value evidence turn
        # b5b19cec-dccc-4eda-b766-1aeb1362957b carried as a `domain_hint`.
        assert fetch_mod.select_tool("purchasing") == []

    def test_select_tool_takes_no_db_no_query_and_no_services(self) -> None:
        """The signature IS the contract: nothing to embed, nothing to search."""
        import inspect

        params = list(inspect.signature(fetch_mod.select_tool).parameters)
        assert params == ["domain"], (
            "select_tool reads DOMAIN_SPEC and nothing else - a `db`, `query` or "
            f"`services` parameter is the RAG contract coming back (found {params})"
        )


# --------------------------------------------------------------------------- #
# AC-2 - no embedding, no registry
# --------------------------------------------------------------------------- #


class TestNoEmbeddingNoRegistry:
    def test_spo_turn_answers_with_no_chunk_row_and_no_registry_row(
        self, session_factory, monkeypatch
    ) -> None:
        """The 8 Sep 2026 production failure, reproduced and closed.

        "last in for SRT62-GM" answered "no spo_allocation matched these" with 10 fully
        received allocations in the table, because `crm_procurement_spo_allocations_last_receipt_list`
        has no `embedding_chunks` row and no `mcp_tools` row in production - the seeding
        chain cannot run in the deployed image. On a database in exactly that state the
        turn must still call the tool.
        """
        from sqlalchemy import text as sql_text

        from app.services.chatbot.lanes import business

        db = session_factory()
        # The blank schema is the production state for this tool: nothing seeded.
        assert db.execute(sql_text("SELECT count(*) FROM embedding_chunks")).scalar() == 0
        assert db.execute(sql_text("SELECT count(*) FROM mcp_tools")).scalar() == 0

        class _NoEmbeddingReads:
            def __init__(self, *_a: Any, **_k: Any) -> None:
                raise AssertionError(
                    "the fetch lane reached EmbeddingReadService - the tool pick must "
                    "read DOMAIN_SPEC, never the embedding registry"
                )

        monkeypatch.setattr(
            "app.services.embedding_service.EmbeddingReadService", _NoEmbeddingReads
        )

        calls: list[tuple[str, dict]] = []

        def recording_mcp_call(name: str, args: dict) -> str:
            calls.append((name, dict(args)))
            return '{"answers": [{"title": "SRT62-GM"}], "has_result": true}'

        business.run_fetch(
            _continue_payload("spo_allocation"),
            services=_tool_services(recording_mcp_call),
            dry_run=False,
        )

        assert [name for name, _ in calls] == [
            "crm_procurement_spo_allocations_last_receipt_list"
        ]

    def test_the_fetch_module_names_no_embedding_seam(self) -> None:
        """H53's replacement: there is no search to leave the service layer at all."""
        from pathlib import Path

        source = Path(fetch_mod.__file__).read_text(encoding="utf-8")
        for banned in ("services.tool_search", "services.embed("):
            assert banned not in source, f"fetch.py still reaches for {banned}"


# --------------------------------------------------------------------------- #
# AC-3 - trace
# --------------------------------------------------------------------------- #


class TestTrace:
    def test_tool_pick_says_how_the_tool_was_chosen(self) -> None:
        """`source: "domain_spec"` is set by the CALLER, not by the ported node.

        `tool_filter` is graded byte for byte against 38 captures, so the field that says
        where the candidate came from is stamped on its output by `run_fetch` instead.
        """
        from app.services.chatbot.lanes import business

        calls: list[tuple[str, dict]] = []

        def recording_mcp_call(name: str, args: dict) -> str:
            calls.append((name, dict(args)))
            return '{"answers": [], "has_result": false}'

        fragment = business.run_fetch(
            _continue_payload("inventory"),
            services=_tool_services(recording_mcp_call),
            dry_run=False,
        )

        tool_pick = fragment["fetch"]["tool"]["_tool_pick"]
        assert tool_pick["chosen"] == "crm_inventory_stock_balance_list"
        assert tool_pick["rejected"] == []
        assert tool_pick["count"] == 1
        assert tool_pick["source"] == "domain_spec"
        # The trace step the drawer reads is otherwise unchanged in shape.
        assert set(tool_pick) == {"chosen", "rejected", "count", "has_product", "source"}


# --------------------------------------------------------------------------- #
# AC-4 - outcome parity
# --------------------------------------------------------------------------- #


class TestOutcomeParity:
    @pytest.mark.parametrize("domain", [None, "purchasing", *EMPTY_TOOL_DOMAINS])
    def test_no_tool_ends_not_found_with_the_same_fragment(self, domain: str | None) -> None:
        """H11 unchanged: zero tools is an ANSWERABLE outcome, not an empty turn."""
        from app.services.chatbot.lanes import business

        def refusing_mcp_call(name: str, args: dict) -> str:
            raise AssertionError(f"no tool was picked, yet {name} was called")

        fragment = business.run_fetch(
            _continue_payload(domain),
            services=_tool_services(refusing_mcp_call),
            dry_run=False,
        )

        assert fragment["kind"] == "error"
        assert fragment["outcome"] == "not_found"
        assert fragment["error"] == "no MCP tool matched this question"
        assert fragment["fetch"]["outcome"] == "not_found"


# --------------------------------------------------------------------------- #
# AC-5 - the egress guard is untouched
# --------------------------------------------------------------------------- #


class TestEgressGuardIntact:
    def test_every_picked_tool_is_on_the_allow_list(self) -> None:
        """A pick that `ensure_read_only` would refuse is a domain table defect."""
        for domain, expected in PINNED_PICK.items():
            assert expected in fetch_mod.CHATBOT_READ_ONLY_TOOLS, f"{domain} picks a refused tool"
            fetch_mod.ensure_read_only(expected)

    def test_a_write_tool_is_still_refused_at_the_egress(self) -> None:
        with pytest.raises(fetch_mod.ToolNotAllowed):
            fetch_mod.ensure_read_only("crm_order_cancel")


# --------------------------------------------------------------------------- #
# AC-6 - the seams are gone
# --------------------------------------------------------------------------- #


class TestSeamsAreGone:
    def test_fetch_services_carries_one_seam(self) -> None:
        import dataclasses

        from app.services.chatbot.lanes.business.services import FetchServices

        assert [f.name for f in dataclasses.fields(FetchServices)] == ["mcp_call"]

    def test_the_embedding_service_has_no_tool_search(self) -> None:
        from app.services.embedding_service import EmbeddingReadService

        assert not hasattr(EmbeddingReadService, "search_tool_chunks")

    @pytest.mark.parametrize(
        "name",
        [
            "collapse_tool_rows",
            "_collapse_incoming_shipments",
            "drop_by_product_without_product",
            "search_tool_chunks",
        ],
    )
    def test_the_retired_name_is_nowhere_under_app(self, name: str) -> None:
        """A grep, not an import check: a name left in a docstring is a name that will be
        reached for again."""
        from pathlib import Path

        app_root = Path(fetch_mod.__file__).resolve().parents[5] / "app"
        offenders = [
            str(path.relative_to(app_root.parent))
            for path in app_root.rglob("*.py")
            if name in path.read_text(encoding="utf-8")
        ]
        assert offenders == [], f"{name} still appears in: {', '.join(sorted(offenders))}"


class TestSalesReportToolIsOnTheReadOnlyAllowList:
    """Coder 25's own pins table (20 Sep 2026): main's deleted `test_domain_spec.py`
    pinned `crm_sales_report` in `CHATBOT_READ_ONLY_TOOLS` (the MCP catalogue's own
    read-only allow-list, `test_tool_pick_from_domain_spec.py`'s whole subject) -
    ported here rather than against the retired `contracts.DOMAIN_SPEC`, against the
    two real seams that now carry the same fact: `fetch.CHATBOT_READ_ONLY_TOOLS`
    itself, and `turn/policy_rows.py::DEFAULT_DOMAIN_ROWS`'s `order` domain row (the
    seed `chatbot_domains.tools` derives from, S9's sales-report port)."""

    def test_crm_sales_report_is_read_only(self) -> None:
        assert "crm_sales_report" in fetch_mod.CHATBOT_READ_ONLY_TOOLS, (
            "a domain that can pick crm_sales_report must never reach a write path"
        )

    def test_the_order_domain_seed_row_carries_the_tool(self) -> None:
        from app.services.chatbot.turn.policy_rows import DEFAULT_DOMAIN_ROWS

        order_row = next(row for row in DEFAULT_DOMAIN_ROWS if row["name"] == "order")
        assert "crm_sales_report" in order_row["tools"], (
            f"the order domain's seed must offer crm_sales_report as a pickable tool: "
            f"{order_row['tools']}"
        )
