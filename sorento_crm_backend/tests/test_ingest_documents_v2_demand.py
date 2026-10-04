"""Group V2 - Demand classification on sales-order ingest (PLAN section 1 D4, 2.3).

  AC-V2-1   stored order_type already classifies -> demand_class from it; the
            payload's order_type never overwrites what is already stored (fill-only)
  AC-V2-2   no stored order_type, payload order_type classifies -> order_type is
            filled AND demand_class is derived from it
  AC-V2-3   neither order_type -> the linked agent's demand_class is written
  AC-V2-4   none of those -> the linked customer's market segment decides it
  AC-V2-5   nothing classifies -> record still lands, demand_class stays NULL,
            verdict warnings carries "unclassified_demand"
  AC-V2-6   a stored demand_class is never downgraded or blanked by a later push
            that cannot classify (even one that names a conflicting segment)
  AC-V2-7   `demand_class` is not an accepted payload key (extra="forbid")

PLAN-demand-class-agent-arrival.md's UAC AC-1..AC-12 (the agent arriving on a LATER push):
  AC-1   no agent, retail segment lands retail; a second push naming a project agent
         takes over
  AC-2   mirror of AC-1: project segment, arriving retail agent takes over
  AC-3   stored class AND a stored agent, a later push names a DIFFERENT agent: the
         class is unchanged (AC-12: the stored agent id still moves A -> B)
  AC-4   stored class, no stored agent, the arriving agent has no class: unchanged
  AC-5   stored class, no stored agent, stored order_type says project, arriving agent
         is retail: order type still outranks the agent
  AC-6   a hand-set class on an order that already HAS an agent survives every later
         push, INCLUDING an agent-less push followed by a push naming a DIFFERENT
         agent (fix round 1: an agent-less push must not blank the stored agent id,
         or that second push would be misread as the agent "arriving" and re-decide
         the hand-set class)
  AC-7   first push unclassified + warns; the push bringing a classed agent fills it
         (existing behaviour, asserted so it cannot regress)
  AC-11  (fix round 1, Seam 1b) an agent-less push never blanks a stored
         `sales_agent_id`
  AC-12  folded into AC-3's own test: a later push naming a DIFFERENT agent still
         replaces the stored agent id

Plus a direct parity test of `app.services.scm.demand_class.classify_document`,
the pure ladder function PLAN section 1 D4 extracts out of
`outstanding_import_service._classify_demand`.

Substrate reused verbatim from `tests.test_ingest_documents` per the tester brief:
the blank scratch schema, the `env` fixture (two companies + one linked master of
every kind), `_so_record` / `_so_line` / `_ref`, the `ZZTDOC` marker. `order_type`
is a NEW optional header field `CanonicalSalesOrder` does not declare yet, so
every test that sends it is expected to fail today on `extra="forbid"` - that is
the RED reason for this slice, not a fixture bug. `sales_agent_ref` and
`customer_ref` are v1 keys already accepted; using them here (rather than the
S1 code/name fallbacks) keeps this file's RED status independent of S1's
progress in the same worktree.
"""
from __future__ import annotations

import uuid

from app.models.access import MarketSegment
from app.models.order import Customer, SalesOrder
from app.models.sales_agent import SalesAgent

from tests._pg_fixture import unique_code
from tests.test_ingest_documents import (
    INGEST_SO,
    MARKER,
    _ref,
    _so_record,
    env,  # noqa: F401 - pytest fixture, imported for reuse per the tester brief
)

__all__ = ["env"]


# ------------------------------------------------------------------ seed helpers
def _seed_so_header(
    env, *, order_type=None, demand_class=None, ref=None, number=None, sales_agent_id=None
):
    """A `sales_orders` header written DIRECTLY (not through the API), because
    `order_type` and `demand_class` are not payload fields - the only way a
    header carries them today is a prior import / CS edit. `sales_agent_id`
    is direct too, for PLAN-demand-class-agent-arrival.md's AC-3/AC-6: a
    stored agent has to already be on the row BEFORE the push under test, not
    resolved by it."""
    row = SalesOrder(
        id=str(uuid.uuid4()),
        company_id=env.company_a,
        so_number=number or f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
        status="open",
        order_type=order_type,
        demand_class=demand_class,
        sales_agent_id=sales_agent_id,
        source_system="autocount",
    )
    env.db.add(row)
    env.db.flush()
    source_ref = ref or _ref("SO")
    env.refs.link(entity_type="sales_orders", entity_id=row.id, source_ref=source_ref)
    return row, source_ref


def _market_segment(env, *, stem: str) -> MarketSegment:
    row = MarketSegment(code=unique_code(stem), name=f"{MARKER} {stem} segment")
    env.db.add(row)
    env.db.flush()
    return row


def _customer_with_segment(env, *, segment_code: str) -> Customer:
    row = Customer(
        customer_code=unique_code(MARKER),
        customer_name=f"{MARKER} segment customer",
        company_id=env.company_a,
        market_segment_code=segment_code,
    )
    env.db.add(row)
    env.db.flush()
    return row


def _linked_customer_ref(env, *, segment_code: str) -> str:
    customer = _customer_with_segment(env, segment_code=segment_code)
    return env._link("customers", customer.id, "DEBTOR-SEG")


def _agent_with_class(env, *, demand_class: str) -> SalesAgent:
    row = SalesAgent(
        sales_agent=f"{MARKER}-{uuid.uuid4().hex[:6].upper()}", demand_class=demand_class
    )
    env.db.add(row)
    env.db.flush()
    return row


def _linked_agent_ref(env, *, demand_class: str) -> str:
    agent = _agent_with_class(env, demand_class=demand_class)
    return env._link("sales_agents", agent.id, "AGENT-DC")


# ================================================================== AC-V2-1
class TestStoredOrderTypeWinsOverPayload:
    def test_demand_class_comes_from_stored_order_type_and_it_is_not_overwritten(self, env):
        header, source_ref = _seed_so_header(env, order_type="Project Alpha")

        record = _so_record(env, ref=source_ref, number=header.so_number, order_type="Dealer")
        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", res.text
        stored = env.header("sales_orders", source_ref)
        assert stored["order_type"] == "Project Alpha", (
            "fill-only: the payload's order_type must never restate an already-"
            f"stored value, got {stored['order_type']!r}"
        )
        assert stored["demand_class"] == "project", stored


# ================================================================== AC-V2-2
class TestPayloadOrderTypeFillsAnAbsentHeader:
    def test_project_order_type_fills_the_header_and_classifies_project(self, env):
        record = _so_record(env, order_type="project")

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        assert header["order_type"] == "project"
        assert header["demand_class"] == "project"

    def test_dealer_order_type_fills_the_header_and_classifies_retail(self, env):
        record = _so_record(env, order_type="dealer")

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        assert header["order_type"] == "dealer"
        assert header["demand_class"] == "retail"


# ================================================================== AC-V2-3
class TestAgentDemandClassIsThirdRung:
    def test_linked_agent_demand_class_is_written_when_no_order_type_states_anything(self, env):
        agent_ref = _linked_agent_ref(env, demand_class="project")
        record = _so_record(env, sales_agent_ref=agent_ref)

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        assert header["demand_class"] == "project"


# ================================================================== AC-V2-4
class TestCustomerSegmentIsFourthRung:
    def test_project_shaped_segment_classifies_project(self, env):
        segment = _market_segment(env, stem="PROJECT")
        customer_ref = _linked_customer_ref(env, segment_code=segment.code)
        record = _so_record(env, customer_ref=customer_ref)

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        assert header["demand_class"] == "project"

    def test_retail_shaped_segment_classifies_retail(self, env):
        segment = _market_segment(env, stem="RETAIL")
        customer_ref = _linked_customer_ref(env, segment_code=segment.code)
        record = _so_record(env, customer_ref=customer_ref)

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        assert header["demand_class"] == "retail"


# ================================================================== AC-V2-5
class TestNothingClassifies:
    def test_record_still_lands_demand_class_null_and_warns_unclassified(self, env):
        record = _so_record(env)  # no order_type, no agent, no customer

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "unclassified_demand" in entry.get("warnings", []), entry
        header = env.header("sales_orders", record["source_ref"])
        assert header["demand_class"] is None


# ================================================================== AC-V2-6
class TestStoredDemandClassNeverDowngraded:
    def test_a_later_unclassifiable_push_leaves_a_stored_class_untouched(self, env):
        header, source_ref = _seed_so_header(env, demand_class="project")
        # A conflicting signal that WOULD classify as "retail" if the ladder ran
        # from scratch - proving the guard is "stored wins outright", not merely
        # "no worse answer happened to be available".
        segment = _market_segment(env, stem="RETAIL")
        customer_ref = _linked_customer_ref(env, segment_code=segment.code)

        record = _so_record(env, ref=source_ref, number=header.so_number, customer_ref=customer_ref)
        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", res.text
        stored = env.header("sales_orders", source_ref)
        assert stored["demand_class"] == "project", (
            "a stored demand_class must never be downgraded or blanked by a "
            f"later push, got {stored['demand_class']!r}"
        )
        assert "unclassified_demand" not in entry.get("warnings", []), entry


# ================================================================== AC-V2-7
class TestDemandClassIsNeverAPayloadField:
    def test_demand_class_in_the_payload_is_dropped_never_written(self, env):
        # AC-V2-7, as amended by the owner (1 Oct 2026): unknown keys are dropped rather
        # than refused. `demand_class` is still never a payload field - it is DERIVED - so
        # the push lands and the stated class reaches no column: with no customer and no
        # agent the ladder has nothing to classify from.
        record = _so_record(env, demand_class="project")

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert env.header("sales_orders", record["source_ref"])["demand_class"] != "project"


# ==================================================== agent-arrival AC-1 / AC-2
class TestAgentArrivingOnALaterPushTakesOverFromTheSegment:
    def test_agent_project_takes_over_from_a_retail_segment(self, env):
        """AC-1: no agent, retail segment lands retail; a classed agent on the
        second push ends project."""
        segment = _market_segment(env, stem="RETAIL")
        customer_ref = _linked_customer_ref(env, segment_code=segment.code)
        record = _so_record(env, customer_ref=customer_ref)

        res = env.post(INGEST_SO, [record])
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        first = env.header("sales_orders", record["source_ref"])
        assert first["demand_class"] == "retail"
        assert first["sales_agent_id"] is None

        agent_ref = _linked_agent_ref(env, demand_class="project")
        record2 = _so_record(
            env,
            ref=record["source_ref"],
            number=record["so_number"],
            customer_ref=customer_ref,
            sales_agent_ref=agent_ref,
        )
        res2 = env.post(INGEST_SO, [record2])
        entry2 = res2.json()["records"][0]
        assert entry2["outcome"] == "updated", res2.text
        stored = env.header("sales_orders", record["source_ref"])
        assert stored["demand_class"] == "project"
        assert stored["sales_agent_id"] is not None

    def test_agent_retail_takes_over_from_a_project_segment(self, env):
        """AC-2: mirror of AC-1, a retail agent takes over from a project segment."""
        segment = _market_segment(env, stem="PROJECT")
        customer_ref = _linked_customer_ref(env, segment_code=segment.code)
        record = _so_record(env, customer_ref=customer_ref)

        res = env.post(INGEST_SO, [record])
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        first = env.header("sales_orders", record["source_ref"])
        assert first["demand_class"] == "project"

        agent_ref = _linked_agent_ref(env, demand_class="retail")
        record2 = _so_record(
            env,
            ref=record["source_ref"],
            number=record["so_number"],
            customer_ref=customer_ref,
            sales_agent_ref=agent_ref,
        )
        res2 = env.post(INGEST_SO, [record2])
        entry2 = res2.json()["records"][0]
        assert entry2["outcome"] == "updated", res2.text
        stored = env.header("sales_orders", record["source_ref"])
        assert stored["demand_class"] == "retail"


# ==================================================== agent-arrival AC-3 / AC-12
class TestStoredAgentAlreadySettlesTheClass:
    def test_a_later_push_naming_a_different_agent_does_not_change_the_class(self, env):
        """AC-3: the class does not change. AC-12: the stored agent id still DOES
        change (A -> B) - only the class is settled, not the agent column."""
        first_agent = _agent_with_class(env, demand_class="retail")
        header, source_ref = _seed_so_header(
            env, demand_class="retail", sales_agent_id=first_agent.id
        )
        other_agent = _agent_with_class(env, demand_class="project")
        other_agent_ref = env._link("sales_agents", other_agent.id, "AGENT-DC")

        record = _so_record(
            env, ref=source_ref, number=header.so_number, sales_agent_ref=other_agent_ref
        )
        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", res.text
        stored = env.header("sales_orders", source_ref)
        assert stored["demand_class"] == "retail"
        assert str(stored["sales_agent_id"]) == other_agent.id


# =========================================================== agent-arrival AC-4
class TestArrivingAgentWithNoDemandClassNeverBlanksTheStoredOne:
    def test_class_is_unchanged_when_the_arriving_agent_has_no_class_of_its_own(self, env):
        header, source_ref = _seed_so_header(env, demand_class="retail")
        agent_ref = _linked_agent_ref(env, demand_class=None)

        record = _so_record(
            env, ref=source_ref, number=header.so_number, sales_agent_ref=agent_ref
        )
        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", res.text
        stored = env.header("sales_orders", source_ref)
        assert stored["demand_class"] == "retail"


# =========================================================== agent-arrival AC-5
class TestStoredOrderTypeOutranksTheArrivingAgent:
    def test_order_type_still_wins_over_a_conflicting_arriving_agent(self, env):
        header, source_ref = _seed_so_header(
            env, order_type="Project Alpha", demand_class="project"
        )
        agent_ref = _linked_agent_ref(env, demand_class="retail")

        record = _so_record(
            env, ref=source_ref, number=header.so_number, sales_agent_ref=agent_ref
        )
        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", res.text
        stored = env.header("sales_orders", source_ref)
        assert stored["demand_class"] == "project"
        assert stored["order_type"] == "Project Alpha"


# =========================================================== agent-arrival AC-6
class TestHandSetClassWithAnAgentAlreadyStoredSurvives:
    def test_survives_a_later_push_that_would_classify_differently_from_scratch(self, env):
        agent = _agent_with_class(env, demand_class="retail")
        header, source_ref = _seed_so_header(
            env, demand_class="project", sales_agent_id=agent.id
        )
        segment = _market_segment(env, stem="RETAIL")
        customer_ref = _linked_customer_ref(env, segment_code=segment.code)

        record = _so_record(
            env, ref=source_ref, number=header.so_number, customer_ref=customer_ref
        )
        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", res.text
        stored = env.header("sales_orders", source_ref)
        assert stored["demand_class"] == "project"

    def test_survives_an_agent_less_push_followed_by_a_different_retail_agent(self, env):
        """Strengthened AC-6 (fix round 1): the seam-1b regression the reviewers
        reproduced. An agent-less re-push must not blank the stored agent id -
        if it did, a LATER push naming a DIFFERENT (retail) agent would find no
        stored agent, treat it as the agent "arriving" for the first time, and
        re-decide the hand-set `project` class down to `retail`."""
        agent = _agent_with_class(env, demand_class="retail")
        header, source_ref = _seed_so_header(
            env, demand_class="project", sales_agent_id=agent.id
        )

        agentless_record = _so_record(env, ref=source_ref, number=header.so_number)
        res1 = env.post(INGEST_SO, [agentless_record])
        entry1 = res1.json()["records"][0]
        assert entry1["outcome"] == "updated", res1.text
        after_first = env.header("sales_orders", source_ref)
        assert after_first["demand_class"] == "project"
        assert str(after_first["sales_agent_id"]) == agent.id

        other_agent_ref = _linked_agent_ref(env, demand_class="retail")
        record2 = _so_record(
            env, ref=source_ref, number=header.so_number, sales_agent_ref=other_agent_ref
        )
        res2 = env.post(INGEST_SO, [record2])
        entry2 = res2.json()["records"][0]
        assert entry2["outcome"] == "updated", res2.text
        stored = env.header("sales_orders", source_ref)
        assert stored["demand_class"] == "project"


# ========================================================== agent-arrival AC-11
class TestAgentlessPushNeverBlanksAStoredAgent:
    def test_stored_sales_agent_id_survives_an_agent_less_push(self, env):
        agent = _agent_with_class(env, demand_class="retail")
        header, source_ref = _seed_so_header(
            env, demand_class="retail", sales_agent_id=agent.id
        )

        record = _so_record(env, ref=source_ref, number=header.so_number)
        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", res.text
        stored = env.header("sales_orders", source_ref)
        assert str(stored["sales_agent_id"]) == agent.id
        assert stored["demand_class"] == "retail"


# =========================================================== agent-arrival AC-7
class TestFirstPushUnclassifiedThenAgentArrivalFillsIt:
    def test_unclassified_first_push_then_a_classed_agent_fills_it(self, env):
        record = _so_record(env)  # no order_type, no agent, no customer

        res = env.post(INGEST_SO, [record])
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "unclassified_demand" in entry.get("warnings", []), entry
        first = env.header("sales_orders", record["source_ref"])
        assert first["demand_class"] is None

        agent_ref = _linked_agent_ref(env, demand_class="project")
        record2 = _so_record(
            env,
            ref=record["source_ref"],
            number=record["so_number"],
            sales_agent_ref=agent_ref,
        )
        res2 = env.post(INGEST_SO, [record2])
        entry2 = res2.json()["records"][0]
        assert entry2["outcome"] == "updated", res2.text
        assert "unclassified_demand" not in entry2.get("warnings", []), entry2
        stored = env.header("sales_orders", record["source_ref"])
        assert stored["demand_class"] == "project"


# ============================================================ parity: the ladder
class TestClassifyDocumentSignatureAndLadder:
    """`classify_document` per PLAN section 1 D4:

        classify_document(db, *, stored_order_type, stated_order_type,
                           agent_demand_class, debtor_code, company_id) -> Optional[str]

    Importing it is itself the RED signal today (the function does not exist
    yet - it is still inlined as `_classify_demand` /
    `_class_of` / `_segment_of` in `outstanding_import_service.py`). Imported
    inside each test body so a missing function fails one test at a time
    rather than the whole module at collection.
    """

    def test_stored_order_type_outranks_everything_else(self, env):
        from app.services.scm.demand_class import classify_document

        result = classify_document(
            env.db,
            stored_order_type="Project Alpha",
            stated_order_type="Dealer",
            agent_demand_class="retail",
            debtor_code="does-not-matter",
            company_id=env.company_a,
        )
        assert result == "project"

    def test_stated_order_type_is_the_second_rung(self, env):
        from app.services.scm.demand_class import classify_document

        project = classify_document(
            env.db,
            stored_order_type=None,
            stated_order_type="project",
            agent_demand_class=None,
            debtor_code=None,
            company_id=env.company_a,
        )
        assert project == "project"

        retail = classify_document(
            env.db,
            stored_order_type=None,
            stated_order_type="dealer",
            agent_demand_class=None,
            debtor_code=None,
            company_id=env.company_a,
        )
        assert retail == "retail"

    def test_agent_demand_class_is_the_third_rung(self, env):
        from app.services.scm.demand_class import classify_document

        result = classify_document(
            env.db,
            stored_order_type=None,
            stated_order_type=None,
            agent_demand_class="project",
            debtor_code=None,
            company_id=env.company_a,
        )
        assert result == "project"

    def test_customer_segment_is_the_fourth_and_last_rung(self, env):
        from app.services.scm.demand_class import classify_document

        segment = _market_segment(env, stem="PROJECT")
        customer = _customer_with_segment(env, segment_code=segment.code)

        result = classify_document(
            env.db,
            stored_order_type=None,
            stated_order_type=None,
            agent_demand_class=None,
            debtor_code=customer.customer_code,
            company_id=env.company_a,
        )
        assert result == "project"

    def test_nothing_classifies_returns_none(self, env):
        from app.services.scm.demand_class import classify_document

        result = classify_document(
            env.db,
            stored_order_type=None,
            stated_order_type=None,
            agent_demand_class=None,
            debtor_code=None,
            company_id=env.company_a,
        )
        assert result is None
