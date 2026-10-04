# UAC: account N selects the ledger whose Account level is N

Plan: `PLAN-account-ledger-2oct.md`. Examples use dev data (25 Sep prod copy, levels as seeded).

- AC-1: The customer form and detail page show "Account level" (Account 1..9, clearable; the API rejects 10 and above) in
  Basic Information; saving stores it, clearing stores none; the change is in the customer's history.
- AC-2: Only a user with `order_management.customers.edit` can change Account level (403 otherwise).
- AC-3: After the migration, every customer whose name carries `[A/C n]` / `(A/C n)` has that
  level (2,284 rows on the copy); every other customer has none. Names are never read afterwards.
- AC-4: The parser output carries `account` on every entity: "Soon Heng account 1" gives
  raw "Soon Heng", account 1; "acc 2", "a/c 2", "A/C II", "ac2", "akaun 2" give 2; none -> null.
- AC-5: A message with no account words behaves exactly as today (linked and staff).
- AC-6: Linked contact (links incl. six HANLIM TRADING SDN BHD rows): "Hanlim account 2
  outstanding" answers HANLIM TRADING SDN BHD [A/C II] only.
- AC-7: Same contact: "Soon Heng account 1" answers SOON HENG HARDWARE CO.SDN.BHD. [A/C I];
  "Soon Heng account 2" refuses naming only that linked ledger; no unlinked ledger is named.
- AC-8: Staff, "Soon Heng account 1 outstanding": the which-customer list shows only level-1
  ledgers of every customer the search matches today (live, 2 Oct 2026: SHING SOON HENG HARDWARE
  SDN BHD [A/C I], SOON GUAN HENG TRADING SDN BHD [A/C I], SOON HENG PLUMBING & SANITARY WORKS
  [A/C I], SOON HENG TRADING [A/C I], SOON HENG HARDWARE CO.SDN.BHD. [A/C I]); a pick answers that
  one only. A cut-off resolver list never produces a "has no Account" refusal.
- AC-8b: When the typed word is exactly one trading name ("Soon Heng Trading account 1"), only
  that name's ledgers count: answered directly for SOON HENG TRADING [A/C I], no picker; AC-9's
  refusal holds whatever accounts the similar names have.
- AC-9: Staff, "Soon Heng Trading account 2": refused, naming Account 1, Account 3 and Account 4.
- AC-10: "account 2 outstanding" with no customer named this message: "Which customer is
  Account 2 for?", nothing fetched, no customer carried from earlier turns. The ANSWER to that
  question ("Hanlim") keeps Account 2 for that one turn (linked: only Hanlim's Account 2 link;
  staff: Hanlim's Account 2 ledgers); any other next message drops it (crew ruling, 2 Oct 2026).
- AC-11: Changing a ledger's Account level on the form changes what "account N" answers on
  the next chatbot turn.
- AC-12: Two customer words with no account words on a linked contact behave exactly as before
  this lane (the pick question), per AC-5.
