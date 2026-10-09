# CLAUDE-CONNECTOR browser pass (PR #1547, head 031fdda1a)

agent-browser, headless, own session, FE :3101, BE :8101 (reloaded worker started after the merge). ZZT test users only (stock template avatar, no real data). Users: ZZT Admin (no analytics.connector.use), ZZT Staff B (role ZZT Connector Role with it).

| Step | Result | Evidence |
| --- | --- | --- |
| 1 Admin without analytics.connector.use: refusal text "Your account does not have access to the Claude connector. Ask an administrator.", only Deny button (1280 + 375, scrollWidth 375) | PASS | 01-no-permission-1280.png, 02-no-permission-375.png |
| 2 Staff B: heading "as ZZT Staff B", Returns to claude.ai, Read only, countdown, Allow + Deny (1280 + 375, scrollWidth 375, buttons 58px wide) | PASS | 03-permitted-1280.png, 04-permitted-375.png |
| 3 Allow: inspect 200, consent/approve 200, form POST to https://example.invalid/connector/callback with fields request_id + assertion, action URL carries no query or token. Deny: consent/deny 200, form POST to callback with request_id + error=access_denied. (Admin Deny also posted deny 200 and navigated to the callback.) | PASS | 05-allow-posted-to-callback.png |
| 4 Used link: "This request has already been used"; expired link: "This request has expired" | PASS | 06-already-used-1280.png, 07-expired-1280.png |
| 5 Signed out: link goes to /signin?callbackUrl=/connect/claude?req=..., after sign-in returns to /connect/claude with req intact (both users) | PASS | (url log) |
| 6 Sidebar from /: Users & Access > People/Access/Market Segments/Account/Logs/Settings, Access = Roles, Permissions, AI Agents, Contact Access Types; no Claude Connector | PASS | (text read) |

Notes: refusal card still shows the "wants read-only access ... as <name>" heading above the refusal text. Unrelated toast "Failed to fetch upload activity (500)" appeared on the admin shell. Form fields in step 3 were read by intercepting submit (preventDefault) after the approve/deny call; the unintercepted Deny run navigated to the callback host.
