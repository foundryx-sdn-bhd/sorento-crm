# UAC: CA-2 topic taxonomy, labeller, Top asks and Topic audit

Plan: `PLAN-chat-analytics-ca2-taxonomy-09oct.md`. Mock: `documentation/mockups/CA-2-TAXONOMY/index.html`.

Status: labeller section ACCEPTED (built, green at 3523fcb1 / bf4a7056). Top asks and Topic audit
sections DRAFT, written on the mock v1 recommendations (Q1-Q5 all (a)); they change only if the
owner's answers differ.

## Labeller (built)

- U1. Subject is the fact domain; a null domain is `general.noise` for a casual turn, else `general`.
- U2. The leaf is always a `chat_topics` code under the ask's subject; an unknown or foreign leaf
  becomes `<subject>.other`, or the subject itself when the subject has no `.other` leaf.
- U3. A message with two asks gets a second fact row (`ask_seq = 2`) copying contact, time,
  outcome; turn counts read `ask_seq = 1` only.
- U4. Every labelled row has `language` in `en / ms / zh / mixed / other`.
- U5. A product item is written only for labeller confidence >= 0.8 AND exactly one `exact`-tier
  resolver hit, with `resolved_by = resolver_pass`.
- U6. Rows with no topic or an older taxonomy version are (re)labelled; nothing else is touched.
- U7. The nightly tick at 02:15 Malaysia does nothing unless `CHAT_TOPIC_LABELLER_ENABLED=true`.
- U8. No customer words are stored on any fact or item row.

## Top asks (report on the Reports screen)

- U9. Menu: System Management > Top Asks, beside Chat Overview, visible only with
  `chatbot.analytics.view`.
- U10. Opens on 5 Sep 2026 to today, Count by = Topics, rows = topic, columns = month, a total
  row; a bar chart of the top topics above the table.
- U11. Filters: Period, Contact type, Outcome, Subject, Count by (Topics / Products), and for
  Products a toggle Asked about / Shown by the bot. Each filter changes the counts.
- U12. Asks not yet labelled count under "(not labelled yet)", never dropped.
- U13. A note under the filters states the labelled share of asks and the taxonomy version.
- U14. Products view counts asks per product per month; a product asked twice in one ask counts once.
- U15. Asks tab: one row per ask, columns Asked on, Subject, Topic, Language, Outcome, Customer
  (name, never an id); fixed layout, resizable columns.
- U16. Export to Excel, Views and Configure summary work as on Chat Overview.

## Topic audit

- U17. Menu: System Management > Topic Audit, visible only with BOTH `system.chat_history.view`
  and `chatbot.analytics.view` (it shows the customer's words).
- U18. Empty state for a version with no sample: heading + hint, no button in the body; the
  only button is "Draw sample" in the header.
- U19. Draw sample takes 300 labelled asks of the chosen taxonomy version: at least 10 per
  subject that has asks, the rest by volume, half of that rest from the lowest confidence.
  Drawing again for the same version is refused (one sample per version).
- U20. Each row shows the message (read from the trace, not stored), subject, topic, confidence,
  products, and the verdict buttons Right / Wrong topic / Wrong subject; a row with products has
  a second Right / Wrong for the products.
- U21. Wrong topic asks for the right leaf from the leaves under the subject; Wrong subject asks
  for the subject, then its leaf (clearable `SearchableSelect`).
- U22. Tiles: Reviewed n / 300, Subject right %, Topic right %, Product right %, each against
  its target (90 / 80 / 95); a tile under target shows red.
- U23. Show filter: To review / Reviewed / All; Subject filter.
- U24. Usable without horizontal page scroll at 375 px and 1280 px.
