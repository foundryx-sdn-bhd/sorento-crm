# PLAN: notify the OI raiser when purchasing rejects rows (OI-REJECT-NOTIFY)

Status: Plan (track: standard, additive seed migration; cloud lane)

## Journey

Purchasing rejects one or more Order Inquiry rows (single reject or batch reject with one reason).
The person who raised the OI gets ONE email and ONE in-app notification per OI per reject action,
listing every rejected line, the reason, who rejected and when, with a link to the OI.

Details, edge cases and test list follow in the next commit.
