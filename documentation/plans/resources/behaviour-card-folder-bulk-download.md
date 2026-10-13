# Behaviour card: Files page, download selected folders and files as one ZIP

Lane FOLDER-BULK-DOWNLOAD, 7 Oct 2026. Facts cite file:line in this worktree (origin/main e4158ab52) or a query on the 6 Oct prod copy (sorento_ai_automation_20261006).

## What exists today (the pieces the plan reuses)

- "Export selected" is the selected-ROWS xlsx export, not a file download: `driveBulkActions.ts:66-71` wires it to the toolbar's `openExport` (`AttachmentsInFolderPanel.tsx:700-715`, `exportConfig` at `:1318`). It is disabled when `selectionHasFolder` (`AttachmentsInFolderPanel.tsx:405`) because folder rows have no xlsx columns. There is NO file-bundle download anywhere today; the only download is one file at a time (`attachmentService.ts:506-513` -> `GET /{attachment_id}/download`, `attachments.py:1418-1443`).
- Single-file download access: company scope only. `Attachment` and `AttachmentDirectory` carry `__company_shared__ = True` (`resources.py:22`, `:123`), so every ORM read is filtered to `company_id IS NULL OR company_id IN <viewer scope>` by `do_orm_execute` (`base.py:112-138`). `_get_attachment_any` (`resources_service.py:1383-1390`) relies on that filter and raises 404 otherwise. `access_levels` (`resources.py:184`) is dealer / end_user visibility for contacts (`contact_attachment_access.py` docstring), not a staff gate: staff see every file in the drive regardless, it is only a list filter (`attachments.py:461-462`).
- Folder tree: `AttachmentDirectoryService.get_descendant_directory_ids_portable` (`resources_service.py:280`) and `build_directory_path_map` (`:312`).
- Bulk import (ZIP) archive layout, the convention to mirror (`import_tasks.py:764-785`, `:872-873`): entry paths are RELATIVE to the folder the import runs in; `a/b/` directory entries create empty folders; path parts split on `/`; `__MACOSX` / `.DS_Store` skipped; same-name collision gets the ` - copy (n)` suffix (`attachments.py:105-135`).
- Background job plumbing already used by bulk import: `JobService.create_job` (`job_service.py:125-132`), enqueue on the `imports` queue (`attachments.py:1341-1366`), worker re-applies the requester's company scope (`import_tasks.py:86-112`), FE dialog polls `GET /system/jobs/{id}/status` every 2 s with a progress bar and finished / failed states (`AttachmentBulkImportDialog.tsx:99-118`, `:282-320`). A job can retain a storage object (`source_file_key` / `source_file_provider`, `job.py:72-75`) and `GET /system/jobs/{id}/source` hands back a 1 h signed URL for it, owner-only (`jobs.py:358-404`, `resolve_signed_url` `storage_router.py:295`). Cancel exists (`jobs.py:465`).

## Real examples (prod copy, 6 Oct; names are brand folders, no personal data)

Folder "Marketing" (root, shared, 27 direct children, the owner's screenshot):

| Folder | Subfolders | Files | Size |
|---|---|---|---|
| Sorento Product Photo | 123 | 1374 | 1559 MB |
| Sorento Technical Drawing | 114 | 646 | 183 MB |
| CATALOG SORENTO | 0 | 2 | 97 MB (one file is 85 MB) |
| SRT LITE / DIY PROMOTION (END USER) | 1 | 2 | 3 MB (name contains "/") |
| Cabana Technical Drawing | 0 | 0 | empty folder |

Whole Marketing tree: 495 folders, 3302 files, 3079 MB. Whole drive: 726 folders, 6585 files, 5347 MB, all on R2. 55 folders hold two files with the same display name (case-insensitive). 45 empty folders. 417 files sit in a folder whose company differs from the file's own company (a shared folder with company-owned files inside), so a viewer scoped to one company sees the folder but not every file in it.

## Rules (proposed)

1. Archive root = the folder the user is looking at (same anchor as bulk import). Selected files go at the archive root; each selected folder becomes a subtree `FolderName/Sub/file.ext`. Mixed selections allowed.
2. A selected folder whose own descendant is also selected is deduplicated: the descendant appears once, inside its parent's subtree.
3. Empty folders are written as directory entries (`Name/`) so re-import recreates them.
4. File entry name = the display name (`stored_filename`, falling back to `original_filename`, exactly what the row and the single download use, `AttachmentsInFolderPanel.tsx:370`). Two files with the same name in one folder: the second gets ` - copy (2)`, the third ` - copy (3)` (import's suffix, `attachments.py:111-135`).
5. `/` and `\` inside a folder or file name are replaced by `-` in the archive path (import splits on `/`, so the real name cannot round-trip either way).
6. Files the viewer cannot see (outside their company scope) are not in the archive, by the same ORM filter the single download relies on. The completion summary says "N files not included (outside your companies)". No manifest file inside the archive (it would be imported as a file).
7. A file whose bytes cannot be read from storage is skipped and counted as failed in the summary; the export continues.
8. Trash view: no download action (trash exposes Restore and Delete only, `driveBulkActions.ts:48-63`).
9. Never stuck: the job ends in exactly one of finished (Download button) or failed (error text); the dialog can be closed while running and the job continues; Cancel is available while running.
10. Permission: the new endpoint requires `resource.attachments.view` (`DRIVE_READERS`, `attachments.py:100`), stricter than today's single download which only checks authentication.
11. Archive file name: `<current folder name or "Files">-YYYYMMDD-HHMM.zip`. Zip64 on (stdlib default) so archives above 4 GB are valid.

## Edge cases

- 3 GB trees: a request-time ZIP would hold 3 GB in the API process and in the browser tab (today's single download is `response.blob()`, `attachmentService.ts:512`). See Q2.
- `ImportJob.source_file_size` is a 4-byte Integer (`job.py:75`): a 3 GB archive overflows it. Size goes in `result` JSON instead.
- `R2Service.upload_file` takes `bytes` (`r2_service.py:88-93`): the worker needs a file-object (multipart) upload for the archive.
- Multi-company viewer: the import job snapshots ONE `company_id` (`job.py:48`, `import_tasks.py:86-112`). The export job must snapshot the requester's FULL scope set and re-apply it, or a multi-company user's export would silently lose the other companies' files. Security-reviewer item.

## Questions (max 5)

Q1 Menu item. (a) NEW item "Download selected" in the same Action menu, enabled on any mix of folders and files; "Export selected" stays the xlsx rows export and stays disabled on folders. (b) Repurpose "Export selected" to produce the ZIP. Recommendation (a): Export means xlsx on every toolbar in the app (`data-grid-list-toolbar.tsx:415-418`), and the owner's ask is a download of the files, not a report of them.

Q2 Delivery. (a) Background job on the worker (`imports` queue, same as bulk import): the worker streams each file from R2 into a ZIP on local disk, uploads it to `bulk-exports/<job>.zip`, the dialog shows live progress (files done / total, MB), then a Download button opens the 1 h signed URL (native browser download, no bytes through the API or the tab). (b) Synchronous streaming ZIP from the API with a short-lived token in the URL. Recommendation (a): Marketing alone is 3 GB and 3302 files; (a) mirrors import exactly, survives tab close, has a clear failed state and a cancel, and puts no token in a URL. Cost: ONE new dialog (progress + download), which I will mock in Lavish before building, reusing the bulk-import dialog's Progress / finished / failed blocks.

Q3 Files outside the viewer's scope. (a) Excluded silently by the scope filter, counted in the summary line, no manifest inside the ZIP. (b) Refuse the whole export when any selected folder holds a file the viewer cannot see. (c) Write a `manifest.txt` into the archive listing skipped files. Recommendation (a): 417 files already live in folders of another company, so (b) would block most shared-folder exports, and (c) adds a file that re-import would ingest.

Q4 Names. Confirm rules 4 and 5: display name as entry name, ` - copy (n)` for same-name files in one folder (55 real cases), `/` and `\` in names replaced by `-` (one real folder). Recommendation: yes; alternative is to refuse exports that contain such names, which blocks "SRT LITE / DIY PROMOTION" for no gain.

Q5 Size and retention. (a) No size cap; start immediately with live progress and Cancel; archive object kept 24 h via a Cloudflare R2 lifecycle rule on the `bulk-exports/` prefix (owner sets it once in the R2 console, no code). (b) Hard cap (for example 2 GB) with a clear error before the job starts. (c) Keep (a) but add a daily scheduler tick that deletes finished export objects older than 24 h. Recommendation (a): whole-drive is 5.3 GB and the job approach handles it; a cap would refuse the owner's own screenshot case (27 folders = 3 GB). If the lifecycle rule is not wanted, (c) is the code-side fallback.
