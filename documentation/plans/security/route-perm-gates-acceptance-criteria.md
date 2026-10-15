# UAC - route permission gates (calendar, scheduled tasks, SLA policies)

**Status:** Acceptance contract for `PLAN-route-perm-gates-15oct.md`.

Legend: each criterion is **Given / When / Then**, testable. **REG** = explicit regression guard.

**Threat statement.** Twelve writes in `system/calendar.py`, `system/scheduled_tasks.py` and
`sla/sla_policies.py` checked only that the caller was signed in. Any staff or portal session
could move public holidays and working hours (every SLA deadline is computed from them), turn
cron jobs off or run them now, and create, rewrite or delete SLA policies and tiers.

## Item 1 - work calendar

UAC1.1 - **Given** a signed-in caller with no permission, or with `system.work_calendar.view`
only, **when** it calls `PUT /system/work-calendar-config`, `POST /system/public-holidays`,
`PUT /system/public-holidays/{id}` or `DELETE /system/public-holidays/{id}`, **then** 403, the
detail names `.edit`, `.add`, `.edit`, `.delete` respectively, and nothing changes.
UAC1.2 - **Given** a caller holding that slug, **when** it makes the same call, **then** it
succeeds and the change is stored.
UAC1.3 (REG) - **Given** an admin, **when** it makes any of the four calls, **then** it succeeds
through the admin bypass.
UAC1.4 (REG) - The two calendar reads stay open to any signed-in caller.

## Item 2 - scheduled tasks

UAC2.1 - **Given** a signed-in caller who is not admin or superadmin (even one holding every
calendar and SLA slug), **when** it calls `PATCH /system/scheduled-tasks/{id}`, **then** 403
and the task is unchanged.
UAC2.2 - **Given** the same caller, **when** it calls `POST /system/scheduled-tasks/{id}/run-now`,
**then** 403 and no run starts.
UAC2.3 - **Given** an admin, **when** it makes either call, **then** it succeeds.
UAC2.4 (REG) - List, detail and run-log reads stay open to any signed-in caller.
UAC2.5 - **Given** a non-admin, **when** the scheduled task detail page opens, **then** it shows
"You don't have access to this page" and loads nothing; an admin sees Run now and the form.

## Item 3 - SLA policies and tiers

UAC3.1 - **Given** a caller with no permission, or with `sla_management.sla_policies.view` only,
**when** it creates, updates or deletes a policy, **then** 403 naming `.add`, `.edit`, `.delete`
respectively, and nothing changes.
UAC3.2 - **Given** the same caller, **when** it creates, updates or deletes a tier, **then** 403
naming `.edit`, and nothing changes.
UAC3.3 - **Given** a caller holding the slug, or an admin, **when** it makes the call, **then**
it succeeds.
UAC3.4 (REG) - **Given** an X-API-Key caller, **then** its act-as user is held to the same slug:
refused without it, accepted with it.
UAC3.5 - **Given** a `.view`-only caller on SLA Policies, **then** the list offers no Create, the
detail offers no Edit, Delete, Add Tier or tier row action, and the create and edit pages answer
no access in place. **Given** `.edit`, Edit and every tier action show; **given** `.delete`,
Delete shows; **given** `.add`, Create shows.
