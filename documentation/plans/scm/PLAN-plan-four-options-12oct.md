# PLAN: Fulfilment Planning limited to four options

Status: built, in review (PR #1586). Track: small fix (no migration, no auth/RBAC change, no new ingest surface).

Owner rule (12 Oct 2026): Fulfilment Planning suggestions are limited to four options:
**Use own location, Borrow (location), Use BRW, Buy.** The planner stops suggesting
'Use incoming', 'Borrow incoming' and 'Borrow from another order'. The board summary cards
show only those four, matching the bulk Decide menu (which, with the switch off, already offers
As suggested, Use own location, Borrow, Use BRW, Buy, Transfer to Cancel SO).

Prod example: SO422492 (revision 2). Cards show Use incoming 43, Borrow from another order 207,
Borrow incoming 0.

## 1. Where each kind is built today (main 31fb8ca1)

Ladder v7.1 is hard-coded in `walk_line` (`sorento_crm_backend/app/services/scm/front_planning_engine.py:1160-1176`
docstring, steps drawn at `:1305-1352`). Order: 0b pool share (Use BRW), 0c own arrival,
1 use, 2 order_borrow, 3 supply_borrow, 4b pool borrow, 5 buy. The first step that covers the
WHOLE remainder wins (`:1330`).

| Card label | Engine source | Candidates from |
|---|---|---|
| Use own location | step 1 floor half, `kind=reserve`, `rung=group_take` (`fpe:1597`) | `use_candidates_for` (`project_supply_service.py:3649-3657`), `_group_take_candidates` (`pss:2553-2638`) |
| **Use incoming** (removed) | step 1 WATER half, same rung, `kind=timely_spo` (`fpe:1597`, `fpe:1690`) | the same two builders, rows with `water: True` (`pss:2624-2638`, `pss:3657`), fed by `_group_water` (`pss:4200`) |
| **Borrow from another order** (removed) | step 2 `order_borrow` (`fpe:1315-1318`) and step 4b pool borrow (`fpe:1338-1352`), with a donor SO named | `order_borrow_candidates_for` (`pss:3847-3956`) |
| Borrow | the same step 2 / 4b, donor blanked by `_location_only` (`pss:1071-1085`, applied `pss:3945-3956` and to the Borrow dialog list `pss:10245-10255`) | same |
| **Borrow incoming** (removed) | step 3 `supply_borrow` (`fpe:1320-1322`), a later order's SPO | `supply_borrow_candidates_for` (`pss:3958`), built unconditionally (`pss:1684`) |
| Use BRW | step 0b pool share, `rung=pool`, `kind=reserve` | `_pool_chain` |
| Buy | step 5 / beyond-window | - |

The one switch: `SystemSetting.borrow_from_order_enabled` (`app/models/user.py:646`, default
false; read `app/services/scm/borrow_mode.py:14`, cached `pss:3804`). Settings page toggle
`user-management/settings/page.tsx:1213`. Off: step 2/4b candidates go through `_location_only`
and the Decide menu hides "Borrow from another order" (`BoardDecideControl.tsx:226-231`).
There is no toggle for Use incoming or Borrow incoming.

Cards: `DecisionStrip.tsx` (rendered from `FulfilmentBoardPanel.tsx:2635`), totals in
`_shared/lib/decisionStrip.ts`, kind mapping `rowOf` (`_shared/lib/supplyVocabulary.ts:285-345`),
`ORDER` (`sv:175`), `LABELS` (`sv:86-99`). Cards at 0/0 stay greyed (`DecisionStrip.tsx:64-70`);
only `borrow_other` hides at 0/0.

### Why prod shows "Borrow from another order 207"

Prod runs main 31fb8ca1 (deploy run 38204823263, 12 Oct), so BORROW-LOCATION-ONLY (#1534) is
live. With the switch off the live engine names no donor, so "Borrow from another order" can only
come from (a) the switch being ON in prod, or (b) a FROZEN composition. A covered line's
"Suggested" is the `proposed_components` frozen at confirm (`project_fulfilment_board_service.py:5298`,
`pss:4993`), and a revision confirmed before #1534 (9 Oct) froze donor-named borrows. #1584's
own evidence names SO422492 line 272 as "an order borrow naming a real donor line". The owner
says the Decide menu shows no "Borrow from another order", which only happens with the switch off
(`BoardDecideControl.tsx:230`). So (b) is the likely reading; the data request confirms it.

## 2. What each removed kind falls back to

- **Use incoming (timely SPO, own group's water).** Removing it, step 1 covers less, and since a
  step must cover the WHOLE unit the line falls to 4b pool borrow or **Buy**. Buy goes to
  purchasing as the confirmed Buy residual with **no netting** against open SPO
  (`project_order_inquiry_service.py:8-16`, `:869-875`). So the plain removal **buys goods that
  are already on the water**. That needs an owner decision (Q1).
- **Borrow incoming (step 3).** Falls to step 4b (shows as Borrow) or Buy. The donor's SPO stays
  with the donor, so nothing is bought twice.
- **Borrow from another order.** Same stock, same ranking, told as a location: "Borrow". This is
  what the switch OFF already does. A location Borrow is internally still a later order's stock
  (`_location_only` keeps `donor_core_line_id`, `donor_project_id`, `donor_required_date` and the
  re-borrow guard). That stays: the owner rule is about the words, and removing it would change
  which stock is lendable.

## 3. Already saved or decided lines of the removed kinds

Nothing in the database is rewritten. Frozen revisions and saved drafts keep their components,
and confirm / undo / order-back keep reading them as they are. Only the DISPLAY folds them:
`rowOf` maps the old kinds onto the four cards (see Q3).

## 4. Dev data (crew-data-request, 12 Oct; rows kept out of the repo)

Switch is OFF on dev. Every donor-named suggestion is FROZEN in revisions confirmed on or before
9 Oct (pre-#1534): 45 components across 10 revisions. timely_spo: 6 components in 3 revisions.
supply_borrow: none. So "Borrow from another order" on the cards is frozen history, not the live
planner.

## 5. Owner answers (12 Oct 2026)

Principle: DISABLE and HIDE, never delete code. Each step sits behind a flag in
`app/services/scm/ladder_steps.py` (all off) so it can be turned back on.

- Q1 Use incoming: disabled; the planner does not consider the group's water. Not shown
  under Use own location.
- Q2 Borrow from another order: the Settings toggle stays, but the planner never names a
  donor, whatever the toggle says (`ORDER_BORROW_NAMES_DONOR` off). The toggle still governs a
  person's pick (Decide menu, Borrow dialog). Cards never show a "Borrow from another order"
  card.
- Q3 Borrow incoming: disabled (step 3 never walked); code kept.
- Q4 (a): display-only fold for old frozen decisions and saved drafts. The board re-walks
  every line's Suggested on each load (`project_fulfilment_board_service.py:2835`, `:2882`), so
  only the Decided side of a confirmed revision and saved drafts still carry removed kinds.
- Q5: the per-line option row and trail question of a disabled step are hidden.
- Cards: Use own location, Borrow, Use BRW, Buy.
- Review round 1 ruling (owner, 12 Oct): the planner's suggestion confirms exactly as
  displayed. With the toggle ON, a borrow naming no donor order confirms as a location borrow
  (order-back on this line, donor decision untouched); a borrow picked with the donor named
  keeps the ON shape (`ProjectSupplyService._names_its_order`).

## 6. Tests

`tests/scm/test_plan_four_options.py` pins the four-option planner, the board's hidden rows and
confirm-as-displayed. Tests that pin the disabled steps' own behaviour run with the
`ladder_steps_on` fixture (`tests/conftest.py`). Vitest: `DecisionStrip.fourOptions.test.tsx`.
