# UAC: DO history backfill (DO-HISTORY), v2

Plan: `PLAN-do-history-backfill-13oct.md` (v2, owner direction 13 Oct). Status: v3. C1 and C3 are being built; C2 is waiting on the cutover ask. The backfill
itself runs in the shared service. These are the CRM-side criteria.

## Slice 1 (built)

- **AC-DH15** An Overall Tracking row for an assumed DO clears `tracking_assumed_at`. Other DOs keep theirs.
- **AC-DH16** `tracking_assumed_at` is a system column. The DO ingest's header write guard refuses it.

## C1 provable links

- **AC-DH20** Key link: a DO line with `FromDocDtlKey` > 0 links to the SO line whose `source_ref`
  ends in that key. The key wins over the product match. (built, `test_exact_so_line_link`)
- **AC-DH21** Unique match: with no key, an SO with exactly one line of the product links.
  (built, `test_from_dtl_key_zero_links_by_doc_no_and_item`)
- **AC-DH22** Ambiguous: two lines of the product, with no location to tell them apart, stay
  unlinked with `so_line_unresolved`. (built, `test_from_dtl_key_zero_with_two_candidate_so_lines_warns`)
- **AC-DH23** Location narrowing: two lines of the product at different warehouses, and the DO
  line at one of them: links to that one.
- **AC-DH24** Location still ambiguous: two lines of the product at the DO line's warehouse, or a
  candidate with no warehouse: unlinked.
- **AC-DH25** The waiting fill applies the same narrowing.

## C2 assumed delivery

- **AC-DH30** With `do_assume_delivered_before` unset, nothing changes (status `new`, no date, no marker).
- **AC-DH31** With it set, a newly created DO with a DocDate before it is `delivered`, with
  `actual_delivery_date` = DocDate 00:00 and the marker set. A DO on or after the date is `new`.
- **AC-DH32** An adopted Excel row with no tracking and a DocDate before the date gets the same
  three writes. An adopted row with any tracking keeps it.
- **AC-DH33** An update of an existing DO never assumes.
- **AC-DH34** A dry run writes nothing.

## C3 quiet backfill

- **AC-DH40** A push with `"backfill": true` enqueues no embedding event and writes no per-DO audit
  row. It writes one summary audit event per batch.
- **AC-DH41** A push without the flag behaves exactly as today.
- **AC-DH42** The DO push writes no SO line or SO header field, and raises no OI row, planning
  change, email or WhatsApp, with or without the flag.
