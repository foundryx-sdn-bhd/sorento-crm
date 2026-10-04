# PLAN: order inquiry line attachments, sent with the OI handover email (27 Sep 2026)

Status: BUILT, REVIEW CLEAN 27 Sep 2026 (PR #1313; reviewer + security-reviewer round 1 fixed,
browser pass done), awaiting owner hand test + merge go. Track: full (one migration, a new
upload surface). Grill posted on #1312; the build assumes the recommended answers unless the
owner rules otherwise.
UAC: `oi-line-attachments-27sep-acceptance-criteria.md`. Issue: #1312.
Lane: cloud lane, branch `claude/oi-line-attachments-ttlgl0` off `origin/main` 52b0ac24.
Classification: CORE extension of the project sales / order inquiry domain, `public` schema
for the links (the generic `entity_attachment_links`), no new module key.

## 1. Journey

See the UAC file's `Journey` section. In one line: CS clicks a paperclip on a board line,
drops the clarification photos or PDF into the existing lightbox, confirms as today, and the
handover email purchasing receives carries those files as real attachments, named per line.

## 2. What exists today (read, not assumed)

- **No lightbox upload is wired to OI lines yet.** #1220's "lightbox linked line" is the
  read-only PO/SPO document dialog (`OrderInquiryDocumentDialog.tsx`). The upload lightbox the
  owner means is the shared shape `ShipmentLinePhotosCell.tsx` already ships for packing list
  lines: `Dialog` + `FileDropzone` + `AttachmentPreviewModal` + `useDeferredRowAction`. This
  lane reuses those four primitives, not a new upload component.
- **Per-line attachment storage already exists.** `entity_attachment_links`
  (`EntityAttachmentService`) with a new `entity_type`, exactly as
  `app/services/scm/shipment_line_photos.py` does for `inbound_shipment_line`. Bytes go
  through `storage_router` (S3 or R2 per `STORAGE_DEFAULT_PROVIDER`). No new table.
- **Board rows** (`FulfilmentBoardListView.tsx`, `BoardContribution`) carry `line_id` = the
  CORE `sales_order_lines.id` (UUID). OI rows reach the same core id through
  `order_inquiry_rows.so_line_id -> projects.sales_order_lines.core_sales_order_line_id`, and
  the OI Lines tab row carries it as `core_line_id`. So the link key is the core line id: the
  one id both surfaces and the email path can all reach.
- **Handover email:** `_record_handover` queues each line, `_fire_pending_handover` builds the
  context after the root commit (`_build_handover_context`) and dispatches
  `order_inquiry_handover`; `AutomationService._send_per_match` (one_email) ->
  `_enqueue_email` -> `Notification.data` -> `notification_tasks._enqueue_email_for_delivery`
  -> `email_outbox_service.enqueue`. The outbox and its drainer ALREADY send attachments by
  storage reference (`metadata_json.extra_attachments`, `email_outbox_tasks._attachments_for`,
  used by the container request email); only the automation hop does not forward them.
- **Provider:** plain SMTP (`notification_email.send_mime_email`). The sending mailbox is the
  Google Workspace account person2@example.com, whose limit is 25 MB per message including
  base64 overhead (about 4/3). No cap exists in code today.
- **Permissions:** there is no separate "edit OI line" slug. The board's own writes (the
  confirm that raises OI lines) are gated `projects.projects.edit`; reads `projects.projects.view`.
- **Audit:** `Attachment` is already declaratively audited (INSERT/DELETE). The line-level
  trail is `log_audit(db, "sales_order_line", line_id, "UPDATE", ...)`.

## 3. Design

### 3.1 Backend

- `app/services/so_line_attachments.py` (new, modelled on `shipment_line_photos.py`):
  `ENTITY_TYPE = "sales_order_line"`, `TYPE_CODE = "so_line_attachment"`.
  - `lookup(db, line_ids)`: filters ids to `SalesOrderLine` rows visible in the caller's
    company scope, then `list_links_for_entities`; serialises `{id, attachment_id, filename,
    size_bytes, content_type, url, thumbnail_url}` (signed URLs, `strict=True`).
  - `upload(db, line_id, files, actor_id)`: extension allowlist (images, pdf, xlsx, xls),
    `check_quota` (type's 10 MB), store, thumbnail for images, `create_attachment`, link,
    `log_audit` on the line, commit per file; the shipment photo failure handling (purge the
    stored object on a mid-batch failure, name what landed). Returns the full list.
  - `delete(db, line_id, link_id, actor_id)`: scoped to the line, deletes the attachment row
    (cascades the link), `log_audit`, commit, then best-effort object purge.
  - `handover_attachments(db, core_line_ids)`: `{line_id: [{filename, size_bytes,
    storage_provider, storage_key}]}` in link order, for the email.
- Routes (new `app/api/v1/projects/so_line_attachments.py`, mounted with the other project
  routers under `/api/v1/project-sales`):
  `POST /sales-order-lines/attachments/lookup` (view), `POST /sales-order-lines/{line_id}/attachments`
  (edit, multipart `files`), `DELETE /sales-order-lines/{line_id}/attachments/{link_id}` (edit).
- Deferred delete: `record_actions.register(FormAction(key="sales_order_line_attachment.delete",
  entity_types=("sales_order_line_attachment",), window=WINDOW_DESTRUCTIVE,
  permission="projects.projects.edit"))`, payload carries `line_id`.
- Email:
  - `_record_handover` also queues `core_line_id` (memoised beside `line_no`, same cache
    read) and `row_id` is already there.
  - `_fire_pending_handover` reads `handover_attachments(fresh, core ids)` on its fresh session
    (the root has committed, so the uploads are visible) and passes it to
    `_build_handover_context(concluded, attachments_by_line)`, which stays pure.
  - The builder copies each line dict, adds `line.attachments = [{name, attached, url}]`,
    walks lines in email order then upload order with a running total against
    `HANDOVER_ATTACHMENT_CAP_BYTES = 15 * 1024 * 1024`, and sets
    `context["email_attachments"] = [{filename, storage_provider, storage_key, optional: True}]`
    for the attached ones. `name` = `"<SO no>-L<line no>-<original>"` (the same string as the
    MIME filename, so the row and the attachment list agree). An unattached file's `url` is
    `build_order_inquiry_link(order_inquiry_id) + "?row=<row id>"`.
  - `AutomationService._send_per_match` one_email branch forwards
    `match.context.get("email_attachments")` into the notification metadata as
    `extra_attachments` (only when present); `_enqueue_email_for_delivery` forwards
    `data["extra_attachments"]` into the outbox metadata. The drainer already reads that key.
  - Drainer: `_ATTACHMENT_MIME` gains png, jpg, jpeg, webp, gif, xls; an extra marked
    `optional` whose download fails is skipped with a warning instead of failing the email
    (AC-E6). Non-optional extras keep today's retry behaviour.
- Migration `soatt_0001_so_line_attachments` (chains on main's single head
  `sales_s1_reports_module`):
  - inserts the `attachment_types` row "Sales Order Line Attachment" (code
    `so_line_attachment`, extensions `jpg,jpeg,png,webp,gif,pdf,xlsx,xls`, 10 MB) when absent;
  - edits the handover template body IN PLACE by anchoring on the remark cell
    (`{{ line.remark | default("", true) }}` in the HTML `<td>` and in the text row) and
    appending the attachments print after it, only when the marker is not already there;
    a template an admin reshaped so the anchor is gone is left alone (logged). Downgrade
    strips exactly the inserted fragment and deletes the type row if nothing uses it.
  - No `alembic_version` touch, no edit of any migration main has.

### 3.2 Frontend

- `project-sales/_shared/services/soLineAttachmentService.ts` (lookup, upload, delete via
  `apiFetch` + `extractApiError`), `project-sales/_shared/hooks/useSoLineAttachments.ts`
  (`useSoLineAttachmentLookup(lineIds)`, `useUploadSoLineAttachments()`; invalidates the
  `['project-sales', 'so-line-attachments']` prefix).
- `project-sales/_shared/components/SoLineAttachmentsButton.tsx`: ghost icon button with
  `Paperclip` (lucide, same size as the verdict actions' `Check`/`X`/`Pencil`), count badge,
  opens `Dialog` with the file list, `AttachmentPreviewModal`, `FileDropzone` + Upload
  (edit only), x -> `useDeferredRowAction({actionKey: 'sales_order_line_attachment.delete'})`.
  Props: `lineId`, `label` ("SO423136 L3 SRTWCY8605-PJ"), `attachments`, `canEdit`.
- Board: `FulfilmentBoardListView` calls the lookup once with every contribution's `line_id`
  and renders the button in the Verdict cell after `BoardVerdictActions`;
  `canEdit = useHasPermission('projects.projects.edit')`.
- OI Lines tab: `OrderInquiryLinesTab` does the same lookup over its rows' `core_line_id`
  and `orderInquiryHeaderLinesColumns` renders the button in the State cell.

## 4. Decisions (grill, recommended answers assumed)

| # | Question | Assumed answer |
| --- | --- | --- |
| Q1 | Which line do files attach to? | **The AutoCount sales order line (core `sales_order_lines.id`)**, so the board, the OI Lines tab and every later OI write for that line share them |
| Q2 | Which handover emails carry a line's files? | **Every handover email whose write includes that line**, not only the first; no "already sent" state |
| Q3 | Accepted file types | **Images (jpg, jpeg, png, webp, gif), PDF, Excel (xlsx, xls)**, what #1311's manual mails carry; 10 MB per file |
| Q4 | Total size cap per email | **15 MB of files per email** (Google Workspace allows 25 MB per message including about 33% encoding overhead); later files over the cap are linked, not attached |
| Q5 | Where does the over-cap link go? | **The order inquiry line in the CRM** (the OI page with `?row=`), where the paperclip lists the file; not a public or expiring storage link, because the recipients are staff |
| Q6 | Who may upload and remove? | **`projects.projects.edit`** (the permission that confirms the board and so writes OI lines); anyone with `projects.projects.view` sees and opens the files |
| Q7 | Attachment file name in the email | **`<SO no>-L<line no>-<original name>`**, so purchasing can tell which photo is which line when several lines carry "image.png" |
| Q8 | Remove a file | **Deferred hard delete (10s countdown, no confirm), removes the file from storage** |

## 5. Known, not built (triggers named)

- An AutoCount re-sync that deletes and re-creates a line gets a new core id; its files stay
  on the old id and stop showing. Trigger to build a carry-over: the first report of files
  vanishing from a line after a sync.
- The files are not sent with the undo email or the reserve emails. Trigger: purchasing asks.
- No per-line "sent" marker. Trigger: purchasing reports duplicate attachments as noise (Q2).

## 6. Deviations (recorded for the PR)

- No separate Phase 1 mock commit: the frontend landed after the backend in the same lane.
  The contract was fixed by the UAC before either, and the red tests (FE and BE) were
  committed first, from the UAC alone.
- Review round 1 moved the board's attachment lookup up to `FulfilmentBoardPanel` (one call
  over every contribution, under the app's own query client), kept the lightbox mounted
  after first open so a delete countdown survives closing it, and read the handover files
  with `company_scope(db, None)` on the post-commit session (without it no file would ever
  attach: `Attachment` is company-scoped and that session carries no scope).
- Security hardening beyond the UAC: content type always derived from the extension, a hard
  10 MB per file and 10 files per request in code (independent of the admin-editable type
  row), malformed ids answer 404/422.
- Fix round 2 (CI on ab5daf39): the lightbox body's `max-h-[60vh]` was a new site for the
  M6-02/M6-03 fixed viewport-height sweep; converted to `max-h-[60dvh]` (not allowlisted),
  pinned by `app/(protected)/mobile-vh.inventory.test.ts`. The red
  `test_migration_identity_0001_s0_model.py` head check is not this lane's: it asserts
  identity is the head, and a separate PR on main moves it to check identity's parent.

## 7. Follow-ups found in review (not this lane)

- `shipment_line_photos.py` trusts the browser's Content-Type the same way this lane first
  did; same one-line fix.
- `DELETE /resource-management/attachments/links/{link_id}` needs no permission slug and can
  unlink any entity's attachment link, this lane's included. Gate it per entity type.

## 8. Tests

See the UAC's test list. Backend on Postgres via `SORENTO_ENV_FILE=.env.ci-tests`; vitest on
the touched files.
