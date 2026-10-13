# CARD: seat cover = product resolution (SEAT-AS-PRODUCT)

Lane SEAT-AS-PRODUCT, PR #1613. Plan: `PLAN-seat-as-product-13oct.md`.

## The rule (owner, 13 Oct)

1. "seat cover for <WC code>" only changes WHICH products the message is about: the WC code is
   replaced by its linked seat-cover codes (`product_compatibility`, #1590), and the turn runs on
   as if the customer had typed those codes. The reverse works the same way ("WC for <seat cover>").
2. The domain is whatever the parser resolves for the message (stock, ETA, files, price, nothing
   asked), answered by the EXISTING handler and formatter under the same audience rules.
3. There is no seat-cover reply lane and no seat-cover formatter: #1601's `crm_find` override and
   its `Code: / Stock:` reply leave the chatbot.

## Why it broke on main (code read, file:line on main d01ca05c)

- Cases 2 and 3: `lanes/business/__init__.py:1775-1779` (`run_fetch`) swaps the domain's own tool
  for `crm_find` whenever the parse carries `find`, whatever the domain, and `fetch.py:2811`
  (`_find_output`) prints the find presenter's own `Code: / Stock:` lines. So "stock" got a new
  format and "eta" got stock.
- The parser rule itself pins the domain: `product_find.FIND_RULE` says "domain_hint is
  master_products" and "never emit it for facts about the named product itself (stock, price,
  ETA ...)". An ETA ask about the seat cover therefore had no right reading.
- Case 1 (same text, two answers): the rule above makes "seat cover for X" with no ask word a
  coin toss between the find and the older readings of "seat cover" (a `seat_cover` product
  specification, a "technical specs" file ask, see prod-20261001 prompt line 1729). One parse
  emitted `find` (Zilin: the find reply), the other read a Technical Specifications file ask. I
  cannot read the dev traces from this sandbox; if crew attaches the two turn traces I will
  confirm which keys each parse carried. The fix does not depend on it: the new rule gives the
  no-ask message one reading (the linked codes, read as a bare code would be).

## Examples (links from the flyer rows in `scripts/oneoff/link_seat_cover_flyer.py`)

The reply column is the shape the existing handler gives for the seat-cover code(s) typed
directly; this sandbox has no dev data, so the live values come from the hand test.

| Message | Resolved as | Domain | Reply = existing reply for |
| --- | --- | --- | --- |
| `stock seat cover for SRTWC8383-RL-MBL` | SRTSCBD823-MBL | stock | `stock SRTSCBD823-MBL` |
| `check stock seat cover for SRTWC8122` | SRTSCBD333-UF, SRTSCBD333A-UF | stock | `stock SRTSCBD333-UF SRTSCBD333A-UF` |
| `eta seat cover for SRTWC8383-RL-MBL` | SRTSCBD823-MBL | incoming | `eta SRTSCBD823-MBL` |
| `seat cover for SRTWC8088` | SRTSCBD330-UF | as a bare code | `SRTSCBD330-UF` |
| `SRTWC8122 马桶盖 有货吗` | SRTSCBD333-UF, SRTSCBD333A-UF | stock | `SRTSCBD333-UF SRTSCBD333A-UF 有货吗` |
| `WC for SRTSCBD330-UF stock` | SRTWC8088 (its linked WC SKUs) | stock | `stock SRTWC8088` |

## Edge cases

- 0 links (no code the message named has one): one line per code, `No seat cover is on record for <code>.` (`No WC is on record for <code>.`
  for a seat cover), the #1601 wording and translations. No fetch runs.
- 2+ links: every linked code, in code order, exactly as when two codes are typed.
- Mixed with another product: see Q2.

## Questions (each with a recommendation)

- Q1 0 links: (a) the one line above as the whole reply; (b) "I could not find a seat cover for
  <code>." Recommendation (a): the wording and its ms/zh already ship; the WC itself exists, so
  "could not find" would be untrue.
- Q2 mixed ("stock seat cover for SRTWC8122 and SRTWT5608"): the parse has no per-entity mark
  for which code the relation is about. (a) Every product named this message is an anchor; one
  with no link gets its Q1 line beside the others' answer; (b) only codes on a link are
  replaced, the rest answer as typed; only when NO named code has a link is the reply the Q1
  line(s). Recommendation (b): a tap named beside a WC is still asked about, and the link data,
  not a word, decides which code is an anchor.
- Q3 the typed code is no exact product (a family `SRTWC8383`, a typo): (a) the code goes to the
  normal resolver unchanged (picker or miss as for a bare code); (b) run the anchor through the
  shared resolver and carry the relation across the picker. Recommendation (a) for this lane;
  trigger for (b): a real turn where a family code is asked for its seat cover.
- Q4 `SRTWC8122 seat cover photo` (#1601's live test kept it a WC photo ask): now it reads as the
  seat cover's photo (the relation + a file ask). Recommendation: yes, the words say the cover's photo.
- Q5 the rule text lives in the live `{{find_filters}}` variable (#1601 design), so the new wording
  reaches every version carrying the token on deploy (dev v88); prod v52 has no token and gets it
  through a NEW version from the publisher. Recommendation: keep the variable; no label moves.

## Answers

Crew 13 Oct: Q2 (b), Q3 (a), Q4 yes, Q5 yes. Owner 13 Oct: Q1 (a), wording
`No seat cover is on record for <code>.` approved as is.

## Built (after crew answers and the lane review)

- No code named and none carried ("which seat cover fits my WC?"): the #1601 ask line,
  `Which product code? For example SRTWC8383-RL-MBL.` A code carried from the previous turn
  ("which seat cover fits it?") is the anchor.
- Q1 lines are said by the engine before any lane, whatever the parsed domain (a bare code's
  null domain included). A code beside them that is no product's exact code (a typo, a family
  prefix) sends the turn on as typed, so the resolver names or places it (Q3 a).
  The WC with no link is then answered as itself too ("stock seat cover for SRTWC8999 and
  SRTWC8383" gives both WCs' stock, no "No seat cover is on record" line). Same trigger as Q3 (b).
- A carried anchor is every product on the focus, with no topic check: after a list answer,
  "which seat cover fits it?" reads all of them. Trigger: a real turn of that shape.

## Known limits (trigger to revisit)

- Q2 (b) replaces EVERY named code that sits on a link. "stock SRTWC8122 and its seat cover"
  answers the covers only, and "stock SRTSCBD333-UF, seat cover for SRTWC8122" turns the typed
  cover into its WC. The parse has no per-entity mark for which code the relation is about.
  Trigger: a real turn of this shape; then the parser names the anchor code in `find.filters`.
- A typed quantity rides on every linked code ("5 seat cover for SRTWC8122" asks 5 of each of
  the two covers). Owner to confirm.

## Across turns (crew v94 check and carry ruling, 13 Oct)

- The parse's `fits` value is the other-side word as typed in THIS message ("seat cover",
  "马桶盖", "WC"), null when the message names none.
- `focus.relation` ("covers" / "wcs") records that the focus products answer a fit ask, and
  which side they are. A message naming a code of its own that links nothing clears it; a
  topic reset drops it.
- Carry (crew ruling, the #1626 pattern): after a fit answer, a bare new code the parser reads
  as a continuation (`domain_in_message` false, only codes) repeats the fit ask in the same
  direction, even when the parse dropped `find`. A bare cover code after "seat cover for <WC>"
  is that cover.
- A code of the fit answer is never re-anchored ("only the 333A one" narrows the answer).
- A relation nothing in THIS message names never reaches a message with its own domain word
  ("SRTWC8383-RL-MBL technical drawing" after a seat-cover exchange is that WC's drawing).
- Limit: a legit reverse ask about a just-answered code ("WC for SRTSCBD333A-UF" right after
  "seat cover for SRTWC8122") answers the cover itself. Trigger: a real turn of that shape.
