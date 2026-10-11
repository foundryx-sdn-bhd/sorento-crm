# DO-SUMMARY-CAP round 3 console pass (lane test copy, 7 Oct 2026)

Head 0f1760817 (red 8cd3f35c4, fix 08fd12476, docs 0f1760817): the trailer counts only, the
offer line alone points, `1` is the detail list as on main (owner ruling on ask a69045557).
Crew test copy :8103 / MCP :8203 serving this worktree (processes started 18:52:14 MYT while
the worktree sat at 1c529167d, which is 0f1760817 plus one docs-only note on the round 2
evidence file; presenter and backend bytes identical to 0f1760817), shared dev copy
`sorento_cagent_stack`, the REAL parser (`production` label), every turn a dry run
(`is_test`, `test_run_id`). Runner: `scripts/chatbot_console_check.py` over
`tests/chatbot/console_cases/2026-10-07-do-summary-cap-r2.yaml`, contact 445239408.

## Table

| Case | Turn | Reply (decisive lines) | Run | Result |
| --- | --- | --- | --- | --- |
| A, the owner's case (DO-only, HANLIM TRADING, Sep 2026) | ask, then `both` | `*Delivery order outstanding*` ... 20 product rows ... `... and 20 more products` / blank / `Reply 1 for the delivery order list.` | 1791371314 | PASS: no bracket, no `reply` inside the group, no `full list` |
| A | `1` | the 25 `*DO Number:*` rows, no `By product`, no `... and` line | 1791371314 | PASS: the detail list as on main |
| B, both blocks (SYNTALUN MARKETING (EM), Sep 2026) | ask, pick `1` | SO `By product` 20 rows then `... and 35 more products`; DO `By product` 20 rows then `... and 18 more products`; `Reply with a number for detail:` / `1. Sales order list` / `2. Delivery order list` / `3. Both lists` | 1791371314 | PASS: two count lines, the offer unchanged |
| C, both blocks | `2` | the September `*DO Number:*` rows, no summary, no `... and` line | 1791371361, 1791371381 | PASS (1791371314 hit the parser dropping the document word on the ask turn, the known instability) |

Verbatim, case A's second turn (run 1791371314):

```
SRTSSB9360-GM: 8 (O/S: 8)
SRTWT2634-BL: 8 (O/S: 8)
... and 20 more products

Reply 1 for the delivery order list.
```

## Not this lane's (unchanged from round 2)

`1` on a BOTH-scope offer is answered by the SO status lane (`so_status.py:316`) when the
parser carries `order_status: so_outstanding` with the pick (precedent for the detail list:
`2026-09-14-outstanding-owner-rounds.yaml` R23); case B no longer grades that turn. The
parser drops the document word on the ask turn in some runs.

## Browser pass

No screen changed (presenter text only, a shorter line than round 2's, whose 375 px check
in `console-pass-07oct.md` showed the trailer wrapping inside the bubble). Small fix track:
no agent-browser pass this round; the owner's hand test on the crew copy covers 375 px (step 7).
