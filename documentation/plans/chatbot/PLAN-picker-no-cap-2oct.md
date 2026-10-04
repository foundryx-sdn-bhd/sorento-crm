# PLAN: chatbot picker no cap (PICKER-NO-CAP)

Status: Build done, in review (standard track because of a one-statement data migration; diff
well under 300 lines, no auth/RBAC change). PR #1436.

## Journey

Owner (2 Oct 2026): the chatbot picker is capped at 10 options, notably in the incoming search.
Every option must be reachable and visible.

## Findings (where the 10 came from)

Not a channel limit: every chatbot picker is a plain numbered text message, never a WhatsApp
interactive list.

1. `chatbot_entity_kinds.roster_cap` (owner ruling 20 Sep 2026, default 10, API range 2 to 50,
   50 = security review S3 ceiling). The incoming search lists products, so it reads the
   PRODUCT kind's cap at `lanes/business/gate.py:900`; the same column cuts the customer picker
   (`gate.py:1010`), the did-you-mean list (`lanes/business/miss_suggest.py`) and the old narrow
   arms (`turn/narrow.py`). The 'Inbound shipment' kind is never read for the incoming picker.
2. `MAX_NAMED = 10` in the stock pickers (`turn/task.py::pick_question`,
   `dealer_stock.py::did_you_mean`): up to 20 options stored, only 10 printed.
3. `turn/context.py::_MAX_PENDING_OPTIONS = 10`: the parser's view of the open question only. It
   ends with "(+N more)" and protects the parser token budget (contract 6.2); the dealer sees
   every option. Left as is.

4. Found by the crew-tester chat pass at 83754a68 ("incoming AMS", 20 matches, 15 listed): the
   chatbot's own resolve body sent `limit: 15` (`lanes/business/resolve_gate.py::
   resolve_entity_body`), and the route cuts every token's matches to it before any roster cap
   is read. The lookup limit is now 50, the roster ceiling (the account case keeps 200).

## Decision (crew answer (c), 2 Oct)

No schema change and the setting stays. Every roster_cap still at 10 moves to 50 (the S3
ceiling): migration `picker_no_cap_0001` (column default + UPDATE where 10), seeds, model and
API defaults, the Policy / gate / narrow fallbacks and the config modal's new-kind default. The
resolver returns at most 20 to 25 matches per typed word, so in practice nothing is cut. The
stock pickers print every stored option.

Not changed: the ranking picker's 5-option rule (R8), the SEC-S2 slot cap of 20, the resolver's
own search limits.

## Tests

- `tests/chatbot/test_picker_no_cap.py` (stock pickers, gate incoming and customer rosters, seeds,
  model default).
- Existing pins of 10 updated: `test_stock_ask_ht26_family_pick.py`,
  `test_stock_ask_ht26_r4_owner_replay.py`, `test_rearch_r3_roster_cap.py`,
  FE `ChatbotEntityKindModal.test.tsx`.
- Hand test: `laneboard/scripts/1436.md`.
