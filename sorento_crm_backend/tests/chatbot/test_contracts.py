"""AC-109 (H28): every enum the turn engine speaks is declared ONCE, in contracts.py.

H28 is enum drift: n8n declared `branch_kind` in the router, again in the Switch, again
in `escalate-catalog`, and the three lists stopped agreeing. The port's answer is a single
`Literal` per vocabulary plus this test, which greps the package for a second copy of any
of those string sets. A duplicated set fails and names the file.

The scan is deliberately dumb - it looks for the literal strings, not for a `Literal[...]`
construct - because a copy hand-written as a `set`, a `frozenset`, a tuple or a match
statement is exactly as much drift as a second `Literal`.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.services.chatbot import contracts
from app.services.chatbot.lanes.escalation import ESCALATION_TEAMS

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = BACKEND_ROOT / "app" / "services" / "chatbot"
CONTRACTS_FILE = PACKAGE / "contracts.py"
# AC-1594: SUGGESTED_TEAMS moved out of contracts.py to lanes/escalation.py -
# escalation-lane vocabulary, not domain data (that file's own comment explains why).
# Its OWN canonical declaration must not be flagged as a duplicate of itself.
ESCALATION_FILE = PACKAGE / "lanes" / "escalation.py"

# The vocabularies AC-109 names, plus the ones the engine itself owns.
VOCABULARIES = {
    "MESSAGE_TYPES": contracts.MESSAGE_TYPES,
    "INTENT_HINTS": contracts.INTENT_HINTS,
    "DOMAIN_HINTS": contracts.DOMAIN_HINTS,
    "SUGGESTED_TEAMS": ESCALATION_TEAMS,
    "SUGGESTED_AGENTS": contracts.SUGGESTED_AGENTS,
    "ENTITY_HINTS": contracts.ENTITY_HINTS,
    "SELECTION_CONTEXTS": contracts.SELECTION_CONTEXTS,
    "BRANCH_KINDS": contracts.BRANCH_KINDS,
    "TURN_STAGES": contracts.TURN_STAGES,
    "TURN_STATUSES": contracts.TURN_STATUSES,
    "ACTION_KINDS": contracts.ACTION_KINDS,
    "INGRESS_KINDS": contracts.INGRESS_KINDS,
}


def test_every_vocabulary_is_non_empty_and_unique() -> None:
    for name, values in VOCABULARIES.items():
        assert values, f"{name} is empty"
        assert len(set(values)) == len(values), f"{name} has a duplicate member"


def test_branch_kinds_are_the_fourteen_the_router_decides() -> None:
    assert set(contracts.BRANCH_KINDS) == {
        "access_denied",
        "escalate_offer",
        "out_of_scope",
        "ideate",
        "offer_hold",
        "escalation_declined",
        "check_promotion",
        "low_signal",
        "clarify_menu",
        "not_supported",
        "stock_denied",
        "demand_qty",
        "business_query",
        # PLAN-chatbot-media-into-turn.md, S2: a media turn that is denied, failed,
        # or outlived its sync wait closes here - no parser call, no LLM.
        "media_denied",
    }


def test_tag_only_branch_kinds_are_a_subset_of_branch_kinds() -> None:
    assert contracts.TAG_ONLY_BRANCH_KINDS <= set(contracts.BRANCH_KINDS)


def _package_sources() -> list[Path]:
    return [p for p in PACKAGE.rglob("*.py") if p not in (CONTRACTS_FILE, ESCALATION_FILE)]


def test_no_second_copy_of_any_vocabulary_lives_in_the_package() -> None:
    """A module that RE-ENUMERATES a whole vocabulary is a second source of truth (H28).

    "Re-enumerates" means a set / list / tuple literal of plain strings whose members
    cover a whole vocabulary. Two things are deliberately NOT flagged, because neither is
    a second declaration:

    * a decision ladder naming each member in its own comparison (`route.py` must say
      `access_denied` somewhere - that is the code, not a copy of the list);
    * a MAPPING keyed by a vocabulary (`DOMAIN_BLOCKED_HINTS`, `AXIS_BY_DOMAIN`) - a
      per-member table is the point, and its keys drifting from the enum is what the
      other tests in this file are for.

    A module that legitimately needs a wider or narrower set derives it from the tuple in
    `contracts.py` (`frozenset(ENTITY_HINTS) | {...}`), which is exactly what this
    forces.
    """
    offenders: list[str] = []
    for path in _package_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                continue
            members = {
                el.value
                for el in node.elts
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            }
            if not members:
                continue
            for name, values in VOCABULARIES.items():
                if len(values) >= 3 and set(values) <= members:
                    offenders.append(
                        f"{path.relative_to(BACKEND_ROOT).as_posix()}:{node.lineno} "
                        f"re-enumerates {name}"
                    )
    assert not offenders, (
        "duplicated enum vocabularies (H28) - derive from the tuple in contracts.py: "
        + "; ".join(sorted(set(offenders)))
    )


def test_contracts_is_the_only_module_declaring_a_literal_of_these_names() -> None:
    declared = {
        path.relative_to(BACKEND_ROOT).as_posix()
        for path in _package_sources()
        if re.search(r"^\s*[A-Z_]+\s*=\s*Literal\[", path.read_text(encoding="utf-8"), re.M)
    }
    assert not declared, (
        "a Literal vocabulary is declared outside contracts.py: " + ", ".join(sorted(declared))
    )


class TestEnvelopeContactIdWireShape:
    """The respond.io contact id is a NUMBER in the webhook body, and the Envelope
    keeps it that way.

    Pinned after #874: the lane fed `ctx.contact.id` into a `contact_id: str`
    schema field raw and every `business_query` turn in production threw
    `contact_id  Input should be a valid string ... input_type=int`. The repair
    stringifies at the two seams that need a string (the resolve body, and the
    text columns the engine writes), NOT here - stringifying on the way in would
    hide the wire shape from every test again and re-open exactly this hole.
    """

    @staticmethod
    def _payload(contact_id: Any) -> dict[str, Any]:
        return {
            "contact": {"id": contact_id, "firstName": "ZZT", "custom_fields": []},
            "message": {
                "event_type": "message.received",
                "contact": {"id": contact_id},
                "message": {
                    "messageId": "ZZT-msg-wire-1",
                    "contactId": contact_id,
                    "channelId": "whatsapp",
                    "traffic": "incoming",
                    "message": {"type": "text", "text": "price for SRTWC8517"},
                },
            },
        }

    def test_an_integer_contact_id_is_accepted_and_stays_an_integer(self) -> None:
        envelope = contracts.Envelope(**self._payload(900000008))

        assert envelope.contact["id"] == 900000008
        assert isinstance(envelope.contact["id"], int)

    def test_a_string_contact_id_is_still_accepted_unchanged(self) -> None:
        envelope = contracts.Envelope(**self._payload("ZZT-contact-1"))

        assert envelope.contact["id"] == "ZZT-contact-1"

    def test_a_missing_or_empty_contact_id_is_still_refused(self) -> None:
        for missing in (None, ""):
            with pytest.raises(ValidationError) as excinfo:
                contracts.Envelope(**self._payload(missing))
            assert "contact.id is required" in str(excinfo.value)
