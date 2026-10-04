"""Pure derivation rules for `sales_orders.project_label` (PLAN-so-project-label.md).

Every function here is total and DB-free; `apply_project_label` (AC-R11) is the one
exception and it is exercised against a bare, unsaved `SalesOrder()` - no session needed,
since it only mutates two attributes and (when it writes) nothing else.

  AC-R1..R4   `label_from_inquiry_cell` - the Order Inquiry PROJECT/CUSTOMER cell
  AC-R5..R9   `label_from_note` - the AutoCount SO note, in its two shapes
  AC-R10      `label_from_ref` - the AutoCount SO `Ref`
  AC-R11      `apply_project_label` - the precedence gate

Security review fix round (SPL-B1/S2/S3 and nits): `_DELIVERY_LINE_RE` and the trailing-
punctuation strip in `label_from_note` used to backtrack quadratically on a long run of
spaces; `apply_project_label` had no length cap; a bare date after a `DELIVERY:` colon
read as a label; the old `_AGENT_STAMP_RE` killed real dash-numbered project names.
"""
from __future__ import annotations

import time

from app.models.order import SalesOrder
from app.services.project_label_rules import (
    SOURCE_RANK,
    apply_project_label,
    label_from_inquiry_cell,
    label_from_note,
    label_from_ref,
)


class TestLabelFromInquiryCell:
    def test_r1_three_part_cell_takes_everything_after_the_first_slash(self):
        assert (
            label_from_inquiry_cell("URC ENGINEERING / BAMBOO RESIDENCE / KUALA LUMPUR")
            == "BAMBOO RESIDENCE / KUALA LUMPUR"
        )

    def test_r2_slashes_with_no_spaces_are_normalised_to_one_space_either_side(self):
        assert label_from_inquiry_cell("KNUSFORD/EKOTITIWANGSA/KL") == "EKOTITIWANGSA / KL"

    def test_r3_stray_leading_space_after_the_first_slash_is_collapsed(self):
        assert (
            label_from_inquiry_cell("GLOBAL INGRESS/ 252U RMMJ TAMAN IMPIAN EMAS")
            == "252U RMMJ TAMAN IMPIAN EMAS"
        )

    def test_r4_no_slash_or_an_empty_remainder_is_no_label(self):
        assert label_from_inquiry_cell("OTM GROUP SDN BHD (SMC-contact-o)") is None
        assert label_from_inquiry_cell("") is None
        assert label_from_inquiry_cell("A / ") is None


class TestLabelFromNoteProjectLine:
    def test_r5_a_project_or_proj_label_line_wins_with_source_note(self):
        assert label_from_note("***PROJECT : TAIGA RESIDENCE") == ("TAIGA RESIDENCE", "note")
        assert label_from_note("**PROJECT; 72U LUMIERE SETIA ALAM") == (
            "72U LUMIERE SETIA ALAM",
            "note",
        )
        assert label_from_note("PROJ: PARK GREEN @ BUKIT JALIL") == (
            "PARK GREEN @ BUKIT JALIL",
            "note",
        )

    def test_r6_a_project_code_line_alone_is_the_label_but_a_name_line_beats_it(self):
        assert label_from_note("PROJECT CODE: 50-02") == ("50-02", "note")
        assert label_from_note(
            "PROJECT CODE : ES(S)-DUDUK\n***PROJECT : DUDUK SANTAI 1 & 2"
        ) == ("DUDUK SANTAI 1 & 2", "note")
        label, source = label_from_note(
            "PROJECT CODE: 110-EF(C)-01\n"
            "PROJECT TITLE: PROPOSED CONSTRUCTION OF 110 UNITS ..."
        )
        # Not pinned to the literal trailing "..." - the UAC uses it to abbreviate the real
        # sheet's much longer title, not as a literal character the rule must reproduce.
        assert source == "note"
        assert label.startswith("PROPOSED CONSTRUCTION OF 110 UNITS")

    def test_trailing_asterisks_are_stripped_like_any_other_punctuation(self):
        assert label_from_note("*** PROJECT : PINE LEGACY ***") == ("PINE LEGACY", "note")


class TestLabelFromNoteDeliveryBlock:
    def test_r7_a_delivery_block_names_the_site_line_with_source_delivery(self):
        assert label_from_note(
            "DELIVERY ADDRESS\n"
            "A-25-07 MAYA ARA RESIDENCES\n"
            "1 JALAN PJU 1A/1, ARA DAMANSARA"
        ) == ("MAYA ARA RESIDENCES", "delivery")
        assert label_from_note("DELIVERY TO : THE MET KL") == ("THE MET KL", "delivery")
        assert label_from_note(
            "DELIVERY TO ADDRESS\n12-09 ASTER GREEN RESIDENCE,"
        ) == ("ASTER GREEN RESIDENCE", "delivery")
        assert label_from_note(
            "DELIVERY ADDRESS : \nDENSO (MALAYSIA) SDN BHD"
        ) == ("DENSO (MALAYSIA) SDN BHD", "delivery")

    def test_r8_a_project_line_beats_a_delivery_block_in_the_same_note(self):
        assert label_from_note(
            "PROJECT: ZUS COFFEE @ KLANG VALLEY AREA\nDELIVERY ADDRESS\nSOME SITE"
        ) == ("ZUS COFFEE @ KLANG VALLEY AREA", "note")
        # And the other way round - the UAC says "regardless of order".
        assert label_from_note(
            "DELIVERY ADDRESS\nSOME SITE\nPROJECT: ZUS COFFEE @ KLANG VALLEY AREA"
        ) == ("ZUS COFFEE @ KLANG VALLEY AREA", "note")


class TestLabelFromNoteDeliveryDateGuard:
    """Security review, SPL-S3: `DELIVERY: 27/08/2026` names WHEN the goods arrive, not
    where - a bare date is never a project."""

    def test_a_bare_date_after_the_colon_is_no_label(self):
        assert label_from_note("DELIVERY: 27/08/2026") == (None, None)

    def test_delivery_date_header_is_still_no_label(self):
        assert label_from_note("DELIVERY DATE : 27/08/2026") == (None, None)


class TestLabelFromNoteNoMatch:
    def test_r9_own_collect_dates_and_contact_only_notes_give_nothing(self):
        assert label_from_note("***OWN COLLECT") == (None, None)
        assert label_from_note("EXCHANGE MODEL FROM SRT6638 - INV: ...\nOWN COLLECT") == (
            None,
            None,
        )
        assert label_from_note("***DELIVERY 27/08/2026") == (None, None)
        assert label_from_note("CONTACT : 016-000 0522") == (None, None)
        assert label_from_note("") == (None, None)
        assert label_from_note(None) == (None, None)


class TestLabelFromRef:
    def test_r10_agent_stamps_reserved_words_and_blanks_are_no_label(self):
        for ref in (
            "JF- 9/9 3.50",
            "JH - 8/9  10.17 PM",
            "JH-21/08/2026  11.32 AM",
            "RETAIL",
            "END USER",
            "REPLACEMENT",
            "REPLACEMENT ORDER",
            "",
            None,
        ):
            assert label_from_ref(ref) is None, ref

    def test_r10_anything_else_is_the_trimmed_text(self):
        assert label_from_ref("THE MET KL") == "THE MET KL"
        assert label_from_ref("PINNACLE SUBANG") == "PINNACLE SUBANG"
        assert label_from_ref("KSL BLOSSOM 733U @ SETIA ALAM") == "KSL BLOSSOM 733U @ SETIA ALAM"

    def test_a_dash_numbered_project_name_is_not_mistaken_for_an_agent_stamp(self):
        # Security review: the old `_AGENT_STAMP_RE` carried a second, broader
        # alternative (`^[A-Z]{2,8}-\d`) that killed real project names of exactly this
        # shape. Only the date-bearing stamp (`JH-21/08/2026 11.32 AM`, tested above)
        # actually distinguishes an agent's own note from a project.
        assert label_from_ref("MRT-2 DEPOT") == "MRT-2 DEPOT"


class TestApplyProjectLabel:
    """AC-R11. `SOURCE_RANK`: inquiry 4 > note 3 > ref 2 > delivery 1."""

    def test_writes_when_the_order_has_no_label(self):
        order = SalesOrder()
        assert apply_project_label(order, "TAIGA RESIDENCE", "note") is True
        assert order.project_label == "TAIGA RESIDENCE"
        assert order.project_label_source == "note"

    def test_writes_when_the_new_rank_is_higher(self):
        order = SalesOrder()
        order.project_label, order.project_label_source = "OLD", "ref"

        assert apply_project_label(order, "NEW", "note") is True
        assert order.project_label == "NEW"
        assert order.project_label_source == "note"

    def test_equal_rank_overwrites(self):
        order = SalesOrder()
        order.project_label, order.project_label_source = "OLD", "inquiry"

        assert apply_project_label(order, "CORRECTED", "inquiry") is True
        assert order.project_label == "CORRECTED"
        assert order.project_label_source == "inquiry"

    def test_a_lower_rank_leaves_the_order_completely_untouched(self):
        order = SalesOrder()
        order.project_label, order.project_label_source = "TAIGA RESIDENCE", "inquiry"
        before_updated_at = order.updated_at

        assert apply_project_label(order, "SOME NOTE LABEL", "note") is False
        assert order.project_label == "TAIGA RESIDENCE"
        assert order.project_label_source == "inquiry"
        assert order.updated_at == before_updated_at

    def test_never_writes_a_none_label_even_with_no_existing_one(self):
        order = SalesOrder()
        assert apply_project_label(order, None, "note") is False
        assert order.project_label is None
        assert order.project_label_source is None

    def test_source_rank_is_the_documented_ladder(self):
        assert SOURCE_RANK == {"inquiry": 4, "note": 3, "ref": 2, "delivery": 1}

    def test_a_300kb_inquiry_cell_yields_a_200_char_label(self):
        # Security review, SPL-S2: `apply_project_label` is the ONE choke point every
        # writer (ingest, importer, migration 511's backfill) goes through, so the length
        # cap belongs here rather than in each of the three callers.
        huge_label = "BAMBOO RESIDENCE " * 17_647  # ~300 KB
        order = SalesOrder()

        assert apply_project_label(order, huge_label, "inquiry") is True
        assert order.project_label == huge_label.strip()[:200]
        assert len(order.project_label) == 200

    def test_a_label_that_is_blank_after_stripping_does_not_write(self):
        order = SalesOrder()
        assert apply_project_label(order, "   ", "note") is False
        assert order.project_label is None
        assert order.project_label_source is None


class TestLabelFromNotePerformance:
    """Security review, SPL-B1: `_DELIVERY_LINE_RE`'s two adjacent `\\s*` runs and the
    regex-based trailing-punctuation strip both backtracked quadratically - measured at
    48s and 10.7s respectively on the payloads below, from a single call this module's
    own callers cannot avoid (the AutoCount ingest route runs a batch of up to 1000 such
    notes through `label_from_note` synchronously, once per push).
    """

    _BUDGET_SECONDS = 0.5

    def test_a_long_run_of_spaces_after_delivery_resolves_quickly(self):
        payload = "DELIVERY" + " " * 100_000 + "x"
        start = time.perf_counter()
        label_from_note(payload)
        assert time.perf_counter() - start < self._BUDGET_SECONDS

    def test_a_long_run_of_spaces_after_a_project_line_resolves_quickly(self):
        payload = "PROJECT: A" + " " * 50_000 + "b"
        start = time.perf_counter()
        label_from_note(payload)
        assert time.perf_counter() - start < self._BUDGET_SECONDS

    def test_a_100kb_project_line_resolves_quickly(self):
        payload = "PROJECT: " + "x" * 100_000
        start = time.perf_counter()
        label_from_note(payload)
        assert time.perf_counter() - start < self._BUDGET_SECONDS
