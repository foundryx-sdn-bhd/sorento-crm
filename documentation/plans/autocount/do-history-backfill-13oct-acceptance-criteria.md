# UAC: DO history backfill (DO-HISTORY), v2

Plan: `PLAN-do-history-backfill-13oct.md` (v2, owner direction 13 Oct). Status: draft, waiting on
crew finding K (does the API send the SO line key) and the one ask (cutover). The backfill
itself runs in the shared service. These are the CRM-side criteria.

## Slice 1 (built)

- **AC-DH15** An Overall Tracking row for an assumed DO clears `tracking_assumed_at`. Other DOs keep theirs.
- **AC-DH16** `tracking_assumed_at` is a system column. The DO ingest's header write guard refuses it.

## C1 exact links (if K = yes)

- **AC-DH20** A DO line with a `FromDocDtlKey` links to the SO line whose `source_ref` ends in that
  key, inside SO `FromDocNo`.
- **AC-DH21** A DO line with no key stays unlinked, even when its SO has exactly one line of that
  product. The waiting fill does not link it either.
- **AC-DH22** A re-push of a DO whose lines held fallback links: a keyed line takes the exact link,
  and a keyless line is cleared.
- **AC-DH23** A key whose SO line is not in the CRM yet links when that SO arrives (waiting fill,
  exact key only).

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
