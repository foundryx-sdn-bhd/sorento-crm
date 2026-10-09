# CARD - cap and dedupe product photo sends (PHOTO-SEND-CAP)

Status: superseded by `CARD-photo-send-links-09oct.md` (owner redirect 9 Oct: links instead of a cap; cap Q1-Q5 parked). The `file_hash` dedupe (R2) shipped on the branch and is kept.
Track was M (new rule + customer-visible wording + parser prompt, no migration, no RBAC).
Lane PR carries `crew-lane: PHOTO-SEND-CAP`.

## The journey (owner, 9 Oct 2026)

A dealer asks "show me wc seat cover copatible". The bot answers "toilet seats with product
photos (52)". The dealer narrows with "sorento"; the header reads "(42, showing 1 to 41)", the
text list runs to 64 numbered rows, and about 50 image files go out, some of them the same
picture twice. Every WhatsApp message costs about 0.042 MYR, so one reply cost about 2 MYR and
the dealer could not read it anyway.

## What exists today (measured on origin/main bf07eff8)

| Fact | Where |
| --- | --- |
| A counted set lists up to `SET_LIST_MAX = 50` PRODUCTS; nothing caps FILES | `lanes/business/answer.py:2670` |
| The set fetch asks the attachments tool for its maximum, 1000 rows, so every photo of every listed product comes back | `lanes/business/fetch.py:584-589`, `:1200-1207` |
| The presenter builds ONE item per attachment row (code, name, file name...), so the text list numbers files, not products | `sorento_crm_mcp/presenters.py:1114-1157`; numbered by `fetch.py:3229` (`_item_line`) |
| Header count = products qualifying; "showing 1 to N" = distinct codes in the rendered rows; the list = one row per file. Three bases | `fetch.py:3460-3497`, `_rendered_product_count` `fetch.py:616-631` |
| Over 50 with a brand breakdown: count + breakdown, zero rows, zero files (`set_withheld`); over 50 with no breakdown: list 50 | `fetch.py:3472-3482`, files dropped at `:3540` |
| Dedupe is by URL only: presenter `(url, filename)` and engine `_label_attachments` `url`; n8n `Remove Duplicates` also on `url` | `presenters.py:1914-1927`; `chatbot/engine.py:9128-9145` |
| `attachments.file_hash` (sha256 of the bytes, indexed) exists, but the product attachment response does not expose it, so the chatbot cannot see it | `app/models/resources.py:162`, `:224`; `app/schemas/product.py:445-457` (`AttachmentSimple`, no `file_hash`) |
| A set page carry already exists: `set_key.shown` offset, continued by `set_continue` | `turn/apply.py:3699-3719`; `turn_runtime.page_the_set` `:3780` |
| The "another N" continue is read by a REGEX (`_CONTINUE_COUNT_RE`) and needs a number; a bare "MORE" is not read at all | `turn_runtime.py:1229-1245` |
| "compatible" has no reader: no predicate leg handles it, the parser dropped it | `product_predicate_service.py:495-502` (`REQUIRE_LEGS`) |
| Parser prompt lives in this repo | `app/services/chatbot_parser_prompt.py` |
| n8n `sub-send-attachments-live` sends one message per file, no limit; the workflow JSON is NOT in this repo | crew to change on n8n |

Measured by crew on dev (9 Oct): 48 Sorento SRTSC products -> 102 photo rows -> 62 distinct
`file_hash`. So about 40% of the photo sends in that reply were the same picture again.

## Proposed behaviour (crew reading, pending Q1-Q5)

**R1. One count basis: products.** In a product-set photo answer, the header, the numbered
list and the files all describe the SAME products. One numbered row per product; its photos
are a sub-line under it, never their own numbered rows. Example row:

```
3. SRTSC1012
*Product Name:* Soft close seat cover
*Photos:* 2
```

**R2. One file per image.** Files are deduped by `file_hash` (falling back to the URL when the
hash is null) in the presenter, in `engine._label_attachments`, and so in what n8n receives.
A picture shared by several products is sent once (naming per Q5).

**R3. Hard cap per reply: `PHOTO_SEND_MAX` files (Q1, recommended 10).** Enforced in the
backend send step (`engine._clean_attachments`, the one place every lane's files pass through)
and as a guard node in n8n (crew). When the reply holds fewer products' photos than the set:

```
Sent 10 of 62 photos (products 1 to 6 of 42). Reply MORE for the next.
```

**R4. Too many distinct photos and the set splits by brand: send zero files** (Q2). The reply
is the count, the brand breakdown and the ask to narrow, the same shape `set_withheld` already
uses for a set over 50:

```
Toilet seats with product photos: 52.
Sorento: 42
Cabana: 10
Name a brand or a product code to see the photos.
```

**R5. "compatible" with no target: the PARSER asks** "Compatible with which WC? Send the WC
code." (a parser prompt rule, no keyword code). With a target ("seat cover compatible with
SRTWC8517") it stays a normal product ask.

**R6. MORE is read by the parser, never by a regex.** A prompt rule says a bare "more",
"next", "lagi", "seterusnya", "下一页" after a paged set answer sets `set_continue`; the
existing carry (`set_key.shown`) supplies the offset.

Owner rules honoured: no regex, keyword or fuzzy matching in new code; never stuck (every
over-cap reply ends with what to send next); one fact per line, no " · " joins.

## Real examples (dev data and the owner's transcript)

1. "show me wc seat cover copatible" -> today "toilet seats with product photos (52)" and
   files. After: R5, the parser asks "Compatible with which WC? Send the WC code." Zero files.
2. "toilet seat photos" (52 products, two brands) -> after: R4, the count and the brand lines,
   zero files.
3. "sorento" (42 products, one brand, 62 distinct images) -> after: products 1 to 6 listed,
   10 files, "Sent 10 of 62 photos (products 1 to 6 of 42). Reply MORE for the next." "more"
   -> products 7 to N, the next 10 files.
4. "SRTSC1012 photo" (one product, 2 photos) -> unchanged: 2 files, no MORE line.
5. Two products with the same picture re-uploaded under two URLs -> one file, both rows read
   "*Photos:* 1".

Crew to confirm 2-5 against dev with:

```sql
SELECT count(DISTINCT p.id) AS products, count(*) AS photo_rows,
       count(DISTINCT coalesce(a.file_hash, a.file_path)) AS distinct_images
FROM product_attachments pa
JOIN products p ON p.id = pa.product_id
JOIN attachments a ON a.id = pa.attachment_id
JOIN attachment_types t ON t.id = a.attachment_type_id
WHERE p.product_code LIKE 'SRTSC%' AND t.type_name ILIKE 'product photo%';
```

## Edge cases

- One product with more than the cap of its own photos: send its first 10, then "Sent 10 of
  14 photos of SRTSC1012. Reply MORE for the next."
- `file_hash` null (old uploads): dedupe on URL, exactly as today.
- MORE with no carried set (the carry expired or another ask came between): the parser's
  `set_continue` has nothing to continue; the turn is read as a fresh ask, as "another 10" is
  today.
- MORE after the last page: "That is all 62." (the existing `exhausted` wording).
- A file whose link could not be signed: listed "(file link unavailable right now)", never
  sent and never counted toward the cap (D9 unchanged).
- Non-photo documents (certs, brochures, packing lists): per Q4.

## Questions for the owner

See the crew-ask on the lane PR (Q1-Q5); answers land here.

## Live checks crew runs locally (no OpenAI in the cloud sandbox)

- Parser: "show me wc seat cover copatible" -> asks compatible with which WC.
- Parser: "more" / "lagi" / "next" after a paged photo answer -> `set_continue` true.
- Parser: "seat cover compatible with SRTWC8517" -> a product ask, no question.
