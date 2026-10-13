# CHAT-PRODUCT-LABELS cloud browser pass (4 Oct 2026)

Code under test: `62962e47` (branch `crew/chat-product-labels`, PR #1479). This folder is docs
and harness only.

## Stack (all inside the cloud sandbox)

- Postgres 16 `sorento_browse`, built by `scripts.bootstrap_env`, then the synthetic seed in
  `harness/` (no customer data): an admin login (throwaway password from the
  `CLOUD_ADMIN_PASSWORD` env var, never written to a file), contact 900000008 "Cloud Buyer" in
  the default company with every suggested access agent, categories, and ONE product:
  `SRTWC286`, "Wall Hung Basin", description "Basin that has no hole", no dimensions, one spec
  (Width 500 mm in `product_specifications` + a `width` row in `product_spec_registry`), and a
  Certificate attachment (`SRTWC286-cert.pdf`, certificate `WM-286-2026`, valid until
  2027-12-31) linked through `product_attachments` and `certificate_products`.
- Backend :8000 (`harness/run_backend_live.py`, the REAL parser), MCP :8765, frontend
  `npm run dev` :3000, agent-browser 0.27.0 on the sandbox Chromium
  (`/opt/pw-browsers/chromium-1194`, passed through `AGENT_BROWSER_EXECUTABLE_PATH`), session
  `cpl`. No RQ worker was started: nothing in these cases is queued.
- Modules installed through `POST /api/v1/system/modules/install` with `bundle_key=full_suite`.
- Sandbox-only DB fixes the seed needed beyond the lowstock harness (all in `harness/`):
  `user_companies` row for the admin (without it the act-as user sees no company), the
  legacy `EXTERNAL_API_KEY` from `.env.browse` seeded as an integration key
  (`seed_legacy_key.py`; the company-scope resolver compares the header with
  `settings.external_api_key`, so a freshly issued integration key alone scopes to zero rows
  and every product lookup comes back empty), and one `ai_assistant_configs` row (openai,
  `gpt-5.4-mini`) so the live parser has a config.

What is NOT real here, and why:

- **The parser is live** (`gpt-5.4-mini`, prompt `chatbot_semantic_parser` v3) through the
  sandbox OpenAI proxy; the backend was started with a placeholder `OPENAI_API_KEY` env var
  because the proxy injects the real credential. Everything after the parser is real: engine,
  lane, MCP hop, presenters, `engine._localize_result`, `Localizer.reply`.
- **File storage is local disk** (`harness/local_storage.py`), no S3/R2 credentials.
- **Respond.io is never reached.** Every console turn is a dry run.
- **`products.list_price` is NOT NULL in this schema**, so "no list price" is stored as `0`,
  and the reply shows `MYR 0.00` rather than `Not defined`. The `Tidak ditetapkan` / `未设定`
  value is therefore exercised through Dimensions (genuinely empty), not List Price.
- **The product has no brand, one company only**, so no `Company` line prints (single
  company lookups do not carry one); `*Syarikat:*` / `*公司:*` were not exercised.

Navigation: sidebar from `/` > System > Messaging > Chatbot Console (contact "Cloud Buyer"),
and System > Configuration > Translations. Replies below are read from the console thread and
checked against the stored raw reply in `chatbot.turns.response->reply->text` (which carries
the `*bold*` markers the thread renders as bold).

## Seeding fact (console is a dry run)

After every console turn above, `translation_memory` held **0 rows**. `label_catalog.resolve`
(`label_catalog.py:871`) skips `_seed` when `dry_run` is true and the console always is, so a
console turn seeds nothing and the console answers from the code defaults. For the
Translations page check the catalog was seeded the way a first live ms/zh turn does it:
`harness/seed_labels.py` calls `label_catalog.resolve(db, 'ms')` and `'zh'` with
`dry_run=False` against the browse DB, which inserted 313 `ai` rows per language (626 total).
No live webhook turn was possible (no Respond.io).

## Results

| # | Case (exact message) | Width | Expected | Seen (raw reply text) | Result |
|---|---|---|---|---|---|
| 1 | `Boleh saya dapat maklumat produk SRTWC286?` | 1280 | ms labels, data as stored | `Berikut ialah produk yang sepadan.` / `1. *Kod Produk:* SRTWC286` / `*Nama Produk:* Wall Hung Basin` / `*Keterangan:* Basin that has no hole` / `*Harga Senarai:* MYR 0.00` / `*Dimensi:* Tidak ditetapkan` / `*Spesifikasi:* Width: 500 mm` / `_Data dikemas kini: 04/10/2026 13:09:21_` | PASS |
| 2 | `SRTWC286 的产品资料` | 1280, 375 | zh labels, data as stored | `以下是匹配的产品。` / `1. *产品代码:* SRTWC286` / `*产品名称:* Wall Hung Basin` / `*描述:* Basin that has no hole` / `*标价:* MYR 0.00` / `*尺寸:* 未设定` / `*规格:* Width: 500 mm` / `_数据更新时间: 04/10/2026 13:09:21_` | PASS |
| 3 | `Boleh saya dapat fail lampiran produk SRTWC286?` | 1280 | attachment reply | Not a listing: the lane asks first, `Produk: SRTWC286` / `Jenis fail apa yang anda perlukan?` (the ask names no file type; the reply is localized) | PASS (see 3a, 3b) |
| 3a | `Boleh saya dapat sijil untuk produk SRTWC286?` (file link present) | 1280 | ms attachment labels | `Saya telah melampirkan fail di bawah.` / `1. *Kod Produk:* SRTWC286` / `*Nama Produk:* Wall Hung Basin` / `*Jenis Lampiran:* Certificate` / `*Nama Fail:* SRTWC286-cert.pdf` / `*No. Sijil:* WM-286-2026` / `*Sah Sehingga:* 2027-12-31` / `*Kesahan:* Sah` / `_Data dikemas kini: ..._`; the file is attached (`attachments_src`) | PASS |
| 3b | same message, attachment `file_path` blanked so the link cannot be given | 1280 | `Berikut ialah fail produk yang saya temui.`, `*Pautan Fail:*` | `Berikut ialah fail produk yang saya temui.` / same lines as 3a / `*Pautan Fail:* (pautan fail tidak tersedia buat masa ini)`; nothing attached | PASS |
| 4 | `product info SRTWC286` | 1280 | English unchanged | `Here are the matching products.` / `1. *Product Code:* SRTWC286` / `*Product Name:* Wall Hung Basin` / `*Description:* Basin that has no hole` / `*List Price:* MYR 0.00` / `*Dimensions:* Not defined` / `*Specs:* Width: 500 mm` / `_Data last updated: 04/10/2026 13:09:21_` | PASS |
| 5a | Translations > Into Malay, search `List Price` | 1280 | `ai` row `Harga Senarai` | row `List Price` / `English to Malay` / `Harga Senarai` / `ai` (seeded as above) | PASS |
| 5b | Translations > Into Chinese, search `Dimensions` | 1280 | `尺寸` | row `Dimensions` / `English to Chinese` / `尺寸` / `ai` | PASS |
| 5c | Edit Malay `List Price` to `Harga Katalog`, Enter | 1280 | saved | `PUT /api/v1/system/translations/<id>` 200, then the row reads `Harga Katalog`, source `manual`, by `Cloud Admin` | PASS |
| 5d | resend case 1 after the edit | 1280 | `*Harga Katalog:*` | `*Harga Katalog:* MYR 0.00`; `Dimensi`, `Keterangan`, intro unchanged | PASS |
| R | 375 layout (case 2 at 375x812) | 375 | no page overflow | `scrollWidth 360 == clientWidth 360 == body.scrollWidth 360` | PASS |

Console and network: no page errors; the turns are `POST /api/v1/system/chatbot/console/turn`
200; `console` shows only NextAuth debug lines. Product data (name, description, the
`Width: 500 mm` spec, the attachment type `Certificate`, the file name, the certificate
number, the ISO valid-until date) is untouched in every language, as intended.

## Observations (not defects of this lane)

- A first ms attempt, made before the scope fix described under Stack, returned the generic
  "no master products matched" reply with an English frame plus `Adakah anda mahu saya rujuk
  kepada pasukan purchasing?`. That was the empty-scope harness problem, not the lane; the
  ms/zh replies above were all taken after it.
- Case 3's brief expected the intro `Berikut ialah fail produk yang saya temui.` for the plain
  "lampiran" ask. Seen: the lane asks for a file type first (3), then on a typed ask and a
  signable file the intro is `Saya telah melampirkan fail di bawah.` (3a). The catalogued
  `Here are the product files I found.` appears when a link cannot be given (3b), which is
  presenters.py:76 versus presenters.py:1866. Both translations render.
- `Data last updated` is also translated (`Data dikemas kini`, `数据更新时间`); it is in the
  catalog, not a leak.
- Console `hits` on the Translations rows stay 0 (dry runs do not count hits).
- Stub list price quirk above (`MYR 0.00`) is a schema fact in this sandbox, not a lane defect.

## Needs the owner's dev copy

- A real live (non dry-run) first ms/zh turn through Respond.io, to see the seed written by
  the webhook path itself rather than by `harness/seed_labels.py`.
- A product with a real brand and a multi-company lookup, for the `*Syarikat:*` / `*公司:*`
  line.
