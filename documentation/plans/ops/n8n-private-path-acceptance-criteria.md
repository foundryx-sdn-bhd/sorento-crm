# Acceptance criteria: HA-N8N-PRIVATE-PATH

Plan: `PLAN-n8n-private-path-14oct.md`. Runbook: `documentation/runbooks/n8n-private-path.md`.

Repo (checked in review):

1. Every prod step in the runbook has a check and a rollback line.
2. No real IP, origin hostname, key or secret in any added line; placeholders only.
3. No em-dash or en-dash in any added line.
4. Every `file:line` citation resolves to what it claims.
5. The tailnet policy is mandatory before either box joins, and limits the n8n box to TCP 443,
   8765 and 6432 on the CRM box.

Owner run (on prod, by the owner):

6. From inside an n8n container, `https://<CRM_DOMAIN>/api/v1/...` answers with peer
   `<CRM_TS_IP>` and no `cf-ray` header (runbook 5.1).
7. From the n8n box, CRM ports other than 443, 8765 and 6432 are closed over the tailnet (2.4).
8. One chatbot turn on the test number answers, and the CRM nginx log shows `<N8N_TS_IP>` (5.2).
9. Failover is one `.env` line on the n8n box plus a container recreate.
