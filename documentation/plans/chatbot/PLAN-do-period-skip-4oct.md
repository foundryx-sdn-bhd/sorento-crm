# PLAN: per-contact "Skip date-range questions" (DO-PERIOD-SKIP)

Status: planned (4 Oct 2026). Track: full (M), carries an additive migration. UAC:
`do-period-skip-acceptance-criteria.md`.

## Why

#1433 (DO-ASK-SIMPLIFY) and #1435 made the chatbot ask a period for every DO and SO list ask.
The owner wants a few internal contacts never asked. He sets the switch himself; nobody is pre-set.

## Owner rulings (4 Oct)

Q1 switch ON + no dates: no date filter, newest, 20-row cap (pre-#1433). Q2 skip the 31-day cap
too. Q3 any contact. Q4 Chatbot tab under "Chatbot hands over to support teams", in copy SWITCHES.
Q5 every period question, one switch, one helper.

## Period-asking call sites on main (all of them)

| Lane | Rule | Call site |
| --- | --- | --- |
| DO list | `do_ask.range_reply` (do_ask.py:101-145) | lanes/business/__init__.py:2061 |
| SO list | `so_status.period_reply` (so_status.py:208-245) | lanes/business/__init__.py:1445 |

Ranking/sales reports on main ask nothing (no period = this calendar year, fetch.py:983).
#1447 adds `report_ask.PERIOD` (report_ask.py:179 on its branch): wire after it merges.

## Design

1. Migration `dps_0001` (down_revision = current head): `ALTER TABLE respond_contacts ADD COLUMN
   IF NOT EXISTS chatbot_skip_period_questions BOOLEAN NOT NULL DEFAULT false` (template
   esc1_0001_escalation_allowed.py). Model column in models/access.py next to escalation_allowed.
2. API: schemas/user.py RespondContactResponse, contact_service.contact_to_response_dict,
   contacts.py ContactChatbotUpdate + PUT handler, contact_access_copy_service.SWITCHES.
3. Chatbot: `check_access` adds `skip_period_questions` (one SELECT by contact id, fail to false).
   `do_ask.skips_period_questions(ctx)` reads it. `range_reply` returns None when true (fetch as
   asked). The SO list branch skips `period_reply` when true; `so_status.list_text` accepts
   `start=None` (no lower bound) and a row cap of 20 for that path, header without a window.
4. FE: contactChatbotService type + mapping + save, ContactChatbotSection SwitchRow under the
   escalation switch, contact.types.ts field.

## Tests (tester writes red first)

- pytest: migration column + default; GET/PUT round trip; copy switch; check_access key; helper;
  range_reply ON/OFF x no dates / >31 days; SO branch ON/OFF x no dates / >31 days incl. 20 cap;
  scope backstop still first.
- vitest: switch renders under escalation switch, toggling calls save with the field.

## Trigger for more

A second kind of per-contact period preference (e.g. a default window) would earn a profile
object; today one boolean.
