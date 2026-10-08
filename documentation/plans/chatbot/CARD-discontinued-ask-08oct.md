# CARD - the chatbot answers "is X discontinued?" directly (DISCONTINUED-ASK)

Status: CARD POSTED (8 Oct 2026), awaiting owner answers on Q1-Q5 before code. Track: small fix
(expected diff well under 300 lines, no migration unless Q5 needs a prompt version, no auth/RBAC).
Lane PR #1535 carries `crew-lane: DISCONTINUED-ASK`. Plan: `PLAN-discontinued-ask-08oct.md`.

## Owner (8 Oct 2026, ~11:30 MYT)

"sometimes the user will ask whether the product is discontinued, and we just straight out dump the
master products list for the item ... when it is not, there is no demarcation, so very confusing".

Screenshot turns (WhatsApp contact):
1. "9613-bl is discon or?" -> "Here are the matching products. 1. *Product Code:* SRTWT9613-BL
   *Description:* ... *List Price:* MYR 710.00 *Dimensions:* Not defined".
2. Quoting "SRTWT9613-BL x 5: ❌ No stock and no incoming. Please refer to your salesman.",
   "mean discontinued model?" -> the same product card again.

## What happens today (origin/main 57fd71c4, code-reasoned; no OpenAI in the cloud sandbox)

The parser ALREADY understands the question. The reply layer never answers it.

| Step | Where |
| --- | --- |
| Parser vocabulary: `requested_attributes` for `master_products` is `price`, `dimension`, `discontinued`; "asking about discontinue of item" routes to `master_products` / `check_product` | `app/services/chatbot_parser_prompt.py:665` (the main literal); prod text `alembic/data/chatbot_semantic_parser.prod-20261001.txt:241-252` |
| Same prompt also maps the word "discontinued" to `is_active=false` (a lifecycle FILTER). For products it filters nothing: `is_active` is not a declared param of `crm_master_products_list`, so it never reaches the backend | `prod-20261001.txt:678-681`; `fetch.py:725-727`; `sorento_crm_mcp/sorento_crm_mcp/catalog.py:93-105` |
| `"discontinued"` is a base-property word (seeded `-> is_discontinued`), so the reply records no miss for it and prints the base card (Code, Name, Description, List Price, Dimensions) | `app/services/chatbot/turn/policy_rows.py:380-394`; `lanes/business/fetch.py:1790-1821`, `:2052-2054` |
| Intro "Here are the matching products." | `sorento_crm_mcp/sorento_crm_mcp/presenters.py:76` |
| The ONLY status signal: `⚠️ *(PRODUCT DISCONTINUED)*` appended when `flags.discontinued` is true. A live product gets nothing, so silence is the only "no" | `lanes/business/fetch.py:3147-3149` (`_item_line`) |
| `flags.discontinued` comes from `products.is_discontinued` (non-null boolean; AutoCount flag or a `****` description) | `app/models/product.py:294`; `app/services/rules/product_rules.py:33-75`; `presenters.py:1082-1106` |
| Turn 2: a quoted reply puts the quoted turn's focus (SRTWT9613-BL) in "Current subject" and the quoted text as `reply to:` | `engine.py:1082-1159`, `:1186-1193`, `:4378-4389` |
| Dealer risk: for an availability-only contact, a `master_products` reading WITH a quantity is rewritten into a stock ask. If the parser copies the quoted "x 5" onto the entity, "mean discontinued model?" becomes a stock check | `engine.py:410-457`, `:4800-4805` |
| No test pins `requested_attributes == ["discontinued"]` | `tests/chatbot/test_attribute_asks_round*.py`, `test_product_spec_projection.py:199` |

So the fix is in the reply layer, keyed on a field the parser already emits. No new parser field.
The live parser is still to be graded on the phrasings below (opt-in test, crew runs it locally); a
prompt example is added ONLY if that grading is red, as a new unlabelled dev version.

## Proposed rules (crew reading, pending Q1-Q5)

R1. When a product answer's `requested_attributes` contains `discontinued`, every product in the
answer gets ONE status line, both ways: `<CODE>: Discontinued.` or `<CODE>: Not discontinued.`
(from `is_discontinued`, the same field the marker reads today). Silence never means "no".

R2. Understanding stays with the parser (any language, "discon or?", "dah discontinue ke?",
"停产了吗", a quoted stock line + "mean discontinued model?"). Code reads only the parsed field;
no word matching on user text.

R3. A dealer's discontinued question is never rewritten into a stock check: the `engine.py:410`
rewrite stands down when the parsed `requested_attributes` carries `discontinued` (a parser
field, not text).

R4. A code that resolves to nothing keeps today's miss reply ("I could not find ..."); we never
say "Not discontinued" for a product we did not find.

R5. Translated like every other label (catalogue entries for ms / zh).

## Examples (real codes; figures from the PRODUCT-ALTERNATIVES card's measured table, prod 6 Oct)

Shown with the Q1 (a) / Q4 (a) recommendation.

1. Live product, no stock (the screenshot). "9613-bl is discon or?"
   ```
   SRTWT9613-BL: Not discontinued.
   ```
   Then, quoting "SRTWT9613-BL x 5: ❌ No stock and no incoming. ...", "mean discontinued model?":
   ```
   SRTWT9613-BL: Not discontinued.
   ```
   (Q2 (b) would add the stock verdict as its own line.)
2. Discontinued, nothing in dealer-visible warehouses. "SRTBF11821 discontinued?"
   ```
   SRTBF11821: Discontinued.
   Alternative: SRTBF11821-NEW
   ```
   (the Alternative line only once #1533 has stored the link, Q5)
3. Discontinued but still holding stock (`BMC6067`, 18 on hand). "BMC6067 still selling?"
   ```
   BMC6067: Discontinued.
   Alternative: BMC6067-NEW
   ```
4. Ambiguous typed code, mixed status (`SRTWC286`: `-SH-200` live, `-SH-P` discontinued, `-SH-NEW-200`
   and `-SH-NEW-P` live). Staff, "srtwc286 discontinued?"
   ```
   SRTWC286-SH-200: Not discontinued.
   SRTWC286-SH-NEW-200: Not discontinued.
   SRTWC286-SH-NEW-P: Not discontinued.
   SRTWC286-SH-P: Discontinued.
   Alternative: SRTWC286-SH-NEW-P
   ```
   (the real family may hold more codes, e.g. `-SH-150`, `-SH-NEW-150`; each gets its line)
5. Discontinued plus another property. "SRTBF11821 price and discontinued?" -> today's card for
   the price, with `*Discontinued:* Yes` as one more field line in the card (Q1).

## Edge cases

- More matches than the list cap: same paging as any product list today, each row with its line.
- Dealer + a typed prefix: dealer turns resolve exact codes only, so the existing "matches N
  products. Which one?" picker comes first; the status line answers after the pick.
- A product that is inactive / not chat-searchable is never returned, so it is a miss (R4).
- Two codes in one message: one line each.
- A message asking "discontinued" with NO product and no subject: the existing "which product?"
  question, unchanged.

## Questions (owner decides; each has crew's recommendation)

**Q1. Shape of the reply when ONLY the discontinued status is asked.**
(a) the status lines only (examples above), the card dropped; when another property is asked too,
the card stays and gains a `*Discontinued:* Yes / No` field line;
(b) the status line first, then the full card as today;
(c) always the card, with a `*Discontinued:* Yes / No` field line.
**Recommend (a)**: it is the direct answer the owner asked for. (b) and (c) bury it in the dump the
owner called confusing.

**Q2. The "no stock" follow-up ("mean discontinued model?" quoting the stock line).**
(a) answer the status only (`SRTWT9613-BL: Not discontinued.`); "not discontinued" is itself the
separation from "no stock now", and the stock line the user quoted still stands;
(b) also re-check stock and add the verdict as its own line, e.g.
`Stock: ❌ No stock and no incoming. Please refer to your salesman.` (dealers get only their
availability verdict, never a figure).
**Recommend (a)**: one fetch, one fact, no risk of a stock line that disagrees with the quoted one
minutes later. (b) is a second fetch per question for a fact the user already holds.

**Q3. Ambiguous code that matches several products of mixed status.**
(a) one status line per matched product (example 4), staff only; dealers keep today's picker
first; (b) ask "which one?" for everyone before answering.
**Recommend (a)**: every line is a true fact, and the mixed family is exactly where a dealer or
salesman needs to see which code is the live one.

**Q4. Wording.**
(a) `SRTWT9613-BL: Not discontinued.` / `SRTBF11821: Discontinued.`
(ms `Tidak dihentikan.` / `Dihentikan.`, zh `未停产。` / `已停产。`);
(b) `SRTWT9613-BL: Still active, not discontinued.`;
(c) `*Discontinued:* No` under a `*Product Code:*` line.
**Recommend (a)**: one fact per line, `Label: value` with the code as the label, the shortest
unambiguous form, same in staff and dealer replies.

**Q5. The Alternative line (PRODUCT-ALTERNATIVES #1533 is still at its card).**
(a) ship this lane now WITHOUT the line, behind one small seam that #1533's `alternatives_for`
plugs into (the line appears when #1533 merges; no second lookup); (b) wait for #1533 to merge
first. On a discontinued line only, plain code, no stock figure (stock stays a stock question).
**Recommend (a)**: the yes/no answer is useful on its own today, and the seam keeps one lookup.

## Tests (red first)

- Engine-level, stubbed parser `requested_attributes ["discontinued"]`: live product -> `Not
  discontinued.`; discontinued -> `Discontinued.`; mixed family -> one line each; with `price`
  too -> card + field line; ms / zh labels; dealer with a quoted quantity is NOT rewritten to stock.
- Opt-in live parser (`CHATBOT_LIVE_LLM=1`, console_cases yaml): "9613-bl is discon or?",
  quoted "mean discontinued model?", "dah discontinue ke SRTBF11821", "SRTBF11821 停产了吗",
  "BMC6067 still selling?" -> `master_products` + `requested_attributes` contains `discontinued`.
