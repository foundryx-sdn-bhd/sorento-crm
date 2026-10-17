# UAC - Files page folder navigation in the URL

Plan: `PLAN-files-url-nav.md`. Final: behaviour card answered 17 Oct (1a 2a 3a 4a 5a).

- AC-1 Opening a folder (tree, double/row click in the right pane, breadcrumb, mobile drawer) changes the URL to `?directoryId=<id>`; All files has no `directoryId`.
- AC-2 Browser Back after opening a folder shows the parent folder (or All files), still on the Files page. Forward re-opens the child.
- AC-3 Reload keeps the same folder open, with the tree expanded to it and the breadcrumb showing the full path.
- AC-4 Pasting the folder URL in a new tab opens that folder.
- AC-5 Breadcrumb always matches the URL; clicking a crumb navigates and adds a history entry.
- AC-6 A folder URL for a folder that is deleted, in Trash, outside the viewer's companies or unknown shows a clear "Folder not available" state with the way back to All files; never a spinner or a silent empty folder.
- AC-7 A folder moved elsewhere keeps its URL (the id is stable); the breadcrumb shows the new path.
- AC-8 Deleting the folder you are in returns you to its parent without adding a Back step into the deleted folder.
- AC-9 Changing folder by any route (including Back/Forward) clears the search box, returns to page 1 and clears the row selection, same as a drill click today.
- AC-10 Other query params (e.g. `attachment_id` deep link) survive folder navigation where they still apply.
- AC-11 (crew audit 17 Oct) Open A > B > C, reload, Back, Back, open D, Back: A is shown with its contents and breadcrumb; the page never stays on a loading skeleton. Replay: `evidence/files-url-nav-replay.sh`.
- AC-12 (crew audit 17 Oct) After a reload into a folder deep in a long tree, its tree row is highlighted and scrolled into view.
