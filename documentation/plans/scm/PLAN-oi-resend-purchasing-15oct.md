# PLAN: OI-RESEND-PURCHASING - "Send to purchasing" for an Order Inquiry (15 Oct)

Status: Plan (behaviour card posted as crew-ask on the PR; awaiting owner answers). Track: LEAD, cloud lane.

## Problem
The "Order inquiry to purchasing" automation (trigger `order_inquiry_handover`) was disabled on prod
~14 Oct 19:45 to ~15 Oct 15:51 MYT. Handovers confirmed in that window sent no email and left no
automation_run / notification row, and there is no way to send the handover again by hand.

## Scope
- Single: OI detail page header Actions gets "Send to purchasing" -> preview (recipients + email) -> Send.
- Bulk: OI Documents list, tick N -> Actions "Send to purchasing" -> one preview -> Send.
- Content: the OI's current open rows rebuilt into the same handover template, subject marked "Resent".
- Recipients: the same automation's recipients (purchasing roles To, raiser, rest Cc).
- Works when the automation is disabled (preview says so; explicit send allowed).
- Duplicate guard: the preview shows when a handover email for this OI was last sent.
- Every send records a notifications row (audit). Never a stuck UI.

Details are filled in after the owner answers the behaviour card.
