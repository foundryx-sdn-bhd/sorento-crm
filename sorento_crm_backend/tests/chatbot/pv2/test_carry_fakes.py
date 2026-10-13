"""The doubles themselves: an unconfigured lookup must never look like a hit."""
from __future__ import annotations

from tests.chatbot.pv2.carry_fakes import FakeResolver, ask, entity, held, out, spec
from app.services.chatbot.pv2.state import Held


def test_unconfigured_is_none_and_recorded():
    r = FakeResolver()
    [res] = r.resolve_entities([entity("product", phrase="X")], held=Held())
    assert (res.status, res.refs, res.options) == ("none", [], [])
    assert r.calls == [("entity", "product|X")]


def test_key_beats_phrase_and_numbers_format():
    r = FakeResolver({"entities": {"brand|SORENTO": {"status": "exact", "refs": [{"kind": "brand", "key": "SORENTO", "label": "Sorento"}]}},
                      "specs": {"|600": {"status": "exact", "specs": [{"attribute": "dim_length", "comparator": "eq", "value": 600}]}}})
    [e] = r.resolve_entities([entity("brand", key="SORENTO", phrase="sorento")], held=Held())
    assert e.status == "exact" and e.refs[0].label == "Sorento"
    [s] = r.resolve_specs([spec(number=600.0)], held=Held())
    assert s.specs[0].attribute == "dim_length"


def test_builders_default_to_said_nothing():
    a = ask()
    assert a.entities == [] and a.specs == [] and a.answer is None and a.cleared == []
    o = out(a)
    assert o.clarify.needed is False and o.escalation is None and o.language == "en"
    assert held("x", {"product": ["A"]}).refs["product"][0].key == "A"
