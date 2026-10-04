"""Group V1 - code and name fallbacks, back-create (PLAN section 2.2, D1/D2/D9/D10).

  AC-V1-1   customer_ref wins over customer_code; debtor_code is still written
  AC-V1-2   no ref, customer_code matches an existing row in the anchor company only
  AC-V1-3   code+name match nothing -> a customer is back-created and linked
  AC-V1-4   code only, no name, no match -> lands unlinked, "customer_unresolved"
  AC-V1-5   purchase order supplier ladder: code, cleaned name, back-create
  AC-V1-6   sales-order agent_code ladder: sales_agents.sales_agent, back-create
  AC-V1-7   line product_code ladder; a miss on a SENT code stays retryable
  AC-V1-7b  a sent-but-unresolved warehouse lands NULL with a warning (v2 deviation)
  AC-V1-8   superseded (autocount-brands-ingest AC-19b, BL-056 D15): a ladder
            ref (customer_ref/supplier_ref) scoped to another company is
            invisible under this anchor, same as an unresolved one - so it is
            `retryable`, not `failed`, new fields present or not
  AC-V1-9   PO line currency follows its header's resolved currency when
            unstated (header still CNY-fills when it states none itself)
  AC-V1-10  dry_run creates no master; the verdict still reports what would happen

Every test seeds its own chain on the blank scratch schema (`tests._pg_fixture`),
reusing the `env` fixture and record builders from `test_ingest_documents` per the
tester brief - no new substrate. None of `customer_code`, `customer_name`,
`agent_code`, `supplier_code`, `supplier_name`, `product_code`, `warehouse_code`
exist on the canonical schemas yet, so most records here are rejected by
`extra="forbid"` today: that ValidationError, surfacing as
`outcome == "failed"` with an `errors` entry naming the field, is the expected
RED reason. A few ACs (currency default, the warehouse-unresolved deviation) need
no new schema field and fail on behaviour instead.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.models.order import Customer
from app.models.procurement import Supplier
from app.models.sales_agent import SalesAgent
from app.services.scm.customer_label import normalize_debtor_code

from tests._pg_fixture import unique_code
from tests.test_ingest_documents import (
    INGEST_PO,
    INGEST_SO,
    MARKER,
    _code_of_ref,
    _po_line,
    _po_record,
    _ref,
    _so_line,
    _so_record,
    env,  # noqa: F401 - pytest fixture, imported for reuse per the tester brief
)

__all__ = ["env"]


def _plain_customer(env, company_id: str, *, code: str, name: str | None = None) -> Customer:
    """A customer row NOT registered under any integration_reference.

    Distinct from `env.link_customer`: AC-V1-2 is specifically about matching a
    row the ESB never pushed (the xlsx era, or a customer created by hand) - one
    that can only be found by code, never by ref.
    """
    row = Customer(
        customer_code=code,
        customer_name=name or f"{MARKER} plain customer",
        company_id=company_id,
    )
    env.db.add(row)
    env.db.flush()
    return row


def _plain_supplier(env, company_id: str, *, code: str, name: str | None = None) -> Supplier:
    row = Supplier(
        supplier_code=code,
        supplier_name=name or f"{MARKER} plain supplier",
        company_id=company_id,
    )
    env.db.add(row)
    env.db.flush()
    return row


def _plain_agent(*, code: str) -> SalesAgent:
    return SalesAgent(sales_agent=code)


# ================================================================== AC-V1-1
class TestCustomerRefWinsOverCode:
    def test_customer_ref_resolves_and_code_is_still_written_to_debtor_code(self, env):
        code = f"  {unique_code(MARKER)}  "
        record = _so_record(env, customer_ref=env.customer_ref, customer_code=code)

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        expected_customer_id = env.refs.resolve(
            entity_type="customers", source_ref=env.customer_ref
        )
        assert str(header["customer_id"]) == str(expected_customer_id)
        assert header["debtor_code"] == normalize_debtor_code(code)


# ================================================================== AC-V1-2
class TestCustomerCodeFallback:
    def test_customer_code_matches_an_existing_row_in_the_anchor_company(self, env):
        """Case/whitespace-insensitive: the payload spells the code differently
        from how the row stores it, and the two must still be the same debtor."""
        stored = _plain_customer(env, env.company_a, code=unique_code(MARKER))

        record = _so_record(env, customer_code=f"  {stored.customer_code.lower()}  ")
        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        assert str(header["customer_id"]) == str(stored.id)
        assert header["debtor_code"] == normalize_debtor_code(stored.customer_code)

    def test_the_same_code_in_the_other_company_is_not_matched(self, env):
        """A code that only exists in company B must not silently attach this
        company-A order to somebody else's debtor."""
        code = unique_code(MARKER)
        _plain_customer(env, env.company_b, code=code)

        record = _so_record(env, customer_code=code)
        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        assert header["customer_id"] is None
        assert header["debtor_code"] == normalize_debtor_code(code)
        assert "customer_unresolved" in entry.get("warnings", [])


# ================================================================== AC-V1-3
class TestCustomerBackCreate:
    def test_code_and_name_matching_nothing_creates_and_links_a_customer(self, env):
        code = unique_code(MARKER)
        name = f"{MARKER} Brand New Sdn Bhd"
        record = _so_record(env, customer_code=code, customer_name=name)

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "customer_created" in entry.get("warnings", [])

        header = env.header("sales_orders", record["source_ref"])
        assert header["customer_id"] is not None
        created = (
            env.db.execute(
                text("SELECT * FROM customers WHERE id = :id"),
                {"id": header["customer_id"]},
            )
            .mappings()
            .first()
        )
        assert created["customer_code"] == code
        assert created["customer_name"] == name
        assert created["customer_type"] == "company"
        assert created["is_active"] is True
        assert created["market_segment_code"] is None
        assert str(created["company_id"]) == str(env.company_a)

    def test_a_customer_ref_sent_alongside_resolves_the_new_row_next_time(self, env):
        """The created row is registered under the ref that was sent, so a
        re-push with only `customer_ref` finds it (step 1 of the ladder)."""
        code = unique_code(MARKER)
        name = f"{MARKER} Another New Co"
        new_ref = _ref("DEBTOR-NEW")
        record = _so_record(
            env, customer_ref=new_ref, customer_code=code, customer_name=name
        )

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        resolved = env.refs.resolve(entity_type="customers", source_ref=new_ref)
        assert resolved is not None
        assert str(resolved) == str(header["customer_id"])


# ================================================================== AC-V1-4
class TestCustomerCodeOnlyUnresolved:
    def test_code_only_no_name_no_match_lands_unlinked_and_warns(self, env):
        code = unique_code(MARKER)
        record = _so_record(env, customer_code=code)

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "customer_unresolved" in entry.get("warnings", [])
        header = env.header("sales_orders", record["source_ref"])
        assert header["customer_id"] is None
        assert header["debtor_code"] == normalize_debtor_code(code)
        # No row minted for a code-only miss (D2): the unique pair index is on
        # (code, name), and a code-only row would collide with a later named one.
        assert (
            env.db.execute(
                text(
                    "SELECT count(*) FROM customers WHERE company_id = :c "
                    "AND upper(btrim(customer_code)) = upper(btrim(:code))"
                ),
                {"c": env.company_a, "code": code},
            ).scalar()
            == 0
        )


# ================================================================== AC-V1-5
class TestSupplierLadder:
    def test_supplier_code_match(self, env):
        stored = _plain_supplier(env, env.company_a, code=unique_code(MARKER))
        record = _po_record(env, supplier_code=f" {stored.supplier_code.lower()} ")

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        assert str(header["supplier_id"]) == str(stored.id)

    def test_supplier_name_matches_after_stripping_the_rmb_suffix(self, env):
        stored = _plain_supplier(
            env, env.company_a, code=unique_code(MARKER), name="Xiamen Taiyang Co"
        )
        record = _po_record(env, supplier_name="Xiamen Taiyang Co (RMB)")

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        assert str(header["supplier_id"]) == str(stored.id)

    def test_neither_matches_back_creates_and_registers_the_ref(self, env):
        code = unique_code(MARKER)
        name = f"{MARKER} New Creditor Co"
        new_ref = _ref("CREDITOR-NEW")
        record = _po_record(
            env, supplier_ref=new_ref, supplier_code=code, supplier_name=name
        )

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "supplier_created" in entry.get("warnings", [])
        header = env.header("purchase_orders", record["source_ref"])
        created = (
            env.db.execute(
                text("SELECT * FROM suppliers WHERE id = :id"),
                {"id": header["supplier_id"]},
            )
            .mappings()
            .first()
        )
        assert created is not None
        assert created["supplier_code"] == code
        assert created["supplier_name"] == name
        resolved = env.refs.resolve(entity_type="suppliers", source_ref=new_ref)
        assert str(resolved) == str(header["supplier_id"])

    def test_ambiguous_cleaned_supplier_name_refuses_and_does_not_back_create(self, env):
        """D2/S1 repoint (ingest-parity-standardisation S3): the ladder's
        `_resolve_supplier_by_name` now delegates to `master_rules
        .resolve_supplier_by_name`, which refuses (rather than picking the
        most recent row) when a cleaned name matches more than one supplier
        - the same company held twice, once per currency account. The
        record still lands (a supplier is optional on a PO the way a
        warehouse is), `supplier_id` is NULL, and the warning names WHY
        rather than the document silently linking to whichever row happened
        to sort last.
        """
        cleaned = f"{MARKER} Ambiguous Creditor Co"
        _plain_supplier(env, env.company_a, code=unique_code(MARKER), name=f"{cleaned} (RMB)")
        _plain_supplier(env, env.company_a, code=unique_code(MARKER), name=f"{cleaned} (USD)")
        record = _po_record(env, supplier_name=f"{cleaned} (SGD)")

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "supplier_ambiguous" in entry.get("warnings", []), entry
        assert "supplier_created" not in entry.get("warnings", []), entry
        header = env.header("purchase_orders", record["source_ref"])
        assert header["supplier_id"] is None

    def test_back_create_slugs_the_name_when_only_a_name_is_sent(self, env):
        name = f"{MARKER} Nameless Supplier Co"
        record = _po_record(env, supplier_name=name)

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        created = (
            env.db.execute(
                text("SELECT * FROM suppliers WHERE id = :id"),
                {"id": header["supplier_id"]},
            )
            .mappings()
            .first()
        )
        assert created is not None
        assert created["supplier_code"]


# ================================================================== AC-V1-6
class TestAgentCodeLadder:
    def test_agent_code_resolves_an_existing_row(self, env):
        agent = _plain_agent(code=f"{MARKER}-{uuid.uuid4().hex[:6].upper()}")
        env.db.add(agent)
        env.db.flush()

        record = _so_record(env, agent_code=f"  {agent.sales_agent.lower()}  ")
        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        assert str(header["sales_agent_id"]) == str(agent.id)

    def test_agent_code_creates_a_shared_row_and_registers_the_ref(self, env):
        code = f"{MARKER}-{uuid.uuid4().hex[:6].upper()}"
        new_ref = _ref("AGENT-NEW")
        record = _so_record(env, sales_agent_ref=new_ref, agent_code=code)

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "agent_created" in entry.get("warnings", [])
        header = env.header("sales_orders", record["source_ref"])
        created = (
            env.db.execute(
                text("SELECT * FROM sales_agents WHERE id = :id"),
                {"id": header["sales_agent_id"]},
            )
            .mappings()
            .first()
        )
        assert created is not None
        assert created["sales_agent"] == code
        assert created["source"] == "import"
        resolved = env.refs.resolve(entity_type="sales_agents", source_ref=new_ref)
        assert str(resolved) == str(header["sales_agent_id"])


# ================================================================== AC-V1-7
class TestLineProductCodeLadder:
    def test_line_product_code_resolves_within_the_anchor_company(self, env):
        from app.models.product import Product

        product = Product(
            product_code=unique_code(MARKER),
            product_name=f"{MARKER} product by code",
            category_id=env._category.id,
            base_uom_id=env._uom.id,
            list_price=1,
            company_id=env.company_a,
        )
        env.db.add(product)
        env.db.flush()

        line = {
            "source_ref": _ref("SOL"),
            "product_code": product.product_code,
            "qty_ordered": 5,
        }
        record = _so_record(env, lines=[line])

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        lines = env.so_lines(header["id"])
        assert str(lines[0]["product_id"]) == str(product.id)

    def test_a_miss_on_a_sent_product_code_drops_the_line_not_the_record(self, env):
        """Superseded 2026-09-06 (ingest-parity-standardisation D9, AC-P2-3):
        an unresolved product code no longer fails the whole record - the
        line is dropped and the (now line-less) header still lands, matching
        `tests/test_ingest_documents.TestUnresolvedReferences` and
        `tests/test_ingest_parity_s2_documents.py
        ::TestAcP23UnknownProductDroppedUnknownLocationKept`."""
        line = {
            "source_ref": _ref("SOL"),
            "product_code": f"{MARKER}-NOT-SYNCED-YET",
            "qty_ordered": 5,
        }
        record = _so_record(env, lines=[line])

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert entry.get("lines", {}).get("dropped") == 1, entry
        header = env.header("sales_orders", record["source_ref"])
        assert header is not None
        assert env.so_lines(header["id"]) == []


# ================================================================= AC-V1-7b
class TestWarehouseUnresolvedIsAWarningNotARetry:
    def test_an_unresolved_sent_warehouse_ref_lands_null_with_a_warning(self, env):
        """v2 deviation from D10: today this is `retryable` (pinned in
        `test_ingest_documents.TestUnresolvedReferences`); v2 lets it through."""
        record = _so_record(
            env, lines=[_so_line(env, warehouse_ref=f"{MARKER}:LOC-NOT-SYNCED-YET")]
        )

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "warehouse_unresolved" in entry.get("warnings", [])
        header = env.header("sales_orders", record["source_ref"])
        lines = env.so_lines(header["id"])
        assert lines[0]["warehouse_id"] is None

    def test_an_unresolved_sent_warehouse_code_lands_null_with_a_warning(self, env):
        line = {
            "source_ref": _ref("SOL"),
            "product_ref": env.product_ref,
            "product_code": _code_of_ref(env, env.product_ref),
            "qty_ordered": 5,
            "warehouse_code": f"{MARKER}-NOT-SYNCED-YET",
        }
        record = _so_record(env, lines=[line])

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "warehouse_unresolved" in entry.get("warnings", [])
        header = env.header("sales_orders", record["source_ref"])
        lines = env.so_lines(header["id"])
        assert lines[0]["warehouse_id"] is None


# ================================================================== AC-V1-8
class TestCrossCompanyRefIsRetryable:
    """Superseded (autocount-brands-ingest AC-19b, BL-056 D15, plan D15.3): a
    ladder ref scoped to another company is simply UNKNOWN under this
    anchor's `resolve()` - the same as one that was never linked at all.
    There is no cross-company peek, so sending ONLY the ref is a plain
    unresolved reference: `retryable`, named in `errors`, nothing written.

    A test that ALSO sends a code is not this case at all - a code makes an
    unresolved ref fall through to the ordinary code/name rung (AC-V1-3/5),
    which resolves or back-creates INSIDE this anchor exactly as it would
    for any other unsynced master with that code. Those two are below,
    expecting the rung's own verdict instead of `retryable`.
    """

    def test_a_customer_ref_alone_into_another_company_is_retryable(self, env):
        foreign_customer = env.link_customer(env.company_b)
        record = _so_record(env, customer_ref=foreign_customer)

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "retryable", res.text
        assert "customer_ref" in entry["errors"]
        assert env.header("sales_orders", record["source_ref"]) is None

    def test_a_supplier_ref_alone_into_another_company_is_retryable(self, env):
        foreign_supplier = env.link_supplier(env.company_b)
        record = _po_record(env, supplier_ref=foreign_supplier)

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "retryable", res.text
        assert "supplier_ref" in entry["errors"]
        assert env.header("purchase_orders", record["source_ref"]) is None

    def test_a_customer_ref_into_another_company_plus_a_code_falls_to_the_code_rung(
        self, env
    ):
        """AC-19b + AC-V1-4: the ref is unknown here, so it falls through to
        the code rung exactly as a bare unsynced `customer_code` would - code
        only, no name, no match inside A -> `created`, unlinked,
        `customer_unresolved`. The foreign ref is never consulted again."""
        foreign_customer = env.link_customer(env.company_b)
        code = unique_code(MARKER)
        record = _so_record(env, customer_ref=foreign_customer, customer_code=code)

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "customer_unresolved" in entry.get("warnings", [])
        header = env.header("sales_orders", record["source_ref"])
        assert header is not None
        assert header["customer_id"] is None
        assert header["debtor_code"] == normalize_debtor_code(code)

    def test_a_supplier_ref_into_another_company_plus_a_code_back_creates(self, env):
        """AC-19b + AC-V1-5: the ref is unknown here, so it falls through to
        the code rung exactly as a bare unsynced `supplier_code` would - code
        only, no match inside A -> back-created, `supplier_created`. The
        foreign ref is never consulted again."""
        foreign_supplier = env.link_supplier(env.company_b)
        code = unique_code(MARKER)
        record = _po_record(env, supplier_ref=foreign_supplier, supplier_code=code)

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "supplier_created" in entry.get("warnings", [])
        header = env.header("purchase_orders", record["source_ref"])
        assert header is not None
        assert header["supplier_id"] is not None
        created = env.db.execute(
            text("SELECT supplier_code FROM suppliers WHERE id = :id"),
            {"id": header["supplier_id"]},
        ).mappings().first()
        assert created["supplier_code"] == code


# ================================================================== AC-V1-9
class TestPurchaseOrderLineCurrencyFollowsHeader:
    """AC-PLC-1..3: a line with no stated currency takes the HEADER's resolved
    currency, never a hardcoded CNY. Header-level CNY fill (when the header
    itself states none) is unchanged and untested here (AC-V1-9's old name);
    it merely becomes the value a line inherits."""

    def test_header_stated_myr_lines_unstated_take_the_header_currency(self, env):
        record = _po_record(env, currency="MYR")  # no currency on the line
        assert "currency" not in record["lines"][0]

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        assert header["currency"] == "MYR"
        lines = env.po_lines(header["id"])
        assert lines[0]["currency"] == "MYR"

    def test_header_stated_usd_one_line_states_cny_the_rest_follow_header(self, env):
        follows = _po_line(env)
        stated = _po_line(env, product_ref=env.product2_ref, currency="CNY")
        record = _po_record(env, lines=[follows, stated], currency="USD")

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        assert header["currency"] == "USD"
        lines = {line["source_ref"]: line for line in env.po_lines(header["id"])}
        assert lines[follows["source_ref"]]["currency"] == "USD"
        assert lines[stated["source_ref"]]["currency"] == "CNY"

    def test_nothing_stated_anywhere_the_line_gets_the_headers_cny_fill(self, env):
        record = _po_record(env)  # no currency anywhere
        assert "currency" not in record

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        assert header["currency"] == "CNY"
        lines = env.po_lines(header["id"])
        assert lines[0]["currency"] == "CNY"

    def test_a_stated_currency_is_stored_as_sent(self, env):
        line = _po_line(env, currency="MYR")
        record = _po_record(env, lines=[line], currency="USD")

        res = env.post(INGEST_PO, [record])

        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        assert header["currency"] == "USD"
        lines = env.po_lines(header["id"])
        assert lines[0]["currency"] == "MYR"


# ================================================================= AC-V1-10
class TestDryRunBackCreate:
    def test_dry_run_creates_no_supplier_but_reports_what_would_happen(self, env):
        code = unique_code(MARKER)
        name = f"{MARKER} Dry Run Co"
        record = _po_record(env, supplier_code=code, supplier_name=name)

        res = env.post(INGEST_PO, [record], dry_run=True)

        body = res.json()
        assert body["dry_run"] is True
        entry = body["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "supplier_created" in entry.get("warnings", [])
        assert (
            env.db.execute(
                text(
                    "SELECT count(*) FROM suppliers WHERE company_id = :c "
                    "AND upper(btrim(supplier_code)) = upper(btrim(:code))"
                ),
                {"c": env.company_a, "code": code},
            ).scalar()
            == 0
        )
        assert env.header("purchase_orders", record["source_ref"]) is None

    def test_dry_run_creates_no_customer_but_reports_what_would_happen(self, env):
        code = unique_code(MARKER)
        name = f"{MARKER} Dry Run Customer"
        record = _so_record(env, customer_code=code, customer_name=name)

        res = env.post(INGEST_SO, [record], dry_run=True)

        body = res.json()
        assert body["dry_run"] is True
        entry = body["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "customer_created" in entry.get("warnings", [])
        assert (
            env.db.execute(
                text(
                    "SELECT count(*) FROM customers WHERE company_id = :c "
                    "AND upper(btrim(customer_code)) = upper(btrim(:code))"
                ),
                {"c": env.company_a, "code": code},
            ).scalar()
            == 0
        )


# ============================================================== schema pin
class TestSchemaDropsUnknownKeys:
    def test_a_v1_payload_with_no_new_keys_still_validates(self, env):
        """Byte-for-byte parity: nothing added here should make a plain v1
        record start failing (AC-V0-2 lives in its own file; this is the same
        guarantee from the resolution side)."""
        record = _so_record(env, customer_ref=env.customer_ref)

        res = env.post(INGEST_SO, [record])

        assert res.json()["records"][0]["outcome"] == "created", res.text

    def test_an_unknown_key_is_dropped_not_refused(self, env):
        # Owner decision, 1 Oct 2026: dropped (its name logged once per request by the
        # ingest route), never refused.
        record = _so_record(env, customer_nick="Bob")

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
