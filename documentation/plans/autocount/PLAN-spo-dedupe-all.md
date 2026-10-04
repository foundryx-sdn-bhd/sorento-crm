# PLAN: SPO dedupe for all SPOs + container received refresh

Status: built, in review on PR #1428 (small fix track: ops tooling, no migration, no UI, no auth change)

UAC: `spo-dedupe-all-acceptance-criteria.md`

## Journey

Owner applied `scripts/oneoff/dedupe_spo_standalone.py --apply` to SPO-2026/08-0074 on prod
(superseded 10, orphans removed 2, blocked 0) on 2 Oct 2026. They now want:

1. the same follow-AutoCount dedupe over every SPO that still carries both Excel-era and
   AutoCount rows, in batches, dry run first;
2. the container received figures (`inbound_shipment_lines.quantity_received` / `line_status`)
   that the chatbot "incoming" answer reads to be refreshed, without opening each packing list.

Real symptom: SRTWCX8605-S-RL-PJ showed incoming 4 on container GCXU6137164 while its packing
list showed 99/99 received; the stored line said 95 until the page was opened.

## Facts (origin/main)

- Dedupe CLI: `--company`, `--spo` (repeatable, required), `--apply`. One transaction per SPO;
  guard failure rolls that SPO back and the run continues (exit 3); other errors stop (exit 4).
  After apply it only prints "packing list to re-open".
- Stored container figures refresh only on packing list open
  (`app/api/v1/procurement/packing_lists.py`), ingest or allocation writes, via
  `InboundShipmentService(db).refresh_shipment_line_statuses(shipment_id)`
  (`app/services/procurement_service.py`), which commits itself.
- Nightly `spo_container_relink_sweep` does not refresh.

## Build

1. Dedupe `--all` (mutually exclusive with `--spo`) + `--limit N`: scan the company for SPO
   numbers holding BOTH an Excel-era row (no `source_ref`, `source_system` NULL/`scm_upload`,
   no `po_line_id`) and an AutoCount row (`source_ref` set), ordered by `spo_number`. Dry run
   prints a one-line summary per SPO, totals, and the final lists of SPOs that would change and
   that were blocked. Existing per-SPO transaction + guards unchanged.
2. After a committed SPO apply, call `refresh_shipment_line_statuses` for every shipment the SPO
   touched (replaces the "re-open" print). The dedupe script stays import-free at module load;
   the refresh imports the app lazily and runs on its own ORM session. A refresh failure is
   reported and the run continues (the SPO itself is already committed).
3. New `scripts/oneoff/refresh_container_received.py`: `--company`, `--container X` or
   `--all-open`, dry run by default printing per line old vs new received / status; `--apply`
   refreshes. Exit codes like the dedupe script.
4. Nightly: `spo_container_relink_sweep` also refreshes open shipments (any line not
   fully received), per company, cheap.

## Known limits (review round 1)

- `open_shipment_ids` keys on line status only: a line stored `received` that
  should now read lower, or a shipment whose lines all read `received` while its
  `shipment_status` is stale, is not revisited nightly. `refresh_container_received.py
  --container X` covers those on demand.
- A shipment with an unallocated line computes `in_transit` forever, so it is
  refreshed every night; the refresh also resets a manual `fully_received` header
  to `in_transit` when its lines are not all received, exactly as opening the page
  does. Count open shipments on prod with `--all-open` (dry run) before relying on it.
- `IntervalTrigger(hours=24)` runs 24h after each scheduler start, not at a fixed time.

## Trigger for more machinery

None. If a third consumer needs "open shipments", lift the query into the service.
