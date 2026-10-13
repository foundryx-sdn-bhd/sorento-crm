# CLAUDE-CONNECTOR browser pass (PR #1547, head d218708a3)

agent-browser, headless, own session, FE :3101, BE :8101 (uvicorn reloaded at 23:54, after the d218708a3 commit at 23:52). ZZT test users only (stock template avatar, no real data). Users: ZZT Admin (no analytics.connector.use), ZZT Staff B (role ZZT Connector Role with it).

| Step | Result | Evidence |
| --- | --- | --- |
| 1 Admin without analytics.connector.use: refusal text "Your account does not have access to the Claude connector. Ask an administrator.", only Deny button (1280 + 375, scrollWidth 1280 / 375) | PASS | 01-no-permission-1280.png, 02-no-permission-375.png |
| 2 Staff B: heading "as ZZT Staff B", Returns to claude.ai, Read only, Allow + Deny (1280 + 375, scrollWidth 1280 / 375, buttons 58px wide) | PASS | 03-permitted-1280.png, 04-permitted-375.png |
| 3 Allow: consent/approve 200, form POST to https://example.invalid/connector/callback with fields request_id + assertion, action URL carries no query or token. Deny: consent/deny 200, form POST with request_id + error=access_denied. | PASS | 05-allow-posted-to-callback.png |
| 4 Used link: "This request has already been used"; expired link: "This request has expired" | PASS | 06-already-used-1280.png, 07-expired-1280.png |
| 5 Signed out: link goes to /signin?callbackUrl=/connect/claude?req=..., after sign-in returns to /connect/claude with the same req (both users) | PASS | (url log) |
| 6 Admin sidebar, Users & Access: People, Access, Market Segments, Account, Logs, Settings; page text has no "Claude Connector" | PASS | (text read) |
| 7 With the consent page open as ZZT Admin, clicking Dashboards in the sidebar loaded the staff Dashboards page (middleware order change) | PASS | (snapshot) |

Notes: form fields in step 3 were read by intercepting form submit (preventDefault) after the approve/deny call, so the browser did not leave for the callback host. Unrelated: GET /api/v1/resource-management/upload-activity returns 500 on the shell (pre-existing, seen in the last pass).
