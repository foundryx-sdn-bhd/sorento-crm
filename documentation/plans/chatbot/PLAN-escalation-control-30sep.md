# PLAN: escalation control per contact (ESCALATION-CONTROL)

Status: implemented on the lane branch, in review (PR #1406). Track: full (migration).
Owner request 30 Sep 2026; redesigned the same day (owner change: per contact, not by access type).

Owner: "we need to be able to control each contact that they cannot access the escalation:
cannot force escalate, won't be offered escalation; this is for dealer".

## Journey
Staff untick "Chatbot hands over to support teams" on a contact's Chatbot card. From then on, that contact
is never offered a hand-off to a person (no "Would you like me to escalate to ... team?", no
routing picker), and asking for a person or answering an old offer gets "Please refer to your
salesman." with nothing handed over. When that contact asks for an order or product the bot
cannot find, the reply still says what could not be found, then "Please refer to your
salesman." Every other contact behaves as before.

## Design
- One per-contact flag: `respond_contacts.escalation_allowed` BOOLEAN NOT NULL DEFAULT true
  (migration `esc1_0001_escalation_allowed`, additive). Every existing contact is backfilled
  allowed and new contacts default allowed (owner ruling, 30 Sep 2026: blocking is only by
  unticking the contact page). Access types do not decide it and carry no column. The migration
  also converges a copy that ran this lane's earlier nullable column (NULL reads as allowed).
- `turn_runtime.load_profile` reads the flag into `Profile.escalation_allowed`
  (`_escalation_allowed`, fail-open: a missing value is allowed). A respond.io id on two rows is
  blocked only when every row is unticked.
- `turn/state.py::offers_escalation(profile)` replaces the staff-only check at every offer gate
  (composer offer, cross-domain ladder rung, miss composers, suggest-offer `_cont`, CS roster
  combine, silent-company offer, the small-talk fallback's team).
- Miss wording (owner ruling Q2): where an allowed contact's miss offers "Would you like me to
  escalate to X team?", a blocked contact's reads "Please refer to your salesman." after the
  same miss sentence (`answer.py` `_esc_offer`, `what_you_want_reply`, the date-window clause,
  `_cont`; `turn/compose.py`'s composer offer; the zero-stock ladder puts its block above the
  line exactly as it does above the offer). Never the bare line on a miss.
- Forced path: `turn/apply.apply` closes an open escalation offer on entry (a stale "yes" has
  nothing to accept), withholds any offer it would ask or carry, and `_lane` returns
  `escalation_barred` (routed as `out_of_scope`, no new wire branch kind). The engine guards in
  front of `_run_escalation_arm` and answers "Please refer to your salesman." The routed trace
  line says "Escalation withheld".
- Backstop: `escalation_control.strip_offers` runs in `engine._run_answer`, `run_tail` and the
  casual lane for a blocked contact, so an offer sentence, routing picker or armed question from
  any composer is removed and the salesman line stands in its place.
- UI: Contact > Chatbot card, "Chatbot hands over to support teams" switch (the card's existing
  `SwitchRow`), saved by `PUT /contacts/{id}/chatbot` (`escalation_allowed`, absent = leave
  alone, guarded by `user_management.contacts.edit`).

## Superseded (kept for the record)
The first cut put `escalation_allowed` on `contact_access_types` (seeded false for every
"... Dealer" type, merged across a contact's types, "Inherited: allowed via Sorento Office" on
the contact page, a checkbox in the access-type editor, and permission gates on the access-type
write routes). The owner replaced it with the per-contact switch above; those pieces are gone and
the access-type files match main again.

## Grill (post-hoc, 30 Sep 2026)

Run after the owner's process audit, as one crew-ask on PR #1406; premises re-checked against the
code after the "get your facts right" ruling. Rows about access types were answered for the first
design and are superseded by the owner change.

| # | Decision | Owner answer |
| --- | --- | --- |
| R-a | Blocked reply | "Please refer to your salesman." (`turn/task.py:42` REFER_TO_SALESMAN), ruling (b) |
| Q1 | Scope and label | Covers every hand-off (the engine guard is on the branch kind whatever the team, `engine.py:5258`), so the label is "Chatbot hands over to support teams" |
| Q2 | A blocked miss | Say what could not be found (the order no. or product), then "Please refer to your salesman."; never the bare line. Measured before the change: `Couldn't find: "SO999001" (order).` with nothing after it |
| Q5 | Unreadable flag | Fail open (allowed): OK |
| Q6 | Reporting | Keep branch kind out_of_scope, lane escalation_barred; the n8n cutover's one-time SLA precondition (`documentation/plans/chatbot/n8n-changes.md:586-598`) excludes that lane |
| Q7 | Staff | Unchanged: never offered, may ask; the switch can block a staff contact too |
| Q8 | Pickers for blocked contacts | Still asked; only their escalation half goes |
| Owner change | Control | Per contact, not by access type |
| Backfill | Existing and new contacts | All allowed (TRUE); blocking only by unticking the contact page |
| Q3, Q4 | Access-type merge, new dealer types | Superseded by the owner change |
| Label (hand test, 1 Oct) | Switch wording | "Chatbot hands over to support teams", helper "When off, the chatbot tells this contact to refer to their salesman." (owner: the first label read oddly, since referring to the salesman is also an escalation). Behaviour PASS on the owner's hand test |

See `escalation-control-acceptance-criteria.md`.
