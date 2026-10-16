# UAC: V1-DOC-SCOPE

- AC-1: An unlinked non-staff contact naming another customer's DO number gets no DO detail
  (the match is dropped, said as an existing "could not find" style miss).
- AC-2: An unlinked non-staff contact naming another customer's SO number (outstanding reveal)
  gets no SO detail.
- AC-3: An unlinked non-staff contact asking for a DO list with no number gets no other
  customer's DOs.
- AC-4: A linked dealer gets their own DOs / SOs, never another customer's.
- AC-5: Staff behaviour unchanged: any customer's documents as today. Staff = tier office OR an
  active office access type; either alone is enough.
- AC-6: No new reply text.
- AC-7 (V1-DOC-SCOPE-SALES, owner ruling 16 Oct): An unlinked contact with an ACTIVE, not trashed
  CRM user holding any staff role (anything but portal_user / guest: customer service, warehouse,
  purchasing, project, marketing, technical, salesperson...) sees any customer's DO / SO by number,
  the DO list and the outstanding report, as today.
- AC-8: A linked salesperson naming another customer's SO number gets that SO's card; for
  customer words and lists it stays on its links as today.
- AC-9: An inactive or trashed user, a user whose only live roles are portal_user / guest or none
  (a dealer's portal login, a trashed role), or a sales agent linked to the contact is not staff:
  still blocked when unlinked, and a linked dealer with a portal login stays held to its links.
  Deactivating the user revokes access.
- AC-10: A salesperson never sees another customer's PO ref (lane, resolver and clarifier), as
  today.
- AC-11 (slice 3): An unlinked non-staff contact typing another customer's (partial) name gets the
  no-link refusal line: no customer picker, no other customer's name, no DO probe. Document staff
  (office tier, office access type, salesperson) keep the picker.
- AC-12: The same holds when the parser tagged the word brand, category, product or order and the
  resolver reads it as a customer; a carried customer word never refuses a later turn.
- AC-13 (owner CA-3 Q5(a), crew ruling 15 Oct): an unlinked contact holding `chatbot.analytics`
  keeps the chat report's customers, and one holding `sales_orders.sales_report` keeps the sales
  report's (top selling and the ranking included), picker included. A report grant does not open
  any other domain.
- AC-14: No DO probe and no has / no DO stamp for an unlinked contact that is not document staff,
  on any customer list (granted report pickers included); the low_signal clarifier's prompt
  carries no customer row for a held contact.
