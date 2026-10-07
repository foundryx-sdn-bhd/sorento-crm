# PLAN - Files page: download selected folders and files as one ZIP

Status: **Approved behaviour (card a38602650 closed 7 Oct 2026: Q1 a, Q2 a, Q3 a with shared files INCLUDED, Q4 yes, Q5 c, no size cap). Built on PR #1520. Owner hand test PASS on ba8c52f57 (7 Oct); final head 9b1f7418b adds the failed-job status guard found in the browser pass. Reviewer READY + security PASS on both heads. Lane stays M: the My downloads surface already exists, no new screen. Track: M, LEAD pattern (tester -> coder -> reviewer + security-reviewer).**
UAC: `documentation/plans/resources/folder-bulk-download-acceptance-criteria.md`
Behaviour card: `documentation/plans/resources/behaviour-card-folder-bulk-download.md`
Lane: `crew/folder-bulk-download` (worktree `sorento_crm-folder-bulk-download`), base `origin/main` e4158ab52.

## 0. What exists (measured, cited)

| Piece | Where | Reused as |
|---|---|---|
| Bulk Action menu gating (pure) | `sorento_crm_frontend/app/(protected)/resource-management/attachment-directories/components/driveBulkActions.ts:40-110` | add one item |
| Panel wiring, selection sets | `.../AttachmentsInFolderPanel.tsx:397-406` (`selectedFileIds`, `selectedFolderIds`), `:687-760` (menu), `:240-253` (`effectiveDirectoryId`) | new handler + dialog mount |
| Job dialog pattern (poll 2 s, progress, finished / failed) | `.../attachments/components/AttachmentBulkImportDialog.tsx:60-123`, `:270-330` | shape of the new dialog |
| FE job status call | `.../attachments/services/attachmentService.ts:329-333` (`getJobStatus`) | reused |
| Start-a-job route shape | `sorento_crm_backend/app/api/v1/resources/attachments.py:1269-1372` (bulk-import: `JobService.create_job` + `enqueue_job(..., queue_name="imports", job_timeout=7200)`) | copied for the download |
| Job model | `app/models/job.py:38-78` (`job_type`, `user_id`, `company_id`, `result` JSONB, `source_file_key/provider/filename`, `job_metadata`) | output archive recorded on `source_file_*`, size in `result` |
| Signed URL for a job's stored file | `app/api/v1/system/jobs.py:358-404` (`/jobs/{id}/source`, owner-only, 1 h) | the Download button's link, unchanged |
| Status + cancel | `jobs.py:408-461`, `:465-495`; `JobService.cancel_job` `job_service.py:333-355` flips the row to `cancelled` first | FE terminal states: finished, failed, cancelled |
| Worker scope re-apply | `app/tasks/import_tasks.py:86-112` (single company snapshot) | pattern; this job snapshots the FULL scope set |
| Folder subtree + paths | `app/services/resources_service.py:280` (`get_descendant_directory_ids_portable`), `:312` (`build_directory_path_map`), `:253` (`get_full_directory_path`) | planning |
| Import layout to mirror | `import_tasks.py:764-785`, `:872-873` (paths relative to the import folder, `dir/` = empty folder, split on `/`), `attachments.py:105-135` (` - copy (n)`) | layout + suffix |
| Storage read / write | `storage_router.get_backend` `:121`, `extract_key` `:204`, `resolve_signed_url` `:295`; `R2Service.upload_file(bytes)` `r2_service.py:88-93`, `S3Service.upload_file(bytes)` `s3_service.py:102` | read per file; NEW `upload_fileobj` for the multi-GB archive |
| Company scope filter | `app/models/base.py:112-138`, `resources.py:22`, `:123` (`__company_shared__ = True` on folders and files) | the only access rule, same as single download |

Data (prod copy 6 Oct): Marketing = 495 folders / 3302 files / 3079 MB; largest file 85 MB; 55 same-name pairs; 45 empty folders; one folder name with `/`; 417 files in a folder of another company.

## 0b. Rulings and must-fix items (owner + crew, 7 Oct 2026)

- Q1 (a): NEW menu item "Download selected" for any mix; "Export selected" unchanged.
- Q2 (a): background job with live progress, Download button, 1-hour signed link.
- Q3 (a), clarified: files marked SHARED (`company_id IS NULL`) are INCLUDED; only files owned by a company the viewer is not a member of are left out and counted. This is exactly the single-file download's predicate (`company_id IS NULL OR company_id IN <scope>`, `base.py:112-138`), so NO new rule is written; one test pins the triple: shared included, other-company excluded, own-company included.
- Q4 yes: list name as entry name, ` - copy (n)`, `/` and `\` to `-`.
- Q5 (c): our own daily cleanup of finished archives older than 24 h; no size cap.

Must-fix (in the plan as hard items):
1. **Archive size field.** `import_jobs.source_file_size` is a 4-byte Integer (`job.py:75`); the archive byte count goes to `result.bytes` and `source_file_size` stays NULL for this job type. Test: a 3 GB stats value round-trips through `result`.
2. **Multipart upload.** `upload_file(bytes)` (`r2_service.py:88`, `s3_service.py:102`) cannot take a multi-GB archive; add `upload_fileobj` (boto3 `upload_fileobj`, multipart) on both backends and the `StorageBackend` Protocol; the task streams the temp file through it. Test: the fake backend receives a file object, never bytes.
3. **Multi-company viewers.** The import job snapshots ONE `company_id` (`job.py:48`, `import_tasks.py:86-112`); this job snapshots the FULL scope (`null` = all companies, else the sorted id list) in `job_metadata.company_scope` and the worker re-applies it with `set_company_scope`. Security-reviewer owns this item. Test: a two-company snapshot includes both companies' files and excludes a third's.

Cleanup tick (Q5 c), SUPERSEDED by the My downloads per-kind purge window below: `purge_bulk_download_archives(db, hours=24)` in the new service deletes the storage object of every `attachment_bulk_download` job with `source_file_key` set and `completed_at` older than 24 h, then nulls `source_file_key` (the job row stays as history); registered in `app/scheduler/task_scheduler.py` next to the daily `ai_trace_sweep` (`:746-752`) with `IntervalTrigger(hours=24)`, same shape as `_handler_user_downloads_purge` (`:303-313`) and `purge_expired_downloads` (`download_service.py:250`, best-effort per object). Test: a 25 h old finished job loses its object and key, a 2 h old one keeps both, a failed job with no key is untouched.

### My downloads entry (owner feedback on mock v1, 7 Oct 2026: "put this under My Downloads also to make this decoupled")

Existing surface, reused as-is: `components/my-downloads/MyDownloadsDrawer.tsx` (right-side Sheet off the top-bar icon `MyDownloadsIcon.tsx`), `DownloadRow.tsx` (`KIND_LABEL` :35-55, `StatusBadge` :57-78, Download/Preview :120-204, `fetchDownloadUrl`), context polling in `MyDownloadsContext.tsx`; backend `app/api/v1/downloads/downloads.py` (`GET /api/v1/downloads` list :27-44, `GET /{id}/url` 1 h signed :47-64, owner-only), `DownloadService` (`download_service.py:23-44` create, `:195-248` mark_processing / mark_ready / mark_failed), model `user_downloads` (`app/models/download.py`), daily purge `purge_expired_downloads` (`:250`) registered at `task_scheduler.py:303-313`.

How the archive rides on it (no migration, no new endpoint for the link):
- The start request creates a `user_downloads` row (`kind="attachment_bulk_zip"`, `filename=<archive name>`, `source_entity_type="import_job"`, `source_entity_id=job.id`) in the same transaction as the job; `job_metadata.download_id` and `result.download_id` point back.
- Worker: `mark_processing` at start; `mark_ready(storage_provider, storage_key, filename)` after the upload; `mark_failed(error)` on failure or cancel. The archive object is owned by the download row (its `storage_key`), so the import job's `source_file_*` stay NULL.
- Dialog "Download ZIP" and the drawer row both call `GET /api/v1/downloads/{download_id}/url`; the `/system/jobs/{id}/source` path is NOT used.
- `KIND_LABEL` gains `attachment_bulk_zip: 'Files archive (ZIP)'`; the Preview control is hidden for this kind (a zip has nothing to render). The ready row shows the size from `result.bytes`? No: the row has no size column and reading the job from the drawer adds a join; the size stays in the dialog summary only (simplest; the mock's "1.6 GB" on the row is dropped unless the owner asks).
- Retention (Q5 c): `purge_expired_downloads` gets a per-kind window, `{"attachment_bulk_zip": 1 day}`, applied inside the same sweep (rows of that kind use `created_at < now - 1 day`; every other kind keeps `retention_days`). The separate cleanup tick in section 0b is therefore NOT built: one purge, two windows.
- Stale sweep: `fail_stale` (`download_service.py:12`, `:98-135`) flips pending/processing rows older than 20 min to failed, tuned for 600 s exports. A 3 GB build runs longer, so the window becomes per kind too: `{"attachment_bulk_zip": 2 h 10 min}` (just past the 7200 s job_timeout). One dict, one filter change.

Alternative checked and rejected AS THE ONLY SURFACE: the "My Downloads" drawer (`app/models/download.py`, `components/my-downloads/*`) already does pending / processing / ready / failed with a signed URL and a 30-day purge, but it has no progress counter (the owner asked for live progress), no cancel, and a 20-minute stale sweep (`download_service.py:12`, `:98-135`) that would flag a live 3 GB build as failed. Giving it those three needs a migration and a sweep exception; the import job has all three today. The archive therefore rides on `import_jobs`.

## 1. Design (simplest thing that works)

One job type, one task, one pure planner, one dialog, one menu item. No new table, no migration (`import_jobs` already has every column needed), no new permission slug, no new queue.

### Backend

1. `POST /api/v1/resource-management/attachments/bulk-download` (in `attachments.py`, next to `bulk-import`).
   Body: `{ directory_id: str | null, folder_ids: [uuid], attachment_ids: [uuid] }`.
   Guard: `require_any_permission_with_api_key(DRIVE_READERS)` (`attachments.py:100`).
   Steps: validate uuids; resolve the visible selected rows under the request scope (folders: `AttachmentDirectory` non-deleted; files: `Attachment` non-deleted); 400 when both lists empty, 404 when nothing visible; derive `archive_filename` (AC-B9) from the viewed folder's name; `create_job(job_type="attachment_bulk_download", user_id, filename=archive_filename, metadata={directory_id, folder_ids, attachment_ids, company_scope: null | [ids]})`; `enqueue_job(process_attachment_bulk_download, str(job.id), queue_name="imports", job_timeout=7200, job_id=str(job.job_id))`; return `{job_id, id, message}`.
   `company_scope` snapshot: `get_company_scope(db)` -> `None` stays `null` (all companies), a frozenset becomes a sorted list. The worker re-applies it exactly with `set_company_scope` (must-fix 3).
2. `app/services/attachment_bulk_download_service.py` (pure, testable without storage):
   - `plan_archive(db, directory_id, folder_ids, attachment_ids) -> ArchivePlan` with `entries: list[ArchiveEntry(path, attachment | None)]` (a `None` attachment = directory entry), `files_not_visible: int`, `folders: int`.
     Rules B1-B7: dedupe nested selections (drop a selected folder/file whose ancestor folder is also selected); walk each selected folder with `get_descendant_directory_ids_portable(include_deleted=False)`; build relative paths from the viewed folder; entry name = `stored_filename or original_filename`; sanitise `/` and `\` to `-`; per-folder case-insensitive collision suffix via the import's `_suffix_copy_name` logic (move that helper into this service and import it back into `attachments.py`, one copy); empty folders -> `path/` entries.
     `files_not_visible` = (count of non-deleted files in the same subtree under `company_scope(db, None)`) minus (count under the request scope); numbers only.
   - `write_archive(plan, read_bytes, out_fileobj, on_progress, should_stop) -> ArchiveStats(files_included, files_failed, bytes)`: `zipfile.ZipFile(out, "w", ZIP_STORED, allowZip64=True)`; per file `read_bytes(attachment)` in a try; failure -> counted, continue; `on_progress(processed)` every 10 files; `should_stop()` checked at the same cadence (cancel).
3. `app/tasks/attachment_bulk_download_task.py`: `process_attachment_bulk_download(db_job_id)`: open `SessionLocal()`, load the job, re-apply the scope snapshot, `plan_archive`, `update_job_progress(total_rows=len(files))`, write to a `tempfile.NamedTemporaryFile` on the worker, `should_stop` = job row status is `cancelled`; on finish upload with the NEW `upload_fileobj(fileobj, key="bulk-exports/<job.id>.zip", content_type="application/zip")` on the default provider backend, set `source_file_key/provider/filename`, `complete_job(result={archive_filename, files_included, files_failed, files_not_visible, folders, bytes})`; `files_included == 0` -> `fail_job("No file could be read")`; any exception -> `fail_job(str)`; `finally` removes the temp file.
   Notification copy: `complete_job` / `cancel_job` say "Import completed / cancelled" with `job.job_type` in the sentence (`job_service.py:189-232`, `:349-355`); give this job_type its own two lines ("Archive ready: <archive_filename>, N files" / "Download cancelled") in `_notify_import_job_event` callers, by job_type, nothing more.
4. `R2Service.upload_fileobj` + `S3Service.upload_fileobj` (boto3 `upload_fileobj`, multipart by default) and the `StorageBackend` Protocol gains the method (`storage_router.py:31`) (must-fix 2).
4b. Per-kind retention (1 day) and stale window (2 h 10 min) in `download_service.py`; My downloads row lifecycle in the start route and the task (section 0b).

### Frontend

5. `driveBulkActions.ts`: new `onDownload` handler and item `{ key: 'bulk-download', label: 'Download selected', onClick }`, enabled on any non-trash selection, placed right after `bulk-move`. Export keeps its gating; icon map in the panel: `bulk-download` -> `Download`, `bulk-export` -> `FileSpreadsheet`.
6. `attachmentService.ts`: `startBulkDownload({ directoryId, folderIds, attachmentIds })`, `getJobSourceUrl(jobId)` (GET `/system/jobs/{id}/source`), `cancelJob(jobId)` (POST `/system/jobs/{id}/cancel`); errors through `extractApiError`. `useAttachments.ts`: `useStartBulkDownload` mutation.
7. `AttachmentBulkDownloadDialog.tsx` (new, mirrors the import dialog's processing / done blocks; Download ZIP calls `fetchDownloadUrl(download_id)` from the My downloads code and the ready hint says the archive is also in My downloads): props `{ open, onOpenChange, directoryId, folderIds, attachmentIds }`. On open: start the job once; poll `getJobStatus` every 2 s until `finished | failed | cancelled`; processing = "Preparing archive", `Files: processed / total`, `Progress`, secondary "Cancel" (calls `cancelJob`) and "Close" (job continues); finished = "Archive ready" + summary lines (AC-D8) + primary "Download ZIP" (fetches the signed URL and opens it) + Close; failed / cancelled = message + Close. A poll error is ignored until the next tick (no stuck spinner: the status endpoint syncs RQ state).
8. `AttachmentsInFolderPanel.tsx`: `onDownload: () => setBulkDownloadOpen(true)`; mount the dialog with `effectiveDirectoryId`, `selectedFolderIds`, `selectedFileIds`.

### Mock

`documentation/mockups/folder-bulk-download/index.html`: the dialog's three states on the real case (Marketing, 3 folders + 1 file, 1374 files / 1.5 GB), 1280 + 375, built from the import dialog's blocks. Filed as a crew ask before the dialog is built.

### Security review round (7 Oct, on 7ebf8125a)

- Fixed: `_safe_name` also maps `.`, `..` and empty names to `_` (AC-B6b); the route refuses a second start while the user's archive is in flight via the EXISTING `DownloadService.has_in_flight(user_id, kind="attachment_bulk_zip")` (`download_service.py:55-97`), 409 (AC-C6b); the task deletes the uploaded object best-effort when `mark_ready` fails (AC-D3b).
- Accepted as is, documented: a shared or own-company file inside an OTHER-company subfolder is not reachable because the subtree walk cannot see that folder; including it would print an invisible folder's name in the archive path, so the stricter behaviour is right and the file counts as not visible. A staff session never resolves to the all-companies scope (`company_scope_resolver.py:175-245`); `None` is reachable only through an API key without contact scoping, the same exposure the single-file download has today. The 1-hour signature cannot limit a public R2 CDN domain; every attachment shares that exposure, unchanged here. A SIGKILL of the worker can leave a temp file in the worker tmp dir, same as the import's staged files.

### Reviewer round (7 Oct, on 7ebf8125a)

- Blocker fixed: `JobService.cancel_job` (`job_service.py:333-375`) sent RQ's stop command, which SIGKILLs the work-horse (`rq/command.py:141`, `rq/worker.py:1549`) so the task's `finally` never ran: temp zip left on the worker, My downloads row stuck at processing until the 2 h 10 min sweep. For `attachment_bulk_download` the stop command is skipped and the DB flip (already first) drives the cooperative `should_stop` (AC-D6b).
- Fixed: `should_stop` was only checked every 10 files and never after the loop, so a small job or a cancel during the upload went on to upload and `complete_job`, which has no CANCELLED guard; the task now re-checks after the loop and before upload / complete (AC-D6c).
- Fixed: only file names were de-duplicated; sibling folders with one name (not unique in the schema), a file and a folder sharing a name, or names colliding after `/` to `-` merged into duplicate zip entries and broke the re-import. Folders join the per-parent seen-set (AC-B5b).
- Tests added: fail-closed scope paths (missing key in the task, UNSET in the route; both mutants had survived), 403 without the drive permission, mid-run cancel, sibling-folder collisions, cancel without the RQ stop.
- Accepted nits: a directly selected file inside a hidden subfolder of a selected root is counted twice in `files_not_visible` (number only); the download row commits before enqueue, so a Redis outage leaves a pending pair that the stale sweep fails 2 h 10 min later (same as every export today); `window.open` uses `noopener,noreferrer` like DownloadRow. The dialog calling the service directly (no mutation hook) is accepted by the reviewer: one-shot imperative start, same as the import dialog.

## 2. Tests (tester writes first, red; coder makes green)

pytest `tests/test_attachment_bulk_download.py` (Postgres `blank_session`, auth-bypass pattern of `tests/test_drive_endpoint.py:1-50`):
1. B1 mixed selection -> file at root, folder subtree paths.
2. B2 nested selection deduped.
3. B3 empty folder -> `Name/` entry.
4. B5 same-name files -> ` - copy (2)`, ` - copy (3)`.
5. B6 `/` in a folder name -> `-`.
6. B7 archived file and trashed subfolder excluded.
7. C2/C3 the ruling triple in one shared folder under a company-A scope: shared file (NULL company) included, own-company (A) file included, company-B file excluded with `files_not_visible == 1`; all three present under the all-companies scope.
8. C1/C4/D1 endpoint: 401 unauthenticated; 400 empty; 404 all-invisible; 202-style body with `job_id` and a job row `job_type == attachment_bulk_download` carrying the scope snapshot.
9. D3/D4/D5 task end to end with a fake backend (monkeypatch `get_backend` and `upload_fileobj`): zip read back with the expected names, `source_file_key` set, result counts; one unreadable file -> `files_failed == 1`, status finished; all unreadable -> failed with "No file could be read".
10. C5 task re-applies a two-company snapshot (both companies' files present, a third company's absent).
11. D6 cancel: `should_stop` true after the first batch -> no upload, temp file gone, status stays cancelled.

vitest:
12. `driveBulkActions.test.ts`: `bulk-download` present and enabled with folders / files / mix; absent in trash; `bulk-export` still disabled with a folder.
11b. Must-fix 1: `result.bytes` carries 3_300_000_000 and `source_file_size` stays NULL.
11c. Retention per kind: a 25 h old ready `attachment_bulk_zip` row is purged (object deleted) while a 25 h old `complaint_pdf` row survives the 30-day default; a 2 h old zip row survives.
11d. My downloads lifecycle: start creates a pending row of kind `attachment_bulk_zip` with the archive filename; task flips it processing -> ready with the storage key (or failed with the reason); `GET /api/v1/downloads/{id}/url` returns a URL for the owner and 403/404 for another user.
11e. Stale window per kind: a 30 min old processing zip row is NOT flipped by `fail_stale`; a 30 min old processing `complaint_pdf` row is.
12b. `DownloadRow.test.tsx`: kind `attachment_bulk_zip` renders the label 'Files archive (ZIP)' and no Preview control.
13. `AttachmentBulkDownloadDialog.test.tsx`: start called once on open; processing shows `Files: 10 / 1374`; finished shows the summary and a Download ZIP button that requests the source URL; failed shows the error; cancelled shows the message; no spinner in any terminal state.

## 3. Order of work

1. Behaviour card closed 7 Oct; UAC/plan updated (this revision).
2. Mock v1 filed as an ask (9b8f5d24e); build only after approval.
3. Tester: red tests, `test(red):` commit alone, red-proof note.
4. Coder: backend 1-4, frontend 5-8, until green; kill-proof note.
5. Reviewer + security-reviewer on the final head (scope snapshot, `files_not_visible` numbers only, owner-only job endpoints, no token in URLs).
6. Browser pass 1280 / 375 on the crew test copy; `laneboard/scripts/<PR>.md`; merge origin/main; crew-done.

## 4. Out of scope

Per-file `access_levels` enforcement for staff (not a staff gate today); download from the chatbot or MCP; archive retention tooling beyond Q5's answer; a per-row "Download folder" action (the bulk menu covers one folder too).
