# UAC - Packing list region (REGION-PACKING-LIST)

Owner answers 2 Oct 2026: Q1 (b) contact grant in ACCESS-MODEL; Q2-Q5 (a). Mock v2 approved, default West Malaysia only.
[now] = this lane; [AM] = lands with ACCESS-MODEL (PR #1434, AC-AM-25).

## Packing list [now]
- AC-RPL-1 A packing list carries one or more regions (West, East, or both). A new one from any path (upload, Create, proforma convert, external API) is West only unless other regions are chosen.
- AC-RPL-2 The Upload dialog shows a required Regions multi-select only for the Packing List type, West Malaysia only preselected; the regions picked reach the packing list n8n creates from that file.
- AC-RPL-3 External create: explicit `regions` in the payload win, else the attachment's regions, else West only. A re-upload matched onto an existing container sets its regions to the upload's (owner ruling 2 Oct).
- AC-RPL-4 Regions are shown and editable on Create Packing List and on the detail page (same layout, edit in place); an empty set or any value other than West / East is refused (422).
- AC-RPL-5 The packing list list has a Regions column and a Region filter (a row matches when any of its regions matches).
- AC-RPL-6 Backfill: every existing packing list reads West only after the migration.

## Filter seam [now]
- AC-RPL-9 Given a contact region set, a packing list is visible when any of its regions is in the set; a set holding East is expanded to East + West. Applies on `/incoming-stock/list`, `/by-product`, `/shipments`, `/shipments/{id}/products`, `/shipments/{id}/attachment`.
- AC-RPL-10 With the West-only set, packing lists tagged West or West + East are visible and East-only ones are not, on every route above: no row, line, ETA or file; totals and paging count only what is visible.
- AC-RPL-11 An unresolved contact gets the West-only set.
- AC-RPL-12 No contact in play (staff session, bare API key) sees every packing list, as today.
- AC-RPL-13 Stock ask: the West-only set's ETA and packing-list file come from the earliest visible packing list; an East-only container gives no ETA.
- AC-RPL-14 Chatbot "has incoming" stamps and did-you-mean / cross-domain probes follow AC-RPL-9..11 (they call `/incoming-stock/list` with the contact).
- AC-RPL-15 A packing list's regions change applies on the next ask.
- AC-RPL-16 Interim: until ACCESS-MODEL lands, every chatbot contact's set is West only (`eta_policy.contact_regions`).

## Contact [AM]
- AC-RPL-7 Every contact holds one or more regions, default West only, set on the contact Access tab; staff tick East by hand.
- AC-RPL-8 Backfill: every existing contact holds West only; duplicate rows resolve to the intersection of their regions, then expand.
