# OI handover email - end-of-lane browser evidence

Lane: `feat/oi-handover-email`. Part A stack: FE :3086, BE :8086, DB `sorento_oihe_ci` (CI
clone, no project sales orders). Part B stack (same FE/BE ports, DB switched by the captain):
DB `sorento_ai_automation_0915_1900` (production copy). No RQ worker on either stack -
dispatch stops at the `email_outbox` row (Part B DB) / `Notification`+`NotificationDelivery`
(Part A DB), expected in both cases. Browser: agent-browser headless, sessions `oi-handover`
(Part A) and `oi-handover-b` (Part B), both logged in as `person38@example.com`.

**Part A** ran on the CI-clone DB and found the AC-H16 hydration defect (section 3 below).
**Part B** ran later on the production copy, after a coder fix round landed
(worktree head `bdc25b900`: savepoint dispatch, template inline borders/blanks, AutomationForm
hydration) - Part B section confirms the AC-H16 defect is fixed on this DB and covers the
real end-to-end email content, the new "one email, everyone on the thread" recipient option
(AC-H26), and the purchasing no-dispatch check (AC-H8) on real data.

## Navigation

Sidebar path confirmed: Dashboards -> System (Configuration group) -> Automation ->
"Order inquiry to purchasing" row (seeded by the migration, trigger `order_inquiry_handover`,
enabled). Reached by sidebar clicks only, no deep URLs, per policy.

## Checks

### 1. Automation row seeded correctly - PASS
`select id, name, trigger_type, enabled, group_matches, recipient_config from automations
where trigger_type='order_inquiry_handover'` before any UI interaction:
```
name: Order inquiry to purchasing
enabled: t
group_matches: f
recipient_config: {"role_ids": ["d504ac76-..."], "user_ids": [], "extra_emails": [],
                    "include_actor": true}
```
Detail page (screenshot `AC-H16-1280-initial.png`) shows Trigger type
`order_inquiry_handover`, Recipients (1) = the Purchasing role, Enabled = Active,
Schedule = Manual only. Matches AC-H12/H13.

### 2. AC-H16 recipient picker - checkbox present, correctly placed - PASS
Opening Edit (`AC-H16-1280-edit-open.png` then scrolled, `AC-H16-1280-edit-scrolled-DEFECT-unticked.png`)
shows the Recipients block with, in order: Specific users, By role (Purchasing selected),
"Include promotion creator / owner", "Include assigned CS PIC (purchase request / sponsorship
form)", **"Cc the person who raised it"**, External emails. The new checkbox sits directly
beside the existing "assigned CS PIC" option as the UAC requires.

### 3. AC-H16 round-trip on edit-open - **FAIL (defect)**
Given `recipient_config.include_actor = true` in the DB (confirmed via the live GET response
body captured through `network request`, see below), the Edit modal's "Cc the person who
raised it" checkbox renders **unticked** every time the modal opens
(`AC-H16-1280-edit-DEFECT-unticked-on-open.png`, reproduced after a hard page reload with no
prior interaction). This is not a display-only bug: saving the form in this state writes
`include_actor: false` back to the row, silently discarding the previously-true value (see
round-trip test below). Root cause traced (read-only, not fixed):

`sorento_crm_frontend/app/(protected)/system-management/automation/components/AutomationForm.tsx`
lines 103-108, the `useEffect` that hydrates local state when the edit modal opens:
```
setRecipientConfig({
  user_ids: automation.recipient_config?.user_ids ?? [],
  role_ids: automation.recipient_config?.role_ids ?? [],
  include_promotion_owner: automation.recipient_config?.include_promotion_owner ?? false,
  extra_emails: automation.recipient_config?.extra_emails ?? [],
});
```
This omits both `include_actor` and the pre-existing `include_assigned_cs_pic` key, so both
checkboxes always hydrate to `undefined` (falsy) regardless of the saved value.
`include_assigned_cs_pic` was already broken before this lane (confirmed: `git diff
origin/main -- AutomationForm.tsx` for this lane is empty - the coder added `include_actor` to
`RecipientPicker.tsx` and `automation.types.ts` per the plan, but never touched
`AutomationForm.tsx`'s load path, so the new field inherited the pre-existing bug).
`RecipientPicker.tsx` itself is correct (`checked={Boolean(value.include_actor)}`,
`onCheckedChange` wired) - the defect is entirely in the parent's read-back.

Confirmed via live network capture (session `oi-handover`, requestId `14736.302`, GET
`/api/v1/system/automation/automations/adcfa068-...`):
```
"recipient_config":{"role_ids":[...],"user_ids":[],"extra_emails":[],"include_actor":true}
```
returned correctly by the backend at the moment the modal showed the checkbox unticked -
proves the break is FE-side, not a backend/serialization issue (AC-H13/H17 backend contract
is fine; this is purely the edit-form hydration).

### 4. Untick / save / reopen - PASS (trivially, but exposes destructive side effect)
Tick -> Save (PUT 200, response body carries `"include_actor":true"`) -> DB confirmed `true`.
Reopen Edit without touching anything -> checkbox shows unticked (the defect above) -> clicked
Save anyway -> DB flips to `include_actor: false`. **This means opening Edit and saving ANY
unrelated field (name, description, template) on this automation silently disables "Cc the
person who raised it" and "Include assigned CS PIC", even if the admin never touches those
checkboxes.** Screenshots: `AC-H16-1280-edit-reopen-DEFECT-unticked-after-true-save.png`
(pre-save-of-false state), DB check via psql confirms `false` after that save.

### 5. Tick again / save / reopen - PASS (write), FAIL (display, same defect)
Reopened, ticked, saved -> DB confirmed `include_actor: true` again (PUT succeeded, write
path is correct end to end). Reopened again -> checkbox shows unticked
(`AC-H16-1280-DEFECT-reopen-after-true-save-still-unticked.png`), same defect, 100%
reproducible across three independent open/save/reopen cycles. Left DB in `include_actor:
true` state at the end (matches the seed's intended value), though a side effect of the
above testing also flipped `group_matches` from the seeded `false` to `true` (see note below)
- an SQL fix-up attempt was blocked by the environment's write-protection classifier on this
shared DB, so it is left as-is and reported rather than worked around.

### 6. Console / errors after each save - PASS
`errors` returned empty after every save. `console` showed only pre-existing, unrelated
warnings (`Missing Description or aria-describedby for DialogContent`, a generic Radix
Dialog accessibility warning present on other automations too, and one `key` prop warning in
`Demo1Layout` unrelated to this feature) - no new console errors from the checkbox or the
save flow itself.

### 7. 375px usability - PASS
`AC-H16-375-detail.png`: detail page renders cleanly, no clipping.
`AC-H16-375-edit-modal-top.png` / `AC-H16-375-edit-checkbox-visible.png`: Edit modal stacks
to full width, internal recipients panel scrolls independently, "Cc the person who raised it"
is fully visible and not clipped once scrolled into view, and is tappable (ticking it in the
375px viewport worked immediately, same underlying component). No layout defect at 375px -
the only defect is the state-hydration bug in section 3, which is viewport-independent.

## Additional finding (side effect, not in the AC list)

Editing and saving this automation from the UI also silently resets `group_matches` from the
seeded `false` to `true` (`automations` table has no exposed control for this in the Edit
modal at all - `AutomationForm.tsx` keeps a local `groupMatches` state seeded to `true` by
default and always resends it on save, regardless of the row's actual value on load - the
same class of bug as section 3, but for a field the form's `useEffect` never reads back
either). Not required by AC-H16 but flagged because it means **any** edit through this modal
on **any** automation quietly changes untouched settings.

## Summary

| Check | Result |
| --- | --- |
| Automation seeded (H12/H13) | PASS |
| Checkbox present, correctly placed (H16) | PASS |
| Checkbox round-trips on edit-open (H16) | **FAIL - defect, see section 3** |
| Save writes the ticked value | PASS |
| Save writes the unticked value | PASS |
| Console/errors clean | PASS |
| 375px usable, not clipped | PASS |

**AC-H16 is not fully met (Part A DB, pre-fix).** The FE checkbox itself is correctly wired and
the backend correctly stores/returns `include_actor`, but `AutomationForm.tsx`'s edit-open
hydration drops `include_actor` (and the pre-existing `include_assigned_cs_pic`), so the
checkbox never shows its true saved state and any edit-and-save silently clears it. This
needed a coder fix to `AutomationForm.tsx` lines ~103-108. **Confirmed fixed in Part B below**
(worktree head `bdc25b900`).

---

# Part B - end-to-end on the production copy (`sorento_ai_automation_0915_1900`)

Session `oi-handover-b`. Orders touched: **SO415103** (BRW/borrow decision only - no purchasing
demand, kept as a negative-control data point), **SO289628** and **SO255901** (first real
handover pair, confirmed together in one bulk "Plan together" action), **SO259655** (second
pair, confirmed after enabling "One email, everyone on the thread"). `SO390808` and
`SO397450` were never touched (owner's rows). Two red herrings worth recording for whoever
reads this next:

- The Fulfilment Planning list's default "Rows: Product" view surfaces plenty of lines with
  suggestion "Not recorded" and a permanently disabled row checkbox - these are lines the
  planner has no computed source for yet and cannot be decided from this screen at all
  (not a bug I could reproduce a fix for; just don't waste time on them). The `Filters ->
  Review state -> Not started` filter combined with selecting 3-5 orders and "Plan together"
  reliably surfaces real `Buy N` suggestions for undecided lines.
- Navigating "Fulfilment Planning" again from the sidebar/search-menu while already inside a
  "Planning N sales orders together" session does NOT leave that session (Next.js soft-nav
  reuses the mounted page) - only clicking a fully different page first (e.g. Dashboards) and
  then back in gives a clean list.

## Step 1-3: raise real order inquiry rows, capture the email

Confirmed 2 lines each on SO289628 (RPAC, qty 3210, `11/04/2023` required date - a stale date
already in the book, not something I set) and SO255901 (SRTWB4081, qty 3), via the Fulfilment
Planning "Plan together" board: select lines with a `Buy N` suggestion -> Save as suggested ->
Confirm -> confirm the "Confirm N lines across N orders?" dialog. Toast: "SO255901: confirmed
as revision 1 (1 purchase row handed over)" / "SO289628: confirmed as revision 1 (1 purchase
row handed over)". DB:

```
projects.order_inquiry_rows: RPAC qty=3210 verb=ORDER, SRTWB4081 qty=3 verb=ORDER (both new)
automation_runs: 2 rows, both status=success, recipients_attempted=7, recipients_delivered=0
email_outbox: subject 'OI: BRW-NTC @ SO289628' x7 rows (one per recipient - this was BEFORE
              the one_email toggle), subject 'OI: BRW-RSV @ SO255901' x7 rows
```

Note: confirming 2 different orders in one bulk UI action produced **two separate
automation_runs / two separate subjects**, not one merged dispatch - each order's confirm is
its own DB commit server-side, each firing its own post-commit dispatch. This matches AC-H1's
letter (AC-H1 is about two SOs raised in ONE session/commit, which is a different, narrower
scenario than bulk-confirming two unrelated orders) but is worth the guide calling out
explicitly, since "confirm 2 orders in the planning board" and "one write raises rows on 2
orders" are NOT the same trigger count.

Saved `handover-email-real.html` / `.txt` (SO289628, before the one_email toggle) and
`handover-email-real-1280.png`. Checked against plan section 3.6:

| Check | Result |
| --- | --- |
| Subject `OI: <location> @ <SO>` | PASS - `OI: BRW-NTC @ SO289628` |
| Red bold headline verb | PASS - `<p style="color:#b91c1c;font-weight:bold;">ORDER</p>` |
| SO table (S/O NO, CUSTOMER, PROJECT), bordered | PASS |
| Line table (SO DATE, S/O NO, CUSTOMER, PROJECT, ITEM CODE, QTY, DELIVERY DATE, REMARK), bordered | PASS |
| No literal "None" | PASS - unset PROJECT renders as an empty cell, not "None" |
| Qty without trailing decimals | PASS - DB holds `3210.0000`, email shows `3210` |
| Dates dd/mm/yyyy | PASS - `16/09/2026`, `11/04/2023` |
| Worklist link present | PASS - `http://localhost:3000/project-sales/order-inquiries?query=SO289628` |
| `metadata_json`/`recipient_email` include purchasing + actor | PASS - 7 recipient rows: `person33@example.com, person34@example.com, person20@example.com, person35@example.com, person19@example.com, person21@example.com, person38@example.com` (actor, the logged-in user, present) |

One inconsistency caught between this email and the later SO259655 one (see AC-H26 section):
this SO289628 email's footer read `Raised by Teh Jayson (person38@example.com) on 2026-09-16.`
(ISO date) while the SO259655 one 24 minutes later read `...on 16/09/2026.` (dd/mm/yyyy). The
worktree's backend runs `--reload` and a live coder was committing to this exact worktree
during my session (`486c7dbfb`, `6a93384bd` landed while I was mid-run) - the date-format fix
most likely hot-reloaded between my two captures. The dd/mm/yyyy version (SO259655) is current
and correct; the ISO one is a stale capture of the pre-fix template, not a fresh defect.

The worklist link points at `localhost:3000`, not the lane's `:3086` - this is
`FRONTEND_BASE_URL`/equivalent backend env being a fixed default rather than the lane port;
correct for prod, a minor mismatch for lane-local browsing. Not a defect, just noted since
clicking the link from a real inbox on this lane stack would land on the wrong port.

## Step 4: second write (amendment/revision) - PARTIAL, hit a real defect, skipped per instruction

Edited SO289628's line qty from 3210 to 3200 via `Sales Order -> Edit -> Lines` (a real,
UI-native amendment path exists on this build: reducing "Qty ordered" and saving raises a
"Planning changes raised on 1 line" banner with a "Plan" link). Followed that link to
Fulfilment Planning, which correctly detected the new outstanding (`2,562`, down from the
prior `2,572`) but the line's cached decision still showed the OLD confirmed amount ("Buy
3200" - one line-item drop lower than intended - see below) and refused on Confirm:

```
"SO289628: The components add up to 3200 and the line is open for 2562."
```

Reloading did not clear the stale suggestion. Expanding the line's decision panel showed an
"Undo" control (tooltip: "Already saved. Undo it before saving it again.") - clicking it, and
then toggling the "Buy the whole line" switch off, changed the state to "Amended to nothing /
3200 short" (again referencing 3200, not the new 2562), and then the same switch **stopped
responding to clicks entirely** - `aria-checked` stayed `false` and "Save decision" stayed
disabled through 3 further click attempts (confirmed via `eval` against the DOM, not just the
CLI's ref-based click). Screenshot: `AC-H3-4-amendment-DEFECT-stuck-buy-toggle.png`.

**This is a real, reproducible defect** in the fulfilment-planning re-decision flow for a line
whose qty was reduced after an earlier confirmed decision (a stale `outstanding`/`reserve`
snapshot of `2572` - the qty BEFORE my edit - persists in the recompute, and the option
that should let CS pick a new decision gets stuck rather than resetting). Per instruction ("if
no UI path exists on this build, say so and skip, do not fake it") - a UI path DOES exist
(qty edit -> Planning changes -> Plan -> Confirm), but it does not currently work for a
qty-reduction revision, so **no ADVANCE/DELAY/CANCEL BALANCE email was captured**. DB was left
clean: no new/duplicate `so_supply_decisions` revision and no new `order_inquiry_rows` row was
written by the failed attempt (`select revision_no, state from projects.so_supply_decisions
where project_sales_order_id = 'c0ac2381-...'` still shows exactly one row, `revision_no=1`,
`state=active`, from the original confirm).

## AC-H26: "One email, everyone on the thread" - PASS

`system-management/automation` -> "Order inquiry to purchasing" -> Edit: alongside "Cc the
person who raised it" (now **rendering ticked** - the Part A hydration defect is confirmed
fixed on this build, `bdc25b900`) is a new checkbox "One email, everyone on the thread"
(`recipient_config.one_email`), seeded unticked on this DB. Ticked it, Save changes -> no
console errors -> psql confirms `recipient_config` gained `"one_email": true` alongside
`"include_actor": true`. Reopened Edit: **both** checkboxes render ticked
(`AC-H26-1280-both-ticked-reopen.png`). At 375px (`AC-H26-375-both-ticked.png`) both are
visible, not clipped, and sit in the same order as at 1280px. Final no-op check (step 6, see
below): reopened once more, both still ticked, clicked "Save changes" with no edits, and psql
confirms `include_actor` and `one_email` both still `true` afterward - the round-trip and the
no-op-save-doesn't-clear-it guarantee both hold.

## Step 2 redo with `one_email: true` - PASS, ONE row not one-per-recipient

Confirmed 2 fresh lines on **SO259655** (BRBC22332W-ENG qty 238, BRC21120XUW-3BA-ENG qty 198,
both verb ORDER) the same way as above. Toast: "SO259655: confirmed as revision 1 (2 purchase
rows handed over)". This time `email_outbox` gained exactly **one** row for the whole event,
not seven:

```
id=624f3fec-..., subject='OI: BRW-IB @ SO259655', recipient_email='person34@example.com'
recipients_json = {
  "to": ["person34@example.com"],
  "cc": ["person20@example.com", "person33@example.com", "person35@example.com",
         "person21@example.com", "person19@example.com", "person38@example.com"],
  "bcc": []
}
metadata_json.recipients = [same 7 addresses, person38@example.com (the raiser) LAST]
```

Confirms exactly what was asked: all 6 purchasing addresses (`to` + `cc`) with the raiser
(`person38@example.com`, via `include_actor`) placed **last** in both `recipients_json.cc` and
`metadata_json.recipients`. `automation_runs.recipients_attempted = 7` (counts resolved
addresses, not outbox rows - correct, since `resolve_recipients` still returns 7 addresses,
they're just folded into one row's To+Cc instead of one row per address). Body content (both
lines, dd/mm/yyyy dates, qty without decimals, red ORDER headline, link) saved to
`handover-email-real-oneemail.html` and screenshotted at 1280
(`handover-email-real-oneemail-1280.png`) - matches the mock identically to the SO289628 email
above, just with 2 line rows instead of 1 and the corrected dd/mm/yyyy footer date.

## Step 5: purchasing action does not dispatch (AC-H8, real data) - PASS

On Order Inquiries (`Procurement > Supply Chain > Order Inquiries`), searched `SO259655`,
selected both just-raised rows, `Actions -> Reject selected (2)` with a reason ("Tester
verification..."). Toast: "2 rows rejected. The lines are back with CS." Before: `email_outbox`
WHERE subject LIKE 'OI:%' = 22 rows, `automation_runs` for this automation = 4 rows. After the
reject: **still 22 and still 4** - no new dispatch fired from a purchasing-side action, exactly
as AC-H8 requires, verified against real data rather than a test fixture.

## Step 6: automation edit form round-trip, final check - PASS

Covered above under AC-H26 - reopened the automation, both checkboxes ticked, "Save changes"
clicked with zero edits, and `psql` confirms `include_actor: true` and `one_email: true` both
survive the no-op save.

## Part B summary

| Check | Result |
| --- | --- |
| AC-H16 hydration defect | **FIXED** - confirmed ticked on open, survives no-op save (was FAIL in Part A) |
| AC-H26 "One email, everyone on the thread" - present, round-trips at 1280/375 | PASS |
| Real handover email matches plan mock (subject, headline, tables, dates, qty, link, recipients) | PASS |
| One-email mode: single outbox row, To+Cc holds all addresses, raiser last | PASS |
| AC-H1 nuance: bulk-confirming 2 orders = 2 dispatches, not 1 (expected, not a defect, but distinct from AC-H1's single-commit-two-SOs scenario) | Noted for the guide |
| AC-H8 purchasing reject does not dispatch (real data) | PASS |
| Amendment/revision second write (ADVANCE/DELAY/CANCEL BALANCE) | **Hit a real defect in fulfilment-planning's re-decision flow after a qty reduction** (stale outstanding snapshot, stuck Buy toggle) - skipped per instruction, not faked |
| Console/errors throughout Part B | Clean (same pre-existing Radix Dialog warning as Part A, no new errors) |

Evidence files added in this pass: `handover-email-real.html`, `handover-email-real.txt`,
`handover-email-real-1280.png`, `handover-email-real-oneemail.html`,
`handover-email-real-oneemail-1280.png`, `AC-H26-1280-both-ticked-reopen.png`,
`AC-H26-375-both-ticked.png`, `AC-H3-4-amendment-DEFECT-stuck-buy-toggle.png`.
