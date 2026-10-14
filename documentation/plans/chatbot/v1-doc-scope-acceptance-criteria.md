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
- AC-7 (V1-DOC-SCOPE-SALES): An unlinked salesperson (an active CRM user with the role
  `salesperson`, or a sales agent linked to the contact) sees any customer's DO / SO by number,
  the DO list and the outstanding report, as today.
- AC-8: A linked salesperson naming another customer's SO number gets that SO's card; for
  customer words and lists it stays on its links as today.
- AC-9: An inactive user, or a user whose only salesperson role is trashed, is no salesperson:
  still blocked when unlinked.
