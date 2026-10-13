"""PV2-CORE: the parser v2 output schema (CONTRACT-PV2-13oct.md section 1)."""
from __future__ import annotations

import json

import pytest

from app.services.chatbot.pv2 import schema
from app.services.chatbot.pv2.schema import IndexKeys, SchemaError, validate
from tests.chatbot.pv2.conftest import minimal_ask, minimal_output

KEYS = IndexKeys(
    domains=frozenset({"inventory", "product_attachment", "sales"}),
    entity_kinds=frozenset({"product", "customer", "brand", "product_type"}),
    entity_keys={"brand": frozenset({"SORENTO", "MOCHA"}), "product_type": frozenset({"water_closet"})},
    attachment_types=frozenset({"technical_specifications", "product_photos"}),
    spec_attributes=frozenset({"dim_length", "trap_type"}),
    spec_values={"trap_type": frozenset({"p_trap", "s_trap"})},
    report_datasets=frozenset({"sales"}),
    report_measures=frozenset({"amount", "qty"}),
    report_dimensions=frozenset({"customer", "brand", "salesman"}),
    report_bases=frozenset({"delivered", "ordered"}),
    teams=frozenset({"sales_support"}),
)


def _walk(node, path="$"):
    yield path, node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def test_strict_schema_every_object_closed_and_fully_required():
    for path, node in _walk(schema.PV2_JSON_SCHEMA):
        if isinstance(node, dict) and node.get("type") == "object":
            assert node.get("additionalProperties") is False, path
            assert sorted(node["required"]) == sorted(node["properties"]), path


def test_strict_schema_has_no_refs_defaults_or_titles():
    text = json.dumps(schema.PV2_JSON_SCHEMA)
    assert "$ref" not in text and "$defs" not in text
    for path, node in _walk(schema.PV2_JSON_SCHEMA):
        if isinstance(node, dict):
            assert "default" not in node or path.endswith(".properties"), path
            assert "format" not in node, path


def test_top_level_keys_and_ask_keys_match_contract():
    props = schema.PV2_JSON_SCHEMA["properties"]
    assert list(props) == ["asks", "clarify", "language", "escalation"]
    ask = props["asks"]["items"]["properties"]
    assert list(ask) == [
        "act", "domain", "switch_back_to", "entities", "specs", "attachment_type", "period",
        "row_pick", "answer", "cleared", "report", "low_stock", "ideation",
    ]


def test_turn_act_is_the_one_enum():
    assert schema.TURN_ACTS == (
        "answers_bot_question", "refines_held", "new_topic", "switch_back", "small_talk", "unclear",
    )
    act = schema.PV2_JSON_SCHEMA["properties"]["asks"]["items"]["properties"]["act"]
    assert act["enum"] == list(schema.TURN_ACTS)


def test_validate_accepts_minimal():
    out, dropped = validate(minimal_output(), KEYS)
    assert out.asks[0].act == "new_topic" and dropped == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o.update(extra=1),
        lambda o: o["asks"][0].update(entity_op="set"),
        lambda o: o["asks"][0].update(act="continuation"),
        lambda o: o["asks"][0].pop("cleared"),
        lambda o: o.update(asks=[]),
        lambda o: o["asks"][0].update(cleared=["brand"]),
    ],
)
def test_validate_rejects_bad_shape(mutate):
    raw = minimal_output()
    mutate(raw)
    with pytest.raises(SchemaError):
        validate(raw, KEYS)


def test_validate_nulls_keys_outside_pinned_index_and_reports_them():
    raw = minimal_output(
        minimal_ask(
            domain="warranty",
            attachment_type="cad_drawing",
            entities=[
                {"kind": "brand", "key": "SORENTOO", "phrase": "sorentoo", "op": "set", "refers_to_held": False},
                {"kind": "brand", "key": "MOCHA", "phrase": "mocha", "op": "add", "refers_to_held": False},
                {"kind": "product", "key": None, "phrase": "SRTWC286", "op": "set", "refers_to_held": False},
            ],
            specs=[
                {"attribute": "dim_width", "value_key": None, "number": 600, "number_to": None,
                 "unit": "mm", "comparator": "eq", "op": "set", "phrase": "600mm"},
                {"attribute": "trap_type", "value_key": "x_trap", "number": None, "number_to": None,
                 "unit": None, "comparator": "eq", "op": "set", "phrase": "x trap"},
            ],
        ),
        escalation={"requested": True, "team_key": "nope"},
    )
    out, dropped = validate(raw, KEYS)
    ask = out.asks[0]
    assert ask.domain is None and ask.attachment_type is None
    assert [e.key for e in ask.entities] == [None, "MOCHA", None]
    assert ask.entities[2].phrase == "SRTWC286"  # non-index kind: phrase untouched
    assert ask.specs[0].attribute is None and ask.specs[0].number == 600
    assert ask.specs[1].attribute == "trap_type" and ask.specs[1].value_key is None
    assert out.escalation.team_key is None
    assert sorted((d.kind, d.value) for d in dropped) == sorted([
        ("domain", "warranty"), ("attachment_type", "cad_drawing"), ("brand", "SORENTOO"),
        ("spec_attribute", "dim_width"), ("spec_value", "x_trap"), ("team", "nope"),
    ])
    assert next(d for d in dropped if d.value == "SORENTOO").phrase == "sorentoo"


def test_validate_report_keys_and_ratio():
    report = {
        "dataset": "sales", "measures": ["amount", "amount/qty", "margin"], "group_by": ["customer", "region"],
        "top_n_per_group": None,
        "filters": [{"dimension": "brand", "op": "in", "keys": ["SORENTO"], "values": []},
                    {"dimension": "state", "op": "in", "keys": ["johor"], "values": []}],
        "period": {"preset": "last_month", "n": None, "date_from": None, "date_to": None},
        "compare_to": None, "compare_period": None, "grain": None, "threshold": None,
        "sort": "desc", "top_n": 10, "basis": "invoiced",
    }
    out, dropped = validate(minimal_output(minimal_ask(domain="sales", report=report)), KEYS)
    r = out.asks[0].report
    assert r.measures == ["amount", "amount/qty"]
    assert r.group_by == ["customer"]
    assert [f.dimension for f in r.filters] == ["brand"]
    assert r.basis is None
    assert {d.value for d in dropped} == {"margin", "region", "state", "invoiced"}


def test_period_dates_parse():
    raw = minimal_output(minimal_ask(period={"preset": None, "n": None, "date_from": "2026-10-01", "date_to": "2026-10-13"}))
    out, _ = validate(raw, KEYS)
    assert str(out.asks[0].period.date_to) == "2026-10-13"


def test_validate_drops_unknown_kinds_and_model_made_keys_on_lookup_kinds():
    raw = minimal_output(minimal_ask(entities=[
        {"kind": "brands", "key": "SORENTO", "phrase": "sorento", "op": "set", "refers_to_held": False},
        {"kind": "product", "key": "SRTWC9999", "phrase": "srtwc 286", "op": "set", "refers_to_held": False},
        {"kind": "customer", "key": None, "phrase": "Hanlim", "op": "set", "refers_to_held": False},
    ]))
    out, dropped = validate(raw, KEYS)
    ents = out.asks[0].entities
    assert [(e.kind, e.key, e.phrase) for e in ents] == [("product", None, "srtwc 286"), ("customer", None, "Hanlim")]
    assert {(d.kind, d.value) for d in dropped} == {("entity_kind", "brands"), ("product", "SRTWC9999")}
