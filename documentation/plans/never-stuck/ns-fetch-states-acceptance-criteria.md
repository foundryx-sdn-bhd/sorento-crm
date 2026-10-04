# UAC: never-stuck levers L2-L5 (NS-FETCH-STATES)

Plan: `PLAN-ns-fetch-states.md`. Standard: `documentation/reference/NEVER-STUCK-UI.md`.

## L4 request deadlines

- AC-L4-1 A read the server never answers fails after 30s with "The server took too long to
  answer." and the screen shows its error state, not a skeleton.
- AC-L4-2 A write (or the AI chat) gets 120s; an export / download / PDF GET gets 120s; an
  upload gets 10 minutes; a caller can name its own budget.
- AC-L4-3 A hung `/api/auth/token` gives up after 10s; no request waits on it forever.
- AC-L4-4 A response that has started arriving is never cut off (downloads, event streams).
- AC-L4-5 Cancelling a request still reads as a cancel (AbortError), not a timeout.

## L3 refusals are final

- AC-L3-1 A 401, 403 or 404, or a timed-out request, is fetched once; no automatic retry,
  even for hooks that ask for `retry: N`.
- AC-L3-2 A 5xx keeps the hook's own retry budget.
- AC-L3-3 Every 403 shape (`Permission required:`, `One of these permissions required` with or
  without the module note, `Module not enabled:`) shows one friendly "You don't have
  permission" toast; the raw string and slug never show.

## L2 lists

- AC-L2-1 A list whose read is refused shows "You don't have access to this list" in place,
  with no Retry and no slug; never "No data".
- AC-L2-2 A list whose read fails otherwise shows the message and a Retry that refetches.
- AC-L2-3 A list that was showing rows keeps them when a background refetch fails.
- AC-L2-4 Every list named in a cleared audit row passes its query error to the grid.

## L5 pickers

- AC-L5-1 A picker whose options were refused shows "No access" on the closed control and
  "You don't have access to this list." in its menu; no Retry, no slug.
- AC-L5-2 A picker whose options failed otherwise shows "Could not load" and a Retry in its
  menu that loads them again.
- AC-L5-3 A chosen value keeps its label even when the options failed.
- AC-L5-4 The shared select hooks never turn a failure into `[]` or into the error body; the
  Users page role filter no longer throws (audit C1 / T13).
- AC-L5-5 Every picker named in a cleared audit row passes its query error.

## Kill test

Reverting any one lever's core line (the deadline timer, the retry guard, the grid's error
branch, the picker's failure branch, a hook's throw) turns its tests red.
