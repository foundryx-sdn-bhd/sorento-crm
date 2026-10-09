# Chat topic taxonomy v1 (APPROVED by the owner, 9 Oct 2026, frozen as version 1)

Lane CA-2 (`PLAN-chat-analytics-ca2-taxonomy-09oct.md`). Approved as drawn (rules 1 and 2 under
"What the data says" included) and seeded, subjects and leaves, at `version = 1` by migration
`chat_topics_0001_taxonomy`. A change is a new version, approved by the owner until he names a
delegate.

Source: crew extract from the local 6 Oct prod snapshot, live turns 24 Sep to 5 Oct 2026 (MYT),
3,809 turns. Counts are aggregates. Examples are real asks, masked: `<product>` for a product
code, `<brand>` for a brand, `<dealer>` for a customer company. No other text was changed.

## Rules the tree follows (owner HYBRID ruling + crew card answers, 9 Oct)

- **Subject = the domain the bot acted on** (CA-1 fact `domain`: the plan's first domain, else
  the parser's `domain_hint`). Free, no tokens. A follow-up with no domain of its own takes the
  domain the engine carried from the previous turn, which is the explicit turn link (card
  answer 3). That is where most of the 144 turns whose fact domain differs from the parser's
  hint come from (103 are "no parser domain -> a carried domain").
- **Leaf = picked by the nightly labeller**, only from the leaves under that subject.
- A leaf under 0.5% of asks (about 19 of the 3,809) folds into `<subject>.other`. Subjects
  this small get one leaf.

## The tree (31 leaves under 16 subjects)

Share of asks per subject is measured. The leaf split inside a subject is ESTIMATED from the 60
sampled asks and is confirmed or corrected by the audit sample after the first labelling run.

| Subject (asks, share) | Leaf | Definition | Example 1 (masked) | Example 2 (masked) |
|---|---|---|---|---|
| inventory (1,758, 46%) | `inventory.availability` | Is a product in stock | "`<product>` has stock?" | "`<product>` ada stock?" |
| | `inventory.quantity_check` | Is there enough for a stated quantity | "`<product>`有货吗？5个" | (more in the audit sample) |
| | `inventory.other` | Any other stock ask (low-stock lists: 11 asks) | | |
| incoming (737, 19%) | `incoming.product_eta` | When a product will arrive | "`<product>` eta" | "ada eta ke tak?" |
| | `incoming.other` | | | |
| order (380, 10%) | `order.delivery_status` | Status or delivery of a customer's order | "`<dealer>` today delivery" | (more in the audit sample) |
| | `order.sales_figures` | Sales totals or rankings asked through orders | "what is my sales today" | "Top 10 hot sale dealer `<brand>` water closet by quantity" |
| | `order.customer_ordered_product` | Did a customer order a given product | "`<dealer>` 有下单`<product>` 吗" | (more in the audit sample) |
| | `order.other` | | | |
| general (283, 7%) | `general.noise` | Small talk, thanks, acknowledgements (casual) | "tq" | "yes" |
| | `general.ask_for_person` | Asks to be put through to staff | "please escalate to marketing" | (more in the audit sample) |
| | `general.document` | Invoice, statement or other document with no domain | "`<dealer>` latest invoice" | (more in the audit sample) |
| | `general.other` | Unparsed messages (22 technical turns had no parse at all) | "check stock / `<product>`" | "`<product>`" |
| product_attachment (224, 6%) | `product_attachment.photo_drawing` | Photo, image or drawing of a product | "Photo for `<product>`" | "send me image and drawing for `<product>`" |
| | `product_attachment.certificate` | A product's certificate | "basin SPAN certificate" | (more in the audit sample) |
| | `product_attachment.other` | | | |
| promotion (170, 4%) | `promotion.product_promotion` | The promotion on a product | "`<product>` promotion" | "`<product>` Promotion dealer use" |
| | `promotion.other` | | | |
| purchase_cost (82, 2%) | `purchase_cost.last_cost` | The last purchase cost of a product | "Purchase cost `<product>`" | "`<product>` purchase cost" |
| master_products (80, 2%) | `master_products.specification` | A size, thickness or other specification | "sink thickness" | "`<brand>` `<product>`, may I know what is the thickness of the sink" |
| | `master_products.installation` | How to install a product | "Installation method `<product>`" | (more in the audit sample) |
| | `master_products.discontinued` | Is a product discontinued | "discon item `<product>`?" | (more in the audit sample) |
| | `master_products.compatibility` | Which product fits with which | "`<product>` 适合哪一款马桶" | (more in the audit sample) |
| | `master_products.other` | | | |
| portal_link (25, 0.7%) | `portal_link.request` | A portal link or a portal task (sponsorship, price tag, complaint) | "Hi I need my portal link" | "i want to make pricetag" |
| resource_attachment (21, 0.6%) | `resource_attachment.document` | A catalogue, guide or report | "`<product>` Installation guide" | "container status report" |
| ideate (19, 0.5%) | `ideate.suggestion` | An idea for the company or the bot | "I think need to have sales target function in the system" | "I suggest to have a project management system in the AI" |
| forms (15, 0.4%) | `forms.request` | A form to fill in | "`<brand>` Application form" | "`<brand>` sponsor form" |
| purchase_order (15, 0.4%) | `purchase_order.outstanding` | Outstanding purchase orders | "any PO" | "Purchase order `<product>`" |
| spo_allocation (0) | `spo_allocation.last_in` | The shipment a product last came in on | (none in the window) | |
| goods_receive (0, not supported) | `goods_receive.request` | Goods received | (none in the window) | |
| sales (0) | (no leaf in v1) | Sales asks were routed to `order` in this window; a `sales` ask is labelled at the subject itself | | |

Where the master_products leaves are under 0.5% each (80 asks in the subject), the first
labelling run will show it, and they fold into `master_products.other` in v2 if the owner wants.

## What the data says about the rules

1. **Casual is not always noise.** Of 214 casual turns, 65 are noise by outcome, but 43 were
   handed over and some are menu picks ("2", a brand name answering a picker). Proposal: a
   casual turn with no domain is `general.noise`; a casual turn the engine carried a domain for
   (a pick, a "yes" to an offer) takes that subject and the labeller picks its leaf.
2. **Sales figures are routed to `order`** (asks 1, 3, 4 of the sample). They stay under `order`
   as `order.sales_figures` (leaves nest under the parser domain), so "most asked: sales
   figures" is visible without a parser change.
3. **Language**: the bot's own reply language is set on only 16% of these turns, which confirms
   the labeller decides language for every row (card answer 4).

## How a leaf falls back

A leaf the model names that is not under the ask's subject becomes `<subject>.other`. A subject
with no `.other` leaf (one-leaf subjects, and `sales`) falls back to the subject itself, so every
`topic_code` is a row of `chatbot.chat_topics`.
