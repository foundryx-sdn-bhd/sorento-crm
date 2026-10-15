# CARD: CHAT-FIT-LINE, the linked-fit line + mirror / shelf fits (15 Oct)

Lane CHAT-FIT-LINE. Plan: `PLAN-chat-fit-line-15oct.md`. Paths are under `sorento_crm_backend/`.
Dev data below is from `sorento_cagent_stack` (the only local DB with `product_compatibility.kind`;
seat_cover 174 rows, mirror 21, shelf 20).

## Rule (owner, final, plus crew scope addition 15 Oct)

1. A turn whose product was reached through a `product_compatibility` link starts its reply with
   ONE line naming the link, worded from the link's kind (`KINDS`,
   `app/services/product_compatibility_service.py:60-64`): seat cover / WC, mirror / cabinet,
   shelf / cabinet.
2. Then the usual answer, unchanged, after a blank line (the `_carried_line` pattern,
   `app/services/chatbot/engine.py:7697-7701`, the one seam every answer, entity-only, stock,
   price and photos, passes through).
3. Mirror and shelf now resolve like seat covers do today (#1613 `as_linked_products`,
   `app/services/chatbot/lanes/business/find.py:120-304`), both directions, driven by `KINDS`;
   today the chatbot reads seat cover links only (`find.py:94,103,224-225`).
4. The line comes from the resolution path (the linked entity carries `linked_from`, stamped at
   `find.py:271-276`), never from words in the message. The parser names which kind.
5. Truthful: links on record only. None on record keeps the #1613 one-line reply, per kind
   (`No seat cover is on record for <code>.`, owner-approved 13 Oct; `No mirror is on record for
   <code>.` etc.).
6. Same line for staff and dealers (codes only, no numbers). ms / zh through the label catalog
   (`label_catalog.py`, templated lines match at :811), like every other line.

## Examples (real dev links; reply = line, blank line, the usual answer as it is today)

| # | Message | Link on record | Reply starts with |
|---|---------|----------------|-------------------|
| 1 | what is the seat cover for SRTWC286-SH-NEW-150 | WC -> SRTSCBD407 | `Seat cover for SRTWC286-SH-NEW-150 is SRTSCBD407.` then `I have SRTSCBD407. What would you like me to know?` (unchanged) |
| 2 | please recommend seat cover for SRTWC287 | WC -> SRTSCBD315 | `Seat cover for SRTWC287 is SRTSCBD315.` then the same entity-only line |
| 3 | seat cover recommendation for SRTWC8383-RL-MBL | WC -> SRTSCBD823-MBL | `Seat cover for SRTWC8383-RL-MBL is SRTSCBD823-MBL.` then today's stock summary |
| 4 | SRTWC8122 seat cover got stock? | WC -> SRTSCBD333-UF, SRTSCBD333A-UF | `Seat cover for SRTWC8122: SRTSCBD333-UF, SRTSCBD333A-UF` (Q1) then the stock reply for both |
| 5 | what is the mirror for SRTBF11413 (prod today: `Product: SRTBF11413. Which kind of file do you need?`) | cabinet -> SRTMR11413 (mirror, flyer) | `Mirror for SRTBF11413 is SRTMR11413.` then `I have SRTMR11413. What would you like me to know?` |
| 6 | SRTBF11413 shelf ada stok? | cabinet -> SRTSV11413 (shelf) | ms: `Rak untuk SRTBF11413 ialah SRTSV11413.` then the ms stock reply |
| 7 | SRTWC287 马桶盖多少钱 | WC -> SRTSCBD315 | zh: `SRTWC287 的马桶盖是 SRTSCBD315。` then the zh price reply |
| 8 | which cabinet fits SRTSV11404-BL | shelf -> SRTBF11404, SRTBF11405 | `SRTSV11404-BL fits these cabinets: SRTBF11404, SRTBF11405` (Q2) then the usual reply |
| 9 | seat cover for SRTWC13017 (real WC, no link, has stock) | none | `No seat cover is on record for SRTWC13017.` only (today's #1613 reply, unchanged) |

ms / zh nouns (proposed, crew to correct): mirror `cermin` / `镜子`, shelf `rak` / `置物架`,
cabinet `kabinet` / `浴室柜`; seat cover and WC keep the catalogued #1613 words
(`product_find.py:216-225`).

## Edge cases (follow the resolution path; no question needed)

- A bare code that carries a fit ask on (the #1613 carry, card `CARD-seat-as-product-13oct.md`
  "Across turns") was reached through a link: it gets the line. "only the 333A one" narrows an
  answer (`find` null): no link resolution, no line.
- Two codes linked in one message: one line per anchor, in the order the answer lists them.
- "mirror and shelf for SRTBF11413": two filters, two lines (mirror, then shelf), then the answer
  for both products.
- A file ask ("SRTBF11413 mirror photo"): the line, then the photo reply as today.
- A cabinet with only a mirror link asked for its shelf: `No shelf is on record for <code>.`
- Existing parity tests (`tests/chatbot/test_seat_as_product.py:122-189,240-262` assert
  `a_reply == c_reply`) become `a_reply == line + blank + c_reply` by design.
- UNVERIFIED: a reverse ask on a cover with many WCs (SRTSCBD331-UF is on 28 WC links) lists every
  code the turn resolves; checked in the build, cap rule unchanged.

## Questions (max 3)

**Q1. Several linked products, the line's shape.**
(a) `Seat cover for SRTWC8122: SRTSCBD333-UF, SRTSCBD333A-UF` (the owner's `Label: value`, reuses
the catalogued head `Seat cover for {code}:` with its ms / zh, `product_find.py:216`);
(b) `Seat covers for SRTWC8122 are SRTSCBD333-UF, SRTSCBD333A-UF.`
Recommendation (a): the brief's `Label: value` rule, and no new plural forms in ms / zh.

**Q2. Reverse direction (an accessory named, its host answered).**
(a) `SRTSCBD823-MBL fits SRTWC8383-RL-MBL.`; several: `SRTSV11404-BL fits these cabinets:
SRTBF11404, SRTBF11405` (reuses `{code} fits these WCs:`, `product_find.py:217`, per kind);
(b) the forward words literally: `WC for SRTSCBD823-MBL is SRTWC8383-RL-MBL.`
Recommendation (a): it says which way the fit goes; (b) reads as if the WC were the accessory.

**Q3. How the parser names the kind (no prompt republish either way; both change the live
`{{find_filters}}` text, `chatbot_prompt_vars.py:290-293`, so prod v63 reads it on deploy).**
(a) Keep the one `fits` filter and add a `kind` field to the find filter in the strict parse
schema (`head/parser.py:543-569`, built in code at :84), enum generated from `KINDS`; the
vocabulary's first sentence lists the `KINDS` pairs; the rest of today's seat cover text
(`product_find.py:192-213`), which prod answers well, stays as it is.
(b) One filter key per `KINDS` entry (`seat_cover` / `mirror` / `shelf`), vocabulary generated
from one template; no schema change, but the working seat cover text is rewritten.
(Rejected: code maps the value word to a kind; that is a keyword rule.)
Recommendation (a): smallest change to what works, registry-driven, the parser decides the kind.
The live check reruns seat cover asks beside the new mirror / shelf ones to catch a regression.

## Split?

No split recommended. The line reads the kind stamped on the linked entity, which the
generalised find creates; split, the seat cover line would be written twice. One PR, slices as
commits: (1) generalise the find to `KINDS`, (2) the fit line. Size M+.
