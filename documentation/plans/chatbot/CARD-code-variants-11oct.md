# CARD - product family relation in did-you-mean (CODE-VARIANTS)

Status: card filed to the owner 11 Oct 2026, no code yet. Track: M (resolver ranking rule,
chatbot reply content changes; L if Q1 lands as a new column, which needs a migration).

## The miss being fixed

A user names a product by its model plus a variant suffix that does not exist (wrong finish,
missing or extra segment). The resolver misses, and the did-you-mean list offers OTHER models
that share the suffix, ahead of (or instead of) the user's own model's real variants.

Measured on `sorento_eval_base` (prod copy) with current main (`fd3eb5a90`), read-only:
every unique product-like token the bot answered "Couldn't find" in `chatbot.turns`
(301 tokens) re-run through `entity_resolver.resolve_references` (product only, no
embedding, `alternatives_active_only=True`):

| Bucket | Tokens |
| --- | --- |
| resolves now (fixed since) | 17 |
| still a miss | 284 |
| miss, typed model has a family (rule below) | 112 |
| ... family member in top 3 AND ranked first | 91 |
| ... family member in top 3 but another model ranked above it | 9 |
| ... family left out of top 3 entirely | 6 |
| miss, no family key (no dash, or key has no digit) | 172 (out of scope: typo of the number itself) |

## Real examples (dev data, current main)

| User typed | Offered today (top 3, in order) | User's own model in the catalogue |
| --- | --- | --- |
| `SRTUB206-BACK INLET` | SRTUB906-BACK INLET, SRTUB2020-BACK INLET, SRTUB668-BACK INLET | SRTUB206, SRTUB206-BI, SRTUB206-NZ (none offered) |
| `SRTWC8357-SH-150` | SRTWC8354-SH-150, SRTWC8036-SH-150, SRTWC8152-SH-150 | SRTWC8357-RL, -RL-P, -200-RL, -300-RL (none offered) |
| `cbs208-bl` | CBS201-BL, CBS203-BL, CBS205-BL | CBS208-DB (none offered) |
| `srtwc8066-RL-uf` | SRTWC8036-RL-UF, SRTWC8366-RL-UF, SRTWC8066 | 15 SRTWC8066 codes; two other models ranked first |
| `srt104-BL` | SRT102-BL, SRT104-GY, SRT106-BL | SRT104-GY ranked second |

Why: the trigram tier scores whole-string similarity (`app/services/entity_resolver.py:3029-3037`);
a shared suffix like `-SH-150` outweighs a one-digit model difference. Its only family boost,
`is_variant`, fires when a candidate EXTENDS the whole typed token
(`entity_resolver.py:3033`), never when the typed suffix is wrong. Did-you-mean prints 3
(`miss_suggest.py:59` `_cap3`, roster caps) of the resolver's 5 (`entity_resolver.py:2961`).

## What exists today

| Fact | Where |
| --- | --- |
| No AutoCount field names a model or family. The product push carries category (~100s of products each, e.g. SRT-FT 1919), ItemType (e.g. FT-BASIN TAP 1092), brand, Desc2 | `app/schemas/canonical_masters.py:187-215`; `master_ingest_service.py:547-613` |
| A stored variant graph exists: `products.variant_of_id` + `variant_link_manual`, derived from codes (longest EXISTING boundary-prefix parent) | `app/models/product.py:248-258`; `app/services/variant_link_service.py:1-15` |
| ...but it is effectively empty: 2 links of 15,390 products on the prod copy. AutoCount sync never runs it (only API create/update/delete do), and a parent must exist as a row: only 870 of 4,093 dash bases do (SRTWC8357, SRTWC8840, CBS208, SRT104 have none) | `product_service.py:960,1112,1184`; query on `sorento_eval_base` |
| 194 products carry `variant_link_manual = true` with no parent (reason UNVERIFIED) | query |
| The graph is read only on a DATA-miss (code exists, no stock), not on a code miss | `entity_resolver.py:3337-3470`, `inventory_service.py:1842` |
| Owner precedent: PRODUCT-ALTERNATIVES Q1 (a) accepted a deterministic rule on PRODUCT codes that writes visible, removable stored links, with a sticky manual flag | `documentation/plans/chatbot/CARD-product-alternatives-08oct.md` |
| Owner precedent: the `head_code` tier exact-probes the leading code-shaped word of the typed text | `entity_resolver.py:5040-5046` |
| The code reader (LLM) can already return a `family` reading for a missed token, guarded to typed characters | `app/services/resolver_code_reader.py:1-30`; `entity_resolver.py:5336` |

## Proposed behaviour (pending Q1-Q5)

**Data: a stored family key per product.** `products.family_code`: the product code up to its
first `-`, whitespace removed, uppercased, and only when that part contains a digit
(`SRTWC8357-RL-P` -> `SRTWC8357`; `SRTWC8066` -> `SRTWC8066`; `TPE-9201`, `SRT-BB7001`,
`MWC-SC604` -> none). Written by the AutoCount product sync, API create/update, Excel import
and a one-off backfill (dry run first). Shown and editable on the product page's existing
Variants tab; a hand edit sets `family_code_manual` and the rule never rewrites it until
"Reset to auto" (the `variant_link_manual` / `alternatives_manual` pattern). Same company only.
Measured: 13,591 active products get a key, 1,340 do not; 1,873 keys hold 2+ products
(5,490 products); the largest family is 16.

**Resolution: exact > same family > other suggestions.** Only when a product token misses
every code tier (exact, prefix, substring, head_code, code reader). The typed token's family key
(Q2) is matched EXACTLY against `family_code`; no similarity decides membership. Those members
(active, searchable, chat-visible, in scope) become the token's `alternatives` FIRST, each with
`relation: "same_family"`; trigram neighbours fill the remaining slots with
`relation: "similar_code"`. Nothing resolves on its own: the user still picks (the existing
did-you-mean roster), so a wrong family never answers silently.

Before / after, `SRTWC8357-SH-150`:
```
before: Did you mean: 1. SRTWC8354-SH-150  2. SRTWC8036-SH-150  3. SRTWC8152-SH-150
after:  Did you mean: 1. SRTWC8357-RL  2. SRTWC8357-RL-P  3. SRTWC8357-200-RL
```

## Edge cases

- Family larger than the roster (SRTWC8066 has 15): order inside the family is Q3.
- Codes whose dash is part of the model (`TPE-9201`, `SRT-BB7001`, `MWC-SC604`) get no key, so
  they keep today's trigram suggestions unchanged.
- Spaces instead of dashes in the typed text (`CWC 7603 300mm`): no dash, no family key, today's
  behaviour (a space split would join `CWC INLET`; checked).
- Discontinued / inactive / unsearchable / other-company members never shown, same filters as now.
- Dealer (availability mode): the did-you-mean roster already prints for dealers; this only
  reorders it. The data-miss neighbour probe stays off for dealers (AC-B14).
- `-NEW` replacements share the family key, so they can appear as family members; the
  Alternative line (PRODUCT-ALTERNATIVES) is unaffected.
- A typed token with no digit in its key (`SRT KS-6047`) or no dash (`SRTKS8152`, a typo of the
  number): unchanged, today's trigram list.

## Questions (each with a recommendation)

**Q1. Where the family relation lives.**
(a) new `products.family_code` + `family_code_manual`, set by the rule above on sync / create /
import / backfill, editable on the Variants tab;
(b) repair the existing `variant_of_id` graph (run it on AutoCount sync + backfill) and use
parent + siblings;
(c) owner curates families by hand only, no rule.
**Recommend (a)**: (b) needs the base code to exist as a product, which misses 3 of the 5
examples above (no SRTWC8357, CBS208, SRT104 row) and 3,223 of 4,093 dash bases; (c) leaves
5,490 products to hand-link before anything changes. (a) is the PRODUCT-ALTERNATIVES pattern
the owner accepted 8 Oct: a rule on PRODUCT codes, written as visible, removable data.

**Q2. How a typed token gets its family key.**
(a) the same rule applied to the typed token (text before the first `-`, spaces removed, must
hold a digit), then EXACT equality with `family_code`, like the `head_code` tier reads the
leading code word;
(b) ask the code reader (LLM) for a `family` reading and match that exactly.
**Recommend (a)**: deterministic, free, and identical on both sides; it never corrects or
guesses (`SRTWC8375-SH` finds family SRTWC8375 or nothing). (b) adds a model call per miss for
the same answer.

**Q3. Order inside a family bigger than the roster.**
(a) members with stock (in the asker's visible locations) first, then by code;
(b) closest code to what was typed first (similarity used only to ORDER members the relation
already chose);
(c) by code only.
**Recommend (b)** then (a) as tie-break: for `srtwc8066-RL-uf` (no RL member exists) it puts
the `-UF` members (SRTWC8066-S-UF, SRTWC8066-SH-UF) ahead of SRTWC8066-S-BL / -P-NEW;
membership is still the stored relation, never similarity. UNVERIFIED: exact order until
the test runs the real `similarity()`.

**Q4. How many family members, and do other models still show?**
(a) family fills the roster first, other models fill only empty slots;
(b) family only, never other models when a family exists.
**Recommend (a)**: for `SRTWT51013` the right answer was SRTWT51012 (another model, a typo of
the number), so other models must keep their slots when the family is small.

**Q5. Where it applies.**
(a) every product did-you-mean (stock, incoming, order, attachments, promo; one resolver);
(b) stock and incoming only.
**Recommend (a)**: one resolver, one behaviour everywhere (owner, RESOLVE-SEMANTIC-FALLBACK).
