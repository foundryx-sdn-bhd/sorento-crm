# UAC: never-stuck safety nets (L6, L8, L9)

Plan: `PLAN-ns-safety-nets.md`.

- AC-1 (L8, row 33): if the app's client bundle fails to load after a deploy, the user sees
  "Something went wrong" with a Reload button, never a blank page or an endless skeleton.
- AC-2 (L8, row 40): a render error on sign-in, a customer link (`/c/...`) or an unsubscribe
  page shows a fixed-copy error screen with Try again, never bare "Application error".
- AC-3 (L8): a chunk-load error anywhere offers Reload (full page load), because Try again
  cannot fetch a build that no longer exists.
- AC-4 (L6, row 34): if loading the user's permissions fails, a guarded page shows "Could not
  check your access" with Retry, never "You don't have access to this page". Retry refetches,
  and on success the page renders.
- AC-5 (L6): while the permissions query is failed, the protected shell shows one banner with
  Retry. A user who really lacks a permission still sees AccessDenied.
- AC-6 (L9, row 11): a user detail page whose record fails to load (non-404) shows an error
  with Retry in place of the tabs, never skeletons; a 403 shows AccessDenied; a 404 leaves for
  the users list once (replace, not push).
- AC-7 (L9, row 12): if system settings fail to load, no settings tab renders and no Save
  button exists; the user sees an error with Retry. Nothing can save blank defaults.
- AC-8 (L9, row 15): a contact detail page whose record fails to load shows an error with
  Retry (or AccessDenied for a 403), never an endless skeleton or a false "not found".
