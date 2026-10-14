"""PV2 redesign R3a red tests (UAC R3a-2, R3a-3, R3a-5, R3a-6, R3a-7, R3a-8): the entity-kind and domain registry columns.

Behaviour-preserving. The tables the code held (clarify_copy words, apply's domain sets, register's field map, the
resolve dispatch) move into `chatbot_entity_kinds` / `chatbot_domains` columns read through `Policy`. These tests pin
DATA (the values in `_r3a_expect`, hard-coded from HEAD), not helper names; the one exception is the deletions guard.
R3a-4 (behaviour unchanged) has no new test: the replay sets (test_core_replay, test_carry_converted,
test_livecheck_replay) and every R1/R2/R-GAPS test are the proof. No model call, no DB except the stale-schema read.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2 import apply as apply_mod
from app.services.chatbot.pv2 import bridge, clarify_copy, register as register_mod
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.turn import policy_rows
from app.services.chatbot.turn.policy import Policy, default_policy
from tests.chatbot.pv2 import _r3a_expect as X
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, out  # noqa: F401

_MISSING = object()


def _field(row: Any, name: str) -> Any:
    assert row is not None, "no such row in the policy"
    assert hasattr(row, name), f"{type(row).__name__} has no field {name!r} (R3a-2: the registry column is not read)"
    return getattr(row, name)


def _domain_rows() -> list[dict[str, Any]]:
    return [
        {**row, "takes_date_filter": any(t in policy_rows.DATE_PARAM_TOOLS for t in row["tools"])}
        for row in policy_rows.DEFAULT_DOMAIN_ROWS
    ]


def _policy(kind_edits: dict[str, dict] | None = None, domain_edits: dict[str, dict] | None = None) -> Policy:
    """The seed rows with ONE value changed: what a staff edit of a registry row would give."""
    kinds = [{**r, **(kind_edits or {}).get(r["kind"], {})} for r in policy_rows.DEFAULT_KIND_ROWS]
    domains = [{**r, **(domain_edits or {}).get(r["name"], {})} for r in _domain_rows()]
    return Policy.from_rows(domains=domains, kinds=kinds)


# --------------------------------------------------------------------------- R3a-2: parity (policy_rows seed)


@pytest.mark.parametrize("kind", X.KINDS)
def test_r3a2_the_seed_policy_gives_every_kind_the_value_the_retired_tables_held(kind):
    row = default_policy().kind(kind)
    for name, want in X.expected_kind(kind).items():
        assert X.norm(name, _field(row, name)) == want, f"kind {kind} field {name}"


@pytest.mark.parametrize("name", X.DOMAINS)
def test_r3a2_the_seed_policy_gives_every_domain_the_value_the_retired_tables_held(name):
    row = default_policy().domain(name)
    for field, want in X.expected_domain(name).items():
        assert X.norm(field, _field(row, field)) == want, f"domain {name} field {field}"


def _non_empty(expected: dict[str, Any]) -> bool:
    return any(v not in ({}, (), frozenset(), None, False) for v in expected.values())


@pytest.mark.parametrize("kind", [k for k in X.KINDS if _non_empty(X.expected_kind(k))])
def test_r3a2_policy_rows_itself_carries_every_non_empty_kind_value(kind):
    """Blank schemas fall back to policy_rows (load_policy), so the seed rows hold the values, not just the Policy."""
    row = next(r for r in policy_rows.DEFAULT_KIND_ROWS if r["kind"] == kind)
    for name, want in X.expected_kind(kind).items():
        if want in ({}, (), frozenset(), None, False):
            continue
        assert name in row, f"policy_rows kind {kind} lacks {name}"
        assert X.norm(name, row[name]) == want, f"policy_rows kind {kind} field {name}"


@pytest.mark.parametrize("name", [d for d in X.DOMAINS if _non_empty(X.expected_domain(d))])
def test_r3a2_policy_rows_itself_carries_every_non_empty_domain_value(name):
    row = next(r for r in policy_rows.DEFAULT_DOMAIN_ROWS if r["name"] == name)
    for field, want in X.expected_domain(name).items():
        if want in ({}, (), frozenset(), None, False):
            continue
        assert field in row, f"policy_rows domain {name} lacks {field}"
        assert X.norm(field, row[field]) == want, f"policy_rows domain {name} field {field}"


# --------------------------------------------------------------------------- R3a-5: a stale schema reads as the seed


def _stale_kinds() -> list[dict[str, Any]]:
    return [{k: r[k] for k in X.OLD_KIND_KEYS} for r in policy_rows.DEFAULT_KIND_ROWS]


def _stale_domains() -> list[dict[str, Any]]:
    return [{k: r[k] for k in X.OLD_DOMAIN_KEYS if k in r} for r in _domain_rows()]


def _assert_seed_values(policy: Policy) -> None:
    for kind in X.KINDS:
        row = policy.kind(kind)
        for name, want in X.expected_kind(kind).items():
            assert X.norm(name, _field(row, name)) == want, f"kind {kind} field {name}"
    for name in X.DOMAINS:
        row = policy.domain(name)
        for field, want in X.expected_domain(name).items():
            assert X.norm(field, _field(row, field)) == want, f"domain {name} field {field}"


def test_r3a5_rows_without_the_new_keys_give_the_seed_values():
    stale = Policy.from_rows(domains=_stale_domains(), kinds=_stale_kinds())
    _assert_seed_values(stale)


def test_r3a5_a_row_with_some_of_the_new_keys_takes_the_seed_for_the_absent_ones():
    kinds = _stale_kinds()
    for r in kinds:
        if r["kind"] == "customer":
            r["labels"] = {"en": "client", "zh": "客人", "ms": "pelanggan"}  # present; the rest absent
    policy = Policy.from_rows(domains=_stale_domains(), kinds=kinds)
    assert X.norm("labels", _field(policy.kind("customer"), "labels"))["en"] == "client"
    assert X.norm("plural_labels", _field(policy.kind("customer"), "plural_labels")) == X.PLURAL_LABELS["customer"]
    assert X.norm("pv2_resolver", _field(policy.kind("customer"), "pv2_resolver")) == "customer"
    assert X.norm("default_domain", _field(policy.kind("delivery_order"), "default_domain")) == "order"


def test_r3a5_a_stale_database_read_through_load_policy_gives_the_seed_values():
    """The shared local DB converges by create_all, but a stale one (this lane's private test DB) lacks the columns:
    `SELECT *` returns no new keys and the live loader must still answer as the seed."""
    import json

    from sqlalchemy import text

    from app.services.chatbot.turn.policy import load_policy
    from tests._pg_fixture import blank_session

    with blank_session() as db:
        db.execute(text("DELETE FROM chatbot_entity_kinds"))
        db.execute(text("DELETE FROM chatbot_domains"))
        for table, cols in (
            ("chatbot_entity_kinds", X.NEW_KIND_FIELDS), ("chatbot_domains", X.NEW_DOMAIN_FIELDS),
        ):
            for col in cols:
                db.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}"))
        for i, r in enumerate(_stale_kinds()):
            db.execute(
                text(
                    "INSERT INTO chatbot_entity_kinds (id, kind, label, resolver_source, did_you_mean, "
                    "default_narrowing, family_grouping, base_property_words, roster_cap, sort_order) VALUES "
                    "(gen_random_uuid(), :k, :l, :rs, :d, :n, :f, CAST(:b AS jsonb), :c, :s)"
                ),
                dict(k=r["kind"], l=r["label"], rs=r["resolver_source"], d=r["did_you_mean"],
                     n=r["default_narrowing"], f=r["family_grouping"], b=json.dumps(r["base_property_words"]),
                     c=r["roster_cap"], s=i),
            )
        for i, r in enumerate(_stale_domains()):
            db.execute(
                text(
                    "INSERT INTO chatbot_domains (id, name, label, intents, tools, switch_words, narrowing, "
                    "supported, ladder, sort_order) VALUES (gen_random_uuid(), :n, :l, CAST(:i AS text[]), "
                    "CAST(:t AS text[]), CAST(:sw AS text[]), CAST(:nw AS jsonb), :su, CAST(:la AS text[]), :s)"
                ),
                dict(n=r["name"], l=r["label"], i=list(r["intents"]), t=list(r["tools"]),
                     sw=list(r["switch_words"]), nw=json.dumps(r["narrowing"]), su=r["supported"],
                     la=list(r["ladder"]), s=i),
            )
        _assert_seed_values(load_policy(db))


# --------------------------------------------------------------------------- R3a-3: the retired tables are gone


def _gone(module: Any, name: str) -> None:
    assert getattr(module, name, _MISSING) is _MISSING, (
        f"{module.__name__}.{name} still exists: R3a-3 requires it deleted once the registry column is the source"
    )


def test_r3a3_guard_bridge_v1_hint_is_gone():
    _gone(bridge, "V1_HINT")


@pytest.mark.parametrize(
    "name",
    [
        "DOMAIN_BY_FOUND_KIND", "DOCUMENT_KINDS", "QUANTITY_DOMAINS", "CARRY_SCOPE_KINDS",
        "SUBJECT_REQUIRED_DOMAINS", "SET_DOMAINS", "FAMILY_DOMAINS", "SINGLE_SUBJECT_DOMAINS", "_FILTER_KINDS",
    ],
)
def test_r3a3_guard_apply_constant_is_gone(name):
    _gone(apply_mod, name)


def test_r3a3_guard_register_field_kind_is_gone():
    _gone(register_mod, "_FIELD_KIND")


def test_r3a3_guard_clarify_copy_keeps_words_only_for_kinds_with_no_row():
    """A per-kind or per-domain word table in clarify_copy may stay only for kinds with NO registry row
    (product_type, order_number, product_class, customer_order, supplier, spec, report_filter): the row kinds'
    words live in `labels` / `plural_labels` and the row domains' in `ask_labels`. `_COPY` and `_STILL_NEED` are
    sentence tables keyed by message, not by kind, and stay."""
    row_names = set(X.KINDS) | set(X.DOMAINS)
    leftovers: dict[str, set[str]] = {}
    for attr, value in vars(clarify_copy).items():
        if attr in ("_COPY", "_STILL_NEED") or attr.startswith("__") or not isinstance(value, dict):
            continue
        if not value or not all(isinstance(v, dict) and "en" in v for v in value.values()):
            continue
        hit = set(value) & row_names
        if hit:
            leftovers[attr] = hit
    assert not leftovers, f"clarify_copy still holds words for kinds or domains that have a row: {leftovers}"
    stays = getattr(clarify_copy, "_KIND_WORD", {})
    assert set(stays) <= X.CODE_FALLBACK_KIND_WORDS, set(stays) - X.CODE_FALLBACK_KIND_WORDS


def test_r3a3_guard_the_no_row_kind_words_stay_in_code():
    """Kinds with no row keep their word (the code fallback): not a regression to a raw kind name."""
    assert clarify_copy.kind_word("product_type", "zh") == "产品类型"
    assert clarify_copy.kind_word("product_class", "ms") == "kelas produk"
    assert clarify_copy.kind_word("spec", "en") == "value"
    assert clarify_copy.kind_word("report_filter", "zh") == "匹配项"
    assert clarify_copy.kind_word("supplier", "ms") == "pembekal"
    assert clarify_copy.kind_word("order_number", "en") == "order"
    assert clarify_copy.kind_word("customer_order", "ms") == "pesanan penghantaran"


# --------------------------------------------------------------------------- R3a-6: a registry edit moves PV2


def _play(state, parsed, *, registry=_MISSING, resolver=None):
    kwargs = {} if registry is _MISSING else {"registry": registry}
    return apply_turn(state, parsed, resolver=resolver or FakeResolver({}), now=NOW, **kwargs)


def _exact(kind, key):
    return {"status": "exact", "refs": [{"kind": kind, "key": key, "label": key}]}


def test_r3a6_a_kinds_default_domain_moves_the_domain_pv2_picks_when_that_kind_is_found():
    resolver = lambda: FakeResolver({"entities": {"order|DO1": _exact("delivery_order", "DO1")}})  # noqa: E731
    parsed = out(ask(act="new_topic", domain=None, entities=[entity("order", phrase="DO1")]))
    st, _ = _play(mk_state(), parsed, resolver=resolver())
    assert st.held.domain == "order" and list(st.held.refs) == ["delivery_order"]  # HEAD behaviour
    edited = _policy(kind_edits={"delivery_order": {"default_domain": "purchase_order"}})
    st, acts = _play(mk_state(), parsed, registry=edited, resolver=resolver())
    assert st.held.domain == "purchase_order", st.held.domain
    assert acts[0].domain == "purchase_order"


def test_r3a6_a_domains_reads_quantity_changes_where_a_bare_count_goes():
    photo = {
        "entities": {"product|SRTWT5880-CR": _exact("product", "SRTWT5880-CR")},
        "attachment_types": {"picture": _exact("attachment_type", "picture")},
    }
    first = out(ask(act="new_topic", domain="product_attachment", attachment_type="picture",
                    entities=[entity("product", phrase="SRTWT5880-CR")]))
    from app.services.chatbot.pv2.schema import Quantity

    count = entity("product", refers_to_held=True).model_copy(update={"quantity": Quantity(number=10, unit=None)})
    bare = out(ask(act="refines_held", domain=None, entities=[count]))

    def run(registry):
        resolver = FakeResolver(photo)
        st, _ = _play(mk_state(), first, registry=registry, resolver=resolver)
        return _play(st, bare, registry=registry, resolver=resolver)

    st, acts = run(_MISSING)
    assert st.held.domain == "inventory" and acts[0].domain == "inventory"  # HEAD: a count after a photo is stock
    edited = _policy(domain_edits={"product_attachment": {"reads_quantity": True}})
    st, acts = run(edited)
    assert st.held.domain == "product_attachment", st.held.domain  # the held domain reads a quantity: no switch
    assert acts[0].domain == "product_attachment"


@pytest.mark.parametrize(
    "language, edited_words, expected",
    [
        ("en", {"en": "client", "zh": "客人", "ms": "pelanggan"}, 'I couldn\'t find any client named "Zzq".'),
        ("zh", {"en": "client", "zh": "客人", "ms": "pelanggan"}, "找不到名为“Zzq”的客人。"),
    ],
)
def test_r3a6_a_kinds_labels_change_the_word_in_a_question(language, edited_words, expected):
    parsed = out(ask(act="new_topic", domain="inventory", entities=[entity("customer", phrase="Zzq")]),
                 language=language)
    base_st, base_acts = _play(mk_state(), parsed)
    assert "client" not in base_acts[0].pending.asked and "客人" not in base_acts[0].pending.asked  # HEAD wording
    edited = _policy(kind_edits={"customer": {"labels": edited_words}})
    st, acts = _play(mk_state(), parsed, registry=edited)
    assert acts[0].pending.asked == expected, acts[0].pending.asked


def test_r3a6_a_domains_escalation_team_is_read_from_the_turns_registry_not_the_seed():
    """`_offer_team` reads the live Policy when the turn carries no domain_teams map (plan R3a, bridge too)."""
    parsed = out(ask(act="new_topic", domain="order", entities=[entity("delivery_order", phrase="DO9")]))
    _, acts = _play(mk_state(), parsed)
    assert {o.payload.get("team_key") for o in acts[0].pending.options} == {"customer_service"}  # HEAD
    edited = _policy(domain_edits={"order": {"escalation_team_code": "zz_team"}})
    _, acts = _play(mk_state(), parsed, registry=edited)
    assert {o.payload.get("team_key") for o in acts[0].pending.options} == {"zz_team"}


# --------------------------------------------------------------------------- R3a-7: nothing else moves


def test_r3a7_guard_pv2_index_kinds_section_and_v1_entity_kind_vars_are_byte_identical_to_head():
    """No new kind rows and no column feeds the prompt text: the PV2 index KINDS lines, v1's `{{entity_kinds}}` var
    and `{{entity_kinds_detail}}` render exactly the strings captured at HEAD 2fb828b40. (The seed kind set equals
    contracts.ENTITY_HINTS: tests/chatbot/test_rearch_s0_entity_kinds_seed.py.)"""
    import json

    from sqlalchemy import text

    from app.services import chatbot_prompt_vars as V
    from app.services.chatbot.pv2 import index as I
    from tests._pg_fixture import blank_session

    with blank_session() as db:
        db.execute(text("DELETE FROM chatbot_entity_kinds"))
        for i, r in enumerate(_stale_kinds()):
            db.execute(
                text(
                    "INSERT INTO chatbot_entity_kinds (id, kind, label, resolver_source, did_you_mean, "
                    "default_narrowing, family_grouping, base_property_words, roster_cap, sort_order) VALUES "
                    "(gen_random_uuid(), :k, :l, :rs, :d, :n, :f, CAST(:b AS jsonb), :c, :s)"
                ),
                dict(k=r["kind"], l=r["label"], rs=r["resolver_source"], d=r["did_you_mean"],
                     n=r["default_narrowing"], f=r["family_grouping"], b=json.dumps(r["base_property_words"]),
                     c=r["roster_cap"], s=i),
            )
        V.clear_cache()
        try:
            assert V.render_value(db, "entity_kinds") == HEAD_ENTITY_KINDS_VAR
            assert V.render_value(db, "entity_kinds_detail") == HEAD_ENTITY_KINDS_DETAIL
        finally:
            V.clear_cache()
        assert I._kinds(db) == HEAD_INDEX_KINDS_LINES


HEAD_ENTITY_KINDS_VAR = (
    "product|promotion|customer|transporter|inbound_shipment|warehouse|attachment|form|order|category|brand|"
    "attachment_type|specification|delivery_order|sales_order|customer_po_ref|purchase_order|purchase_request"
)
HEAD_ENTITY_KINDS_DETAIL = "\n".join(
    [
        "Entity kind attachment: resolver attachments. Did-you-mean off. Default narrowing not_applicable.",
        "Entity kind attachment_type: resolver attachment_types. Did-you-mean on. Default narrowing narrow_by_type.",
        "Entity kind brand: resolver brands. Did-you-mean on. Default narrowing optional_filter.",
        "Entity kind category: resolver product_categories. Did-you-mean on. Default narrowing optional_filter.",
        "Entity kind customer: resolver customers. Did-you-mean on. Default narrowing must_narrow_one.",
        "Entity kind customer_po_ref: resolver sales_orders. Did-you-mean off. Default narrowing not_applicable.",
        "Entity kind delivery_order: resolver orders. Did-you-mean off. Default narrowing not_applicable.",
        "Entity kind form: resolver forms. Did-you-mean off. Default narrowing not_applicable.",
        "Entity kind inbound_shipment: resolver packing_lists. Did-you-mean off. Default narrowing optional_filter.",
        "Entity kind order: resolver orders. Did-you-mean off. Default narrowing not_applicable.",
        "Entity kind product: resolver master_products. Did-you-mean on. Default narrowing list_all.",
        "Entity kind promotion: resolver promotions. Did-you-mean on. Default narrowing optional_filter.",
        "Entity kind purchase_order: resolver purchase_orders. Did-you-mean off. Default narrowing not_applicable.",
        "Entity kind purchase_request: resolver purchase_requests. Did-you-mean off. Default narrowing not_applicable.",
        "Entity kind sales_order: resolver sales_orders. Did-you-mean off. Default narrowing not_applicable.",
        "Entity kind specification: resolver product_spec_registry. Did-you-mean off. Default narrowing optional_filter.",
        "Entity kind transporter: resolver transporters. Did-you-mean on. Default narrowing optional_filter.",
        "Entity kind warehouse: resolver warehouses. Did-you-mean on. Default narrowing optional_filter.",
    ]
)
HEAD_INDEX_KINDS_LINES = [
    "product = product", "promotion = promotion", "customer = customer", "transporter = transporter",
    "inbound_shipment = inbound shipment", "warehouse = warehouse", "form = form", "order = order",
    "brand = brand (key from BRANDS)", "delivery_order = delivery order", "sales_order = sales order",
    "customer_po_ref = customer po ref", "purchase_order = purchase order", "purchase_request = purchase request",
    "product_class = product class (key from PRODUCT CLASSES)",
    "product_type = product type (key from PRODUCT CLASSES types)",
]


# --------------------------------------------------------------------------- R3a-8: the live Policy rides the turn


def test_r3a8_the_turn_context_carries_the_live_registry():
    from app.services.chatbot.pv2.turn import TurnV2Context

    names = {f.name for f in dataclasses.fields(TurnV2Context)}
    assert "registry" in names, "TurnV2Context.registry (the turn's live Policy) is missing"


def test_r3a8_bootstrap_env_seeds_the_registry_columns_through_the_migrations_apply():
    import inspect

    from scripts import bootstrap_env

    src = inspect.getsource(bootstrap_env.seed_chatbot_policy)
    loaded = re.search(r"(\w+)\s*=\s*_load\([^)]*pv2_0004_kind_registry\.py", src)
    assert loaded, "seed_chatbot_policy does not load pv2_0004_kind_registry.py"
    call = f"{loaded.group(1)}.apply("
    assert call in src, f"seed_chatbot_policy never calls {call}...)"
    assert src.index(call) > src.index("rkind3.insert_kinds("), "apply must run after the kind rows are inserted"
    assert src.index(call) > src.index("ca3_domain.apply("), "apply must run after the domain rows are inserted"
