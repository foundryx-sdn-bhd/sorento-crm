# PLAN - Request CS to reserve on an OI line linked to a received SPO

Status: PLAN, behaviour card being filed to crew (15 Oct 2026). Track: M (new rule on an existing
screen, no migration expected). Lane OI-RESERVE-RECEIVED, branch `crew/oi-reserve-received` off
origin/main 504ce9252. UAC: `oi-reserve-received-acceptance-criteria.md` (written after the card
answers).
Domain: SCM, order inquiries. Builds on the shipped "Request CS to reserve" flow
(`documentation/plans/_archive/scm/PLAN-oi-request-cs-reserve.md`).

## The problem

Owner, 15 Oct 2026: an SPO line already received into the warehouse, whose stock went to other
sales orders because the schedule moved, still links an OI line in full (SRT6638, SO qty 1260,
Taken 1260, Remaining 0, PO 202511-S0082 via SPO-2026/01-0005, BRW-IR). The pool location now has
plenty of free stock, so purchasing wants CS to reserve it for this SO line, but "Request CS to
reserve" is disabled because the line is linked.

## Owner rulings (15 Oct ~16:00, final)

1. Only rows whose links point to FULLY RECEIVED SPO/PO lines. Open/incoming links: no change.
2. CS accepts (full or partial): release the WHOLE received link; the reserve covers the line;
   whatever CS could not reserve goes back to Remaining. CS declines (0): nothing changes.
3. The released received SPO must never become free / linkable for other SOs (or this row again).
4. A committed reserve does not hold stock in the CRM. No change to Stock Debt / board holding.
   Stock-transfer push to AutoCount is out of scope.

## Design

To be filled after the behaviour card answers (request gate, commit release, re-link guard).

## Slices

To be filled.
