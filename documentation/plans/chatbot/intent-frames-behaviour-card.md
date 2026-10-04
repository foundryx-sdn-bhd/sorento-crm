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
2. **A new intent** (the parser's `intent_hint` is a known intent other than the frame's):
   a clean frame. Every intent-owned axis goes. A subject axis carries ONLY when the parser
   says the message is about the same subject: `entity_op: "reuse"`, or
   `anaphora.backward_reference: true`, or `continuation: true`, and only on an axis this
   message did not name itself. Nothing else moves over. No word rules: the engine reads
   only those parser fields.
3. **Stale entities never reach the resolver or the miss list**: the resolver and the miss
   composer read the frame after rule 2 ran, so an axis the new frame dropped is not there
   to miss.
4. **The lane's own reply outranks generic miss text**: the resolver's `not_found` exit
   speaks only when the lane has nothing of its own (no rows, no question of its own). One
   rule replaces the three exemptions.
5. **Expiry**: the frame (subject, owned axes, intent, held questions) is dropped at load
   when (a) the contact's access fingerprint changed since it was written (stock mode,
   escalation, reveals: `Profile.access_fp`, #1471), or (b) the contact was silent longer
   than the frame TTL (Q1). A reset (`topic_reset`) drops it as today (tier and brand stay).
6. **Held questions** keep #1471's rule unchanged: a pick-only roster survives a new intent
   but speaks only when the reply is a pick (ruling (a)).
7. **Reply format and escalation are untouched**: one fact per line `*Label:* value`; a
   no-answer for an escalation-allowed contact still ends with the escalation offer.

## Real examples (owner's 3-4 Oct sequences, masked)

1. **Stale product into a low stock report.** Availability contact: "check stock
   SRTWC286-SH-150 and SRTWC286-SH-200" (frame: check_stock, 2 products) then "low stock
   report for sorento water tap" (new intent, `entity_op` not reuse). Today the 2 products
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
   "check stock" (parser: check_stock, no entities, `entity_op: reuse`) -> "7". "check
   stock" carries the picked product (explicit reuse); "7" picks off the surviving incoming
   roster (#1352, #1471 ruling (a)).
5. **Picker -> "1" -> empty -> escalation.** A product pick answered with "1" whose fetch
   comes back empty ends with the escalation offer (PICKER-ESCALATION, #1467), unchanged.

Also kept: "any gunmetal basin has incoming?" -> "cert?" (#833: parser `entity_op:
reuse`), so the gunmetal basins carry into the certificate ask.

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

## Questions (recommendations first)

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
