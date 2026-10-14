"""SPEC-PROMO-PARAMS slice 2, B: the virtual `size` key in the parser vocabulary and grounding.

Card `documentation/plans/chatbot/CARD-spec-promo-params-12oct.md` sections 8 and 9 (slice 1 and 5).

RED today: `size` is not a registry key, so `specification_lines` renders no line for it and
`head/grounding.ground` drops a `specification` entity whose key is unknown and whose raw
("250mm") holds no key-name word. The inactive-key guards (B2) and the non-numeric guard (B4)
may already pass; they pin behaviour the build must not break.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.chatbot.test_lane_require import _seed_registry


@pytest.fixture()
def vdb(session_factory):
    db = session_factory()
    _seed_registry(db)
    db.commit()
    yield db
    db.close()


def _seat_cover_off(db) -> None:
    from app.models.product_spec import ProductSpecRegistry

    db.add(
        ProductSpecRegistry(
            id=str(uuid.uuid4()),
            spec_key="seat_cover",
            label="Seat cover",
            data_type="enum",
            allowed_values=["zzt_cover_a", "zzt_cover_b"],
            is_active=False,
        )
    )
    db.flush()


def _e(raw: str, hint: str, **extra: Any) -> dict[str, Any]:
    return {
        "raw": raw,
        "hint": hint,
        "canonical_code": extra.pop("canonical_code", None),
        "current_message": True,
        "confident": True,
        **extra,
    }


def _ground(db, *entities: dict[str, Any]) -> list[dict[str, Any]]:
    from app.services.chatbot.head.grounding import ground

    verdict, _notes = ground(db, {"entities": list(entities)})
    return verdict["entities"]


def test_b1_specification_lines_render_one_size_line(vdb):
    """RED: no line starts with 'Specification size' today."""
    from app.services.chatbot_parser_prompt import specification_lines

    size_lines = [ln for ln in specification_lines(vdb) if ln.startswith("Specification size")]
    assert len(size_lines) == 1, size_lines
    line = size_lines[0].lower()
    assert "number" in line and "mm" in line
    for axis in ("length", "width", "height", "diameter"):
        assert axis in line, f"the size line must say it is used when no {axis} word is said: {line}"


def test_b2_inactive_key_is_absent_from_the_parser_vocabulary(vdb):
    """Guard (may already pass): an inactive key reaches neither the specification block nor
    the `Product details:` values."""
    from app.services.chatbot import turn_runtime
    from app.services.chatbot_parser_prompt import specification_lines

    _seat_cover_off(vdb)
    assert not [ln for ln in specification_lines(vdb) if "seat_cover" in ln]
    assert "seat_cover" not in [k for k, _values in turn_runtime.product_detail_values(vdb)]


def test_b3_grounding_keeps_a_size_entity(vdb):
    """RED: 'size' is not in the registry and '250mm' holds no key-name word, so the
    specification entity is dropped."""
    out = _ground(
        vdb,
        _e("floor grating", "category"),
        _e("250mm", "specification", spec_key="size", spec_value=250),
    )
    sizes = [e for e in out if e.get("hint") == "specification" and e.get("spec_key") == "size"]
    assert len(sizes) == 1, out
    assert sizes[0]["spec_value"] == 250


def test_b4_size_with_a_non_numeric_value_is_not_kept_as_size(vdb):
    """Guard (passes today): 'big' is not a number in mm, so no size entity survives."""
    out = _ground(
        vdb,
        _e("floor grating", "category"),
        _e("big", "specification", spec_key="size", spec_value="big"),
    )
    assert not [e for e in out if e.get("spec_key") == "size" and e.get("spec_value") not in (None, "")], out
