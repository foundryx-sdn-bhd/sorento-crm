# PLAN: OI handover email - no multi-line cells, fits one printed page (OI-EMAIL-PRINT)

Status: Plan (behaviour card being written; feature track, abbreviated per the crew brief: card + mock first, then red tests, one PR)
Lane: OI-EMAIL-PRINT, branch `claude/oi-email-print-ucrsgl`
Domain: scm (order inquiry handover email, `order_inquiry_handover` automation) + email layout shell

## Problem (owner + end user, 9 Oct)

On the "ORDER, DELAY" email for SO418869 (project EUGENIA) the end user asked: "The form can
fit in one page to print out?" Owner: avoid multiple lines inside one cell; hard to read and to
print. REMARK carried an internal planning note ("Planning keeps this Buy: 140 landed for this
line on SPO-2026/09-0036 stay linked to this line; adjust the linkage if the Buy replaces
them"), and SO DATE / S/O NO repeat on every row.

(Card, measured facts and rules to follow in the next commit.)
