# S6 deferred actions + S5 sign-in background - browser verification run log

Date: 2026-08-30. FE http://localhost:3090, BE http://localhost:8000. agent-browser session
`s6-evidence` (+ `s6-evidence-b2` for the two-browser check). Login: person38@example.com.

Test data created (all deleted by end of run except where noted):
- Products ZZTEST-S6-*, ZZTEST-S6B..S6I-* (category SAMPLE, UOM PCS) - all deleted via the
  deferred-delete UI, either by letting the window lapse or by explicit follow-up delete.
- Delivery order AC-SMOKE-DO-1 (pre-existing shared smoke fixture): a status-change countdown
  was triggered twice during Cancel-timing tests. The Cancel click did not land before the 5s
  window lapsed (test-harness CLI latency, not an app bug - see finding 4), so its status
  changed transiently to "Picked Up / In Transit" then was manually restored to "New Order" via
  the same deferred UI (closest available state; the original was blank/no status, which the UI
  has no path back to). Documented as a side effect, not left silently.
- User profile "name" field for person38@example.com was transiently changed to "Test" during a
  root-cause probe of the sign-in-background bug (multipart parsing sanity check against a
  different, working endpoint) and restored to "Jayson Personal" (confirmed via audit_logs).

System Settings > General "Delete countdown" was changed 10 -> 20 -> 10 (final state 10, verified
by reload). "Change countdown" was left at 5 throughout.

Screenshots numbered 01-28 in this folder; `debug-*.png` are working screenshots kept for
traceability, not final evidence.
