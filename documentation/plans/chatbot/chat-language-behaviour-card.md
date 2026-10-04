# CHAT-LANGUAGE behaviour card (2 Oct 2026)

Owner approved the direction (2 Oct): one label catalog; only labels and fixed sentences are
translated, through the translation module (en->ms, en->zh, staff-correctable); values are never
translated; the reply language comes from per-message detection plus `pick_language`; a model
never translates a whole reply. This card holds the remaining specifics.

## Languages

English (`en`), Malay (`ms`), Chinese (`zh`), the three the bot already speaks
(`chatbot_reply_copy.py:268` `FALLBACK_LANGUAGES`, `lanes/fallback.py:33` `LANGUAGES`).
Any other language is answered in English. The system code is `zh`. The staff UI's own locale
switcher calls Chinese `ch` (`sorento_crm_frontend/i18n/config.ts:40`), but that is the CRM
screen language and is not read by the bot.

## How the language is chosen, per message

No model call is used. Detection is deterministic and runs on the message text after product
codes, numbers and customer names are removed:

1. **Chinese** if the message has at least one Chinese character.
   "SRTSWT3001 有货吗" -> zh.
2. **Malay** if it has more Malay marker words than English marker words. Malay markers
   include ada, tak, berapa, boleh, nak, saya, stok, barang, bila, sampai, sudah, belum, ini,
   itu, untuk, dengan, mana, harga, lagi and tolong. English markers include do, have, how,
   many, what, when, is, are, can, please, stock and check.
   "ada stok SRTSWT3001?" -> ms. "SRTSWT3001 ada stock tak" -> ms (Manglish counts as Malay
   when its Malay words outnumber its English ones).
3. **English** if it has more English marker words than Malay.
   "can check stock SRTSWT3001 ah" -> en.
4. **No decision** if the message has no marker words at all: a bare code, a number picked
   from a list, "ok" or "thanks", or a tie. The reply then keeps the **conversation's last
   reply language**. If there is none, it uses the contact's saved language (memory fact). If
   there is none of either, English.

Switching mid-chat: the next message that decides a language switches the reply at once, and
that language is kept for the replies that follow with no decision.

## What changes language, and what never does

**Translated** (catalog entries only, an allowlist):
- Field labels, e.g. Product Code, Total, Warehouse, Quantity On Hand.
- Intros, e.g. "Stock summary for the requested products."
- Fixed verdict sentences, e.g. "yes, we have stock."
- Flag lines, e.g. PRODUCT DISCONTINUED.
- The "Data last updated" footer.
- "No stock found for ...".

**Never translated:**
- Every value: product and customer codes, product names, customer names, quantities,
  prices, dates, location codes (BRW-BB) and warehouse names.
- Anything not in the catalog. An unknown label stays English, so a location code used as a
  label is never touched.

**Safety check:** a translated sentence must carry exactly the same `{tokens}` as its English
source. If it does not, the English is used for that line and the trace records it.

## Examples, stock answer (dev-shaped data)

**A. Dealer, compact mode, "ada stok SRTSWT3001?" (ms)**

```
Ringkasan stok untuk produk yang diminta.

1. *Kod Produk:* SRTSWT3001
*Jumlah:* 51
*BRW-BB:* 30
*PKL-01:* 21

_Data dikemas kini: 02/10/2026 09:15_
```

**B. Same ask in zh, "SRTSWT3001 有货吗"**

```
所请求产品的库存摘要。

1. *产品代码:* SRTSWT3001
*总数:* 51
*BRW-BB:* 30
*PKL-01:* 21

_数据更新时间: 02/10/2026 09:15_
```

**C. Dealer, availability mode, "SRTSWT3001 10 unit ada?" (ms)**

```
SRTSWT3001 x 10: ya, stok ada. Sila rujuk jurujual anda.
```

The same ask in zh:

```
SRTSWT3001 x 10: 有库存。请联系您的销售员。
```

Incoming in zh:

```
SRTSWT3001 x 10: 目前没有库存，ETA 15/10/2026。
```

**D. Staff, detailed mode, "BRBC22102W 库存" (zh)**

```
1. *公司:* SORENTO
*产品代码:* BRBC22102W
*产品名称:* WALL BASIN 22"
*仓库:* BUKIT RAJA
*系统位置:* BRW-BB
*现有数量:* 40
```

**E. Mid-chat switch.** "stock SRTSWT3001" -> English answer. Then "berapa unit kat PKL?" ->
Malay. Then "1" (a pick) -> still Malay.

## Questions (max 5, each with a recommendation)

- **Q1 (owner: a, 2 Oct). Manglish tie** ("stock ada?": 1 Malay word, 1 English word). (a) keep the
  conversation's language; (b) Malay. **Rec: (a)**. A tie is no evidence, and a flip on a tie
  reads as random.
- **Q2 (owner: a, 2 Oct). Saved language vs the message.** (a) the message's own language wins, and the saved
  fact is only used when nothing else decides; (b) the saved fact always wins. **Rec: (a)**,
  because the ask is "follow the user's language".
- **Q3 (owner: a, 2 Oct). Abbreviations.** (a) PO, SO, SPO, DO, GR, ETA, ETC and O/S stay as they are in every
  language (they are what is printed on the documents); (b) translate them. **Rec: (a)**.
  Only spelled-out words translate: "PO Number" -> "No. PO" / "PO 编号".
- **Q4 (crew: a, 2 Oct). Where the ms/zh wording comes from.** (a) Reviewed ms/zh defaults ship in code and are
  written into the Translations page (source `ai`) the first time they are used. Staff correct
  them there, and a staff edit (`manual`) always wins. A chat turn never calls a model; an
  uncatalogued label stays English. (b) Ask the AI model on a miss during the turn. **Rec:
  (a)**: no added latency, and no model writes a word a dealer reads unchecked.
- **Q5 (crew: a, 2 Oct). Escalation offer.** "Would you like me to escalate to X team?" is matched as English
  text in two places (`order_list.py:226`, the reply exchange). (a) Translate it in a later
  slice, after both matchers read a stored flag instead of the English text; until then it
  stays English. (b) Translate it now. **Rec: (a)**. Stock goes first, the offer goes last.
