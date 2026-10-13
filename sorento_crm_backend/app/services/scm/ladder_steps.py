"""Which ladder steps the PLANNER walks (`PLAN-plan-four-options-12oct.md`).

Owner rule, 12 Oct 2026: Fulfilment Planning suggests four options only - Use own location,
Borrow (a location's), Use BRW, Buy. The other steps are DISABLED, never deleted: each sits
behind one flag here, and turning a flag back on restores the step exactly as it was.

These bind the planner's SUGGESTION only. Confirming, undoing and reading a decision that
already carries one of these kinds work as they always did.
"""
from __future__ import annotations

#: Step 1's water half: the group's own SPO landing by the line's date, composed as
#: `timely_spo` ("Use incoming").
USE_INCOMING = False

#: Step 3: a later order's SPO ("Borrow incoming").
SUPPLY_BORROW = False

#: Steps 2 and 4b name the later order they take from ("Borrow from another order") when
#: the Settings toggle is on. Off, the planner always proposes the same stock as a
#: location's Borrow, whatever the toggle says; the toggle still governs a person's pick.
ORDER_BORROW_NAMES_DONOR = False


def without_water(candidates):
    """Step 1's candidates less the water half while `USE_INCOMING` is off."""
    if USE_INCOMING or not candidates:
        return candidates
    return [candidate for candidate in candidates if not candidate.get("water")]
