# UAC: DO history backfill (DO-HISTORY), v2

Plan: `PLAN-do-history-backfill-13oct.md` (v2, owner direction 13 Oct). Status: v4. Slice 1, C1, C2 and C3 are built; line keys for ambiguous lines are on hold for the owner. The backfill
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

## C2 assumed delivery (owner answer 13 Oct)

- **AC-DH30** With `do_assume_delivered_before` unset (the default), nothing changes: status
  `new`, no date, no marker.
- **AC-DH31** With it set to 2026-07-01, a newly created DO with DocDate before it is `delivered`
  with the marker set and `actual_delivery_date` EMPTY. A DO dated on or after the cutover is `new`.
- **AC-DH32** An adopted Excel row with no tracking, dated before the cutover, gets the same
  writes. An adopted row with a date, or with a status other than `new`, keeps its tracking.
- **AC-DH33** An update of an existing DO never assumes.
- **AC-DH34** A dry run writes nothing.
- **AC-DH35** Delivered = delivered status AND (a date OR the marker). Outstanding is its
  null-safe negation. A delivered status alone, or the marker under `new`, is outstanding.
- **AC-DH36** A Master-sheet re-upload with no Tracking row for an assumed DO keeps it delivered
  with its marker (review round 1).

## C3 quiet backfill

- **AC-DH40** A push with `"backfill": true` writes no per-DO audit row and no listener embedding
  event for any DO or line; the batch queues one embedding event per written DO in one insert.
- **AC-DH43** The batch's one `IMPORT` audit row carries the company, book, DocDate range,
  verdict counts and every created or updated DO (source ref, order id, outcome), and is written
  in the batch's transaction.
- **AC-DH41** A push without the flag behaves exactly as today.
- **AC-DH42** The DO push writes no SO line or SO header field, and raises no OI row, planning
  change, email or WhatsApp, with or without the flag.
