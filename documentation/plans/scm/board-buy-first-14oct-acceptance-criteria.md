# UAC: Fulfilment Planning decision panel, Buy can be typed first (BUY-FIRST-PANEL)

Plan: `PLAN-board-buy-first-14oct.md`. Tests: `BoardLineDecisionPanel.test.tsx`, describe
"Buy can be typed first (BUY-FIRST-PANEL)".

- AC-1 Buy is a number input beside the "Buy the whole line" switch. Until it is typed, editing
  Reserve or Borrow moves it to the remainder, as before.
- AC-2 Once typed, Buy is pinned: no Reserve or Borrow edit changes it.
- AC-3 While the parts do not add up, the Decision column reads "Unassigned N" or "Over by N", and
  Save decision stays disabled until it is 0.
- AC-4 Only while something is unassigned (and the row is not locked), "Rest to Buy" adds it to
  Buy (and pins it) and "Rest to Reserve" adds it to the first Reserve row. There is no
  "Rest to Borrow".
- AC-5 "Buy the whole line" ON buys the whole line and hides the Buy box. OFF puts back the stock
  side and the Buy exactly as they were; a typed Buy stays pinned, an untyped one follows the
  remainder again.
- AC-6 A Buy left at 0 (on leaving the box) clears Order back and the document cited. Retyping
  through an empty box does not.
- AC-7 Add location starts at what is left for stock: the line minus everything, a pinned Buy
  included; an unpinned Buy is left out as before.
- AC-8 A decision re-seeded under an untouched panel (the Decide dropdown) drops the pin.
- AC-9 On a locked (covered) row the Buy box is disabled until Amend.
