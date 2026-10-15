"""ACCESS-MODEL S7: the leak matrix (AC-AM-12, 13, 14).

For each of the five seeded roles x every domain and every field/report/reveal key in the
registry, a contact holding ONLY that role must show the same answer in three places:
  (a) the tagged prompt rendered for it (`render_for_access`),
  (b) `check_access(...)["attributes"]` (field/report gates),
  (c) `load_profile(...).grants` (domain gate).
A tag is present in the prompt iff it is granted. The expectation is computed here from the
card section 2 ticks (constants imported from the migration test), not from the code under test.

One loop per role; every mismatching cell is reported with its role, tag and expectation.

Red-first: access_tree / access_seed / prompt_access do not exist yet.
"""
from __future__ import annotations

import pytest

from tests.chatbot._access_seed import SPACE_ID, give_role, make_contact, make_workspace
from tests.chatbot.test_chatbot_access_migration import (
    DEALER_DOMAINS,
    INCOMING_FIELDS,
    K_COST,
    K_LOW,
    K_OUTSTANDING,
    K_PLACED,
    K_SALES,
    K_SELLABLE,
    K_STAMP_ATTACHMENT,
    K_STAMP_INCOMING,
    K_SUPPLIER,
    PURCHASING_DOMAINS,
    SALES_OFFICE_DOMAINS,
    SUPPORTED_DOMAINS,
    WAREHOUSE_DOMAINS,
    _registry,
    _role_fields,
    _role,
)

ROLE_DOMAINS = {
    "dealer": DEALER_DOMAINS,
    "sales_office": SALES_OFFICE_DOMAINS,
    "purchasing": PURCHASING_DOMAINS,
    "warehouse": WAREHOUSE_DOMAINS,
    "management": SUPPORTED_DOMAINS,
}
ALL_DOMAINS = sorted(SUPPORTED_DOMAINS | {"goods_receive"})
#: field/report key -> owning domain, as `_registry` seeds it
FIELD_OWNER = {
    K_SELLABLE: "inventory",
    K_PLACED: "inventory",
    K_LOW: "inventory",
    K_OUTSTANDING: "order",
    K_SUPPLIER: "purchase_order",
    "purchase_orders.po_number": "purchase_order",
    K_STAMP_INCOMING: "incoming",
    K_STAMP_ATTACHMENT: "product_attachment",
    **{f"incoming_stock.{f}": "incoming" for f in INCOMING_FIELDS},
}
#: reveal keys that ride in with a granted domain
DOMAIN_REVEAL = {"purchase_cost": K_COST, "sales": K_SALES, "purchase_order": K_PLACED}
ALL_KEYS = sorted(set(FIELD_OWNER) | set(DOMAIN_REVEAL.values()))
ALL_TAGS = frozenset(ALL_DOMAINS) | frozenset(ALL_KEYS)

TAGGED_TEXT = "Intro, always.\n" + "".join(
    f"{{{{#only {tag}}}}}\nTITLE::{tag}\nbody of {tag}\n{{{{/only}}}}\n" for tag in sorted(ALL_TAGS)
) + "Tail, always.\n"


def _expected(role_code: str, db) -> tuple[set[str], set[str]]:
    domains = set(ROLE_DOMAINS[role_code])
    ticked = _role_fields(db, role_code)
    attributes = {k for k in ticked if FIELD_OWNER.get(k) in domains}
    attributes |= {key for dom, key in DOMAIN_REVEAL.items() if dom in domains}
    return domains, attributes


@pytest.mark.parametrize("role_code", sorted(ROLE_DOMAINS))
def test_prompt_attributes_and_grants_agree_for_every_cell(session_factory, role_code):
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.access_seed import seed_default_roles
    from app.services.chatbot.access_tree import effective_access
    from app.services.chatbot.head.access import check_access
    from app.services.chatbot.prompt_access import render_for_access

    db = session_factory()
    wid = make_workspace(db)
    _registry(db)
    seed_default_roles(db)
    db.commit()
    pk, rio = make_contact(db, workspace_id=wid)
    give_role(db, pk, _role(db, role_code).id)

    want_domains, want_attributes = _expected(role_code, db)
    access = effective_access(db, contact_respond_id=rio, space_id=SPACE_ID)
    rendered = render_for_access(TAGGED_TEXT, access, known_tags=ALL_TAGS)
    present = {line[len("TITLE::"):] for line in rendered.splitlines() if line.startswith("TITLE::")}
    got_attributes = set(check_access(db, agent_code="general_enquiries", contact_id=rio, space_id=SPACE_ID)["attributes"])
    profile, _ = turn_runtime.load_profile(db, rio, space_id=SPACE_ID)
    got_grants = set(profile.grants or [])

    misses: list[str] = []
    for tag in ALL_DOMAINS:
        want = tag in want_domains
        if (tag in present) != want:
            misses.append(f"[{role_code}] prompt: domain {tag!r} expected {'present' if want else 'absent'}")
        if (tag in got_grants) != want:
            misses.append(f"[{role_code}] Profile.grants: domain {tag!r} expected {'in' if want else 'out'}")
    for tag in ALL_KEYS:
        want = tag in want_attributes
        if (tag in present) != want:
            misses.append(f"[{role_code}] prompt: key {tag!r} expected {'present' if want else 'absent'}")
        if (tag in got_attributes) != want:
            misses.append(f"[{role_code}] attributes: key {tag!r} expected {'in' if want else 'out'}")
    assert not misses, "\n".join(misses)
    assert "Intro, always." in rendered and "Tail, always." in rendered
    assert "{{" not in rendered
