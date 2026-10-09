# CLAUDE-CONNECTOR browser pass (PR #1547, cea0447ca)

agent-browser, headless, own session, FE :3101, BE :8101. ZZT test users only (stock template avatar, no real data).

| Step | Result | Evidence |
| --- | --- | --- |
| 1 Admin without analytics.connector.use: refusal card, no Allow, only Deny (1280 + 375, no sideways scroll) | PASS | 01-no-permission-1280.png, 02-no-permission-375.png |
| 2 Permission granted through Roles UI (PUT roles/<id> 200), user with it: heading "as ZZT Staff B", Returns to claude.ai, Read only, countdown, Allow + Deny (1280 + 375, scrollWidth 375, buttons 41-165px) | PASS | 03-permitted-1280.png, 04-permitted-375.png |
| 3 Allow: POST consent/approve 200, then Document POST https://example.invalid/connector/callback, landing chrome-error with no token in URL. Deny: consent/deny 200, form action POST callback, fields request_id + error=access_denied | PASS | 05-allow-posted-to-callback.png |
| 4 Used link: "This request has already been used"; expired link: "This request has expired" | PASS | 06-already-used-1280.png, 07-expired-1280.png |
| 5 Signed out: link goes to /signin?callbackUrl=/connect/claude?req=..., after sign-in returns to /connect/claude with req intact | PASS | (url log) |
| 6 Sidebar from /: Users & Access > People/Access/Market Segments/Account/Logs/Settings, Access = Roles, Permissions, AI Agents, Contact Access Types; no Claude Connector | PASS | (text read) |

Notes: refusal card still shows the "wants read-only access ... as <name>" heading above the refusal text. Roles dialog logs a pre-existing Radix DialogContent description warning.
