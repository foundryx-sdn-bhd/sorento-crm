# PLAN - Files page: every folder has its own URL

Status: **Behaviour card answered 17 Oct (build as written: 1a 2a 3a 4a 5a). Red tests committed; fix in progress. Track: small fix (frontend only, no migration, no auth/RBAC change, no new ingest).**
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

## 1. Design (proposed, pending the card)

- URL shape unchanged: `/resource-management/attachment-directories?directoryId=<folder id>`;
  root = no param. Every existing link and quick-access pin keeps working.
- `AttachmentDirectoriesView` derives `selectedId` from `useSearchParams()` instead of
  `useState`. Selecting a folder (tree, drill, breadcrumb, mobile drawer) does `router.push`,
  copying every other query param (same idiom as `useUrlTab`). Deleting the folder you are in
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
