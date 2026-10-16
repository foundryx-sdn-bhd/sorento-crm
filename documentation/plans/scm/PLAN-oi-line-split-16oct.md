# PLAN: split one SO line into delivery tranches in Order Inquiries (OI-LINE-SPLIT)

Status: Plan. Discovery in progress; behaviour card goes to crew as ONE ask before any code; Lavish
mock next; build only after the owner OKs the mock. Size L (new interactive flow on the OI, likely a
migration for tranche rows). Track: standard (L), lead pattern (tester red tests, coder, reviewer,
security-reviewer only if auth/RBAC is touched, browser pass 1280/375).

Card: `oi-line-split-behaviour-card.md` (alongside, after discovery). UAC:
`oi-line-split-16oct-acceptance-criteria.md` (after the card is answered). Mock:
`documentation/mockups/OI-LINE-SPLIT/index.html`.

## Journey

Owner 16 Oct ~21:20: staff Excel shows one SO line split into several delivery tranches (SO417636
CSK14B: 417 @ 20/10/2026 split 14 @ 20/9/2026 + 236 @ 20/11/2026 + 167 @ 20/11/2029; 504 @
20/12/2026 split 37 @ 20/11/2026 + 467 @ 2/1/2027). AutoCount users reduce the line, change its
date and add new lines, so the CRM OI only sees net numbers and the split is lost. Staff need a
very interactive, flexible, drag-and-drop way to allocate one line's qty to several tranches (qty +
delivery date + remark), before Confirm or inside Confirm.

## Steps

1. Discovery with file:line (OI line mapping, AutoCount pull, Confirm, OI email/Excel, grid
   components, OI Lines page from #1727).
2. Behaviour card as ONE crew ask.
3. Lavish mock on SO417636 (business data only).
4. After owner OK: UAC, tickets, tester red tests, coder, reviewer, browser pass, hand-test script.

## Open

- Everything below Step 1 waits on discovery.
