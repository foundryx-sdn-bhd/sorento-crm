# UAC - Files page: download selected folders and files as one ZIP

**Slug:** `folder-bulk-download`
**Status:** Draft, pending behaviour-card answers Q1-Q5 (crew ask a38602650, 7 Oct 2026). Written under the card's recommendations; a different answer edits the marked ACs.
**Plan:** `documentation/plans/resources/PLAN-folder-bulk-download.md`
**Behaviour card:** `documentation/plans/resources/behaviour-card-folder-bulk-download.md`

## Goal

On Dashboards > Resources > Files, a user selects any mix of folders and files and gets ONE archive holding the folder tree and its files, recursively, laid out so Bulk import (ZIP) can read it back, respecting the viewer's company scope exactly as the single-file download does.

## A. Action menu (frontend, pure gating)

- **AC-A1** (Q1a) The bulk Action menu gains "Download selected" (key `bulk-download`). It is enabled for any non-empty selection: files only, folders only, or a mix.
- **AC-A2** "Export selected" (the xlsx rows export) is unchanged: still present, still disabled when the selection contains a folder, same reason text.
- **AC-A3** In the Trash view the menu still exposes only Restore and Delete; no Download.
- **AC-A4** The two items do not share an icon (Export gets a spreadsheet icon, Download keeps the download icon).

## B. Archive layout (backend, pure planning; mirrors Bulk import)

- **AC-B1** Archive root = the folder being viewed (`directory_id`; root or "All" = drive root). A selected file is written at the archive root as `<name>`; a selected folder is written as `<FolderName>/...` with its whole non-deleted subtree underneath.
- **AC-B2** Nested selection: when a selected folder's descendant (folder or file) is also selected, it appears exactly once, at its place inside the parent's subtree, never a second time at the root.
- **AC-B3** An empty folder (no non-deleted files, no non-deleted subfolders) is written as a directory entry `Path/To/Name/`, so a re-import recreates it.
- **AC-B4** Entry name = `stored_filename`, falling back to `original_filename` (the name shown in the row and used by the single download).
- **AC-B5** (Q4) Two or more files in the same folder with the same entry name (case-insensitive): the first keeps its name, the second becomes `<base> - copy (2)<ext>`, the third `<base> - copy (3)<ext>`, using the import's suffix rules (`archive.tar.gz` -> `archive.tar - copy (2).gz`).
- **AC-B6** (Q4) `/` and `\` inside a folder or file name become `-` in the archive path. No other character is changed.
- **AC-B7** Deleted (trashed) folders and archived files are never included, even when they sit inside a selected folder.
- **AC-B8** The archive is a valid zip readable by Python `zipfile` and by Bulk import: re-importing the archive into an empty folder recreates the same folder tree and the same file names (modulo AC-B5/B6 renames).
- **AC-B9** Archive file name is `<folder name>-YYYYMMDD-HHMM.zip` for a folder view and `Files-YYYYMMDD-HHMM.zip` at the root, with AC-B6 applied to the folder name. Zip64 is allowed so archives above 4 GB are valid.

## C. Access and company scope (security)

- **AC-C1** The start endpoint requires the drive read permission (`resource.attachments.view` or `resource.attachment_directories.view`). Unauthenticated -> 401; a user without it -> 403.
- **AC-C2** Only folders and files visible to the requester (company_id NULL or in the requester's scope) are planned into the archive. A file of company B inside a shared folder is absent from a company-A viewer's archive and present in an all-companies viewer's archive.
- **AC-C3** (Q3a) The completion summary carries `files_not_visible` = number of non-deleted files in the selected subtree that the scope filter excluded. No name, path or id of an excluded file appears anywhere in the response or the archive. No manifest file is written into the archive.
- **AC-C4** When every selected id is invisible or unknown the request fails with 404 and no job is created. An empty selection fails with 400.
- **AC-C5** The worker re-applies the requester's FULL company scope snapshot (several companies for a multi-company user, all-companies for an unscoped superadmin) before planning; it never widens to all companies because the snapshot is a set.
- **AC-C6** Job status, cancel and the download link are owner-only (the job's `user_id` must equal the caller), as the existing job endpoints already enforce.
- **AC-C7** The download link is a signed storage URL with a 1 h expiry; no session token or API key ever appears in a URL.

## D. Job, progress, never stuck

- **AC-D1** (Q2a) Starting a download creates an `import_jobs` row (`job_type = attachment_bulk_download`) and enqueues a worker job on the `imports` queue; the response returns `job_id` within the request, before any file is read.
- **AC-D2** Progress: `total` = planned file count, `processed` advances as files are written, `percentage` derived; status polls at 2 s show it moving.
- **AC-D3** The job ends in exactly one terminal state: `finished` with `result = {archive_filename, files_included, files_failed, files_not_visible, folders, bytes}` and the archive stored under `bulk-exports/<job id>.zip`, or `failed` with a one-line `error`. No third state; the temp file on the worker is removed in both cases.
- **AC-D4** A file whose bytes cannot be read from storage is skipped, counted in `files_failed`, and the job still finishes.
- **AC-D5** `files_included = 0` (every planned file failed) ends as `failed` with "No file could be read", not as an empty archive.
- **AC-D6** Cancel while running (existing `POST /system/jobs/{id}/cancel`) stops the job within one batch; the archive is not uploaded and the temp file is removed.
- **AC-D7** (Q5a) No size cap. The archive byte size is reported in `result.bytes` (the Integer `source_file_size` column is not used for it).
- **AC-D8** Dialog: opening it starts the job at once and shows "Preparing archive" with files done / total and a bar; a terminal state replaces the spinner. `finished` shows the summary (files, folders, size, "N files not included (outside your companies)" only when N > 0, "N files could not be read" only when N > 0) and a primary "Download ZIP" button that opens the signed URL. `failed` shows the error and Close. The dialog can be closed while running; the job continues.
- **AC-D9** The dialog never shows a spinner forever: a poll error is retried on the next tick, and a job that leaves the queue without a result (RQ lost it) is surfaced by the existing status sync as `failed`.

## E. Verification

- **AC-E1** pytest: planning rules (B1-B7), scope (C2-C5), endpoint (C1, C4, D1), task end to end against a fake storage backend (D3-D6) with a real zip read back.
- **AC-E2** vitest: `driveBulkActions` gating (A1-A4); the dialog's processing / finished / failed renders and the Download button (D8).
- **AC-E3** Browser pass on the crew test copy at 1280 and 375: Files > Marketing, select folders + a file, Download selected, archive opens with the expected tree; Export selected still disabled with a folder in the selection.
