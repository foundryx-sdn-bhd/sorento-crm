"""PARSER-PER-AUDIENCE, owner ruling of 5 Oct 2026 (after a browser pass showed misroutes).

The per-audience trim KEEPS the classification vocabulary for every audience and strips ONLY
the heavy instruction blocks. On the trimmed prompt a contact without a grant asking "last
purchase cost for X" was routed as order, "status of PO-..." as a customer order, and
"sales report this month" / "low stock report" got a clarify menu, because the trim had
removed the gated domain enum entries, the domain words, the status values and the intent
name.

  V1 every audience keeps the vocabulary (tagged prod text AND the #1405 variable path).
  V2 the no-grant audience still drops the heavy block headings and is >= 6000 tokens smaller.
  V3 each gated domain keeps a one-line stub for a contact without the grant.
  V4 the registry list variables render the same rows for every audience.
  S1 "did you mean" order suggestions follow the contact's customer scope (turn runner).

Postgres only for the variable path and S1 (blank-schema `session_factory`). No em or en dashes.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from app.services import ai_prompt_registry, chatbot_prompt_vars as pv
from app.services.chatbot.head import parser as parser_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.prompt_gates import render_for_audience
from tests._mc_lookup_seed import customer as seed_customer, order as seed_order
from tests.chatbot.test_outstanding_lane import CONTACT_ID, _run_turn, _seed_contact, _session_of
from tests.chatbot.test_parser_audience_render import ALL_GRANTS, _prod, _tagged
from tests.chatbot.test_parser_audience_tagged_version import (
    KEY,
    _migration,
    _production_template,
)
from tests.chatbot.test_parser_audience_wiring import _publish

C, P, S, L = ALL_GRANTS
AUDIENCES = [
    pytest.param([], id="none"),
    pytest.param([C], id="C"),
    pytest.param([P], id="P"),
    pytest.param([S], id="S"),
    pytest.param([L], id="L"),
    pytest.param(list(ALL_GRANTS), id="all"),
]


@pytest.fixture(autouse=True)
def _fresh_cache():
    pv.clear_cache()
    ai_prompt_registry.bust_cache()
    yield
    pv.clear_cache()
    ai_prompt_registry.bust_cache()


def _has(text_: str, word: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", text_) is not None


def _section(rendered: str, start: str, width: int = 1500) -> str:
    at = rendered.find(start)
    assert at != -1, f"{start!r} is not in the render at all"
    return rendered[at : at + width]


# --------------------------------------------------------------------------- #
# V1 on the real tagged prod text
# --------------------------------------------------------------------------- #


class TestVocabularyKeptTaggedText:
    @pytest.mark.parametrize("grants", AUDIENCES)
    def test_domain_enum_keeps_the_gated_entries(self, grants) -> None:
        out = render_for_audience(_tagged(), grants)
        enum = _section(out, "domain_hint = ONE of:", 600).splitlines()[0]
        for name in ("purchase_cost", "spo_allocation"):
            assert _has(enum, name), (name, enum)

    @pytest.mark.parametrize("grants", AUDIENCES)
    def test_domain_in_message_words_keep_po_and_purchase_cost(self, grants) -> None:
        out = render_for_audience(_tagged(), grants)
        words = _section(out, "== DOMAIN IN MESSAGE ==", 600)
        for word in ("PO", "purchase cost"):
            assert _has(words, word), (word, words)

    @pytest.mark.parametrize("grants", AUDIENCES)
    def test_domain_names_status_values_and_intent_survive(self, grants) -> None:
        out = render_for_audience(_tagged(), grants)
        for word in ("purchase_order", "SPO", "sales_report", "low_stock_report", "check_po", "check_po_cost", "check_spo"):
            assert _has(out, word), word
        # the status value in the `status` OUTPUT line, not only in prose
        assert re.search(r'"status":[^\n]*sales_report', out), "status lost sales_report"
        for word in ("sales_analysis", "top_selling"):
            assert _has(out, word), word


EXAMPLES = {
    "purchase_cost": '"harga belian terakhir"',
    "purchase_order": '"stock, incoming and PO" -> asks',
    "spo_allocation": '"terakhir masuk"',
    "sales": '"dealer sales report for hanlim" -> sales_channel',
    "low_stock_report": '"laporan stok rendah"',
}


class TestWorkedExamplesStay:
    """Crew ruling 5 Oct 2026: the gated WORKED EXAMPLES are classification aid."""

    @pytest.mark.parametrize("grants", AUDIENCES)
    @pytest.mark.parametrize("domain", list(EXAMPLES))
    def test_each_gated_domains_example_line_is_in_every_audience(self, grants, domain) -> None:
        assert EXAMPLES[domain] in _prod(), "fixture line must exist in the prod text"
        assert EXAMPLES[domain] in render_for_audience(_tagged(), grants), domain


# --------------------------------------------------------------------------- #
# V2 / V3 on the real tagged prod text
# --------------------------------------------------------------------------- #

HEAVY = (
    "LAST PURCHASE COST", "PURCHASE ORDERS", "SPO LAST RECEIPT", "SALES REPORT",
    "SALES ANALYSIS", "TOP SELLING", "LOW STOCK REPORT",
)


class TestHeavyBlocksStillDropped:
    @pytest.mark.parametrize("title", HEAVY)
    def test_no_grant_render_has_no_heavy_heading(self, title) -> None:
        bare = render_for_audience(_tagged(), [])
        assert not re.search(rf"^== {re.escape(title)}\b", bare, flags=re.MULTILINE), title

    def test_no_grant_render_has_no_low_stock_filters_block(self) -> None:
        from app.services.chatbot_parser_prompt import LOW_STOCK_FILTERS_ADDENDUM

        bare = render_for_audience(_tagged(), [])
        assert "LOW STOCK REPORT FILTERS" not in bare
        assert LOW_STOCK_FILTERS_ADDENDUM.strip("\n") not in bare

    def test_no_grant_render_is_at_least_6000_tokens_smaller_than_full(self) -> None:
        tiktoken = pytest.importorskip("tiktoken")
        enc = tiktoken.get_encoding("o200k_base")
        full = len(enc.encode(render_for_audience(_tagged(), list(ALL_GRANTS))))
        bare = len(enc.encode(render_for_audience(_tagged(), [])))
        assert full - bare >= 6000, (full, bare, full - bare)
        assert full == len(enc.encode(_prod()))

    @pytest.mark.parametrize(
        "domain,pattern",
        [
            ("purchase_cost", r"purchase_cost"),
            ("purchase_order", r"purchase_order"),
            ("spo_allocation", r"spo_allocation"),
            ("sales", r"(?<![A-Za-z_])sales(?![A-Za-z_]| (?:order|report|analysis|figure))"),
            ("low_stock_report", r"low_stock_report"),
        ],
    )
    def test_each_gated_domain_keeps_a_stub_line_for_a_contact_without_the_grant(self, domain, pattern) -> None:
        bare = render_for_audience(_tagged(), [])
        stubs = [
            ln for ln in bare.splitlines()
            if re.search(pattern, ln)
            and not ln.startswith("domain_hint = ONE of:")
            and not re.match(r"^== (PURCHASE|SPO|LAST|SALES|TOP|LOW)", ln)
        ]
        assert stubs, f"no stub line names {domain} outside the enum line and the removed blocks"


# --------------------------------------------------------------------------- #
# V1 / V4 on the variable path: parser.resolve_config(grants=...)
# --------------------------------------------------------------------------- #

DOMAINS = [
    ("purchase_cost", [], ["purchase cost"]),
    ("purchase_order", [], ["PO"]),
    ("spo_allocation", [], ["SPO"]),
    ("sales", [], ["sales report"]),
    ("inventory", ["check_stock", "low_stock_report"], ["stock"]),
    ("order", ["check_order"], ["delivery"]),
]
STATUSES = ["outstanding", "sales_report", "sales_analysis", "top_selling"]
WORDS = ["stock", "PO", "purchase cost", "SPO", "delivery"]
LISTS = ["status_values", "status_field_values", "statuses"]
VARS = ["domains", "domain_words", "status_values", "status_field_values", "order_status_values", "statuses"]
VAR_TEMPLATE = "HEAD {{current_date}}\n" + "".join(f"@@{v}: {{{{{v}}}}}\n" for v in VARS) + "@@detail:\n{{domains_detail}}\nTAIL\n"


def _seed_registry(db) -> None:
    for i, (name, intents, switch) in enumerate(DOMAINS):
        db.execute(
            text(
                "INSERT INTO chatbot_domains (id, name, label, intents, switch_words, sort_order) "
                "VALUES (gen_random_uuid(), :n, :n, :i, :w, :o)"
            ),
            {"n": name, "i": intents, "w": switch, "o": i},
        )
    for i, value in enumerate(STATUSES):
        db.execute(
            text(
                "INSERT INTO chatbot_status_words (id, domain, value, label, prompt_lists, sort_order) "
                "VALUES (gen_random_uuid(), 'order', :v, :v, :l, :o)"
            ),
            {"v": value, "l": LISTS, "o": i},
        )
    for i, word in enumerate(WORDS):
        db.execute(
            text("INSERT INTO chatbot_domain_words (id, word, sort_order) VALUES (gen_random_uuid(), :w, :o)"),
            {"w": word, "o": i},
        )
    db.commit()


def _config(session_factory, monkeypatch, grants) -> str:
    monkeypatch.setattr("app.services.llm_provider.resolve_api_key", lambda cfg, provider: "sk-test")
    pv.clear_cache()
    return parser_mod.resolve_config(session_factory(), current_date="D", grants=grants).system_prompt


def _registry_template(session_factory) -> None:
    db = session_factory()
    if not db.execute(text("SELECT 1 FROM chatbot_domains LIMIT 1")).first():
        _seed_registry(db)
    _publish(session_factory, VAR_TEMPLATE)


def _var_lines(prompt: str) -> dict[str, str]:
    """Each `@@name:` variable's WHOLE block: up to the next `@@` marker or the TAIL line
    (`{{statuses}}` renders one status per line)."""
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"^@@(\w+): (.*?)(?=^@@|^TAIL$)", prompt, flags=re.MULTILINE | re.DOTALL)
    }


class TestRegistryListsAreTheSameForEveryAudience:
    @pytest.mark.parametrize("grants", AUDIENCES)
    def test_every_list_variable_renders_the_same_rows(self, session_factory, monkeypatch, grants) -> None:
        _registry_template(session_factory)
        full = _var_lines(_config(session_factory, monkeypatch, list(ALL_GRANTS)))
        got = _var_lines(_config(session_factory, monkeypatch, grants))
        assert set(got) == set(VARS), got
        assert got == full, {k: (got[k], full[k]) for k in VARS if got[k] != full[k]}

    def test_no_grant_lists_carry_the_gated_vocabulary(self, session_factory, monkeypatch) -> None:
        _registry_template(session_factory)
        got = _var_lines(_config(session_factory, monkeypatch, []))
        for name in ("purchase_cost", "purchase_order", "spo_allocation", "sales"):
            assert _has(got["domains"], name), got["domains"]
        for word in ("PO", "purchase cost", "SPO"):
            assert _has(got["domain_words"], word), got["domain_words"]
        for status in ("sales_report", "sales_analysis", "top_selling"):
            for var in ("status_values", "status_field_values", "order_status_values", "statuses"):
                assert _has(got[var], status), (var, got[var])


def _publish_tagged_from_production(session_factory) -> None:
    """The production template with its #1405 variables, tagged by the migration, and made
    the version `production` points at (the owner's publish step)."""
    _publish(session_factory, _production_template())
    db = session_factory()
    mod = _migration()
    version = mod.apply(db.connection())
    assert version is not None, "the migration must publish a tagged version from the production template"
    db.execute(
        text(
            "UPDATE ai_prompt_labels SET version_id = (SELECT id FROM ai_prompt_versions WHERE name = :n "
            "AND version = :v) WHERE name = :n AND label = 'production'"
        ),
        {"n": KEY, "v": version},
    )
    db.commit()
    ai_prompt_registry.bust_cache()


class TestVocabularyKeptVariablePath:
    @pytest.mark.parametrize("grants", AUDIENCES)
    def test_resolve_config_keeps_the_vocabulary_and_drops_the_heavy_blocks(
        self, session_factory, monkeypatch, grants
    ) -> None:
        db = session_factory()
        _seed_registry(db)
        _publish_tagged_from_production(session_factory)
        out = _config(session_factory, monkeypatch, grants)
        enum = _section(out, "domain_hint = ONE of:", 900).splitlines()[0]
        for name in ("purchase_cost", "purchase_order", "spo_allocation", "sales"):
            assert _has(enum, name), (name, enum)
        words = _section(out, "== DOMAIN IN MESSAGE ==", 900)
        for word in ("PO", "purchase cost", "SPO"):
            assert _has(words, word), (word, words)
        for word in ("sales_report", "sales_analysis", "top_selling", "low_stock_report", "check_po", "check_po_cost", "check_spo"):
            assert _has(out, word), word
        held = set(grants)
        gated_titles = {C: ["LAST PURCHASE COST"], P: ["PURCHASE ORDERS", "SPO LAST RECEIPT"],
                        S: ["SALES REPORT", "SALES ANALYSIS", "TOP SELLING"], L: ["LOW STOCK REPORT"]}
        for grant, titles in gated_titles.items():
            for title in titles:
                present = re.search(rf"^== {re.escape(title)}\b", out, flags=re.MULTILINE) is not None
                assert present == (grant in held), (title, grant in held)
        assert ("LOW STOCK REPORT FILTERS" in out) == (L in held)


# --------------------------------------------------------------------------- #
# S1: order "did you mean" follows the customer scope
# --------------------------------------------------------------------------- #

B_ORDER = "PO-2026/04-0011"
A_ORDER = "PO-2026/04-0013"
ASKED = "PO-2026/04-0012"


def _seed_orders(session_factory, *, office: bool) -> None:
    _seed_contact(session_factory, variables={})
    db = session_factory()
    a = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT SCOPE A")
    b = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT SCOPE B")
    seed_order(db, company_id=DEFAULT_COMPANY_ID, customer_id=str(a.id), number=A_ORDER)
    seed_order(db, company_id=DEFAULT_COMPANY_ID, customer_id=str(b.id), number=B_ORDER)
    db.execute(
        text(
            "INSERT INTO respond_contact_customers (id, contact_id, customer_id, company_id, created_at) "
            "SELECT gen_random_uuid(), id, :cust, :company, now() FROM respond_contacts WHERE respond_io_id = :cid"
        ),
        {"cust": str(a.id), "company": DEFAULT_COMPANY_ID, "cid": str(CONTACT_ID)},
    )
    if office:
        code = f"zzt_at_{uuid.uuid4().hex[:12]}"
        db.execute(
            text("INSERT INTO contact_access_types (code, name, is_active) VALUES (:c, 'Sorento Office', true)"),
            {"c": code},
        )
        db.execute(
            text(
                "INSERT INTO respond_contact_access_types (contact_id, access_type_code) "
                "SELECT id, :c FROM respond_contacts WHERE respond_io_id = :cid"
            ),
            {"c": code, "cid": str(CONTACT_ID)},
        )
    db.commit()


def _ask_for_missing_order(session_factory, monkeypatch) -> tuple[str, str]:
    from tests.chatbot.test_engine import _parser_output

    qf = _parser_output(
        domain_hint="order",
        intent_hint="check_order",
        entities=[{"raw": ASKED, "hint": "customer_order", "canonical_code": None,
                   "current_message": True, "confident": True}],
    )
    result, _captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=f"status of {ASKED}",
        msg_id=f"ZZT-s1-{uuid.uuid4().hex[:10]}", attributes=["sales_orders.outstanding"], real_resolver=True,
    )
    assert result.status == "done", result.error
    reply = str(result.reply or {})
    session = str(_session_of(session_factory))
    return reply, session


class TestOrderSuggestionsFollowCustomerScope:
    def test_scoped_contact_never_sees_another_customers_order_number(self, session_factory, monkeypatch) -> None:
        _seed_orders(session_factory, office=False)
        reply, session = _ask_for_missing_order(session_factory, monkeypatch)
        assert B_ORDER not in reply, reply
        assert B_ORDER not in session, "the other customer's order is in the stored offer candidates"

    def test_office_contact_is_still_offered_the_trigram_neighbour(self, session_factory, monkeypatch) -> None:
        _seed_orders(session_factory, office=True)
        reply, session = _ask_for_missing_order(session_factory, monkeypatch)
        assert B_ORDER in reply or B_ORDER in session, (reply, session)
