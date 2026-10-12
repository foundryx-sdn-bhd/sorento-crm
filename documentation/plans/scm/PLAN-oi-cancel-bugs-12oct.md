# PLAN: OI cancel bugs from SO422492 (12 Oct)

Status: in progress (small fix track: bug fixes, red-first, no migration)

Prod incident SO422492. Three defects in the AutoCount planning change -> OI netting path:

- BUG A: confirming any line un-plans every line with an undecided planning change, and the
  OI netting then emails CANCEL ALL for qty the book still needs.
- BUG B: a NEW SO line whose product AutoCount changes again loses its planning change row
  (retired with no replacement), so the moved qty is never re-ordered.
- BUG C: a pure AutoCount split (same product, same total qty) raises a CANCEL + re-order pair.

Approach: red tests seeded with the exact SO422492 shapes, then the minimal fix for each.
Findings with file:line are recorded in the PR body.
