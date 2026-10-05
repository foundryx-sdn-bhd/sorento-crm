# PLAN: photo picker stamp control + carry-on line (PHOTO-PICKER)

Status: Build (both fixes red-then-green, in review). Track: small fix track (backend only, no
migration, no auth change, under 300 lines).

## Asks (owner, 5 Oct 2026)
1. Keep listing every matching code on the photo / attachment picker, but control the
   " - has Product Photos" / " - no Product Photos" stamp the same way the incoming / ETA
   picker does.
2. Bug: picking "1" off that picker replied "Carrying on from Mon 5 Oct: incoming stock for
   SRTWC286-SH." before the photo: the wrong topic, on an immediate pick.

## Findings
- Incoming / ETA control: no setting. A contact on the "Availability only" stock policy (a
  dealer, `eta_policy.is_dealer`) gets incoming rows flagged `dealer_view`
  (`api/v1/incoming_stock.py:138-146`), and the picker prints the roster bare
  (`lanes/business/pickers.py:92-100`). Everyone else, staff included, is stamped. The same
  fact is on the chatbot profile as `Profile.stock_availability_only` (`turn/state.py:198`).
- Photo stamp: `build_suggest_offer` (`lanes/business/answer.py`) stamps three surfaces (the
  require-specific picker, the multi-token did-you-mean, the single-token code mode). Nothing
  checked the audience.
- Carry-on line: `engine._carried_line` treats every entity being `current_message: false`,
  with the code missing from the customer's own recent words, as a carry from memory. A
  picked code comes from our roster, not from what the customer typed, so a pick read as a
  carry, and the newest closed conversation naming the code (an incoming chat) supplied the
  topic.

## Ruling (crew answer (a), owner's own words)
| Audience | Attachment picker |
|---|---|
| Dealer (Availability-only contact) | every code listed, no stamp, original order |
| Other contact | stamped (unchanged) |
| Staff | stamped (unchanged) |

Applies to every attachment type and to all three surfaces. The carry-on fix is as proposed.

## Changes
- `build_suggest_offer`: `profile.stock_availability_only` turns stamping off (`dym_ok =
  False`), so the existing unannotated wording renders.
- `_carried_line(decision=plan.trace.decision)`: a turn decided as ANSWER (it answered this
  conversation's open question) prints no carried line.

## Tests
- `tests/chatbot/test_photo_picker_carry_line.py` (Postgres): a pick prints no line, while a
  real carry still does.
- `tests/chatbot/test_photo_picker_dealer_stamps.py`: dealer bare on all three surfaces and
  on the several-type ask; contacts and staff stamped.
