# Behaviour card: bulk reserve on the OI Lines tab (OI-BULK-RESERVE)

Base: origin/main 35008d5a6 plus #1684 (OI-STAGED-NOTIFY, merge-ready, not merged yet). Size M,
frontend only (the commit route already takes many lines in one call). Follow-up PR from main
after #1684 merges.

Owner 15 Oct: "now when we reserve, need to click line by line ... something like fulfilment
planning bulk select then Decide ... then do the reserving, then only click Reserve to confirm".

## Today (code)

- CS stages ONE line at a time on the Lines tab: per line "Reserve" (full requested qty at the
  default pool) or "Edit reserve" (ReserveLineForm: Location, Reserved, Reason required when below
  the requested qty, `ReserveLineForm.tsx:125-126`), each filling one entry of the staged map
  (`StagedReserveEntry`, `orderInquiryHeaderLinesColumns.tsx:84`).
- The header "Reserve (N)" then commits every staged line in one call; since #1684 it opens the
  confirmation with "Park notice first" (`ReserveConfirmDialog.tsx`).
- The Lines grid already ticks lines (`OrderInquiryLinesTab.tsx:243`) and its toolbar already shows
  "N selected · Clear" (`data-grid-list-toolbar.tsx` ~456-475).
- Fulfilment Planning's bulk pattern (`BoardDecideControl.tsx`): tick lines, the strip's "Decide"
  button opens a menu; "As suggested" saves at once, the other choices open a lightbox
  "Decide N lines: <choice>" with one shared Reason and a "Save N lines" button.

## Rules (proposed)

1. CS ticks lines on the Lines tab (same tick boxes). The toolbar's selection strip gets a
   "Reserve" button with a menu, the FP Decide pattern:
   - "Reserve as requested": stages every ticked line that has an open request at its requested
     qty (capped at what is left) and the request's location, in one click.
   - "Reserve...": opens a lightbox "Reserve N lines": one row per line (Product, Delivery date,
     Requested, Reserve, Location, Reason). Reserve defaults to the requested qty, Location to the
     request's location. A Reason is required only on a line reserved below its requested qty. One
     "Reason for partial lines" box at the top fills every empty partial reason. Footer:
     Cancel / "Stage N lines".
2. Staging only fills the staged map, exactly like N single-line stages: chips on each line, the
   header "Reserve (N)" count, and nothing saved yet. "Reserve (N)" + the confirmation (Park notice
   first) commits as today.
3. Ticked lines with no open request (already answered, or never requested) are left out of the
   bulk stage; the menu counts only the reservable ones ("Reserve as requested (10)"). Amending an
   answered line stays the single-line "Amend reserve".
4. Single-line Reserve / Edit reserve keep working; a bulk stage over a line already staged
   replaces its staged entry.

## Real dev examples (sorento_cagent_stack, read-only)

| OI | SO | Customer | Raised by | Open ORDER lines |
|---|---|---|---|---|
| OI-2609-0730 | SO421072 | APEX CONNECTION SDN BHD (PROJECT) | Eling | 92 |
| OI-2609-0731 | SO419603 | APEX CONNECTION SDN BHD (PROJECT) | Eling | 51 |
| OI-2609-0718 | SO421824 | MENTARI BERSERI SDN BHD (PROJECT) | Niki | 17 |

Mock scenario (OI-2609-0731): Joey asked CS to reserve 12 lines due 01/12/2026 (B2155-NL-BLUE,
C-FH14, CB2806A-SS-CR-DIY, CB2807-DIY, CB2829-SS-CR-DIY, CB313, CGB9032B-NEW, CKS1050, CSA150; pool
BRW). Eling ticks all 12, Reserve... , types 60 on CB313 and 0 on CKS1050 with "Partial stock" in
the shared reason box, Stage 12 lines, then Reserve (12), ticks Park notice first, confirms.

## Questions (each with a recommendation)

- Q1 Menu: (a) two items, "Reserve as requested" (one click) and "Reserve..." (lightbox);
  (b) the lightbox only. Rec (a): mirrors FP's "As suggested" quick path; most lines go in full.
- Q2 Answered lines: (a) bulk covers only lines with an open request; amend stays single-line;
  (b) the lightbox also amends answered lines. Rec (a): bulk is for answering a request; an amend is
  a correction CS makes one line at a time.
- Q3 Reason for partial lines: (a) per-line Reason column plus one shared "Reason for partial
  lines" box that fills the empty ones; (b) per-line only. Rec (a): typing "Partial stock" 20
  times is the click-by-click pain this removes.

## Owner note on mock v1 (15 Oct, via crew)

"just need one button, put it on the right please": Q1 = one button "Reserve (N)" at the right end
of the selection strip, no menu. It opens the "Reserve N lines" panel prefilled with each line's
requested qty and location, so a full reserve is just "Stage N lines". Staging clears the ticks.
Owner confirmed Q2 (a) (bulk covers only lines with an open request; amend stays single-line) and Q3 (a) (per-line Reason plus one shared box for partial lines).

## Owner note on mock v2 (15 Oct, via crew) - FINAL rules, supersede everything above

"oh don't need la, just one reserve button": NO panel, no shared reason box (Q3 dropped).

1. CS ticks lines on the Lines tab. The selection strip shows ONE button "Reserve (N)" at its right
   end, N = ticked lines with an open request (Q2 (a)); greyed at 0.
2. Pressing it stages every one of those lines straight away at its requested qty (capped at what
   is left) and the request's location, exactly like pressing each line's own "Reserve" today,
   clears the ticks, AND opens the #1684 confirmation at once, covering everything staged (these
   lines plus any staged singly before). Crew decision (b), 15 Oct: one click from ticks to the
   "Park notice first" confirmation; no screen shows two same-named buttons doing different things.
   An already staged line is replaced. Cancel on the confirmation keeps the staged chips.
3. A partial reserve stays the single-line "Edit reserve" (with its required Reason). Amending an
   answered line stays the single-line "Amend reserve".
4. The header "Reserve (N)" stays for single-line stages and opens the same confirmation, as today.
5. Frontend only: the staged map (`StagedReserveEntry`) and the commit route are unchanged.

## Owner note on mock v2, round 2 (15 Oct, via crew) - FINAL rules v3, replace (b) above

Owner: "in the line level we got tick to reserve all, x to reject i think? and maybe pen to amend?
so we need to be able to reject also ma, will it need the Decide button like fulfilment planning?"

Line icons today (no change): a requested line has the tick "Reserve" (full requested qty at the
request's location, `orderInquiryHeaderLinesColumns.tsx:255-266`) and the pen "Edit reserve" (qty,
location, reason, `:267-278`; reason required below the requested qty, `ReserveLineForm.tsx:125-126`).
A reserved or declined line has the pen "Amend reserve" (`:283-300`). There is NO x on the line
today: CS turns a request down by reserving 0 with a reason through the pen (state "declined").
Purchasing's "Reject selected" (`OrderInquiryDetail.tsx:789-800`) is a different action (purchasing
rejects the OI row itself) and is not part of this.

1. Selection strip: ONE "Decide (N)" button at its right end (the FP `BoardDecideControl` pattern),
   N = ticked lines with an open request; greyed at 0.
2. Menu: "Reserve as requested" = the tick for every counted line (stages at once, no dialog,
   clears the ticks); "Reject..." = a small lightbox "Reject N lines" with one required Reason,
   "Stage N lines" stages Reserve 0 + that reason on every counted line.
3. Amend stays per line (pen). Partial reserves stay per line (pen, Edit reserve).
4. Staged results show as today's chips ("Reserve 88 @ BRW", "Reserve 0", Undo); the header
   "Reserve (N)" + the #1684 confirmation commits as today (no auto-open; the strip button is named
   Decide, so no two same-named buttons).
5. Notify: a reject is a Reserve 0, so purchasing hears it exactly as a single-line Reserve 0
   today: the reserved notice lists RESERVED 0, the full BALANCE and the REASON
   (`order_inquiry_reserve_service.py:877-891`), sent at once or parked per "Park notice first".

## Owner OK on mock v3 (15 Oct, "ok can") + crew decision on the line x

6. Add a line-level x "Reject" beside the tick and the pen on a requested line (crew decision (a)):
   it opens the same one-reason prompt ("Reject 1 line", Reason required) and stages Reserve 0 +
   reason, exactly what bulk "Reject..." does per line. Line and bulk mirror: tick / x / pen.
