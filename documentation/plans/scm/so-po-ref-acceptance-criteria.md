# SO-PO-REF acceptance criteria

Owner rulings 7 Oct 2026 (behaviour card v1 + v2 note, crew lane SO-PO-REF). Plan: `PLAN-so-po-ref.md`.

## Ingest

- **U1** An SO payload carrying `po_ref` "PO-4500 123" stores `sales_orders.po_ref` = "PO-4500 123" (trimmed, as sent) and `po_ref_key` = "4500123" on create.
- **U2** A re-push of the same SO with `po_ref` "4500777" overwrites both columns.
- **U3** A re-push with `po_ref` sent blank ("" or whitespace) clears both columns to NULL.
- **U4** A re-push with NO `po_ref` key (older shared-service contract) leaves both columns untouched.
- **U5** `po_ref` never touches `project_label` / `project_label_source`: an SO with `ref` "THE MET KL" and `po_ref` "4500123" keeps Project "THE MET KL" (source `ref`); an SO with only `po_ref` gets no Project from it.
- **U6** `po_ref` longer than 255 characters is rejected by the canonical schema like `ref` is.
- **U7** Key fold (same function at write and lookup): upper-case, keep only letters and digits, then drop ONE leading "PONO" or "PO" when something remains after it. "po 4500-123", "PO#4500123", "P.O. No: 4500 123", "4500123" all give "4500123"; "SR-2026/10-035" gives "SR202610035"; "PO" alone gives "PO"; blank gives NULL.

## SO screens

- **U8** GET /sales-orders rows and GET /sales-orders/{id} carry `po_ref` (raw value; null when blank).
- **U9** The existing SO list search box also matches PO ref (owner Q5 a): case-insensitive contains on the raw value like the other fields ("4500" finds "PO-4500 123"), OR contains on the folded key of the search text ("po 4500-123" finds it too).
- **U10** SO list has a "PO ref" column (sortable, resizable, truncate + title for long values), blank shows the normal empty value.
- **U11** SO detail General tab, Order card, shows a "PO ref" field next to Project; view and edit layouts are the same (read-only value; not editable, AutoCount owns it).
- **U12** Usable at 375px and 1280px.

## Chatbot (audience decides the meaning)

- **U13** Dealer/customer contact, "status of PO 4500123", PO ref on exactly one of their SOs: reply is `PO ref: 4500123` then the existing SO card for that SO.
- **U14** Same, PO ref on several of their SOs: `PO ref: 4500123`, `Sales orders: 3`, then one card each, newest order date first.
- **U15** Same, no SO of theirs carries it (including when ANOTHER dealer's SO does): `I could not find PO ref 4500123.` No refusal line, no card, nothing about the other dealer.
- **U16** Match is the key fold of U7 on both sides; "PO 4500-123" finds an SO stored as "4500123" and one stored as "PO-4500 123". No partial or fuzzy match.
- **U17** Sorento staff asking "status of PO 4500123" gets the existing supplier purchase order answer, unchanged.
- **U18** The PO number reaches code only as a structured parser output field; no regex/keyword detection of PO numbers in code. Audience comes from the session, never from the words.
- **U19** Reply lines carry no dot separators; one fact per line `Label: value`.
- **U20** Who is "staff": `is_staff_profile(profile)` (`respond_contacts.chatbot_profile.tier == "office"`, `app/services/chatbot/turn/state.py:218`). Every other contact is the dealer/customer audience. A dealer contact with no customer link sees no SO, so any PO ask answers `I could not find PO ref <x>.`
- **U21** The parser teaches `po_refs` for any "PO <number>" in either audience; staff turns ignore it (supplier PO path unchanged), so the meaning is decided by the session alone.
