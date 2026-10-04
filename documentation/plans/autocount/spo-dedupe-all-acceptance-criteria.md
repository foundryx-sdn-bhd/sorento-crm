# UAC: SPO dedupe all + container received refresh

Plan: `PLAN-spo-dedupe-all.md`

1. `dedupe_spo_standalone.py --all` without `--apply` lists, in `spo_number` order, only SPOs
   that hold both Excel-era and AutoCount rows, writes nothing, and ends with totals plus a
   "would change" list and a "blocked" list.
2. `--all` and `--spo` together is an argument error; neither is an argument error.
3. `--all --limit N` processes at most the first N candidate SPOs.
4. `--all --apply` reaches the same end state per SPO as `--spo <n> --apply`; a guard failure on
   one SPO rolls back only that SPO and the run exits 3.
5. After a committed apply, every inbound shipment the SPO touched has its stored line received
   figures and statuses recomputed (no packing list open needed).
6. `refresh_container_received.py --container X` dry run prints each line's stored vs computed
   received and status and writes nothing; `--apply` stores the computed values.
7. `--all-open` covers only shipments with at least one line not fully received.
8. The nightly `spo_container_relink_sweep` heals a stale open container line.
