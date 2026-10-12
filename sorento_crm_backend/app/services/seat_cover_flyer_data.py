"""The seat cover flyer, transcribed by code (WC-SEAT-COMPAT).

Source: "SORENTO SEAT COVER COMPATIBLE WATER CLOSET MODEL", END USER, UPDATE 01.04.2026,
footer 2025-08, page 1 (35 rows). Codes copied exactly as printed; prices, dimensions and
the PDF itself are deliberately not here. Every pair in this list still goes through owner
review before it becomes a link (`product_compatibility_service.flyer_proposals`).

Page 2 lists seven intelligent-seat WCs (SRTWC185/187/189/189-G2/191/191-G2/195) with a
drawing and no seat cover code. They are not here on purpose: there is nothing to link
(owner decision e, never fabricate).

Plan: documentation/plans/products/PLAN-wc-seat-compat-12oct.md.
"""
from __future__ import annotations

FLYER_LABEL = "Seat cover flyer, update 01.04.2026"

#: (flyer row number, seat cover codes as printed, WC model codes as printed).
#: Row 6 prints two covers for one WC; every other row prints one.
FLYER_ROWS: tuple[tuple[int, tuple[str, ...], tuple[str, ...]], ...] = (
    (1, ("SRTSCBD319-UF",), ("SRTWC8320", "SRTWC8319", "SRTWC200")),
    (2, ("SRTSCBD330-UF",), ("SRTWC8088",)),
    (3, ("SRTSCBD331-UF",), ("SRTWC8036", "SRTWC8354", "SRTWC8035", "SRTWC8383", "SRTWC8517")),
    (4, ("SRTSCBD332-UF",), ("SRTWC8357", "SRTWC8366")),
    (5, ("SRTSCBD289-UF",), ("SRTWC289", "SRTWC6031")),
    (6, ("SRTSCBD333-UF", "SRTSCBD333A-UF"), ("SRTWC8122",)),
    (7, ("SRTSCBD335-UF",), ("SRTWC6015",)),
    (8, ("SRTSCBD336-UF",), ("SRTWC6005",)),
    (9, ("SRTSCBD337-UF",), ("SRTWC6020", "SRTWC6021")),
    (10, ("SRTSCBD338-UF",), ("SRTWC6022",)),
    (11, ("SRTSCBD403",), ("SRTWC8504",)),
    (12, ("SRTSCBD324",), ("SRTWC8317",)),
    (13, ("SRTSCBD323",), ("SRTWC8318",)),
    (14, ("SRTSCBD320",), ("SRTWC8152", "SRTWC8356")),
    (15, ("SRTSCBD407",), ("SRTWC286", "SRTWC8355", "SRTWC8066")),
    (16, ("SRTSCBD401",), ("SRTWC7614",)),
    (17, ("SRTSCBD315",), ("SRTWC287",)),
    (18, ("SRTSCBD313",), ("SRTWC287A",)),
    (19, ("SRTSCBD339-UF",), ("SRTWC6030",)),
    (20, ("SRTSCBD408",), ("SRTWC8518",)),
    (21, ("SRTSCBD325",), ("SRTWC201",)),
    (22, ("SRTSCBD326",), ("SRTWC202",)),
    (23, ("SRTSCBD327",), ("SRTWC203",)),
    (24, ("SRTSCBD823-MBL",), ("SRTWC8383-MBL",)),
    (25, ("SRTSCBD824-BL",), ("SRTWC8383-BL", "SRTWC8152-BL")),
    (26, ("SRTSCBD834-MBL",), ("SRTWC8318-MBL",)),
    (27, ("SRTSCBD833-BL",), ("SRTWC8318-BL",)),
    (28, ("SRTSCBD825-GY",), ("SRTWC8383-GY",)),
    (29, ("SRTSCBD826-RG",), ("SRTWC8383-RG",)),
    (30, ("SRTSCBD836-RG",), ("SRTWC8318-RG",)),
    (31, ("SRTSCBD835-GY",), ("SRTWC8318-GY",)),
    (32, ("SRTSCBD841-BL",), ("SRTWC8066-S-BL",)),
    (33, ("SRTSCBD842-MBL",), ("SRTWC8066-S-MBL",)),
    (34, ("SRTSCBD843-GY",), ("SRTWC8066-S-GY",)),
    (35, ("SRTSCBD844-RG",), ("SRTWC8066-S-RG",)),
)


#: A finish the flyer prints as a note beside the WC code rather than in it: row 22 prints
#: SRTWC202 with "Pink" and a pink dot, row 23 prints SRTWC203 with "Blue" and a blue dot.
#: Every other colour row prints its finish inside the code (SRTWC8383-MBL).
FLYER_ROW_FINISH: dict[int, str] = {22: "PINK", 23: "BLUE"}


def flyer_pairs() -> list[tuple[int, str, str]]:
    """Every (row, WC code, seat cover code) the flyer prints, in flyer order: 49 pairs."""
    return [
        (row, wc, cover)
        for row, covers, wcs in FLYER_ROWS
        for wc in wcs
        for cover in covers
    ]
