# UAC - Contact region (West / East Malaysia)

Plan: `PLAN-contact-region-5oct.md`. IDs AC-CR-n. Owner answers 5 Oct: Q1 multi-select West/East, Q2 copy regions, Q3 list column + filter.

## Schema
- AC-CR-1: `respond_contacts.regions` exists, text[] NOT NULL, server default `{west}`; every existing contact reads `{west}` after upgrade.
- AC-CR-2: the database rejects an empty set and any value outside `west` / `east` (CHECK `ck_respond_contacts_regions`).

## API (`PUT /api/v1/user-management/contacts/{id}/chatbot`, `GET` contact detail)
- AC-CR-3: the contact response carries `regions` (list of codes), `["west"]` for an untouched contact.
- AC-CR-4: a PUT with `regions: ["west","east"]` stores it; the response and a fresh GET show it.
- AC-CR-5: a PUT without `regions` leaves the stored value alone (absent = leave alone, like every switch on the card).
- AC-CR-6: `regions: []`, `regions: ["north"]` or `regions: null` -> 422, row unchanged.
- AC-CR-7: duplicates are collapsed and order is canonical (`["east","west","east"]` stores `{east,west}` in one fixed order).
- AC-CR-8: a caller without `user_management.contacts.edit` gets 403 and the row is unchanged.
- AC-CR-9: a region change writes an audit row for the contact (automatic RespondContact audit) naming the old and new value.

## Chatbot effect (`eta_policy.contact_regions` / `rules_for_contact`)
- AC-CR-10: `contact_regions(db, id)` returns the contact's stored set; unknown / missing id returns `{west}`.
- AC-CR-11: a contact holding `{west}` is told only about packing lists whose regions overlap West; an East-only packing list stays hidden.
- AC-CR-12 (owner ruling 5 Oct 11:50, LITERAL): a contact holding only East sees East-only and West + East packing lists, NOT West-only; holding both sees all three; holding only West sees West-only and West + East. Changes #1439 behaviour for any contact holding East.
- AC-CR-13: staff screens and a bare API key with no contact in play are unfiltered (unchanged).

## Screen (Contact > Access > Chatbot card)
- AC-CR-14: the card shows a Regions `SearchableMultiSelect` with labels "West Malaysia" / "East Malaysia" (from `lib/packing-list-regions.ts`), showing the stored value.
- AC-CR-15: changing it saves at once through the card's save, sending `regions`; success toast like the other switches.
- AC-CR-16: the control cannot be left empty (no save of an empty set).
- AC-CR-17: no UUID shown; usable and not clipped at 375px and 1280px.

## Copy access (Q2 = yes)
- AC-CR-18: "Copy access from contact" copies `regions` with the other chatbot switches; the preview/diff lists a "Regions" change (labels West Malaysia / East Malaysia) when source and target differ, and no change when equal.

## Contacts list (Q3 = yes)
- AC-CR-19: the list response carries `regions` per row; the list shows a Regions column (one Badge per region, labels West Malaysia / East Malaysia), explicit `size`, truncate + title.
- AC-CR-20: list filter `region=west|east` returns contacts whose stored set contains that code (array contains); absent = no filter; other value -> 422.

## Region rule (owner ruling 5 Oct 11:50: literal)
- AC-CR-21: `eta_policy.visible_regions(held)` returns the held set as-is, filtered to west/east; empty / None / only unknown codes -> {west}. It is the only place that turns a stored set into a visible set.
- AC-CR-22: the Regions control on the card is the packing-list picker reused unchanged: `SearchableMultiSelect` + `PACKING_LIST_REGION_OPTIONS`, last region cannot be removed (same as `AttachmentUploadDialog.tsx:551-558`, `PackingListForm.tsx:338-344`).
