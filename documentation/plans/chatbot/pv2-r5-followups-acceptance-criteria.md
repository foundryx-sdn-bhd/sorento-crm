# UAC: PV2 R5 follow-ups (15 Oct 2026)

Plan: `PLAN-pv2-r5-followups.md`. Every criterion is tested from a parser output (a parse object fed to PV2's code),
never from battery wording or a battery case id. Every red test carries at least one fresh paraphrase that is not in
the battery. "Same list re-shown" means the reply's option set equals the previous turn's option set.

## R5-1 Number / ordinal pick acts on the row
- Given the bot's last reply printed a numbered list (clarify options, or answer rows), when the parse picks row N
  (from '2', 'no 2', 'number two', '第二', 'yang kedua'), then PV2 acts on row N: the held question is answered for
  that row's subject. The reply does not repeat the same list.
- A pick past the end of the list (e.g. '7' on a 3-row list) asks again once with the same names, never acts on a
  wrong row.

## R5-2 'this product' after a one-item document
- Given the last answer showed a sales order or delivery order with exactly one product line, when the parse refers to
  'this product' / 'this one' / 'that item' / '这个', then the product is that line's product (the question runs on it).
- Given several product lines, PV2 asks which one, listing the real product names (codes as printed), never ids.
- Only lines the user may see count (lead ruling 15 Oct, security): a contact without the document's product-lines
  permission (lanes/business/fetch.py:3084-3085 drops them) never gets a line code from 'this product', neither in the
  reply nor as the resolved product; PV2 asks which product instead.

## R5-3 Account pick after a confirm or account menu is applied
- Given the bot asked to confirm an account or listed accounts, when the parse picks one ('A/C I only', 'the second
  one', the printed account name), then that account is applied and the held question runs. The same confirm is never
  asked twice in a row.

## R5-4 Promotion copy pick sends that copy
- Given the bot offered promotion copies (dealer copy / end-user copy), when the parse picks one ('dealer one',
  'end user', 'untuk pengguna'), then that copy is sent. The reply never echoes the promo code.

## R5-5 Question kind survives a pick
- Given the held question is an arrival / delivery-date question and the bot asked a clarify (customer, document,
  product), when the user answers the clarify, then the answer is an arrival / delivery-date answer. PV2 never replies
  'Sales orders or delivery orders?' nor 'need at least one filter' when the kind is already known.

## R5-6 Reply language
- The language typed this turn wins (en / ms / zh).
- A language-neutral turn (a number, a code, 'ok', a product code, a name) keeps the conversation language.

## R5-7 Reply-build guard
- No PV2 reply text contains a UUID (8-4-4-4-12 hex) or an internal id (a primary key or a register key). A label with
  no human name falls back to its printed code, never to an id.

## Owner product rules (15 Oct, unchanged)
A named customer carries when a product is named next; the clarify is only product -> customer; staff get stock
numbers, dealers got / no stock; promo replies send the list, never echo the code; size ask -> size or 'none on
record'; one exact option is used, a guess is a yes/no confirm with the real name.

## R5-8 Unlinked non-staff contacts see no other customer (crew answer 15 Oct, slice G)
PV2 rule: non-staff documents only from own links, unlinked none; owner ruling 15 Oct: unlinked contacts = dealer view.
- A non-staff contact with no customer links (contact_asker.classify not salesperson/staff; salespeople keep staff
  scope per V1-DOC-SCOPE-SALES) has an EMPTY customer scope in PV2. A partial-name customer search prints no customer
  names, and PV2's call into the v1 fetch passes that empty scope, so no other customer's rows are shown.
- Account (ledger) names in a confirm only ever come from the contact's own linked accounts.
- v1-only paths are unchanged (#1648 owns v1).
