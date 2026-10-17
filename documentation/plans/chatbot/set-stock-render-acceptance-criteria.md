# UAC: SET-STOCK-RENDER - a set stock answer reads like the product-code answer

Source: `CARD-set-stock-render-16oct.md` v2 (owner direction 16 Oct ~17:40, crew reading).
Built with the card's recommended defaults Q1 (a) first 50, Q2 (b) over-50 + misspelt word,
Q3 (a) numbering as the code answer; a different owner answer is a small change to SSR-4/SSR-5.

"Set stock answer" = the reply to a class/spec ask with the stock leg ("any undermount basin has
stock?"), rendered by `fetch.output_structurer`. "Code answer" = the reply to the same products
asked by product code. "Breakdown line" = any line starting "• " that counts products by a value
("• Sorento wash basins: 9", "• 140 mm wash basins: 42").

## Shape (R1)

- SSR-1. Detailed contact: below the first line, the set stock answer is the code answer for the
  same products, line for line (same blocks, fields, order, numbering, footer).
- SSR-2. Compact contact: the same as SSR-1 (one block per product: Product Code, Total,
  locations).
- SSR-3. Availability (dealer) contact, no quantity given: the header, the numbered product codes,
  then "How many units do you need?"; no stock number anywhere in the reply.
- SSR-4. The first line is the set header ("Here's what you want: under counter wash basins with
  stock (20)"); the code answer's intro "Stock details found for the requested products." is not
  printed in a set answer.

## No breakdown (R2)

- SSR-5. More than 50 qualifying products, stock leg: no breakdown line; the rows of the first 50
  products are listed; the header reads "(<total>, showing 1 to 50)".
- SSR-6. More than 50 qualifying products on another leg (incoming, product ask): no breakdown
  line; the first 50 products' rows are listed under the same header form.
- SSR-7. A set ask with a word the set reader cannot use (misspelt or unknown value, e.g.
  "sorento undrmount basin" with stock): no breakdown line; the reply keeps today's miss shape:
  what was asked ("Here's what you want: Sorento undrmount wash basins"), then
  `Couldn't find: "undrmount".` and the existing escalate offer where one applies. No rows: a
  word nobody understood is never answered with a different set (captain ruling 16 Oct on the
  coder's conflict report: listing the set without the word would answer 276 basins nobody
  asked for and drop the escalate offer; the misspelt word itself is CHAT-GROUNDING-TYPO's fix).
- SSR-8. "Nothing matched" replies (nothing qualifies, or no described product) keep today's text
  (out of scope, Q2 (c) not chosen).

## With stock (R3)

- SSR-9. A set asked with stock prints no location with on hand 0: no detailed block whose
  Quantity On Hand is 0, no compact location line whose value is 0. Holds when the contact's
  policy shows empty locations.
- SSR-10. The code answer for the same product, same contact, still prints its empty locations
  when the policy shows them (policy unchanged outside the set answer).

## Same code in two companies (R4)

- SSR-11. A product code that exists in two companies and qualifies in the set: the set answer
  lists both companies' rows (as the code answer does); the header count counts the code once.

## Brand follow-up (R5)

- SSR-12. After a set stock answer listed in full, the user's brand word ("sorento") answers the
  set narrowed to that brand in the SSR-1 shape; the header names the brand ("Sorento under
  counter wash basins with stock (6)"); no breakdown line.

## Always

- SSR-13. No UUID or internal id in any reply; no " · " separator.
- SSR-14. The header count equals the number of distinct product codes the rows show (or the
  total when SSR-5 applies, with "showing 1 to N" equal to the codes shown). Every continuation
  page ("more", a named count after a partial list) says "(<total>, showing X to Y)", the last
  page included; a bare "(<total>)" only when the whole set is on one page. Pages step by
  product CODE: a code listed for two companies (SSR-11) is one step, so no code repeats or is
  skipped across pages (reviewer blocker, 16 Oct).
