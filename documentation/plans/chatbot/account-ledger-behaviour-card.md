# ACCOUNT-LEDGER behaviour card, revision 2 (2 Oct 2026)

"account 1" in a chatbot message selects the customer ledger whose **Account level setting**
is 1. Revision 1 read the `[A/C I]` words in names; the owner ruled that out (Q1).

## Owner answers to revision 1

- Q1: NO name markers. A SETTING on each ledger says its account level.
- Q2: NO carry-over. Account words with no customer named this turn -> the bot asks which customer.
- Q3: yes, a linked contact asking a ledger it is not linked to is refused naming only its own linked ledgers of that name.
- Q4: yes, staff asking a ledger the name lacks is refused naming the levels the name has.
- Q5: addendum text below, verbatim.

## Q1 design: the Account level setting

**Where it lives.** The existing customer record (`customers` table, `Customer` model,
`sorento_crm_backend/app/models/order.py:50`). A "ledger" IS a customer row: SOON HENG HARDWARE
CO.SDN.BHD. [A/C I] (300-S002) and [A/C II] (300-S082) are two `customers` rows.

**Data model (additive).** One column, `customers.account_level SMALLINT NULL`,
`CHECK (account_level >= 1)`. Null = no level set (the row is not a numbered account).
Added to `__audit_columns__` (`order.py:70-95`) so a change is in the customer's history.
Migration `acct_ledger_0001`. Nothing else: no new table (one preference = one column).

**Screen.** One plain field on the existing customer form and detail page, no new screen:
- Edit: `sorento_crm_frontend/app/(protected)/order-management/customers/components/CustomerForm.tsx:164-247`
  ("Basic Information"), a clearable `SearchableSelect` "Account level" with options
  Account 1 .. Account 9, beside "Sales Agent" (same component, `CustomerForm.tsx:228-245`).
- View: same field, same position, read-only, in `CustomerDetail.tsx` (View = Edit layout).
- Saved through the existing `PUT /api/v1/order-management/customers/{id}`
  (`app/api/v1/order_management/customers.py:215`) via `CustomerUpdate` (`app/schemas/order.py:~54`).
Plain field on an existing form, so no Lavish mock (owner's own threshold).

**Who can edit.** Holders of `order_management.customers.edit` (existing permission, already
used at `customers.py:183`). Note: the PUT route itself today checks only sign-in
(`customers.py:215-220`, `Depends(get_current_user)`). Recommendation: the route refuses a
CHANGE to `account_level` without `order_management.customers.edit` (403); the rest of the
route is left as it is (fixing the whole route is a separate lane). security-reviewer runs.

**Default for existing ledgers: one-time seed (recommended).** The migration sets
`account_level` from the name marker ONCE, only where it is still null: `[A/C I]`/`(A/C I)` -> 1,
II -> 2, III -> 3, IV -> 4, V -> 5, `(A/C 2)` -> 2. After that the name is never read again;
the setting is the truth and the office corrects it on the form. Count on the 25 Sep prod copy
(`sorento_ai_automation_0925`): I 1159, II 84, III 513, IV 526, V 1, "2" 1 = **2,284 rows**;
every other row (bare, (PROJECT), [CERAMIC], (SRT) ...) stays null. Crew rule: this UPDATE is
held for the owner on the shared dev DB; on prod it runs inside the migration at deploy.
Option b: no seed, the office sets levels by hand (the bot then answers nothing for "account N" until they do).

**How the bot uses it.**
1. Parser emits `account` (integer or null) on a customer entity (addendum below).
2. Linked contact: name words match its links as today, then keep links whose
   `account_level` equals `account`. None left -> refusal naming its linked ledgers of that name (Q3).
3. Staff: right after the customer lookup and before the "which customer" grouping
   (`lanes/business/resolve_gate.py` ~1030, before `run_gate`), customer rows whose
   `account_level` differs are dropped for that word. None left -> refusal naming the levels
   that name has, e.g. "SOON HENG TRADING has Account 1, Account 3 and Account 4." (Q4).
4. Account words with no customer named this message (Q2): the bot asks
   "Which customer is Account 2 for?" and fetches nothing. Nothing is carried from earlier turns.

## Examples (dev data; levels as the seed would set them)

| # | Who | Message | Proposed |
|---|-----|---------|----------|
| 1 | contact 80560c8f (12 links incl. 6 HANLIM TRADING SDN BHD rows) | "Hanlim account 2 outstanding" | HANLIM TRADING SDN BHD [A/C II] only (the one link at level 2) |
| 2 | same | "Soon Heng account 1 sales" | SOON HENG HARDWARE CO.SDN.BHD. [A/C I] |
| 3 | same | "Soon Heng account 2" | "Sorry, that isn't under your account. I can only check on SOON HENG HARDWARE CO.SDN.BHD. [A/C I]." |
| 4 | staff | "Soon Heng account 1 outstanding" | which-customer list of 3: SOON HENG HARDWARE (300-S002), PLUMBING & SANITARY WORKS (300-S037), TRADING (300-S254), each level 1 only |
| 5 | staff | "Soon Heng Trading account 2" | refused: has Account 1, Account 3 and Account 4 |
| 6 | anyone | "account 2 outstanding" (no name) | "Which customer is Account 2 for?" |

## Q6 (answered: a)

Q6. Linked contact "my account 1" (names no customer, but "my" means its own links):
(a) "my" counts as naming the customer: answer its level-1 links [rec: "my" already scopes to the links, `engine.py:1398-1400`];
(b) clarify like any unnamed account ask.

## Q5: the exact parser addendum

Inserted in `sorento_crm_backend/app/services/chatbot_parser_prompt.py` between
`SEMANTIC_PARSER_PROMPT += PO_SPO_WAREHOUSE_ADDENDUM` (line 621) and
`SEMANTIC_PARSER_PROMPT += MEMORY_ADDENDUM` (line 622), so MEMORY stays the tail. The entity
object in the strict schema (`head/parser.py:139-193`) gains `"account": integer or null`
(required, as every entity key). Published as a new UNLABELLED prompt version; the owner moves
the `production` label. If PR #1405 lands first, the same block is appended to its text.

```text

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CUSTOMER ACCOUNT NUMBER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Every entity object carries one more key, exactly as if it were listed there:

  "account": an integer, or null

== ACCOUNT: which numbered account of a customer the message names ==
A customer can have several accounts, numbered 1, 2, 3 ... Set "account" on the CUSTOMER
entity when the CURRENT message names an account number for it, in any spelling or
language. Roman numerals become integers.
  - "account 1", "acc 1", "a/c 1", "A/C I", "ac1", "akaun 1", "户口1", "第一个户口" -> 1
  - "account 2", "acc2", "A/C II", "a/c ii", "akaun 2" -> 2
  - "A/C III" -> 3, "A/C IV" -> 4
The account words are NOT part of the name: leave them out of raw and canonical_code.
  - "Soon Heng account 1 outstanding" -> entities [{"raw": "Soon Heng", "hint":
    "customer", "account": 1}]
  - "hanlim acc 2 sales" -> entities [{"raw": "hanlim", "hint": "customer", "account": 2}]
  - "Hanlim A/C II and 1 Living A/C I" -> two customer entities, account 2 and account 1
An account number with NO customer name in the current message ("account 2 outstanding")
-> emit ONE entity {"raw": null, "hint": "customer", "account": 2, "current_message":
true}. Never copy a customer name from earlier in the conversation to fill it.
"my account" with no number names no account: "account" stays null and self_reference
is true as usual. "my account 2" -> self_reference true AND the entity {"raw": null,
"hint": "customer", "account": 2}.
Every non-customer entity, and every customer entity without an account number, has
"account": null. Never guess a number.
```
