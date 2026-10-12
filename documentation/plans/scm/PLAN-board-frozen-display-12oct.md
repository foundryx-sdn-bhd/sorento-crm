# PLAN: fulfilment board shows the frozen decision, not a stale batch proposal

Status: in progress. Track: small fix (hotfix, prod display bug). Lane BOARD-FROZEN-DISPLAY.

## Journey (owner, 12 Oct 2026, SO422492 on prod)

SO422492 rev4 is active (confirmed 06:42 UTC). C-FH14 lines are confirmed BUY in rev3 and rev4,
and the purchasing handover listed them as ORDER. The board shows them as Suggested Own N (BRW-NTC)
AND Decided Own N (BRW-NTC), and the header says "Revision 1 - confirmed by Balqis, 2:12 pm".

## Work items

- (a) A line whose batch row is applied shows its frozen decision, never the batch proposal.
- (b) A batch with rows left pending shows only its genuinely pending rows.
- (c) The header revision line names the active revision.

Measured cause and fix: filled in below as the lane progresses.
