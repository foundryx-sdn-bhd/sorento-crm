# Evidence run: FULFIL-CONFIRM-SCOPE (PR #1395)

- Head: bfc4da31e (branch crew/fulfil-confirm-scope)
- Date: 30 Sep 2026
- Stack: http://fulfil-confirm-scope.localhost:3107 (FE dev), API :8107, shared dev DB
- Tool: agent-browser 0.27.0, session fcs-evidence (closed at the end)
- Navigation: sign in, then sidebar Supply Chain > Project Demand > Fulfilment Planning, search + tick the SO row, "Plan SOxxxxxx"
- SO402757 was read-only throughout (no Decide, Confirm, Undo or row ticks). Only view switches, toggle, Filters and Columns were used there.

## Step 1: SO402757, list view, 1280x900 - PASS
URL: /project-sales/fulfilment-planning?sort=earliest_required_date&dir=asc&orders=SO402757
- Header text read: "Planning 1 sales orders together", "Rows: Product | By date", "Grid | List", "1 to confirm . 0 rejected", "Save all suggested (56)", "Board actions", "Confirm (1)". Only one text input on the page (the toolbar search, at top 736 px, below the cards); none in the header. The text "Every contributing line" is absent from the page.
- Toolbar, one row (all at top 739): Search (left 325), "Saved (1)" (left 590), "Others (78)" (left 664), "Filters" (760), "Columns" (848) on the left; "Actions" (1042), "Decide" (1138) on the right.
- Others pressed: aria-pressed=true, class token bg-primary present. Saved: aria-pressed=false, no bg-primary.
- Pressed Saved: Saved aria-pressed=true with bg-primary, Others loses it. The contributing-lines grid showed one row: "24 SO402757 CONTACT N OIB CONSTRUCTION SDN BHD (PROJECT) TPE-9204 ... Saved" (line 24 TPE-9204). Screenshot list-1280.png taken here. Pressed Others: back to Others (78) pressed with bg-primary.
- Filters menu > Status dropdown lists 8 options: Saved, Suggested, Confirmed, Change proposed, Rejected, Suggestion changed, Cancelled, Needs a location.
- Chose Saved under Others: chip "Status: Saved" shown, Filters shows badge "1", list empty text: "Nothing is outstanding on this board". Cleared via the chip's "Clear filter: Status: Saved": chip gone, 29 rows back.
- Columns menu (Toggle Columns): Line, Sales Order, Agent, Customer, Product, OI, Required Date, Outstanding Qty, Suggested, Decided, Rank, Verdict. Rank unticked, all others ticked.
- Network: board GET, stock-transfers GET, column-config GETs, all 200. No writes.

## Step 2: SO402757, grid view - PASS
URL: ...&orders=SO402757&view=grid (header "Grid" button)
- Strip under the cards: Search (left 325, top 736), "Saved (1)" (590), "Others (78)" (664), "Status" (760), all on one row.
- Search left edge: list = 325, grid = 325. Same.
- Pressed Saved: bg-primary moved to Saved; grid narrowed from 21 to 5 table rows (4 stock-transfer rows + the one grid row) and the remaining grid row is product TPE-9204. Pressed Others: back to Others (78) pressed.

## Step 3: SO290633 - PASS with deviations noted below
URL: ...&orders=SO290633
Deviation 1: the brief said SO290633 has 4 open lines and nothing saved. It shows "Others (57)", "Save all suggested (33)" and most lines already Confirmed, header "Revision 1" line absent at start ("No decision yet"). So the "tick header box (4 selected)" step selected 57; I unticked it and ticked four lines (15, 16, 17, 19) instead.
Deviation 2: because lines 15, 16, 17 were already confirmed and line 19's suggested source (SO358264 line 16 at MWH) is stale, the first Confirm was refused. This is a real path worth recording, then I used two other lines for the clean path.
- Decide > As suggested with 4 selected. Toast: "1 saved as As suggested . 3 skipped: CWCSC604-QQ line 15 (already confirmed), CWCX604-S-RL line 16 (already confirmed), CWCY604 line 17 (already confirmed)". Network: PUT .../lines/<...>|19|WESERP10B|2026-08-03/draft 200. Header showed Confirm (1); toggle Saved (1) | Others (56).
- Confirm (1) press, no popup and no dialog. Network: one POST /api/v1/project-sales/fulfilment-planning/confirm-all 200 (the preview; no second POST). Results block: "0 of 1 orders confirmed. SO290633: 1 line cannot be confirmed. Nothing was written. Line 19, WESERP10B: SO358264 line 16 is no longer at MWH." No toast seen. Toggle unchanged (Saved (1) | Others (56), Others pressed).
- Undo line 19 (Saved segment, Undo button): DELETE .../lines/<...>|19|WESERP10B|2026-08-03/draft 204; Saved (0) | Others (57). Pressed Saved with 0: text "No saved decisions yet". Pressed Others.
- Ticked lines 34 and 40 (2 selected). Decide > As suggested: toast "2 saved as As suggested"; PUT .../lines/<...>|34|B2155-NL-BLUE|2026-07-15/draft 200 and PUT .../lines/<...>|40|CB2829-DIY|2026-07-15/draft 200. Header Confirm (2); Saved (2) | Others (55).
- Confirm (2) press: no popup, no dialog. Network in order: POST confirm-all 200, POST confirm-all 200, then GET fulfilment-planning list, GET board, GET stock-transfers (all 200). Results block: "1 of 1 orders confirmed. SO290633: confirmed as revision 1 (0 purchase rows handed over). Confirmed: line 34 B2155-NL-BLUE . line 40 CB2829-DIY". No carried and no held-back lines reported. Header then "Revision 1 . confirmed by Teh Jayson, 30/09/2026, 11:22 pm . 2 lines", Confirm (0). No toast text was captured (none in DOM at 1 s and 9 s).
- Limit: agent-browser 0.27.0 `network request <id>` returns only the URL, not request bodies. So "preview true" then "only_line_ids" is inferred from the order and from the refused run, where only the first (preview) POST fired and nothing was written. It is not read from a body.
- Toggle after the press: Saved (0) | Others (57), Others pressed with bg-primary (unchanged segment). Pressed Saved: "No saved decisions yet". Pressed Others.
- Data left behind on SO290633: lines 34 and 40 confirmed as revision 1 by Teh Jayson (the press adopts the order, as expected).
- Side note (not a defect of this lane, recorded for the owner): using the sidebar leaf "Fulfilment Planning" while a board is open keeps the board on screen (the URL gains product=SO290633 from the board search box). I went to Plans and back to reach the picker.

## Step 4: SO402757, 375x800 - PASS
- Grid strip wraps to 3 rows: Search (left 37, right 293, top 956), Saved | Others (top 999; Saved 38 to 112, Others 112 to 199), Status (37 to 197, top 1036). Others pressed with bg-primary (blue). documentElement.scrollWidth = 360 (<= 375). Screenshot grid-375.png taken here.
- Pressed Saved at 375: Saved aria-pressed=true with bg-primary, Others loses it. Pressed Others: restored.
- List toolbar at 375 wraps to 4 rows: Search (top 1259); Saved | Others (1302); Filters | Columns (1339); Actions | Decide (1379). scrollWidth = 360.

## Console
- agent-browser `errors`: empty after every step.
- `console`: debug lines ("JWT token extracted successfully"), Fast Refresh, i18next logs, React DevTools info. No error-level or warning-level entries.

## Files
- list-1280.png (step 1, toggle on Saved), grid-375.png (step 4, grid strip).
