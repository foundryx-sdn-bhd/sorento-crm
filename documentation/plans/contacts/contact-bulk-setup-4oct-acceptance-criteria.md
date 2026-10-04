# UAC: contact bulk setup (CONTACT-BULK-SETUP)

Fake data only in tests. "BO" = Access Level starting "Business Owner"; "EMP" = "Employee".

## Parse
- U1 Only rows whose APPROVAL cell (header starting "APPROVAL", trimmed, any case) is YES are read; PENDING/blank skipped.
- U2 "Sorento Sdn Bhd" -> SRT, "Mocha Sdn Bhd" -> MCH, "Sorento Sdn Bhd, Mocha Sdn Bhd" -> both.
- U3 Up to 11 Name/Contact/Access-level triples per row are read; empty triples skipped.
- U4 Phone normalisation: `0123456789`, `60123456789` (int or text), `+60 12-345 6789`, `012 345 6789` all -> `+60123456789`. A number that is not a MY mobile (`+601` + 8/9 digits) is listed as invalid, never matched.
- U5 Same phone in several rows/slots: desired set = union (BO in any row for a company gives that company's dealer type).

## Desired per contact
- U6 BO: access types dealer (SRT) / mocha_dealer (MCH) for each company + end_user. EMP: end_user only.
- U7 Companies per row; stock override mode availability, include SRT -> BRW+MWH, MCH -> HQ+MOCHA-WH for the contact's desired companies (warehouse codes resolved to ids in the DB); segment retail; media image allowed; three agents allowed 2026-01-01 -> 2026-12-31; stock checks + ETA buffer switches ON; escalation switch (`escalation_allowed`, "Chatbot hands over to support teams") OFF (owner Q3, 4 Oct).

## Dry run (default)
- U8 Writes nothing (DB unchanged after run).
- U9 CSV: one row per contact x facet with columns phone, contact, excel_rows, facet, before, add, status; plus missing-contact list (phone, name, Excel row, reason missing/invalid).
- U10 Contact already holding everything -> status already, zero adds.

## Apply
- U11 ADD only, except stock override (U13) and escalation (U13b): existing access types, companies, segments, agent grants, media rows and the other switches are never removed or turned off.
- U12 Existing agent grant with other dates/allowed -> kept untouched, reported "kept".
- U13 Stock override is SET, not merged (owner Q2, 4 Oct): mode becomes availability and the include list becomes exactly the rule's locations for the contact's desired companies; excluded_warehouse_ids becomes NULL; hide_zero_locations is kept on an existing row (false on a new row). CSV shows old -> new for mode and locations.
- U13b escalation_allowed is set false where true; CSV shows old -> new.
- U14 Existing image media row with is_allowed false -> set true; limits untouched.
- U15 One transaction per contact; a failure on one contact rolls back that contact only and is reported; others apply.
- U16 One audit row `contact.bulk_setup` per changed contact (entity respond_contacts, old/new per facet).
- U17 Idempotent: second --apply makes zero changes and zero audit rows.
- U18 Contacts are matched by digits of phone_number (formatting-insensitive); never created.

## Links
- U19 --links-review writes an xlsx: one block per Excel row (company name), up to 5 candidates per company in that row's companies: company code, debtor code (customer_code), customer name, match reason (similarity score, or "already linked"), empty Link column.
- U20 Nothing is linked by --links-review or by --apply.
- U21 --apply-links links only rows whose Link cell is ticked (Y/YES/X/TRUE/1), to every found contact of that Excel row, source bulk_setup, company_id = customer's, is_primary false; existing pairs skipped; idempotent; audit row per link.
- U22 --apply-links with a debtor/customer that no longer exists -> reported, others still linked.
