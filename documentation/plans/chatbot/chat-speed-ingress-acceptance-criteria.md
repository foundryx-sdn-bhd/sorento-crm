# UAC: CHAT-SPEED direct ingress

Plan: `PLAN-chat-speed-ingress.md`. Each AC names its test.

- **AC-1** Mode `off` (the default): a signed incoming message is stored exactly as today and
  no turn job is enqueued. (`test_off_mode_enqueues_nothing`)
- **AC-2** Mode `all`: a new signed incoming WhatsApp message from the contact enqueues exactly
  one turn job on `chat`, and the webhook still answers 200 `created`.
  (`test_all_mode_enqueues_one_job`)
- **AC-3** Mode `contacts`: only contacts in the list enqueue.
  (`test_contacts_mode_only_listed`)
- **AC-4** Nothing is enqueued for a duplicate delivery, an outgoing message, a non-WhatsApp
  channel, a non-text message, a staff sender, a message whose clock is over 120 s old (or
  missing), a bad signature, or a contact over 30 messages in a second. (`test_skip_*`)
- **AC-4b** The job looks the contact up first. `is_human_intervened` true, or a failed
  lookup, runs no turn; the looked-up record is the envelope contact.
  (`test_human_takeover_runs_no_turn`, `test_failed_lookup_runs_no_turn`,
  `test_the_looked_up_contact_is_the_envelope_contact`)
- **AC-5** An enqueue failure (redis down) never fails the webhook: the message is stored and
  the answer is 200. (`test_enqueue_failure_is_swallowed`)
- **AC-6** The job runs the turn with `ingress = "respond_webhook"` and POSTs its actions to
  the sender URL with the sender key header, redirects off. Then the n8n copy of the same
  message returns `duplicate: true` with no second turn. (`test_job_posts_actions_then_n8n_copy_is_duplicate`)
- **AC-7** A duplicate turn or a turn with no actions posts nothing.
  (`test_job_posts_nothing_for_duplicate`)
- **AC-8** A sender POST that fails (non-2xx or transport) writes a failed outbound
  `integration_log` row and a trace record on the turn. It raises nothing.
  (`test_sender_failure_is_recorded`)
- **AC-9** A sender URL on loopback or a private host is refused at use time; nothing is
  posted and the failure is recorded. With no URL configured, nothing is posted and the turn
  records why. (`test_sender_url_guarded`, `test_no_sender_configured`)
- **AC-10** While direct ingress covers a contact, every live turn of it takes the
  per-contact ticket even with S7 ordering off. Two direct turns, or a direct turn and the n8n
  copy of a later message, never overlap. (`test_direct_turn_is_ordered_with_s7_off`,
  `test_n8n_copy_of_a_covered_contact_waits_for_the_direct_turn`)
- **AC-11** Settings GET returns both switch fields and PUT saves them. A mode outside
  `off|contacts|all` answers 422. (`test_settings_roundtrip`, `test_settings_rejects_bad_mode`)
- **AC-12** The respond workspace accepts a sender URL and key. The key is never returned
  (`has_chatbot_sender_key` only), and an unsafe URL is refused at save.
  (`test_workspace_sender_fields`)
- **AC-13** The chatbot package import boundary holds. (`test_import_boundary.py`, unchanged)
