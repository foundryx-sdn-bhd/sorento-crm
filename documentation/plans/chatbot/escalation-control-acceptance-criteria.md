# UAC: escalation control (per contact)

Owner change and rulings, 30 Sep 2026. Every AC names its test.

1. **[BE]** Given the migration, every existing contact reads `escalation_allowed = true`, a new
   contact defaults to true, and access types carry no escalation column; a copy that ran the
   earlier nullable column converges (NULL becomes true, false stays false).
   `tests/test_migration_esc1_0001_escalation_allowed.py`
2. **[BE]** Given a contact whose switch is ticked (the default), even one holding dealer access
   types (Mr Loo), a miss offers the team as before and "talk to a human" is handed over.
   `TestTheContactFlagDecides`, `test_an_allowed_miss_still_offers_the_team`
3. **[BE]** Given the switch unticked, a miss offers no escalation, arms no escalation question
   and shows no routing picker. `test_a_miss_offers_no_escalation_and_arms_nothing`
4. **[BE]** Given the switch unticked, an order or product miss says what could not be found,
   then "Please refer to your salesman." Exact text:
   - order number: `Couldn't find: "SO999001" (order). Please refer to your salesman.`
   - product code: `Couldn't find: "ZZTNOPE9" (product). Please refer to your salesman.`
   - product near a real code: `Here's what you want:` / `• product: ZZTSC07` /
     `But no master products matched these. Please refer to your salesman.`
   `test_a_blocked_miss_names_what_was_asked_then_refers_to_the_salesman`
5. **[BE]** Given the switch unticked, "talk to a human" gets "Please refer to your salesman." and
   no hand-off; a "yes" to an older offer hands nothing over.
   `test_asking_for_a_person_gets_the_salesman_line_and_no_hand_off`,
   `test_a_stale_yes_to_an_old_offer_hands_nothing_over`
6. **[BE]** Staff behaviour is unchanged. `test_a_staff_profile_still_asks_for_a_person_and_is_handed_over`
7. **[BE]** Ticking the switch again restores the offer. `test_ticking_the_flag_again_restores_the_offer`
8. **[BE]** `PUT /contacts/{id}/chatbot` sets `escalation_allowed` (absent leaves it alone) behind
   `user_management.contacts.edit`; every contact read carries it.
   `tests/chatbot/test_escalation_control_api.py`
9. **[FE]** The contact's Chatbot card shows a "Chatbot hands over to support teams" switch, checked by
   default; unticking saves `escalation_allowed: false`. `ContactChatbotSection.test.tsx`
