# Behaviour card: INTENT-FRAMES (per-question conversation frames)

Lane INTENT-FRAMES, size L, owner approved 4 Oct 2026. Built on #1471 (STUCK-QTY-LOOP, the
central held-question rule `turn/held.py`) and #1470 (LOWSTOCK-SEMANTIC, the per-report
`focus.low_stock` frame), both merged into this branch first.

## The problem, measured in code

Conversation state is ONE sticky `Focus` (`turn/state.py:108`): products, customers,
warehouse, brands, `extra` (sales_agent, specification, order...), document, status,
sales_channel, date_window, sort, plus the held questions. It has no owner and no expiry.

- `decide()` (`turn/decide.py:551-572`) reads any message with entities and no domain word
  as REFINE ("refines_standing_subject"), whatever intent it is. `apply._focus_rules`
  (`turn/apply.py:1015-1170`) then COMBINES the new entities with every carried axis.
- #1471 added `focus.intent` and the `NEW_INTENT` verdict marker, but on a new intent it
  drops only `extra["sales_agent"]` (`apply.INTENT_OWNED_EXTRA`). Every other carried axis
  of the old ask still rides into the new one and reaches the resolver and the miss list.
- `answer_bridge._miss_triggers` (`answer_bridge.py:1585-1639`) lets the resolver's
  `not_found` exit replace the lane's reply, with three one-flag exemptions
  (`so_status`, `low_stock_report`, `required_ask`).
- Nothing expires the subject: a customer who comes back tomorrow and types "stock?" is
  answered for yesterday's product.

## The rule (one frame, owned by its intent)

The focus IS the frame. `focus.intent` (from #1471) names its owner. Each axis is either
SUBJECT (what the question is about) or OWNED by the intent (how that one ask is filtered):

| Class | Axes |
|---|---|
| Subject | products, customers, warehouse, brands, outstanding_brand_ids, `extra` kinds that describe the products (specification, category, attachment_type, order) |
| Intent-owned | document, status, sales_channel, date_window, sort, `extra["sales_agent"]`, top_selling, low_stock, required_ask, set_page, set_clarify, tasks |
| Contact (not the frame) | tier, the profile |

1. **Same intent, or no intent named** (a pick, "yes", a bare value): today's reading,
   unchanged. A refinement narrows the frame ("taiyang only" on a low stock report narrows
   that report's supplier, #1470).
2. **A new intent** (a `business_query` whose `intent_hint` is a known intent other than
   the frame's, and that answers no held question): a clean frame. Every intent-owned axis
   goes. A subject axis carries ONLY when the parser says the message is about the same
   subject: `continuation: true` (defined by this lane's prompt addendum) or
   `anaphora.backward_reference: true`, and only on an axis this message did not name
   itself. `entity_op: "reuse"` alone is NOT a carry signal: measured live (gpt-5.4-mini,
   4 Oct 2026), the parser also emits it for a fresh "low stock report for sorento water
   tap" right after a stock check. An entity the parser copied from the conversation
   (`current_message: false`) never lands on a new frame. An escalation request, a
   clarification or a casual turn never starts a frame, and never relabels the frame's
   intent. No word rules: the engine reads only those parser fields.
3. **Stale entities never reach the resolver or the miss list**: the resolver and the miss
   composer read the frame after rule 2 ran, so an axis the new frame dropped is not there
   to miss.
4. **The lane's own reply outranks generic miss text**: one rule
   (`answer_bridge._lane_owns_reply`): no miss trigger fires over the lane's own required
   question or a reply the lane declared (`LANE_OWN_REPLY_KEYS`: SO status, low stock
   report). `has_result` alone is not one: "Sales order figures are not enabled" sets it,
   and "Couldn't find SO999001" is the better answer there.
5. **Expiry is by context only, never by time or turn count** (owner ruling Q1). A frame
   ends when the parser reads the next message as leaving it (rule 2), on a reset
   (`topic_reset`, tier and brand stay), or at load when the contact's access fingerprint
   changed since it was written (stock mode, escalation, reveals: `Profile.access_fp`,
   #1471), which drops the whole frame, held questions and the open question included.
   This lane removes every TTL: #1471's 6-turn held-question TTL and its turn/access
   stamps, and `pending.tick`'s 3-turn escalation-offer clock (AC-816).
6. **Held questions** keep #1471's consume rule: a roster survives a new intent but speaks
   only when the reply is a pick (ruling (a)). An escalation offer lives as long as the
   contact's next message (security review B1, crew-ask pending): a message that does not
   answer it closes it after APPLY has read the turn, and a roster loses the escalate
   sentence printed under it, so a later "ok thanks" never hands the chat to a person.
7. **Reply format and escalation are untouched**: one fact per line `*Label:* value`; a
   no-answer for an escalation-allowed contact still ends with the escalation offer.

## Real examples (owner's 3-4 Oct sequences, masked)

1. **Stale product into a low stock report.** Availability contact: "check stock
   SRTWC286-SH-150 and SRTWC286-SH-200" (frame: check_stock, 2 products) then "low stock
   report for sorento water tap" (new intent, `continuation: false`). Today the 2 products
   stay on the focus beside the low stock ask. After: the low stock frame starts clean; the
   report is filtered only by what the parser placed in `low_stock`.
2. **"taiyang only" -> "Could not find inventory for W\*\*\*\*\*\*".** A ranking by sales
   agent W\*\*\*\*\*\* (frame: top selling, `extra.sales_agent`, status top_selling), then
   "taiyang only" read as a stock ask. Today the agent rode into the stock lookup and the
   miss said his name. After: new intent, clean frame, the agent and the ranking's status
   go; only taiyang is looked up.
3. **Same-intent refinement keeps narrowing.** "low stock report for sorento water tap"
   then "taiyang only" (parser: low_stock_report, supplier taiyang). Same intent: the
   report narrows to supplier taiyang (#1470), nothing is dropped.
4. **Domain switch about the same subject.** "incoming SRTWC286" -> roster -> "4" ->
   "check stock" (parser: check_stock, no entities, `continuation: true`) -> "7". "check
   stock" carries the picked product; "7" picks off the surviving incoming roster (#1352,
   #1471 ruling (a)).
5. **Picker -> "1" -> empty -> escalation.** A product pick answered with "1" whose fetch
   comes back empty ends with the escalation offer (PICKER-ESCALATION, #1467), unchanged.

Also kept: "any gunmetal basin has incoming?" -> "cert?" (#833: parser `continuation:
true`, measured live), so the gunmetal basins carry into the certificate ask.

## Edge cases

- A parser intent synonym outside the routing table never counts as a new intent (#1471
  `KNOWN_INTENTS`).
- A new intent that names a value on an axis replaces that axis (as today), and drops the
  rest unless carried.
- A pick off a surviving roster under a new intent is answered in the roster's frame
  (#1352 `own_domain` judgement unchanged).
- An unstamped frame (a row written before this ships) is kept and stamped on its next
  write; it never gets dropped wholesale on deploy.
- A failed access read (fingerprint None) never counts as a change.
- n8n / console dry runs read and write the same keys; no new session key beyond two
  optional focus stamps (no migration).

## Owner rulings (4 Oct 2026)

- **Q1:** frames must NOT expire by time or by turn count. Expiry is decided from context:
  the parser's reading of whether the new turn continues, refines or leaves the frame; an
  access or profile change; an explicit clear/reset. Remove every TTL in this lane,
  including #1471's 6-turn held-state TTL and `pending.tick`-style limits (#1471 merges
  first as is).
- **Q2 (a):** a carry signal carries only the subject, never the old ask's filters.
- **Q3 (a):** one live frame, no stack.
- **Q4 (a):** "clear", "reset", "start over" examples in a new unlabelled prompt version.
- **Q5 (a):** one not-found rule.

## Questions as asked (recommendations first)

- **Q1 Frame TTL.** (a) the frame expires after 60 minutes of contact silence; (b) 24 h;
  (c) a turn count like held questions (6 turns). Recommendation (a): a turn count never
  fires on the live frame (every turn touches it), and an hour of silence is a new visit.
- **Q2 What a carry signal carries.** (a) subject axes only, never the old ask's own
  filters (agent, date window, status, sort, document, channel); (b) everything. Recommend
  (a): it is example 2's fix, and #833's carry is subject only.
- **Q3 Returning to an older intent.** (a) one live frame, no stack: going back starts
  clean unless the parser carries (pick-only rosters still survive per #1471); (b) keep a
  frame per intent and restore it. Recommend (a): no evidence for a stack yet (simplest
  thing that works); trigger to revisit: a hand test where "back to the ranking" must
  restore filters.
- **Q4 "clear".** The live parser does not reliably flag "clear" as `topic_reset` (#1471
  live pass). (a) add "clear", "reset", "start over" to the prompt's TOPIC RESET examples
  and publish it as a new unlabelled parser prompt version (additive data migration, same
  pattern as #1470's `lss_0001`); (b) leave the prompt alone. Recommend (a).
- **Q5 Miss precedence.** (a) one rule: the resolver's `not_found` exit speaks only when
  the lane returned no rows and asked nothing of its own, replacing the `so_status` /
  `low_stock_report` / `required_ask` exemptions; (b) keep adding per-lane exemptions.
  Recommend (a).
