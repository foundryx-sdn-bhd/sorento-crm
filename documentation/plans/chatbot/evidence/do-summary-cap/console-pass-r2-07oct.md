# DO-SUMMARY-CAP round 2 console pass (lane test copy, 7 Oct 2026)

Head 8ae0621e1 (red b3e04d966, fix ccc4983db, docs 7487be97a, origin/main 292a366ee merged),
served by the crew test copy on :3103 / :8103 / MCP :8203 (`lanes.json` sha 8ae0621e1e1b),
shared dev copy `sorento_cagent_stack`, the REAL parser (`production` label), every turn a dry
run (`is_test`, `test_run_id`). Runner: `scripts/chatbot_console_check.py` over
`tests/chatbot/console_cases/2026-10-07-do-summary-cap-r2.yaml`, contact 445239408 (holds
`sales_orders.outstanding`; the owner's own contact 487555417 carried an open stock ask from a
later hand test, and "both" answered that ask, run console-check-1791358754).

Round 1's run (`console-pass-07oct.md`) still shows `(reply 1 for the full list)`; this file is
the R2 wording live.

## Table

| Case | Turn | Reply (decisive lines) | Run | Result |
| --- | --- | --- | --- | --- |
| A, the owner's case (DO-only) | `HANLIM TRADING delivery order outstanding 01/09/2026 to 30/09/2026` | `Which one do you mean?` 1. HANLIM TRADING SDN BHD / 2. ... (CERAMIC & ELLECI) | 1791358874, 1791359093 | as the owner saw |
| A | `both` | `*Delivery order outstanding*` / `Delivery orders: 25` / ... 20 product rows ... / `... and 20 more products (reply 1 for the delivery order list)` / blank / `Reply 1 for the delivery order list.` | same | PASS (no `full list`, no `reply 2`) |
| A | `1` | the 25 `*DO Number:*` rows, no `By product`, no trailer | same | PASS |
| B, both blocks (SYNTALUN MARKETING (EM), Sep 2026) | ask + customer pick `1` | SO block: 20 rows then `... and 35 more products (reply 1 for the sales order list)`; DO block: 20 rows then `... and 18 more products (reply 2 for the delivery order list)`; `Reply with a number for detail:` / `1. Sales order list` / `2. Delivery order list` / `3. Both lists` | 1791359093 (and 1791358956, 1791358984, 1791359008 without the range: `171` / `129` more) | PASS: two trailers, two numbers, each the offer entry's own words |
| C, both blocks | `2` | the 11 `*DO Number:*` rows for September, no summary, no trailer | 1791359168 (also 1791358956, 1791358984 without the range) | PASS: `reply 2` lands on the DO list |
| B | `1` | `Sales orders for SYNTALUN MARKETING (EM) SDN BHD, 1 Sep 2026 to 30 Sep 2026:` / `SO422013 - 18 Sep 2026 - Open - not delivered yet` ... | 1791359093 | NOT the SO detail list; engine finding below |

Verbatim, case A's second turn (run 1791358874, the owner's exact data):

```
Product: all
Customer: HANLIM TRADING SDN BHD
Location: all
Order date: 01/09/2026 to 30/09/2026

*Delivery order outstanding*
Delivery orders: 25
DO qty: 998
...
SRTSSB9360-GM: 8 (O/S: 8)
SRTWT2634-BL: 8 (O/S: 8)
... and 20 more products (reply 1 for the delivery order list)

Reply 1 for the delivery order list.
```

## Not this lane's (reported on the PR)

1. `1` on a BOTH-scope offer does not print the SO detail list. The parser returned
   `order_status: so_outstanding`, `document: []`, `reference_positions: [1]` on every attempt
   (runs 1791358956, 1791358984, 1791359008, 1791359093) and the engine answered from the SO
   status lane (`so_status.py:316`, `Sales orders for ..., <window>:` rows), or asked
   `Which period for ...?` (`so_status.period_reply`, Q1 (a)) when the ask had no date range.
   Round 1 only demonstrated `2`. The committed precedent for `1` landing on `1. *SO Number:*`
   is `2026-09-14-outstanding-owner-rounds.yaml` R23. The offer roster the lane reads off the
   text is unchanged by the cap (`test_do_summary_cap_offer.py`), and the `2` pick on the same
   roster lands, so the trailer neither causes nor hides it. Same class as round 1's `3` finding
   (a status carried with the pick). UNVERIFIED on origin/main itself: the round 1 copy on
   :8101 was already down when this was found.
2. The live parser drops the document word on the ask turn in some runs (`order_status:
   outstanding`, `document: []`, then `Outstanding for which document?`): case C failed that
   way in 1791359093 and 1791359186 and passed in 1791358956, 1791358984, 1791359168. The
   instability round 1 and #1515 recorded.

## Browser pass

No screen changed (presenter text only; the trailer line is a few characters longer than
round 1's, whose 375 px check in `console-pass-07oct.md` showed it wrapping inside the bubble
with no horizontal scroll). Small fix track: no agent-browser pass this round.
