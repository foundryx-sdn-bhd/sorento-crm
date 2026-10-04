# Browser verification pass 5, 17 Sep 2026

Stack: frontend http://localhost:3081 (dev/HMR), backend :8081, clone DB. Lane branch
`feat/chatbot-turn-rearch`. Briefed head `99b97c8fb`; actual worktree HEAD at run time was
`5828792e6` ("Merge remote-tracking branch 'origin/test/chatbot-turn-rearch-red' into
feat/chatbot-turn-rearch") - confirmed `99b97c8fb` is an ancestor of HEAD
(`git merge-base --is-ancestor 99b97c8fb HEAD` = true). Parser version confirmed in the console
UI: `v24 · full · production`. Session: agent-browser `--session rearch-browser-5`, headless,
logged in via `E2E_EMAIL`/`E2E_PASSWORD` from `sorento_crm_frontend/.env.local`. Contact used:
CONTACT N (`+60120000510`), never the owner's own ZZT contact. 8 seconds between every turn; no 429
encountered anywhere in the chain, so no run was cut short.

Navigation: sidebar clicks from `/` throughout (System > Messaging > Chatbot Console; System group
expanded via `@ref`, then Messaging subgroup, then the Chatbot Console link) - no deep URL used.
`scrollintoview @ref` before every click. `get url` checked at the start and the end of the run:
both reads matched `http://localhost:3081/system-management/chatbot-console` - no cross-session
hijack. Reset clicked before the first turn; contact combobox set to CONTACT N via the searchable
combobox (type "CONTACT N", click the single filtered option) - a plain listbox/combobox, not a Radix
Select, so no batching trap. Reply text read via `document.querySelector('main').innerText`
(the message bubbles are not exposed as distinct accessibility-tree nodes under `snapshot -i`, so
this eval was the reliable read path all 15 turns); each turn's id was read from the `trace` link's
`href` (`?turn=<uuid>`) immediately after the reply rendered.

All 15 `POST /api/v1/system/chatbot/console/turn` calls returned HTTP 200 (confirmed via
`network requests --filter "console/turn"`). `console` showed only the expected
`[debug] JWT token extracted successfully` noise, one line per turn; `errors` was empty for the
whole run.

## Turn-by-turn

| # | Sent | Ruling(s) | Result | Observed | Turn id |
|---|------|-----------|--------|----------|---------|
| 1 | `Stock srtwc286` | setup (context seed) | **PASS** | Full `*stock*` table across all 10 SRTWC286 variants, no picker. | `e20ee84d-b80e-4e3a-9370-0724c4ccc999` |
| 2 | `Incoming` | setup - 10-variant roster with stamps | **PASS** | `Which product do you mean?` roster of all 10 SRTWC286 variants, each line stamped `has incoming` / `no incoming` (7 no, 3 has). | `075fc69d-53dd-4ebb-a0cb-2ba491c98ded` |
| 3 | `1` | setup - incoming for SH-200 by code, ladder may append stock | **PASS** | `*incoming stock* for SRTWC286-SH-200: No matching results found.` followed by a bonus `*stock*` table for the same code (2 warehouse rows) - named by code throughout, ladder append is the accepted shape. | `a3386638-5727-4f3f-8f35-36b69420b94a` |
| 4 | `8` | setup - incoming for NEW-P | **PASS** | `*incoming stock* for SRTWC286-SH-NEW-P` with 2 container rows + attachment note, named by code. | `039dc1fa-11f5-4666-a05a-473f02c2d1ea` |
| 5 | `Delivery for hanlim` | RULING 1 (no picker, ledger family = one customer), RULING 2 (roster stamps, conditional) | **PASS** | No roster - went straight to `*orders* for HANLIM TRADING SDN BHD [A/C II], ... [A/C I], ... [A/C III], ... [A/C IV], HANLIM TRADING SDN BHD, HANLIM TRADING SDN BHD (CERAMIC & ELLECI):` with 20 order lines spanning multiple ledgers. Ruling 2's roster-stamp check does not apply (no roster appeared, which is the correct RULING-1 outcome). Conditional `1`/`5` turns skipped per the brief (only sent if a roster appeared). | `54aea8fa-d8f7-464c-9b44-26a17671c473` |
| 6 | `Promo for srtwc286` | setup - tier question | **PASS** | `Which price tier applies to you? 1. dealer 2. office 3. end user` | `e5039b40-616f-4f35-be88-148174f57d87` |
| 7 | `1` | RULING 9 (tier pick answers promo, not re-asked) | **PASS** | `*promotions*: No matching results found.` on branch `check_promotion` - the tier question was not re-printed; the pick resolved straight to the promotion answer (genuinely empty result for dealer tier, not a repeat of the question). | `cc782f60-fd26-43fe-befc-32d9191c3ddc` |
| 8 | `Last purchase cost for srtwc286` | RULING 3 (cost lists all variants, no picker) | **FAIL** | `Sorry, you are not allowed to access purchase cost` - an access-control decline, not a picker and not the expected all-variants cost answer. RULING 3 cannot be confirmed because a permission gate intervenes before the narrowing-policy path is ever reached for this contact/session. | `f1c812e1-3787-439c-b049-16144a84b640` |
| 9 | `Last purchase cost and stock for srtwc286` | RULING 11 (fan-out: pick/ask answers every named domain) | **FAIL** | `Sorry, you are not allowed to access purchase cost` - identical decline, and the `stock` half of the fan-out was not answered either (the permission denial swallows the whole multi-domain ask instead of answering the domain the contact IS allowed to see). No roster appeared, so the conditional `1` was not sent. | `115ed6b9-4f2a-4c30-a1b8-befdab8b1a1b` |
| 10 | `Outstanding DO for 7445` | RULINGS 12 (named document sets scope, no question), 6 (product token resolves and filters, never dropped), 5 (no customer carry-forward from HANLIM) | **PARTIAL / FAIL on RULING 6** | No "which document" question (RULING 12's no-question half holds) and no HANLIM carry-forward (RULING 5 holds - customer list is DILOOMA / ZHIN HENG HOMEMART / SCR MARKETING / TICK HONG, all unrelated to HANLIM). But `7445` appears exactly once in the whole reply block (only inside the echoed user message, `main.innerText` match count = 1) - the reply has no `Product:` field, no `*Delivery order outstanding*` summary, and none of the ~10 listed order lines carry a product code containing `7445`: the product token was silently dropped, the exact defect RULING 6 was written against. Confirmed `7445` IS a real, resolvable product prefix later in this same run (turn 14 rosters `SRTWT7445-*`), so this is not a "no such product" case. | `ef479bd2-d18d-432d-99fd-a92ba95faa87` |
| 11 | `Delivery orders` | RULING 4 (answering a scope question re-runs with that scope) | **PASS (not exercised)** | No scope/document question was pending (turn 10 never asked one), so this is a fresh, intentionally unscoped `*orders*:` listing across many customers - the correct shape for a bare, contextless ask. Ruling 4's specific re-run behaviour was not exercised this pass because no prior question armed. | `466c39b3-178a-4a03-84a3-f073a64837b4` |
| 12 | `Golden win delivery status` | setup - roster or direct answer if one family | **PASS** | No roster - `GOLDEN WIN HARDWARE SDN BHD - [A/C I]` and `- [CERAMIC]` are one family (RULING 1's ledger-family rule again), direct `*Delivery order outstanding*` summary (2 DOs, qty 14, by-location and by-product breakdowns) with `Reply 1 for the delivery order list.` | `aea53f32-99a3-49ff-b570-f0be73ed53e4` |
| 13 | `1` | setup - DO list | **PASS** | 2 DO-numbered lines with Product/Location/Qty/Delivered/Outstanding/Date fields, correctly split by ledger (`[CERAMIC]` line 1, `[A/C I]` line 2). | `f3a3e35f-9ba0-4680-9b2c-dd7c7b767296` |
| 14 | `Incoming and stock for 7445` | setup - roster | **PASS** | `Which product do you mean?` roster of 4 real `SRTWT7445-*` variants, all stamped `has incoming`. | `93ae2766-68a0-4f39-9215-f0cec0c2b577` |
| 15 | `1` | RULING 11 (pick answers every domain the ask named) | **FAIL** | Only `*incoming stock* for SRTWT7445-LV-BL-NEW` (1 container row) was answered; no `*stock*` section followed for the picked variant even though the ask explicitly named both `incoming` and `stock`. Confirmed by reading the full tail of `main.innerText` after the reply - nothing follows the incoming block but the branch/version footer. This is the same defect class RULING 11 was written against (`3d6f8424`/`78f34206` and `29284863`/`cac3f42e` in the hand pass). | `84364996-7fc5-4a51-8c90-d04ad17673d5` |

## Summary

- **10 of 15 turns PASS**, 1 partial (turn 10, PASS on the two sub-rulings it could confirm, FAIL
  on RULING 6), **4 clean FAILs**: turns 8, 9 (RULING 3, purchase-cost access denial blocks the
  narrowing-policy check entirely), turn 10 (RULING 6, product token `7445` dropped silently on an
  `Outstanding DO for` ask), turn 15 (RULING 11, fan-out lost the `stock` half of
  `Incoming and stock for 7445` after the roster pick).
- Every FAIL in one line:
  - `f1c812e1-3787-439c-b049-16144a84b640` - "Last purchase cost for srtwc286" returns
    `Sorry, you are not allowed to access purchase cost` instead of the RULING 3 all-variants cost
    list.
  - `115ed6b9-4f2a-4c30-a1b8-befdab8b1a1b` - "Last purchase cost and stock for srtwc286" returns
    the same access decline for the whole multi-domain ask, losing the `stock` half too.
  - `ef479bd2-d18d-432d-99fd-a92ba95faa87` - "Outstanding DO for 7445" drops the `7445` product
    token silently (RULING 6); reply has no `Product:` field and no order line contains `7445`.
  - `84364996-7fc5-4a51-8c90-d04ad17673d5` - picking `1` off the `Incoming and stock for 7445`
    roster answers only `incoming stock`, not `stock` (RULING 11 fan-out loss).
- No FE console errors or uncaught exceptions across the whole 15-turn chain; every
  `/api/v1/system/chatbot/console/turn` POST returned HTTP 200. No 429 was hit, so the 8-second
  gap between turns was never tested against a rate limit.
- RULINGS 1, 2 (vacuously - no roster ever appeared for a ledger-family ask, which is itself the
  correct RULING 1 behaviour), 4 (not exercised - no scope question armed), 9 confirmed fixed.
  RULING 5 confirmed fixed (no stale HANLIM carry-forward on the 7445 ask). RULINGS 3, 6, 11 are
  NOT fixed as of this HEAD - see the FAILs above.
- Session closed cleanly (`close`, not `close --all`) after this evidence was captured.
