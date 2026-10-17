# PLAN - Files page: every folder has its own URL

Status: **Built; crew audit round 2 (17 Oct) fixed: reload -> Back -> Back -> open -> Back stuck skeleton (native pushState wrote stale Next router trees) and tree scroll-into-view. Reviewer READY on ccb75637 (+ should-fixes taken in 3bf9b4f9). Sandbox pass PASS at 1280 and 375. Awaiting owner hand test on PR #1759. Track: small fix (frontend only, no migration, no auth/RBAC change, no new ingest).**
UAC: `documentation/plans/resources/files-url-nav-acceptance-criteria.md`
Lane: FILES-URL-NAV, base `origin/main` 20c191d8.

## Owner request (17 Oct)

Clicking into a folder and then pressing browser Back leaves the Files page, because folder
navigation is not URL based. Make it work like Google Drive.

## 0. What exists (measured, cited)

Paths below are under `sorento_crm_frontend/app/(protected)/resource-management/attachment-directories/`.

| Piece | Where | Today |
|---|---|---|
| Page reads `?directoryId=` ONCE | `page.tsx:12-18` | server reads it and passes `initialDirectoryId` |
| Folder state | `components/AttachmentDirectoriesView.tsx:278` | `useState(initialDirectoryId)`, so every later click changes state only, never the URL |
| Tree click | `components/DirectoryTreeSidebar.tsx:268`, root row `:602`, delete-current `:535` | `onSelect(id)` -> `setSelectedId` |
| Right-pane drill + breadcrumb | `components/AttachmentsInFolderPanel.tsx:427-437` (`navigateToFolder`), breadcrumb `:794` (`buildBreadcrumb`, `drivePath.ts:14`) | resets search, page, selection, then `onSelectFolder` |
| Tree auto-expands to the selected folder | `components/DirectoryTreeSidebar.tsx:445-467` | already derived from `selectedId`, so a reload into a deep folder opens the tree path for free |
| Existing `?directoryId=` links | quick access pins `DirectoryTreeSidebar.tsx:379-381` + `quick-access-block.tsx:70`, attachment detail `attachments/components/AttachmentDetail.tsx:319,377`, `AttachmentDetailModal.tsx:1043`, product + promotion attachment tabs | all already write `?directoryId=<id>` |
| URL-state precedent | `hooks/useUrlTab.ts` | reads `useSearchParams`, writes with the router, copies every other param verbatim |
| Unknown / unreadable folder id | backend `resources_service.py:1078` `list_drive_contents` | returns an EMPTY 200 listing, no error, so today a bad link shows an empty folder with only the root crumb |
| View mode | `components/useDriveViewMode.ts` | localStorage, per user |
| Search / sort / page | `AttachmentsInFolderPanel.tsx:143-158` | component state |

## 1. Design (card answered 17 Oct: 1a 2a 3a 4a 5a)

- URL shape unchanged: `/resource-management/attachment-directories?directoryId=<folder id>`;
  root = no param. Every existing link and quick-access pin keeps working.
- `AttachmentDirectoriesView` derives `selectedId` from `useSearchParams()` instead of
  `useState` (`useFolderUrl.ts`). Selecting a folder (tree, drill, breadcrumb, mobile drawer)
  navigates with `router.push` / `router.replace`, copying every other query param.
- NOT native `history.pushState` (tried in round 1 for speed, reverted after the crew audit):
  Next 15.5's patched pushState copies the current entry's router tree into each new entry;
  after a reload those trees name page segments the new document never loaded, and Back
  onto one left the page on the loading skeleton for good.
- The router round trip (about 1s on dev) is hidden by a `pending` folder shown at once and
  dropped as soon as the URL moves (its own navigation landing, Back / Forward, or a newer
  navigation that discarded it).
- The tree scrolls the open folder's row into view (`block: 'nearest'`). Deleting the folder you are in
  `router.replace`s to its parent so Back never lands on a deleted folder.
- The panel resets search / page / selection whenever the folder changes from ANY source
  (Back/Forward included), not only on a drill click.
- Folder id not in the loaded tree (deleted, moved to Trash, outside your companies, or a
  typo): the panel shows a "Folder not available" state instead of an empty listing; the
  breadcrumb root crumb returns to All files. While the tree is loading, the normal skeleton.
- Search, sort and view mode stay out of the URL in this lane.

## 2. Tests (red first)

- `useFolderUrl` / View: selecting a folder pushes `?directoryId=`; root removes it; other
  params survive; selectedId follows a URL change (Back/Forward simulated by changing the
  search params).
- Panel: folder change via prop (not a click) clears the search box and resets page.
- Unknown folder id with the tree loaded renders the not-available state, not the grid.

## 3. Verification

Browser pass at 1280 and 375 on seeded folders 3 deep: drill, Back, Forward, reload, paste in a
new tab, breadcrumb clicks.
