# UAC: Chat analytics CA-1, facts from traces + Chat overview

Plan: `PLAN-chat-analytics-ca1-facts-09oct.md`. Design: `DESIGN-chat-analytics-09oct.md` (PR #1545)
section 8, lane CA-1. Behaviour card: PR #1546 comment 6073678534.

## Facts

- **CA1-01** A live turn that closes `done` or `failed` gets exactly one `chatbot.chat_ask_facts`
  row (`ask_seq = 1`, `source = trace`) when it closes.
- **CA1-02** A test turn (`is_test`) or a console turn (`ingress = console`) never gets a fact row,
  by the live hook or by the sweep.
- **CA1-03** A turn still `processing`, `queued` or `delegated` gets no row until it closes.
- **CA1-04** Recording the same turn twice writes one row and raises nothing.
- **CA1-05** A manual Retry (attempt 2 of the same respond message) replaces attempt 1's row; an
  older attempt never replaces a newer one.
- **CA1-06** Audience is `staff` when a user is linked to the contact, else `dealer` when the
  contact has a customer link, else `unknown`. `customer_id` and `company_id` are the primary
  link at the time the turn closed.
- **CA1-07** A named product or brand is stored as an item only when its catalogue code equals a
  product or brand code exactly (case-insensitive). Unmatched words are never stored. A product
  shown in the reply's result set is stored as role `shown`.
- **CA1-08** No fact, item or episode column holds customer words, a reply text, an episode
  summary, a phone number or a contact name.
- **CA1-09** If recording a fact fails, the customer's turn still closes and answers normally.

## Outcome rules (first match wins)

- **CA1-10** `status = failed` is `technical` (failed).
- **CA1-11** `low_signal` is `noise`, excluded from counts by default.
- **CA1-12** The escalation lane (`out_of_scope`) is `handover`; when escalation was barred it is
  `refused_policy`.
- **CA1-13** Access refusals, ungranted domains and denied-without-fetch plans are
  `refused_policy`.
- **CA1-14** `not_supported` is `cannot_do` (failed).
- **CA1-15** A clarify menu, a quantity ask, an escalation offer, a held offer, a plan that asks, or
  a required question still being asked is `clarified`.
- **CA1-16** A lookup that missed (or a cross-domain ladder that answered nothing) is `missed`
  (failed).
- **CA1-17** A lookup with no miss and no sections is `answered_empty`.
- **CA1-18** Everything else is `answered`.
- **CA1-19** A dropped phantom answer sets a flag and does not change the outcome.

## Episodes

- **CA1-20** When an episode closes, one `chat_episode_facts` row is written in the same
  transaction, before older episodes are trimmed: turn count, asks (non-noise), failed turns, last
  outcome, handover, domain, intent, close reason. The closed turns' fact rows get its
  `episode_id`.
- **CA1-21** An episode trimmed from `conversation_frames` keeps its `chat_episode_facts` row.
- **CA1-22** If the snapshot fails, the episode itself is still written.

## Load

- **CA1-23** `scripts/chat_facts_sweep.py --since 2026-09-05` fills every closed live turn with no
  fact row and is safe to re-run. A nightly sweep refills the last 3 days.

## Chat overview (Reports screen)

- **CA1-24** A report "Chat overview" appears on the Reports screen only for holders of
  `chatbot.analytics.view`; without it the report is absent and its routes answer 403. The
  permission is granted to every role that holds `system.chat_history.view`.
- **CA1-25** It opens on a summary of outcome by week (Malaysia time), measures Asks and Failed,
  from 5 Sep 2026 to today, staff and noise excluded by default. Failed counts only technical,
  cannot do and missed.
- **CA1-26** Count by Products switches to one row per ask and product, measures Named and Shown,
  product shown by its code.
- **CA1-27** Labels read as words (Answered, Cannot do, Dealer), never raw codes or ids.
- **CA1-28** The report note says the data comes from bot traces from 5 Sep 2026 and that topics
  arrive with CA-2.
