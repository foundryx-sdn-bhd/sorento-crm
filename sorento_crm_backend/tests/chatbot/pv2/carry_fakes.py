"""Test doubles and builders for the PV2-CARRY tests (pure, no DB, no LLM).

`FakeResolver` implements `ResolverLike` (apply.py) from a plain dict, the same shape the
JSONL tables carry in their `resolver` field:

    {"entities": {"<kind>|<key or phrase>": RESULT},
     "specs": {"<attribute or ''>|<value_key or number>": RESULT},
     "attachment_types": {"<key>": RESULT}}
    RESULT = {"status": "exact"|"ambiguous"|"none",
              "refs": [{"kind","key","label"}],          # entities / attachment types
              "specs": [ResolvedSpec dicts],             # specs
              "options": [{"key","label","payload"}]}

Anything not configured returns status "none" with nothing in it, so a test cannot pass by
accident. Every lookup is recorded in `.calls` as `(method, lookup_key)`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.chatbot.pv2.schema import (
    Ask,
    Clarify,
    Entity,
    Escalation,
    ParserV2Output,
    Spec,
)
from app.services.chatbot.pv2.state import (
    ConversationState,
    Held,
    Option,
    PendingQuestion,
    ResolvedRef,
    ResolvedSpec,
)

NOW = datetime(2026, 10, 13, 2, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Resolver double
# --------------------------------------------------------------------------- #


def _num(value: Any) -> str:
    """600.0 -> "600" so a table key reads the way a human writes it."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def entity_lookup_key(kind: str, key: str | None, phrase: str | None) -> str:
    return f"{kind}|{key if key is not None else phrase}"


def spec_lookup_key(attribute: str | None, value_key: str | None, number: float | None) -> str:
    tail = value_key if value_key is not None else (_num(number) if number is not None else None)
    return f"{attribute or ''}|{tail}"


@dataclass
class FakeResolution:
    status: str = "none"
    refs: list[ResolvedRef] = field(default_factory=list)
    specs: list[ResolvedSpec] = field(default_factory=list)
    options: list[Option] = field(default_factory=list)
    phrase: str | None = None
    unrecognised: bool = False


class FakeResolver:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.calls: list[tuple[str, str]] = []

    # -- ResolverLike ------------------------------------------------------ #

    def resolve_entities(self, ents: list[Entity], *, held: Held) -> list[FakeResolution]:
        table = self.config.get("entities", {})
        out = []
        for e in ents:
            k = entity_lookup_key(e.kind, e.key, e.phrase)
            self.calls.append(("entity", k))
            out.append(self._build(table.get(k), e.phrase))
        return out

    def resolve_specs(self, specs: list[Spec], *, held: Held) -> list[FakeResolution]:
        table = self.config.get("specs", {})
        out = []
        for s in specs:
            k = spec_lookup_key(s.attribute, s.value_key, s.number)
            self.calls.append(("spec", k))
            out.append(self._build(table.get(k), s.phrase))
        return out

    def resolve_attachment_type(self, key: str | None, *, held: Held) -> FakeResolution:
        self.calls.append(("attachment_type", str(key)))
        return self._build(self.config.get("attachment_types", {}).get(str(key)), None)

    # -- helpers ----------------------------------------------------------- #

    @property
    def entity_lookups(self) -> list[str]:
        return [k for m, k in self.calls if m == "entity"]

    @staticmethod
    def _build(cfg: dict[str, Any] | None, phrase: str | None) -> FakeResolution:
        if not cfg:
            return FakeResolution(status="none", phrase=phrase)
        return FakeResolution(
            status=cfg.get("status", "none"),
            refs=[ResolvedRef(phrase=phrase, **r) for r in cfg.get("refs", [])],
            specs=[ResolvedSpec(**s) for s in cfg.get("specs", [])],
            options=[Option(**o) for o in cfg.get("options", [])],
            phrase=phrase,
            unrecognised=bool(cfg.get("unrecognised", False)),
        )


# --------------------------------------------------------------------------- #
# Parser output builders: every field defaults to "said nothing"
# --------------------------------------------------------------------------- #


def ask(**overrides: Any) -> Ask:
    base: dict[str, Any] = dict(
        act="new_topic",
        domain=None,
        switch_back_to=None,
        entities=[],
        specs=[],
        attachment_type=None,
        period=None,
        row_pick=None,
        answer=None,
        cleared=[],
        report=None,
        low_stock=None,
        ideation=None,
    )
    base.update(overrides)
    return Ask(**base)


def entity(
    kind: str,
    key: str | None = None,
    phrase: str | None = None,
    op: str = "set",
    refers_to_held: bool = False,
) -> Entity:
    return Entity(kind=kind, key=key, phrase=phrase, op=op, refers_to_held=refers_to_held)


def spec(
    attribute: str | None = None,
    value_key: str | None = None,
    number: float | None = None,
    number_to: float | None = None,
    unit: str | None = None,
    comparator: str = "eq",
    op: str = "set",
    phrase: str | None = None,
) -> Spec:
    return Spec(
        attribute=attribute,
        value_key=value_key,
        number=number,
        number_to=number_to,
        unit=unit,
        comparator=comparator,
        op=op,
        phrase=phrase,
    )


def out(
    *asks: Ask,
    clarify: Clarify | dict[str, Any] | None = None,
    language: str = "en",
    escalation: Escalation | dict[str, Any] | None = None,
) -> ParserV2Output:
    if clarify is None:
        clarify = Clarify(needed=False, phrase=None, about=None)
    elif isinstance(clarify, dict):
        clarify = Clarify(**clarify)
    if isinstance(escalation, dict):
        escalation = Escalation(**escalation)
    return ParserV2Output(asks=list(asks), clarify=clarify, language=language, escalation=escalation)


# --------------------------------------------------------------------------- #
# State builders
# --------------------------------------------------------------------------- #


def held(
    domain: str | None = None,
    refs: dict[str, list[str]] | None = None,
    specs: list[tuple[str, Any]] | None = None,
    attachment_type: str | None = None,
    **extra: Any,
) -> Held:
    """`refs` maps kind -> list of keys; the label is the key."""
    return Held(
        domain=domain,
        refs={k: [ResolvedRef(kind=k, key=v, label=v) for v in vs] for k, vs in (refs or {}).items()},
        specs=[ResolvedSpec(attribute=a, comparator="eq", value=v) for a, v in (specs or [])],
        attachment_type=attachment_type,
        **extra,
    )


def option(key: str, label: str, payload: dict[str, Any] | None = None) -> Option:
    return Option(key=key, label=label, payload=payload or {})


def pending(
    kind: str,
    options: list[Option],
    asked: str = "Which one do you mean?",
    misses: int = 0,
    asked_turn: int = 1,
) -> PendingQuestion:
    return PendingQuestion(kind=kind, asked=asked, options=options, asked_turn=asked_turn, misses=misses)


def mk_state(
    held_: Held | None = None,
    *,
    pending_: PendingQuestion | None = None,
    topics: list[Held] | None = None,
    last_rows: list[Option] | None = None,
    turn_no: int = 3,
    last_turn_at: datetime | None = None,
    now: datetime = NOW,
) -> ConversationState:
    return ConversationState(
        session_started_at=now - timedelta(minutes=30),
        last_turn_at=last_turn_at or now - timedelta(minutes=1),
        turn_no=turn_no,
        held=held_ or Held(),
        pending=pending_,
        topics=topics or [],
        last_rows=last_rows or [],
    )
