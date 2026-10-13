# UAC: OI-SPO-EXACT

Plan: `PLAN-oi-spo-exact-13oct.md`.

- **AC-SX-1** A row whose SO line's PO line has no allocation naming it links to that PO line,
  never to an allocation of a sibling line of the same PO and product (case 1, via ref).
- **AC-SX-2** The same through an AutoCount claim pairing the SO line with the PO line.
- **AC-SX-3** An allocation naming the row's own PO line is still taken first; the PO line
  answers for the remainder only.
- **AC-SX-4** An allocation stating the PO number but no PO line is never taken through a PO
  line.
- **AC-SX-5** An SO line bought on two PO lines takes each line's own container and nothing
  crosses between them.
- **AC-SX-6** The worklist shows no derived SPO entry, no SPO card share and no `spo_number`
  filter hit for an allocation of a sibling PO line; it still shows the row's own line's.
- **AC-SX-7** The repair script's dry run writes nothing and lists OI, product, old -> new;
  `--apply` moves the auto fallback link to the new target; manual links are untouched; a
  second run reports 0.
