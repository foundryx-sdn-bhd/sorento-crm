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
