# ESCALATION-CONTROL browser evidence (PR #1406, per-contact design, 1 Oct 2026)

agent-browser 0.27.0, headless Chromium (`/opt/pw-browsers/chromium-1194`, via
`AGENT_BROWSER_EXECUTABLE_PATH`), session `esc`, in the lane's cloud sandbox. Stack: backend on
:8000 against a private database `sorento_browse` (built by `scripts.bootstrap_env`; the new
`esc1_0001_escalation_allowed` upgrade was run on it, converging the earlier nullable column:
2 NULL rows became true, column NOT NULL DEFAULT true), frontend `npm run dev` on :3000, the
`full_suite` module bundle installed.

Seed (no business data): superadmin `esc.admin@sorentocrm.dev`; contact "ZZT Mr Loo" holding
Sorento/Mocha/Cabana Office + Dealer + End User X access types (the owner's hand-test shape).

Every screen was reached by sidebar clicks from `/`: Users & Access > People > Internal Users >
ZZT Mr Loo > Chatbot tab, and Users & Access > Access > Contact Access Types. `get url` was read
after each navigation.

| Shot | Width | What it shows |
| --- | --- | --- |
| 01 | 1280 | Mr Loo's Chatbot card: "Chatbot hands over to support teams" ON (backfilled allowed; his dealer types do not block him), helper "When off, the chatbot tells this contact to refer to their salesman." under it (owner-approved wording, 1 Oct 2026) |
| 02 | 1280 | After unticking and a page reload: the switch reads OFF; the API read `escalation_allowed: false` |
| 03 | 375 | The same card at 375: label and helper wrap inside the row, nothing clipped, page scrollWidth 360; ticking it at 375 saved `true` (API) |
| 04 | 1280 | Edit Mocha Dealer in Contact Access Types: only "Active", no escalation setting (the access-type files match main) |

Not covered here: the Chatbot Console needs a live parser model key, which this sandbox lacks
(UNVERIFIED in a browser). The replies are pinned by the engine tests in
`sorento_crm_backend/tests/chatbot/test_escalation_control.py` (real `engine.run_turn`, parser
stubbed), including the exact owner-visible miss texts, and by the owner's hand test
(`laneboard/scripts/1406.md`).
