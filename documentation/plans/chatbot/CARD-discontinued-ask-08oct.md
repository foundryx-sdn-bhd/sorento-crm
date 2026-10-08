# CARD - the chatbot answers "is X discontinued?" directly (DISCONTINUED-ASK)

Status: RULED (8 Oct 2026): Q1 (a), Q2 (a), Q3 (a), Q4 (a) by the owner, Q5 (a) by crew. Building (red-first). See "Rulings". Track: small fix
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

## Measured on real data (crew, prod snapshot 6 Oct 2026, read-only)

| Case | Codes |
| --- | --- |
| The screenshot | `SRTWT9613-BL`: NOT discontinued; 0 in BRW/MWH, 2 in MKT-D; no open PO (last PO received) |
| Discontinued with stock | `CSK13` (355 in BRW/MWH), `SRT7106` (207), `BMC6067` (18, 9 in BRW/MWH) |
| Discontinued, no stock | `AB3306`, `AP8050A`; `SRTBF11821` (0 in BRW/MWH, 3 elsewhere) |
| Live, no stock | `ZP1019`, `ZGL B5`, `BMC6067-NEW` |
| Mixed family | `SRTWC286`: 10 codes, only `SRTWC286-SH-P` discontinued, the other 9 live |
| Size | 2,536 of 14,931 active searchable products are discontinued (17%) |

Which warehouses a dealer "sees" comes from the stock-visibility config (`segment='dealer'`
rows), not a fixed BRW + MWH list. That matters only to a stock answer (Q2), never to the status
line, which reads `is_discontinued` alone.

## Examples

Shown with the Q1 (a) / Q4 (a) recommendation and Q5 (a) as ruled.

1. Live product, no stock (the screenshot). "9613-bl is discon or?"
   ```
   SRTWT9613-BL: Not discontinued.
   ```
   Then, quoting "SRTWT9613-BL x 5: ❌ No stock and no incoming. ...", "mean discontinued model?":
   ```
   SRTWT9613-BL: Not discontinued.
   ```
   (Q2 (b) would add the stock verdict as its own line. Note the staff and dealer stock answers
   differ here: 2 units sit in MKT-D.)
2. Discontinued, no stock where dealers look, with a -NEW alternative. "SRTBF11821 discontinued?"
   ```
   SRTBF11821: Discontinued.
   Alternative: SRTBF11821-NEW
   ```
   (the Alternative line appears once #1533 merges and its lookup is plugged in, Q5)
3. Discontinued, still holding plenty of stock. "CSK13 discon already?"
   ```
   CSK13: Discontinued.
   ```
   (the status is the fact asked; that 355 remain is a stock question)
4. Discontinued, no stock, no alternative. "AP8050A still have or discontinued?"
   ```
   AP8050A: Discontinued.
   ```
5. Live, no stock. "ZP1019 discontinued?"
   ```
   ZP1019: Not discontinued.
   ```
6. Mixed family, staff, "srtwc286 discontinued?" (Q3 (a)): one line per code, 9 of them
   `Not discontinued.` and one `SRTWC286-SH-P: Discontinued.` followed by
   `Alternative: SRTWC286-SH-NEW-P`. Codes known from #1533's table: `-SH-150`, `-SH-200`, `-SH-P`,
   `-SH-NEW-150`, `-SH-NEW-200`, `-SH-NEW-P` (the other four still to be listed from crew data).
7. Status plus another property. "SRTBF11821 price and discontinued?": today's card for the
   price, with a `*Discontinued:* Yes` field line in place of the item's `⚠️ (PRODUCT DISCONTINUED)`
   marker (a live item gets `*Discontinued:* No` where it had nothing), then `Alternative: <code>`
   under a discontinued item that has one.

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
the card stays and each item gains a `*Discontinued:* Yes / No` field line (example 7);
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

**Q5. The Alternative line. RULED (a) by crew, 8 Oct 2026:** #1533 is approved and in build
(owner: auto -NEW links; its availability reply shows the Alternative line when discontinued or not
full stock). This lane ships with the hook: a discontinued status line names each alternative
the item carries, and #1533's lookup is plugged in when it merges. Here only on a discontinued
line, plain code, no stock figure.

## Tests (red first)

- Engine-level, stubbed parser `requested_attributes ["discontinued"]`: live product -> `Not
  discontinued.`; discontinued -> `Discontinued.`; mixed family -> one line each; with `price`
  too -> card + `*Discontinued:* Yes/No` field line; ms / zh labels; dealer with a quoted quantity is NOT rewritten to stock.
- Opt-in live parser (`CHATBOT_LIVE_LLM=1`, console_cases yaml): "9613-bl is discon or?",
  quoted "mean discontinued model?", "dah discontinue ke SRTBF11821", "SRTBF11821 停产了吗",
  "BMC6067 still selling?" -> `master_products` + `requested_attributes` contains `discontinued`.

## Rulings (8 Oct 2026)

Owner, verbatim: "Q1 - (a), Q2 a, Q3, correct a, Q4 a okay". Crew: Q5 (a).

- Q1 (a): status lines only when the status alone is asked; with another property, the card plus a
  `*Discontinued:* Yes / No` field line (it replaces the marker on that item).
- Q2 (a): the status only, no second stock fetch.
- Q3 (a): one line per matched product for staff; dealers keep the exact-code picker first.
- Q4 (a): `CODE: Not discontinued.` / `CODE: Discontinued.`, ms `Tidak dihentikan.` / `Dihentikan.`,
  zh `未停产。` / `已停产。`.
- Q5 (a): the alternative hook. Owner (8 Oct): the alternative capability applies in ALL domains,
  this discontinued answer included: a discontinued code with an alternative gets
  `Alternative: <code>` from #1533's lookup. This lane reads the item's `alternatives` key; #1533
  fills it (for `crm_master_products_list` items too) when it merges.

## Hand test (8 Oct 2026): PASS with one wording change

Owner, verbatim: "ok I am okay wiht #1535, but just need emoji, discon can use the warning sign, the
not discon sign can use tick emoji". So the lines are `SRTWT9613-BL: ✅ Not discontinued.` /
`SRTBF11821: ⚠️ Discontinued.`, the card field `*Discontinued:* ⚠️ Yes` / `*Discontinued:* ✅ No`,
and the ms / zh lines carry the same marks (`✅ Tidak dihentikan.`, `⚠️ 已停产。`).
