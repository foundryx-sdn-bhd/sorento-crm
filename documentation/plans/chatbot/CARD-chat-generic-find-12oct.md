# CARD - generic product find, first filter: WC <-> seat cover fit (CHAT-FIND)

Status: answered by crew 12 Oct 2026, Q1-Q5 all (a) (owner asleep, crew holds authority; owner reviews in the morning). Plan: `PLAN-chat-generic-find-12oct.md`.

## The ask being answered

Owner request, 13 Oct: "what seat cover fits SRTWC8383-RL-MBL", "which WC fits SRTSCBD823-MBL", in
English, Malay, Chinese and mixed. Today the bot has no way to read the fit: the links exist only
since PR #1590 (`product_compatibility`, one seat cover SKU to one WC SKU, flyer import plus manual
links on the product page). There is no MCP tool or parser key for it.

Architecture (ARCH-scalable-chat-11oct, option D, "reads generic"): this lane does not add a
"seat cover" tool or domain. It adds ONE generic read tool, `crm_find` on products, whose filters
come from a registry. The fit is the registry's first filter. A later question type is a new
registry row plus eval cases, never prompt surgery, a new tool or a new domain.

## Rules

1. The parser decides what the message means: that it asks which products fit a named product,
   and which code(s) it names. Code never reads the user's words (no regex, no keyword list).
2. The named code is resolved by the existing product resolver. An unknown or partial code goes
   through today's did-you-mean / picker (CODE-VARIANTS family first); nothing is guessed.
3. The direction comes from the DATA, not from the words: if the named SKU is the WC end of a
   link, the reply lists its seat covers; if it is the seat cover end, the WCs it fits. The head
   line names which, so a reversed question ("which seat cover fits SRTSCBD823-MBL") is answered
   truthfully ("SRTSCBD823-MBL is a seat cover. It fits: ...").
4. Only links on record. No link = say so plainly; never a family, colour or model guess (owner
   ruling 12 Oct on #1590: "match only what the flyer prints, fabricate nothing").
5. Reply: a head line, then each linked code with its stock, one fact per line `Label: value`,
   never joined with " · ". Stock follows the asker's stock visibility (Q2).
6. No code at all ("which seat cover fits my WC?"): ask for the WC code. Never pick one.
7. MCP is the only executor: the chatbot calls `crm_find`; the route reads the links and stock under
   the contact's company scope and stock policy.

## Real examples (codes from the flyer data in PR #1590; stock figures illustrative)

| Message | Reply |
| --- | --- |
| what seat cover fits SRTWC8383-RL-MBL | `Seat cover for SRTWC8383-RL-MBL:` / `Code: SRTSCBD823-MBL` / `Stock: 12` |
| which WC fits SRTSCBD823-MBL | `SRTSCBD823-MBL fits these WCs:` / `Code: SRTWC8383-RL-MBL` / `Stock: 4` (one block per linked WC SKU) |
| SRTWC8122 pakai seat cover apa? (Malay) | Two blocks, SRTSCBD333-UF and SRTSCBD333A-UF, each with its stock line (flyer row 6 prints both) |
| SRTWC191-G2 配什么马桶盖? (Chinese) | `No seat cover is on record for SRTWC191-G2.` (intelligent seat, the flyer prints no cover code) |
| SRTSCBD824-BL boleh pakai WC mana? (mixed) | `SRTSCBD824-BL fits these WCs:` then each WC SKU linked under printed SRTWC8383-BL and SRTWC8152-BL |

Reply wording follows the conversation's language the same way every other templated reply does
today (chat-language plan); labels shown here in English.

## Edge cases

- A named WC SKU in a finish the flyer does not print (an MK SRTWC8383): no link, so rule 4's line.
- A `-SC` SKU (SRTWC8383-SC) is a seat cover: if linked it is listed as one, never as a WC.
- Bare model with no SKU row (`SRTWC8383`): resolver miss, today's family did-you-mean lists the
  SKUs; the user picks. Never a union of every SKU's covers.
- Two codes in one message ("seat cover for SRTWC8088 and SRTWC8122"): one block per code, in the
  order named.
- A linked product that is discontinued: Q4.
- A linked product in another company the contact cannot see: never shown (company scope).
- A dealer whose contact resolves to nobody: the route fails closed (no rows), as stock does today.
- Follow-up "stock for the second one" / "how about BL?": out of this card; the next question is a
  fresh ask (the held-call patch is ARCH step 2 work).

## Questions (crew answers; recommendation first)

**Q1. Who decides the direction (covers for a WC vs WCs for a cover)?**
(a) the data: one filter `fits` (the named product's links, whichever end it is), head line names
the direction; (b) the parser: two filters, `seat_covers_for` and `wcs_for`.
**Recommend (a)**: the link row already says which end is the cover, so a reversed or loosely
worded question cannot pick the wrong table (ARCH "the kind comes from the data"); the parser only
has to know it is a fit question and which code. (b) adds a meaning the data already holds and a
way to get it wrong.

**Q2. What "with stock" prints, per audience.**
(a) staff / compact and detailed policies: `Stock: <on hand in the locations the contact may see>`;
dealer (availability policy): the stock tool's own availability verdict for 1 unit
(`Stock: Got stock` / `Stock: No stock`), never a number; (b) dealers get no stock line and the
usual "How many units do you need?"; (c) every audience gets the full stock tool reply under each
code.
**Recommend (a)**: it runs the same visibility policy and the same availability judgement the stock
tool runs (`stock_visibility.resolve_policy`, `inventory_service` availability branch), so it shows
a dealer nothing a "1 unit?" ask would not; (b) makes the dealer ask twice; (c) is long and repeats
headers per code.

**Q3. No link on record.**
(a) `No seat cover is on record for <code>.` (round 3, crew: `No WC is on record for <code>.` when the data calls the product a seat cover), nothing more;
(b) the same line plus the escalation offer used when a product ask misses.
**Recommend (a)**: the question was answered truthfully; the owner ruled no fabrication, and an
escalation offer on every unlinked SKU (the 7 intelligent-seat WCs, unprinted finishes) would page
staff for a known gap. Staff fill the gap on the product page.

**Q4. A linked product that is discontinued.**
(a) listed, with the existing discontinued marker the product and stock replies print;
(b) left out.
**Recommend (a)**: the link is true and staff linked it on purpose; the marker is how every other
product reply says it today.

**Q5. PR #1582's `spec_constraints` / `has_active_promotion` as filters 2 and 3.**
(a) once #1582 merges, register both in the same registry, delegating to #1582's own clauses
(`product_spec_filter`, `active_promotion_clause`), with no behaviour change on the existing tools
until `crm_find` matches them on the eval; (b) leave them on the old tools and only build new
filters on `crm_find`.
**Recommend (a)**: the lane brief asks for it, and delegating keeps one implementation of each
filter; the old tools leave the allow-list only after the eval says so (ARCH exit ramps).
