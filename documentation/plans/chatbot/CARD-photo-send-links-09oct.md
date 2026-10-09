# CARD - send files as short links, A/B per contact (PHOTO-SEND-CAP round 2)

Status: Plan. Behaviour card posted to the owner (crew-ask on PR #1541); no further code
before the owner answers Q1-Q5. Track: L (migration: a link table and one contact column;
a new PUBLIC route, so security-reviewer joins; customer-visible wording). Supersedes the
round 1 cap card `CARD-photo-send-cap-09oct.md` (cap Q1-Q5 parked by the owner, 9 Oct).

Already on the branch from round 1 and kept: one file per image, deduped by `file_hash`
(commit 08c46748: `app/schemas/product.py` `AttachmentSimple.file_hash`,
`presenters.py` `attach` + dedupe, `engine._label_attachments`).

## The journey (owner redirect, 9 Oct 2026)

Every file the bot sends is its own WhatsApp message (about 0.042 MYR each); a photo set sent
about 50. Instead of capping, the owner wants the bot to send ONE text message where each
file is a block with a short link, and to A/B test it per contact: staff switch a contact
between attachment mode (today) and link mode, then compare how dealers react. Excel files
must open in the browser with no extra software.

## What exists today (measured on origin/main bf07eff8)

| Fact | Where |
| --- | --- |
| The CRM never sends a chatbot file itself: the turn returns `send_message` then `send_attachments`; n8n `sub-send-attachments-live` sends one WhatsApp message per file | `chatbot/engine.py:9596-9644` (`_send_actions`), `:7530-7541`, `:8360-8380`; `documentation/plans/chatbot/n8n-changes.md:248` |
| The url in an entry is the stored, UNSIGNED CDN url (`https://{CLOUDFRONT_DOMAIN or R2_CDN_DOMAIN}/{key}`); n8n signs it on the way out | `presenters.py:344`; `storage_router.py:174-180`; `sorento_crm_mcp/server.py:159-163` |
| Signing on demand exists: `resolve_signed_url`, default 1 h, S3 via CloudFront key pair, R2 presigned | `storage_router.py:299-373` |
| Excel reports (sales analysis, low stock) are generated files: an R2 url with no expiry or an S3 url signed for 7 days, purged at 30 days | `api/v1/sales/analysis.py:390-405`; `scm/low_stock_report_service.py:76-102` |
| A long text is already split into WhatsApp-sized parts: whole lines, 3900 characters, "(k/m)" markers, one `send_message` per part | `presenters.py:2872-2874`, `:2997-3027`; `engine.py:134-180` |
| Public routes live under `/api/v1/public`; 10 token kinds exist, the closest is `view_tokens` (`token_urlsafe(32)`, read-only, attachment download, no expiry) | `api/v1/__init__.py:239`; `api/v1/public/view.py:142-153`; `models/procurement.py:1222-1234` |
| A token page that previews an attachment with NO login already exists: `/view/complaint?token=` renders `AttachmentPreviewModal` with a plain `fetch` | `frontend app/(auth)/view/complaint/page.tsx:94-122`; `app/(auth)/view/lib/attachmentDownload.ts:44-48` |
| `AttachmentPreviewModal` opens xlsx, xls, xlsm and csv in the browser (SheetJS; at most 200 rows x 40 columns), plus PDF, images and video | `components/common/AttachmentPreviewModal.tsx:184-193`, `:540-556` |
| Open-count precedent: `supplier_price_links` (`token`, `expires_at`, `revoked_at`, `open_count`) | `models/cost_price.py:253-275` |
| Rate limiter: the Redis fixed-window limiter, used on public portal OTP | `services/rate_limit.py`; `public/portal.py:197-209` |
| No short-link table, no redirect route, no short-domain setting; `frontend_base_url` is the only public base url | `app/config.py:313` |
| Per-contact chatbot switches are booleans on `respond_contacts`, saved by `PUT /contacts/{id}/chatbot`, shown as `SwitchRow`s on the contact page | `models/access.py:271-301`; `user_management/contacts.py:365-430`; `ContactChatbotSection.tsx:102-134`, `:275-281` |
| The turn reads contact flags once into `Profile` | `turn_runtime.py:170-174`, `:416-497`; `turn/state.py:192-225` |
| Each turn's trace is JSONB on `chatbot.turns.trace` (stage records with `facts`); no thumbs or rating on chatbot turns | `models/chatbot_turn.py:100`; `trace.py:155-183` |

## Proposed behaviour (crew reading, pending Q1-Q5)

**L1. Two modes per contact.** New column `respond_contacts.chatbot_files_as_links`
(boolean, default false = attachment mode, today's behaviour). One new `SwitchRow` on the
contact's Chatbot card, "Send files as links", the same control as "Skip date-range
questions". It reuses an existing control, so no mock.

**L2. Link mode sends one text, no files.** The turn emits no `send_attachments` action. Each
file becomes a block in the reply text, and the existing 3900-character splitter
(`whatsapp_parts`) cuts a long list into parts on whole lines. Every file type the bot sends
is covered: photos, promotion files, certificates, brochures, documents, and Excel reports.

**L3. Own short links, no third party.** New table `chatbot_file_links`: `code` (unique),
the target (an attachment id, or a storage provider + key for a generated report), the
contact, the company, the turn id, `created_at`, `expires_at`, `click_count`,
`first_clicked_at`, `last_clicked_at`. Codes are 10 characters of Crockford base32 (50 bits,
`secrets`), unguessable. The link is `{frontend_base_url}/f/{code}`, so there is no new
domain and no new certificate.

**L4. Opening a link.** `/f/{code}` is a public page with no login. The backend route
`GET /api/v1/public/file-links/{code}` checks that the code exists and has not expired,
counts the click, and:
- image, PDF or any other file: redirects to a freshly signed CDN url (1 h,
  `resolve_signed_url`), so the stored url never leaves us;
- xlsx, xls, xlsm or csv: the page opens the sheet read-only in the browser through
  `AttachmentPreviewModal` with a plain `fetch`, exactly as `/view/complaint` does, with a
  Download button.

The route is rate limited by IP with the existing Redis limiter. An unknown or expired code
gets one page: "This link has expired. Ask us on WhatsApp for a new one." No detail leaks.

**L5. Access stays where it is today.** A link is minted only for a file the contact's own
fetch already returned under their access levels. Anyone holding the link can open it until
it expires, the same as a forwarded WhatsApp file today (Q2).

**L6. A/B measurement.** Every turn that sends files records `file_mode` (`attachments` or
`links`) and the file count in its `replied` stage facts (JSONB, no migration). Clicks are
counted on the link row, which carries the turn id. Crew's comparison query is per mode:
turns with files, files per turn, share of links clicked, and the dealer's next message
within 10 minutes. There is no thumbs-up/down today; a feedback ask is out of scope unless
the owner wants one.

**Kept from round 1 (both modes):** one file per image (`file_hash`); products are the one
count basis (one block per product, its files under it); a bare "compatible" makes the
parser ask "Compatible with which WC? Send the WC code."

## Sample messages (customer-visible, exact)

Link codes below are illustrative; real ones are random.

**1. Photo set, link mode** ("sorento" after "toilet seat photos"; 42 products, 62 distinct
images on dev):

```
Sorento toilet seats with product photos: 42.

1. *Product Code:* SRTSC1012
*File:* SRTSC1012-front.jpg
*Link:* https://crm.example.com/f/7K2QX9MWAP
*File:* SRTSC1012-side.jpg
*Link:* https://crm.example.com/f/H3V8RZ4N1C

2. *Product Code:* SRTSC1013
*File:* SRTSC1012-front.jpg
*Link:* https://crm.example.com/f/7K2QX9MWAP
```

One product block holds about 150 characters, so 42 products with 62 images comes to about
two parts, "(1/2)" and "(2/2)": 2 messages instead of about 62.

**2. Promotion PDF, link mode** (owner's format, one file):

```
1. *Promotion:* <promotion name>
*File:* <flyer>.pdf
*Link:* https://crm.example.com/f/Q4M7T2B9XE
```

**3. Excel report, link mode** (the sales analysis workbook):

```
Here is your sales analysis.

1. *File:* Sales Analysis Sep 2026.xlsx
*Link:* https://crm.example.com/f/N8C3W6P0KD
```

Tapping it opens the sheet in the phone's browser, read-only, with a Download button.

**4. Same asks in attachment mode:** unchanged from today, except that one image under two
urls is sent once.

**5. Single code with 2 photos:** link mode gives one block and 2 links in 1 message;
attachment mode gives the text plus 2 files (unchanged).

## Edge cases

- A file whose link could not be signed: still listed with "(file link unavailable right
  now)", no link minted (D9 unchanged).
- Link opened after expiry, or a report purged at 30 days before expiry: the expired page.
- A spreadsheet over 200 rows or 40 columns: the preview shows the first 200 rows and the
  Download button gets the whole file.
- WhatsApp unfurling: the API sends text with link previews off unless the sender asks for
  them (Respond.io's own default; crew confirms with one test send). If a preview does
  show, `/f/{code}` serves no Open Graph image, so the preview never shows the file itself.
- Same file asked again in a later turn: a new code each turn, so clicks attribute to the
  turn that sent them.
- Contact switched mid-conversation: the next turn uses the new mode; links already sent
  keep working.
- The staff console's dry-run turns record the mode, and their links are minted with the
  same expiry.
- Files sent outside a turn (the low-stock push from the worker, `export_tasks.py:1008-1080`)
  are out of scope for round 2.

## Security notes (public repo; files can be dealer-only promos and prices)

- No file url, bucket, key or company name in the code, the page or the error.
- Codes come from `secrets`, 50 bits, plus per-IP rate limiting, so enumerating codes is
  impractical.
- The route never lists, never searches, never takes a url, only a code (no open redirect).
- A signed url lives 1 h; the code lives per Q1.
- The new public route and migration bring in security-reviewer (PRINCIPLES.md).

## Questions for the owner

On the lane PR as one crew-ask (Q1-Q5); answers land here.

## Live checks crew runs locally

- One real test send in link mode to a test contact: link preview on or off, how the block
  wraps on a phone, and an xlsx opening in the phone's browser.
- Parser: the bare "compatible" ask (from round 1).
