"""Phase 2 RED tests - AC-19: the card's real examples 1, 2 and 3 through the turn runtime.

`CARD-contact-brand-scope-4oct.md` "Real examples". A contact scoped to MOCHA asks for stock:

1. a MOCHA code            -> answered as today
2. a SORENTO code          -> the reply of a code that does not exist
3. an unbranded code (Q1)  -> the same reply as 2

The real resolve seam runs in process on the engine's own scoped session (the
`test_engine_company_scope.py` wiring); the fetch is a canned success, so a product that
RESOLVES gets "Yes, we have that in stock." and one that does not gets the not-found reply.
Example 4 (top selling for a CABANA + SORENTO contact) is covered per path in
`tests/test_brand_scope_paths.py`.
"""
from __future__ import annotations

import re
from typing import Any

import pytest

from app.models.access import RespondContact
from app.models.company import RespondContactCompany
from app.services.chatbot import engine as engine_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests._brand_scope_seed import brand, branded_product
from tests.chatbot.test_engine import _parser_output, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_engine_company_scope import (
    _entity_for,
    _scope_envelope,
    _seed_workspace,
    _spy_resolve_kinds,
    _wire_business_query_turn,
)


class Seed:
    def __init__(self, session_factory, *, scoped: bool = True) -> None:
        db = session_factory()
        self.mocha = brand(db, "MOCHA")
        self.sorento = brand(db, "SORENTO")
        self.p_mocha = branded_product(db, unique_code("MCH", alpha=True).replace("-", ""), self.mocha)
        self.p_sorento = branded_product(db, unique_code("SRT", alpha=True).replace("-", ""), self.sorento)
        self.p_null = branded_product(db, unique_code("NUL", alpha=True).replace("-", ""), None)
        db.commit()
        workspace_id = _seed_workspace(session_factory)
        self.contact_id = unique_code("rid")
        db = session_factory()
        kwargs = {"brand_ids": [self.mocha.id]} if scoped else {}
        row = RespondContact(
            respond_io_id=self.contact_id, phone_number=f"+6{unique_code('PH')[:10]}",
            workspace_id=workspace_id, session_vars={"variables": {}}, **kwargs,
        )
        db.add(row)
        db.flush()
        db.add(RespondContactCompany(respond_contact_id=row.id, company_id=DEFAULT_COMPANY_ID))
        db.commit()


def _ask(session_factory, system_settings_row, monkeypatch, stub_parser, stub_access, seed, code: str):
    _wire_business_query_turn(session_factory, system_settings_row, monkeypatch)
    stub_parser(_parser_output(domain_hint="inventory", entities=[_entity_for(code)]))
    stub_access()
    calls = _spy_resolve_kinds(monkeypatch)
    result = engine_mod.run_turn(
        _scope_envelope(seed.contact_id, message_id=unique_code("msg"), text=f"stock {code}"),
        session_factory=session_factory,
    )
    assert result.status == "done", result.error
    matched = {m.get("uuid") for m in (calls[-1] if calls else [])}
    return result.reply["text"], matched


def test_example_1_in_scope_code_is_answered_as_today(
    session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
) -> None:
    seed = Seed(session_factory)
    text, matched = _ask(session_factory, system_settings_row, monkeypatch, stub_parser, stub_access,
                         seed, seed.p_mocha.product_code)
    assert seed.p_mocha.id in matched
    assert not text.startswith("Couldn't find"), text


@pytest.mark.parametrize("which", ["p_sorento", "p_null"], ids=["example_2_other_brand", "example_3_no_brand"])
def test_example_2_and_3_reply_equals_the_reply_for_a_nonexistent_code(
    session_factory, stub_parser, stub_access, system_settings_row, monkeypatch, which
) -> None:
    seed = Seed(session_factory)
    product = getattr(seed, which)
    code, ghost = product.product_code, "ZZTGHOST" + unique_code("g", alpha=True).replace("-", "")[-8:]
    text, matched = _ask(session_factory, system_settings_row, monkeypatch, stub_parser, stub_access, seed, code)
    miss, _ = _ask(session_factory, system_settings_row, monkeypatch, stub_parser, stub_access, seed, ghost)
    assert product.id not in matched, "the resolver matched an out-of-scope product"
    assert "Couldn't find" in text, text
    assert re.sub(re.escape(code), "<X>", text, flags=re.I) == re.sub(re.escape(ghost), "<X>", miss, flags=re.I)


def test_unscoped_contact_still_resolves_every_product(
    session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
) -> None:
    """AC-6: NULL brand_ids -> the SORENTO and unbranded codes resolve exactly as today."""
    seed = Seed(session_factory, scoped=False)
    for product in (seed.p_sorento, seed.p_null):
        text, matched = _ask(session_factory, system_settings_row, monkeypatch, stub_parser, stub_access,
                             seed, product.product_code)
        assert product.id in matched, product.product_code
        assert not text.startswith("Couldn't find"), text
