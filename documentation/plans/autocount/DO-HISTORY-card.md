# DO-HISTORY owner card (13 Oct 2026)

Plan: `PLAN-do-history-backfill-13oct.md`. Source: `main` 64dfd86c. Counts on the prod clone are
NOT in yet: section 6 of the plan has the read-only queries for the crew to run.

## What changes for you

| Before | After |
| --- | --- |
| AutoCount DOs in the CRM only from July 2026 | Every AutoCount DO from 1 Jan 2023, Sorento then Mocha |
| A pre-July DO would land as `New Order`, so 3.5 years of DOs would sit in "outstanding" (DO list, outstanding report DO side, chatbot) | Pre-July DOs with no tracking read **Delivered**, delivery date = DO date, marked "assumed" so it can be undone |
| DO lines show `from_dtl_key` 0 | Still 0 (that is what AutoCount sends). The real link is the SO line, matched by SO number + product; the dry run shows the link rate |

## What does NOT change

- No old SO becomes open demand: the DO import never writes SO quantities or status. SO delivered
  comes from AutoCount's TransferedQty, as today.
- No OI rows, planning changes, emails or WhatsApp during the run.
- Pre-July DOs that DO have tracking from the Excel upload keep it.

## Watch-outs

- If the CRM has no SOs from 2023, those DO lines land unlinked (query Q-E tells us).
- Sales achievement (delivered basis) moves linked quantities from the SO date to the DO date:
  month figures can shift, totals do not.
- #1623 cancel matching: Q4 keeps it from raising CS planning changes on 2023 orders.

## Run

Prod clone dry run -> counts to you -> your OK -> off-hours apply, Sorento first -> counts again.
Every step has a revert.

## Your 5 answers (recommended first)

1. Cutover: DocDate before **1 Jul 2026**?
2. Assumed delivery date = **DO date** (vs leave blank)?
3. Old SOs still open: **close them in AutoCount** from our report (vs force-close in CRM, which the next SO sync undoes)?
4. #1623: **no** planning changes / OI flags for pre-July SOs?
5. Path: **CRM monthly pull script** (vs shared-service push backfill)?
