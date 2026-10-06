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
   first three, so the did-you-mean set "1, 2, 3, Yes, No" goes out as "1, 2, 3" and the
   Yes / No is answered in words (the text already offers the escalation in a sentence).
3. **No title longer than 20 characters** (after trimming). One over-long title drops the
   whole set for that reply: a partial set would mislabel which option is tappable.
4. **No duplicate titles.** Repeats are dropped, first occurrence kept.

Rules 2 to 4 live at the one seam every reply crosses before it leaves the engine
(`app/services/chatbot/quick_reply_guard.py`, applied in `engine.TurnResult`), so a future
producer that forgets rule 1 still cannot trip Meta. The guard logs once per reply when it
drops anything.

## Where rule 1 is implemented

| Producer | File | Behaviour now |
| --- | --- | --- |
| Pending re-asks and first asks | `turn/pending.py` `quick_replies()` | roster kinds (`customer_pick`, `product_pick`, `tier_pick`, `kind_pick`, `company_pick`, `attachment_type_ask`, `member_offer`, `top_selling_pick`) send none; `team_pick` and the other fixed-option kinds keep theirs |
| Did-you-mean, Yes / No | `lanes/business/answer.py` | unchanged ("Yes, No") |
| Did-you-mean, numbered | `lanes/business/answer.py` | "1,2,3,Yes,No", cut to "1,2,3" by the guard |
| Did-you-mean, product codes | `lanes/business/answer.py` | no buttons |
| Did-you-mean, free-text alternatives | `lanes/business/answer.py` | no buttons |
| Escalation company clarify | `lanes/escalation.py` `_clarify_actions` | no buttons (company names) |
| Escalation team clarify | `lanes/escalation.py` `_clarify_actions` | team names keep |
| Tail escalation offer | `tail/compose.py` | "Yes escalate, No it's okay" unchanged |

No n8n change: the `sendmsg` sub still posts `quick_reply.split(",")` untouched.
