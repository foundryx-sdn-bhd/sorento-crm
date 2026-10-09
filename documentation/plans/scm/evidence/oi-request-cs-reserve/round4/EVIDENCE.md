# Round 4 browser evidence (AC-RS-90 rerun after the 6e.4 fix round)

agent-browser 0.27.0, session `oireserve-r4-fix` (closed by name at the end), lane stack
:3080 (FE, `npm run dev`) and :8080 (BE, `--reload`) from worktree
`sorento_crm-oi-reserve`, database `sorento_oireserve_stack` at alembic head
`oirs_0004_reserve_event_zero`. Reached from `/` by sidebar (Procurement -> Supply Chain ->
Order Inquiries), then the list's All filter and a search for `OI-001395`. Logged in with
the `E2E_EMAIL` account from `sorento_crm_frontend/.env.local`, which holds both the
requester (`acknowledge`) and the reserve permission on this stack, so one login plays
Joey and Eling.

Live OI: `OI-001395` (SO400884), four ORDER lines of 36 each at BRW-IR, none ever
requested before this walk. Grid row order: CSH2072, CSA150, CB231SS-NL, CB4702. The
three requested lines: line 1 = CSA150, line 2 = CB231SS-NL, line 3 = CB4702. Request
id `e8f390f5-4232-4b17-9e93-fb6e921f4139` (read from the request mail's own link in
`email_outbox`).

Outbox rows are never sent from the lane: `ENABLE_SCHEDULER=false`, and the query below
shows 0 of 5 rows for this OI in status `sent` (all `pending`).

| AC | what was checked | file | result |
| --- | --- | --- | --- |
| AC-RS-83b | Before any request: no `Reserve actions` column on the grid (the inquiry has no open request and no reserved / declined line) | `AC-RS-83b-no-action-column-1280.png` | PASS |
| AC-RS-90 step 1 | Joey ticks the three lines, Actions -> `Request CS to reserve`, dialog lists all three at BRW with availability, `Send request`; toast `Request #1 sent to Teh Jayson` | `AC-RS-90-1-request-three-lines-1280.png` | PASS |
| AC-RS-88 | Following `?reserve=<request id>`: State filter preselected `Request to reserve`, three amber lines, no dialog, header `Reserve` disabled | `AC-RS-90-2-reserve-link-preselected-1280.png` | PASS |
| AC-RS-84 | Tick on line 1 stages `Reserve 36 @ BRW` + Undo, nothing posted | `AC-RS-90-3-staged-two-lines-1280.png` | PASS |
| AC-RS-85b | Pencil on line 2 opens `ReserveLineForm` only after the pools loaded: Reserved prefilled 36 (`min(36, BRW 54)`), Location BRW, Stage enabled | `AC-RS-85b-form-prefilled-1280.png` | PASS |
| AC-RS-85 | Reserved 20 shows Reason and disables Stage until it is filled; staged `Reserve 20 @ BRW` | `AC-RS-85-form-short-with-reason-1280.png`, `AC-RS-90-3-staged-two-lines-1280.png` | PASS |
| AC-RS-87 | Header `Reserve (2)` posts ONE commit; toast `Reserved, Teh Jayson notified`; button greys back to `Reserve` | `AC-RS-87-commit-toast-1280.png` | PASS |
| AC-RS-90 step 2 | Filter cleared (URL loses `?reserve=`): line 1 `Reserved 36` and line 2 `Reserved 20` green with Amend + History, line 3 `Request to reserve 36` amber with tick + pencil; the action column header has no drag grip | `AC-RS-90-4-after-commit-green-amber-1280.png` | PASS |
| AC-RS-80 | The commit's outbox row names lines 1 and 2 only, reason printed, and says `1 line still to reserve.` | `AC-RS-80-outbox-partial-commit-mail.png`, query below | PASS |
| AC-RS-86 | Amend on line 2: Location locked (`BRW` text), Reserved prefilled 20 (the request row's own qty), Reason required below 36; staged `Amend to 10`, `Reserve (1)` committed, pill `Reserved 10` | `AC-RS-86-amend-form-1280.png`, `AC-RS-86-amend-form-375.png` | PASS |
| AC-RS-89 | History on line 2, newest first, in Malaysia time (recaptured on the final head): `Unreserved 10 @ BRW` 12:25 pm (reason), `Reserved 20 @ BRW` 12:23 pm (reason), `Requested 36 @ BRW` 12:20 pm | `AC-RS-89-history-reserve-then-amend-1280.png` | PASS |
| AC-RS-83b / 78c | Pencil on line 3, Reserved 0 with a reason, `Reserve (1)`: line 3 reads `Not reserved` (neutral) with Amend + History | `AC-RS-83b-declined-not-reserved-1280.png` | PASS |
| AC-RS-78b | Amend on the declined line: prefilled 0, set 15 with a reason, `Reserve (1)`: pill `Reserved 15`; History `Reserved 15`, `Reserved 0` (the Reserve 0 decision), `Requested 36` | `AC-RS-90-5-declined-amended-up-1280.png`, `AC-RS-89-history-declined-then-amended-1280.png` (recaptured, Malaysia times 12:27 / 12:25 / 12:20 pm) | PASS |
| AC-RS-88 / 88b | State options: To buy, Partly on PO/SPO, On PO/SPO, Done, Request to reserve, Reserved (no Cancelled); `Done` alone reads `No line matches the filter.` | `AC-RS-88-state-filter-1280.png`, `AC-RS-88b-no-line-matches-1280.png` | PASS |
| AC-RS-85c | Final pass (session `oireserve-r4-final`, head `f2c4c1ef3`): Joey requested CB231SS-NL's balance (request #2, 26), Eling ticked it (net 36). Amend prefills the line's net 36 with max 36 (net + remaining 0) and reads `Requested 62 across 2 requests` | `AC-RS-85c-amend-line-net-1280.png` | PASS |
| AC-RS-78d | Amend that line's net 36 -> 20 with a reason: pill `Reserved 20`; History shows `Unreserved 16` taken from request #2's link (newest first), request #1's 10 untouched | `AC-RS-78d-history-net-release-1280.png` | PASS |
| AC-RS-83c | Session `oireserve-r4-83c`, from `/` by sidebar: the reserve icons sit INSIDE the State cell right of the pill (tick + pencil on the requested CSH2072 line, request #3; pencil + history on the reserved lines), a staged chip `Reserve 36 @ BRW` + Undo in the same cell, no separate column; header `Reserve (1)` in the primary (blue) CTA colour like Confirm. The State column is at least 380 wide for a reserve holder so a saved narrower width cannot clip the icons | `AC-RS-83c-icons-in-state.png` | PASS |
| AC-RS-90 (375px) | Detail page and Lines tab usable at 375, no clipping (the grid scrolls inside its card); amend form fits, Stage/Cancel full width | `AC-RS-90-6-mobile-375.png`, `AC-RS-86-amend-form-375.png` | PASS |

## Outbox rows (`email_outbox` on `sorento_oireserve_stack`)

```
time      subject                                   status   CSA150 CB231SS-NL CB4702 still-to-reserve line
04:20:04  Reserve request: OI-001395 #1 - SO400884  pending  yes    yes        yes    -
04:23:56  Reserved: OI-001395 #1 - SO400884         pending  yes    yes        no     1 line still to reserve
04:25:02  Reserved: OI-001395 #1 - SO400884         pending  no     yes        no     1 line still to reserve
04:25:53  Reserved: OI-001395 #1 - SO400884         pending  no     no         yes    -
04:27:44  Reserved: OI-001395 #1 - SO400884         pending  no     no         yes    -
sent: 0 of 5
```

Row 2 is the `Reserve (2)` click (lines 1 and 2), row 3 the amend of line 2, row 4 the
Reserve 0 on line 3 (the click that completed the request, so no "still to reserve"
line), row 5 the amend-up of line 3.

## Found and fixed on this walk

- The action column was 90px wide, so the staged chip truncated to a single letter
  beside Undo. Widened to 240px, the chip carries a `title` (commit `d41a9fed8`); the
  capture above is after the fix.

## The Next dev "1 Issue" badge

Opened on this walk (`next-dev-issue-overlay.png`): `Console Error: Each child in a list
should have a unique "key" prop. Check the render method of Demo1Layout.` It appears on
a full load of the dashboard `/` too, not only on this page, and
`app/components/layouts/demo1/layout.tsx` is untouched by this lane (`git diff
origin/main` is empty for it). A pre-existing app-shell dev warning, not caused by this
feature; it does not show on every load, which is why some captures show the plain `N`
button instead. Not fixed here (outside the lane).

## Timestamps

Fixed in `1dd7283f6`: the reserve columns hold naive UTC, the API now sends them with a
UTC offset, and the History dialog prints them in Malaysia time
(`formatDateTimeInMalaysia`). The first walk's History captures read `4:25 AM` for a
12:25 MYT write; the two History captures above were retaken on the final head and read
`12:25 pm`. The reserve mails print dates in Malaysia time too (`7bf4f0697`).
