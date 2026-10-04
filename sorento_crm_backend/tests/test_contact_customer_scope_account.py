"""Phase 2 RED tests - the linked-contact scope carries Account levels (ACCOUNT-LEDGER, AC-6, AC-7).

`app/services/contact_customer_scope.py` (plan Design 5), contract pinned here:

* `ContactCustomerScope.levels: dict[str, int | None]` (customer_id -> level), default empty,
  filled by `contact_customer_scope(db, contact_id)` from `customers.account_level`. `linked`
  keeps its (id, name, code) shape.
* `match_words(words, accounts=None)`: `accounts` is parallel to `words` (int or None). A name
  match as today, then for a word whose account is not None only hits whose level equals it
  survive; a word left with no hit returns None (the caller refuses). A link whose level is
  unknown (not in `levels`) is not at ANY level.
* `refusal_line_for(scope, ids)`: the refusal line naming ONLY those linked ledgers, in link
  order, in the same words as `refusal_line`.

Pure tests build the dataclass directly; the DB tests seed a contact, customers and links.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services import contact_customer_scope as scope_mod
from app.services.contact_customer_scope import ContactCustomerScope, contact_customer_scope, refusal_line
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.models.base import set_company_scope

from tests._mc_lookup_seed import customer as seed_customer
from tests._pg_fixture import blank_session
from tests.test_top_selling_report import _contact, _link

# (id, name, level) in link order. Names are the real shapes of the HANLIM / SOON HENG books.
ROWS = [
    ("h-bare", "HANLIM TRADING SDN BHD", None),
    ("h-cer", "HANLIM TRADING SDN BHD (CERAMIC & ELLECI)", None),
    ("h-1", "HANLIM TRADING SDN BHD [A/C I]", 1),
    ("h-2", "HANLIM TRADING SDN BHD [A/C II]", 2),
    ("h-3", "HANLIM TRADING SDN BHD [A/C III]", 3),
    ("h-4", "HANLIM TRADING SDN BHD [A/C IV]", 4),
    ("s-1", "SOON HENG HARDWARE CO.SDN.BHD. [A/C I]", 1),
]


def _scope(rows=ROWS, *, staff: bool = False) -> ContactCustomerScope:
    return ContactCustomerScope(
        linked=tuple((i, n, f"C-{i}") for i, n, _l in rows),
        staff=staff,
        levels={i: lvl for i, _n, lvl in rows},
    )


# ------------------------------------------------------------------ levels field


def test_levels_defaults_to_empty_and_linked_shape_is_unchanged() -> None:
    scope = ContactCustomerScope(linked=(("a", "A", "CA"),), staff=False)
    assert scope.levels == {}
    assert scope.linked == (("a", "A", "CA"),)


# ------------------------------------------------------------------ match_words with accounts


def test_account_two_keeps_only_the_level_two_link() -> None:
    assert _scope().match_words(["hanlim"], [2]) == ["h-2"]


def test_account_one_with_a_word_keeps_that_familys_level_one() -> None:
    assert _scope().match_words(["soon heng"], [1]) == ["s-1"]


def test_account_none_for_a_word_is_todays_name_match() -> None:
    scope = _scope()
    assert scope.match_words(["hanlim"], [None]) == ["h-bare", "h-cer", "h-1", "h-2", "h-3", "h-4"]
    assert scope.match_words(["hanlim"]) == ["h-bare", "h-cer", "h-1", "h-2", "h-3", "h-4"]
    assert scope.match_words(["hanlim"], None) == ["h-bare", "h-cer", "h-1", "h-2", "h-3", "h-4"]


def test_a_word_left_with_no_hit_refuses() -> None:
    assert _scope().match_words(["soon heng"], [2]) is None


def test_a_word_naming_no_link_still_refuses_whatever_the_account() -> None:
    assert _scope().match_words(["nobody"], [1]) is None


def test_accounts_are_parallel_to_words_and_results_keep_link_order() -> None:
    assert _scope().match_words(["soon heng", "hanlim"], [1, 2]) == ["h-2", "s-1"]


def test_one_word_with_an_account_and_one_without() -> None:
    assert _scope().match_words(["hanlim", "soon heng"], [3, None]) == ["h-3", "s-1"]


def test_one_refused_word_refuses_the_whole_set() -> None:
    assert _scope().match_words(["hanlim", "soon heng"], [2, 2]) is None


def test_a_link_with_an_unknown_level_is_at_no_level() -> None:
    scope = ContactCustomerScope(linked=(("a", "ZZT ALPHA CO", "CA"),), staff=False)
    assert scope.match_words(["alpha"]) == ["a"]
    assert scope.match_words(["alpha"], [1]) is None


def test_exact_code_match_is_narrowed_by_account_too() -> None:
    scope = _scope()
    assert scope.match_words(["C-h-2"], [2]) == ["h-2"]
    assert scope.match_words(["C-h-2"], [1]) is None


# ------------------------------------------------------------------ refusal_line_for


def test_refusal_line_for_names_only_the_given_ledgers_groups_in_link_order() -> None:
    """Owner rule (2 Oct 2026, DO-ASK-SIMPLIFY): a customer company is named by its group
    name only, so the given ledgers print as their groups, each once."""
    scope = _scope()
    assert (
        scope_mod.refusal_line_for(scope, ["s-1"])
        == "Sorry, that isn't under your account. I can only check on SOON HENG HARDWARE CO.SDN.BHD."
    )
    assert scope_mod.refusal_line_for(scope, ["h-4", "h-2"]) == (
        "Sorry, that isn't under your account. I can only check on HANLIM TRADING SDN BHD."
    )


def test_refusal_line_for_two_groups_uses_and() -> None:
    line = scope_mod.refusal_line_for(_scope(), ["h-1", "h-2", "s-1"])
    assert line == (
        "Sorry, that isn't under your account. I can only check on "
        "HANLIM TRADING SDN BHD and SOON HENG HARDWARE CO.SDN.BHD."
    )


def test_refusal_line_for_all_ids_equals_refusal_line() -> None:
    scope = _scope()
    assert scope_mod.refusal_line_for(scope, scope.customer_ids) == refusal_line(scope)


# ------------------------------------------------------------------ contact_customer_scope fills levels


def _set_level(db, cid: str, level) -> None:
    db.execute(text("UPDATE customers SET account_level = :n WHERE id = :i"), {"n": level, "i": cid})


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


def test_contact_customer_scope_fills_levels_from_the_setting(db) -> None:
    contact = _contact(db)
    a = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT LEDGER ONE [A/C I]")
    b = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT LEDGER BARE")
    c = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT LEDGER TWO [A/C I]")
    for cust in (a, b, c):
        _link(db, contact, cust)
    # The setting is the truth, never the name: `c` is named A/C I but set to level 2.
    _set_level(db, a.id, 1)
    _set_level(db, c.id, 2)
    db.expire_all()

    scope = contact_customer_scope(db, contact.id)

    assert scope.levels == {str(a.id): 1, str(b.id): None, str(c.id): 2}
    assert [row[0] for row in scope.linked] == [str(a.id), str(b.id), str(c.id)]
    assert all(len(row) == 3 for row in scope.linked)
    assert scope.match_words(["ledger"], [2]) == [str(c.id)]
