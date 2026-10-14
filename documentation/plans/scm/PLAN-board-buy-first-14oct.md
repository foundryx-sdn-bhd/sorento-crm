# PLAN: Fulfilment Planning decision panel, Buy can be typed first (BUY-FIRST-PANEL)

Status: in review, PR #1675. Track: small fix (frontend only, no migration, no auth change).

Owner, 14 Oct 2026: "I want to key in the BUY quantity first; now I must calculate reserve and
borrow and the rest is buy; I might do it from a different angle."

## Ruling

Buy on the board's line decision panel becomes a number input. Until the planner types it, it
keeps following the remainder (D7). Once typed it is pinned: no Reserve or Borrow edit changes it,
and the panel states what is left instead. Crew ruling (a), 14 Oct: the "Buy the whole line" switch
is a detour, so OFF puts back exactly what was there, a typed Buy and its pin included.

## Seam

- `sorento_crm_frontend/app/(protected)/project-sales/fulfilment-planning/components/BoardLineDecisionPanel.tsx`
  only: `buyPinned` state, `editComposition` skips the derivation while pinned, `typeBuy`,
  `settleBuy` (blur), `restToBuy`, `restToReserve`, `setBuying` holds the Buy and its pin,
  the re-seed effect unpins, the hint and the Add-location remainder.
- The Save gate is unchanged: `supplyComposition.ts` `lineBlockers` already refuses a line whose
  parts do not add up. The backend already stores `buy_qty` as its own number.
- Delivered credit (`reserveCredits`) is unchanged (Buy first). PR #1667 flips the backend to
  Reserve/Borrow first; if it merges, the panel should follow it.

Acceptance criteria: `board-buy-first-14oct-acceptance-criteria.md`.
