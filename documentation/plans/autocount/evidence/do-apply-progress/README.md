# DO-APPLY-PROGRESS browser evidence (1 Oct 2026)

agent-browser 0.27.0, headless Chromium (`/opt/pw-browsers/chromium-1194`), session `doap`, in
the lane's cloud sandbox. Backend on :8000 against a private database `sorento_browse` (built by
`scripts.bootstrap_env`, `full_suite` bundle installed), frontend `npm run dev` on :3000.
Superadmin `do.admin@sorentocrm.dev`, no business data beyond what the run seeds.

## The run

The REAL `apply_autocount_pull` task ran in its own process (own DB connection, like the RQ
worker) with FoundryX replaced by the test suite's in-process fake (`_FakeFoundryX`; no network,
no AutoCount call of any kind). 3,000 synthetic delivery orders cloned from
`tests/fixtures/autocount/do_live_sample.json`, every 250th naming an unknown product
(retryable), plus one tracking-upload row numbered `ZZDO-0001` (adopted by number, one line
deleted). Each document slowed by 30 ms so the page could be watched: the run took 124.5 s.

Mid-run DB read (separate connection): `started|3000|200|200|0|0`, so the publisher's fresh
session commits while the apply's own transaction is open, with no lock wait.

## Steps

1. `/` > sidebar System > Operations > Import Jobs (`get url` = `/system-management/import-jobs`).
   The row read `autocount_delivery_orders_apply  STARTED  3000  500 / 3000 (17%)`.
2. Clicked the row. Job Summary: Total Rows 3000, Processed 800 / 3000, progress 26%; Results
   Successful 797, Failed 3, Skipped 0 (shot 01, 1280).
3. Viewport 375, no reload: Total Rows 3000, Processed 1200 / 3000, Successful 1196, Failed 4;
   `scrollWidth` 375.
4. Waited for the run to end, no reload: the page flipped to FINISHED and Results read
   Created 2987, Adopted 1, Updated 0, Unchanged 0, Failed 0, Retryable 12, Lines deleted 1
   (shot 02, 375; `scrollWidth` 360). Job row: `finished|3000|3000|2988|12|0`.
5. 1280 after reload: same counts; the Rows card reads 3,000 matching.

Shot 02 was taken before the fix round that also refetches the Rows card at finish; its
"0 matching" is the mid-run cache that fix removes.

## Not covered here

- A real FoundryX snapshot of the dev book (the owner's hand test covers it).
- The "Outcome breakdown" card says the job "ran before per-row outcome capture existed" for
  every AutoCount apply job, on main too: the apply never writes `job.result`'s breakdown
  envelope. Pre-existing, out of this lane's scope, raised as a follow-up.
