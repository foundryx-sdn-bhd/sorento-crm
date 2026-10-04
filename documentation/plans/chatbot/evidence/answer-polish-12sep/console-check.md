# Console check, lane stack, 12 Sep 2026

Backend :8080 (worktree `chatbot-answer-polish`, coder commit on top of origin/main #834),
MCP :8765 with `CRM_BASE_URL=http://localhost:8080`, DB `sorento_ai_automation_0907` (prod copy
of 7 Sep, upgraded to alembic 512). Contact 900000008, stock policy as prod's copy has it:
`detailed`, `hide_zero_locations = true`. Runner:

    venv/bin/python scripts/chatbot_console_check.py --say "ETA SRTWT6236" \
        --say "SRT6550-DIY ETA" --say "SRTKT71SS" --say "stock CWCX7605-S-ECO" \
        --base-url http://127.0.0.1:8080 --contact 900000008

## Before (same stack, origin/main #834, same contact)

"ETA SRTWT6236" -> `But no incoming matched these. Would you like me to escalate to purchasing
team?` with trace `tool=crm_incoming_stock_list` and NO rung (finding 4).
"SRT6550-DIY ETA" -> `No incoming and no stock for SRT6550-DIY, but PO is placed:` + plain
labels, trace `rung=crm_inventory_stock_balance_list(0 rows)` (findings 3 and 5).

## After

### "ETA SRTWT6236" (finding 4 + 3 + 5)

    Here's what you want:
    • product: SRTWT6236-GY

    But no incoming matched these.
    But here are the stock details for the requested products:

    - *Product Code:* SRTWT6236-GY
    *Warehouse:* BUKIT RAJA
    *System Location:* BRW
    *Quantity On Hand:* 0
    *Outstanding:* 0

    No incoming and stock is 0 at every location for SRTWT6236-GY, but PO is placed:
    *Product Code:* SRTWT6236-GY
    *Ordered:* 99
    *Outstanding:* 99
    *PO date:* 2026-07-13
    *Location:* BRW

    Would you like me to escalate to purchasing team?

trace: `rung=crm_inventory_stock_balance_list(1 rows)  rung=purchase_order(1 rows)`

### "SRT6550-DIY ETA" (finding 5, detailed mode, hide-zero ON)

    But no incoming matched these.
    But here are the stock details for the requested products:

    - *Product Code:* SRT6550-DIY
    *Warehouse:* BUKIT RAJA
    *System Location:* BRW
    *Quantity On Hand:* 0
    *Outstanding:* 21

    No incoming and stock is 0 at every location for SRT6550-DIY, but PO is placed:
    *Product Code:* SRT6550-DIY
    *Ordered:* 300
    *Outstanding:* 300
    *PO date:* 2026-07-27
    *Location:* BRW

Same facts as the compact reply the owner pasted (`*Total:* 0 (O/S: 21)`).

### "SRTKT71SS" (finding 1)

Every product's `*Specs:*` line lists all keys, e.g. item 1:
`Product class: Tap, Type: kitchen_tap, Pull-out shower: True, Steel grade: 304, Water
supply: mixer, Material: stainless_steel, Mounting: pillar_mounted, Spout: pull_out, Brand:
SORENTO, Finish or colour: black` - no "and N more" anywhere in the reply.

### "stock CWCX7605-S-ECO" (finding 2)

Incoming block renders (container WHSU8712991, ETA 2026-08-22, 522 pcs, BRW 481 / BRW-BB 41)
and the reply carries NO "I have attached the file(s) below." sentence.

Note, out of scope: the entity line reads `• product: CWCX7605-S-ECO (+1 more)` because the
resolver matched CWCX7605-S-ECO-NEW as well; that "(+N more)" is the entity summary, not the
spec cap.

## Graded case file

Review fix round (commit `f07f93df8`), backend :8080 restarted on that commit, MCP :8765 up,
contact 900000008 (default in the yaml), the four cases in
`tests/chatbot/console_cases/2026-09-12-answer-polish.yaml`:

    set -a; source <(grep -E "^EXTERNAL_API_KEY=" .env); set +a
    venv/bin/python scripts/chatbot_console_check.py \
        tests/chatbot/console_cases/2026-09-12-answer-polish.yaml \
        --base-url http://127.0.0.1:8080

First two attempts at the finding-1 case text needed a rewrite (parse variance, not a code
defect - measured against this tenant's live `production`-labelled prompt):

* `"SRTKT71SS spec"` (console-check-1789179209, -1789179229, two runs): parsed as an
  `attachment_type` ask ("Technical Specifications") rather than a product attribute ask,
  branch answered "no technical drawing matched these" - never reached the specs
  projection at all.
* `"SRTKT71SS"` alone (console-check-1789179271): read as `low_signal`
  ("Could you clarify which product code you want me to check?") - a bare code with no
  verb does not carry enough intent for this parser.
* `"SRTKT71SS product details"` (D12's own no-property phrasing) reaches `business_query`
  deterministically with `requested_attributes: []`, landing on AC-1's no-attribute
  branch. Confirmed stable over two consecutive runs (console-check-1789179308 and
  -1789179324, both 4/4 green) and the yaml case text updated to match.

### Final run (console-check-1789179308)

    console-check-1789179308  4 cases against http://127.0.0.1:8080  parser prompt: whatever the `production` label points at
    lane switches before: enabled=True lanes=['access_denied', 'escalate_offer', 'out_of_scope', 'ideate', 'offer_hold', 'escalation_declined', 'check_promotion', 'low_signal', 'clarify_menu', 'not_supported', 'stock_denied', 'demand_qty', 'business_query']
    lane switches for the run: enabled=True lanes=13
    PASS  finding 4 - a typed prefix (no exact suffix) still reaches the PO rung branch=business_query  "Here's what you want: • product: SRTWT6236-GY  But no incoming matched these. But here are the stock details for the req"
    PASS  finding 5 - detailed mode states zero-everywhere, not unknown branch=business_query  "Here's what you want: • product: SRT6550-DIY  But no incoming matched these. But here are the stock details for the requ"
    PASS  finding 1 - a product with many specs lists all of them, no "more" branch=business_query  'Here are the matching products.  1. *Product Code:* SRTKT71SS *Description:* SORENTO S/STEEL PILLAR MOUNTED SINK TAP WIT'
    PASS  finding 2 - a stock ask with incoming rows never claims an attachment send branch=business_query  "Here's what you want: • product: CWCX7605-S-ECO (+1 more)  But no inventory matched these. No stock for CWCX7605-S-ECO. "
    lane switches restored: enabled=True lanes=['access_denied', 'escalate_offer', 'out_of_scope', 'ideate', 'offer_hold', 'escalation_declined', 'check_promotion', 'low_signal', 'clarify_menu', 'not_supported', 'stock_denied', 'demand_qty', 'business_query']

    4 passed, 0 failed  (console-check-1789179308)

Rerun (console-check-1789179324) is byte-identical apart from the run id and the run's own
`4 passed, 0 failed` line - all four cases green twice in a row.
