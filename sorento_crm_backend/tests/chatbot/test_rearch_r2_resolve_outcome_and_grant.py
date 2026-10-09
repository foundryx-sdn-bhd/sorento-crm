"""R2 - `PLAN-chatbot-answer-half-reattach.md` slice R2 ("Measured" + "Grant before
roster"). Written test-FIRST, with no R2 implementation to read.

Contract under test (captain's brief, 20 Sep 2026), pinned exactly:

- `turn_runtime.ResolveOutcome`: a FROZEN dataclass with fields, in this order,
  `resolved_kinds, compatible_entities, predicate, resolved_candidates,
  unplaced_tokens, spec_tier, unplaced_alternatives, payload`. The first seven are
  today's 7-tuple values unchanged; `payload` is the dict `resolve_gate.run` returned
  (`resolved`, `gate`, `aggregate`, `tier_gate`, `_exit_kind`), or `None` on the two
  early returns (no entities named; the resolver raised).
- `engine.py` stops hard-coding `branch_kind="business_query"` at the
  `turn_runtime.resolve_kinds(...)` call site; `resolve_kinds` itself must enter
  `resolve_gate.run` at `ENTRY_BY_BRANCH_KIND[branch_kind]`
  (`lanes/business/__init__.py:45-49`) instead of the literal `"resolve"` it hard-codes
  today (`turn_runtime.py`, inside `resolve_kinds`).
- `turn_runtime.make_tool_runner` must hand `lanes.business.run_fetch` the RESOLVER's
  real gate (carrying `gate_reason` / `require_specific` / `customer_probe_entities`
  when the resolver produced them), not the synthetic `{"compatible_entities":
  entities}` dict built from nothing (`turn_runtime.py:1115`).
- Grant before roster (SF-1): the sales report grant must be checked before the
  resolver ever runs for an ungranted, ambiguous-customer sales-report ask.

`ResolveOutcome` does not exist yet on this branch - every test that needs it fails via
an explicit `getattr`/attribute assertion INSIDE the test body, never at import or
collection, so the rest of this file (and the whole test session) still collects and
runs. No test here imports a name that does not exist today.

Postgres not required for most of this file (`resolve_kinds` / `make_tool_runner` are
exercised directly with `resolve_gate.run` stubbed, so no DB session is ever opened);
the SF-1 supplement reuses `session_factory` + the existing harness in
`test_outstanding_lane.py` / `test_sales_report_grant_security.py`, matching how those
files already run.
"""
from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from app.services.chatbot import turn_runtime
from app.services.chatbot.lanes.business import ENTRY_BY_BRANCH_KIND
from app.services.chatbot.lanes.business import resolve_gate as resolve_gate_mod
from app.services.chatbot.turn import policy_rows
from app.services.chatbot.turn.plan import FetchSpec
from app.services.chatbot.turn.route import _domain_branch
from app.services.chatbot.turn.state import Focus

ENGINE_PY = Path(engine_mod.__file__)
TURN_RUNTIME_PY = Path(turn_runtime.__file__)

_PAYLOAD_KEYS = {"resolved", "gate", "aggregate", "tier_gate", "_exit_kind"}


def _resolve_outcome_cls():
    cls = getattr(turn_runtime, "ResolveOutcome", None)
    assert cls is not None, (
        "turn_runtime.ResolveOutcome does not exist yet - resolve_kinds still returns "
        "the bare 7-tuple"
    )
    return cls


def _ctx_with_entities(entities: list[dict[str, Any]]) -> dict[str, Any]:
    return {"parse": {"output": {"entities": entities}}}


def _stub_resolve_gate_run(
    *, gate: dict[str, Any] | None = None, exit_kind: str = "continue"
):
    """A `resolve_gate.run` stand-in returning a well-formed payload. `calls` records
    the `entry` argument of every call, so a test can assert both the call COUNT and
    which entry it was made with."""
    calls: list[Any] = []

    def stub(ctx, entry, item, **kwargs):
        calls.append(entry)
        return {
            "resolved": {"resolutions": []},
            "gate": gate if gate is not None else {"compatible_entities": []},
            "aggregate": None,
            "tier_gate": None,
            "_exit_kind": exit_kind,
        }

    return stub, calls


# --------------------------------------------------------------------------- #
# AC-1681: resolve_gate.run called once per turn; resolve_kinds returns a
# ResolveOutcome whose payload carries the five keys. Parametrized over every seeded,
# supported business domain (read off policy_rows.DEFAULT_DOMAIN_ROWS).
# --------------------------------------------------------------------------- #


def _seeded_supported_domains() -> list[str]:
    return [
        str(row["name"])
        for row in policy_rows.DEFAULT_DOMAIN_ROWS
        if row.get("supported")
    ]


class TestResolveKindsReturnsAResolveOutcome:
    def test_resolve_outcome_is_a_frozen_dataclass_with_the_contract_fields_in_order(
        self,
    ) -> None:
        cls = _resolve_outcome_cls()
        assert dataclasses.is_dataclass(cls), f"{cls!r} is not a dataclass"
        params = getattr(cls, "__dataclass_params__", None)
        assert params is not None and params.frozen is True, (
            f"ResolveOutcome must be frozen, got __dataclass_params__={params!r}"
        )
        field_names = [f.name for f in dataclasses.fields(cls)]
        # Reviewer S3 (review round, 20 Sep 2026): `unplaced_alternatives` has no
        # reader left anywhere (R6 deleted `turn/apply.py::_did_you_mean`, its only
        # consumer) and the coder's own docstring on the field names this test as
        # the reason it is still there. Dropped from the pinned list so the coder
        # can delete the dead field - this goes RED until it is deleted, which is
        # expected and correct (the field is currently still present).
        assert field_names == [
            "resolved_kinds",
            "compatible_entities",
            "predicate",
            "resolved_candidates",
            "unplaced_tokens",
            "spec_tier",
            "payload",
            # RESOLVER-TOKEN-CAP: the tokens the resolver cut at its ceiling, as
            # typed, defaulted so the two early returns stay seven-positional.
            "not_checked",
        ], f"ResolveOutcome field order/names do not match the contract: {field_names!r}"

    @pytest.mark.parametrize("domain", _seeded_supported_domains())
    def test_resolve_gate_run_called_once_and_payload_has_the_five_keys(
        self, domain: str, monkeypatch
    ) -> None:
        stub, calls = _stub_resolve_gate_run()
        monkeypatch.setattr(resolve_gate_mod, "run", stub)

        branch_kind = _domain_branch([domain])
        ctx = _ctx_with_entities(
            [{"raw": "TESTCODE1", "hint": "product", "canonical_code": "TESTCODE1"}]
        )

        result = turn_runtime.resolve_kinds(
            object(), ctx=ctx, branch_kind=branch_kind, space_id=None, dry_run=True
        )

        assert len(calls) == 1, (
            f"domain={domain!r} (branch_kind={branch_kind!r}): resolve_gate.run was "
            f"called {len(calls)} time(s), expected exactly 1"
        )

        cls = _resolve_outcome_cls()
        assert isinstance(result, cls), (
            f"resolve_kinds returned {type(result).__name__}, expected ResolveOutcome "
            f"(domain={domain!r})"
        )
        assert result.payload is not None, (
            f"domain={domain!r}: ResolveOutcome.payload is None for a turn that "
            "actually resolved"
        )
        assert _PAYLOAD_KEYS <= set(result.payload.keys()), (
            f"domain={domain!r}: ResolveOutcome.payload is missing keys - got "
            f"{sorted(result.payload.keys())!r}, need at least {sorted(_PAYLOAD_KEYS)!r}"
        )

    def test_payload_is_none_when_the_message_named_no_entities(self) -> None:
        result = turn_runtime.resolve_kinds(
            object(),
            ctx=_ctx_with_entities([]),
            branch_kind="business_query",
            space_id=None,
            dry_run=True,
        )
        cls = _resolve_outcome_cls()
        assert isinstance(result, cls), (
            f"resolve_kinds returned {type(result).__name__}, expected ResolveOutcome"
        )
        assert result.payload is None, (
            f"the early-return (no entities) shape must carry payload=None, got "
            f"{result.payload!r}"
        )

    def test_payload_is_none_when_the_resolver_raised(self, monkeypatch) -> None:
        def raising_run(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(resolve_gate_mod, "run", raising_run)

        result = turn_runtime.resolve_kinds(
            object(),
            ctx=_ctx_with_entities(
                [{"raw": "TESTCODE1", "hint": "product", "canonical_code": "TESTCODE1"}]
            ),
            branch_kind="business_query",
            space_id=None,
            dry_run=True,
        )
        cls = _resolve_outcome_cls()
        assert isinstance(result, cls), (
            f"resolve_kinds returned {type(result).__name__}, expected ResolveOutcome"
        )
        assert result.payload is None, (
            f"the early-return (resolver raised) shape must carry payload=None, got "
            f"{result.payload!r}"
        )


# --------------------------------------------------------------------------- #
# Real branch_kind: resolve_kinds enters resolve_gate.run at
# ENTRY_BY_BRANCH_KIND[branch_kind], not the literal "resolve"; engine.py stops
# hard-coding branch_kind="business_query" at the call site.
# --------------------------------------------------------------------------- #


class TestRealBranchKindReachesTheResolver:
    @pytest.mark.parametrize(
        "branch_kind,expected_entry", sorted(ENTRY_BY_BRANCH_KIND.items())
    )
    def test_resolve_kinds_enters_at_the_mapped_entry(
        self, branch_kind: str, expected_entry: str, monkeypatch
    ) -> None:
        stub, calls = _stub_resolve_gate_run()
        monkeypatch.setattr(resolve_gate_mod, "run", stub)

        turn_runtime.resolve_kinds(
            object(),
            ctx=_ctx_with_entities(
                [{"raw": "TESTCODE1", "hint": "product", "canonical_code": "TESTCODE1"}]
            ),
            branch_kind=branch_kind,
            space_id=None,
            dry_run=True,
        )

        assert calls == [expected_entry], (
            f"resolve_kinds(branch_kind={branch_kind!r}) called resolve_gate.run with "
            f"entry={calls[0] if calls else None!r}, expected {expected_entry!r} "
            "(ENTRY_BY_BRANCH_KIND lookup)"
        )

    def test_engine_does_not_hard_code_business_query_at_the_resolve_kinds_call_site(
        self,
    ) -> None:
        """Source scan: `engine.py`'s own `turn_runtime.resolve_kinds(...)` call must
        compute the real branch_kind (so a promotion ask can reach entry="access_check"),
        not pass the literal string "business_query" regardless of what turn this is."""
        source = ENGINE_PY.read_text(encoding="utf-8")
        marker = "turn_runtime.resolve_kinds("
        idx = source.find(marker)
        assert idx != -1, "engine.py no longer calls turn_runtime.resolve_kinds(...) at all"
        call_block = source[idx : idx + 1200]
        assert 'branch_kind="business_query"' not in call_block, (
            "engine.py still hard-codes branch_kind=\"business_query\" at the "
            "turn_runtime.resolve_kinds(...) call site - a promotion ask can never "
            "reach resolve_gate.run with entry=\"access_check\" while this literal stays"
        )


# --------------------------------------------------------------------------- #
# AC-1682: the tool runner receives the resolver's REAL gate; the synthetic
# {"compatible_entities": entities} construction is gone.
# --------------------------------------------------------------------------- #


class TestMakeToolRunnerCarriesTheRealGate:
    def test_synthetic_gate_literal_no_longer_exists_in_turn_runtime(self) -> None:
        source = TURN_RUNTIME_PY.read_text(encoding="utf-8")
        assert '{"compatible_entities": entities}' not in source, (
            "turn_runtime.py still builds a synthetic gate = {\"compatible_entities\": "
            "entities} from nothing - the resolver's real gate_reason / "
            "require_specific / customer_probe_entities never reach lanes.business."
            "run_fetch while this literal stays"
        )

    def test_run_fetch_receives_the_resolvers_gate_reason_and_require_specific(
        self, monkeypatch
    ) -> None:
        """Ambiguity flagged to the captain: `make_tool_runner` has no parameter today
        that carries the resolver's own gate object through to `lanes.business.
        run_fetch` - only the narrowed `compatible_entities` list reaches it. This
        test's own choice of keyword name for the new parameter the fix needs
        (`resolver_gate`) is a guess, not read off any committed R2 source (none
        exists yet). A coder who names it differently only needs to update this
        test's CALL SITE; the assertions below (what `lanes.business.run_fetch` must
        receive) are the actual contract and should not need to change.
        """
        from app.services.chatbot.lanes import business

        calls: list[dict[str, Any]] = []

        def stub_run_fetch(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            calls.append(payload)
            return {"has_result": False}

        monkeypatch.setattr(business, "run_fetch", stub_run_fetch)

        sig = inspect.signature(turn_runtime.make_tool_runner)
        assert "resolver_gate" in sig.parameters, (
            "make_tool_runner has no parameter carrying the resolver's own gate "
            "through to lanes.business.run_fetch yet (checked via "
            "inspect.signature) - this tester's own chosen name is 'resolver_gate'"
        )

        resolver_gate = {
            "compatible_entities": [
                {"raw": "ignored-by-narrowing", "entity_type": "product"}
            ],
            "gate_reason": "no matching customer",
            "require_specific": True,
            "customer_probe_entities": [
                {"raw": "srtwc286", "entity_type": "product", "canonical_code": "SRTWC286"}
            ],
        }

        runner = turn_runtime.make_tool_runner(
            object(),
            ctx={"parse": {"output": {}}},
            verdict={"access_levels": []},
            focus=Focus(),
            compatible_entities=[
                {
                    "raw": "SRTWC286",
                    "entity_type": "product",
                    "canonical_code": "SRTWC286",
                }
            ],
            predicate=None,
            unplaced={},
            space_id=None,
            dry_run=True,
            turn_trace=None,
            resolver_gate=resolver_gate,
        )
        spec = FetchSpec(domain="order", entities=[], filters={}, date_window=None)
        runner("order", spec)

        assert calls, "lanes.business.run_fetch was never called"
        payload_gate = calls[0].get("gate") or {}
        assert payload_gate.get("gate_reason") == "no matching customer", (
            f"lanes.business.run_fetch's payload['gate'] dropped gate_reason: "
            f"{payload_gate!r}"
        )
        assert payload_gate.get("require_specific") is True, (
            f"lanes.business.run_fetch's payload['gate'] dropped require_specific: "
            f"{payload_gate!r}"
        )
        assert (
            payload_gate.get("customer_probe_entities")
            == resolver_gate["customer_probe_entities"]
        ), (
            f"lanes.business.run_fetch's payload['gate'] dropped "
            f"customer_probe_entities: {payload_gate!r}"
        )


# --------------------------------------------------------------------------- #
# F2 stamp scope (pytest half of AC-1694): the customer stamp probe must receive
# customer_probe_entities (which carries the product), not the customer-only
# compatible_entities list the gate's picker arm replaces it with.
# --------------------------------------------------------------------------- #


class TestCustomerStampProbeReceivesTheEnrichedEntities:
    def test_probe_customer_gets_the_product_alongside_the_customers(
        self, monkeypatch
    ) -> None:
        product_entity = {
            "uuid": "prod-1",
            "entity_type": "product",
            "canonical_code": "SRTWC286",
        }
        customer_only_compatible = [
            {"uuid": "cust-1", "entity_type": "customer", "code": "300-H070"},
            {"uuid": "cust-2", "entity_type": "customer", "code": "300-H071"},
        ]
        enriched_customer_probe_entities = customer_only_compatible + [product_entity]

        stub_run, _calls = _stub_resolve_gate_run(
            gate={
                "compatible_entities": customer_only_compatible,
                "customer_probe_entities": enriched_customer_probe_entities,
            },
            exit_kind="offer",
        )
        monkeypatch.setattr(resolve_gate_mod, "run", stub_run)
        # Isolate this test from a real DB: fill_customer_names fails soft (a bare
        # object() already makes it fail-soft via its own try/except), but a no-op
        # keeps this test's intent legible without relying on that incidental safety.
        monkeypatch.setattr(turn_runtime, "fill_customer_names", lambda db, entities: None)

        captured_entities: list[list[dict[str, Any]]] = []

        def spy_probe_customer(services, *, ctx, entities, aggregate, default_start, space_id):
            captured_entities.append(entities)
            return {"items": []}

        monkeypatch.setattr(resolve_gate_mod, "probe_customer", spy_probe_customer)

        ctx = _ctx_with_entities(
            [
                {"raw": "hanlim", "hint": "customer", "canonical_code": None},
                {"raw": "srtwc286", "hint": "product", "canonical_code": "SRTWC286"},
            ]
        )

        turn_runtime.resolve_kinds(
            object(),
            ctx=ctx,
            branch_kind="business_query",
            space_id=None,
            dry_run=True,
            stamp_customer=True,
        )

        assert captured_entities, "resolve_gate.probe_customer was never called"
        probed = captured_entities[0]
        assert product_entity in probed, (
            f"the customer stamp probe was called with {probed!r}, missing the "
            f"product entity {product_entity!r} that customer_probe_entities "
            "carries - the probe measures the wrong population (every DO for this "
            "customer, not just this product's), so a stamp like 'no DO' can be "
            "wrong for the product actually asked (F2)"
        )


# --------------------------------------------------------------------------- #
# AC-1686 / SF-1 supplement: `test_sales_report_grant_security.py::
# TestSF1RosterMustNotPrecedeTheGrantRefusal` already pins RED that no MCP tool call
# happens and no roster reaches the reply for an ungranted, ambiguous-customer sales
# report ask. What it does NOT yet pin (this round's own gap): that the RESOLVER
# itself - `resolve_gate.run` / `turn_runtime.resolve_kinds` - is never called at all.
# The roster the existing test proves leaks is BUILT from the resolver's own output
# (the `must_narrow_one` customer roster over `resolved_candidates`), so "grant before
# roster" has to move the check to before THIS call, not merely before the MCP tool
# call - a fix that only guards the tool call would leave the resolver (and so a real
# customer lookup) running for an ungranted contact.
#
# Reuses the SAME harness and fixtures as `test_sales_report_grant_security.py` and
# `test_outstanding_lane.py` - no new harness invented, per the brief.
# --------------------------------------------------------------------------- #

from tests.chatbot.test_outstanding_lane import (  # noqa: E402
    _ambiguous_hanlim_resolve_services,
    _qf,
    _run_turn,
    _seed_contact,
    _session_of,
)
from tests.chatbot.test_sales_report_grant_security import _no_probe  # noqa: E402


class TestSF1TheResolverItselfIsNeverCalledForTheUngrantedAsk:
    @pytest.mark.parametrize(
        "text_body,extra_qf",
        [
            pytest.param("sales report for hanlim", {}, id="fresh-ask"),
            pytest.param(
                "dealer sales report for hanlim",
                {"sales_channel": "dealer"},
                id="channel-worded-ask",
            ),
        ],
    )
    def test_resolve_gate_run_and_resolve_kinds_are_never_called(
        self, session_factory, monkeypatch, text_body: str, extra_qf: dict[str, Any]
    ) -> None:
        calls_run: list[Any] = []
        calls_resolve_kinds: list[Any] = []
        original_run = resolve_gate_mod.run
        original_resolve_kinds = engine_mod.turn_runtime.resolve_kinds

        def spy_run(*args: Any, **kwargs: Any) -> Any:
            calls_run.append(args[1] if len(args) > 1 else kwargs.get("entry"))
            return original_run(*args, **kwargs)

        def spy_resolve_kinds(*args: Any, **kwargs: Any) -> Any:
            calls_resolve_kinds.append(kwargs.get("branch_kind"))
            return original_resolve_kinds(*args, **kwargs)

        monkeypatch.setattr(resolve_gate_mod, "run", spy_run)
        monkeypatch.setattr(engine_mod.turn_runtime, "resolve_kinds", spy_resolve_kinds)

        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status="sales_report",
                entities=[
                    {
                        "raw": "hanlim",
                        "hint": "customer",
                        "canonical_code": None,
                        "current_message": True,
                        "confident": True,
                    }
                ],
                **extra_qf,
            ),
            text_body=text_body,
            msg_id=f"ZZT-sf1-spy-{text_body[:10]}-1",
            attributes=[],  # no grant at all
            resolve_services=_ambiguous_hanlim_resolve_services(_no_probe),
        )

        assert calls_resolve_kinds == [], (
            f"turn_runtime.resolve_kinds was called {len(calls_resolve_kinds)} "
            f"time(s) (branch_kind values: {calls_resolve_kinds!r}) for an ungranted "
            "sales report ask - the grant check must run before the resolver, not "
            "only before the MCP tool call"
        )
        assert calls_run == [], (
            f"resolve_gate.run was called {len(calls_run)} time(s) (entry values: "
            f"{calls_run!r}) for an ungranted sales report ask"
        )
        assert captured == [], (
            f"no MCP tool call may run either: {captured}"
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        assert not open_question, (
            f"no Pending may be written for a turn refused before the resolver ran: "
            f"{open_question}"
        )
