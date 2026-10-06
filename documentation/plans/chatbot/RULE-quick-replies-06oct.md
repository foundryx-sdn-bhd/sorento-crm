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
| Did-you-mean, numbered | `lanes/business/answer.py` `_numbered_quick_reply` | "1,2" / "1,2,3" for two or more picks; "1,Yes escalate,No it's okay" for one |
| Did-you-mean, product codes | `lanes/business/answer.py` | no buttons |
| Did-you-mean, free-text alternatives (codes, dates, names) | `lanes/business/answer.py` | no buttons |
| Escalation company clarify | `lanes/escalation.py` `_clarify_actions` | no buttons (company names) |
| Escalation team clarify | `lanes/escalation.py` `_clarify_actions` | team names keep (the guard still drops a set with a name over 20 chars) |
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
- The console check (`scripts/chatbot_console_check.py`) needs the live parser and so runs
  on the owner's test copy, not in the cloud sandbox: `laneboard/scripts/1500.md`.
