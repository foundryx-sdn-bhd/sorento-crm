# PLAN: "account N" in a chatbot message selects the ledger whose Account level is N

Status: building, FULL track (migration: additive column + one-time NULL-only seed; permission check on one field; new parser key). Card final 2 Oct 2026 (owner: seed (a), Q6 (a), addendum approved, 403 rec approved). Red tests first.
Lane: ACCOUNT-LEDGER (branch `crew/account-ledger`). Card: `account-ledger-behaviour-card.md`.
UAC: `account-ledger-acceptance-criteria.md`.

## Owner rulings (2 Oct 2026)

- "account 1 means the A/C I ledger ... maybe an account parameter for the case of customer".
- Q1: a SETTING on each ledger, never words in the name. Q2: no carry-over; unnamed -> clarify.
- Q3 (a), Q4 (a): refusals name only the asker's own linked ledgers / the levels the name has.

## Design

1. **Setting.** `customers.account_level SMALLINT NULL CHECK (>= 1)`, model
   `app/models/order.py:50` + `__audit_columns__`. Migration `acct_ledger_0001`: add column,
   seed ONCE from the `A/C <n>` name marker where null (2,284 rows on the 25 Sep copy).
   Schemas `CustomerUpdate` / `CustomerCreate` (1..9) / `CustomerResponse` (`app/schemas/order.py`).
2. **Edit gate.** `PUT /customers/{id}` (`customers.py:215`) refuses a CHANGE to
   `account_level` without `order_management.customers.edit` (403); `POST /customers` refuses
   a set level without it too (security review S1).
3. **UI.** `CustomerForm.tsx` "Account level" clearable `SearchableSelect` (Account 1..9)
   in Basic Information; same read-only field in `CustomerDetail.tsx`. Types + zod schema.
4. **Parser.** Entity key `account` (integer or null), `head/parser.py:139-193`, required.
   `ACCOUNT_LEDGER_ADDENDUM` (verbatim in the card) between PO_SPO_WAREHOUSE and MEMORY
   (`chatbot_parser_prompt.py:621-622`); published UNLABELLED by the same migration file
   pattern as `alembic/versions/chatbot_self_reference_vocab.py` (separate migration
   `acct_ledger_0002_vocab`).
5. **Linked contacts.** `ContactCustomerScope.linked` carries `account_level`;
   `match_words` takes an account per word and keeps only links at that level. Engine
   `_customer_scope_gate` (`engine.py:1380`) passes it; refusal line names only the
   contact's linked ledgers of that name.
6. **Staff.** `resolve_gate.run` between `services.resolve_entity` and `run_gate`
   (`lanes/business/resolve_gate.py` ~1030): drop customer matches for that entity's token
   whose `account_level` differs (read by uuid in one query). All dropped -> refusal line
   naming the levels the name has.
7. **Unnamed (Q2).** A customer entity with `account` and null raw, and no other customer
   entity this message (and not "my": Q6 (a), "my" names the links) -> reply
   "Which customer is Account N for?", nothing fetched, no customer carried. The answer turn
   to that question keeps Account N for that one turn only (crew ruling 2 Oct 2026).
8. **Enforced contacts never see the staff level refusal** (it is built from company-wide
   resolver rows; security review B1). A resolver list cut at its cap never refuses (S-1).
9. **Refusal plumbing.** Reuse `customer_scope_refused` (`engine.py` ~4126, ~5212) with the
   turn's own refusal text.

## Coordination

PR #1405 (PROMPT-DYNAMIC) rewrites the parser prompt into variables. This lane adds one
trailing addendum + one entity schema key; whichever lands second re-applies the block.

## Test list (red first)

- Migration seed: marker -> level over real names (I-V, `(A/C 2)`, `[A/C II]-( KL OUTLET)`,
  bare and `(PROJECT)` stay null); re-run leaves an office-edited level alone.
- Customer API: PUT sets/clears `account_level`; without `customers.edit` a change is 403;
  response carries the field; audit row records the change.
- `test_contact_customer_scope.py`: match_words with account.
- `tests/chatbot/test_account_ledger_scope.py`: linked examples 1-3 and refusal wording.
- `tests/chatbot/test_account_ledger_staff.py`: resolve_gate narrowing, picker of 3, refusal.
- `tests/chatbot/test_account_ledger_unnamed.py`: unnamed account -> clarify, no fetch.
- Parser schema: `account` declared and required. FE vitest: form field renders, saves, clears.
- Kill tests: revert each rule, the matching test goes red.
