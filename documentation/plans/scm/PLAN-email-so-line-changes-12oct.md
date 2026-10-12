# PLAN: Order inquiry email - remove REMARK, derive Was -> Now from the SO line change

Status: Plan (lane EMAIL-SO-CHANGES, size M). Behaviour card ask pending before code.

## Owner rules (12 Oct 2026)

1. Remove the REMARK column from the order inquiry handover email entirely.
2. QTY and DELIVERY DATE moves must always print Was -> Now, derived from the actual change on
   the SO line (the same source the app "What changed" dialog uses), not from the remark/action
   label. Real miss: SO400236 line SRTWB246 BRW-BB qty 1, dialog Was 01/10/2026 Now 02/11/2026,
   email printed only DELIVERY DATE 02/11/2026 with an empty CHANGE TO cell.

## Findings

(filled in as the trace lands)
