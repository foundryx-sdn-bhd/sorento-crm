"""NARROW-CODE-OWN-LINE: `narrow_code_tokens` reads descriptor words from the code's own
line, and never narrows a base code whose variants the message types as their own tokens.

Owner's 15-line stock check of 6 Oct 2026 20:02 MYT (found by lane RESOLVE-SEMANTIC-FALLBACK,
PR #1511 crew-note 2026-10-06T14:44:17Z): the message carries `TAP SRTWT5608 x 5` AND the
variants `SRTWT5608-FRG x 5` / `SRTWT5608-GM x 10` on their own lines. PR #833's
`narrow_code_tokens` read every non-code word of the WHOLE message as a variant descriptor
for the bare code, so the other lines' suffix words `FRG` / `GM` narrowed `SRTWT5608` to
`SRTWT5608-FRG` and the base code's own line was lost: 13 unique codes reached the tool, not
14, and on prod the TAP line answered with SRTWT5608-FRG's stock.

Fake ZZT fixture codes only (`SRTWT` typed as `ZZTWT`). The 15-line message below is the
owner's, verbatim apart from that prefix. On main the TAP line is a raw-only entity until
#1511's canonical_code lands; the engine test therefore types the line bare (`ZZTWT5608 x
5`), which is the shape the crew-note reproduced the loss with on origin/main. The unit test
hands `narrow_code_tokens` the exact tokens #1511's parser emits for the verbatim message
(the TAP line as its canonical code).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.product_code_family import narrow_code_tokens

from tests.chatbot._turn_helpers import make_office_access_type
from tests.chatbot.test_engine import CONTACT_ID
from tests.chatbot.test_outstanding_lane import REPORT_HIT, _qf, _run_turn, _seed_contact

#: The owner's message, codes ZZT-prefixed, lines in the order typed.
OWNER_LINES: list[tuple[str, int]] = [
    ("ZZTWT7408", 5), ("ZZTWT6808", 5), ("ZZTWT162", 5), ("ZZTWT8258-GM", 5), ("ZZTWT8258-GM", 5),
    ("ZZTWT8212", 5), ("ZZTWT6832-RG", 5), ("ZZTWT5608", 5), ("ZZTWT5608-FRG", 5), ("ZZTWT5608-GM", 10),
    ("ZZTWT6201", 5), ("ZZTWT8301-RG", 5), ("ZZTWT8301-GMB", 5), ("ZZTWT8303-RG", 5), ("ZZTWT8303-GMB", 5),
]
BASE = "ZZTWT5608"
UNIQUE = list(dict.fromkeys(code for code, _ in OWNER_LINES))


def _owner_message(*, tap_word: bool) -> str:
    body = "\n".join(f"{'TAP ' if tap_word and c == BASE else ''}{c} x {q}" for c, q in OWNER_LINES)
    return body + "\n\ncheck stock"


def _gate_token(code: str) -> str:
    """The token `resolve_gate._token_of` builds from a product entity: separators folded."""
    return code.replace("-", "").replace(" ", "")


def _scoped(session_factory):
    """A session scoped to the default company, the way the resolve route's own session is
    (an UNSET scope fails closed and `_family` would see no products at all)."""
    from app.models.base import set_company_scope
    from app.services.company_scope import DEFAULT_COMPANY_ID

    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    return db


def _seed(session_factory, *codes: str, names: dict[str, str] | None = None) -> None:
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import product as seed_product

    db = session_factory()
    for code in codes:
        row = seed_product(db, company_id=DEFAULT_COMPANY_ID, code=code)
        if names and code in names:
            row.product_name = names[code]
    db.commit()


def _entity(raw: str, canonical: str | None, qty: int) -> dict[str, Any]:
    return {
        "raw": raw, "hint": "product", "canonical_code": canonical, "current_message": True,
        "confident": True, "quantity": qty,
    }


def _uuids_passed(captured) -> int:
    diagnostics = [args.get("_diagnostics") or {} for _name, args in captured]
    return max((d.get("total_uuids_passed", 0) for d in diagnostics), default=0)


# --------------------------------------------------------------------------- #
# narrow_code_tokens, the function itself                                        #
# --------------------------------------------------------------------------- #


def test_the_owners_fifteen_line_message_narrows_nothing(session_factory):
    """The verbatim message with the tokens #1511's parser emits (TAP line as its canonical
    code): no token is a code plus a descriptor, so the request stays byte-identical. On
    main the other lines' `FRG` / `GM` narrowed ZZTWT5608 to ZZTWT5608-FRG."""
    assert len(UNIQUE) == 14
    _seed(session_factory, *UNIQUE)
    tokens = [_gate_token(code) for code, _ in OWNER_LINES]

    narrowed = narrow_code_tokens(
        _scoped(session_factory),
        query=_owner_message(tap_word=True),
        tokens=tokens,
        allowed_types=["product"] * len(tokens),
    )

    assert narrowed is None, narrowed


def test_a_base_code_is_never_narrowed_when_its_variants_are_typed_as_their_own_tokens(session_factory):
    """One line, so own-line reading alone would still see `FRG` and `GM`: the variants are
    their own tokens, so the base code is an ask of its own and stays."""
    _seed(session_factory, BASE, f"{BASE}-FRG", f"{BASE}-GM")
    query = f"{BASE} x 5, {BASE}-FRG x 5, {BASE}-GM x 10"
    tokens = [BASE, f"{BASE}FRG", f"{BASE}GM"]

    narrowed = narrow_code_tokens(_scoped(session_factory), query=query, tokens=tokens, allowed_types=["product"] * 3)

    assert narrowed is None, narrowed


def test_descriptor_words_are_read_from_the_codes_own_line_only(session_factory):
    """Two code asks on two lines: `p trap` belongs to ZZTWC7604's line and picks its P trap
    variant as before (PR #833 item 4); ZZTWC8840 on its own line carries no descriptor and
    is left alone, even though 8840 also has a P trap bowl. On main both were narrowed."""
    _seed(
        session_factory,
        "ZZTWC8840-SC", "ZZTWCX8840-P", "ZZTWCX8840-S", "ZZTWC7604-RL", "ZZTWCX7604-P-RL-NEW",
        names={
            "ZZTWC8840-SC": "Seat cover",
            "ZZTWCX8840-P": "P trap bowl",
            "ZZTWCX8840-S": "S trap bowl",
            "ZZTWC7604-RL": "Water closet",
            "ZZTWCX7604-P-RL-NEW": "P trap bowl",
        },
    )
    query = "ZZTWC8840 price\nZZTWC7604 p trap price"
    tokens = ["ZZTWC8840", "ZZTWC7604PTRAP"]

    narrowed = narrow_code_tokens(_scoped(session_factory), query=query, tokens=tokens, allowed_types=["product", "product"])

    assert narrowed == ["ZZTWC8840", "ZZTWCX7604-P-RL-NEW"], narrowed


def test_a_descriptor_on_the_codes_own_line_still_narrows_in_a_multi_line_message(session_factory):
    """The #833 behaviour is kept where it belongs: the words beside the code, on its line,
    pick the variant; the other line is another ask."""
    # The base code is a product too (SRTWT5608 is, on prod): a bare token must never read
    # ITSELF as a typed variant (reviewer S2 on b05987f6).
    _seed(
        session_factory,
        "ZZTWC8840", "ZZTWC8840-SC", "ZZTWCX8840-P", "ZZTWCX8840-S",
        names={"ZZTWC8840-SC": "Seat cover", "ZZTWCX8840-P": "P trap bowl", "ZZTWCX8840-S": "S trap bowl"},
    )
    query = "ZZTWC8840 seat cover price\nZZTWT7408 x 5"
    tokens = ["ZZTWC8840", "seat cover", "ZZTWT7408"]

    narrowed = narrow_code_tokens(
        _scoped(session_factory), query=query, tokens=tokens, allowed_types=["product", "category", "product"]
    )

    assert narrowed == ["ZZTWC8840-SC", "ZZTWT7408"], narrowed


_8840_NAMES = {"ZZTWC8840-SC": "Seat cover", "ZZTWCX8840-P": "P trap bowl", "ZZTWCX8840-S": "S trap bowl"}


def test_a_code_typed_on_two_lines_with_two_descriptors_reads_each_line_for_its_own_token(session_factory):
    """"ZZTWC8840SEATCOVER" (the lane's fold of a code plus its words) is not a typed
    variant of ZZTWC8840, and each token's own line is the one carrying its words: the P
    trap line picks the P trap bowl, the seat cover line the seat cover, in that order."""
    _seed(session_factory, *_8840_NAMES, names=_8840_NAMES)
    query = "ZZTWC8840 p trap price\nZZTWC8840 seat cover price"
    tokens = ["ZZTWC8840PTRAP", "ZZTWC8840SEATCOVER"]

    narrowed = narrow_code_tokens(_scoped(session_factory), query=query, tokens=tokens, allowed_types=["product", "product"])

    assert narrowed == ["ZZTWCX8840-P", "ZZTWC8840-SC"], narrowed


def test_a_bare_code_and_a_described_one_on_two_lines_each_keep_their_own_ask(session_factory):
    """Reviewer case on a793e315: the bare line stays the bare code (its variant -SC is
    typed as its own token), the P trap line is still narrowed to the P trap bowl."""
    _seed(session_factory, *_8840_NAMES, names=_8840_NAMES)
    query = "ZZTWC8840 x 1\r\nZZTWC8840-SC x 2\r\nZZTWC8840 p trap"
    tokens = ["ZZTWC8840", "ZZTWC8840SC", "ZZTWC8840PTRAP"]

    narrowed = narrow_code_tokens(_scoped(session_factory), query=query, tokens=tokens, allowed_types=["product"] * 3)

    assert narrowed == ["ZZTWC8840", "ZZTWC8840SC", "ZZTWCX8840-P"], narrowed


def test_a_bare_code_does_not_read_the_line_where_the_same_code_was_typed_with_words(session_factory):
    """Reviewer S1 on b05987f6: no variant typed, so rule 5 is silent; the bare line is the
    bare ask and the P trap line belongs to the token carrying those words."""
    _seed(session_factory, *_8840_NAMES, names=_8840_NAMES)
    query = "ZZTWC8840 x 1\nZZTWC8840 p trap"
    tokens = ["ZZTWC8840", "ZZTWC8840PTRAP"]

    narrowed = narrow_code_tokens(_scoped(session_factory), query=query, tokens=tokens, allowed_types=["product", "product"])

    assert narrowed == ["ZZTWC8840", "ZZTWCX8840-P"], narrowed


def test_a_bare_code_with_its_own_category_words_is_still_picked_beside_a_described_line(session_factory):
    """Reviewer N1 on 884ef00b: rule 5 reads the family's real codes, so the described
    token ZZTWC8840PTRAP on the second line is not a typed variant; the bare token's own
    line still carries "seat cover" (the parser's category) and picks the seat cover,
    the second line picks the P trap bowl, and the category token is consumed."""
    _seed(session_factory, *_8840_NAMES, names=_8840_NAMES)
    query = "ZZTWC8840 seat cover x 1\nZZTWC8840 p trap"
    tokens = ["ZZTWC8840", "seat cover", "ZZTWC8840PTRAP"]

    narrowed = narrow_code_tokens(
        _scoped(session_factory), query=query, tokens=tokens, allowed_types=["product", "category", "product"]
    )

    assert narrowed == ["ZZTWC8840-SC", "ZZTWCX8840-P"], narrowed


def test_a_bare_only_code_typed_on_two_lines_reads_both(session_factory):
    """One token for a code typed twice, bare and with words: there is no second token to
    own the second line, so the bare token reads both and the words pick the variant."""
    _seed(session_factory, *_8840_NAMES, names=_8840_NAMES)
    query = "ZZTWC8840 x 1\nZZTWC8840 seat cover"

    narrowed = narrow_code_tokens(_scoped(session_factory), query=query, tokens=["ZZTWC8840"], allowed_types=["product"])

    assert narrowed == ["ZZTWC8840-SC"], narrowed


def test_a_token_the_parser_shortened_still_reads_its_line(session_factory):
    """Reviewer S3 on b05987f6: "ZZTWC8840 white seat cover price" typed, the parser's
    token "ZZTWC8840SEATCOVER" is not a word-for-word run of the line, so the containment
    filter finds no line and the lines holding the code word stand in (PR #833 behaviour)."""
    _seed(session_factory, *_8840_NAMES, names=_8840_NAMES)

    narrowed = narrow_code_tokens(
        _scoped(session_factory),
        query="ZZTWC8840 white seat cover price",
        tokens=["ZZTWC8840SEATCOVER"],
        allowed_types=["product"],
    )

    assert narrowed == ["ZZTWC8840-SC"], narrowed


def test_a_code_with_its_own_words_still_narrows_beside_a_typed_sibling_variant(session_factory):
    """Rule 5 is about the BARE base code only (reviewer should-fix 1 on a793e315): a token
    carrying its own descriptor is narrowed by those words even when another token is one of
    the family's codes, exactly as on main for this single line."""
    _seed(session_factory, *_8840_NAMES, names=_8840_NAMES)
    query = "ZZTWC8840 seat cover, ZZTWCX8840-P x 2"
    tokens = ["ZZTWC8840SEATCOVER", "ZZTWCX8840P"]

    narrowed = narrow_code_tokens(_scoped(session_factory), query=query, tokens=tokens, allowed_types=["product", "product"])

    assert narrowed == ["ZZTWC8840-SC", "ZZTWCX8840P"], narrowed


def test_a_variant_typed_with_a_space_narrows_to_itself(session_factory):
    """"ZZTWT5608 FRG x 5" joined by the lane into one token: the token is not the bare
    base code, so rule 5 never reads the token as its own typed variant, and its word FRG
    picks the variant it names."""
    _seed(session_factory, BASE, f"{BASE}-FRG", f"{BASE}-GM")

    narrowed = narrow_code_tokens(
        _scoped(session_factory), query=f"{BASE} FRG x 5", tokens=[f"{BASE}FRG"], allowed_types=["product"]
    )

    assert narrowed == [f"{BASE}-FRG"], narrowed


# --------------------------------------------------------------------------- #
# End to end: the real engine, the real resolver                                 #
# --------------------------------------------------------------------------- #


def test_the_owners_fifteen_line_stock_check_reaches_the_tool_with_fourteen_codes(session_factory, monkeypatch):
    """Every unique code of the owner's message reaches the tool (14 uuids: the duplicate
    ZZTWT8258-GM is one), nothing is reported not found. The TAP line is typed bare here
    (`ZZTWT5608 x 5`), the shape the crew-note reproduced on origin/main; #1511's strict
    xfail covers the same message with the TAP word once its canonical_code lands."""
    assert len(UNIQUE) == 14
    _seed(session_factory, *UNIQUE)
    _seed_contact(session_factory, variables={})
    # V1-DOC-SCOPE: an unlinked contact reads any customer's orders only as staff.
    make_office_access_type(session_factory, CONTACT_ID)
    entities = [_entity(code, code, qty) for code, qty in OWNER_LINES]

    result, captured = _run_turn(
        session_factory,
        monkeypatch,
        qf=_qf(order_status="so_outstanding", entities=entities),
        text_body=_owner_message(tap_word=False) + " sales order outstanding",
        msg_id="ZZT-narrow-15",
        attributes=["sales_orders.outstanding"],
        mcp_response={**REPORT_HIT, "product_code": UNIQUE[0]},
        real_resolver=True,
    )

    reply = (result.reply or {}).get("text") or ""
    assert _uuids_passed(captured) == 14, captured
    assert "could not find" not in reply.lower(), reply
    assert "couldn't find" not in reply.lower(), reply
    assert "not checked" not in reply.lower(), reply
