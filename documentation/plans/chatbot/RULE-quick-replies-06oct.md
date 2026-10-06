# Rule: quick replies are fixed short options only (QUICK-REPLY-NAMES)

Status: implemented (small fix track, lane QUICK-REPLY-NAMES, 6 Oct 2026)

## The incident

Prod, 6 Oct 2026: the customer picker

```
Which customer do you mean? Please choose:
1. SAMPLE ONG BROTHERS (SRT)
2. ONG BROTHERS TILING TRADING SDN BHD (SRT)
3. ONG BROTHERS TILING TRADING SDN BHD (SAMPLE) (SRT)
```

went out with the three full names as WhatsApp quick-reply buttons. Meta rejected the
message with error #131009 "Duplicate button title": a button title is limited to 20
characters, and options 2 and 3 are the same string once truncated. The customer received
nothing.

## The rule (owner, 6 Oct 2026)

1. **A quick-reply set goes out only when EVERY title is a fixed short option.** Yes / No,
   the numbers "1" "2" "3", team names, "This month" / "Last month", "Yes escalate" /
   "No it's okay". A set whose titles are roster or free-text values (customer, product,
   tier, kind, company names, free-text alternatives, product codes) sends NO buttons. The
   numbered list in the message text is the picker; a typed number or the exact label still
   resolves (`turn/decide.py`, `picked_positions`).
2. **At most 3 buttons.** WhatsApp shows three. A set with more than three is cut to its
   first three (owner confirmation 6 Oct 11:30): the did-you-mean numbered set is "1, 2, 3"
   and the Yes / No is answered in words (the text already offers the escalation in a
   sentence). Beside a single pick all three fit, so that set stays "1, Yes, No".
3. **No title longer than 20 characters** (after trimming). One over-long title drops the
   whole set for that reply: a partial set would mislabel which option is tappable. This
   reaches team names too: a team clarify naming "Project Customer Service" (24 chars)
   goes out as text only, and the typed name or number resolves.
4. **No duplicate titles.** Repeats are dropped, first occurrence kept, compared without
   case.

Rules 2 to 4 live in `app/services/chatbot/quick_reply_guard.py` and run at the three places
a reply's `quick_replies` string is composed before it is persisted and sent:
`engine._reply_of` (the business lane's reply and its send action), `engine.run_tail` (the
canned / escalation / casual tails) and `engine._seal_send` (a lane's own hand-built action
when the tail sealed none). A future producer that forgets rule 1 still cannot trip Meta.
The guard logs one INFO line per reply when it drops anything. A set it did not change is
returned byte-identical.

## Where rule 1 is implemented

| Producer | File | Behaviour now |
| --- | --- | --- |
| Pending re-asks and first asks | `turn/pending.py` `quick_replies()` | allowlist `FIXED_OPTION_KINDS` = `team_pick`, `outstanding_scope`, `outstanding_detail`, `sales_report_detail` keep theirs; every other kind (`customer_pick`, `product_pick`, `tier_pick`, `kind_pick`, `company_pick`, `attachment_type_ask`, `member_offer`, `top_selling_pick`, any `<kind>_pick` / `<kind>_ask` minted off `chatbot_entity_kinds`) sends none. A `team_pick` whose options carry `payload.company` (the company clarify `answer_bridge.py` mints) sends none |
| Did-you-mean, Yes / No | `lanes/business/answer.py` | unchanged ("Yes escalate, No it's okay") |
| Did-you-mean, numbered (n8n-completed `/complete` suggest offer) | `lanes/business/answer.py` `_numbered_quick_reply` | "1,2" / "1,2,3" for two or more picks; "1,Yes escalate,No it's okay" for one. The CRM-completed lanes never take this path: their did-you-mean is a `product_pick` roster (`dealer_stock.py`, `answer_bridge._miss_question`) and sends no buttons |
| Did-you-mean, product codes in a sentence (`/complete` path) | `lanes/business/answer.py` | codes dropped; the fixed "Yes escalate, No it's okay" pair for the escalation question the sentence ends on stays |
| Did-you-mean, free-text alternatives in a sentence (codes, dates, names; `/complete` path) | `lanes/business/answer.py` | values dropped; the fixed Yes / No pair stays, except the customer service date miss, which never carried one |
| Escalation company clarify | `lanes/escalation.py` `_clarify_actions` | no buttons (company names) |
| Escalation team clarify | `lanes/escalation.py` `_clarify_actions` | team names keep (the guard still drops a set with a name over 20 chars) |
| Escalation product did-you-mean (`clarify_product`, minted as a `team_pick` with product options) | `engine._question_offered` + `turn/pending.py` | no buttons (options are products, not teams) |
| Tail escalation offer | `tail/compose.py` | "Yes escalate, No it's okay" unchanged |

No n8n change: the `sendmsg` sub still posts `quick_reply.split(",")` untouched.

## Verification

- `tests/chatbot/test_quick_reply_names.py`: the guard, the pending allowlist, the
  numbered set, the engine seams, and the incident conversation end to end through the real
  engine (three names -> list intact, no buttons, "2" picks the second customer).
- Updated: `test_rearch_r4_miss_engine.py` (product pick now none, team pick keeps),
  `test_top_selling_round4.py` (cap measured on `team_pick`), `test_s5_escalation_lane.py`
  (company clarify none), `test_s5_escalation_seams.py` (team clarify: short names reach
  the persisted reply, a 24-char name drops the set everywhere).
- `tests/chatbot/test_console_pass_quick_reply_names.py`: the Chatbot Console's own
  service (`console_service.run_console_turn`) with the parser stubbed to recorded
  verdicts and the resolver real over seeded customers and products: the incident
  conversation, "2" picking, the stock did-you-mean (no buttons), "2" picking a code.
- Round 2 (6 Oct, local lane): the fixed-option kinds are tested in the option shape
  production builds (`turn/compose.py::_lane_question` stamps `entity_type = kind` on the
  outstanding and sales report options), after review found the options inspection was
  suppressing them; it now runs for `team_pick` only. The `/complete` suggest offer's
  "Try: ..." arm, its customer service date miss and its numbered set are pinned by direct
  `build_suggest_offer` tests.
- Real parser pass (6 Oct, local lane, in-app Chatbot Console against the shared dev data,
  nothing stubbed): "DO for ong brothers" prints the three ONG BROTHERS lines with no
  buttons and "2" picks the second; a product did-you-mean prints three codes with no
  buttons and a typed code picks; the escalation offer keeps "Yes"; the outstanding offer
  keeps "Delivery order list". The same message on `origin/main` sends the customer names
  as buttons. Steps and exact values: `laneboard/scripts/1500.md`.
- Captures of `build-suggest-offer` in the full n8n corpus (not vendored here) that carry a
  code-mode or numbered `suggest_quick_reply` now differ on that one field.

## Known, not changed here

- "This month" / "Last month": the period question (`do_ask.py::range_reply`) is plain text
  and has never carried buttons, so there is no set to keep.
- Tapping "Delivery order list" under the outstanding summary prints the summary again; the
  typed number opens the list. `origin/main` does the same.
- The 20 character limit is measured on the trimmed title. n8n splits the string on ","
  without trimming, so a title after the first in a ", " joined set travels with a leading
  space. Unchanged by this lane (no n8n change).
