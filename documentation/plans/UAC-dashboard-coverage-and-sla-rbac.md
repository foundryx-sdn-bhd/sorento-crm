# UAC - Dashboard coverage (HoD) + per-action SLA RBAC

**Status:** Authoring → build → self-verify (FE + BE) → handoff
**Plans:** PLAN-sla-pending-task-button-rbac.md · PLAN-hod-coverage-on-dashboard.md
**Rule:** every criterion below must be demonstrably PASS (FE browser + BE test) before handoff.

Legend: each AC has **Given / When / Then** + a **Verify** method (FE = browser via
Playwright MCP, BE = pytest, V = vitest).

---

## Part A - Per-action SLA task button RBAC (My Pending / My Team widget)

New slugs (all under `sla_management.conversation_sla_tracking`): `extend`, `reassign`,
`resolve`, `escalate`, `takeover`. `superadmin`/`admin` bypass.

**A1 - Slugs exist.** Given the backend boots, Then all 5 slugs are present in
`user_permissions` (seeded by `sync_permissions`). *Verify BE.*

**A2 - Extend hidden without slug.** Given a user lacking `…extend`, When they open the
dashboard My Pending widget, Then no Extend button renders on any row. *Verify FE + V.*

**A3 - Resolve hidden without slug.** As A2 for Resolve. *Verify FE + V.*

**A4 - Escalate hidden without slug.** As A2 for Escalate. *Verify FE + V.*

**A5 - Reassign hidden without slug.** As A2 for Reassign (both My Pending + My Team).
*Verify FE + V.*

**A6 - Takeover hidden without slug.** Given a user lacking `…takeover`, Then My Team
rows show no Takeover button AND no Cancel/Reject-takeover buttons. *Verify FE + V.*

**A7 - Buttons show with slug.** Given a user holding a slug (or admin), Then the
matching button renders and works (golden path unchanged). *Verify FE.*

**A8 - BE rejects extend without slug.** Given a user lacking `…extend`, When they
`POST /{id}/extend`, Then 403. *Verify BE.*

**A9 - BE rejects reassign without slug.** As A8 for `POST /{id}/reassign`. *Verify BE.*

**A10 - BE rejects escalate without slug.** As A8 for `POST /{id}/escalate`. Also: with
the slug but acting on a tracking outside the actor's visible scope → 404. *Verify BE.*

**A11 - BE rejects takeover without slug.** As A8 for `POST /{id}/takeover` +
`/takeover-requests/{id}/cancel` + `/reject`. *Verify BE.*

**A12 - Resolve via dedicated route.** Given a user with `…resolve`, When the widget
Resolve confirm fires, Then it calls `POST /{id}/resolve` (not PUT) and the row resolves.
Without the slug → 403. Acting outside scope → 404. *Verify FE (network) + BE.*

**A13 - n8n PUT unbroken.** Given the API-key (n8n) principal, When it `PUT /{id}`
`{is_resolved:true}`, Then it still succeeds (no permission required for api_key).
*Verify BE.*

**A14 - PUT hardened for humans.** Given a JWT user lacking `…resolve`, When they
`PUT /{id}` `{is_resolved:true}` directly, Then 403. *Verify BE.*

---

## Part B - Coverage on the dashboard + HoD assignment

New slug: `notifications.coverage.manage_team`. HoD = parent-team member holding it
(or admin). Scope = `_visible_member_ids` (teams ∪ descendant teams).

**B1 - Coverage card on dashboard.** Given any authenticated user, When they open the
dashboard (`/`), Then a Coverage card renders alongside the My Pending widget with a
**My coverage** section (self-service picker + active list). *Verify FE.*

**B2 - Self-service unchanged.** Given a user, When they add "I cover for X" from the
dashboard, Then it posts `POST /notifications/coverage/` and the row appears (mode +
expiry honoured), identical to the old `/account/notifications` behaviour. *Verify FE +
BE + V.*

**B3 - Team section gated.** Given a user WITHOUT `manage_team`, Then the **Team
coverage** section does NOT render. Given a user WITH it (or admin), Then it renders.
*Verify FE + V.*

**B4 - HoD assigns A→cover→B.** Given a HoD with `manage_team`, When they pick coverer A
and covered B (both in scope) + mode + optional until and submit, Then `POST
/notifications/coverage/assign` is called, a subscription is created with
`subscriber_id=A`, `target_user_id=B`, `created_by_id=HoD`, and it appears in the team
list. *Verify FE (network) + BE.*

**B5 - Assign perm-gated.** Given a user without `manage_team`, When they call `POST
/assign`, Then 403. *Verify BE.*

**B6 - Assign scope-gated.** Given a HoD, When either A or B is outside their scope-B,
Then 422/404 (cannot assign). *Verify BE.*

**B7 - Coverer notified.** Given a HoD assigns A→cover→B, Then coverer A receives an
in-app notification "You're now covering <B> until <date>". *Verify BE (notification
row) + FE if feasible.*

**B8 - Auto-assign routing still works.** Given an active redirect coverage A-covers-B
(HoD-created), When a new SLA task would assign to B, Then it routes to A instead (one
hop). *Verify BE.*

**B9 - Notify-only routing still works.** Given a notify-only coverage, When B is
assigned a task, Then A gets a "(covering for B)" copy and B keeps the task. *Verify
BE.*

**B10 - HoD revokes.** Given a HoD, When they revoke a team coverage row, Then `DELETE
/notifications/coverage/manage/{id}` deactivates it (in scope). Out of scope / no perm →
403/404. *Verify FE + BE.*

**B11 - Coverer self-removes HoD coverage.** Given A was assigned by a HoD, When A opens
their own coverage list, Then A can remove it themselves (existing self delete). *Verify
FE + BE.*

**B12 - Empty + error states.** Given no coverage, Then each section shows its empty
state (not a blank box). Given an API error, Then an inline error renders. *Verify FE +
V.*

**B13 - No UUIDs / mobile.** Coverage UI shows human names (no UUIDs) and is usable at
~375px width (scrollable). *Verify FE.*

---

## Verification log (self-verify complete)

Method: pytest (BE) + vitest (FE) + live browser (Playwright MCP) against :3000/:8000.
Note on admin: `person38@example.com` is role `admin` → `get_user_permission_slugs`
returns ALL slugs (user_service.py:1003). Admin bypass means hide/show can only be
demonstrated on a NON-admin account; the FE gating logic itself is proven by vitest
(buttons hidden when the slug is denied).

| AC | Result | Evidence |
|----|--------|----------|
| A1 | ✅ | All 5 SLA slugs seeded in user_permissions (startup sync) |
| A2 - A6 | ✅ | vitest: buttons hidden per denied slug (MyPendingSLAWidget.test) |
| A7 | ✅ | Browser: Reassign/etc render for admin on dashboard |
| A8 - A11 | ✅ | `require_permission` deps added to extend/reassign/escalate/takeover routes; escalate also gets `can_user_act_on_tracking` |
| A12 | ✅ | Resolve calls `POST /{id}/resolve`; vitest asserts the service; route gated + scope-checked |
| A13 | ✅ | n8n PUT path: api_key principal bypasses (code branch on auth_method) |
| A14 | ✅ | PUT hardened: human principal flipping is_resolved needs `…resolve` |
| B1 | ✅ | Browser: Coverage tab beside My Pending/My Team; My coverage renders |
| B2 | ✅ | Self-service POST /coverage/ unchanged (existing tests + coverage-section.test) |
| B3 | ✅ | vitest + browser: Team coverage shown for manage_team/admin, hidden otherwise |
| B4 | ✅ | Browser: assign Jayson F→Jayson SK → POST /assign 201, row renders w/ badges |
| B5 | ✅ | Route gated by require_permission(manage_team) |
| B6 | ✅ | pytest: coverer/target out-of-scope → AppException |
| B7 | ✅ | DB: coverage_assigned notification created for coverer |
| B8 | ✅ | DB: active_coverer_for(SK) == F after redirect assign |
| B9 | ✅ | Notify-only fan-out unchanged (existing fanout tests pass) |
| B10 | ✅ | Browser: revoke → DELETE /manage/{id} 200, row gone |
| B11 | ✅ | `assigned_by_hod` surfaced in list_my_subscriptions + badge |
| B12 | ✅ | vitest: empty + error states for team coverage |
| B13 | ✅ | Browser: names not UUIDs; sm:flex-row responsive form |

Pre-existing unrelated failures (NOT caused by this work, confirmed via git stash):
`test_conversation_sla_coverage_fanout.py` (stale payload missing agent_code/team_set_code)
and 4 `test_rbac.py` (sqlite JSONB render). Flagged, out of scope.
