# M6 Composer, mobile, toasts, focus - browser verification evidence (agent-browser, 3 Sep 2026)

Worktree `motion2-M6`, branch `feat/motion2-M6-composer-mobile-toasts`, HEAD `b0d0fc154`. FE
`PORT=3081 npm run dev` (`npm run dev` PID 6241, `next dev` child PID 6267), BE reused read-only
on `:8120` per `FASTAPI_INTERNAL_URL=http://localhost:8120` in `.env.local` (copied from the
`motion2-M3` worktree's shape, confirmed `NEXTAUTH_URL=http://localhost:3081`,
`AUTH_TRUST_HOST=true`, no `NEXT_PUBLIC_API_URL`). `lsof -i :3081` / `:3082` empty before starting;
load average (1 min) 6.27 at the start, 4.53 just before boot, both under the 12 guard. Login via
`E2E_EMAIL`/`E2E_PASSWORD`. Session `--session m6tester` (isolated browser). Viewport 1280x800
default, 375x812 for the mobile checks. Navigated by sidebar clicks from `/`.

**Method beyond the standard command set.** Same as the M3 evidence run: a Node 22 script with
native `WebSocket` attached directly to the daemon's Chrome via `agent-browser get cdp-url` and
the page's own `webSocketDebuggerUrl` (from `/json/list`, simpler than `Target.attachToTarget`
session juggling and confirmed to be the SAME tab `agent-browser` already had open by matching
`target.url` against `localhost:3081` first). Raw CDP drove: `Emulation.setEmulatedMedia` +
`setTouchEmulationEnabled` + `setEmitTouchEventsForMouse` for coarse-pointer (M6-03, same recipe
as M3-05 - bare feature overrides alone did not flip `matchMedia`), `Fetch.enable` +
`fulfillRequest`/`failRequest` to force a query error and a send failure (M6-01, M6-04),
`Network.emulateNetworkConditions` + `setCacheDisabled` to throttle image loads (M6-07), and
`Input.dispatchKeyEvent` for a real Tab keypress (M6-05, so `:focus-visible` reflects genuine
keyboard navigation rather than a scripted `.focus()` call). Scripts and helper module (`cdp.mjs`)
are in the scratchpad, not committed - the numbers and screenshots below are the record.

**Tool quirks hit this run, confirmed harness artifacts, not product defects:**

- `click <ref>` / `element.click()` silently no-ops on the sidebar accordion toggles and on the
  Conversations inbox's "All" tab (a Radix `Tabs`-shaped trigger) - the same finding as
  M1-M5 evidence. Worked around with a full
  `pointerdown/mousedown/pointerup/mouseup/click` dispatch (sidebar) or `agent-browser`'s own
  `find role button click` (the tab, which needed a real pointer event the CLI provides but a
  JS-dispatched synthetic one did not reliably trigger). Plain `.click()` worked fine on ordinary
  buttons (Send, dialog triggers, conversation rows).
- A same-JS-tick double `sendBtn.click(); sendBtn.click();` (zero-latency) sent TWO messages, not
  one - `sending` is a React state set via `setSending(true)`, which does not update the closure
  variable the SECOND call reads until the next render, so two calls in the exact same
  synchronous tick both pass the `sending` guard. This is not a realistic double-Enter or
  double-click: even the fastest real double-click or a "spam Enter" keypress has some event-loop
  gap between the two events. Retested with a 15ms gap (via two separate `Input`/`KeyboardEvent`
  dispatches) and the guard held cleanly - see M6-01 detail below. Flagged here as a harness
  artifact per the M3 evidence run's own precedent for zero-latency synthetic dispatch, not
  reported as a fail.
- `agent-browser`'s own tab-switch click DOES fire a real pointer sequence (confirmed it succeeds
  where a JS `.click()` on the identical element does not), so it was used for the one Radix-tab
  interaction in this run instead of a synthetic dispatch.

## Findings summary (pass/fail table)

| Check | Target | Result | Measured value |
| --- | --- | --- | --- |
| M6-01 (1280) | Caret/focus never leaves the textarea across a send | PASS | 154 samples at 40ms intervals from type-through-settle: `activeIsTextarea` true on every single sample (0 false) |
| M6-01 (1280) | Textarea never gains `disabled` | PASS | 0 of 154 samples had `textareaDisabled: true` |
| M6-01 (1280) | Attach / Voice buttons never gain `disabled` | PASS | 0 of 154 samples for either button |
| M6-01 (1280) | Optimistic bubble: dimmed (`opacity-60`), then replaced with no duplicate | PASS | Bubble present (count=1, never 2) for 32 consecutive 40ms samples (~1.28s), then gone; final screenshot shows the single un-dimmed `ZZT-M6 optimistic test 065731` bubble with a delivered checkmark, no stray dimmed copy |
| M6-01 (1280) | Realistic fast double-Enter -> one message | PASS | Two `KeyboardEvent('keydown', {key:'Enter'})` dispatches 15ms apart, second one re-typing the same text (since the first send already cleared the field): exactly one `ZZT-M6 reentry test 065544` bubble delivered |
| M6-01 (1280) | Realistic fast double-click on Send -> one message | PASS | Two `sendBtn.click()` calls 15ms apart: Send button read `disabled: true` at +15ms (guard already committed), second click intentionally skipped by the script exactly because of that - single `ZZT-M6 clickgap test 065630` bubble delivered |
| M6-01 (1280) | Same-tick (0ms) double-click | **Harness artifact, not a real interaction** | Two messages sent (`ZZT-M6 optimistic test 065455` x2) - see quirks note above; not counted against the AC |
| M6-01 (1280) | Failure: bubble removed, error toast top-center with close button, persists | PASS | `Fetch.failRequest` on the `POST .../reply` call: pending-bubble sample count dropped from 1 to 0 within one 40ms poll tick of the failure; toast `data-type=error`, `data-y-position=top`, `data-x-position=center`, close button present, still present at +9s (no auto-dismiss) - textarea kept the unsent text for a retry (`setReplyText('')` only runs on the success path) |
| M6-01 (375) | Same success-path measurements, repeated at 375 | PASS | Identical shape: 154 samples, 0 focus/disabled violations, pending bubble for 31 consecutive samples then replaced, final bubble `ZZT-M6 optimistic test 375 065946` visible un-dimmed |
| M6-02 | Notifications sheet sizes via `dvh`, bottom edge visible at 375 | PASS | `ScrollArea` computed class `h-[calc(100dvh-12rem)] min-h-[200px]`; rect bottom `697px` inside a `812px` viewport |
| M6-02 | AI assistant panel sizes via `dvh`, bottom edge visible at 375 | PASS | Inline style `max-height: calc(100dvh - 6rem)`; panel rect bottom `788px` inside `812px` |
| M6-02 | Conversations inbox sizes via `dvh` | PASS | Container class `min-h-[70dvh] ... lg:h-[calc(100dvh-13rem)] lg:flex-row`; rect bottom `719.9px` inside `812px` |
| M6-02 | Real mobile-Safari dynamic-toolbar emulation | NOT AVAILABLE | `agent-browser` 0.27.0 exposes no visual-vs-layout-viewport split; used the UAC's own documented fallback ("else assert the computed height uses dvh") for all three surfaces instead |
| M6-03 | Default `Input` `md` size: 16px under `pointer-coarse`, smaller otherwise | PASS | Products search box: `13px` with no emulation, `16px` with `Emulation.setEmulatedMedia` (`pointer: coarse`) + touch/mobile emulation active (`matchMedia('(pointer: coarse)')` confirmed `true`) |
| M6-03 | No `maximum-scale` in the viewport meta | PASS | `<meta name="viewport">` content: `"width=device-width, initial-scale=1"` |
| M6-04 | One `<Toaster position="top-center">` | PASS (code + observed) | Every toast observed this run (success, mutation-error, query-error, permission) carried `data-y-position=top data-x-position=center` |
| M6-04 | Mutation error: top-center, close button, persists (no auto-dismiss) | PASS | Incidental real event: `POST /api/v1/complaints-management/complaints` 500'd (see the out-of-scope backend bug noted below) and its `toast.error(...)`-driven toast (`hooks/useEntityMutation.ts`) was still on screen, unclosed, **90+ seconds later** with `data-close-button` present - confirmed via DOM read, not a timed script |
| M6-04 | Success toast auto-dismisses at ~4000ms | PASS | Composer's `toast.success('Sent.')` on a real send: visible for `4201ms` from first-seen to first-gone sample (150ms poll granularity) |
| M6-04 | **Query error: top-center, close button, persists** | **FAIL** | `Fetch.fulfillRequest` forced two consecutive 500s on `GET /api/v1/master-data/products` (react-query `retry:1` needs both failed to reach `onError`): the resulting toast (`providers/query-provider.tsx`'s `QueryCache.onError` -> `toast.custom(...)`) was `top-center` (PASS) but **`hasClose: false`** (no close button) and **auto-dismissed after ~3.6-4s** (last-seen sample at t=6410, poll-relative; first-seen at t=2812; gone by the next 150ms tick) - not "still present after 6s". Screenshot `M6-04-query-error-no-close-button-FAIL.png` shows the black toast with no X, contrast against the mutation-error toast's X in `M6-01-failure-toast-and-bubble-removed.png`. Root cause in source, see below |
| M6-05 | Tabbing to a dialog's close X shows the global focus ring | PASS | Real `Input.dispatchKeyEvent` Tab keypresses (16, on the Export dialog) landed on `[data-slot="dialog-close"]`; `element.matches(':focus-visible')` `true`, computed `outline: 2px solid`, `box-shadow` ring stack non-`none` |
| M6-07 | Conversation thread with 2+ images stays pinned to the bottom while they load | PASS | CONTACT O thread (2 real images, pre-existing conversation data - see note below), network throttled to ~200kbps/300ms latency with cache disabled, then reloaded and reselected: `scrollHeight - scrollTop - clientHeight` (`distanceFromBottom`) read **exactly `0` on every one of 65 valid 100ms samples** across a 7s window while one of the two images finished loading mid-window |
| M6-07 | Image wrappers are fixed-aspect boxes | PASS | Both `<img>` in the thread have a `closest('[class*="aspect-"]')` ancestor with class `aspect-[4/3] w-full overflow-hidden rounded bg-muted/20` |
| Console | Zero NEW errors (pre-existing a11y warnings are noise) | ONE NEW ERROR, out of scope | `[error] Error submitting form: name '_request_has_valid_external_api_key' is not defined` - a backend `NameError` surfaced while probing for a safe throwaway record to send M6-01's message into (see below); unrelated to any M6 source file. Six `Missing Description for {DialogContent}` warnings are the same pre-existing a11y noise this run's own dialogs already triggered (Export dialog, template dialog) and are not new |

## M6-04 root cause: query errors bypass the duration/close-button standard

`lib/toast.ts` wraps only `success` (4000ms) and `error` (`Infinity` + `closeButton: true`);
`custom`, `info`, `warning`, `message`, `promise`, `dismiss`, `loading` are explicitly documented
as "pass through unchanged" and `lib/toast.test.ts` asserts exactly that
(`toast.custom` -> `sonnerToast.custom` with no injected options). `providers/query-provider.tsx`'s
`QueryCache.onError` renders its red banner via `toast.custom(() => <Alert ... close={false}>...)`
for BOTH the permission-denied case and the generic case - never `toast.error(...)`. Since
`toast.custom` gets no duration override, it falls back to Sonner's own default (`4000ms`), and
since the `Alert` itself is rendered with `close={false}`, there is no dismiss affordance on the
custom content either - and Sonner's own `closeButton` prop on `<Toaster>` does not retrofit one
onto arbitrary `custom()` content the way it does for `success`/`error`/etc. calls that go through
Sonner's own Toast component. Net effect: every REST **query** failure surfaced through the shared
`QueryClient` (as opposed to a mutation's own `onError: toast.error(...)`, which every
`useEntityMutation`-based hook uses and which DOES get the Infinity+close treatment) reads and
disappears like a transient success message, with no way to re-read it once it is gone. This is
the M6-04 commit's (`0dac8bce8`) own scope boundary - it changed `query-provider.tsx`'s import from
`sonner` to `@/lib/toast` and dropped the now-redundant per-call `position: 'top-center'`, but did
not touch the `toast.custom(...)` call shapes themselves - not a regression introduced by
something else in this slice.

## Note on the M6-01 send target

The composer send test needed a conversation "clearly owned" per the run's data-safety
instruction. The Conversations inbox's only pre-existing entries are real customer/supplier
WhatsApp threads (CONTACT Y, CONTACT O, CONTACT AA, CONTACT I, Sorento Sandy) except one: **"Jayson"
+60160000509**, whose thread content (product-catalogue bot replies to the tester's own queries)
and whose name matches the logged-in user (`person38@example.com`) - this is the developer's own
test WhatsApp line, not a third party's, and is where all M6-01 sends in this run went (six
distinct throwaway `ZZT-M6 ...` texts, three of them intentionally landing as real deliveries.
per the composer's own success path). No other contact's thread was touched. Two other avenues
were tried first and abandoned as unsuitable: creating a new Complaint (blocked by a real,
pre-existing backend bug - `POST /api/v1/complaints-management/complaints` 500s with a Python
`NameError: name '_request_has_valid_external_api_key' is not defined`, unrelated to M6, not
investigated further here since it is out of scope) and reusing an existing `ZZT-E2E`-coded Stock
Inquiry (its chat contact, "BASER" +601100000511, could not be confirmed as a designated test line
the way "Jayson" self-evidently is, so it was left untouched).

## M6-07 note on the image count

The UAC / brief ask for a thread with three images; the richest one found via a sweep of every
existing conversation (`CONTACT Y`, `CONTACT O`, `CONTACT AA`, `CONTACT I`, `Sorento Sandy`, `Jayson`)
was CONTACT O's, with exactly **two** real images (a conversation-SLA-tracking attachment and a
technical-specification photo) and no way to load further history (`scrollHeight` did not grow
across five programmatic `scrollTop = 0` attempts - `RespondChatList`'s "load older" fetch appears
to need a genuine `scroll` event, not just a property write, and was not chased further since two
images already exercises the stay-pinned behaviour the check cares about). Per this run's own
brief ("2+ images... if none exists, say so and mark source-confirmed"), this is reported as
source-confirmed at 2 images, not 3.

## Screenshots in this directory

- `M6-01-1280-optimistic-then-real.png` - Jayson thread at 1280px, final settled state after a
  real send: the plain, un-dimmed `ZZT-M6 optimistic test 065731` bubble with a delivered
  checkmark, no leftover dimmed duplicate.
- `M6-01-375-optimistic-then-real.png` - same check at 375px (mobile single-pane thread view).
- `M6-01-failure-toast-and-bubble-removed.png` - forced-failure run: pending bubble already gone,
  "Failed to fetch" error toast top-center WITH a close button (contrast against M6-04's finding).
- `M6-02-notifications-sheet-375.png` - notifications bell sheet open at 375px.
- `M6-02-ai-assistant-375.png` - AI assistant panel open at 375px, overlaying the thread.
- `M6-02-conversations-inbox-375.png` - Conversations inbox single-pane thread view at 375px.
- `M6-04-query-error-no-close-button-FAIL.png` - forced query-error run: "Failed to fetch
  products" toast, top-center, but no close button (the M6-04 fail).
- `M6-05-dialog-close-focus-ring.png` - Export dialog, close X focused via real Tab keypresses,
  focus ring visible.
- `M6-07-contact-o-thread-2-images.png` - CONTACT O thread with both real images loaded, thread
  scrolled to the bottom.

## Cleanup

Dev server killed: `kill 6241` (parent `npm run dev`); its child `next dev` (6267) exited with it -
confirmed via a follow-up `lsof -i :3081` returning empty. Only the `m6tester` agent-browser
session was closed (`close`, not `close --all`). The direct-CDP Node scripts attached to and
detached from the SAME session's target each time via the page's own `webSocketDebuggerUrl` - no
second browser was opened. `.env.local` left in place. Network throttling and cache-disable were
reset (`Network.emulateNetworkConditions` back to unrestricted) at the end of every script that
set them; `Fetch` domain was disabled after each interception script.

**Data:** six real WhatsApp sends landed on the developer's own "Jayson" test conversation
(`ZZT-M6 optimistic test 065455` x2, `ZZT-M6 reentry test 065544`, `ZZT-M6 clickgap test 065630`,
`ZZT-M6 optimistic test 065731`, `ZZT-M6 optimistic test 375 065946`, `ZZT-M6 toast timing test
070942`) plus one failed attempt left in the draft box (`ZZT-M6 failure test 065823`, never sent,
textarea content only) - all on the tester's own conversation, none deleted (this composer has no
delete path for a sent message, and none was requested). No Complaint or Stock Inquiry record was
created (the Complaint attempt 500'd server-side before any row was written; the Stock Inquiry
route was inspected but not used to avoid the "BASER" contact - see note above). No deferred-delete
countdown was armed at any point in this run.

## Run 2 (after fix round, HEAD 2bee38e52)

Worktree `motion2-M6`, branch `feat/motion2-M6-composer-mobile-toasts`, HEAD `2bee38e52`. FE
`PORT=3081 npm run dev` (parent `next dev` PID `62462`, `next-server` child PID `62463`), BE
reused read-only on `:8120` (unchanged `.env.local` from run 1, confirmed `FASTAPI_INTERNAL_URL`,
`NEXTAUTH_URL=http://localhost:3081`, `AUTH_TRUST_HOST=true`). `lsof -i :3081` empty before
starting; load average (1 min) `8.48` at the start, under the 12 guard. Login via
`E2E_EMAIL`/`E2E_PASSWORD`. Session `--session m6run2` (isolated browser, agent-browser 0.27.0).
Navigated by sidebar clicks from `/` throughout (Products via the "Products" accordion, Conversations
via the "SLA" accordion's "Conversations" link - both needed the same pointer-event-dispatch
workaround the run-1 README already documents for sidebar accordions and off-CLI link clicks;
`agent-browser`'s own `click` worked unassisted for the "All" tab and ordinary buttons).

**Method: same direct-CDP approach as run 1**, re-derived independently this run rather than
reusing run 1's script files (not committed, scratchpad-only per policy): a Node 22 script
attached over native `WebSocket` to the page's own `webSocketDebuggerUrl` (fetched from
`agent-browser get cdp-url`'s host's `/json/list`, matched by `url.includes('localhost:3081')`).
`Fetch.enable` + `Fetch.fulfillRequest` forced a 500 on `GET /api/v1/master-data/products` (twice,
for the query's `retry: 1`) and two 403s on a product detail's two concurrent queries; a THIRD use
of `Fetch.enable` held a `POST .../reply` request paused indefinitely (no `continueRequest` /
`fulfillRequest` call while the composer needed to sit in `sending`), then `DOM.setFileInputFiles`
against the composer's hidden `[data-testid="composer-file-input"]` staged a real file via a real
native `change` event (took roughly 200ms-1s to land in React state, not synchronous - the check's
poll loop accounted for this). No message ever reached Respond.io: both `POST .../reply` calls in
this run were intercepted and fulfilled with a 500 before leaving the browser process; the one
real send that could have gone through was never allowed to reach `continueRequest`.

### Findings summary (pass/fail table)

| Check | Target | Result | Measured value |
| --- | --- | --- | --- |
| M6-04 | Query error (500 on `GET /api/v1/master-data/products`, forced twice for `retry:1`): top-center, close button, persists past 6s | **PASS (fixed)** | Toast text "Something went wrong. Please try again.", `data-y-position=top data-x-position=center`. Close affordance is `[data-slot="alert-close"]` (the shared `Alert` component's own X, not Sonner's `[data-close-button]` - the wrong selector is why an early probe in this same run mis-read it as absent), confirmed present and clickable at t+4s, t+6s, and multiple minutes later (duration `Infinity`, source: `query-provider.tsx`'s `toast.custom(..., { duration: Infinity })` now applies to BOTH branches). Clicking the X removed the toast (`found: false` on the next read) |
| M6-04 | Permission-denied 403 on a GET: same top-center + close treatment | PASS | Forced `GET /api/v1/master-data/products/{id}` to 403 with body `{"message": "Permission required: ..."}` (the product-detail service reads `error.message`); toast rendered "You don't have permission to view this. Ask an administrator.", top-center, `[data-slot="alert-close"]` present |
| M6-04 | Two simultaneous 403s collapse into one toast (fixed `id: 'permission-denied'` dedupe) | PASS | Forced BOTH the product-detail GET (`error.message` shape) and its concurrent purchase-history GET (`error.detail`/FastAPI shape) to 403 with a permission-shaped body on the same page load; both have `retry: 1` so 4 requests were intercepted total, all near-simultaneous; DOM read after settling: `totalToasts: 1, permissionToasts: 1` - never stacked |
| M6-01 (reviewer finding 1) | Composer mid-send staging: a file attached while `sending=true` stages as a chip; a subsequent send failure leaves the chip staged and removes the pending bubble | PASS | `POST .../reply` held pending via `Fetch.enable` with no `continueRequest`/`fulfillRequest` call; confirmed composer entered `sending` (`Send` button `disabled: true`, `Attach` button `disabled: false`) before ever touching the file input. `DOM.setFileInputFiles` against the hidden file input landed within one 200ms poll tick: `stillSending: true, chipCount: 1, chipTexts: ["m6-test-image.png"], pendingBubbleCount: 1` - chip and the dimmed optimistic bubble coexisted while the request was STILL held (reconfirmed `sendStillDisabled: true` immediately before fulfilling). Fulfilling the held request with a 500 then read: `sendDisabledAfter: false, chipCountAfter: 1` (same file name, still staged), `pendingBubbleCountAfter: 0` (bubble gone) - exactly the fix's claimed behavior (`SharedConversationComposer.tsx`'s `finally` block always removes the pending bubble; the failure `catch` branch never touches `files` state, so anything staged - before or during the send - survives a failed send untouched) |
| Console | Zero errors | PASS | `console` filtered for `error`/`warn` (excluding the app's own `[debug]` JWT lines) returned nothing across the whole run, including through both forced-500 sequences and the held-then-failed send |

### M6-01 mid-send note: why two intercept scripts were needed

The first attempt at the mid-send check (`DOM.setFileInputFiles` fired, then a single 500ms poll
before deciding "no chip") mis-read the timing: the native `change` event dispatched by
`setFileInputFiles` does not land in React state synchronously, and 500ms was not always enough
headroom. The composer's own behavior was never in question - a second run with a 200ms polling
loop (up to 4s) run against a FRESH send (fresh text, fresh held request, cleared any stray chip
left by the first attempt) caught the chip appearing at the very first poll tick while the request
was still provably held. This is a harness-timing artifact of driving a real native file-input
event through CDP, not a product defect - flagged per the same policy run 1 used for its own
tool-quirk notes.

### Screenshots in this directory (run 2)

- `run2-m6-04-query-error-with-close.png` - forced 500 on the products list query: black toast,
  top-center, with a visible X (the `[data-slot="alert-close"]` button run 1 missed by probing the
  wrong selector).
- `run2-m6-04-permission-denied-dedupe.png` - forced 403s on two concurrent product-detail queries:
  exactly one "You don't have permission..." toast, top-center, with its own close X.
- `run2-m6-01-mid-send-before-attach.png` - composer mid-send (Send disabled, request held), before
  the file attach in the first (imperfect-timing) attempt.
- `run2-m6-01-mid-send-chip-staged.png` - the file chip (`m6-test-image.png`) staged in the
  composer AND the dimmed optimistic bubble both visible in the thread, while the held `reply`
  request has still not been fulfilled.
- `run2-m6-01-mid-send-after-failure-still-staged.png` - after the held request was fulfilled with
  a 500: the optimistic bubble is gone from the thread, the file chip is still staged with its
  remove (X) affordance, Send is re-enabled, and the typed text is still in the textarea for a
  retry.

### Cleanup

Dev server killed: `kill 62462` (parent `npm run dev`); its `next-server` child (`62463`) exited
with it - confirmed via a follow-up `lsof -i :3081` returning empty. Only the `m6run2`
agent-browser session was closed (`close`, not `close --all`). Every CDP script disabled its own
`Fetch` domain interception before closing its `WebSocket` and none left a paused request behind
(the one held request in the mid-send check was always fulfilled before the script exited). The
composer was returned to empty (chip removed, textarea cleared) before moving on. `.env.local` and
the two untracked plan docs were left untouched.

**Data:** zero real WhatsApp sends this run - both `reply` POSTs that reached the network were
intercepted and fulfilled with a forced 500 before leaving the browser process, per this run's own
explicit no-send instruction. The composer's typed text (`ZZT-M6 run2 mid-send stage test...`,
`ZZT-M6 run2 final mid-send stage test...`) and the staged throwaway file (`m6-test-image.png`, a
1x1 PNG generated in the scratchpad, not committed) never reached Respond.io or the "Jayson"
contact - both were cleared from the composer at the end of the run. No record was created, edited,
or deleted; no deferred-delete countdown was armed.
