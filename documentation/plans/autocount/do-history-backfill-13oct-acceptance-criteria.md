# UAC: DO history backfill (DO-HISTORY), v2

Plan: `PLAN-do-history-backfill-13oct.md` (v2, owner direction 13 Oct). Status: v5. Slice 1, C1, C2 and C3 are built (PR #1633). Owner rulings 14 Oct: no line-key source; several lines of the product link to the SO + product group (AC-DH22..26, follow-up PR). The backfill
itself runs in the shared service. These are the CRM-side criteria.

## Slice 1 (built)

- **AC-DH15** An Overall Tracking row for an assumed DO clears `tracking_assumed_at`. Other DOs keep theirs.
- **AC-DH16** `tracking_assumed_at` is a system column. The DO ingest's header write guard refuses it.

## C1 provable links

- **AC-DH20** Key link: a DO line with `FromDocDtlKey` > 0 links to the SO line whose `source_ref`
  ends in that key. The key wins over the product match. (built, `test_exact_so_line_link`)
- **AC-DH21** Unique match: with no key, an SO with exactly one line of the product links.
  (built, `test_from_dtl_key_zero_links_by_doc_no_and_item`)
- **AC-DH22** Group link (owner rule 14 Oct): with no key, an SO with two or more lines of the
  product links the DO line to no single line, whatever their warehouses. The line keeps
  `from_doc_no` + `product_id` (the SO + product group) and warns `so_line_unresolved`.
  (`test_from_dtl_key_zero_with_two_candidate_so_lines_warns`, `tests/test_do_history_links.py`)
- **AC-DH23** No location narrowing (owner rule 14 Oct, supersedes the 13 Oct crew definition):
  two lines of the product at different warehouses, the DO line at one of them: still no line,
  group-linked. Dev: 570 of 9,828 single-match DO lines sit at another warehouse than their SO
  line, so the warehouse does not prove the line.
- **AC-DH24** Two lines at the DO line's warehouse, or a candidate with no warehouse: no line,
  group-linked (unchanged).
- **AC-DH25** The waiting fill never narrows by location either.
- **AC-DH26** The unique match ignores location: the one line of the product links even when it
  sits at another warehouse than the DO line.

## Cancel SO groups (owner Q2 (a), 14 Oct)

- **AC-DH27** A key-less Cancel SO line is decided with every line of its item on the SO,
  whatever the location: two lines of the item at two warehouses, one cancel that is not every
  transfer: `undetermined`, no line. (`test_no_key_the_so_group_is_so_and_item_whatever_the_location`)
- **AC-DH28** The SO's one line of the item takes the cancel even at another location (`item`).
  (`test_no_key_one_line_of_the_item_at_another_location_matches`)
- **AC-DH29** The group's DO qty counts every live DO line naming the SO and item at any
  location. (`test_a_do_at_another_location_still_counts_as_delivered_for_the_so_group`)
- **AC-DH30a** Cancels at two locations that together are every transfer, with nothing
  delivered, are one `group_whole`. (`test_two_locations_in_one_batch_are_one_so_group`)
- Cancel PO / SPO groups keep the location (unchanged).

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
