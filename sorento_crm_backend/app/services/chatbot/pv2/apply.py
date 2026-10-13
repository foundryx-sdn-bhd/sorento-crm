"""PV2 apply_turn (CONTRACT-PV2-13oct.md section 3): ONE function for every domain.

Pure except through `resolver`. Code decides from structured fields only: no word or
fuzzy matching on the user's text (option keys and T-keys are compared EXACTLY; phrases
go to the resolver, which matches against data).
"""
from __future__ import annotations

from typing import Any, Literal, Protocol

from app.services.chatbot.pv2.schema import Entity, ParserV2Output, Spec
from app.services.chatbot.pv2.state import Action, ConversationState, Held

#: The 11 Oct rule (carried + new subject -> which_held): on these domains one subject is
#: answered at a time, so a new entity of a held kind with no `refers_to_held` and op
#: `add` asks which one is meant instead of answering both.
SINGLE_SUBJECT_DOMAINS: frozenset[str] = frozenset({"product_attachment"})


class ResolverLike(Protocol):
    """The subset of PV2-RESOLVE's `Resolver` (section 4) apply_turn calls. Resolution /
    SpecResolution are read by attribute: `status` ("exact" | "ambiguous" | "none"),
    `refs` / `specs`, `options`, `phrase`."""

    def resolve_entities(self, ents: list[Entity], *, held: Held) -> list[Any]: ...

    def resolve_specs(self, specs: list[Spec], *, held: Held) -> list[Any]: ...

    def resolve_attachment_type(self, key: str | None, *, held: Held) -> Any: ...


class ClarifyPolicy:
    """Ask-or-default rows (PR #1570 shape). Tonight a constant table whose default is
    ask: a single candidate is still asked unless a row says default."""

    def __init__(self, rows: dict[tuple[str, str | None], Literal["ask", "default"]] | None = None):
        self.rows = dict(rows or {})

    def decide(self, about: str, domain: str | None) -> Literal["ask", "default"]:
        return self.rows.get((about, domain)) or self.rows.get((about, None)) or "ask"


DEFAULT_POLICY = ClarifyPolicy()


def apply_turn(
    state: ConversationState,
    out: ParserV2Output,
    *,
    resolver: ResolverLike,
    policy: ClarifyPolicy = DEFAULT_POLICY,
) -> tuple[ConversationState, list[Action]]:
    raise NotImplementedError
