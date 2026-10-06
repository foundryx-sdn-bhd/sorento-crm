# UAC - MENU-PERM-GATE

Plan: `PLAN-menu-perm-gate-06oct.md`.

- AC-1 A signed-in user whose only permission is `ideation.board.view` sees exactly one sidebar
  entry, Ideas, under OVERVIEW. No Dashboards, Marketing, Complaints, SLA, Forms, Resources,
  System or Users & Access group, and no orphan section heading.
- AC-2 An admin impersonating that user sees the same sidebar as AC-1 (the impersonation banner
  aside): the superadmin-only System items (Companies, Import Jobs, Import Logs, Tracking
  Validation, Audit Logs, System Health, Activity Timeline, Scheduled Tasks, Integration Logs,
  Work Calendar) are hidden together with everything else.
- AC-3 The same admin, after ending the impersonation, sees every group and every superadmin-only
  item again. Nothing changes for an admin who never impersonates.
- AC-4 Every menu entry that was ungated now names an existing registry slug of the page it
  opens (table in the plan). No new slug is introduced.
- AC-5 While impersonating a non-admin, `RequireAccess superadmin` pages (Companies) render the
  access-denied state instead of the page body.
