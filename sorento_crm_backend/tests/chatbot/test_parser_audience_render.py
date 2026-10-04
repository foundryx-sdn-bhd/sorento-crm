"""PARSER-PER-AUDIENCE, Phase 2 red tests: the tag renderer and the gate table
(AC-PA-1, AC-PA-5 to AC-PA-8).

`documentation/plans/chatbot/PLAN-parser-per-audience-2oct.md` and
`parser-per-audience-2oct-acceptance-criteria.md`. Written BEFORE `app/services/chatbot/
prompt_gates.py` and the tagged template data file exist, so every test here is red on
ModuleNotFoundError / FileNotFoundError / AssertionError until the coder lands them.

Pure unit tests: no database. The leak matrix is parametrised over the 16 subsets of the
four gated grants plus the unidentified contact, and its forbidden terms and block titles
are typed out HERE from the UAC (AC-PA-6), not read off `PROMPT_GATES`, so a table that
quietly dropped a term cannot make its own test pass. `PROMPT_GATES` is only asserted to
CONTAIN those terms.

No em or en dashes.
"""
from __future__ import annotations

import importlib
import itertools
import logging
import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
DATA = BACKEND / "alembic" / "data"
PROD_TEXT = DATA / "chatbot_semantic_parser.prod-20261001.txt"
TAGGED_TEXT = DATA / "chatbot_semantic_parser.prod-20261001.tagged.txt"

C = "purchase_orders.cost"
P = "purchase_orders.placed"
S = "sales_orders.sales_report"
L = "scm.low_stock_report"
ALL_GRANTS = (C, P, S, L)

#: One grant may carry several rows (placed carries purchase_order and spo_allocation).
TAGS = {
    C: ["purchase_cost"],
    P: ["purchase_order", "spo_allocation"],
    S: ["sales"],
    L: ["low_stock_report"],
}
TAG = {tag: grant for grant, tags in TAGS.items() for tag in tags}

#: AC-PA-6 as reversed by the owner ruling of 5 Oct 2026: the classification vocabulary
#: (domain ids purchase_cost, purchase_order, spo_allocation, sales; the words "PO",
#: "purchase cost", "SPO"; the statuses sales_report, sales_analysis, top_selling; the
#: intent low_stock_report) stays for EVERY audience, so it is no longer forbidden. What
#: stays forbidden is the heavy instruction detail only. The block headings are asserted
#: absent by `^== TITLE` in the matrix below and in test_parser_audience_vocab_kept.py.
FORBIDDEN = {
    C: [],
    P: ["PO placed", "supplier order"],
    S: ["rank_by", "sales_basis"],
    L: [],
}

#: The block headings (`== TITLE`) a held grant must keep.
BLOCKS = {
    C: ["LAST PURCHASE COST"],
    P: ["PURCHASE ORDERS", "SPO LAST RECEIPT"],
    S: ["SALES REPORT", "SALES ANALYSIS", "TOP SELLING"],
    L: ["LOW STOCK REPORT"],
}


def _gates():
    return importlib.import_module("app.services.chatbot.prompt_gates")


def _render(template: str, grants) -> str:
    return _gates().render_for_audience(template, grants)


def _prod() -> str:
    return PROD_TEXT.read_text(encoding="utf-8")


_tagged_cache: list[str] = []


def _tagged() -> str:
    if not _tagged_cache:
        _tagged_cache.append(TAGGED_TEXT.read_text(encoding="utf-8"))
    return _tagged_cache[0]


def _has_term(text: str, term: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _subsets():
    for n in range(len(ALL_GRANTS) + 1):
        yield from itertools.combinations(ALL_GRANTS, n)


def _subset_id(subset: tuple[str, ...]) -> str:
    letters = {C: "C", P: "P", S: "S", L: "L"}
    return "+".join(letters[g] for g in subset) or "none"


# --------------------------------------------------------------------------- #
# AC-PA-1: one table
# --------------------------------------------------------------------------- #


class TestPromptGatesShape:
    def _rows(self) -> dict[tuple[str, str | None], object]:
        return {(g.domain, g.part): g for g in _gates().PROMPT_GATES}

    def test_exactly_the_five_rows_keyed_by_domain_and_part(self) -> None:
        gates = _gates().PROMPT_GATES
        keys = [(g.domain, g.part) for g in gates]
        assert len(set(keys)) == len(keys), "(domain, part) must be unique"
        assert sorted(keys, key=str) == sorted(
            [
                ("purchase_cost", None),
                ("purchase_order", None),
                ("spo_allocation", None),
                ("sales", None),
                ("inventory", "low_stock_report"),
            ],
            key=str,
        )

    def test_several_rows_may_share_one_grant_and_placed_has_two(self) -> None:
        gates = _gates().PROMPT_GATES
        by_grant: dict[str, list[str]] = {}
        for g in gates:
            by_grant.setdefault(g.grant, []).append(g.domain)
        assert sorted(by_grant) == sorted(ALL_GRANTS)
        assert sorted(by_grant[P]) == ["purchase_order", "spo_allocation"]
        assert by_grant[C] == ["purchase_cost"]
        assert by_grant[S] == ["sales"]
        assert by_grant[L] == ["inventory"]

    def test_the_row_fields_and_tags(self) -> None:
        rows = self._rows()
        expected = {
            ("purchase_cost", None): (C, "purchase_cost"),
            ("purchase_order", None): (P, "purchase_order"),
            ("spo_allocation", None): (P, "spo_allocation"),
            ("sales", None): (S, "sales"),
            ("inventory", "low_stock_report"): (L, "low_stock_report"),
        }
        for key, (grant, tag) in expected.items():
            row = rows[key]
            assert (row.grant, row.tag) == (grant, tag), key
            for field in (
                "blocks", "forbidden_terms", "refused_domains", "refused_intents",
                "refused_order_statuses",
            ):
                assert isinstance(getattr(row, field), tuple), (key, field)

    def test_the_row_is_frozen(self) -> None:
        row = _gates().PROMPT_GATES[0]
        with pytest.raises(Exception):
            row.tag = "other"  # type: ignore[misc]

    def test_blocks_and_refusals_per_row(self) -> None:
        rows = self._rows()
        cost = rows[("purchase_cost", None)]
        assert cost.blocks == ("LAST PURCHASE COST",)
        assert cost.refused_domains == ("purchase_cost",)
        po = rows[("purchase_order", None)]
        assert "PURCHASE ORDERS" in po.blocks
        assert "purchase_order" in po.refused_domains
        spo = rows[("spo_allocation", None)]
        assert spo.blocks == ("SPO LAST RECEIPT",)
        assert spo.refused_domains == ("spo_allocation",)
        for term in ("spo_allocation", "check_spo", "SPO LAST RECEIPT", "last received"):
            assert term in spo.forbidden_terms, term
        sales = rows[("sales", None)]
        assert sales.blocks == ("SALES REPORT", "SALES ANALYSIS", "TOP SELLING")
        assert sales.refused_domains == ("sales",)
        assert sales.refused_order_statuses == ("sales_report", "sales_analysis", "top_selling")
        low = rows[("inventory", "low_stock_report")]
        assert low.blocks == ("LOW STOCK REPORT",)
        assert low.refused_intents == ("low_stock_report",)
        assert low.refused_domains == ()

    @pytest.mark.parametrize("grant", ALL_GRANTS)
    def test_forbidden_terms_of_a_grant_are_the_union_of_its_rows(self, grant: str) -> None:
        union = {t for g in _gates().PROMPT_GATES if g.grant == grant for t in g.forbidden_terms}
        missing = [t for t in FORBIDDEN[grant] if t not in union]
        assert not missing, missing

    def test_gate_for(self) -> None:
        gates = _gates()
        assert gates.gate_for("sales").grant == S
        assert gates.gate_for("purchase_order").grant == P
        assert gates.gate_for("spo_allocation").grant == P
        assert gates.gate_for("purchase_cost", None).grant == C
        assert gates.gate_for("inventory", "low_stock_report").grant == L
        assert gates.gate_for("inventory") is None
        assert gates.gate_for("inventory", "check_stock") is None
        assert gates.gate_for("no_such_domain") is None

    def test_prompt_blocks_for_aggregates_every_row_of_a_grant_in_table_order(self) -> None:
        gates = _gates()
        assert gates.prompt_blocks_for(P) == ["PURCHASE ORDERS", "SPO LAST RECEIPT"]
        assert gates.prompt_blocks_for(S) == ["SALES REPORT", "SALES ANALYSIS", "TOP SELLING"]
        assert gates.prompt_blocks_for(C) == ["LAST PURCHASE COST"]
        assert gates.prompt_blocks_for(L) == ["LOW STOCK REPORT"]
        assert gates.prompt_blocks_for("inventory.sellable") == []
        assert gates.prompt_blocks_for("no.such.key") == []


# --------------------------------------------------------------------------- #
# AC-PA-5: the full render is today's prompt, byte for byte
# --------------------------------------------------------------------------- #


class TestFullRenderUnchanged:
    def test_tagged_file_exists_beside_the_prod_text(self) -> None:
        assert TAGGED_TEXT.is_file(), TAGGED_TEXT

    def test_all_four_grants_render_the_prod_text_byte_for_byte(self) -> None:
        assert _render(_tagged(), list(ALL_GRANTS)) == _prod()

    def test_grant_order_and_extra_grants_do_not_matter(self) -> None:
        rendered = _render(_tagged(), [L, S, P, C, "inventory.sellable", "purchase_orders.supplier"])
        assert rendered == _prod()

    def test_the_tagged_file_actually_carries_tags(self) -> None:
        tagged = _tagged()
        for tag in TAG:
            assert "{{#only " + tag + "}}" in tagged, tag
        assert tagged.count("{{#only ") == tagged.count("{{/only}}")


# --------------------------------------------------------------------------- #
# AC-PA-6: leak matrix, prompt half
# --------------------------------------------------------------------------- #

_AUDIENCES = [pytest.param(list(s), id=_subset_id(s)) for s in _subsets()] + [
    pytest.param(None, id="unidentified-None"),
    pytest.param([], id="unidentified-empty"),
]


class TestLeakMatrixPromptHalf:
    @pytest.mark.parametrize("grants", _AUDIENCES)
    def test_no_forbidden_term_of_a_missing_grant(self, grants) -> None:
        # Owner ruling 5 Oct 2026: only the heavy detail terms are forbidden now (see
        # FORBIDDEN); the vocabulary is asserted KEPT in test_parser_audience_vocab_kept.py.
        held = set(grants or [])
        rendered = _render(_tagged(), grants)
        leaked = {
            _subset_id((grant,)): [t for t in FORBIDDEN[grant] if _has_term(rendered, t)]
            for grant in ALL_GRANTS
            if grant not in held
        }
        leaked = {k: v for k, v in leaked.items() if v}
        assert not leaked, leaked
        for grant in ALL_GRANTS:
            if grant in held:
                continue
            for title in BLOCKS[grant]:
                assert not re.search(rf"^== {re.escape(title)}\b", rendered, flags=re.MULTILINE), title

    @pytest.mark.parametrize("grants", _AUDIENCES)
    def test_every_block_of_a_held_grant_is_kept(self, grants) -> None:
        held = set(grants or [])
        rendered = _render(_tagged(), grants)
        absent = [
            title
            for grant in ALL_GRANTS
            if grant in held
            for title in BLOCKS[grant]
            if not re.search(rf"^== {re.escape(title)}\b", rendered, flags=re.MULTILINE)
        ]
        assert not absent, absent

    @pytest.mark.parametrize("grants", _AUDIENCES)
    def test_no_tag_marker_survives_and_the_render_is_not_empty(self, grants) -> None:
        rendered = _render(_tagged(), grants)
        assert "{{#only" not in rendered and "{{/only}}" not in rendered
        assert len(rendered) > 50_000

    def test_a_missing_grant_removes_its_block_and_only_its_block(self) -> None:
        full = _render(_tagged(), list(ALL_GRANTS))
        for grant in ALL_GRANTS:
            held = [g for g in ALL_GRANTS if g != grant]
            assert len(_render(_tagged(), held)) < len(full), grant

    def test_the_render_depends_only_on_the_grants_argument(self) -> None:
        # Kill test: the audience read from anything but `grants` (access types, a cache
        # keyed by something else) would make two identical calls differ, or leak across.
        first = _render(_tagged(), [S])
        _render(_tagged(), list(ALL_GRANTS))
        _render(_tagged(), [])
        assert _render(_tagged(), [S]) == first
        assert _render(_tagged(), (S,)) == first
        assert _render(_tagged(), {S}) == first


# --------------------------------------------------------------------------- #
# AC-PA-7: sizes
# --------------------------------------------------------------------------- #


class TestSizes:
    """Character proxy for the UAC's o200k_base token bounds (no network, no tiktoken).
    The plan measured 26,666 tokens for the minimal audience and 27,252 for {P}, against
    33,207 for the full text: 80.3% and 82.1%. The ratios below are the 27,000 / 27,600
    token bounds over 33,207 (81.3% and 83.1%) with a little headroom for the proxy."""

    def test_minimal_audience_is_at_most_82_percent_of_the_prod_text(self) -> None:
        assert len(_render(_tagged(), [])) / len(_prod()) <= 0.83  # crew ruling 5 Oct: worked examples stay

    def test_placed_only_is_at_most_84_percent_of_the_prod_text(self) -> None:
        assert len(_render(_tagged(), [P])) / len(_prod()) <= 0.84


# --------------------------------------------------------------------------- #
# AC-PA-8: malformed tags, and the two marker forms
# --------------------------------------------------------------------------- #


class TestMarkerForms:
    def test_own_line_block_is_removed_with_its_markers_and_no_blank_line(self) -> None:
        template = "a\n{{#only sales}}\nSECRET\nmore\n{{/only}}\nb\n"
        assert _render(template, []) == "a\nb\n"

    def test_own_line_block_held_keeps_the_body_and_drops_only_the_marker_lines(self) -> None:
        template = "a\n{{#only sales}}\nSECRET\nmore\n{{/only}}\nb\n"
        assert _render(template, [S]) == "a\nSECRET\nmore\nb\n"

    def test_inline_span_is_stripped_when_not_held(self) -> None:
        template = 'x "A", {{#only sales}}"sales_report", {{/only}}"B" y\n'
        assert _render(template, []) == 'x "A", "B" y\n'

    def test_inline_span_keeps_only_the_marker_text_removed_when_held(self) -> None:
        template = 'x "A", {{#only sales}}"sales_report", {{/only}}"B" y\n'
        assert _render(template, [S]) == 'x "A", "sales_report", "B" y\n'

    def test_each_tag_is_matched_to_its_own_grant(self) -> None:
        template = (
            "{{#only purchase_cost}}\nC\n{{/only}}\n"
            "{{#only purchase_order}}\nP\n{{/only}}\n"
            "{{#only spo_allocation}}\nO\n{{/only}}\n"
            "{{#only sales}}\nS\n{{/only}}\n"
            "{{#only low_stock_report}}\nL\n{{/only}}\n"
        )
        assert _render(template, [C]) == "C\n"
        assert _render(template, [P]) == "P\nO\n"  # one grant, two rows
        assert _render(template, [S]) == "S\n"
        assert _render(template, [L]) == "L\n"

    def test_text_without_tags_is_returned_unchanged(self) -> None:
        template = "plain\n\n  indented {{current_date}}\ntrailing"
        assert _render(template, []) == template

    def test_grants_none_is_empty(self) -> None:
        template = "a\n{{#only sales}}\nS\n{{/only}}\nb\n"
        assert _render(template, None) == _render(template, []) == "a\nb\n"


class TestMalformedTags:
    def test_unknown_tag_keeps_its_block_drops_markers_and_warns(self, caplog) -> None:
        template = "a\n{{#only nonsense}}\nKEEP\n{{/only}}\nb\n"
        with caplog.at_level(logging.WARNING):
            out = _render(template, [])
        assert out == "a\nKEEP\nb\n"
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_unbalanced_open_keeps_the_block_and_warns(self, caplog) -> None:
        template = "a\n{{#only sales}}\nKEEP\nb\n"
        with caplog.at_level(logging.WARNING):
            out = _render(template, [])
        assert "KEEP" in out and "b" in out and "{{" not in out
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_stray_close_is_removed_and_warns(self, caplog) -> None:
        template = "a\n{{/only}}\nb\n"
        with caplog.at_level(logging.WARNING):
            out = _render(template, [])
        assert out == "a\nb\n"
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_nested_tags_never_raise_keep_the_text_and_warn(self, caplog) -> None:
        template = (
            "a\n{{#only sales}}\nS1\n{{#only purchase_cost}}\nC1\n{{/only}}\nS2\n{{/only}}\nb\n"
        )
        with caplog.at_level(logging.WARNING):
            out = _render(template, [])
        assert "{{" not in out
        for kept in ("S1", "C1", "S2", "a", "b"):
            assert kept in out, kept
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_well_formed_tags_log_no_warning(self, caplog) -> None:
        template = "a\n{{#only sales}}\nS\n{{/only}}\nb {{#only purchase_cost}}c{{/only}}\n"
        with caplog.at_level(logging.WARNING):
            _render(template, [])
            _render(template, list(ALL_GRANTS))
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
