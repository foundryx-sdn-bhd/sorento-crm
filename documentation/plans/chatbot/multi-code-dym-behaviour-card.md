# Behaviour card: did-you-mean per missing code in multi-code replies (MULTI-CODE-DYM)

Status: RULED (4 Oct 2026), BUILT on the lane branch. Track: M (LEAD pattern).
Owner: Q1 (a), Q2 (a), Q3 (a) plus "a reply may pick several, or pick and type other codes,
each handled individually", Q4 (a), Q5 (a). See "Ruled behaviour" at the end; it supersedes
R2 and R6 above where they differ.

## The owner's rule (4 Oct ~01:50, gist)

> We must cater single product code miss and multi product code miss; we must handle
> multiple product codes just like how we handle a single product code; treat each
> product code individually.

So every code in a multi-code ask gets the treatment it would get if asked alone: a found
code gets its stock block, a missing code gets its own `Couldn't find "X" (product). Did
you mean: ...` with its own suggestions. That holds when SOME codes miss and when ALL miss.

## What happens today (trace, `main` 6bc6e239)

| ask | path | reply |
| --- | --- | --- |
| `srt5764 stock` (one code, misses) | resolver exit / runner `_answered_unfiltered` -> `answer_bridge.answer_for` -> `miss_suggest.run_miss_lane` -> `answer.build_suggest_offer` D1 single-token arm (`lanes/business/answer.py:4954-5045`) | `Couldn't find "srt5764" (product). Did you mean:` + `1. SRT57-CR` / `2. SRT5713` / `3. SRT5732` + `Reply with a code to continue, or would you like me to escalate to warehouse team?`; a `product_pick` roster is stored with `escalate_offered: True` (`answer_bridge.py:1210-1224`) |
| `srtwc286 , srtwc6022  srt5764  stock` (two found, one misses) | the fetch HITS, so the bridge never runs; `turn/compose.py::compose` renders the found rows and then the turn-wide `unresolved` tokens (`turn_runtime.envelope_of`, `turn_runtime.py:4054`) | found blocks, then a bare `I could not find srt5764.` (`turn/compose.py:565-579`). No suggestions, no pick, no escalation offer (the offer arm needs every section to miss, `compose.py:589`) |
| `srt5764 srt9999 stock` (all miss) | bridge -> `build_suggest_offer` D1 multi-token arm (`answer.py:4853-4952`) | `Couldn't find some items:` + one `"X" (product) - did you mean:` sub-list per token, numbered continuously, + `Reply a number to pick, or 'yes' to escalate to warehouse.` A token with no suggestion is not in `survivors` and is not named (edge E3) |

The suggestions already exist for the partial case: the resolver computes trigram
`alternatives` for every token that matched nothing (`entity_resolver.py:5143-5200`,
floor `ENTITY_MISS_SUGGEST_FLOOR = 0.30`, cap 5, companies the contact cannot see already
dropped at `:4437-4452`) and they ride on `resolver_payload["resolved"]["resolutions"][i]
["alternatives"]`. Nothing new to compute; the hit arm just never reads them. The single
code reply shows the first 3 (`_cap3`, `answer.py:4867`).

## Proposed behaviour (rules)

R1. **Partial miss.** After the found products' stock blocks, each missing code gets its
own paragraph, in the order typed:

```
Couldn't find "srt5764" (product). Did you mean:
3. SRT57-CR
4. SRT5713
5. SRT5732
```

Same sentence as the single-code reply, same first 3 suggestions, same source (the
resolver's alternatives). Numbers are unique on screen (see Q1).

R2. **A missing code with no suggestion** gets `Couldn't find "xyz123" (product).` on its
own line, in its typed position. It is never dropped.

R3. **One closing line**, once per reply, after the last miss paragraph:
- customer (escalation allowed): `Reply with a code to continue, or would you like me to escalate to warehouse team?`
  (team = the stock domain's escalation team, as today's all-miss offer names it);
- no suggestion anywhere: `Would you like me to escalate to warehouse team?` (see Q4);
- staff: `Reply with a code to continue.` (no bot-initiated offer, AC-S11-1);
- barred contact: the escalation clause is replaced by `Please refer to your salesman.`
  (ESCALATION-CONTROL, `refer.after`);
- dealer (availability-only): the existing `dealer_stock.without_escalation` strip applies
  unchanged (`engine.py:5780`), so the clause becomes `Please refer to your salesman.`.

R4. **The pick.** One `product_pick` roster holds every suggestion of every missing code,
in printed order with their printed numbers, `payload.domain` = the original ask's domain,
`escalate_offered` as R3 says. A number or a code answers THAT product for the original
ask (its stock block alone, Q3). `yes` escalates. Fewer than 2 options in total: no
roster (AC-1691), the customer can still type the code.

R5. **A lane question wins.** If a lane already asked a question this turn
(`_lane_question`), the misses are still named per R1/R2 but no roster is stored, so the
session keeps one open question.

R6. **All miss.** Same per-code paragraphs as R1/R2 (no `Couldn't find some items:` header,
no indented sub-lists), numbers continuous across codes, R3's closing line. See Q5.

R7. Reply format: no ` · ` separators, nothing else on the suggestion lines; the found
blocks are untouched (WA-CONCISE #1455 owns their `*Label:* value` lines and numbering).

## Examples

Codes below are from the owner's 4 Oct transcript and the AVAIL-MODE scenarios
(`tests/chatbot/AVAIL-MODE-SCENARIOS.md` S47: SRT5764 -> SRT57-CR, SRT5713, SRT5732). The
cloud sandbox cannot reach the dev DB, so the suggestion lists for codes other than
srt5764 are illustrative; the hand-test script will read the real ones.

1. `srtwc286 , srtwc6022  srt5764  stock` (customer, after WA-CONCISE numbering)
   ```
   1. *Product Code:* SRTWC286 ...        (found block, unchanged)
   2. *Product Code:* SRTWC6022 ...       (found block, unchanged)

   Couldn't find "srt5764" (product). Did you mean:
   3. SRT57-CR
   4. SRT5713
   5. SRT5732
   Reply with a code to continue, or would you like me to escalate to warehouse team?
   ```
   Reply `4` -> SRT5713's stock block. Reply `yes` -> escalation to warehouse.
   (On main before WA-CONCISE the found blocks carry no numbers, so the suggestions read 1-3.)
2. `srtwc286 srt5764 srtwc99x stock` (two missing, both with suggestions): two miss
   paragraphs, `srt5764` 2-4 and `srtwc99x` 5-7 (found block is 1), one closing line.
3. `srtwc286 srt5764 zzq123 stock` (one missing has suggestions, one has none):
   `Couldn't find "srt5764" (product). Did you mean:` 2-4, then `Couldn't find "zzq123" (product).`, then the closing line.
4. `srtwc286 zzq123 stock` (no suggestions at all): found block, `Couldn't find "zzq123" (product).`, `Would you like me to escalate to warehouse team?` (Q4).
5. `srt5764 srtwc99x stock` (all miss): two miss paragraphs numbered 1-3 and 4-6, closing line (Q5).

## Edge cases

- E1 Several missing codes: one paragraph each, numbering continuous, ONE closing line.
- E2 A suggestion that is also a code the customer typed and got answered (e.g. srt5764 ->
  SRTWC286 while SRTWC286 is in the reply): dropped from that list; it was already answered.
- E3 All miss, one code with no suggestion: today it vanishes; R2/R6 name it.
- E4 The same suggestion for two missing codes: listed under the first only, so one
  number never means two rows.
- E5 A found code with no stock row keeps today's `No stock found for X.` inside its
  section (`compose.py:487-495`); that is a found code, not a miss.
- E6 Non-stock domains (ETA/incoming, price, attachments) with several codes: the rule is
  per-code, so the same paragraphs apply wherever the hit arm prints `I could not find`.

## Questions (recommendations first)

Q1. **Numbering when found blocks are numbered.** After WA-CONCISE, found blocks read
`1.` `2.`. (a) suggestions continue after the last found block (3, 4, 5), so no number
appears twice on screen; (b) suggestions restart at 1. **Recommend (a)**: a `2` that
could mean the second stock block or the second suggestion is the ambiguity this lane
exists to remove. Before WA-CONCISE merges, (a) and (b) print the same thing.

Q2. **Where the escalation offer goes.** (a) once, as the closing line after all miss
paragraphs; (b) at the end of each miss paragraph. **Recommend (a)**: one open question
per turn, and `yes` escalates every missing code together.

Q3. **What a pick answers.** (a) the picked product alone, for the original ask; (b) the
picked product plus the found ones again. **Recommend (a)**: the found ones were already
answered in the previous message.

Q4. **Partial miss, no suggestions anywhere.** Today: bare `I could not find X.`, no
offer. Asked alone, that code gets an escalation offer. (a) name it per R2 and offer the
escalation (closing line `Would you like me to escalate to warehouse team?`, a `yes`
stored as the usual team offer); (b) name it, no offer. **Recommend (a)**: the owner's
rule is "treat each code as if asked alone".

Q5. **All-miss wording.** (a) switch the existing all-miss reply to the same per-code
paragraphs as R1 (drop `Couldn't find some items:` and the indented sub-lists, name
codes with no suggestion); (b) leave all-miss as it is and fix only the partial case.
**Recommend (a)**: one shape for one fact; a customer should not see two different
did-you-mean layouts depending on whether one of their other codes happened to exist.

## Ruled behaviour (owner, 4 Oct 2026)

- **Q1 (a)** Suggestion numbers run on after the reply's own numbered blocks. Measured on
  main: the stock presenter already numbers a found block `1. *Product Code:* ...`, so the
  first suggestion reads `2.` (`turn/compose.py::_did_you_mean_per_code`, `_NUMBERED_BLOCK`).
- **Q2 (a)** One closing line after the last miss paragraph:
  `Reply with a code to continue, or would you like me to escalate to <team> team?`;
  staff `Reply with a code to continue.`; barred contact `Reply with a code to continue. Please refer to your salesman.`.
- **Q3 (a)+** A pick answers that product alone, for the original ask. The parser decides
  what was picked: `2 and 3` answers both (the roster pick already takes several
  positions), and `2 and SRTWC286-SH-150` answers the picked code AND the typed one
  (`engine._with_the_picked_axis` keeps an entity whose word is in this message;
  `turn/apply.py::_focus_rules` adds it beside the pick; `engine._with_settled_picks`
  keeps the picked row in the fetch).
- **Q4 (a)** A partial miss with no suggestion anywhere: `I could not find X.` then
  `Would you like me to escalate to <team> team?`, stored as the domain's team offer
  (none for staff, salesman line for a barred contact, nothing when the domain has no team).
- **Q5 (a)** All miss: the same per-code paragraphs (`Couldn't find "X" (product). Did you
  mean:` + numbers running on), no `Couldn't find some items:` header, no indented
  sub-lists; a missed product code with no suggestion is named `I could not find X.` above
  the closing line (`answer.py::build_suggest_offer` D1 arms). One code with suggestions
  plus others without keeps the single-code sentence for that one.
- **R2 as built**: a code with no suggestion keeps today's `I could not find X.` wording
  (not `Couldn't find "X" (product).`), so the reply has one sentence per kind of miss and
  existing transcripts of that line do not move.
