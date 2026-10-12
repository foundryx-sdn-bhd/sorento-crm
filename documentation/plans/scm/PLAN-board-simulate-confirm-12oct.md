# PLAN: Fulfilment Planning board, admin "Simulate confirm" (dry run)

Status: Plan (behaviour card pending owner answers). Track: standard (Size M, LEAD pattern).
Lane: SIMULATE-CONFIRM, branch `crew/simulate-confirm`.

## Owner ask (12 Oct 2026)

On the Fulfilment Planning board, add a "Simulate confirm" action under the gear button, admins
only. It runs the full Confirm for the current selection (incl. the planning-change batch /
revision path) as a dry run: one DB transaction, always rolled back; no emails, no notifications,
no AutoCount writes, no outbox rows. It shows per order and per line whether it would confirm, or
the exact error and the line it hits, plus what would be written (holds, borrows, order-backs /
stock-short warnings).

Detail follows in the behaviour card.
