# PLAN - DO-SUMMARY-CAP: the outstanding summary's By product / By customer block stops at the top N rows

Status: small fix track (one presenter function and its goldens, no migration, no auth change, no new ingest surface, no screen change). Lane started 7 Oct 2026 from origin/main e9aa88b2. Behaviour card posted as a crew-ask on the lane PR; nothing in code until the owner answers it.
Issue: crew lane DO-SUMMARY-CAP (owner ruling 7 Oct 2026 07:10 MYT, Option 1 from the #1515 plan's "Owner question"). Related: DO-LIST-REPLY-LABEL #1515 (the quick-reply label answers the pending detail offer).
UAC: `do-summary-cap-acceptance-criteria.md`
Classification: MCP presenter, `sorento_crm_mcp/sorento_crm_mcp/presenters.py` (pure string rendering). No prompt change, no engine change, no route change.

## Symptom (prod)

Contact 551644114, turn 11 (6 Oct 2026): the dealer asked for their delivery order outstanding.
The "Delivery order outstanding" summary's `*_By product_*` block printed 300+ lines in one
WhatsApp message, most of them 1-unit rows (`<code>: 1 (O/S: 1)`). The counts above it and
the `Reply 1 for the delivery order list.` line below it were correct; the message was simply
unreadable on a phone.

## Cause (file:line)

`presenters.py:2067` `_outstanding_group` prints one line per row, "in the order given", for
all three groups (By location, By customer, By product). The route
(`outstanding_report_service.py:71` `_rank`) already orders every group outstanding desc, total
desc, name asc (R10), so the top of the list is the part worth reading, but nothing stops the
tail. AC-1103 of the 13 Sep lane pinned "no line is ever elided" when the largest group the
owner had seen was 14 customers; a customer-subject report over a dealer's whole range has
hundreds of products.

## Fix (presenter only, owner ruling Option 1)

- `*_By product_*` and `*_By customer_*` (the SECOND group of each block, SO block and DO block
  alike) print at most N rows (N = 20 unless the card's answer says otherwise), in the order the
  route gave them, then ONE trailer line.
- The counts (Delivery orders / DO qty / Delivered / Outstanding / date range, and the SO
  equivalents) and the `*_By location_*` group print whole, as today.
- Trailer wording (exact bytes, the owner's):
  - By product: `... and K more products (reply P for the full list)`
  - By customer: `... and K more customers (reply P for the full list)`
  - K = rows beyond N. P = the position that block's list holds in the detail offer printed at
    the end of the same reply: SO block = 1; DO block = 1 when only the DO list is offered, 2 when
    both lists are offered (the offer is `1. Sales order list / 2. Delivery order list / 3. Both
    lists`, R14). A trailer that said "reply 1" under a DO block of a both-scope reply would run
    the SO list.
- N rows or fewer: the block prints exactly as today, byte for byte. The existing goldens
  (3 customers, 14 customers, 3 products) do not move.
- The detail list reached by that reply (`_outstanding_detail`, presenters.py:2444) is
  unchanged: it is the DO (or SO) row list, every row, as before.
- "reply P" lands on the EXISTING pending offer. The chatbot lane reads the offer off the
  rendered text with two anchored patterns (`lanes/business/fetch.py:2062-2067`:
  `^\d+\.\s*Delivery order list\s*$` and `^Reply \d+ for the delivery order list\.$`, SO alike).
  The trailer starts with `...`, says `reply` in lower case inside brackets and never stands as
  one of those lines, so the stored roster is identical with or without the cap, "1" (or the
  label, once #1515 merges) resolves exactly as today, and no offer code is added or copied.
  Nothing in #1515's diff (`turn/decide.py`) is touched here.

## Co-ordination with #1515 (DO-LIST-REPLY-LABEL)

#1515 is open (head 796158ae) and changes `decide.py` only. The two diffs do not overlap. If
#1515 merges first this lane merges origin/main before crew-done as usual; otherwise the trailer
is wired to the offer seam that already exists on main (the regex roster above), and #1515's
label fix applies to it unchanged when it lands. Noted in the PR body.

## Edge cases (pinned by tests)

| Case | Behaviour |
|---|---|
| exactly N rows | N rows, no trailer |
| N + 1 rows | N rows, `... and 1 more product (reply P for the full list)` (singular, pending the card's answer) |
| 0 rows in the second group | heading with no rows, as today (the route never sends this for a non-miss block) |
| a customer with one product | one row, no trailer |
| By location with more than N rows | whole, never capped |
| both scopes offered, DO block capped | `reply 2` |
| DO only offered, DO block capped | `reply 1` |
| SO block capped (either offer shape) | `reply 1` |
| miss block (count 0) | title + miss line, nothing to cap |
| "1" after a capped summary | the full DO list, same as today (offer roster unchanged) |

## Tests (red first)

`sorento_crm_mcp/tests/test_presenters_outstanding.py` gains a cap section; new goldens
`outstanding-report-do-capped.txt` (customer subject, 300 one-unit products, the prod shape) and
`outstanding-report-both-capped.txt` (both scopes, DO block says `reply 2`) under
`tests/fixtures/outstanding/` with their `documentation/plans/chatbot/samples/` mirrors.
`test_fourteen_customers_render_without_elision` stays as is (14 < N).

## Not built here

A cap on the detail list itself. A 300-product dealer's "full list" is one row per outstanding
DO (8 lines each), so that reply can be long too; the owner's ruling kept the list unchanged and
the card names this as a risk, not a question.
