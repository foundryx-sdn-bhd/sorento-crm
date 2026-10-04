# PLAN: a message that names its own domain is an ask, never a roster pick by label

Status: reviewed, PR pending. Track: small fix. Owner ruling 24 Sep 2026 ("okay then i agree,
proceed"). UAC: `chatbot-roster-label-vs-ask-24sep-acceptance-criteria.md`.

## Evidence (prod, 23 Sep 2026 19:48-19:50 MYT, contact 900000039, turns 336-342)

| Turn | Message | Open question on entry | Parser said | Engine did |
| --- | --- | --- | --- | --- |
| 336 | "Srt446 purchased cost" | `tier_pick` (Office / Dealer / End user) | `purchase_cost` | correct: no label match, parser domain used |
| 337 | "Purchase cost srt466-RG" (typo) | none | `purchase_cost` | miss, did-you-mean roster `product_pick` (SRT446-RG / SRTWT5866-RG / SRT449-RG), `payload.domain: purchase_cost` |
| 338 | pick | roster | | position 1 answered; roster stays open (contract 36) |
| 339 | "Photo srt446-RG" | roster, `answered_positions [1]` | `product_attachment`, `domain_in_message: true`, `reference_positions: []` | WRONG: `label_match` on option 1, contract 121 lock, fetched `crm_procurement_po_last_cost_list` |
| 342 | "Srt446-RG list price" | same roster | `master_products`, `domain_in_message: true`, `reference_positions: []`, `requested_attributes: ["price"]` | WRONG: same path, same tool, "last purchase cost" reply |

Both wrong turns show the split inside one turn: `gate_debug.domain` carried the
parser's domain (339 even resolved `photo` to "Product Photos"), while `fetch.tool`
ran the locked roster domain. The trace's `parse.output.domain_hint` differs from
`_parser_raw.domain_hint` because `turn_runtime.lane_parse_output` projects the locked
domain back.

## Cause (one seam)

`app/services/chatbot/turn/decide.py::picked_positions` (about line 250) reads
`_positions_by_label` whenever `reference_positions` is empty. `_positions_by_label` is
an exact, case-insensitive string compare of each current-message entity's `raw` /
`canonical_code` against every option label. It was written for the bare code typed back
at a roster (owner ruling, hand pass 3: "SRTWC286-SH-150" typed back is position 2, and
the parser emits that as an ENTITY because the prompt forbids an entity and a
`reference_position` on the same message). It never reads `domain_in_message`, the
parser's own answer to "is this message asking something of its own". So a code plus a
domain word ("Photo X", "X list price") reads exactly like the bare code.

`decide()` then hits `if positions:` before `_subject_reading`'s table, so the table's own
row (`domain_in_message: true` + entities = NEW_ASK) never runs, and `apply.py`'s
contract 121 lock (`_answer_pending`, then `domain_locked_by_pick` at step 5) keeps the
roster's domain over the parser's.

The parser is right on both turns and needs no change. The prompt already says: "A
message that asks something NEW is NOT an answer to that question, however much it sounds
like one: a product code ... emit its entities and its domain_hint as usual and leave
reference_positions EMPTY."

## Fix

`turn/decide.py::picked_positions`: the label-match arm counts ONLY when
`domain_in_message(verdict)` is not `True`. `reference_positions` (parser-resolved
paraphrase, ordinal, "the DO one") and the "all" broaden are untouched. Nothing else
changes: no `apply.py` edit, no prompt edit, roster retention (contract 36) untouched,
escalation offers (`ESCALATION_OFFER_KINDS`, already reject label match) untouched,
`OUTSTANDING_KINDS` (already read entities as NEW_ASK first) untouched.

Resulting reading, parser as decider, engine as resolver:

| `reference_positions` | `domain_in_message` | entity equals an offered label | reading |
| --- | --- | --- | --- |
| non-empty | any | any | PICK (parser said so) |
| `[]` | `true` | any | ASK: falls to the table, NEW_ASK, parser's domain wins |
| `[]` | `false` / absent | yes | PICK, engine resolves which position (`label_match`) |
| `[]` | `false` / absent | no | REFINE / CARRY, existing table |

Applies to every pending kind that label-matches today: `product_pick`, `customer_pick`,
`tier_pick`, `attachment_type_pick`, any operator-minted `<kind>_pick` / `<kind>_ask`, and
the non-escalation offer kinds `tier_ask`, `team_clarify`, `company_clarify`. Escalation
offers (`team_pick`, `member_offer`, `company_pick`) return before the label arm and are
unaffected.

Three side effects of the same condition, all consistent with the rule (reviewer, 24 Sep):

- `domain_in_message: true` plus a label-equal entity plus `anaphora.backward_reference`
  now reads REFINE / `domain_in_message_anaphora` instead of a label pick. Probed:
  domains follow the parser, `answered_positions` unchanged.
- Under `OUTSTANDING_KINDS`, the same anaphora shape returns REFINE instead of NEW_ASK.
  Theoretical: outstanding option labels ("Delivery order list") are not entity strings.
- `turn_runtime._accepted_pending_field` also calls `picked_positions`: on an
  `escalate_offered` roster, a label match on a member-typed option no longer counts as
  acceptance when the message carries a domain word.

Same-domain restatement ("purchase cost SRT446-RG" over that roster): NEW_ASK, same
domain, same answer, roster stays open through `answer_pending_not_an_answer`. No
regression.

## Measurement attempted (24 Sep 2026)

Wanted: prod turns where `label_match` fired, split by `domain_in_message`, to confirm
bare codes carry `false`. Not measurable from stored data on the 0921 prod copy:
`chatbot.turns.trace` persists the `apply` event payload on 86 of 4777 turns (none with
`label_match`), and `chat_histories.state_trace` is the pre-rearch n8n shape with no
`open_question` and no `domain_in_message`. The replay corpus has no `label_match` case
either. Covered instead by AC-1866 (one live parser probe on a bare code over a roster)
at the end of the lane.

## AC-1866 live probe (24 Sep 2026, lane backend :8088 from this worktree, real parser, prod-copy DB)

`scripts/chatbot_console_check.py --say` chain, contact 900000039, four turns as one
conversation. Tool fetches fail locally (no MCP up), so the reply header is the evidence:

| Turn | Message | Parser `domain_in_message` | Parser `domain_hint` | Parser `reference_positions` | Reply header |
| --- | --- | --- | --- | --- | --- |
| 1 | "Purchase cost srt466-RG" | true | purchase_cost | [] | did-you-mean roster SRT446-RG / SRTWT5866-RG / SRT449-RG |
| 2 | "SRT446-RG" | true | purchase_cost | [1] | "last purchase cost for SRT446-RG" |
| 3 | "Photo srt446-RG" | true | product_attachment | [] | "product attachments for SRT446-RG, Product Photos" |
| 4 | "Srt446-RG list price" | true | master_products | [] | "product information for SRT446-RG" |

Findings:

- Turns 3 and 4 are the two prod defects, fixed live: the parser's domain planned, not
  the roster's.
- Turn 2 shows the real parser resolving the typed-back exact code to
  `reference_positions: [1]` on its own, with `domain_in_message: true` (it treats the
  carried domain as in the message). The pick therefore went through the `positions`
  arm; the engine's `label_match` arm was NOT exercised live. AC-1862 keeps that arm
  covered as a unit test for the shape the prompt describes (entity, no position).
- Had the parser emitted the entity instead of the position on turn 2, the fix would
  have read it as NEW_ASK with `domain_hint: purchase_cost`: same tool, same answer, only
  `answered_positions` left unset. No customer-visible difference on this chain.
- The `apply` trace payload was again not persisted on these turns (see "Measurement
  attempted"), so `decision.why` was read off the reply header, not the trace.

## Tests (pytest, `tests/chatbot/`, engine-level through `apply()` like
`test_rearch_handpass3_owner_17sep.py::_decide`, parser mocked via `_turn_helpers.verdict`)

One file, `tests/chatbot/test_roster_label_vs_ask_24sep.py`:

1. AC-1860 turn 339 shape: roster `product_pick` (three options above, `payload.domain:
   purchase_cost`, `answered_positions: [1]`), verdict entities `srt446-RG` (product,
   `current_message: true`) + `Photo` (attachment_type), `domain_hint:
   product_attachment`, `domain_in_message: True`, `reference_positions: []`. Expect
   decision NEW_ASK / `domain_in_message`, `plan.domains == ["product_attachment"]`,
   `"label_match"` absent from `decision.why`, `"domain_locked_by_pick"` not in
   `rules_fired`, pending still the roster (not closed, not re-answered).
2. AC-1861 turn 342 shape: same roster, entity `Srt446-RG`, `domain_hint:
   master_products`, `domain_in_message: True`, `requested_attributes: ["price"]`. Expect
   `plan.domains == ["master_products"]`.
3. AC-1862 bare code: same roster, entity `SRT446-RG` only, `domain_hint: None`,
   `domain_in_message: False`, `reference_positions: []`. Expect ANSWER / `label_match`,
   `plan.domains == ["purchase_cost"]`, `"domain_locked_by_pick"` in `rules_fired`,
   `answered_positions` gains 1.
4. AC-1863 bare "2": `reference_positions: [2]`, no entities, `domain_in_message: False`.
   Expect ANSWER / `positions`, position 2 settled on focus, domain locked.
5. AC-1864 same-domain restatement: entity `SRT446-RG`, `domain_hint: purchase_cost`,
   `domain_in_message: True`. Expect NEW_ASK, `plan.domains == ["purchase_cost"]`,
   roster still open.
6. AC-1865 second roster kind: `tier_pick` (Office / Dealer / End user), entity `Dealer`
   (tier) + product `SRT446`, `domain_hint: promotion`, `domain_in_message: True`. Expect
   NEW_ASK with the tier landing on focus as an entity, not a pick; and bare `Dealer`
   with `domain_in_message: False` still picks position 2.
7. AC-1867 `tests/chatbot/test_turn_replay.py` corpus stays green, no DIVERGENCES entry.
8. AC-1866 (live, end of lane, captain runs): one console turn "SRT446-RG" over an open
   product roster, assert the real parser emits `domain_in_message: false` and the engine
   reads `label_match`.

Red first (tester-first is folded into the one coder on the small fix track): write all
six unit tests, run, confirm 1, 2, 5, 6 (ask half) fail on main's `decide.py` and 3, 4, 6
(pick half) pass; then the one-condition change; then all green.

## Out of scope, named triggers

- Closing a roster on its own pick (drop contract 36). Trigger: a measured prod chain
  where a retained roster is picked a second time is never observed, once `apply`
  payloads persist on every turn.
- Persisting the `apply` payload on every turn so the measurement above becomes
  possible. Trigger: the next chatbot diagnosis that needs `rules_fired` from prod.
- Making the parser emit `reference_positions` for a typed-back exact code (would move a
  string compare into the LLM). Rejected 24 Sep 2026.
- `apply.py::_roster_is_about` reads the roster's kind through `KIND_FIELD_MAP`, which has
  no `tier` entry, so it looks in `focus.extra["tier"]` and never sees `focus.tier`. A
  `tier_pick` roster is therefore never "still about" the focus and any fetching NEW_ASK
  closes it (`new_ask_closes_stale_roster`). Pre-existing; AC-1865's test pins the
  observed close rather than the intended survival. Trigger: a measured turn where a tier
  roster should have survived an ask.
