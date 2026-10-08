# PLAN: DELIVERY-DEBTOR-GROUP - delivery picker groups by debtor code, not DO-level debtor name

Status: in progress (small fix track: no migration, no auth/RBAC, no new ingest surface)

## Owner's case (8 Oct 2026)

"check today delivery to hanlim" offered two options, "HANLIM TRADING SDN BHD - has DO" and
"HANLIM TRADING SDN BHD (CERAMIC & ELLECI) - has DO". The second name was typed on one DO at DO
level; the DO's debtor code is the same customer. Only one customer (group) exists, so no picker
should appear.

## Investigation

(filled in as the lane progresses)
