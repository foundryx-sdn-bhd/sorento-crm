"""Which RESTRICTED chatbot fields a contact may be told, and the keys that exist.

One mechanism for two owner requirements (chatbot growth r1, Slice C): sellable
stock defaults off for every contact (D3), and a PO's supplier must never reach a
dealer by default (D4). A presenter marks a field `restricted=<key>` in
`field_vocabulary`; `contact_field_reveals` is the grant, keyed directly on the
contact - unlike `agent_field_access`, there is no owning agent to route a PO
supplier's reveal through.

Default is HIDDEN, except the DO keys (`DEFAULT_ON_KEYS`), which default ON for every
contact (owner, 4 Oct 2026): a contact with no row for any other key never sees that field,
and a `granted=False` row hides a DO key. A full
list PUT does not delete non-listed rows - it flips them to `granted=False` - so
the table keeps who granted or revoked a key and when, rather than losing that
history on the next save.

WHICH KEYS EXIST is a frozen literal here, not a read of `mcp_tools.restricted_fields`
at request time. `mcp_tool_registry_service.sync_catalog` still writes that column at
startup by importing `sorento_crm_mcp.catalog` - but the deployed backend image is built
from `context: ./sorento_crm_backend` only (see `sorento_crm/docker-compose.yml` and
`.github/workflows/deploy.yml`), `sorento_crm_mcp` is not in `requirements.txt`, and no
volume mounts it in, so that import raises `ModuleNotFoundError` in every container and
the startup sync never runs - the column stays at its migration-488 default of `[]`
forever on prod, and this checklist read "No restricted field exists yet" even though the
catalogue declares three. A local checkout works because the dev venv happens to have the
MCP package installed, which is exactly what hid the bug. The precedent is
`app/services/chatbot/lanes/business/fetch.py::CHATBOT_READ_ONLY_TOOLS`: a frozen set the
container CAN read, kept honest by a CI test (`tests/chatbot/test_field_reveal_keys_pinned_to_catalog.py`)
that imports the catalogue - which CI and a checkout both can - and asserts the two agree.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.access import ContactFieldReveal

#: Every restricted key the catalogue declares, sorted by key, source of truth for the
#: Contacts > Access > Field reveals checklist. Mirrors `ToolSpec.restricted_fields` on
#: `crm_inventory_stock_balance_list` and `crm_procurement_po_placed_list` in
#: `sorento_crm_mcp/sorento_crm_mcp/catalog.py`. A test pins this literal to the catalogue
#: (see the module docstring above), so a new `restricted=` field on a presenter fails
#: here until this tuple is updated to match, rather than silently missing the checklist.
FIELD_REVEAL_KEYS: tuple[tuple[str, str], ...] = (
    # DO-ASK-SIMPLIFY rule 2 (owner, 2 Oct 2026; every printed DO field since the 3 Oct hand
    # test): one switch per DO field, ON by default for every contact, new ones
    # included (owner, 4 Oct 2026); the owner turns one off per contact. Default ON
    # comes from `DEFAULT_ON_KEYS`, with no seeding migration (crew review, 4 Oct 2026: a seed
    # would reset owner-OFF switches).
    ("delivery_orders.driver", "DO driver"),
    ("delivery_orders.lorry_plate", "DO lorry plate"),
    ("delivery_orders.pickup_time", "DO pickup time"),
    ("delivery_orders.status", "DO status"),
    ("delivery_orders.transporter", "DO transporter"),
    ("delivery_orders.order_number", "DO order number"),
    ("delivery_orders.customer", "DO customer"),
    ("delivery_orders.order_date", "DO order date"),
    ("delivery_orders.delivery_date", "DO delivery date"),
    ("delivery_orders.warehouse", "DO warehouse"),
    ("delivery_orders.products", "DO products"),
    ("inventory.sellable", "Outstanding SO on stock answers"),
    ("purchase_orders.placed", "PO placed (on order) on stock answers"),
    ("purchase_orders.supplier", "PO supplier"),
    ("purchase_orders.cost", "Last purchase cost"),
    ("sales_orders.outstanding", "Sales order outstanding"),
    # PLAN-chatbot-sales-report.md S10 (AC-1641). Like `sales_orders.outstanding`
    # above, this gates a whole TOOL rather than one field - the lane refuses the
    # ask before any fetch (`lanes/business/__init__.py::run_fetch`), with no
    # fallback scope to redirect to (unlike D13's SO/DO redirect).
    ("sales_orders.sales_report", "Sales report"),
    # PLAN-low-stock-report S6 (AC-63). Unlike its neighbours this key does not hide a
    # FIELD - it gates a whole tool, because that tool's fetch creates a reorder run and
    # sends a workbook. The lane refuses before any fetch (AC-64) and the route refuses
    # again in-route (AC-41). Owner ruling 1 (Phase 3): it is a STAFF-ONLY SUPERSET grant,
    # NOT a per-field toggle - the workbook is not column-gated per reveal key (only
    # Supplier follows `purchase_orders.supplier`). The label spells out what it hands over
    # (security S1) so an admin granting it knows it exposes the whole book incl. Dealer
    # o/s, PO and SPO numbers, and does not read it as another narrow field reveal.
    ("scm.low_stock_report", "Low stock report over chat (staff: full workbook incl. Dealer o/s, PO and SPO numbers)"),
)

#: The DO reveal keys, on for every contact until a `granted=False` row says otherwise
#: (owner, 4 Oct 2026). Every other key stays hidden by default. Any future
#: `delivery_orders.*` reveal key is ON by default through this prefix rule, so adding one
#: is an owner decision.
DEFAULT_ON_KEYS: frozenset[str] = frozenset(
    key for key, _label in FIELD_REVEAL_KEYS if key.startswith("delivery_orders.")
)


def field_reveal_keys() -> list[dict[str, str]]:
    """Every restricted key that exists, with its label, sorted by key.

    Sourced from the `FIELD_REVEAL_KEYS` literal above, not from `mcp_tools` - see the
    module docstring for why a live query cannot be used here.
    """
    return [{"key": key, "label": label} for key, label in sorted(FIELD_REVEAL_KEYS)]


def granted_keys_for(db: Session, respond_contact_ids: list[str]) -> dict[str, list[str]]:
    """`granted_keys` for many contacts in one query: the ONE definition of the effective set.

    Keyed by the ids as given (every id present, `[]` when nothing is held).
    """
    ids = [str(i) for i in respond_contact_ids]
    held: dict[str, set[str]] = {i: set() for i in ids}
    have_row: dict[str, set[str]] = {i: set() for i in ids}
    if ids:
        for contact_id, key, granted in (
            db.query(
                ContactFieldReveal.respond_contact_id,
                ContactFieldReveal.field_key,
                ContactFieldReveal.granted,
            )
            .filter(ContactFieldReveal.respond_contact_id.in_(ids))
            .all()
        ):
            have_row[str(contact_id)].add(key)
            if granted:
                held[str(contact_id)].add(key)
    return {i: sorted(held[i] | (DEFAULT_ON_KEYS - have_row[i])) for i in ids}


def granted_keys(db: Session, respond_contact_id: str) -> list[str]:
    """The keys this contact currently holds: every granted row, plus each default-on key
    with no row at all (a `granted=False` row hides it)."""
    return granted_keys_for(db, [respond_contact_id])[str(respond_contact_id)]


def set_granted_keys(
    db: Session,
    respond_contact_id: str,
    keys: list[str],
    *,
    actor_id: str | None,
    commit: bool = True,
) -> list[str]:
    """Full-list replace over the keys this build knows: exactly `keys` end up granted,
    every other KNOWN key revoked. A row for a key outside `FIELD_REVEAL_KEYS` (seeded by
    a lane this build predates) is never touched, so a save here cannot revoke it.

    Upserts rather than delete-then-insert, so a key toggled off and back on
    keeps its original `created_at` / `created_by` rather than looking newly
    granted.
    """
    wanted = set(keys)
    existing = {
        row.field_key: row
        for row in db.query(ContactFieldReveal)
        .filter(ContactFieldReveal.respond_contact_id == respond_contact_id)
        .all()
    }

    for key in wanted:
        row = existing.get(key)
        if row is None:
            db.add(
                ContactFieldReveal(
                    respond_contact_id=respond_contact_id,
                    field_key=key,
                    granted=True,
                    created_by=actor_id,
                )
            )
        elif not row.granted:
            # `updated_at`'s own `onupdate=func.now()` fires from this write; an
            # explicit UTC value here landed 9 hours BEFORE `created_at` on a
            # server in a positive-UTC-offset timezone (both columns are naive,
            # so one is `now()` in local time and the other was UTC).
            row.granted = True

    known = {key for key, _label in FIELD_REVEAL_KEYS}
    for key in DEFAULT_ON_KEYS - wanted - existing.keys():
        # No row means ON for these, so turning one off needs an explicit row to stick.
        db.add(
            ContactFieldReveal(
                respond_contact_id=respond_contact_id,
                field_key=key,
                granted=False,
                created_by=actor_id,
            )
        )
    for key, row in existing.items():
        if key in known and key not in wanted and row.granted:
            row.granted = False

    # `commit=False`: the caller owns the transaction (bulk copy access writes each target
    # inside its own savepoint, which a commit here would close).
    if commit:
        db.commit()
    else:
        db.flush()
    return granted_keys(db, respond_contact_id)
