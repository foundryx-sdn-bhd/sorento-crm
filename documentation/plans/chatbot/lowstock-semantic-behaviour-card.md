# Behaviour card: low stock asks read by the parser alone, hard-coded rules removed (LOWSTOCK-SEMANTIC)

Status: RULED (4 Oct 2026). Crew: Q1 (a), Q2 (a), Q3 (a) plus "a second miss runs with no
supplier filter AND says so", Q4 (a) under the STUCK-QTY-LOOP pending-question rule. Crew
root cause added the persisted frame (see "Ruled behaviour" at the end).
Plan: `PLAN-lowstock-semantic-4oct.md`. Predecessor: `lowstock-filter-ask-behaviour-card.md` (#1445).

## Owner report (4 Oct 2026, 03:00, gist)

"I thought I said we need to be semantic and flexible... remove the rules entirely, this is
hard coded, I don't want."

## Step 1: what happens today (`main` 61a54ef2)

The parser (`LOW_STOCK_ADDENDUM`, `app/services/chatbot_parser_prompt.py:118`, published by
migration `517_chatbot_low_stock_vocab`) gives only `intent_hint "low_stock_report"` plus
entities hinted warehouse / product / category / brand. Everything else is read out of the
message text by rules in `app/services/chatbot/lanes/business/low_stock_ask.py`:

| rule | where | what it does |
| --- | --- | --- |
| digit shape | `take_words` `:283` | a `product` entity with no digit in it is re-labelled `category` |
| grouping regexes | `_SPLIT_PATTERNS` `:202-209`, `split_from` `:213` | "by supplier", "per category", "supplier-wise", "by supplier and category" |
| "all categories" regex | `_ALL_CATEGORIES` `:210`, `says_all_categories` `:221` | "all categories" / "all product categories" |
| leftover words | `_ASK_WORDS` `:40`, `leftover_words` `:225`, `supplier_word` `:243` | strips ask words, grouping words and placed words; the longest run of what is left that matches the supplier master is the supplier |
| text presence | `_in_text` `:263`, used in `take_words` `:291` | a category / brand entity counts only when its exact words appear in the message |

Why each one fails the owner's users:

- "per vendor", "split by brand", "group them by supplier", "ikut pembekal", "按供应商"
  match no regex, so the grouping is silently dropped.
- A category name with digits (a code like "SRT-FT" is fine, but a range like "2 in 1 basin
  mixer" or "600mm vanity") stays a product, and the run is scoped to a product code that
  does not exist.
- A product name without digits ("Sorento Lucia basin mixer") becomes a category word.
- A Malay or Chinese word the parser translates ("paip air" -> "water tap", "水龙头") fails
  `_in_text` and is dropped: the bot asks for a category the user already gave.
- Any leftover word ("tolong", "boss", "this month") is offered to the supplier master.

## Proposed behaviour

**Understanding comes only from the parser.** A new parser output key, `low_stock`, carries
everything the low stock ask needs. It is filled only when `intent_hint` is
`low_stock_report`, from the CURRENT message only, in any language:

```json
"low_stock": {
  "group_by": "supplier | category | supplier_category | brand | warehouse | none | null",
  "categories": ["the product type / category words, as said or translated to English"],
  "all_categories": true | false | null,
  "brands": ["brand words"],
  "suppliers": ["supplier names"]
}
```

Products (a specific item, by code or by model name) and locations stay `entities` with hints
`product` and `warehouse`, exactly as today.

**The code only resolves.** Each word the parser placed is looked up in master data, nothing
else:

| field | resolved against | none | one | several | unknown |
| --- | --- | --- | --- | --- | --- |
| categories | `product_categories` (code, name, class vocabulary), narrowed by the brands | ask the category (required) | take | numbered pick | `I don't know 'x' as a category.` + ask again |
| brands | `product_categories.brand_hint` / code prefix | no narrowing | narrow | narrow by all | dropped (never narrows to nothing) |
| suppliers | active `suppliers` (name or code, whole words) | no supplier filter | take | numbered pick | `I don't know 'x' as a supplier.` + ask (Q3) |
| group_by | the workbook's splits | no grouping | take | - | brand / warehouse: ask (Q2) |

No word shape, no phrase list, no leftover-word search, no "is the word in the text" check.

Wording (exact, unchanged from #1445 except the two new lines marked NEW):

- Category missing: `Which product category? Reply with a category (e.g. water tap) or "all".`
- NEW, unsupported grouping: `I can group the low stock report by supplier, by category, or both. Which one? Reply 1, 2, 3, or "none":` then `1. Supplier` / `2. Category` / `3. Supplier x category`.
- NEW, unknown supplier: `I don't know 'xyz' as a supplier.` + `Which supplier? Reply with a supplier name or "all".`
- Settled: the existing first line `Low stock report (<category as said>, supplier <name> | all suppliers, by <grouping> | no grouping)`.

## Examples (expected parse -> reply)

Real dev rows are not reachable from the cloud sandbox; these are the owner's own words from
the #1445 hand tests (turns 3dec9b68, 4a90dd1d) and today's report, plus the paraphrases the
lane is asked to cover. Codes are the committed fixture codes (SRT-FT = Sorento tap, CB-FT =
Cabana tap, SRT-WC = Sorento water closet).

1. "low stock report for sorento water tap" -> categories ["water tap"], brands ["Sorento"]
   -> SRT-FT -> runs, `Low stock report (Sorento water tap, all suppliers, no grouping)`.
2. "low stock water closet, group them by supplier" -> categories ["water closet"],
   group_by supplier -> runs split by supplier.
3. "low stock report water closet taiyang" -> categories ["water closet"], suppliers
   ["taiyang"] -> two suppliers named XIAMEN TAIYANG... -> numbered pick (as #1445).
4. "stok rendah paip air ikut pembekal" -> categories ["water tap"], group_by supplier -> runs.
5. "低库存 水龙头 per vendor" -> categories ["water tap"], group_by supplier -> runs.

## Edge cases

- Parser says a category the master does not know -> said and asked (never a whole-book run).
- Parser puts the same word in `categories` and as a `product` entity -> the `low_stock`
  field wins: that entity is taken off the list (two parser readings reconciled, not a word rule).
- A product entity that is a real product code ("low stock for SRTWT7408") still names its
  own scope; no category asked (unchanged).
- Reply to the category question: the reply turn's own `low_stock` fields are read first
  ("water tap by supplier" settles both). A reply the parser leaves empty is resolved as the
  answer to the question asked ("1", "all", "water closet"), exactly as the shared helper does
  for every ask (Q4).
- A contact without `purchase_orders.supplier`: supplier words and supplier groupings are not
  taken (unchanged from #1445).
- Until the owner promotes the new prompt version, the production prompt emits no `low_stock`
  key: every fresh low stock ask then asks the category once, and the reply ("water tap") runs
  it. No rule fills the gap. Merge and promote together.

## Questions (crew-ask)

- **Q1. Shape.** (a) one new parser key `low_stock` (object above); (b) widen the existing
  `group_by` enum and add a `supplier` entity hint, reading category / brand off entities.
  **Recommend (a)**: one place where the parser decides which word is what, a closed enum, no
  collision with the other asks' `group_by` / entity resolver (category and brand entities are
  read as promotions by the shared resolver today, the reason #1445 had to take them off).
- **Q2. "group by brand" / "by warehouse"** (the workbook has no such split). (a) ask
  supplier / category / both; (b) run ungrouped and say so. **Recommend (a)**: never guess.
- **Q3. A supplier name the master does not know.** (a) say it and ask once (two misses =
  no supplier filter, the helper's existing rule); (b) drop it silently (today). **Recommend
  (a)**: the user asked for a filter, a silent whole-supplier run is a wrong answer.
- **Q4. The reply to the bot's own category question.** (a) the reply's parse first, and the
  bare reply resolved against master data as the answer to that question; (b) parse only.
  **Recommend (a)**: "1" or "all" carry no low stock fields, and resolving a direct answer
  against the master is resolution, not a rule.

## Rulings (4 Oct 2026, crew for the owner)

- **Q1 (a)**: one `low_stock` parser key with a closed `group_by` enum.
- **Q2 (a)**: brand / warehouse grouping asks supplier / category / both / none.
- **Q3 (a)**: an unknown supplier is said and asked once; the second miss runs with no
  supplier filter AND says so: `Low stock report (water tap, all suppliers, no supplier 'bolt'
  found, no grouping)`.
- **Q4 (a)**, under the central pending-question rule (STUCK-QTY-LOOP #1471): the pending
  category question captures only a reply that answers it; a new question always wins; "clear"
  resets.
- **Crew root cause (taiyang / william, 4 Oct)**: the settled filters lived for one turn, so
  "taiyang only" lost the category and asked again. Persist the frame; refinements narrow it.

## Ruled behaviour

As "Proposed behaviour", plus:

- The pending question is stated to the parser as its `Open question:` object (`about:
  low_stock_report`, `owed`, numbered options for a pick). The parser's
  `open_question_answer.mode` (fill / pick / all / cancel) or the same intent decides whether a
  reply answers it; nothing reads the reply's length or punctuation.
- A report's filters persist on `focus.low_stock`. A message the parser reads as the same ask
  with `domain_in_message` false ("taiyang only", "by supplier", "cabana only") keeps every
  filter it does not name; a brand alone narrows the kept categories. A message naming the
  report itself ("low stock report") starts fresh.
- The unsupported-grouping pick reads `I can group the low stock report by supplier, by
  category, or both. Which one? Reply with a number:` then `1. Supplier` / `2. Category` /
  `3. Supplier x category` / `4. No grouping` (only `Category` / `No grouping` for a contact
  without `purchase_orders.supplier`).
